# Knowing when a pinned version has stopped moving — context and prompt

Written 2026-08-08, at the end of the session that put chart versions under Flux
image automation and gave Prometheus an Alertmanager. Everything here was
verified against the live cluster that day. Intended as the starting context for
a follow-up session.

## The problem

Flux image automation keeps everything current *within its configured range* and
commits each bump to git. That works well. The gap is at the edge of the range:
when a version appears outside it, the `ImagePolicy` simply resolves to the
newest version that still fits and carries on. It reports Ready. Flux Bot keeps
committing patch bumps. Nothing anywhere says "there is a newer major and you
are no longer tracking it."

A policy that has quietly stopped advancing is indistinguishable from one that
is fully up to date.

This is the same shape as the failure that prompted the whole audit — kube-proxy
sat eight minor versions behind for three years because nothing compared what was
running against what existed.

### It is already happening

`apps/photoprism-mariadb` and `apps/salamander-mariadb` are both `^10.5.12`,
resolving to 10.11.18. MariaDB 11 and 12 exist. Those two have been capped at
10.x for some time with no signal. They are not hypothetical.

`infrastructure/kube-prometheus` is the most imminent: kube-prometheus-stack cuts
a major roughly monthly, and its range is `>=88.1.5 <89.0.0`.

## Scope: which policies are affected

34 `ImagePolicy` objects, categorised 2026-08-08.

**Capped — will silently stop at a boundary (23).** Note that caret ranges cap
exactly as hard as an explicit `<` bound, and on a `0.x` version caret pins the
*minor*, which is tighter still.

| Policy | Range | Caps | Resolved |
|---|---|---|---|
| apps/beets | `^2.0.0` | major | 2.13.1 |
| apps/borgmatic | `^1.0.0` | major | 1.9.14 |
| apps/headplane | `^0.6.1` | **minor** | 0.6.3 |
| apps/headscale | `^0.28.0` | **minor** | v0.28.0 |
| apps/hello-world | `^1.0.0` | major | v1.0.0 |
| apps/homepage | `^0.9.6` | **minor** | v0.9.13 |
| apps/mumble | `^1.0.0` | major | v1.6.870 |
| apps/photoprism-mariadb | `^10.5.12` | major | 10.11.18 |
| apps/pinepods-curl | `^8.0.0` | major | 8.21.0 |
| apps/pinepods-postgres | `^18.0.0` | major | 18.4 |
| apps/pinepods-valkey | `^8.0.0` | major | 8.1.9-alpine |
| apps/plex | `^1.0.0` | major | 1.43.3.10828 |
| apps/podgrab | `^1.0.0` | major | 1.0.0 |
| apps/rclone | `^1.0.0` | major | 1.75.0 |
| apps/salamander-mariadb | `^10.5.12` | major | 10.11.18 |
| apps/syncthing | `^2.0.0` | major | 2.1.3 |
| automatic-ripping-machine/automatic-ripping-machine | `^2.6.67` | major | 2.24.3 |
| infrastructure/cert-manager | `>=1.21.1 <2.0.0` | major | v1.21.1 |
| infrastructure/coredns | `>=1.47.0 <2.0.0` | major | 1.47.0 |
| infrastructure/ingress-nginx | `>=4.15.1 <5.0.0` | major | v4.15.1 |
| infrastructure/kube-prometheus | `>=88.1.5 <89.0.0` | major | 88.2.0 |
| infrastructure/loki | `>=6.30.1 <6.31.0` | **minor** | 6.30.1 |
| infrastructure/metallb | `>=0.16.1 <1.0.0` | major | 0.16.1 |

`infrastructure/loki` is deliberately clamped to the current minor pending its
6→7 upgrade; it should widen to `<8.0.0` once that is done.

**Uncapped — majors land unattended (7).** `apps/linkding`,
`apps/media-toolkit-webtop`, `apps/pinepods`, `apps/podbook-rebound`,
`apps/rustdesk`, `apps/speedtest-tracker`, `apps/stump`.

**Non-semver (4).** `apps/photoprism`, `apps/salamander` (both `numerical` on
date tags), `apps/trello-randomizer` (`alphabetical`), `apps/youtube-dl-server`
(`numerical` on commit-hash tags). These always take the newest available, so
they cannot go stale — but they have no major to reason about and are out of
scope for everything below.

## Why Prometheus cannot answer this today

Both plausible routes were checked directly.

**The data exists but is not a metric.** `ImageRepository.status.lastScanResult`
already holds everything needed — `infrastructure/kube-prometheus` had scanned
**1,102 tags** with `latestTags` populated. Flux knows a new major exists the
moment it is published. It lives in a custom resource status field, which
Prometheus cannot read.

**image-reflector-controller exposes nothing useful.** Querying Prometheus for
every metric name matching `image_`, `imagepolicy`, `imagerepo` or `gotk`
returns only `gotk_event_http_*` and `gotk_receiver_http_*` — HTTP server
plumbing. No tags, no policies, no repositories.

This is the same root cause `todos/backups.md` records for Flux object Ready
state: Flux removed `gotk_reconcile_condition`, and upstream's replacement is
kube-state-metrics' `customResourceState`, which this cluster does not configure.

## The approach

Configure kube-state-metrics `customResourceState` to expose Flux image
automation objects as metrics, then write rules on top. Verified available in
kube-prometheus-stack 88.x:

```yaml
kube-state-metrics:
  rbac:
    extraRules: []          # needs get/list/watch on the image.toolkit.fluxcd.io CRs
  customResourceState:
    enabled: false          # -> true
    config: {}              # -> the CRD state definitions
```

Both `customResourceState.{enabled,config}` and `rbac.extraRules` exist in the
subchart's values, so this is configurable from
`kubernetes/infrastructure/prometheus.yaml` with no chart forking.

### Two signals to derive

**1. Capped and stuck — the primary goal.** Compare the newest tag in
`ImageRepository.status.lastScanResult` against the tag in
`ImagePolicy.status.latestRef`. If the newest available major exceeds the
resolved major, the policy has stopped tracking. Covers the 23 capped policies.

**2. An unattended major landed — secondary, for consideration.** Alert when the
resolved tag's major changes. Covers the 7 uncapped policies, which the first
signal structurally cannot see: when the range permits majors there is no gap
between newest and resolved, so nothing is detectable.

On signal 2, the user's position is explicit: *"I'm pretty okay with a major
release landing and I'm not notified. If I've allowed such a wide semver range,
then I'm okay with it."* It is documented here for consideration rather than as
a requirement. If built, it belongs at `info` severity or as a Grafana
annotation alongside the existing Flux deploy markers, not as a Pushover alert.

### Why this over the alternatives

- **A CronJob per chart** querying registries directly: simpler for one chart,
  but bespoke, and needs repeating for all 34 policies plus anything added later.
- **Widening the ranges** so majors flow automatically: solves it by deciding
  majors are not a checkpoint. For kube-prometheus-stack that would mean
  unattended CRD upgrades, which is a bad idea — see the manual CRD step in its
  entry below.
- **Renovate or Dependabot**: ruled out early in the 2026-08-07 session. The
  requirement was in-cluster automation with git as the source of truth and no
  GitHub-side dependency.

kube-state-metrics wins because it derives both signals from data Flux already
collects, for **every** policy at once — container images and OCI charts alike,
since Flux treats them identically — and anything added later is covered without
further work.

### The bonus

The same `customResourceState` configuration closes the other gap in
`todos/backups.md`: no metric can currently express "a Flux object is not Ready",
for `ImagePolicy`, `Kustomization` or `HelmRelease`. That gap let a broken
`ImagePolicy` sit unnoticed for sixteen hours. Defining Ready-state gauges
alongside the version metrics is mostly incremental once the mechanism is in
place, and arguably the larger prize.

## The prompt

> I want alerting for when a Flux ImagePolicy has stopped tracking upstream
> because its range caps out. Read `todos/version-notification-prompt.md` first —
> it has the verified state as of 2026-08-08, the full policy inventory, and why
> Prometheus cannot answer this today.
>
> Start by planning, not editing. Work out:
>
> 1. The kube-state-metrics `customResourceState` definitions for
>    `ImageRepository` and `ImagePolicy`, and the `rbac.extraRules` they need.
>    Getting a version *string* into a metric is the awkward part — metrics are
>    numeric, so decide early whether to expose the version as a label and
>    compare with `label_replace`, or decompose it into numeric major/minor
>    gauges. The existing `KubernetesComponentVersionSkew` rule in
>    `kubernetes/infrastructure/prometheus-rules.yaml` uses the `label_replace`
>    approach against image tags and is worth reading first.
> 2. Whether `lastScanResult.latestTags` is ordered usefully. It is capped at ten
>    entries and appeared newest-first when inspected, but confirm rather than
>    assume — and note the tags are unfiltered, so a repository publishing
>    `latest`, `alpine3.24` or date stamps alongside semver will need the same
>    `filterTags` pattern the policy uses.
> 3. Whether to alert per policy or aggregate. 23 capped policies could mean 23
>    notifications; Alertmanager groups by `alertname` and `namespace`, so
>    consider what grouping produces one useful message rather than a wall.
> 4. Signal 2 (an unattended major landed) — build it or not, and if so at what
>    severity. See the note above; it is explicitly optional.
> 5. Whether to fold in Flux object Ready-state metrics at the same time, which
>    closes the gap in `todos/backups.md`. Probably yes, since the mechanism is
>    the same, but scope it deliberately.
>
> Verify any rule in both directions before committing it — silent when healthy
> *and* provably firing when not. A rule that never fires is the exact failure
> this whole effort exists to prevent, and it has already been hit several times
> in this repo: a `promtetheus` typo that disabled a ServiceMonitor for three
> years, a `compactor` block at the wrong indentation that meant Loki never
> deleted anything, and a kustomization entry that silently omitted a Secret.
>
> Do not apply cluster changes without checking with me first.

## Constraints and existing conventions

- Alertmanager routes to Pushover, with a healthchecks.io dead-man's switch on
  `Watchdog`. Config lives in `kubernetes/infrastructure/prometheus.yaml`;
  credentials are read from mounted files out of the `notification-secrets`
  Secret so routing stays readable in git.
- Severity conventions: `critical` raises Pushover priority to 1, everything else
  is 0, `info` routes to the null receiver and never notifies. Two inhibit rules
  suppress lower severities when a higher one fires for the same
  alertname/namespace.
- Custom rules go in `kubernetes/infrastructure/prometheus-rules.yaml`. The
  HelmRelease sets `ruleSelector: {}` with
  `ruleSelectorNilUsesHelmValues: false`, so no matching label is needed.
- Chart versions are managed by `OCIRepository` + `ImagePolicy` + a
  `$imagepolicy` marker, with Flux Bot committing bumps. See
  `kubernetes/infrastructure/cert-manager.yaml` for the established pattern.
- `kubeScheduler`, `kubeControllerManager` and `kubeProxy` scrapes are disabled:
  Talos binds their metrics to localhost. Anything depending on their
  self-reported metrics will be silently blind — build on kube-state-metrics
  instead, as `KubernetesComponentVersionSkew` does.
- The repo is public. See the Security Policy in `CLAUDE.md`.
- The user's SSH key is passphrase-protected — they run `git push` and `sops -d`.
