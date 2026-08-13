# Kubernetes conventions

How workloads in this cluster are expected to behave, and the traps that have
caught us. Most of these exist because something failed quietly first — that is
the recurring theme, and the reason several entries are about *noticing* rather
than *doing*.

For the cluster's shape — nodes, storage, networking — see [`talos.md`](talos.md),
[`disk_management.md`](disk_management.md) and [`network.md`](network.md).

## "Applied" is not "in effect"

Flux reporting a Kustomization `Ready` means the desired state was applied to the
API. It does not mean any running process has re-read it. A ConfigMap-only
change updates the object and leaves every pod that consumed it running whatever
it read at startup — and `flux get kustomizations` will report healthy the whole
time, correctly.

This has bitten twice, both silently:

- The commit fixing beets' path routing touched only a ConfigMap. No rollout
  followed, the pod kept the old file, and five audiobooks imported to the wrong
  directory before anyone noticed. Nothing errored; the imports "succeeded".
- A later commit disabling ReplayGain-on-import had the same shape, and was
  caught before doing damage only because the drift was checked for
  deliberately.

beets-flask is the worst case, because it seeds `/config` onto a PVC from an
initContainer — so even the *file* stays stale until the pod is recreated. Where
a ConfigMap is mounted directly, the kubelet does refresh the files within its
sync period, but a process that reads config only at startup still carries on
with the old values.

**When a config change matters, verify at the layer that matters.** Four things
can disagree, and checking one proves little about the others:

```bash
git show HEAD:kubernetes/apps/<app>/config-map.yaml   # 1. what git says
kubectl get configmap -n apps <name> -o yaml          # 2. what the cluster has
kubectl exec -n apps deploy/<app> -- cat /path/to/config   # 3. what the pod sees
kubectl exec -n apps deploy/<app> -- <app> config     # 4. what the program resolved
```

A ConfigMap-only change needs `kubectl rollout restart` to take effect. Nothing
signals this, which is why
[`todos/config-change-rollouts.md`](../todos/config-change-rollouts.md) exists —
either `configMapGenerator` name hashing or Reloader would close it permanently.

Note that `kubectl rollout restart` does not survive the next reconciliation:
Flux does not own the `restartedAt` annotation it adds, strips it, and rolls the
pod a second time. Harmless, but expect two restarts.

## Probes

Most apps here define none, and that has been mostly fine — but "mostly" has
already cost us one silent outage, and the bar for adding one is lower than the
current absence suggests.

**Default to a readiness probe.** It is the only thing that distinguishes a
container that is *running* from an application that is *serving*. beets-flask
demonstrated the gap: its uvicorn workers crash-looped roughly eleven times a
second while the container stayed up and the log cheerfully printed
`Server running on http://0.0.0.0:5001`. Nothing ever bound the port. Without
the probe the Deployment would have reported `Available` indefinitely.

Worth adding one when any of these hold:

- **The process that serves is not the process the container supervises.** Any
  entrypoint that spawns workers, drops privileges via `su`, or runs a
  supervisor. The container's liveness tells you about the parent, not the thing
  answering requests.
- **Startup is slow or staged.** Database migrations, index builds, cache warming.
  Readiness keeps traffic away until the app can actually answer.
- **It sits behind a Service that something else polls.** The homepage dashboard
  monitors most of these apps; a readiness probe makes that signal honest rather
  than merely "the port is open".
- **The app can lose a dependency without exiting.** Up, but its database
  connection is gone.

**Be much more cautious with liveness probes.** A liveness probe restarts the
pod, so it must only fire on a hang that a restart actually fixes. Two ways to
get this wrong:

- **Long-running work looks like a hang.** beets-flask deliberately has no
  liveness probe: an import can occupy the process for many minutes, and a
  liveness probe would kill it mid-import and leave a wedged session behind. If
  the app does long synchronous work, readiness-only is the safer default.
- **Checking dependencies in a liveness probe turns one outage into many.** A
  shared database blip becomes a cluster-wide restart storm. Dependencies belong
  in readiness, if anywhere.

If you do add liveness to something slow to boot, add a `startupProbe` as well,
so the slow first start is not mistaken for a hang.

Practical shape, from the one probe currently in the repo
(`kubernetes/apps/beets/deployment.yaml`):

- Probe an endpoint the *serving* process answers. A static file served by a
  sidecar or ingress proves nothing about the app.
- Be generous with `failureThreshold` on cold starts. Readiness failures cost
  nothing but a `NotReady` status; flapping costs confidence.
- With a single replica and `strategy: Recreate`, readiness buys honest status
  rather than traffic management — which is precisely what was missing.

## Changing a workload

- **Swapping one chart for another: diff the *rendered* manifests, not the values
  files.** `helm template` each chart with its real values and diff the resulting
  pod specs. Comparing values only shows what you are *setting*, which is
  structurally blind to what the chart *defaults* — and defaults are where this
  bites. Replacing promtail with Alloy on 2026-08-10 shipped two defects that a
  rendered diff would have caught in one step: Alloy's chart ships
  `tolerations: []` where promtail's tolerated the control-plane taint, and
  Loki's chart set `whenDeleted: Delete` where Kubernetes defaults to `Retain`.
- **DaemonSet health is a trap: `desiredNumberScheduled` is computed from
  schedulable nodes,** so one that cannot tolerate a node's taint reports full
  readiness while silently covering fewer nodes. After any DaemonSet change,
  check desired equals your node count rather than trusting `READY`.

## Removing a stateful workload

**Inventory what dies with it.** For each PVC it owns, check three things: the
StorageClass `reclaimPolicy`, the StatefulSet's
`persistentVolumeClaimRetentionPolicy`, and whether Flux will prune it. Both
`openebs-hostpath` and `openebs-device` are `reclaimPolicy: Delete`, so a deleted
PVC takes its data with it via an OpenEBS cleanup job. Deleting the Loki release
removed ~131 GiB this way, on the mistaken assumption that the Kubernetes
`Retain` default applied.

```bash
kubectl get pvc -n <ns>                                        # what exists
kubectl get sc -o custom-columns='NAME:.metadata.name,RECLAIM:.reclaimPolicy'
kubectl get sts <name> -n <ns> -o jsonpath='{.spec.persistentVolumeClaimRetentionPolicy}'
```

## Versions and automation

- **Chart versions are automated where the chart is published as an OCI
  artifact**: `OCIRepository` + `ImageRepository` + `ImagePolicy`, with a
  `$imagepolicy` marker on `ref.tag`, so Flux Bot commits version bumps to git
  and git keeps describing what is deployed. See
  `kubernetes/infrastructure/cert-manager.yaml` for the pattern. Charts with no
  OCI artifact — `csi-driver-smb`, `metrics-server`, `alloy` — stay pinned and are
  bumped by hand.
- **Ranges are major-pinned.** Minors and patches flow automatically; crossing a
  major is a deliberate edit. The consequence is that a chart silently stops
  advancing at the boundary, which is what
  [`todos/version-notification-prompt.md`](../todos/version-notification-prompt.md)
  exists to make visible. The one exception is `beets-flask`, which tracks
  release candidates because 2.0.0 has never shipped a stable release; see
  [`beets.md`](beets.md).
- **Every HelmRelease sets `install`/`upgrade` `remediation.retries: 3`**, so a
  failed chart rolls itself back instead of stalling half-applied. This was added
  after two upgrades failed in the same window and behaved completely differently
  depending on whether they had it.
