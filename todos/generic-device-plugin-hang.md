# generic-device-plugin `/metrics` hang — outcome B, now testing the gather rate

## Opening prompt

> The generic-device-plugin pods on the worker nodes degrade until a liveness
> probe reaps them, every ~11 minutes on one and ~20 on the other. Read
> `todos/generic-device-plugin-hang.md` from the top, then go straight to **"The
> sawtooth, 2026-09-17"** — the hours-long total wedge described in "The defect"
> is the *pre-2026-08-26* failure and no longer happens. What replaced it is a
> progressive slowdown that resets on every restart. **The open question is what
> flips a process into it.** The flip is in-process (27 of 29 onsets), a median
> **495× step inside one scrape interval** at a median process age of 5.2
> minutes, with no runtime metric moving across it. It is not gather traffic —
> 117 req/s for 45 s, 350× the scrape rate, leaves a healthy process at 2.93 ms
> — and it is not an abandoned gather, since only 1% of onsets begin with a
> timeout. Traffic only amplifies a process that has already flipped. The next
> move is "Catching a flip": the signature is sharp enough to trigger a dump
> automatically, and there are ~11 minutes between flip and first timeout. Fix C (`d310a0f`, live
> 2026-09-17 03:32Z) took both arrival rates 15s → 60s and was shipped on the
> mechanism that test disproved; it is a mitigation, still worth measuring, and
> all three pods being stable right now is **not** evidence it worked. The live
> lead is that onsets correlate across nodes, so look for a cluster-wide event.
> Two measurement traps: never time `/metrics` through `kubectl port-forward`
> (~243 ms of tunnel), and never judge health by median latency — the control
> plane has the worst median of the three and is the one that survives.

## Where things stand

Written 2026-08-14 from recurring `TargetDown` Pushover alerts; **substantially
rewritten 2026-08-19 after two live goroutine dumps**, which disproved most of
the original reading. Treat anything undated as 2026-08-19.

| | |
|---|---|
| Phase 1 — device selection by hardware identity | shipped 2026-08-14 |
| Goroutine dumps captured | done 2026-08-19 |
| Fix A — remove the CPU limit | shipped 2026-08-26 |
| Fix B — liveness probe on `/metrics` | shipped 2026-08-26 |
| Did A and B work? | **outcome B**, confirmed 2026-09-17 — no collapses, no OOM kills, device availability 98.25%/24h, restarts 130 and 75/day on the two workers and 2 on the control plane |
| Fix C — scrape interval and probe period 15s → 60s | shipped 2026-09-17 in `d310a0f`; **live 03:32Z** — a mitigation, on a mechanism since disproved |
| Does load cause the degradation? | **no**, settled 2026-09-17 — 117 req/s leaves a healthy process at 2.93 ms |
| Is the flip in-process or at startup? | **in-process**, settled 2026-09-17 — 27/29 onsets, median 495× step at median age 5.2 min |
| What flips a process into degrading? | **open, and now the whole question** — next move is "Catching a flip" |
| Upstream bug report | **not filed** — draft at the end of this file, Will files it |
| Did C work? | **no** — clean for ~5 days, then the failure returned; worker-0 at 11 restarts/24h and OOMKilled on 2026-09-23 |
| Flip captured? | **yes**, 2026-09-22 04:31:10Z on worker-1 — see "The capture" |

The state the fixes were applied against, 2026-08-26: worker-0 wedged and ten
hours down, the control plane having flapped four times that day, 7-day
availability at 82.7% / 69.4% / 89.9% (control-plane / worker-0 / worker-1),
five restarts each on two of the three, and every pod's last termination
`OOMKilled` exit 137. `TargetDown` had been firing since 08:53. All three rolled
clean at 19:06Z and were serving immediately.

**Two things stated elsewhere in this file were true before 2026-08-26 and are
not now.** Both would waste a session:

- **"CPU pinned at exactly the limit" was the reliable wedge signature.** There
  is no CPU limit any more, so a wedged pod will not sit at a round number. Use
  `up == 0` on the job, or a `/metrics` fetch that times out — and expect the
  probe to have reaped it inside 45s either way, so a wedge is now something you
  catch in metrics after the fact rather than something you find still running.
- **Forensics on a wedged pod was near-impossible**, because an ephemeral
  container joined the same throttled cgroup — a consequence of the 50m limit.
  A dump should now be prompt rather than a 25-minute crawl. But the probe will
  restart the pod out from under you, which is a new obstacle in place of the
  old one: **remove the `livenessProbe` from `devices.yaml` before trying to
  capture another dump.**

## The defect

**This describes the pre-2026-08-26 regime, which no longer occurs.** The dumps
and corrections below still hold and are why the current reading is what it is,
but for present behaviour read "The sawtooth, 2026-09-17" first.

Both plugin pods on the worker nodes stop serving HTTP entirely, burn 100% of
their CPU quota indefinitely, and recover only when the container is restarted.
The device advertisement dies with them.

### What the dumps show

Two dumps taken 2026-08-19 by sending `SIGQUIT` with `GOTRACEBACK=all`, from
pods wedged for 5 and 4 hours respectively.

**Both dump files are lost** (confirmed 2026-09-17; nowhere on this machine and
never committed). Everything below is what was read off them at the time and
cannot be re-checked, so treat the specific figures — the eight gather ages, the
`go_collector_latest.go:326` line, `utime=111 stime=17482` — as quoted rather
than verifiable. They also describe the pre-2026-08-26 regime, which no longer
occurs, so their loss costs less than it appears: a report about the flip wants
a *fresh* capture, not these. Keep new captures at the `--out` path
`tools/gdp-flip-watch/watch.py` was run with, and record that path here when one
is taken. Captures go under `captures/<tool>/` in the checkout — see "Debugging
captures" in CLAUDE.md — which is `.gitignore`d and dies with the worktree;
writing down where they went is the step that was missed.

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

**Both applied 2026-08-26** (Will's decision), in that order of importance.
They were never mutually exclusive, and the OOM finding of 2026-08-25 is what
settled the ordering: restarts were already happening involuntarily, so B makes
recovery fast rather than possible, while A is the only one that attacks how the
collapse forms.

The same finding rules out one thing that looks adjacent: **do not raise the
memory limit.** `anon-rss` at kill time is 11–13 MB against 20Mi — it is cgroup
page cache crossing the line, not the process — and that OOM kill is the only
recovery a pod has if the probe ever stops working.

**A. Remove the CPU limit, keep the request.** The throttling numbers say the
50m limit is what converts a slow gather into an unrecoverable collapse: at 97%
throttling the process cannot drain its queue no matter how little work is left.
A `requests: 50m` with no limit still protects the node under contention while
letting the process finish a 2ms gather in 2ms. This is the standard shape for a
small latency-sensitive daemon. Shipped 2026-08-26 on Will's decision, taken
against his standing instruction that raising the *memory* limit was the wrong
answer — the same reasoning, opposite conclusion, because here the limit is the
mechanism rather than padding around it.

It carries a risk the memory limit did not: **a wedged pod can now take a whole
core on an otherwise-idle node** until the probe reaps it. worker-1 has four and
also runs ARM's transcode. 45 seconds of that is acceptable; a probe that fails
to fire is not, which is part of why B shipped alongside rather than after.

**B. The liveness probe.** The backstop, and what stops the Pushover alerts. It
must target `/metrics`, not `/health`.

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

`GOTRACEBACK=all` stays for now. Its own condition is "once the report is filed
and fixed", and it is not filed — and with the CPU limit gone a dump is no
longer a 25-minute throttled crawl, so keeping the ability is cheap. Remove it,
and fold the probe rationale into `docs/kubernetes.md`, before deleting this
spec.

## Operational note: capturing another dump

Two obstacles, and **the first one is gone since 2026-08-26**. Recorded because
the old text below is what a reader will otherwise budget for.

**Was true, now is not.** An ephemeral container from `kubectl debug` joins the
same pod cgroup, so it inherited the saturated 50m quota: a `for` loop over
eight threads reading `/proc` got through **one** thread in 17 minutes, and the
worker-1 dump took ~25 minutes to write 405 lines while worker-0 exceeded an
hour. With no CPU limit that throttling is gone and a dump should be prompt.

**Is true now.** The liveness probe restarts a wedged pod within ~45s, which is
less time than noticing and reacting takes. **Remove the `livenessProbe` from
`kubernetes/infrastructure/devices.yaml` before attempting a capture**, and put
it back afterwards.

Still true either way: the container only restarts *after* the dump finishes, so
`kubectl logs --previous` is empty until then — poll `restartCount` and read the
**current** log to watch it stream.

Capture procedure that worked:

```bash
# 1. Find the wedged pod. up == 0 is the signature now; CPU is no longer pinned
#    at a round number, because there is no limit to pin it to.
kubectl exec -n infrastructure alertmanager-kube-prometheus-kube-prome-alertmanager-0 \
  -c alertmanager -- wget -qO- \
  'http://kube-prometheus-kube-prome-prometheus.infrastructure.svc:9090/api/v1/query?query=up{job="infrastructure/generic-device-plugin"}' \
| jq -r '.data.result[] | "\(.metric.pod) up=\(.value[1])"'

# 2. Confirm by fetching /metrics directly and watching it time out.
#    Probing other paths distinguishes nothing: at full wedge every path hangs.

# 3. Dump — one short command, distroless image so an ephemeral container is required
kubectl debug -n infrastructure <pod> --image=busybox:1.36 \
  --target=generic-device-plugin -c dbg --attach=false -- sh -c 'kill -QUIT 1'

# 4. Wait for restartCount to increment, then
kubectl logs -n infrastructure <pod> -c generic-device-plugin --previous > dump.txt
```

One question a fresh dump could still answer, which the two existing ones do
not: the CPU time at wedge was overwhelmingly **system** time — thread 11 on
worker-1 showed `utime=111 stime=17482`. That is a syscall pattern rather than a
compute one, and nothing here explains what those syscalls are. If A turns out
not to have prevented the collapse, that is where to look next.

## Step — File the bug report

**The draft at the end of this file is not filable as it stands**, for two
reasons found 2026-09-17. Its dumps are lost, so there is nothing to attach; and
its thesis — abandoned gathers accumulating until the plugin saturates its CPU
limit — is the pre-2026-08-26 mechanism, which the flip evidence contradicts.
Filing it would send upstream after a queue that forms *after* the interesting
event. Rebuild it around the flip once `tools/gdp-flip-watch/` has caught one:
that capture is the evidence, and a 495× step with no runtime metric moving is a
far better report than a pile-up.

**The user asked to do the filing themselves** — write it up, hand it over, do
not submit it.

Checked 2026-08-26: upstream's last binary release is `0.2.0` (2026-04-14),
nothing since May touches the metrics path, and the single open issue is
unrelated. So this is unreported and no fix is pending.

Its **Environment** block deliberately still says `limits: 50m/20Mi`. That is
the configuration the bug was observed under, it is *upstream's own manifest
default*, and that is the point — the report's third suggestion is that those
defaults are the problem. Do not update it to match what this cluster runs now.
If the local removal of the CPU limit produces a result before the report goes
in, add it as a separate data point rather than by editing the environment.

## Verification

The regression test is that an onset no longer becomes a collapse. The tell is
`scrape_duration_seconds` for the job: it should spike and return to baseline
rather than ratchet toward the 10s timeout.

```bash
# Availability is the number that answers the question. Compare against the
# 2026-08-26 baseline in "Where things stand": 82.7% / 69.4% / 89.9%.
avg_over_time(up{job=~".*generic-device-plugin.*"}[7d])

# Onset shape: does it recover, or ratchet toward 10?
scrape_duration_seconds{job=~".*generic-device-plugin.*"}
```

`rate(container_cpu_usage_seconds_total{container="generic-device-plugin"}[5m])`
is still worth looking at, but it is no longer the *signal* it was: with no
limit there is no round number to pin at, and a healthy pod reads ~0.0003 cores
against a wedged one reading whatever the node will give it.

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
  That purpose was served by the 2026-08-19 dumps, and the probe shipped
  2026-08-26.

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

## It does not only fail admission — it makes admission slow

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
get its device — but it answered slowly, because the process serving it was
pinned at the 50m limit that has since been removed.

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

## What to watch now that both have shipped

Applied 2026-08-26 with worker-0 wedged and 10 hours down, the control plane
having flapped four times that day, and 7-day availability at 82.7% / 69.4% /
89.9% (control-plane / worker-0 / worker-1). All three pods' last termination
was `OOMKilled`, exit 137.

The regression test is unchanged and is in **Verification** above:
`scrape_duration_seconds` should return to baseline after an onset rather than
ratchet toward the 10s timeout. Three things distinguish the outcomes:

- **A worked** — onsets stop becoming collapses. `scrape_duration_seconds`
  spikes and recovers, restart count stops climbing.
- **A did not work but B caught it** — restarts continue, but each wedge lasts
  ~45s rather than hours, and availability goes to ~99%. The remaining question
  would then be the `stime`-dominated CPU profile (`utime=111 stime=17482`),
  which says the work is syscalls rather than compute and which nothing here
  explains.
- **Neither worked** — restart count climbs *faster* than before, because the
  probe is now reaping wedges the OOM killer used to take hours to reach. That
  looks like a regression in `kube_pod_container_status_restarts_total` and is
  not one; read availability, not restarts.

Watch for one new risk that did not exist before: with no CPU limit, a wedged
pod can take a whole core on an idle node until the probe reaps it. Worker-1 has
four cores and also runs ARM's transcode. 45 seconds of that is acceptable; if
the probe ever fails to fire, it is not.

Still open and untouched by this change: the correlation between the
2026-08-14 commit and worker-0's first-ever failures. `3e4e016` bundled the
image bump with the glob path, the data cannot separate them, and that commit is
also what made ARM's device matching correct — so it is not casually reverted.

## The sawtooth, 2026-09-17

All timestamps and day buckets here are UTC; the work was done on the evening of
2026-09-16 Denver time.

**The answer to the section above is outcome B.** A and B did what they were
meant to: there are no collapses any more, no OOM kills, and `devic.es/cdrom`
was allocatable 98.25% of the 24h to 2026-09-17. What is left is not the defect
described in "The defect" — that section describes the pre-2026-08-26 regime and
should be read as history.

### What replaced it

Gather latency against *process age*, worker-1, 17 process lifetimes in 3h:

| process age | median `scrape_duration_seconds` |
|---|---|
| 0–1 min | 7.3 ms |
| 1–2 min | 113 ms |
| 2–3 min | 190 ms |
| 4–5 min | 404 ms |
| 7–8 min | 965 ms |
| 9–10 min | 1813 ms |
| 12–13 min | 2161 ms |
| 16–17 min | 3315 ms → crosses the 5s probe timeout, reaped |

Every process starts healthy and degrades ~1.25× per minute until the probe
kills it. **Restart resets it completely.** High restart count with low downtime
is the fix working, exactly as "Neither worked" warned it would look — and it is
not that case, because availability is 98.25%.

The ~17 minutes this sample ends at is worker-1's figure *from these three
hours*; over the full day it averages ~11, and worker-0 ~19. Use the per-node
baseline under "Fix C" rather than any number from this table, which is a shape
rather than a rate.

### Gather traffic amplifies a degraded process; it does not degrade a healthy one

Scraping a **already-degrading** pod's `/metrics` every 3s and watching the
container:

```
every 3s:    0.51c → 0.88c → 1.97c → 1.94c → 3.20c   (4-core node)
             goroutines 17-23, heap ~5MB, fds 11, threads 9 — all flat
stop 45s:    0.018 cores
```

The process is **idle when nothing scrapes it**, and in this state each gather
makes the next one dearer, so arrivals compound into collapse. Consistent with
the dumps' finding that nothing leaks — there is no accumulated state, only a
queue. The CPU figures are `process_cpu_seconds_total` read from inside the
process, so they are not an artefact of how the request was delivered.

**The same load does nothing to a healthy process.** Tested 2026-09-17
03:51–03:55Z against worker-0, chosen because it advertises no
`devic.es/cdrom` so a reap costs nothing:

| load | in-cluster gather, max | container CPU |
|---|---|---|
| 4 req/s for 90 s | 2.9 ms | 0.004 cores |
| **117 req/s for 45 s** (5383 requests, ~350× the scrape rate) | **2.93 ms** | 0.073 cores |

Zero errors, zero reaps, cost linear at ~1.8 ms of CPU per gather throughout.

**So arrival rate is an amplifier, not a trigger.** Something else flips a
process from ~1.8 ms per gather to hundreds of ms, and only then does traffic
compound it into a reaping. This is the single most important correction in this
file: `d310a0f`'s commit message and the reasoning behind Fix C both assert that
gather traffic causes the degradation, and it does not.

**Do not trust wall-clock latency measured through `kubectl port-forward`.** The
tunnel relays via the apiserver and costs **~243 ms per request** here: the same
pod in the same minute read 245 ms through the tunnel and 1.8 ms to Prometheus
in-cluster. An earlier reading of this file claimed a "~200 ms base gather for a
129-line payload" and built a mystery on it; that number was the tunnel. Use
`scrape_duration_seconds`, or `process_cpu_seconds_total` deltas, both of which
are measured in-cluster or inside the process.

**Always filter onsets by `process_start_time_seconds`.** A naive
stable→degraded detector cannot tell a running process flipping from a fresh
process that came up already slow, and the two have opposite meanings. Detecting
without that filter produced an apparent finding that
`go_gc_duration_seconds_count` falls 2004 → 411 at onset — a monotonic counter
decreasing, which is only possible because the "after" sample came from a
younger process. The tell is any counter going down; if one does, the population
is contaminated with restarts.

### There are three modes, and a process flips between them mid-life

Restarts per day, reconstructed from the counter (scrape coverage is complete on
all 30 days, so the zeroes are real and not gaps):

| days | control-plane | worker-0 | worker-1 |
|---|---|---|---|
| 08-27 … 09-03 | 0 | 0 | ~22/day |
| 09-04 … 09-10 | 0 | 0 | ~130/day |
| **09-11 … 09-15** | **0** | **0** | **0** |
| 09-16 (cluster reboot) | 2 | 64 | 111 |

Median gather during 09-11…09-15 was **1.7 ms** — same pod, same config, same
scrape rate as the days either side. The 2026-09-16 reboot moved **all three
nodes** off the stable mode, the control plane included, after it had been clean
since 08-27.

**A process is not born into its mode; it flips, discontinuously, a few minutes
in.** Of 29 clean onsets on worker-1 between 08-27 and 09-16, **27 kept the same
`process_start_time_seconds` across the transition** — the process was running
before and after:

```
09-05 00:49:15    1.5 ms →   746.2 ms    process age  3.2 min
09-06 12:00:30    1.7 ms →  4338.3 ms    process age  4.2 min
09-06 18:16:15    1.6 ms →  8885.6 ms    process age  6.2 min
09-08 09:41:45    1.5 ms →  1456.0 ms    process age 36.9 min

n=27   step: median 495x, inside one 15 s scrape interval
       age at flip: median 5.2 min, range 1.0–52.3
```

Across that step, `go_goroutines`, `go_threads`, `process_open_fds`,
`go_memstats_heap_inuse_bytes`, `go_memstats_next_gc_bytes` and
`go_sched_gomaxprocs_threads` are all unchanged. Nothing in the runtime's own
accounting moves with it.

**It is not an abandoned gather either.** Only 1% of onsets begin with a 10 s
timeout; 66% begin between 50 ms and 1 s, median 250 ms, and the two minutes
before a flip are clean at 6.4 ms max. The 2026-08-19 reading — a scrape
Prometheus gave up on holding `goCollector`'s mutex forever — cannot be the
trigger, because there is no timeout to abandon. Those eight aged gathers in the
worker-0 dump are what the *degraded* state accumulates, not what starts it.
Median time from flip to the first 10 s timeout is 11.2 minutes.

**Node-local signals do not explain it.** Comparing 151 degraded against 328
stable hours on worker-1 over 08-27…09-16: `sr0` io_time ratio 0.09, `sr0`
reads/s 0.15, ARM CPU 0.65 — all *lower* while degraded, which reads as effect
rather than cause, since a busy plugin polls devices less often. Interrupts run
1.39× higher, consistent with the `stime`-dominated CPU the dumps showed, and
also an effect. `node_load1`, context switches and etcd fsync p99 are flat.

**Degrading does not always end in a reaping, and the median does not say which
way it goes.** Gather latency over the rolling 24 h to 2026-09-17 03:30Z — the
last full day at a 15 s arrival rate, and a different window from the UTC
calendar days above, which is why its restart counts are higher:

| node | p50 | p90 | p99 | max | samples > 5 s | restarts |
|---|---|---|---|---|---|---|
| control-plane | 590 ms | 1292 ms | 2751 ms | 5187 ms | 1 / 1433 | 2 |
| worker-0 | 309 ms | 2019 ms | 6529 ms | 10015 ms | 28 / 1433 | 75 |
| worker-1 | 395 ms | 4361 ms | 10000 ms | 10002 ms | 106 / 1433 | 130 |

**The control plane has the worst median of the three and the best tail.** Its
p50 is nearly double worker-1's, yet its p99 is 2.75 s against worker-1's 10 s,
and its post-reboot process ran from 2026-09-16 06:24Z for twenty-one hours
without being reaped. The two restarts against its name are the reboot itself.

So "degrades ~1.25× per minute until the probe kills it" is the worker pods'
behaviour, not a law, and degradation is not one thing with a severity dial: a
process can sit two to three orders of magnitude above the stable mode
indefinitely, because what reaps it is tail excursions past 5 s, not where the
distribution sits. Median latency is therefore the wrong health signal — it
ranks the survivor worst. (The control plane did cross 5 s once; `failureThreshold`
is 3, and one crossing is not three consecutive ones.)

Whether the workers also find a ceiling and simply find it above 5 s, or
genuinely climb without bound, is not answerable from the 3 h sample — every
worker process in it was reaped before it could show one, which is itself an
argument for reading time-to-first-restart under Fix C rather than latency.

Ruled out with data, so as not to be re-tried: CPU steal (≤0.04 everywhere),
node contention (worker-1's non-plugin busy time moves 0.08 → 0.17 cores, and
most of "busy" on bad days *is* the plugin), `sr0` I/O and ARM activity (flat
across both regimes), and any growth in goroutines, heap, fds or threads.

**Still unexplained, and now the whole question:** what flips a process from
~1.8 ms per gather into the degrading mode. It is not load, not device presence
(worker-0 has no `/dev/disk/by-id` at all and degrades), not steal, not node
contention, and nothing leaks. The strongest clue is that **onsets correlate
across nodes** — the 2026-09-16 reboot moved all three off the stable mode at
once, and the 2026-08-25 OOM kills landed 25 seconds apart on separate VMs. That
points at a cluster-wide or scrape-side event rather than anything node-local,
which is the lead to chase next: correlate known onset times against Prometheus
restarts and reloads, apiserver restarts, and etcd stalls, all of which are in
the 30 days of retention.

A dump is still wanted, and is now catchable. It cannot be *manufactured* —
load will not produce a degrading process — but the flip has a precise
signature: a single-interval step past 50 ms from a run of sub-10 ms samples,
with `process_start_time_seconds` unchanged. Median 11.2 minutes separate that
step from the first 10 s timeout, which is a comfortable window to fire a
`SIGQUIT` into. See "Catching a flip" below.

What a dump has to explain: a **495× step in gather cost with nothing in the
runtime's accounting moving**. Grab per-thread `utime`/`stime` from
`/proc/1/task/*/stat` before the `SIGQUIT`, because the one unexplained clue
from 2026-08-19 is that the CPU is overwhelmingly system time
(`utime=111 stime=17482`), which says syscalls rather than compute and which
nothing in this file accounts for.

### Fix C — take the arrival rate down

`kubernetes/infrastructure/devices.yaml`: PodMonitor `interval` and the liveness
probe's `periodSeconds` both 15s → 60s. Two gathers per 15s become two per 60s.

**It was shipped on a mechanism since disproved.** The reasoning was that
arrivals drive the degradation; the 117 req/s test above shows they do not. What
Fix C can still do is reduce the amplification *after* a process has flipped,
which should stretch time-to-first-restart — but it cannot prevent a flip, so it
is a mitigation and not a fix. Left in place because a quarter of the gather
traffic is worth having either way, and because the stretch is worth measuring.

Read the result with that in mind: **all three processes being stable is not
evidence C worked.** 09-11…09-15 had all three stable for five days at the old
15 s rate. Only a flip that then takes much longer than its per-node baseline to
reach a reaping says anything.

**The baseline C is measured against**, 24 h to 2026-09-17 03:30Z — the last
full day at a 15 s arrival rate, ending 2 minutes before the rollout:

| | control-plane | worker-0 | worker-1 |
|---|---|---|---|
| restarts / 24 h | 2 (the reboot) | 75 | 130 |
| mean interval between restarts | never reaped | ~19 min | ~11 min |
| `devic.es/cdrom` allocatable / 24 h | n/a | n/a | 98.25% |

Per-node spread is wider than any single number implies, so compare each node
against its own row rather than against a cluster figure.

```sh
# Fix C went live 2026-09-17 03:32Z; both of these read 60.  Re-check after any
# Flux change that touches the DaemonSet, since a revert would silently restore
# the 15 s rate and invalidate everything measured after it.
kubectl -n infrastructure get podmonitor generic-device-plugin \
  -o jsonpath='{.spec.podMetricsEndpoints[0].interval}{"\n"}'
kubectl -n infrastructure get ds generic-device-plugin \
  -o jsonpath='{.spec.template.spec.containers[0].livenessProbe.periodSeconds}{"\n"}'

# The measurement. Compare against ~11 min (worker-1) and ~19 min (worker-0).
kubectl -n infrastructure get pods -l app.kubernetes.io/name=generic-device-plugin \
  -o custom-columns=POD:.metadata.name,RESTARTS:.status.containerStatuses[0].restartCount,AGE:.metadata.creationTimestamp
```

The cost if it fails: detection of a genuine total wedge goes from ~45s to
~180s, and `devic.es/cdrom` is unallocatable for that window. Given ARM is the
only claimant and is not restarting on its own, that is cheap.

This does not displace the split-`3e4e016` experiment above, which is still the
only thing that addresses *why* worker-0 ever started failing.

### Catching a flip

`tools/gdp-flip-watch/` watches every plugin instance and fires the capture when
one flips. `detect.py` holds the predicates and `test_detect.py` their tests,
including the restart-conflation case that is the whole reason they are tested
functions rather than inline comparisons.

```sh
python3 tools/gdp-flip-watch/watch.py --dry-run          # detect only
python3 tools/gdp-flip-watch/watch.py --out ~/gdp-flip --node piraeus-worker-1 --once
```

It reaches Prometheus by exec-ing the Alertmanager pod rather than through a
port-forward, because a tunnel does not survive the hours this has to run. On a
confirmed flip it writes `<node>-<stamp>.threads.txt` (per-thread
`utime`/`stime` and `wchan`, collected *before* the kill, since `SIGQUIT`
destroys that evidence) and `<node>-<stamp>.goroutines.txt`. **Record the
`--out` path here when a capture lands** — losing the 2026-08-19 dumps was a
failure to write down where they went.

**Detection is two-stage, because an onset is not a flip.** Every excursion
above 50 ms is appended to `excursions.jsonl` in the output directory —
transients included, since whether flips begin as transients is unanswered and
keeping the ones that recover is the only way to find out. The destructive
capture fires only once three consecutive samples stay elevated. Use `--once`:
without it the watcher fires on every subsequent flip.

**The healthy control for the `stime` clue**, taken 2026-09-17 from worker-0's
idle process — the thing the 2026-08-19 dumps lacked:

| | utime | stime |
|---|---|---|
| thread 1 | 2 | 0 |
| worker threads | 61–93 | 26–35 |
| **degraded, 2026-08-19 worker-1 thread 11** | **111** | **17482** |

Healthy runs about 2.4:1 utime:stime with every thread parked in
`futex_do_wait`. Degraded inverts that to 1:157. Whatever the flip is, it turns
a process that is mostly idle into one that is almost entirely in the kernel.

### Transients exist, and Fix C has a first real signal

First run, 2026-09-17 04:26Z → 16:04Z on all three nodes, capturing worker-1
only. Two excursions, **neither a flip**:

```
06:23:32Z  worker-0   1.5 ->  98.5 ms                     recovered in 1 sample
08:56:14Z  worker-1   8.1 -> 775.6 -> 138.1 -> 5.5 ms     recovered in ~3 min
```

Both recovered unaided, with no restart — which also retires "a restart is the
only thing that clears a wedge", true of the old regime and not of this one.
Neither process was reaped; all three have run since 03:32Z with zero restarts.

**Fix C did not hold, 2026-09-23.** It bought about five clean days —
2026-09-17 03:32Z to the first real flip on 2026-09-22 04:31Z — and then the
failure returned. By 2026-09-23 worker-0 is at **11 restarts in 24 h with
`OOMKilled` exit 137**, the memory backstop doing the reaping again, while
worker-1 sits degraded around 700 ms without being reaped at all, in the same
bounded mode the control plane held on 2026-09-16.

Five days is not distinguishable from the 09-11…09-15 stable run at the old
15 s rate, so C cannot be credited with it. That resolves the tension the
2026-09-20 reading flagged, and in the direction the 117 req/s test predicted:
arrival rate is not the trigger, so halving it was never going to prevent a
flip. **Fix C is a mitigation that bought time and nothing more.** Leave it —
a quarter of the gather traffic costs nothing — but stop treating it as the
experiment.

### The capture, 2026-09-22

Taken 04:31:10Z on worker-1, two minutes into a flip, at
`captures/gdp-flip/piraeus-worker-1-20260922t043110.{threads,goroutines}.txt` in
the **main checkout**. The ledger entry that triggered it shows three distinct
climbing scrapes, which is the dedupe working:

```
{"event": "flip", "node": "piraeus-worker-1",
 "samples": [53.934736, 91.496143, 413.358715], "t": "2026-09-22T04:31:10Z"}
```

**There is no metrics machinery in the process at all.** Zero occurrences of
`Gather`, `promhttp`, `client_golang`, `goCollector`, `metrics.Read`,
`ServeHTTP` or `stopTheWorld` across the whole dump, taken while scrapes were
costing 413 ms. All 26 goroutines are parked — `chan receive`, `IO wait`,
`select`, GC workers idle — and the only non-Go frames are the plugin's own run
loops and gRPC transports.

That retires the queueing family of explanations for the *trigger*. A flip is
not a stuck gather, a queued gather, or a mutex someone is holding: between
scrapes the process is entirely idle, and when a scrape arrives it completes,
just slowly. The eight aged `Registry.Gather` calls in the lost 2026-08-19 dump
were a late-stage consequence of hours of amplification, not the cause.

**The system-time signature is real and early.** Per-thread `utime`/`stime` from
the same capture:

| | utime | stime | stime:utime |
|---|---|---|---|
| healthy, worker-0 idle 2026-09-17 | 61–93 | 26–35 | 0.42:1 |
| **flipped, worker-1 2026-09-22** | **27** | **178** | **6.59:1** |
| quoted 2026-08-19, dump lost | 111 | 17482 | 157:1 |

A 16× shift toward the kernel two minutes in, on a process whose goroutines are
all asleep. Whatever the flip is, it makes the syscalls a gather performs
expensive, rather than making Go code run longer — which is where to look next,
and the `/proc` read is now the cheap instrument for it.

### The ledger oversampled, 2026-09-20

The first three days of `excursions.jsonl` record four "confirmed flips" on
worker-0 and they are all artefacts. Their sample arrays are byte-identical
floats:

```
{"event": "flip", "node": "piraeus-worker-0",
 "samples": [887.510338, 887.510338, 887.510338], "t": "2026-09-19T17:05:04Z"}
```

Three reads of one scrape, not three scrapes. The watcher polls every 15 s while
the PodMonitor scrapes every 60 s, so an instant query returns the same value
four times over, and `CONFIRM_RUN` counted reads. `MIN_RUN` was compromised the
same way: ten "stable" samples were two and a half real scrapes. Nothing was
captured only because `--node` was worker-1 and every artefact was on worker-0.

`sample()` now carries each sample's own timestamp from `timestamp()` — not the
query evaluation time, which is the same for every poll — and the loop skips an
instance whose scrape has not advanced. `is_sustained` also rejects exact
repeats as a backstop, because this failure is silent and reads as success.

**The general trap:** polling a derived store faster than it updates does not
oversample, it fabricates agreement. Any rule of the form "N consecutive
samples" needs those samples to be distinct observations, and the way to know
is to carry the source timestamp rather than the read time.

**The capture is destructive and the watcher must be aimed deliberately.**
`SIGQUIT` is fatal to a Go process, so the container restarts; on worker-1 that
withdraws `devic.es/cdrom` for the restart, and an ARM pod admitted in that
window is rejected permanently — the failure this file already documents. That
is why `--node` defaults to capturing nowhere useful until named, and why
`--dry-run` exists. Aim it at worker-0 or the control plane if a flip there will
do; aim it at worker-1 only knowingly, since worker-1 is where flips are
frequent.

Two smaller catches: each capture leaves a terminated ephemeral container on the
pod, which cannot be removed until the pod is recreated, and a capture
contaminates that node's Fix C sample.

### Correction to the ARM coupling

"It does not only fail admission — it makes admission slow" frames the coupling
as wedge-overlaps-recreation. Two `UnexpectedAdmissionError` pods found in the
`automatic-ripping-machine` namespace on 2026-09-17 were **both** killed by the
2026-09-16 cluster reboot, not by a wedge: kubelet re-admits pods before the
plugin re-registers, the allocation fails, and the rejection is terminal. One of
them had been created 2026-09-04 and run healthily for twelve days — a pod's
`startTime` is when it was created, not when it failed, which is what made the
wedge reading look right.

Nothing GCs those pods: `--terminated-pod-gc-threshold` is unset, so the default
12500 never triggers here, and the ReplicaSet controller replaces Failed pods
rather than deleting them. `KubeContainerWaiting` then fires on the corpse
indefinitely, which is how this was found — twelve days late. **Nothing alerts on
`UnexpectedAdmissionError`**, and a rule for it is the gap worth closing: a
device outage too short for `OpticalDriveUnavailable`'s 30m `for:` still leaves a
permanent casualty.

Both pods were deleted 2026-09-20 after checking they held nothing the above
does not already record — same rejection message, same resources block, same
container states, and their Events had long since aged out. Their full JSON is
archived under `captures/arm-zombies/`. Deleting them is what silences the
`KubeContainerWaiting` that had been firing on the 09-04 corpse for 16 days.
---

# Upstream bug report — draft

Repository: `squat/generic-device-plugin`.

**Superseded — do not file unedited.** Kept because its Environment block and
its account of the queueing behaviour are still accurate for a process that has
already degraded, and rewriting from nothing would lose that. What it gets wrong
is the cause: see "Step — File the bug report" above. The dumps it says to
attach no longer exist.

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
