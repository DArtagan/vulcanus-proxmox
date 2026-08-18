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

**3. [etcd-disk-latency.md](etcd-disk-latency.md) — get etcd off spinning disks**
etcd's p99 WAL fsync is 0.41s against a target of 0.010s, because `rpool` is two
raidz2 vdevs of 7200rpm disks with no SLOG and the host holds no SSD at all. The
nightly Proxmox backup drives it past 8s, and apiserver p99 for mutating verbs
reaches 8.77s against a 1s SLO for the 22 minutes it runs. Six containers restart
in that window; `kube-scheduler` and `kube-controller-manager` had their
leader-election durations widened on 2026-08-18 and the other four — OpenEBS and
SMB CSI provisioning, kube-state-metrics — are still exposed on 15s leases. Third
because it is a live degradation of the component everything else depends on, and
because the first step — choosing an SSD with power-loss protection — is quick.
Blocked on that purchase, which is the only reason it is not higher. The alert is
silenced until 2026-09-17.

**4. [version-notification-prompt.md](version-notification-prompt.md) — notice when a version stops tracking**
23 of 34 ImagePolicies will silently stop advancing when a new major appears
outside their range, and two are already stuck. This is the work that makes the
other items visible rather than needing to be rediscovered by audit. It also
closes the separate gap where no metric can express a Flux object being unready.

**5. [talos-terraform-migration-prompt.md](talos-terraform-migration-prompt.md) — Talos versions under Terraform**
kube-proxy ran eight minor versions behind the control plane for roughly three
years, because Talos refreshes bootstrap manifests only via `upgrade-k8s`, which
the documented upgrade path here never ran. Fixed by hand on 2026-08-07; this is
about making it not recur. Newer provider versions also make the factory image
schematic declarative.

**6. [config-change-rollouts.md](config-change-rollouts.md) — make a ConfigMap change reach the running process**
Flux applies an updated ConfigMap without restarting the workload that reads it,
so the cluster can run configuration that no longer matches the repo with
nothing to indicate it — `flux get kustomizations` reports healthy, correctly,
because the desired state *was* applied. It caused two silent misbehaviours in
the beets stack on 2026-08-13 and eight Deployments are exposed. Here because
the failure mode is invisible rather than loud, which is the same reason the
alerting work sits where it does.

**7. [openebs-4x-migration-prompt.md](openebs-4x-migration-prompt.md) — OpenEBS 3.10 to 4.x**
The chart repository in use was abandoned in December 2023, so the unpinned
version silently meant "3.10.0 forever". 4.x is an architectural change touching
every PVC in the cluster. Last not because it matters least but because it
carries the most risk and needs a verified restore path first — which is item 1.

**8. [tailnet-multi-user.md](tailnet-multi-user.md) — family on the tailnet**
Every policy rule is `src: will@`, so a second Headscale user currently gets no
access at all — their devices would register and then reach nothing, which
presents as a broken tunnel rather than an intentional deny. Needs a tag scheme,
rules for them, and a less manual way to issue keys. Last in the ordering
because nothing is broken until someone is actually added, and it is the only
item here driven by a new want rather than an existing defect.

## Waiting on a trigger

Unranked because it cannot be scheduled — it needs a live failure to act on.

**[generic-device-plugin-hang.md](generic-device-plugin-hang.md) — capture a hang, report it upstream, then probe for it**
The plugin's `/metrics` endpoint wedges permanently on nodes with an optical
drive, and only an OOMKill recovers it hours later. The fix that stops the
recurring Pushover alerts is a liveness probe, but shipping it first would
restart a wedged pod within ~45s and make the defect impossible to capture — so
the probe deliberately waits until a goroutine dump has been taken from a live
wedge. The `TargetDown` alert is the cue to act. Device selection, the domain
move and the digest pin shipped and were verified on 2026-08-14; only the
capture, the upstream report and the probe remain.

## Applications

Separate track; these do not compete with the infrastructure ordering.

| Spec | What it is |
|---|---|
| [audiobook-importing.md](audiobook-importing.md) | Getting ~440 GiB of audiobooks out of the inbox and into MusicBrainz, and moving path routing off `genres` onto `albumtypes` |
| [book-import-spec.md](book-import-spec.md) | Design for a CLI tool to be the single entry point into the Stump library |
| [podcast-archive-context.md](podcast-archive-context.md) | Follow-up context from replacing Podgrab with Pinepods, including the feed snapshot job |

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
