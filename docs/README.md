# Documentation

How the system works as it stands today. Descriptive, not aspirational — if
something here is not true of the running cluster, it is a bug in the docs.

Work that has not been done yet lives in [`todos/`](../todos/), not here.

| Doc | Contents |
|---|---|
| [network.md](network.md) | IP inventory, DNS architecture, service exposure, router port forwarding |
| [talos.md](talos.md) | Talos cluster operations and upgrades |
| [disk_management.md](disk_management.md) | Storage layout and resizing |
| [logging.md](logging.md) | Alloy → OTLP → VictoriaLogs pipeline, field model, retention |
| [automatic-ripping-machine.md](automatic-ripping-machine.md) | ARM setup and disc handling |
| [beets.md](beets.md) | Music/audiobook library, beets-flask, import model, library concurrency |

## Conventions the cluster follows

Recorded here because they are load-bearing across many files, and because
several were arrived at by getting them wrong first.

- **Chart versions are automated where the chart is published as an OCI
  artifact**: `OCIRepository` + `ImageRepository` + `ImagePolicy`, with a
  `$imagepolicy` marker on `ref.tag`, so Flux Bot commits version bumps to git
  and git keeps describing what is deployed. See
  `kubernetes/infrastructure/cert-manager.yaml` for the pattern. Charts with no
  OCI artifact — `csi-driver-smb`, `metrics-server`, `alloy` — stay pinned and are bumped
  by hand.
- **Ranges are major-pinned.** Minors and patches flow automatically; crossing a
  major is a deliberate edit. The consequence is that a chart silently stops
  advancing at the boundary, which is what
  [`todos/version-notification-prompt.md`](../todos/version-notification-prompt.md)
  exists to make visible.
- **Every HelmRelease sets `install`/`upgrade` `remediation.retries: 3`**, so a
  failed chart rolls itself back instead of stalling half-applied. This was
  added after two upgrades failed in the same window and behaved completely
  differently depending on whether they had it.
- **Alerting reaches a person.** Alertmanager routes to Pushover, with a
  healthchecks.io dead-man's switch on `Watchdog` so a dead monitoring stack is
  itself noticed. Before 2026-08-07 there was no Alertmanager at all and twelve
  alerts were firing into nothing.
- **Secrets are SOPS-encrypted**, including any value that would help an
  attacker. Credentials that Alertmanager needs are referenced by mounted file
  path rather than inlined, so routing config stays readable in git while the
  credentials do not appear in it.
- **Verify alert rules in both directions.** A rule that never fires looks
  exactly like a healthy one. Prove it silent when healthy *and* firing when
  not. This repo has produced that class of bug four times: a `promtetheus`
  typo that disabled a ServiceMonitor for three years, a `compactor` block at
  the wrong nesting level that meant Loki never deleted anything, a missing
  kustomization entry that silently omitted a Secret, and a `use_tcp` omission
  that would have dropped DNS over TCP.
