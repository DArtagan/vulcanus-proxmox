# Test whether generic-device-plugin's degradation is its memory limit

## Opening prompt

> Read `todos/generic-device-plugin-memory.md` and its evidence file. The plugin's
> memory limit went from 20Mi to 64Mi as an experiment; nothing else changed.
> Check the readouts under "What decides it" against the dates the change
> reached the cluster, and say which outcome the numbers support. Do not close
> the spec before the two weeks are up, and do not touch GOMAXPROCS, the probe
> or the scrape interval while it runs.

## Where things stand

Opened 2026-10-15, after two weeks of `DevicePluginDegraded`. The
`generic-device-plugin-hang` project closed on 2026-10-03, crediting a
`GOMAXPROCS=2` pin with ending the degradation. From 2026-10-06 all three
replicas degraded again for 18–24h a day, the 2-core control plane included,
with GOMAXPROCS still reading 2 on every one.

**The hypothesis: the 20Mi memory limit is the cause.** Verified 2026-10-15 on
the live processes (figures in the evidence file):

- A degraded process spends 99% of its CPU in the kernel (23s user, 2,468s
  system over 6.5h) and takes 60–150 major page faults a second. On a healthy
  day the cAdvisor fault rate is 0.
- Its cgroup sits at 98% of `memory.max`, and `memory.events` counts 104,203
  hits on the limit. The binary maps 14Mi and anonymous memory is ~9Mi, so the
  plugin's working memory does not fit in 20Mi. The kernel evicts the plugin's
  own code and reads it back from disk. Threads blocked in those faults would
  hold up the GC's stop-the-world, which is the pause the alert reads.
- Across all retained history (from 2026-09-15), the days usage sat at ≥95% of
  the limit are the days the plugin degraded, in both the September episode and
  this one. The one near-miss was 2026-10-01: a quarter of the day near the
  limit, no degradation.

How it fits the earlier findings, still to be confirmed by the result:

- **The GOMAXPROCS correlation was memory.** Each P costs resident memory. Pinned
  to 2, worker-0's RSS fell from 10–14.6Mi to 7.7Mi, which is consistent with
  the pin working by freeing headroom.
- **Why the degradation came back.** Dropping Fix C's 60s scrape and probe
  periods (`e34b589a`, live 2026-10-04 00:12 UTC) roughly tripled `/metrics`
  traffic and raised RSS from 7.7 to 9.1Mi, which used up the remaining
  headroom. Those processes held for 49h, just under the limit.
- **What tipped it over.** A storage stall at 01:14–01:20 UTC on 2026-10-06
  restarted all three at 01:21. Every process started since has degraded within
  minutes. Why the 10-04 processes held on and fresh ones do not is **not
  established**.

The change: memory request 10Mi → 32Mi, limit 20Mi → 64Mi. GOMAXPROCS stays at
2, and the probe and scrape keep their current defaults (10s and 30s), the
configuration that failed within two days. Changing nothing else keeps the
result attributable.

## What decides it

**Within an hour of the rollout, if the hypothesis holds:**

- The major fault rate falls to ~0:
  `rate(container_memory_failures_total{container="generic-device-plugin",failure_type="pgmajfault",scope="container"}[10m])`.
- The median GC pause returns to ~55µs on all three nodes.
- `memory.current` settles well below 64Mi. The prediction is 23–30Mi; record
  the real figure.

**Over two weeks:** `DevicePluginDegraded` does not fire. This is not proven by
two quiet weeks alone, because 2026-09-23 to 10-05 was also quiet and the fix
credited then turned out not to be the cause. What makes the result meaningful
is the control: the old limit with today's probe and scrape settings degraded
within two days, and that combination is still running, with only the limit
changed.

**The question that came up before, a stuck process gradually building up
memory until OOM.** If that is real, a larger limit only lets a hung plugin
live longer before it is reaped. Against it, as of 2026-10-15: over a
degraded process's 89h life, goroutines held at 23–25, threads at 8, RSS at
8.9–9.3Mi and fds at 10–12. That is the current failure only. The August
episode, with gathers piling up under the old CPU limit, predates retention and
cannot be re-checked. During the experiment, watch `go_goroutines`,
`container_memory_rss` and `memory.current` for a climb. Also note that the
liveness probe on `/metrics`, not the OOM killer, is what reaps a plugin whose
`/metrics` hangs, whatever the memory limit. Once the settle level is measured,
encode the build-up watch as an alert with a threshold taken from that figure,
so the answer arrives without anyone looking.

**Falsified if** the GC pause stays above 1ms with `memory.current` well below
64Mi and major faults near zero. In that case memory is not the mechanism, the
limit goes back down, and the cause is still open.

## When it closes

If confirmed:

- Rewrite "Keeping the plugin healthy" in `docs/automatic-ripping-machine.md`
  around the memory limit. GOMAXPROCS then becomes a memory measure rather than
  the cure.
- Correct the GOMAXPROCS comment in `kubernetes/infrastructure/devices.yaml`.
- Correct the `DevicePluginDegraded` description in
  `kubernetes/infrastructure/prometheus-rules.yaml`, which points at GOMAXPROCS
  first.
- Rewrite the upstream draft in `todos/generic-device-plugin-upstream.md`.
  Upstream's manifest ships the same 20Mi limit.
- Whether the pin is still needed with headroom is a separate experiment, for
  after this one.

## Wrong turns

- **cAdvisor's `container_memory_failcnt` and `container_oom_events_total` read
  0 throughout**, including across OOM kills the kubelet reported. They do not
  work on this cgroup v2 setup. Read `memory.events` on the node instead:
  `talosctl -n <ip> read /sys/fs/cgroup<path from /proc/<pid>/cgroup>/memory.events`.
- **The working set understates the pressure.** It subtracts inactive file
  pages, so it read 8–14Mi while the cgroup sat at the limit. The old manifest
  comment's "~12Mi, mostly page cache" is that misreading.
- **`memory.current` is not over `memory.max`.** 20,623,360 is 19.7MiB, 98% of
  20,971,520. It is easy to misread the bytes as MiB-over-limit.
- **"Completed, exit 0" restarts are liveness reaps**, not clean exits: the log
  ends `caught interrupt`, which is the kubelet's SIGTERM.
