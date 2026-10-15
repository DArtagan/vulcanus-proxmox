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
