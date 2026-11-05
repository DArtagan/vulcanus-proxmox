# Report generic-device-plugin's resource defaults upstream

## Opening prompt

> Read `todos/generic-device-plugin-upstream.md`. It holds a finished draft of an
> issue for `squat/generic-device-plugin`, rewritten 2026-11-05 when the
> `generic-device-plugin-memory` project closed. **Will files it himself** — check
> the draft against upstream's current `manifests/` and chart, update anything
> that has moved, and hand it over; do not submit it. Once it is filed, record
> the issue link in `docs/project_log.md` under this slug and delete this file.

## Where things stand

The local problem is fixed and documented — see "Keeping the plugin healthy" in
`docs/automatic-ripping-machine.md`. What remains is telling upstream, because
what was found is a trap in upstream's own defaults rather than in anything
local:

- Upstream's manifest ships `limits.memory: 20Mi`, which is smaller than the
  plugin's own code plus its heap. Under it the kernel evicts the plugin's code
  and faults it back from disk, and the plugin degrades until a liveness probe
  reaps it. Raised to 64Mi, it ran three weeks clean in the configuration that
  had degraded within two days on 20Mi.
- Upstream's manifest also ships `limits.cpu: 50m`. Under it the plugin
  collapsed roughly daily and could not recover, because CFS throttling froze it
  for 97% of every period.

Things to be careful of, learned the hard way:

- **GOMAXPROCS is not the fix.** The `generic-device-plugin-hang` project
  credited pinning it to 2, and the degradation came back two weeks later with
  the pin in place. The pin lowers resident memory, which under 20Mi decided
  whether the code stayed resident. Whether it matters with headroom is
  untested, so the draft reports it as an observation and does not suggest it.
- **The memory mechanism is shown, the CPU-limit one inferred.** Fault rates,
  `memory.events` and system CPU were read off live processes under the memory
  limit. The CPU-limit collapse is from August, before Prometheus retention, and
  that it was the same thrashing under a throttle fits its figures but was not
  observed. The draft says so.
- **The 2026-08-19 goroutine dumps are lost.** Evidence 3 quotes figures read off
  them at the time; it cannot be re-checked and is labelled as such.
- **The working set misleads.** `container_memory_working_set_bytes` subtracts
  inactive page cache, so it read 8–14Mi on a cgroup at 98% of its limit. Quote
  `memory.current` (`container_memory_usage_bytes`) and `memory.events`.

## Draft

**Title:** Shipped 20Mi memory limit is smaller than the plugin's own code plus heap, so it thrashes and degrades; the 50m CPU limit makes that unrecoverable

### Summary

Seen over three months on a three-node cluster, with the plugin scraped by
Prometheus.

With the manifest's `limits.memory: 20Mi`, the plugin periodically degraded on
every node: its median GC pause rose from ~90µs to 1–18ms, `/metrics` slowed to
seconds, and a liveness probe reaped it every few hours. The binary maps ~14Mi,
which is charged to the container's cgroup as page cache, and the runtime adds
~9Mi of anonymous memory, so the cgroup sat at its limit and the kernel evicted
the plugin's own code and read it back from disk 60–150 times a second. Raising
the limit to 64Mi, with nothing else changed, ended it: three weeks with no
degradation and no limit hits.

With the manifest's `limits.cpu: 50m` as well, a degraded plugin never
recovered: it sat throttled for 97% of every CFS period, stopped serving HTTP,
and the node's advertised device count dropped to zero until the container was
restarted.

### Environment

- `ghcr.io/squat/generic-device-plugin@sha256:dc192e164c69b03f156765793a1be62ca437709ae477b27ca7d8f3dcf5021576` (`latest` as of 2026-08-14), built with go1.26.2
- Kubernetes v1.35.0; Talos Linux v1.12.4; kernel 6.18.9-talos; cgroup v2; amd64; containerd 2.1.6
- Started from the manifest's defaults: `requests: 50m/10Mi`, `limits: 50m/20Mi`
- Nodes with 2, 4 and 8 cores; Prometheus scraping `/metrics` every 30s with a
  10s timeout; a liveness probe on `/metrics` every 10s

### Evidence

**1. Under 20Mi, the plugin thrashes its own code.** A degraded process on the
8-core node, 6.5 hours old, CPU limit removed:

| | |
|---|---|
| `memory.current` / `memory.max` | 20,623,360 / 20,971,520 |
| `memory.events` `max` | 104,203 |
| major faults (cgroup) | 1,855,870 |
| utime / stime | 23s / 2,468s |
| heap in use, goroutines | 4.9MB, 23 |

The binary's mappings in `/proc/<pid>/maps` sum to 14,721,024 bytes. Over a
month, the days `memory.current` sat at ≥95% of the limit were the days the
plugin degraded, on every node.

**2. At 64Mi, it does not.** Same image, same arguments, same probe and scrape,
with only the memory request and limit raised to 32Mi/64Mi. Over the 21 days
since, on all three nodes:

| | |
|---|---|
| `memory.current` | 18–23Mi, flat after the first day |
| `memory.events` `max` | 0 |
| major faults, total | 27–37 per process |
| median GC pause | 80–100µs typical |
| restarts | 0 |

RSS held at 8.9–9.6Mi, goroutines at 21–22 and open fds at 10 throughout, so
nothing builds up given room. The same configuration on 20Mi had degraded on
all three nodes within two days of being deployed.

**3. Under the CPU limit, degradation is irreversible.** *(From August, before
the memory limit was identified. Goroutine figures are quoted from a dump that
no longer exists.)* `scrape_duration_seconds`, flat at 1.7ms for four hours,
then:

```
22:35  0.0017
22:36  0.143
22:37  4.44
...    oscillating 0.5-10s for ~45 minutes
23:20  no successful scrape again until restart
```

The process pinned its limit — `rate(container_cpu_usage_seconds_total[5m])`
0.048–0.050 against 50m — and was throttled in 215,176 of 221,981 CFS periods,
against 102 of 15,894 on a healthy replica. A thrashing process uses 105–126
millicores, nearly all of it in the kernel, so the same thrashing under a 50m
throttle would produce these figures, though it was not observed directly. A
`SIGQUIT` dump from that state held eight `Registry.Gather` calls still
running, the oldest 308 minutes old: `promhttp` is served with no handler or
server timeout, so a scrape abandoned at the client's timeout is never
cancelled. Under throttling the backlog cannot drain.

**4. GOMAXPROCS moves resident memory.** With no CPU limit the runtime sizes
itself to the node. Setting `GOMAXPROCS=2` lowered RSS on the 8-core node from
10–14.6Mi to 7.7Mi, and the degradation stopped for twelve days. It returned
with the pin still in place, after `/metrics` traffic tripled and RSS rose to
9.1Mi, and the 2-core node degraded too, at two without any pin. We read the
pin as headroom rather than a fix, and have not tested whether it matters at
64Mi.

### Suggested changes

1. **Raise the memory limit in the shipped manifests and chart.** 32Mi/64Mi
   works here; `memory.current` settles at 20–23Mi, which leaves the current
   20Mi no room at all.
2. **Drop the CPU limit in favour of a request.** The limit is what makes a
   degraded plugin unrecoverable.
3. **Bound the gather and the server** — `promhttp.HandlerOpts{Timeout: ...}`,
   plus `ReadHeaderTimeout`/`WriteTimeout` on an `http.Server` rather than bare
   `http.Serve` — so a scrape whose client has gone does not keep running.
4. **Ship a liveness probe on `/metrics`.** Not `/health`, which answered 200
   while `/metrics` was already unresponsive.

### Unrelated request

Would you consider semver-tagged images? Only git SHAs and `latest` are
published, so Flux `ImagePolicy` and similar ordering-based automation cannot
track releases. The Helm chart is versioned, but its DaemonSet template
hard-codes the `--device` arguments, so it cannot serve custom device groups.
