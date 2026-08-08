# Documentation

## Outstanding work

Each of these is written to be self-contained: enough verified context to start
a session cold, plus a prompt to open with. Roughly in the order they are worth
doing.

| Doc | What it covers | Why it matters |
|---|---|---|
| [backups.md](backups.md) | Borgmatic repair, and what actually protects data today | The Kubernetes-side backup stack does not work. Borgmatic fails every run and now announces it via Pushover, so it is no longer deferrable. |
| [ingress-nginx-migration-prompt.md](ingress-nginx-migration-prompt.md) | Moving to a Gateway API implementation | ingress-nginx is **retired** — no security patches since March 2026 — and it is the internet-facing entry point. Its announced successor, InGate, is archived. |
| [version-notification-prompt.md](version-notification-prompt.md) | Detecting when a pinned version has stopped tracking upstream | 23 of 34 ImagePolicies will silently stop at a range boundary. Two are already stuck. Also closes the "no metric can express a Flux object being unready" gap. |
| [talos-terraform-migration-prompt.md](talos-terraform-migration-prompt.md) | Bringing Talos and Kubernetes versions under Terraform | kube-proxy sat eight minor versions behind for three years because bootstrap manifests are only refreshed by `upgrade-k8s`, which nothing ran. |
| [promtail-to-alloy-prompt.md](promtail-to-alloy-prompt.md) | Replacing promtail with Grafana Alloy | promtail is deprecated upstream and this one is a June 2023 build. Not urgent — nothing is broken. |
| [openebs-4x-migration-prompt.md](openebs-4x-migration-prompt.md) | OpenEBS 3.10 → 4.x | The chart repository in use was abandoned in December 2023. 4.x is an architectural change touching every PVC, so it needs a restore path first. |

## Reference

| Doc | Contents |
|---|---|
| [network.md](network.md) | IP inventory, DNS architecture, service exposure, router port forwarding |
| [talos.md](talos.md) | Talos cluster operations |
| [disk_management.md](disk_management.md) | Storage layout and resizing |
| [automatic-ripping-machine.md](automatic-ripping-machine.md) | ARM setup and disc handling |

## Working notes

Point-in-time context from specific pieces of work, kept because it explains
decisions that are otherwise hard to reconstruct.

| Doc | Contents |
|---|---|
| [podcast-archive-context.md](podcast-archive-context.md) | Replacing Podgrab with Pinepods, and the feed snapshot job |
| [book-import-spec.md](book-import-spec.md) | Book import handling |

## Conventions these assume

- **Chart versions are automated where the chart is published as an OCI
  artifact**: `OCIRepository` + `ImageRepository` + `ImagePolicy` with a
  `$imagepolicy` marker on `ref.tag`, so Flux Bot commits version bumps to git.
  See `kubernetes/infrastructure/cert-manager.yaml`. Charts without an OCI
  artifact — `csi-driver-smb`, `metrics-server`, and Alloy when it lands — stay
  pinned and are bumped by hand.
- **Ranges are major-pinned.** Minors and patches flow automatically; crossing a
  major is a deliberate edit. Note this is exactly what
  `version-notification-prompt.md` exists to make visible.
- **Every HelmRelease sets `install`/`upgrade` `remediation.retries: 3`**, so a
  failed chart rolls itself back rather than stalling half-applied.
- **Alerting reaches a person.** Alertmanager routes to Pushover, with a
  healthchecks.io dead-man's switch on `Watchdog`. Before 2026-08-07 Prometheus
  had no Alertmanager at all and twelve alerts were firing into nothing, which
  is the common cause behind most of the work above.
- **Verify rules in both directions.** A rule that never fires looks identical to
  a healthy one. This repo has produced that bug four times — a `promtetheus`
  typo, a `compactor` block at the wrong nesting level, a missing kustomization
  entry, and a `use_tcp` omission that would have dropped DNS over TCP.
