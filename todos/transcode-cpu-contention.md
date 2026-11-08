# Keep ARM's transcodes from slowing everything else on worker-1

## Opening prompt

> Read `todos/transcode-cpu-contention.md`. ARM's jobs now run pinned off
> CPU 0 with no CPU limit, SVT-AV1 is held at level of parallelism 3, and
> worker-1 has 6 vCPUs at half the default host CPU weight. Measure the first
> Blu-ray transcode since those landed against the figures below, decide
> whether `NodeSystemSaturation` still fires and whether it needs changing, and
> close the project if the checks pass. Do not change the plugin's memory limit
> or GOMAXPROCS while doing so.

## Where things stand

Opened 2026-11-05 while closing `generic-device-plugin-memory`; reworked the same
day. All figures from Prometheus unless stated.

### The mechanism, measured

Worker-1 had 4 vCPUs, and ARM a 3000m limit with 500m requested. HandBrake
(1.11.2, SVT-AV1 v4.1.0, preset 4) ran 124 threads at nice 10 on all four CPUs.
The limit is a CFS quota, not a core reservation: HandBrake spent its 300ms per
100ms period across all four CPUs in about the first 75ms, and was throttled in
69% of periods. Until it was throttled, no CPU was free, and every other tenant
waited. `nice` does not help, because it ranks threads only within their own
cgroup.

**The pin test, 2026-11-05.** Job 99's running HandBrake was moved to CPUs 1–3
with `taskset -a -p -c 1-3` at 20:51:45 UTC, limit unchanged. The 20 minutes
before against the 20 after:

| | before | after |
|---|---|---|
| generic-device-plugin CPU wait | 1.38 ms/s | 0.44 ms/s |
| alloy | 58 ms/s | 27 ms/s |
| victoria-logs | 59 ms/s | 37 ms/s |
| metallb-speaker | 15 ms/s | 9.1 ms/s |
| kube-state-metrics | 9.8 ms/s | 3.7 ms/s |
| ARM periods throttled | 69% | 2% |
| ARM CPU | 2.80 cores | 2.54 cores |
| load1, mean / max | 9.4 / 12.6 | 9.3 / 12.8 |
| node CPU pressure ("some") | 65% | 65% |

Node CPU pressure and load did not move, because both mostly count HandBrake's
threads queueing behind each other. The per-container waits are what measure the
harm; the 60% node pressure figure this spec opened with does not.

### `NodeSystemSaturation`

The stock node-mixin rule: `node_load1` per CPU above 2 for 15 minutes, so
load 8 on 4 vCPUs. Blu-ray transcodes hold load between 7 and 14 with no blocked
processes. 0 to 18 of HandBrake's threads were runnable at each of ten samples.
It fired 0.5–1.2h a day from 10-24 to 10-29 and 0.1–0.25h a day from 11-02, on
every Blu-ray and never on a DVD. It is not related to the 11-01 change.

The pin did not lower the load, and pinning alone will not: see the wrong turn
below. At 6 vCPUs the threshold becomes load 12. The figures above put a level-3
encode's mean below that and its peaks around it. Whether the 15-minute `for`
still trips is the first thing to measure.

### What changed on 11-01: not found

With more days included, the plugin's CPU wait during Blu-ray transcodes was
0.8–1.3 ms/s from 10-23 to 10-28 and 1.1–1.5 ms/s from 11-01 to 11-05. Node CPU
pressure was 0.63–0.67 against 0.61–0.64. The shift is about 25%, not the 40%
first read from 10-24 alone. Ruled out:

- **What was encoded.** Every long transcode from 10-23 to 11-05 is a Downton
  Abbey Blu-ray (jobs 78–98), on both sides of 11-01.
- **The Proxmox host.** Its own RRD shows 29–38% CPU, load 4.7–5.8 and ~4%
  iowait in every transcode window from 10-24 to 11-05.
- **Everything else checked:** kernel 6.18.9-talos and Talos v1.12.4, with no
  reboot; the ARM image (2.24.4); the plugin pod (up since 10-15, no restarts);
  and the CPU use of every other pod on worker-1 (all 0–33m, flat).

The first Blu-ray after ARM's 10-28 23:38 redeploy was job 93, which started on
10-31 at 20:14 and ran into 11-01. Nothing in that deploy touched transcoding
beyond title selection. If the pin removes the mechanism, the cause may not
matter.

## What landed on branch `transcode-cpu-contention`

- `arm-disc-wrapper.sh` runs the job under `taskset -c 1-(N-1)`, where N is the
  number of online CPUs.
- ARM's CPU limit is gone; the request stays at 500m.
- The HandBrake preset passes `lp=3` to SVT-AV1.
- Worker-1 is set to `cores = 6` and `cpu_units = 50` in Terraform. Every VM's
  `cpu.weight` reads 100 on the host, so 50 is half. The LXCs 105 and 108 have
  1024.

**Host headroom, read 2026-11-05.** The host is an i7-6800K with 6 cores and 12
threads, and 15 GiB of RAM free. The VMs have 16 vCPUs between them. Over the
past month, half-hour peaks of host CPU had a median of 25%, a 95th percentile
of 50% and a maximum of 89% (10-13 04:30, with worker-0 at 3.9 cores during the
night's backups). Average use over the month: control plane 0.3 cores (highest
1.09), worker-0 0.75 (highest 3.9), worker-1 1.11 (highest 3.58). Beyond 6 busy
threads, the encoder shares physical cores, so 6 vCPUs will speed it up less
than 6/4. That is expected and has not been measured.

**Terraform state.** The `terraform.tfstate` in the primary checkout is from
2026-02-04 and has no `module.talos_worker_1`, so a plan from it would create
the VM rather than update it. Plan from wherever the current state lives, and
target `module.talos_worker_1`: the Talos module has unrelated pending changes.
The expected diff is `cpu.cores` 4 → 6 and `cpu.units` 0 → 50, and nothing else.
`automatic_reboot` is true, so the apply reboots worker-1 itself.

## Checks on the first Blu-ray after deploy

Compare windows of similar length, since the GC-pause quantile summarises the
runtime's last 256 pauses, about six hours of GCs here.

- HandBrake's affinity reads CPUs 1–5:
  `grep Cpus_allowed_list /proc/$(pgrep -o HandBrakeCLI)/status`. Its log
  shows no `encsvtav1: error parsing option lp`.
- Per-container CPU wait for the plugin, alloy, victoria-logs and the metallb
  speaker is at or below the "after" column.
- ARM's memory: RSS peaked at 6.4–6.7 GiB of 8 GiB over the 14 days to 11-05.
  `lp=3` should hold it there; confirm.
- ARM's CPU use against the 5 CPUs it may use, and the transcode's duration
  against a comparable Downton Abbey disc (jobs 93–98), to see what the extra
  vCPUs bought.
- `NodeSystemSaturation`: does it fire? If it does, decide whether to raise
  `lp` (more threads), replace the rule for this node with one built on the
  per-container waits, or accept it.
- The plugin's median GC pause. `docs/automatic-ripping-machine.md` still says a
  transcode lifts it to ~0.5ms; correct that paragraph to what is measured.
- Control-plane steal and etcd latency during the next night's backups, now
  that worker-1 can demand 6 vCPUs, though at half the weight.

## Wrong turns

- **Load average, busy % and ARM's thread count do not separate the good days
  from the bad.** All three read the same on 10-24 as on 11-05. The difference
  only shows in per-container CPU wait.
- **`kubectl top` and the plugin's CPU use say nothing here.** The plugin uses
  0.4 millicores either way; what changes is how long it waits.
- **Pinning alone does not shrink SVT-AV1.** Its Parameters.md says that without
  `--lp`, parallelism follows "the N cores available for the process". It was
  offered as the reason pinning would lower the load before anyone read the code.
  v4.1.0's `get_num_processors()` is `sysconf(_SC_NPROCESSORS_ONLN)`, the CPUs
  online in the machine, whatever the affinity. With 3–5 CPUs it picks level 3,
  with 6–11 level 4, and its docs say levels of 4 and above in CRF mode cost
  "much higher memory". Going to 6 vCPUs without `lp=3` would have moved the
  encoder to level 4 against a container already at 6.7 of 8 GiB.
- **SVT-AV1 asks for real-time priority.** `enc_switch_to_real_time()` sets
  SCHED_FIFO 99 when running as root, and ARM runs as root. The call fails because
  the container lacks `CAP_SYS_NICE`; all 124 threads read `TS` at nice 10.
  Granting it would let the encoder preempt everything on the node.
