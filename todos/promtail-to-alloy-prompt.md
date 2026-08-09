# Replacing promtail with Grafana Alloy — context and prompt

Written 2026-08-08, at the end of the session that brought the Helm-based
workloads up to date. Verified against the live cluster that day. Intended as
the starting context for a follow-up session.

## Why

Promtail is deprecated upstream. The chart's own index entry carries
`deprecated: true`, its last release was 6.17.1 on 2025-10-31, and Grafana
directs users to Alloy as the replacement.

This deployment is further behind than that suggests. It runs chart **6.11.5**,
app **2.8.2** — a June 2023 build — as a 3-replica DaemonSet, shipping into Loki
**3.6.11**. Roughly three years of drift between collector and store.

### It is not urgent, and the reason matters

Earlier in that session, Loki was logging `negative structured metadata bytes
received` at roughly 350 occurrences per 2000 log lines. The working hypothesis
was that promtail 2.8.2 predates structured metadata entirely — it arrived in
Loki 3.0 — and that the Alloy migration would fix it.

**That was wrong.** After the Loki chart went 6.30.1 → 7.2.0 (app 3.5.0 →
3.6.11), the errors stopped completely: zero occurrences of that message, and
zero errors of any kind, in 1286 log lines over 19 minutes. Whatever the
accounting bug was, Loki fixed it on its side.

So this work is about not depending on a deprecated collector. There is no
active breakage driving it. Logs are flowing and queryable across the full 90
day retention window.

## Current state

- `kubernetes/infrastructure/loki.yaml` holds both the `loki` and `promtail`
  HelmReleases. The promtail release is the only thing to replace; Loki itself
  was upgraded and is current.
- promtail's values are a single line: `serviceMonitor.enabled: true`. Nothing
  else is customised, so there is very little configuration to port — the
  scrape config is entirely chart defaults.
- Loki is reachable in-cluster at `loki.infrastructure.svc.cluster.local:3100`,
  with a gateway at `loki-gateway` on port 80.

## Alloy

Chart **1.11.1**, app **v1.18.1**, released 2026-08-06, from the existing
`grafana` HelmRepository already declared in `loki.yaml`.

**It is not published as an OCI artifact.** Probing
`ghcr.io/grafana/helm-charts/alloy` and `ghcr.io/grafana/alloy/charts/alloy`
returns 403, while `loki` and `promtail` under the same prefix resolve fine. So
Alloy cannot join the `OCIRepository` + `$imagepolicy` automation the
OCI-backed charts use, and belongs in the manually-pinned tier alongside
`csi-driver-smb` and `metrics-server`.

`k8s-monitoring` **is** OCI-published (`ghcr.io/grafana/helm-charts/k8s-monitoring`,
chart 4.3.2), but it is a much larger opinionated bundle that would overlap
heavily with the kube-prometheus-stack already running. Probably the wrong shape
here; worth a look only if the manual pinning proves annoying.

## The prompt

> I want to replace promtail with Grafana Alloy in the vulcanus-proxmox repo.
> Read `todos/promtail-to-alloy-prompt.md` first — it has the verified state as
> of 2026-08-08 and explains why this is not urgent.
>
> Start by planning, not editing. Work out:
>
> 1. The Alloy configuration to match what promtail does today. promtail here is
>    pure chart defaults — discover pods, tail their logs, push to Loki — so the
>    target is equivalence, not feature parity with something elaborate. Alloy
>    uses its own configuration language rather than promtail's YAML, so this is
>    a rewrite rather than a translation. `discovery.kubernetes`,
>    `loki.source.kubernetes` and `loki.write` are the relevant components.
> 2. Whether to keep the existing label set. Changing labels changes how existing
>    log lines join to new ones in queries, and there are 90 days of retained
>    logs to stay compatible with. Confirm what promtail's defaults produce —
>    `namespace`, `pod`, `container`, `job`, `filename`, `app`, `stream` are
>    present on current entries — and match them.
> 3. Run both in parallel first, then remove promtail. Duplicate ingestion for a
>    short window is cheaper than a gap, and lets a query confirm Alloy's lines
>    look right before anything is deleted. Watch for the two collectors racing
>    on the same log files if that matters at this scale.
> 4. Keep `serviceMonitor.enabled: true` so Prometheus scrapes Alloy, matching
>    what promtail had.
> 5. Alloy is not OCI-published, so it gets a pinned `version:` on the existing
>    `grafana` HelmRepository rather than an `OCIRepository`. Give it
>    `install`/`upgrade` `remediation.retries: 3` like every other release here.
>
> Verify by querying Loki for lines Alloy produced and confirming the labels
> match what existing dashboards and queries expect. Do not apply cluster
> changes without checking with me first.

## An unrelated observation worth folding in

`loki.yaml` sets `deploymentMode: SingleBinary<->SimpleScalable`. That is
documented upstream as a **migration** mode, for moving between deployment
topologies — not as a resting state. Git history shows some back-and-forth
("Transitional deployment mode", "Back to single binary") that settled there.

It validates against the 7.2.0 schema and works, so this is not a bug. But
running permanently in a transitional mode is worth resolving to plain
`SingleBinary`, which is what the running topology actually is: one `loki-0`
StatefulSet, a gateway, and the two caches, with no read/write/backend
components. Worth doing while already editing this file.

## Constraints that carry over

- Every HelmRelease here sets `install`/`upgrade` `remediation.retries: 3`, so a
  failed chart rolls itself back rather than stalling.
- Loki, Prometheus and node-exporter are all **distroless** as of the 2026-08-08
  upgrades — no shell, so `kubectl exec … wget` and `df` do not work. Query
  Loki's HTTP API from a pod that still has a shell, or use
  `kubectl debug --image=busybox`.
- Alerting exists: Alertmanager routes to Pushover. A `KubeDaemonSetRolloutStuck`
  or similar will now actually reach someone, so a broken rollout is visible.
- The repo is public. See the Security Policy in `CLAUDE.md`.
- The user's SSH key is passphrase-protected — they run `git push` and `sops -d`.
