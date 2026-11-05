# Find why transcodes on worker-1 slow its Go processes more since 2026-11-01

## Opening prompt

> Read `todos/transcode-cpu-contention.md`. Since 2026-11-01, each long ARM
> transcode on worker-1 lifts generic-device-plugin's median GC pause from ~90µs
> to 275–521µs, and other Go processes there further. Transcodes before
> 11-01 at the same node load did not. Find what changed, starting from the
> leads below, and decide whether ARM's CPU needs bounding. Do not change the
> plugin's memory limit or GOMAXPROCS while doing so.

## Where things stand

Found 2026-11-05 while closing `generic-device-plugin-memory`, all figures from
Prometheus. Worker-1 has 4 cores; ARM requests 0.5 CPU, is limited to 3, and
HandBrake uses ~2.8 cores while it encodes (SVT-AV1 preset 4, see "Stage 2:
Transcode" in `docs/automatic-ripping-machine.md`).

**What happens.** During a transcode worker-1 runs ~76% busy with CPU pressure
("some") ~60%, and every Go process on the node waits for CPU. On 11-05,
07:00–14:00 UTC, the hourly maxima of median GC pause were: generic-device-plugin
521µs, metallb 2.2ms, alloy 7.7ms, ingress-nginx metrics 809µs, cainjector
394µs. All return to normal within two hours of the transcode ending. The
plugin's own memory is not involved: memory.current ~19Mi, no major faults,
0.4 millicores.

**What changed on 11-01.** The plugin's hours a day with its median pause above
200µs were 0 every day from 10-16 to 10-31, then 4.8, 9.3, 3.3, 3.4 and 4.8 from
11-01 to 11-05. The node's load did not change:

| transcode window | node CPU pressure | ARM cores | ARM threads | plugin CPU wait | plugin pause at end |
|---|---|---|---|---|---|
| 10-24 08:00–20:00 | 65% | 2.81 | 179 | ~0.9ms/s | 111µs |
| 11-01 02:00–10:00 | 57% | 2.53 | 176 | ~1.25ms/s | up to 413µs |
| 11-05 07:00–14:00 | 61% | 2.68 | 172 | ~1.25ms/s | up to 521µs |

The plugin's CPU wait (`container_pressure_cpu_waiting_seconds_total`) for the
same node load rose by ~40%, while its own CPU use held at 0.4ms/s. The pause
it reports is mostly that wait: a stop-the-world has to get its threads
scheduled. Its GC rate (~40/h), heap and GOGC are unchanged. Nothing in the repo
around 11-01 touches CPU. ARM was restarted at 2026-10-28 23:38 UTC, and its
transcodes on 10-30 and 10-31 were short (2–4h) and left the plugin under 125µs.

Short transcodes move the quantile less than long ones, since it summarises the
runtime's last 256 pauses, about six hours of GCs here. Compare windows of
similar length.

## Leads

- **What was being encoded.** A Blu-ray source through SVT-AV1 may schedule
  differently from a DVD at the same core count. ARM's job database records each
  job's disc and title. Compare the 10-24/10-25 jobs with the 11-01 onwards ones.
- **The Proxmox host.** Worker-1's steal is ~0.05%, so the VM is not being
  denied CPU outright, but host-side load (backups, other guests) can change
  wakeup latency without showing as steal. Check the host's load across the same
  windows.
- **Whether to bound ARM.** A CPU limit of 3 on a 4-core node leaves one core for
  everything else, including the metallb speaker that answers ARP for whichever
  LoadBalancer IPs worker-1 currently holds. Lowering the limit, or running HandBrake at a lower
  priority, would trade transcode time for the node's other tenants. Decide on
  the measured effect, not on principle.

## Why it matters

`DevicePluginDegraded` fires on a median pause above 1ms for an hour. The
plugin peaked at 521µs, half that, and the peak has risen most days since
11-01. If it crosses, the alert reports the plugin degrading when the node is
merely busy. metallb already crosses 1ms during these windows.

## Wrong turns

- **Load average, busy % and ARM's thread count do not separate the good days
  from the bad.** All three read the same on 10-24 as on 11-05. The difference
  only shows in per-container CPU wait.
- **`kubectl top` and the plugin's CPU use say nothing here.** The plugin uses
  0.4 millicores either way; what changes is how long it waits.
