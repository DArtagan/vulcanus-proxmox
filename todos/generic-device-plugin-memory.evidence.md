# generic-device-plugin-memory: evidence

## 2026-10-15: baseline before the change

Prometheus retention starts 2026-09-15. Day boundaries are UTC.

### Degradation against memory at the limit

Hours with median GC pause > 1ms, and the fraction of the day
`container_memory_usage_bytes` sat at ≥95% of the 20Mi limit (cp / w0 / w1):

| day | hours degraded | fraction ≥95% of limit |
|---|---|---|
| 09-16 | 15.3 / 13.7 / 12.4 | 0.79 / 0.71 / 0.70 |
| 09-21 | 0 / 0 / 4.3 | 0.01 / 0.02 / 0.19 |
| 09-22 | 0 / 12.8 / 17.9 | 0.17 / 0.67 / 0.95 |
| 09-23 – 09-30 | 0 | ≤0.07 |
| 10-01 | 0 | 0.26 / 0.18 / 0.23 |
| 10-02 – 10-05 | 0 | ≤0.08 |
| 10-06 | 22.6 / 22.3 / 18.6 | 0.95 / 0.95 / 0.94 |
| 10-07 – 10-14 | 12.5–24 each | 0.89–1.0 each |

### Before and after the onset (daily, per node)

| | 09-28 (pin + Fix C) | 10-05 (pin, Fix C dropped) | 10-12 (degraded) |
|---|---|---|---|
| inbound bytes/min | 1.3k / 3.3k / 1.3k | 4.1k / 6.2k / 4.2k | 4.3k / 6.4k / 4.4k |
| GCs/min | ~0.5 | ~0.7 | ~0.7 |
| process CPU, millicores | 0.2–0.3 | 0.4 | 105–126 |
| goroutines (max) | 21 | 21–22 | 24–25 |
| RSS MiB (max) | 7.6–7.9 | 9.0–9.2 | 9.1–9.3 |
| major faults/s | 0 | — | 64–96 (10-13) |

Every other Go process in the cluster held a median GC pause of 60–130µs
through the onset, which rules out a host-wide cause.

### Onset

- 2026-10-04 00:12 UTC: the `e34b589a` deploy recreates all three pods. They stay
  healthy for 49h.
- 2026-10-06 01:14–01:20: storage stall. Worker-0 I/O full-stall PSI 55%, and
  flux, cert-manager and Alertmanager targets go down.
- 01:21: all three plugin containers restart together. They were healthy
  (100µs pause, 1.8ms scrape) up to the gap.
- From 01:28 (cp), 01:36 (w0) and 05:19 (w1), median pause > 1ms. Since then,
  fresh processes mostly degrade within 2–30 minutes of starting.

### Live process, worker-0, pid 125271, started 10:18 UTC

From `/proc/125271/stat` at about 16:40 UTC: utime 2,315 ticks, stime 246,789
ticks, majflt 1,763,225. `/metrics` read `go_gc_duration_seconds_sum` 4.39s over
282 GCs, `go_sched_gomaxprocs_threads` 2, heap in use 4.9MB, 23 goroutines.

The cgroup:

| | worker-0 (degraded) | control plane (1.6h old, under threshold) |
|---|---|---|
| memory.current / memory.max | 20,623,360 / 20,971,520 | 20,717,568 / 20,971,520 |
| memory.events `max` | 104,203 | 5,782 |
| `pgmajfault` | 1,855,870 | 160,541 |
| active + inactive file | 11.3MB | 12.1MB |
| file_mapped | 2.2MB | 2.4MB |

The binary maps 14,721,024 bytes (sum of its VMAs in `/proc/<pid>/maps`).

### No build-up over a long life

Control plane, process started 10-09 11:01 and reaped 10-13 04:26 (89h, about
93% degraded), 6-hourly maxima: goroutines 23–25, threads 8, RSS 8.9–9.3MiB,
open fds 10–12. Heap in use 3.5–5.7MB throughout.

### Restart reasons

`lastState.terminated` on 2026-10-15: two OOMKilled (exit 137) and one
Completed (exit 0). The Completed one's log ends `caught interrupt; gracefully
cleaning up`, which is a liveness reap.

## 2026-10-23: one week after the change

The new pods (`cdt6h` w1, `pp7p8` w0, `r9bp2` cp) started at 16:39:31–39 UTC on
2026-10-15 with request 32Mi and limit 64Mi. At 00:21 UTC on 10-23 they had
0 restarts.

### Hour one

| | cp / w0 / w1 |
|---|---|
| max median GC pause, to 17:40 | 116 / 151 / 127µs |
| max pgmajfault/s, new pods | 0.005 / 0.022 / 0.005 |
| memory.current at 17:40, MiB | 12.1 / 16.6 / 10.5 |

The outgoing pods were still faulting at 62 / 16 / 13 per second in their last
ten minutes.

### The week

- `DevicePluginDegraded`: no pending or firing sample since 16:39:41 on 10-15,
  the scrape at which the old pods disappeared.
- Median GC pause: max 111 / 97 / 149µs, typical 94 / 81 / 91µs.
- pgmajfault: max rate 0.015 / 0.010 / 0.023/s, 21 / 11 / 17 faults in total.
- I/O full-stall PSI (5m rate) went over 25% on all three nodes during the week,
  and on worker-1 in most hours of most nights. The plugins showed nothing.

Daily maxima (cp / w0 / w1), from cAdvisor and `/metrics`:

| day to 00:00 UTC | memory.current MiB | RSS MiB | goroutines |
|---|---|---|---|
| 10-16 (from 16:40) | 20.4 / 18.8 / 11.9 | 8.9 / 8.8 / 8.9 | 21 / 21 / 21 |
| 10-17 | 20.7 / 21.3 / 20.8 | 9.1 / 8.9 / 9.1 | 22 / 22 / 21 |
| 10-18 | 22.9 / 21.5 / 21.0 | 9.2 / 9.1 / 9.1 | 21 / 21 / 22 |
| 10-19 | 20.8 / 21.3 / 21.0 | 9.2 / 8.9 / 9.1 | 22 / 21 / 21 |
| 10-20 | 21.1 / 21.4 / 21.2 | 9.1 / 9.0 / 9.2 | 22 / 22 / 22 |
| 10-21 | 21.0 / 22.9 / 21.1 | 9.4 / 9.0 / 9.1 | 22 / 21 / 22 |
| 10-22 | 21.2 / 23.2 / 21.3 | 9.2 / 9.0 / 9.0 | 21 / 22 / 24 |
| 10-23 (to 00:21) | 21.1 / 22.7 / 21.1 | 9.2 / 9.1 / 9.4 | 21 / 22 / 21 |

memory.current settles at 21–23MiB, which is just above the old 20MiB limit and
at the low end of the predicted 23–30Mi. After the first day it stays flat.

### Live process, worker-0, pid 63421, started 16:39 UTC on 10-15

Read on the node at about 00:25 UTC on 10-23, compared with pid 125271 on
10-15:

| | 10-15, 20Mi limit, 6.5h old | 10-23, 64Mi limit, 7.3 days old |
|---|---|---|
| memory.current | 20,623,360 | 22,048,768 |
| memory.events `max` | 104,203 | 0 |
| `pgmajfault` (cgroup) | 1,855,870 | 24 |
| file_mapped | 2.2MB | 8.6MB |
| anon | — | 8.0MB |
| utime / stime, ticks | 2,315 / 246,789 | 13,758 / 13,063 |

file_mapped has nearly quadrupled at the same memory.current. With room, the
binary's pages stay resident. stime is 5% of what the degraded process
accumulated, over 27 times the lifetime.
