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

## Debugging a running process

**You cannot attach a debugger here — plan the experiment instead.** The Talos
nodes set `kernel.yama.ptrace_scope=2`, so `strace`, `gdb` and even reading
another process's `/proc/<pid>/environ` need `CAP_SYS_PTRACE`, and PodSecurity
enforces `baseline` cluster-wide, which refuses that capability — so
`kubectl debug --profile=sysadmin` is rejected outright. Two things still work.
`talosctl` runs outside all of it and will read any `/proc` and `/sys` file on
the node, which is enough to characterise a process without touching it:
`processes` gives state and CPU time, `/proc/<pid>/io` gives syscall counts and
byte totals, `fdinfo/<n>` identifies eventfds and inotify instances, `maps`
lists loaded libraries, and per-thread files under `task/` say which thread is
actually burning the CPU. And an A/B run of the process under two configurations
answers "why" more convincingly than a backtrace would, because it demonstrates
the fix rather than the fault — that is how webtop's Thunar spin was pinned on
its preloaded udev shim.

## Adding a stateful workload

**Decide how its data is backed up as part of adding it.** Every PVC is either
backed up or explicitly excluded, and the coverage check holds the cluster to that:
a new claim reports as awaiting its first backup for a night, then fails until a
snapshot of it lands or it is excluded. [`backups.md`](backups.md) describes the
machinery; this is how to choose.

**Read the application's own backup and restore documentation first.** Its designers
know which files belong together, what a restore needs, and what a version change
does to the data. Plex's restores two databases as a pair; headscale's cannot skip a
minor version.

Then work down the list and take the first that fits:

1. **Nothing to back up.** State that git reproduces — manifests, ConfigMaps,
   SOPS Secrets — needs nothing. If the application has no PVC, it is done.
2. **Exclude the claim** with `k8up.io/backup: "false"` when its contents are
   regenerable: caches, metrics, logs, sync state that a resync rebuilds. Also for
   every SMB share, which ZFS snapshots and replication cover instead. An exclusion
   is a statement, not an omission; the coverage check lists them.
3. **Back up the volume and dump the database** when a database writes to the
   claim continuously: PostgreSQL, MariaDB, or SQLite in WAL mode or with steady
   writes. A file copy of a live database is crash-consistent at best, so the
   volume backup is a fallback and the dump is what a restore uses.
   - A relational database: a `k8up.io/backupcommand` annotation on its pod,
     running the dump inside the database's own container.
   - SQLite: a PreBackupPod in the application's `backups.yaml`, using the shared
     `keinos/sqlite3` image.

   Either way, follow *Writing a backup command* in [`backups.md`](backups.md),
   name the application in the file extension, and label the producer
   `k8up-dump: "true"` so the six-hourly dumps take it.
4. **Back up the volume and nothing more** otherwise: configuration directories,
   key files, plain files, and SQLite in journal mode with negligible writes. This
   is K8up's default, so there is nothing to add.

**A new namespace needs a Schedule**, or none of its claims is backed up. Give it a
directory under `kubernetes/k8up/`, copying an existing one: a kustomization
setting its `namespace:` and pulling in `../repository`, and a `schedules.yaml`
whose time is staggered against the others in the schedule table in
[`backups.md`](backups.md). If another Flux Kustomization creates the namespace,
add it to `dependsOn` in `kubernetes/cluster/k8up.yaml`.

**Check it after the first night.** In the coverage check's latest ping in
healthchecks, the summary's claim and dump counts should include the new ones, and
nothing should still call them new or awaiting a backup.

**Write down how to restore it**, under *Each application* in
[`backups.md`](backups.md): what the application's own guidance says a restore needs,
which files go back together, and anything version-bound. Then restore its first
backup once, into scratch space, before relying on it.

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

**Tag its final restic snapshots `decommissioned` before the claims go**, or they age
out on the ordinary retention windows. See *Retention* in [`backups.md`](backups.md).

**Exclude its claims from K8up before deploying the removal:**

```bash
kubectl annotate -n <ns> pvc <claim> k8up.io/backup=false
```

A deleted claim stays `Terminating` while K8up's completed backup pods still
reference it, which lasts until K8up prunes those Jobs, and it is still `Bound`
throughout, so K8up keeps selecting it. The scheduler refuses a pod that mounts a
claim being deleted, and K8up puts all of a namespace's claims on a node into one
pod, so the next night's backup of the whole namespace would fail.

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
