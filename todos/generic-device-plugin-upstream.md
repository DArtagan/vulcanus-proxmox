# Report generic-device-plugin's CPU and GOMAXPROCS behaviour upstream

## Opening prompt

> Read `todos/generic-device-plugin-upstream.md`. It holds a finished draft of an
> issue for `squat/generic-device-plugin`, written 2026-10-03 when the
> `generic-device-plugin-hang` project closed. **Will files it himself** — check
> the draft against upstream's current `manifests/` and chart, update anything
> that has moved, and hand it over; do not submit it. Once it is filed, record
> the issue link in `docs/project_log.md` under this slug and delete this file.

## Where things stand

The local problem is fixed and documented — see "Keeping the plugin healthy" in
`docs/automatic-ripping-machine.md`. What remains is telling upstream, because
both halves of what was found are traps in upstream's own defaults rather than
in anything local:

- Upstream's manifest ships `limits.cpu: 50m`. Under it the plugin collapsed
  roughly daily and could not recover, because CFS throttling froze it for 97% of
  every period.
- Removing the limit stopped the collapse but let the runtime size itself to the
  node's core count, and on multi-core nodes the plugin then degraded every few
  hours until a liveness probe reaped it. Pinning `GOMAXPROCS` to 2 eliminated
  that: zero reaps in the ten days after, against ~11 a day on the 8-core node
  before.

Two things to be careful of, both learned the hard way during the project:

- **Do not overclaim the mechanism.** The garbage-collection pause and
  `/metrics` latency rose and fell together, ordered exactly by GOMAXPROCS, and
  pinning fixed both. Why a wider runtime degrades *intermittently*, and what
  makes a scrape wait on it, were never established. An earlier version of this
  draft asserted that the gather stops the world; this client_golang derives its
  `go_memstats_*` series from `runtime/metrics` rather than `ReadMemStats`, so
  that was not shown and is not claimed below.
- **The 2026-08-19 goroutine dumps are lost.** Evidence 1 quotes figures read off
  them at the time; it cannot be re-checked and is labelled as such. A capture
  from a degrading process on 2026-09-22 showed every goroutine parked and no
  gather in flight, which is why the draft does not lean on queueing as the
  cause.

## Draft

**Title:** Default 50m CPU limit makes `/metrics` degradation unrecoverable; without it, GOMAXPROCS follows the node and the plugin degrades on multi-core hosts

### Summary

Two related problems with the shipped resource settings, seen over six weeks on
a three-node cluster with the plugin scraped by Prometheus.

With the manifest's `limits.cpu: 50m`, `/metrics` periodically slowed past the
scrape timeout and never recovered: the process sat throttled for 97% of every
CFS period, stopped serving HTTP entirely, and the node's advertised device count
dropped to zero until the container was restarted.

With the CPU limit removed, that collapse stopped, but on the 4- and 8-core nodes
the plugin began degrading every few hours instead — the Go runtime had sized
itself to the node, and its GC pauses and `/metrics` latency both rose two to
three orders of magnitude. The 2-core node never degraded. Setting
`GOMAXPROCS=2` eliminated it.

### Environment

- `ghcr.io/squat/generic-device-plugin@sha256:dc192e164c69b03f156765793a1be62ca437709ae477b27ca7d8f3dcf5021576` (`latest` as of 2026-08-14), built with go1.26.2
- Kubernetes v1.35.0; Talos Linux v1.12.4; kernel 6.18.9-talos; amd64; containerd 2.1.6
- Started from the manifest's defaults: `requests: 50m/10Mi`, `limits: 50m/20Mi`
- Nodes with 2, 4 and 8 cores; Prometheus scraping `/metrics` with a 10s timeout

### Evidence

**1. Under the CPU limit, degradation is irreversible.** *(Goroutine figures are
quoted from a dump that no longer exists.)* `scrape_duration_seconds`, flat at
1.7ms for four hours, then:

```
22:35  0.0017
22:36  0.143
22:37  4.44
...    oscillating 0.5-10s for ~45 minutes
23:20  no successful scrape again until restart
```

The process pinned its limit — `rate(container_cpu_usage_seconds_total[5m])`
0.048–0.050 against 50m — and was throttled in 215,176 of 221,981 CFS periods,
against 102 of 15,894 on a healthy replica. A `SIGQUIT` dump from that state
held eight `Registry.Gather` calls still running, the oldest 308 minutes old:
`promhttp` is served with no handler or server timeout, so a scrape abandoned at
the client's timeout is never cancelled. Under throttling the backlog cannot
drain.

**2. Without the limit, degradation tracks GOMAXPROCS.** Same image, same config,
no CPU limit, read off each replica's `/metrics` within one minute:

| node cores | GOMAXPROCS | GC pause p50 | GC pause max | heap in use | scrape |
|---|---|---|---|---|---|
| 2 | 2 | 65µs | 17.6ms | 4.4MB | 1.5ms |
| 4 | 4 | 13.6ms | 99.8ms | 5.4MB | 1625ms |
| 8 | 8 | 11.4ms | 291ms | 4.4MB | 655ms |

Heap, goroutine count and memory working set were similar across all three.
Degradation was intermittent: replicas ran healthy for minutes to days, then
stepped up inside a single scrape interval and stayed there until restarted.
Request rate does not trigger it — 117 req/s for 45s against a healthy replica
left `/metrics` at 2.9ms.

**3. Pinning GOMAXPROCS removes it.** With `GOMAXPROCS=2` on every replica and
still no CPU limit, after ~1,950 GCs each:

| node cores | GC pause p50 | GC pause max | scrape p50 | liveness restarts, 10 days |
|---|---|---|---|---|
| 2 | 56µs | 0.5ms | 1.7ms | 0 |
| 4 | 56µs | 1.4ms | 1.7ms | 0 |
| 8 | 52µs | 0.9ms | 1.6ms | 0 (was ~11/day) |

### Suggested changes

1. **Set `GOMAXPROCS` in the shipped manifests and chart**, and drop the CPU limit
   in favour of a request. The limit is what makes a slow `/metrics`
   unrecoverable; GOMAXPROCS keeps the runtime narrow without throttling it.
2. **Bound the gather and the server** — `promhttp.HandlerOpts{Timeout: ...}`,
   plus `ReadHeaderTimeout`/`WriteTimeout` on an `http.Server` rather than bare
   `http.Serve` — so a scrape whose client has gone does not keep running.
3. **Ship a liveness probe on `/metrics`.** Not `/health`, which answered 200
   while `/metrics` was already unresponsive.

### Unrelated request

Would you consider semver-tagged images? Only git SHAs and `latest` are
published, so Flux `ImagePolicy` and similar ordering-based automation cannot
track releases. The Helm chart is versioned, but its DaemonSet template
hard-codes the `--device` arguments, so it cannot serve custom device groups.
