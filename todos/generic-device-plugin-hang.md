# generic-device-plugin `/metrics` hang — capture, report upstream, then probe

Written 2026-08-14, after investigating recurring `TargetDown` Pushover alerts
for `infrastructure/generic-device-plugin`. Everything below was verified against
the live cluster on 2026-08-12 and 2026-08-14 unless stated otherwise.

**Phase 1 has already shipped** (`kubernetes/infrastructure/devices.yaml`). What
remains is capturing a goroutine dump from a live wedge, filing it upstream, and
only then adding the liveness probe.

## The defect

The plugin's `/metrics` endpoint wedges permanently on any node that has an
optical device. The process stays otherwise healthy, and only an OOMKill recovers
it — hours later.

Verified evidence:

- **It tracks device presence exactly.** Over 14 days of `up == 0` samples:
  `piraeus-worker-0` (no `sr0`/`sg0`) — **0 samples**; `piraeus-control-plane-0`
  (QEMU virtual DVD-ROM) — 75; `piraeus-worker-1` (Pioneer BDR-212U) — 95.
- **Only `/metrics` blocks.** Against the same wedged pod at the same moment:
  `/metrics` hung until timeout, `/health` returned 200 instantly, and an unknown
  path returned 404 instantly. TCP connected immediately in all three cases.
  Prometheus reported `context deadline exceeded` with `lastScrapeDuration`
  pinned at exactly the 10s scrape timeout.
- **The devices are not blocked.** While `/metrics` was still hung, `open()` on
  both `/dev/sr0` and `/dev/sg0` succeeded immediately from a container holding
  them. So it is a goroutine stuck once, not ongoing device unavailability.
- **Memory is a consequence, not a cause.** Working set was flat at 10.35 MB for
  two hours, then climbed only *after* `up` went to 0 (10.56 MB at 00:00 with
  `up=1`; 11.68 MB at 00:05 as `up` hit 0; 16.97 MB by 00:10). Upstream calls
  `http.Serve` with no `ReadTimeout`/`WriteTimeout`, so every 15s scrape that
  blocks in `Gather` leaks its handler goroutine permanently.
- **Only an OOMKill recovers it**, roughly daily across the two device-bearing
  nodes: `2026-08-08T10:00:29Z` control-plane, `2026-08-11T22:00:01Z` worker-1,
  `2026-08-12T05:57:50Z` worker-1, `2026-08-13T18:00:57Z` control-plane — all
  `exitCode 137`.
- **Device advertisement dies with it.** During worker-1's wedge its
  `squat.ai/cdrom` capacity went 1 → 0 and stayed there until the OOMKill.

Source read at `squat/generic-device-plugin@main`: the registry holds only
`NewGoCollector()`, `NewProcessCollector()` and per-resource gauges, and
`/metrics` is a plain `promhttp.HandlerFor(r, promhttp.HandlerOpts{})`. Since
`/health` keeps answering, the blockage is inside the gather path rather than the
mux or listener.

### A wrong turn worth recording

The first hypothesis was that `refreshDevices()` held `gp.mu` across a blocking
device syscall, so `/metrics` blocked on the mutex. **That is wrong** — the
source takes `gp.mu` only *after* `discover()` returns, so the syscalls happen
outside the lock. What actually pointed at the truth was the 404-returns-instantly
test, which proved the process and HTTP server were fine and narrowed it to the
promhttp gather path. Do not re-propose the mutex explanation without new
evidence.

## What Phase 1 already did

In `kubernetes/infrastructure/devices.yaml`, shipped 2026-08-14:

- **Device selection moved to hardware identity.** The group now requires
  `/dev/disk/by-id/ata-PIONEER_BD-RW_*` (mounted at `/dev/sr0`) alongside
  `/dev/sg0`, so the control plane stops advertising a phantom cdrom backed by
  its Talos install ISO. Rationale and mechanics are documented in
  `docs/automatic-ripping-machine.md`.
- **`GOTRACEBACK=all`** added, purely so the next wedge produces a complete
  goroutine dump. **Remove it once this work is finished.**
- **Image pinned by digest** to what was `latest` on 2026-08-14
  (`sha256:dc192e164c69b03f156765793a1be62ca437709ae477b27ca7d8f3dcf5021576`);
  the previous pin `854e0c1` predated Feb 2026.
- **The memory limit was deliberately left at 20Mi.** Decision made by the user:
  the pod only exhausts memory *because* it is wedged, so the cause and its rapid
  resolution are what to fix. Keeping 20Mi also preserves the OOMKill as a
  backstop. Do not "fix" this by raising the limit.
- **The liveness probe was deliberately NOT shipped.** It would restart a wedged
  pod within ~45s, far too fast to catch by hand, making the defect permanently
  uncapturable. One more cycle of alerts is the accepted cost of a real upstream
  fix.

**All of the above is verified working as of 2026-08-14** — ARM was restarted so
it took a fresh allocation, and both `/dev/sr0` and `/dev/sg0` inside the
container report `PIONEER / BD-RW   BDR-212U`. Only worker-1 advertises the
device; the control plane and worker-0 report 0. All three scrape targets are
`up` and no `TargetDown` is firing. **Nothing in Phase 1 remains to be done or
checked** — what follows is the outstanding work.

### The image bump had a side effect worth knowing about

Bumping the digest also changed the resource **domain**. Upstream's default moved
from `squat.ai` to `devic.es`, and `devices.yaml` had never set `--domain`, so it
silently inherited the new one. That renamed the resource and dropped
`squat.ai/cdrom` — still requested by ARM at that moment — to `allocatable: 0` on
every node. ARM kept running on its existing allocation, so nothing looked
broken, but it would have gone `Pending` forever on its next restart.

Caught by a pre-flight check before restarting ARM. Resolved by moving both sides
to `devic.es/cdrom` (the user chose to match upstream's default rather than pin
`--domain`, accepting that a future upstream default change would recur this).
`docs/automatic-ripping-machine.md` records the symptom and the two commands that
diagnose it.

The general lesson, which is why this is written down: **the manifest was relying
on an upstream default for a value that is half of a contract with another
workload.** A "hygiene" image bump is enough to break that, invisibly, with the
failure deferred to an unrelated restart weeks later.

## Step 1 — Capture the dump on the next wedge

**The existing Pushover `TargetDown` alert is the trigger.** It fires after 10
minutes of downtime; the OOMKill does not arrive for another 2–6 hours, so there
is a comfortable window.

Identify the wedged pod and confirm the signature:

```bash
kubectl exec -n infrastructure alertmanager-kube-prometheus-kube-prome-alertmanager-0 \
  -c alertmanager -- wget -qO- \
  'http://kube-prometheus-kube-prome-prometheus.infrastructure.svc:9090/api/v1/query?query=up{job="infrastructure/generic-device-plugin"}' \
| jq -r '.data.result[] | "\(.metric.pod) \(.metric.instance) up=\(.value[1])"'

POD_IP=<ip of the up=0 pod>
# expect an instant 404, proving the process and HTTP server are healthy:
kubectl exec -n infrastructure alertmanager-kube-prometheus-kube-prome-alertmanager-0 \
  -c alertmanager -- timeout 8 wget -O- -T 6 "http://$POD_IP:8080/nonexistent"
# expect a hang:
kubectl exec -n infrastructure alertmanager-kube-prometheus-kube-prome-alertmanager-0 \
  -c alertmanager -- timeout 8 wget -O- -T 6 "http://$POD_IP:8080/metrics"
```

Then take the dump. The plugin image is distroless with no shell, so this needs
an ephemeral container sharing the target's PID namespace:

```bash
kubectl debug -n infrastructure <wedged-pod> \
  --image=busybox --target=generic-device-plugin -it -- sh
# inside:
ps -o pid,comm            # find the generic-device-plugin pid
kill -QUIT <pid>          # Go dumps all goroutine stacks to stderr, then exits
```

The container restarts, so retrieve the dump from the previous log:

```bash
kubectl logs -n infrastructure <wedged-pod> --previous > gdp-goroutines.txt
```

Read for the goroutine blocked inside the `promhttp`/`Gather` path, and the
others piled up behind it in `semacquire` on the registry mutex. **The one
goroutine that is not waiting on the mutex is the defect.**

## Step 2 — File the bug report

The full draft is at the end of this file. Attach `gdp-goroutines.txt` and fill
the bracketed placeholder. The user asked to do the filing themselves — write it
up, hand it over, do not submit it.

## Step 3 — Add the liveness probe

Only after the dump is captured. In `kubernetes/infrastructure/devices.yaml`:

```yaml
livenessProbe:
  httpGet:
    path: /metrics
    port: http
  periodSeconds: 15
  timeoutSeconds: 5
  failureThreshold: 3
```

**It must target `/metrics`, not `/health`** — `/health` returns 200
unconditionally and did so *on the wedged pod*, so it cannot detect this fault.

`docs/kubernetes.md` says to default to readiness and be cautious with liveness.
This is the carve-out, and each stated risk was checked: a restart is
demonstrably the only thing that has ever fixed it; healthy `/metrics` responds
in 1.4–2.3 ms so there is no long synchronous work in the probe path; and
`/metrics` has no external dependency, so it cannot turn one outage into many.
**Readiness would accomplish nothing** — the DaemonSet sits behind no Service, so
NotReady would neither restore the device nor stop the alert. No `startupProbe`
needed: the plugin serves ~95 ms after start.

Then remove `GOTRACEBACK=all`, and fold the permanent parts into
`docs/kubernetes.md` (the probe rationale) before deleting this spec.

## Verification

The regression test is that the next wedge produces `reason: Error` from the
probe rather than `OOMKilled`, with downtime under 10 minutes so no alert fires:

```bash
kubectl get pod -n infrastructure -l app.kubernetes.io/name=generic-device-plugin \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[0].restartCount}{"\t"}{.status.containerStatuses[0].lastState}{"\n"}{end}'
kubectl get events -n infrastructure --sort-by=.lastTimestamp | grep -i 'unhealthy\|killing'
```

Also confirm the Phase 1 device selection still holds — only worker-1 should
advertise:

```bash
kubectl get nodes -o custom-columns='NODE:.metadata.name,CDROM:.status.allocatable.devic\.es/cdrom'
```

### The by-id passthrough is already verified

Done 2026-08-14 and **does not need repeating**: ARM was restarted so it took a
fresh allocation, and both `/dev/sr0` and `/dev/sg0` inside the container report
`PIONEER / BD-RW   BDR-212U`. containerd 2.1.6 resolves the by-id symlink
correctly, so the documented fallback config was not needed.

The procedure and the known-good baseline now live in
`docs/automatic-ripping-machine.md` under "Verifying the passthrough" — including
why checking a *running* ARM pod is a false pass. Re-run it only if the device
config, the plugin image, or the container runtime changes.


## The prompt to open with

> The generic-device-plugin `/metrics` endpoint wedges permanently on nodes with
> an optical drive, and only an OOMKill recovers it. Phase 1 — device selection by
> `/dev/disk/by-id`, `GOTRACEBACK=all`, digest pin — shipped and was verified on
> 2026-08-14; do not redo it. Read `todos/generic-device-plugin-hang.md`. A
> `TargetDown` alert has just fired for `infrastructure/generic-device-plugin`:
> capture the goroutine dump from the wedged pod before it OOMKills, then finish
> the bug report draft for me to file.

---

# Upstream bug report — draft

Repository: `squat/generic-device-plugin`. Attach `gdp-goroutines.txt`, fill the
placeholder, then hand to the user to file.

**Title:** `/metrics` hangs permanently on nodes with a CD-ROM device; only an OOMKill recovers it

## Summary

On nodes where the configured device paths exist, the `/metrics` endpoint
eventually blocks forever. The rest of the process stays healthy — `/health` and
unknown paths still answer instantly — so this is isolated to the Prometheus
gather path. Because the HTTP server sets no timeouts, every subsequent scrape
leaks a handler goroutine, memory climbs until the container hits its limit, and
the resulting OOMKill is the only thing that restores service. It then recurs.

Critically, the same stall also takes down the kubelet `ListAndWatch` stream: the
node's advertised device count drops to 0 and the workload that requires the
device can no longer be scheduled.

## Environment

- generic-device-plugin: `ghcr.io/squat/generic-device-plugin:854e0c1`, also
  reproduced on `[DIGEST FROM THE RUN THAT PRODUCED THE DUMP]`
- Kubernetes: v1.35.0
- OS: Talos Linux v1.12.4, kernel 6.18.9-talos, amd64
- Runtime: containerd 2.1.6
- Resources: upstream defaults (requests 50m/10Mi, limits 50m/20Mi)
- Device config:

```yaml
--device
name: cdrom
groups:
  - paths:
      - path: /dev/sr0
      - path: /dev/sg0
```

The affected devices are optical drives: a physically passed-through Pioneer
BD-RW BDR-212U on one node, and a QEMU virtual DVD-ROM on another.

## Evidence

**1. It only happens on nodes that actually have the device.** Three-node
cluster, identical DaemonSet. Over 14 days of `up == 0` samples:

| Node | has `/dev/sr0`+`/dev/sg0` | `up==0` samples |
|---|---|---|
| worker-0 | no | **0** |
| control-plane-0 | yes (QEMU virtual DVD-ROM) | 75 |
| worker-1 | yes (Pioneer BDR-212U) | 95 |

The node with no matching device has never once failed.

**2. Only `/metrics` blocks — the process is fine.** Against the same wedged pod,
at the same moment:

```
GET /metrics      -> hangs until client timeout
GET /health       -> 200, instantly
GET /nonexistent  -> 404, instantly
```

TCP connects immediately in all three cases. Prometheus reports
`Get "http://.../metrics": context deadline exceeded` with `lastScrapeDuration`
pinned at exactly the 10s scrape timeout.

**3. The devices are not blocked.** While `/metrics` was still hung, from a
container holding the same devices:

```
open("/dev/sg0") -> OK, immediate
open("/dev/sr0") -> OK, immediate
stat both        -> OK (b 11:0, c 21:0)
```

So this is not ongoing device unavailability — it is a goroutine that got stuck
once and never recovered.

**4. Memory growth is a consequence, not a cause.** Working set was flat for two
hours, then began climbing only *after* the endpoint went down:

```
23:55  10.52MB  up=1
00:00  10.56MB  up=1
00:05  11.68MB  up=0   <-- wedge begins, memory follows
00:10  16.97MB  up=0
00:45  16.27MB  up=0
```

`http.Serve` is called without `ReadTimeout`/`WriteTimeout`, so each 15s scrape
that blocks in `Gather` leaks its handler goroutine permanently.

**5. Only an OOMKill recovers it.** Every recovery observed, roughly one per day
across the two device-bearing nodes:

```
2026-08-08T10:00:29Z  control-plane  OOMKilled exit=137   (~6h wedged)
2026-08-11T22:00:01Z  worker-1       OOMKilled exit=137   (~2h wedged)
2026-08-12T05:57:50Z  worker-1       OOMKilled exit=137
2026-08-13T18:00:57Z  control-plane  OOMKilled exit=137
```

**6. Device advertisement dies with it.** During the wedge the node's
`squat.ai/cdrom` capacity went `1 -> 0` and stayed there until the OOMKill,
making the consuming workload unschedulable. Since that workload selects its node
purely by requesting the device, this also caused it to prefer a different node
holding a *different* physical drive.

## Goroutine dump

Captured with `GOTRACEBACK=all` by sending `SIGQUIT` to a wedged instance:

```
[ATTACH gdp-goroutines.txt]
```

## Analysis

The registry holds only `NewGoCollector()`, `NewProcessCollector()` and the
per-resource gauges, and `/metrics` is a plain
`promhttp.HandlerFor(r, promhttp.HandlerOpts{})`. Since `/health` continues to
answer, the blockage is inside the gather path rather than the mux or the
listener; subsequent scrapes then pile up on the registry mutex behind the first
stuck one, which matches the unbounded goroutine growth.

## Suggested fixes

1. **Set HTTP server timeouts.** `ReadHeaderTimeout` and `WriteTimeout` on the
   `http.Server` would bound each request and stop a single stuck gather from
   leaking every subsequent scrape. This alone converts a hard failure into a
   degraded one.
2. **Bound the gather itself** via `promhttp.HandlerOpts{Timeout: ...}`, so a
   slow or stuck collector returns an error instead of hanging forever.
3. **Ship a `livenessProbe` in the manifests and chart, targeting `/metrics`.**
   Note `/health` is unsuitable — it returns 200 unconditionally and does so even
   while the plugin is wedged, so it cannot detect this class of failure.
4. **Reconsider the default 20Mi memory limit** in `manifests/` and the chart. A
   healthy baseline here is ~10.3 MB, leaving very little headroom.

## Unrelated request

Would you consider publishing semver-tagged releases? The image currently carries
only git SHAs plus `latest`, which means Flux `ImagePolicy` (and similar
ordering-based automation) cannot track it — `semver`, `numerical` and
`alphabetical` are all meaningless over commit SHAs. The Helm chart is versioned,
but its DaemonSet template hardcodes the `--device` arguments, so it cannot be
used for custom device groups like the one above.
