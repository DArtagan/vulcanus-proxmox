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

**5. [promtail-to-alloy-prompt.md](promtail-to-alloy-prompt.md) — replace promtail**
promtail is deprecated upstream and this deployment runs a June 2023 build.
Deliberately low in the order: nothing is broken. The log-spam that once made it
look urgent was a Loki bug, fixed by the 6→7 upgrade.

**6. [openebs-4x-migration-prompt.md](openebs-4x-migration-prompt.md) — OpenEBS 3.10 to 4.x**
The chart repository in use was abandoned in December 2023, so the unpinned
version silently meant "3.10.0 forever". 4.x is an architectural change touching
every PVC in the cluster. Last not because it matters least but because it
carries the most risk and needs a verified restore path first — which is item 1.

## Applications

Separate track; these do not compete with the infrastructure ordering.

| Spec | What it is |
|---|---|
| [book-import-spec.md](book-import-spec.md) | Design for a CLI tool to be the single entry point into the Stump library |
| [podcast-archive-context.md](podcast-archive-context.md) | Follow-up context from replacing Podgrab with Pinepods, including the feed snapshot job |

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
- **Be honest about wrong turns.** `promtail-to-alloy-prompt.md` records that its
  original justification turned out to be mistaken. Inheriting bad reasoning is
  worse than inheriting no reasoning.
