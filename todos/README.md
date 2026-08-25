# Outstanding work

Transient work specs. Each is self-contained: enough verified context to start a
session cold, plus a prompt to open with.

These are **not** documentation of the running system — see [`docs/`](../docs/)
for that. When a piece of work lands, whatever it leaves behind that is
permanently true gets written into `docs/`, and the spec here is deleted. A file
sitting in this directory means the work has not been done.

## Infrastructure, in the order worth tackling

**1. [backups.md](backups.md) — repair the backup stack**
The Kubernetes-side backups do not work. Borgmatic fails on every run and no
`openebs-hostpath` volume is covered by anything at cluster level. What actually
protects data today is ZFS replication offsite plus a whole-VM Proxmox backup.
First because it is the only item where the failure mode is losing data, and
because borgmatic now reports its own failures via Pushover, so it announces
itself until dealt with.

**2. [ingress-nginx-migration-prompt.md](ingress-nginx-migration-prompt.md) — move to Gateway API**
ingress-nginx is retired upstream: no releases, no bugfixes and **no security
patches** since March 2026. It is the internet-facing entry point, and it had an
unauthenticated RCE as recently as CVE-2025-1974. Its announced successor,
InGate, is archived. Traefik is ruled out on prior experience; the doc records
why so it does not get proposed again.

**3. [version-notification-prompt.md](version-notification-prompt.md) — notice when a version stops tracking**
23 of 34 ImagePolicies will silently stop advancing when a new major appears
outside their range, and two are already stuck. This is the work that makes the
other items visible rather than needing to be rediscovered by audit. It also
closes the separate gap where no metric can express a Flux object being unready.

**4. [talos-terraform-migration-prompt.md](talos-terraform-migration-prompt.md) — Talos versions under Terraform**
kube-proxy ran eight minor versions behind the control plane for roughly three
years, because Talos refreshes bootstrap manifests only via `upgrade-k8s`, which
the documented upgrade path here never ran. Fixed by hand on 2026-08-07; this is
about making it not recur. Newer provider versions also make the factory image
schematic declarative.

**5. [config-change-rollouts.md](config-change-rollouts.md) — make a ConfigMap change reach the running process**
Flux applies an updated ConfigMap without restarting the workload that reads it,
so the cluster can run configuration that no longer matches the repo with
nothing to indicate it — `flux get kustomizations` reports healthy, correctly,
because the desired state *was* applied. It caused two silent misbehaviours in
the beets stack on 2026-08-13 and eight Deployments are exposed. Here because
the failure mode is invisible rather than loud, which is the same reason the
alerting work sits where it does.

**6. [openebs-4x-migration-prompt.md](openebs-4x-migration-prompt.md) — OpenEBS 3.10 to 4.x**
The chart repository in use was abandoned in December 2023, so the unpinned
version silently meant "3.10.0 forever". 4.x is an architectural change touching
every PVC in the cluster. Last not because it matters least but because it
carries the most risk and needs a verified restore path first — which is item 1.

**7. [tailnet-multi-user.md](tailnet-multi-user.md) — family on the tailnet**
Every policy rule is `src: will@`, so a second Headscale user currently gets no
access at all — their devices would register and then reach nothing, which
presents as a broken tunnel rather than an intentional deny. Needs a tag scheme,
rules for them, and a less manual way to issue keys. Last in the ordering
because nothing is broken until someone is actually added, and it is the only
item here driven by a new want rather than an existing defect.

## Waiting on a decision

**[generic-device-plugin-hang.md](generic-device-plugin-hang.md) — file the upstream report, then pick a fix**
The plugin pods stop serving HTTP entirely, pin their CPU at the 50m limit with
97% CFS throttling, and recover only on restart. Root cause is abandoned
Prometheus gathers: the 10s scrape timeout does not cancel the gather, so they
queue on goCollector's mutex forever — eight of them, the oldest five hours old,
were still running in the dump. Two goroutine dumps were captured on 2026-08-19,
so the capture step that gated everything is done and the liveness probe is no
longer held back. Outstanding: hand the bug report over for filing, and choose
between removing the CPU limit and adding the probe. The spec records several
conclusions from 2026-08-14 that the dumps disproved.
**[etcd-disk-latency.md](etcd-disk-latency.md) — get etcd off spinning disks**
etcd's p99 WAL fsync is 0.25s at rest against a target of 0.010s, because `rpool`
is two raidz2 vdevs of spinning disks with no SLOG and the host holds no SSD at
all. The nightly Proxmox backup drives it past 8s, and apiserver p99 for mutating
verbs reaches 8.77s against a 1s SLO for as long as the backup runs — 22 minutes
usually, 2h22m on 2026-08-19, because a guest restart discards QEMU's dirty
bitmap and forces a full 1.1TiB read. Six containers restart in that window;
widening leader-election on `kube-scheduler` and `kube-controller-manager`
reduced their restarts but did not stop them, and the other four — OpenEBS and
SMB CSI provisioning, kube-state-metrics — are still exposed on 15s leases. Third
because it is a live degradation of the component everything else depends on.
The device is chosen and the procedure written, and the whole thing is **parked
on the NAND shortage**: a Kingston DC2000B is ~$310 against a normal sub-$100.
Nothing to do here but re-check the price. The alert is silenced until
2026-09-17.

**[vzdump-job-in-terraform.md](vzdump-job-in-terraform.md) — the backup job into IaC**
The nightly Proxmox backup exists only in `/etc/pve/jobs.cfg`, including two
settings applied by hand on 2026-08-23 that decide how hard it hits `rpool`. It
is also the only thing protecting the OpenEBS PVC data, which makes item 1 above
its neighbour. `telmate/proxmox` has no backup-job resource at all;
`bpg/proxmox` has one but no *released* version implements `exclude`, and the
alternative it does offer inverts the safety property so a new guest would be
silently unbacked-up. Everything else about the approach was proven to work.
Waiting on a bpg release, and nothing to do until one appears.


## Applications

Separate track; these do not compete with the infrastructure ordering.

| Spec | What it is |
|---|---|
| [audiobook-importing.md](audiobook-importing.md) | Getting ~440 GiB of audiobooks out of the inbox and into MusicBrainz, and moving path routing off `genres` onto `albumtypes` |
| [audiobook-cover-art.md](audiobook-cover-art.md) | A unified system for sourcing, filing and refreshing cover art. `artpath` is empty on every audiobook while 95% of the inbox carries art `fetchart` cannot see |
| [book-import-spec.md](book-import-spec.md) | Design for a CLI tool to be the single entry point into the Stump library |
| [podcast-archive-context.md](podcast-archive-context.md) | Follow-up context from replacing Podgrab with Pinepods, including the feed snapshot job |
| [disc-ripping.md](disc-ripping.md) | Getting ARM to rip audio CD, DVD, Blu-ray and 4K reliably into the `import/` folders. No job has ever produced a transcoded file; a one-year manual-identification wait wedges the drive on every disc |

The nine beets-flask v2.0.0-rc5 bugs that used to sit here moved to
`~/repositories/beets-flask/todos/` on 2026-08-14, decomposed one spec per bug.
That fork is set up to contribute back to `pSpitzner/beets-flask`, so the work of
reporting and fixing them belongs next to the code.

## Writing a spec

What makes these useful when opened cold, months later:

- **State what was verified and when.** "Verified 2026-08-07" beats an assertion
  with no provenance. Anything not checked should say so.
- **Record why, not just what.** A future session that knows Traefik was rejected
  on experience will not re-propose it.
- **Include the prompt.** Ending with the literal text to open a session with
  removes the work of reconstructing intent.
- **Note decisions already made, and by whom.** Where the user has expressed a
  preference — accepting unattended major upgrades, say — record it verbatim so
  it is not relitigated.
- **Be honest about wrong turns.** Inheriting bad reasoning is worse than
  inheriting no reasoning. The promtail-to-alloy spec was the worked example
  until it was retired on 2026-08-10: it recorded that its own original
  justification — log-spam blamed on promtail — had turned out to be a Loki bug,
  and the session that acted on it went further still, discarding the spec's
  central assumption that promtail's label set had to be preserved. Neither
  correction would have been possible if the spec had only stated conclusions.
