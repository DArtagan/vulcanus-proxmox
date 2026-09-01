# Logging

Pod logs are collected by **Grafana Alloy** running as a DaemonSet, shipped over
**OTLP** to **VictoriaLogs**, and queried from Grafana.

Replaced promtail and Loki on 2026-08-10. See "Why this shape" for the reasoning.

## The pipeline

```
/var/log/pods/*/*/*.log
  → otelcol.receiver.filelog      (reads files, `container` operator parses CRI)
  → otelcol.processor.k8sattributes (workload identity from the Kubernetes API)
  → otelcol.processor.batch
  → otelcol.exporter.otlphttp     (OTLP/HTTP)
  → VictoriaLogs /insert/opentelemetry/v1/logs
```

Defined in `kubernetes/infrastructure/alloy.yaml` and
`kubernetes/infrastructure/victoria-logs.yaml`. The Grafana datasource and its
plugin live in the `grafana:` block of
`kubernetes/infrastructure/prometheus.yaml`, because Grafana is part of the
kube-prometheus-stack release rather than its own.

## Field model

Logs are OpenTelemetry semantic conventions, not Loki-style labels. A typical
entry:

| Field | Example |
|---|---|
| `_msg` | the log line, CRI prefix stripped |
| `_time` | parsed from the CRI timestamp |
| `_stream` | `{k8s.container.name, k8s.namespace.name, k8s.pod.name}` |
| `k8s.pod.uid`, `k8s.node.name`, `k8s.container.restart_count` | regular fields |
| `k8s.deployment.name` / `k8s.daemonset.name` / … | from the API, mutually exclusive |
| `log.file.path`, `log.file.name` | the source file |
| `log.iostream` | `stdout` or `stderr` |

### `VL-Stream-Fields` is load-bearing

VictoriaLogs promotes **every** OTLP resource attribute to a stream field by
default. Since `k8s.pod.uid` is among them, the default would mint a brand new
stream on every pod restart and grow cardinality without bound.

The exporter therefore pins the stream fields explicitly via a
`VL-Stream-Fields` header, and everything else stays an ordinary field —
queryable, but not part of the stream identity.

This fails slowly and silently if it is wrong. After any change to the pipeline,
check that the distinct stream count is on the order of *containers*, not
*containers × restarts*:

```bash
kubectl port-forward -n infrastructure svc/victoria-logs 9428:9428
curl -s localhost:9428/select/logsql/streams --data-urlencode 'query=*' --data-urlencode 'limit=5000'
```

## Two settings that fail silently

Both were found by validating rather than by the cluster complaining, and both
produce a collector that looks healthy while doing the wrong thing.

- **`alloy.stabilityLevel: public-preview`.** `otelcol.receiver.filelog` and
  `otelcol.storage.file` are below the chart's `generally-available` default.
  Without this Alloy refuses to load its config outright. The label describes
  Alloy's component wrapper, not the upstream OpenTelemetry filelogreceiver
  underneath it.
- **`include_file_path = true`** on the receiver. It defaults to `false`, and
  the `container` operator derives *all* of its Kubernetes metadata from
  `log.file.path`. Without it the log lines still parse but carry no `k8s.*`
  attributes at all, so everything lands in one anonymous stream.

Validate config changes before committing, against the same image the cluster
runs:

```bash
podman run --rm -v "$PWD":/cfg docker.io/grafana/alloy:v1.18.1 \
  validate --stability.level=public-preview /cfg/config.alloy
```

## Scheduling

The Alloy chart ships **no tolerations**, unlike the promtail chart it replaced.
Without an explicit control-plane toleration the DaemonSet runs on the two
workers only and silently stops collecting `kube-apiserver`,
`kube-controller-manager`, `kube-scheduler` and etcd logs — while reporting
fully ready. `controller.tolerations` in `alloy.yaml` covers this.

Sanity check after any rollout: **pod count must equal node count.**

Static control-plane pods live under a config-hash directory rather than a pod
UID. The `container` operator handles that path form natively; promtail needed a
dedicated second relabel rule for it.

## Checkpoints

`otelcol.storage.file` keeps filelog read offsets under `alloy.storagePath`,
which is backed by a hostPath at `/var/lib/alloy`. `/var` is writable on Talos
and survives reboots, unlike the tmpfs `/run` promtail kept its positions on.

The receiver's `start_at` defaults to `end`, so losing checkpoints causes a
**gap**, not a re-read of every file. That is the opposite of Loki's
`loki.source.file`, which restarts from the beginning by default.

## Retention and disk

`retentionPeriod: 90d`.

`retentionDiskSpaceUsage` is deliberately **unset**. It would delete data
silently once a threshold was crossed; the preference here is to be told the
disk is filling instead. That is safe because kube-prometheus-stack's
`NodeFilesystemAlmostOutOfSpace` already covers `/var/openebs` on both workers —
its selector excludes no fstype or mountpoint — and pages Pushover at <5% free.

**The PVC size is not a limit.** `openebs-hostpath` volumes are directories on
the node's disk and the requested size is never enforced; every such PVC reports
the whole 1 TB disk as its capacity in `kubelet_volume_stats_*`. Loki sat on a
"10Gi" claim while holding ~131 GiB. The `50Gi` on VictoriaLogs is intent and
documentation, not protection — the real signal is the node filesystem alert.

Measured at cutover: ~2 GiB/day of log content, ~850 lines/sec.

## Why this shape

- **OTel rather than Loki-native** because OTLP on the wire makes the store a
  swappable component. Moving off VictoriaLogs later is an endpoint change, not a
  collector rewrite. Alloy is itself an OpenTelemetry Collector distribution, so
  this uses `otelcol.*` components rather than the Grafana-specific `loki.*` ones.
- **Alloy rather than VictoriaMetrics' own collector** for the same reason —
  keeping the collector vendor-neutral is the point.
- **VictoriaLogs rather than Loki** because Loki was six pods (`loki-0`, gateway,
  two memcached caches, two canary) for a homelab with no queries against it, and
  VictoriaLogs is one binary. Loki was healthy and current; it was replaced for
  operational size, not because it was broken.
- **No tracing.** Nothing here emits spans, so Tempo would sit empty. SigNoz was
  considered and rejected for the same reason plus its ClickHouse footprint.
- **Label parity with promtail was explicitly not a goal.** There were no
  dashboards or queries in use — Grafana had no Loki datasource at all — so
  nothing depended on the old label names.
