# generic-device-plugin `/metrics` hang — report upstream, then decide the fix

Written 2026-08-14 from recurring `TargetDown` Pushover alerts; **substantially
rewritten 2026-08-19 after two live goroutine dumps were captured.** The dumps
disproved most of the original reading, so treat anything here as dated to
2026-08-19 unless it says otherwise.

**Phase 1 shipped 2026-08-14** (`kubernetes/infrastructure/devices.yaml`).
**The dumps are captured** — that step is done. What remains is filing the
report and choosing between two candidate fixes.

## The defect

Both plugin pods on the worker nodes stop serving HTTP entirely, burn 100% of
their CPU quota indefinitely, and recover only when the container is restarted.
The device advertisement dies with them.

### What the dumps show

Two dumps taken 2026-08-19 by sending `SIGQUIT` with `GOTRACEBACK=all`, from
pods wedged for 5 and 4 hours respectively. Saved as
`gdp-goroutines-worker-0.txt` and `gdp-goroutines-worker-1.txt`.

**Prometheus scrapes accumulate inside the process and never terminate.** The
scrape interval is 15s and the scrape timeout is 10s. When a `Gather` exceeds
10s, Prometheus gives up and disconnects — but `promhttp` does not cancel the
gather, so the goroutine keeps running forever. `piraeus-worker-0`'s dump holds
eight abandoned `Registry.Gather` calls, aged **308, 286, 263, 257, 245, 145,
126 and 31 minutes**. The oldest matches the onset minute exactly: the very
first scrape that got stuck was still running five hours later.

They serialise against each other on `goCollector`'s mutex
(`client_golang/prometheus/go_collector_latest.go:326`), so each new scrape
queues behind every previous one. Arrival is fixed at one per 15s and service
time only grows: once it tips, it cannot recover.

**The process is pinned at exactly its CPU limit.** `rate(container_cpu_usage_
seconds_total[5m])` reads 0.048–0.050 against `limits.cpu: 50m`, versus 0.00036
on the healthy pod — a 140× difference. Cumulative CPU time inside the container
was 12:42 (worker-1) and 15:44 (worker-0), essentially all of it accrued after
onset. It is overwhelmingly **system** time: thread 11 on worker-1 showed
`utime=111 stime=17482`.

**97% of every CFS period is throttled** — `container_cpu_cfs_throttled_periods_
total` 215,176 of 221,981 periods on worker-0 and 169,865 of 179,244 on
worker-1, against 102 of 15,894 (0.6%) on the healthy control-plane pod. The
process gets ~5ms of each 100ms period and is frozen for the other 95. This is
the amplifier that turns a slow gather into a permanent collapse.

**The onset is sharp and simultaneous on both nodes.** worker-1's
`scrape_duration_seconds` sat at 1.6ms for four hours, then:

```
22:35  0.0017     22:37  4.44
22:36  0.143      22:38  5.35    ... oscillating 0.5-10s until
                                 23:20, then up=0 permanently
```

worker-0 crossed at the same minute, 22:36, on a different node.

**It is not node or cluster contention.** Median `scrape_duration_seconds` for
every other target in the cluster held flat at 0.005–0.006s straight through
22:36. Only these pods were affected.

### Corrections to the 2026-08-14 reading

Recorded because acting on the old text would waste a session, and because
inheriting mistaken reasoning is worse than inheriting none.

- **"It tracks device presence exactly" is false.** `piraeus-worker-0` has no
  `/dev/sr0`, no `/dev/sg0` and no `/dev/disk/by-id` directory at all, and it
  wedges. The device is not required.
- **"Only `/metrics` blocks" is false at full wedge.** The instant-404 test was
  real but describes an early stage. At full wedge *every* path hangs: an 85s
  wait on `/nonexistent` still timed out. `/health` also hangs. The old
  signature check in this file was wrong and has been replaced.
- **There is no goroutine leak.** worker-1's dump holds **22** goroutines total.
  The claim that each 15s scrape leaks a handler permanently is wrong — the
  count is bounded, they just never finish.
- **There is no fd leak.** `process_open_fds` is flat at **10** across 14 days.
  A `processCollector`/`FileDescriptorsLen` frame in the dump is an incidental
  sample, not a cause. (This was a hypothesis formed and killed on 2026-08-19;
  measuring took one query.)
- **"Only an OOMKill recovers it" is false.** The control-plane pod recovered
  twice on 2026-08-18 with `exitCode 0`, `reason: Completed`. Memory is not the
  binding constraint it was taken to be: at full wedge the pods sat at 14.9 MB,
  and the *healthy* pod sat at 14.9 MB too.
- **The mutex hypothesis from 2026-08-12 remains wrong**, but for a new reason:
  `gp.mu` is not involved at all. The contended lock is `goCollector`'s, inside
  client_golang.

### The wedge does end in an OOM kill, and that is what pages, 2026-08-25

Both open questions above are about how the wedge *ends*. It ends both ways, and
the OOM path is noisy in a place nobody was looking.

`talosctl --nodes 192.168.0.190 dmesg` and the same on `.195`:

```
[2026-08-21T05:12:01Z] oom-kill:constraint=CONSTRAINT_MEMCG ... task=generic-device-
[2026-08-25T10:08:42Z] oom-kill:constraint=CONSTRAINT_MEMCG ... task=generic-device-
  Killed process 176966 (generic-device-) total-vm:1274432kB, anon-rss:11344kB
[2026-08-24T20:43:11Z] worker-0, same signature
[2026-08-25T10:09:07Z] worker-0, 25 seconds after the control-plane kill
```

This does **not** overturn "memory is not the binding constraint" — `anon-rss`
at the moment of the kill is 11–13 MB, comfortably under 20Mi, and
`container_memory_working_set_bytes` peaks at 19.8 MB on wedged and healthy pods
alike. What crosses the limit is the cgroup total, page cache included, not the
process. The kill is a symptom of the wedge, exactly as the user's decision
assumes; the limit is what reaps it.

Two things follow that were not previously recorded:

- **The two nodes wedge together.** 08-25's kills are 25 seconds apart on
  separate VMs. Whatever abandons the gathers is cluster-wide — a scrape-side
  event, not something local to a node. That is a lead the goroutine dumps do not
  cover.
- **It pages as `NodeOOMKill`, misattributed.** `node_vmstat_oom_kill` counts
  CONSTRAINT_MEMCG kills alongside node-wide ones, so every one of these on the
  control-plane node fired an alert whose text says the *node* ran out and names
  kube-apiserver as the victim. All 24 firing samples in the seven days to
  2026-08-25 were this plugin. The rule now carries an `unless` that excludes
  kills attributable to a container with its own memory limit — see
  `kubernetes/infrastructure/prometheus-rules.yaml`. Until this spec's work
  lands, the plugin's OOM kills are therefore silent; `restartCount` on the
  DaemonSet is the remaining signal.

### A regression that correlates with our own change

`piraeus-worker-0` had **zero** `up == 0` samples between 2026-08-01 and the
2026-08-14 change, and **1426** after it. It had never failed. Over the same
split the control-plane pod went from 1761 down-samples to 63.

Commit `3e4e016` bundled the image bump (`854e0c1` →
`sha256:dc192e16…`) **and** the switch to a glob path
(`/dev/disk/by-id/ata-PIONEER_BD-RW_*`) in one commit, so this data cannot say
which is responsible — or whether it is neither. Two things argue for caution
before blaming the glob: worker-0 ran the new config for four days without
wedging, and its `/dev/disk/by-id` does not exist, so the glob resolves to
nothing and should be cheap.

**Do not silently revert either half.** If the upstream report does not explain
the trigger, the cheap experiment is to split them: pin the old image with the
new device config, or vice versa, and wait.

## The two candidate fixes

Neither has been applied. They are not mutually exclusive.

**A. Remove the CPU limit, keep the request.** The throttling numbers say the
50m limit is what converts a slow gather into an unrecoverable collapse: at 97%
throttling the process cannot drain its queue no matter how little work is left.
A `requests: 50m` with no limit still protects the node under contention while
letting the process finish a 2ms gather in 2ms. This is the standard shape for a
small latency-sensitive daemon. It is a real change to cluster behaviour and is
**the user's call**, not to be applied unilaterally — noting the user's standing
instruction that raising the *memory* limit was the wrong answer for the same
reason it may be the right one here: fix the mechanism, do not pad around it.

**B. The liveness probe.** Still worth having as the backstop, and it is what
stops the Pushover alerts. It must target `/metrics`, not `/health`.

```yaml
livenessProbe:
  httpGet:
    path: /metrics
    port: http
  periodSeconds: 15
  timeoutSeconds: 5
  failureThreshold: 3
```

`docs/kubernetes.md` says default to readiness and be wary of liveness. This is
the carve-out, and each risk was checked: a restart is demonstrably the only
thing that fixes it; healthy `/metrics` answers in 1.6ms so there is no long
synchronous work in the probe path; `/metrics` has no external dependency, so it
cannot turn one outage into many. **Readiness would accomplish nothing** — the
DaemonSet sits behind no Service, so NotReady would neither restore the device
nor stop the alert. No `startupProbe`: the plugin serves ~95ms after start.

Note `/health` returned 200 on a wedged pod at the early stage and hangs at the
late stage; it is never a correct probe target here.

Once the probe ships, remove `GOTRACEBACK=all` and fold the probe rationale into
`docs/kubernetes.md` before deleting this spec.

## Operational note: you cannot debug a wedged pod the easy way

An ephemeral container from `kubectl debug` joins the **same pod cgroup**, so it
inherits the saturated 50m quota. A `for` loop over eight threads reading
`/proc` got through **one** thread in 17 minutes. Budget accordingly:

- Do not plan interactive forensics. Issue one short command at a time.
- `kill -QUIT 1` is cheap and works — but the dump itself is throttled. The
  worker-1 dump took ~25 minutes to write 405 lines; worker-0 exceeded an hour.
- The container only restarts *after* the dump finishes, so
  `kubectl logs --previous` is empty until then. Poll `restartCount`, and read
  the **current** log to watch it stream in the meantime.

Capture procedure that worked:

```bash
# 1. Confirm which pod
kubectl exec -n infrastructure alertmanager-kube-prometheus-kube-prome-alertmanager-0 \
  -c alertmanager -- wget -qO- \
  'http://kube-prometheus-kube-prome-prometheus.infrastructure.svc:9090/api/v1/query?query=up{job="infrastructure/generic-device-plugin"}' \
| jq -r '.data.result[] | "\(.metric.pod) up=\(.value[1])"'

# 2. Confirm the signature: CPU pinned at the limit is the reliable tell
#    (every HTTP path hangs, so probing paths distinguishes nothing)

# 3. Dump — one short command, distroless image so an ephemeral container is required
kubectl debug -n infrastructure <pod> --image=busybox:1.36 \
  --target=generic-device-plugin -c dbg --attach=false -- sh -c 'kill -QUIT 1'

# 4. Wait for restartCount to increment, then
kubectl logs -n infrastructure <pod> -c generic-device-plugin --previous > dump.txt
```

## Step — File the bug report

The draft is at the end of this file, rewritten 2026-08-19 against the dumps.
Attach both dump files. **The user asked to do the filing themselves** — write
it up, hand it over, do not submit it.

## Verification

Once a fix ships, the regression test is that the next onset does not become a
collapse. The tell is `scrape_duration_seconds` for the job: it should return to
baseline rather than ratchet toward the 10s timeout.

```bash
# CPU pinned at the limit is the earliest reliable signal
kubectl exec -n infrastructure alertmanager-kube-prometheus-kube-prome-alertmanager-0 \
  -c alertmanager -- wget -qO- \
  'http://kube-prometheus-kube-prome-prometheus.infrastructure.svc:9090/api/v1/query?query=rate(container_cpu_usage_seconds_total{namespace="infrastructure",container="generic-device-plugin"}[5m])'
```

With the probe (fix B), expect `reason: Error` from the probe rather than
`OOMKilled`, and downtime under 10 minutes so no alert fires:

```bash
kubectl get pod -n infrastructure -l app.kubernetes.io/name=generic-device-plugin \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[0].restartCount}{"\t"}{.status.containerStatuses[0].lastState}{"\n"}{end}'
```

Also confirm the Phase 1 device selection still holds — only worker-1 advertises:

```bash
kubectl get nodes -o custom-columns='NODE:.metadata.name,CDROM:.status.allocatable.devic\.es/cdrom'
```

### The by-id passthrough is already verified

Done 2026-08-14 and **does not need repeating**: both `/dev/sr0` and `/dev/sg0`
inside the ARM container report `PIONEER / BD-RW   BDR-212U`, and containerd
2.1.6 resolves the by-id symlink, so the documented fallback config was not
needed. Procedure and known-good baseline live in
`docs/automatic-ripping-machine.md` under "Verifying the passthrough", including
why checking a *running* ARM pod is a false pass.

## What Phase 1 did, 2026-08-14

- **Device selection by hardware identity** — the group requires
  `/dev/disk/by-id/ata-PIONEER_BD-RW_*` (mounted at `/dev/sr0`) alongside
  `/dev/sg0`, so the control plane stops advertising a phantom cdrom backed by
  its Talos install ISO. See `docs/automatic-ripping-machine.md`.
- **`GOTRACEBACK=all`**, purely to make the dump complete. It worked; remove it
  once this work is finished.
- **Image pinned by digest** to what was `latest` on 2026-08-14.
- **Memory limit deliberately left at 20Mi.** The user's decision: the pod only
  exhausts memory *because* it is wedged, so fix the cause. Do not raise it.
  (The 2026-08-19 dumps support this from a new angle — memory is not the
  binding constraint at all.)
- **Liveness probe deliberately not shipped**, so the defect stayed capturable.
  That purpose is now served.

### The image bump had a side effect worth knowing about

Bumping the digest also changed the resource **domain**: upstream's default moved
from `squat.ai` to `devic.es`, and `devices.yaml` had never set `--domain`, so it
silently inherited the new one. That renamed the resource and dropped
`squat.ai/cdrom` — still requested by ARM at that moment — to `allocatable: 0` on
every node. ARM kept running on its existing allocation, so nothing looked
broken, but it would have gone `Pending` forever on its next restart.

Caught by a pre-flight check before restarting ARM. Resolved by moving both sides
to `devic.es/cdrom` (the user chose to match upstream's default rather than pin
`--domain`, accepting that a future upstream default change would recur this).

The general lesson: **the manifest was relying on an upstream default for a value
that is half of a contract with another workload.** A "hygiene" image bump is
enough to break that, invisibly, with the failure deferred to an unrelated
restart weeks later.

## The prompt to open with

> The generic-device-plugin pods wedge: they stop serving HTTP entirely, pin
> their CPU at the 50m limit with 97% CFS throttling, and recover only on
> restart. Root cause is abandoned Prometheus gathers accumulating — the scrape
> timeout (10s) does not cancel the gather, so they queue on goCollector's mutex
> forever. Two goroutine dumps are captured and the analysis is in
> `todos/generic-device-plugin-hang.md`; read it, and note that the file records
> several earlier conclusions it disproved. Outstanding: hand me the upstream bug
> report to file, and decide between removing the CPU limit and adding the
> liveness probe.

---

# Upstream bug report — draft

Repository: `squat/generic-device-plugin`. Attach `gdp-goroutines-worker-0.txt`
and `gdp-goroutines-worker-1.txt`, then hand to the user to file.

**Title:** Abandoned `/metrics` gathers accumulate until the plugin saturates its CPU limit and stops serving entirely

## Summary

Under a modest CPU limit, this plugin reaches a state it cannot leave: `/metrics`
stops responding, the process burns 100% of its CPU quota indefinitely, the
kubelet `ListAndWatch` stream stalls so the node's device count drops to 0, and
only a container restart recovers it. It then recurs, roughly daily.

The mechanism is that **a scrape that Prometheus abandons keeps running inside
the process forever.** `promhttp.HandlerFor` is used with no `Timeout`, and
`http.Serve` is called with no server timeouts, so when Prometheus hits its
scrape timeout and disconnects, the in-flight `Registry.Gather` is never
cancelled. Each subsequent scrape queues behind it on `goCollector`'s mutex.
Arrival rate is fixed by the scrape interval and service time only grows, so past
a threshold the queue can never drain.

## Environment

- generic-device-plugin: `ghcr.io/squat/generic-device-plugin@sha256:dc192e164c69b03f156765793a1be62ca437709ae477b27ca7d8f3dcf5021576` (`latest` as of 2026-08-14)
- Kubernetes v1.35.0; Talos Linux v1.12.4; kernel 6.18.9-talos; amd64; containerd 2.1.6
- Resources: **upstream's own manifest defaults** — `requests: 50m/10Mi`, `limits: 50m/20Mi`
- Scraped by Prometheus every 15s with a 10s scrape timeout (kube-prometheus-stack defaults)
- Device config:

```yaml
--device
name: cdrom
groups:
  - paths:
      - path: /dev/disk/by-id/ata-PIONEER_BD-RW_*
        mountPath: /dev/sr0
      - path: /dev/sg0
```

## Evidence

**1. Abandoned gathers accumulate and never terminate.** From a pod wedged ~5
hours, `SIGQUIT` with `GOTRACEBACK=all` shows eight concurrent
`prometheus.(*Registry).Gather` calls, aged **308, 286, 263, 257, 245, 145, 126
and 31 minutes**. The oldest corresponds to the exact minute the endpoint first
exceeded the scrape timeout. They block in two places:

```
goroutine 8366 [sync.WaitGroup.Wait, 308 minutes]:
  prometheus.(*Registry).Gather.func2()
    client_golang@v1.23.2/prometheus/registry.go:473

goroutine 8640 [sync.Mutex.Lock, 245 minutes]:
  internal/sync.(*Mutex).lockSlow(...)
  prometheus.(*goCollector).Collect(...)
    client_golang@v1.23.2/prometheus/go_collector_latest.go:326
  prometheus.(*Registry).Gather.func1()
    client_golang@v1.23.2/prometheus/registry.go:456
```

**2. The onset is abrupt, and the collapse is irreversible.**
`scrape_duration_seconds` for the pod, having been flat for four hours:

```
22:35  0.0017
22:36  0.143     <- 90x
22:37  4.44      <- 2700x
22:38  5.35
...              oscillating 0.5-10s for ~45 minutes
23:20  no successful scrape ever again
```

**3. The process pins its CPU limit and is throttled 97% of the time.**
`rate(container_cpu_usage_seconds_total[5m])` = 0.048–0.050 against
`limits.cpu: 50m`, versus 0.00036 on a healthy pod of the same DaemonSet.
`container_cpu_cfs_throttled_periods_total` = 215,176 of 221,981 periods (97%)
versus 102 of 15,894 (0.6%) on the healthy pod. Cumulative container CPU time
15:44, essentially all after onset, and overwhelmingly **system** time
(`utime=111 stime=17482` on the busiest thread).

I want to be careful about the causal direction here: the CPU limit is not the
bug, but it is what makes the bug unrecoverable. Once gathers overlap, the
process needs *more* CPU to drain the queue and is instead frozen for 95ms out of
every 100ms. I would expect this to be much harder to hit without a CPU limit,
and correspondingly easy to hit at the 50m the shipped manifests specify.

**4. At full wedge the process stops serving HTTP entirely — not just
`/metrics`.** From the same pod, `/nonexistent` did not return within **85
seconds**; `/health` likewise hung. (Earlier in the degradation, `/health` and
404s still answered instantly while `/metrics` hung, so `/health` is misleading
at both stages — see the note under suggested fixes.)

**5. It is not the device, and not node contention.** One affected node has **no
`/dev/sr0`, no `/dev/sg0`, and no `/dev/disk/by-id` directory at all** — the
configured paths match nothing there, and it still wedges. Meanwhile the median
`scrape_duration_seconds` across every other scrape target in the cluster held
flat at 0.005–0.006s straight through the onset minute, so the node itself was
fine.

**6. Device advertisement dies with it.** The stall takes down the kubelet
`ListAndWatch` stream; the node's advertised device count drops to 0 and the
consuming workload becomes unschedulable. Since that workload selects its node
purely by requesting the device, this can also push it onto a different node
holding different hardware.

**7. What it is not.** Ruling these out took measurement, and they may save you
time: there is **no goroutine leak** (22 goroutines total on one wedged pod);
**no file-descriptor leak** (`process_open_fds` flat at 10 for 14 days); and
memory is **not** the binding constraint (wedged pods sat at 14.9 MB, and so did
the healthy one — recoveries have been observed both as OOMKills and as clean
`exitCode 0` restarts).

## Suggested fixes

1. **Bound the gather** — `promhttp.HandlerOpts{Timeout: ...}`, so a gather that
   outlives its client returns an error instead of running forever. This is the
   one that breaks the accumulation.
2. **Set HTTP server timeouts** — `ReadHeaderTimeout` and `WriteTimeout` on the
   `http.Server`, rather than bare `http.Serve`.
3. **Reconsider the CPU limit in `manifests/` and the chart.** 50m is low enough
   that normal jitter can start the cascade, and low enough that the process
   cannot recover from it. A request without a limit would be a safer default for
   a daemon on the metrics path. The 20Mi memory limit is similarly tight against
   a ~10–15 MB baseline.
4. **A `livenessProbe` in the manifests and chart** would make this
   self-healing. Note `/health` is unsuitable: it returns 200 unconditionally,
   and in the observed early stage it did so *while* `/metrics` was already
   hung.

## A debugging note

Because an ephemeral debug container shares the pod cgroup, it inherits the
saturated quota — a loop reading `/proc` for eight threads got through one thread
in 17 minutes, and the `SIGQUIT` dump itself took over an hour to write. Anyone
reproducing this should expect to work one short command at a time.

## Unrelated request

Would you consider publishing semver-tagged releases? The image currently carries
only git SHAs plus `latest`, so Flux `ImagePolicy` and similar ordering-based
automation cannot track it — `semver`, `numerical` and `alphabetical` are all
meaningless over commit SHAs. The Helm chart is versioned, but its DaemonSet
template hardcodes the `--device` arguments, so it cannot be used for custom
device groups like the one above.

### It does not only fail admission — it makes admission slow, 2026-08-25

A third outcome, not covered above or in the ARM spec, observed while restarting
ARM to pick up a new secret. The pod scheduled onto `piraeus-worker-1`
immediately and then sat `Pending` for **~2m30s with no container statuses at
all** — not `ContainerCreating`, not an event beyond `Scheduled`. Then it started
normally and has run since.

Measured at that moment, with the other two nodes as the control:

| node | `/metrics` lines | CFS throttled, 10m |
|---|---|---|
| piraeus-worker-1 | **0** (wget times out) | **97.8%** |
| piraeus-worker-0 | 129 | 0.3% |
| piraeus-control-plane-0 | 129 | 0.3% |

So the wedge was live on worker-1 during the admission. gRPC `Allocate` still
answered — `devic.es/cdrom` stayed `allocatable: 1` throughout and the pod did
get its device — but it answered slowly, because the process serving it is
pinned at its 50m limit.

This matters for how the coupling in `todos/disc-ripping.md` is framed. That spec
says a wedge overlapping an ARM pod recreation makes admission *fail*. It can
also just make it take minutes, which looks like nothing being wrong and is
easy to attribute to a slow SMB mount or image pull. Neither `OpticalDriveUnavailable`
nor anything else fires, correctly — the resource never went away.

It also argues for removing the CPU limit over adding the liveness probe, or at
least ahead of it: a probe restarts a wedged pod after the fact, whereas the
throttling is what turns a wedge into a delay that other workloads feel.

**A wedged pod was left running rather than restarted**, so worker-1's plugin is
available for another goroutine dump for as long as it lasts. Restarting it is
the documented recovery and would destroy that.
