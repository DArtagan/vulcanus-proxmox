# Documentation

How the system works as it stands today. Descriptive, not aspirational — if
something here is not true of the running cluster, it is a bug in the docs.

Work that has not been done yet lives in [`todos/`](../todos/), not here.

| Doc | Contents |
|---|---|
| [network.md](network.md) | IP inventory, DNS architecture, service exposure, router port forwarding |
| [kubernetes.md](kubernetes.md) | Workload conventions: probes, config rollouts, chart automation, safe teardown |
| [talos.md](talos.md) | Talos cluster operations and upgrades |
| [disk_management.md](disk_management.md) | Storage layout and resizing |
| [logging.md](logging.md) | Alloy → OTLP → VictoriaLogs pipeline, field model, retention |
| [automatic-ripping-machine.md](automatic-ripping-machine.md) | ARM setup and disc handling |
| [beets.md](beets.md) | Music/audiobook library, beets-flask, import model, library concurrency |

## Conventions the cluster follows

Recorded here because they are load-bearing across many files, and because
several were arrived at by getting them wrong first.

Conventions for working with the Kubernetes layer itself — probes, config
rollouts, chart version automation, tearing down stateful workloads — have moved
to [kubernetes.md](kubernetes.md). What follows is cluster-wide policy that is
not specific to it.

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
