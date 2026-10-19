# Outstanding work

Transient work specs. Each is self-contained: enough verified context to start a
session cold, plus a prompt to open with.

These are **not** documentation of the running system — see [`docs/`](../docs/)
for that. When a piece of work lands, whatever it leaves behind that is
permanently true gets written into `docs/`, and the spec here is deleted. A file
sitting in this directory means the work has not been done.

## Infrastructure, in the order worth tackling

**1. [backups.md](backups.md) — build a 3-2-1-0 backup architecture**
Replication, retention and scrubs work again, and every Kubernetes volume and
database is backed up to an append-only restic repository with a verified
offsite replica. What remains is taking the OpenEBS disks out of vzdump, PBS
offsite, the offline disk and the restore runbook. Until
PBS has its offsite twin, losing `rpool` loses every guest image together with
its backups. First because it is the only item where the failure mode is losing
data.

Unlike the other specs here this one is **maintained through implementation** — it
spans sessions and carries a status table. Slug `backups`, branch `backups`.

**2. [talosconfig-renewal.md](talosconfig-renewal.md) — keep `talosctl`'s certificate from lapsing unnoticed**
The client certificate in `.talosconfig` expires 2026-11-07, and the provider
renews it only on an apply from 2026-10-07, with nothing to announce it. A lapse
costs one apply; it is second for its date, and because `talosctl` is the tool
needed when the cluster is what is broken. Also carries the one command never to
run: `-replace` on the machine secrets.

**3. [mariadb-datadir-rebuild.md](mariadb-datadir-rebuild.md) — rebuild two downgraded MariaDB data directories**
PhotoPrism's and salamander's MariaDB run 10.11 on data directories that 11.0 and
12.2 last upgraded, after salamander's image went untagged for years and was then
pinned back. MariaDB does not support downgrades, and both log a change-buffer error
at every start. The data reads correctly today, and each dump has been loaded into a
fresh 10.11, so the fix is proven; which version to settle on is the open decision.

**4. [cert-manager-dns01.md](cert-manager-dns01.md) — stop renewing certificates through the public IP**
All 24 certificates validate over HTTP-01, so every renewal needs the public A
record, port 80 and `ingress-nginx-external`. Fourteen internal-only hostnames
are included, because the solver names the external ingress class and is used
whatever class the certificate's own ingress has. Above the ingress migration
because that migration has to rework this solver anyway; doing it first deletes
the question rather than answering it mid-migration.

**5. [ingress-nginx-migration-prompt.md](ingress-nginx-migration-prompt.md) — move to Gateway API**
ingress-nginx is retired upstream: no releases, no bugfixes and **no security
patches** since March 2026. It is the internet-facing entry point, and it had an
unauthenticated RCE as recently as CVE-2025-1974. Its announced successor,
InGate, is archived. Traefik is ruled out on prior experience; the doc records
why so it does not get proposed again.

**6. [version-notification-prompt.md](version-notification-prompt.md) — notice when a version stops tracking**
22 of 33 ImagePolicies will silently stop advancing when a new major appears
outside their range, and two are already stuck. This is the work that makes the
other items visible rather than needing to be rediscovered by audit. It also
closes the separate gap where no metric can express a Flux object being unready.

**7. [talos-terraform-migration-prompt.md](talos-terraform-migration-prompt.md) — Talos versions under Terraform**
kube-proxy ran eight minor versions behind the control plane for roughly three
years, because Talos refreshes bootstrap manifests only via `upgrade-k8s`, which
the documented upgrade path here never ran. Fixed by hand on 2026-08-07; this is
about making it not recur. Newer provider versions also make the factory image
schematic declarative.

**8. [config-change-rollouts.md](config-change-rollouts.md) — make a ConfigMap change reach the running process**
Flux applies an updated ConfigMap without restarting the workload that reads it,
so the cluster can run configuration that no longer matches the repo with
nothing to indicate it — `flux get kustomizations` reports healthy, correctly,
because the desired state *was* applied. It caused two silent misbehaviours in
the beets stack on 2026-08-13 and seven Deployments are exposed. Here because
the failure mode is invisible rather than loud, which is the same reason the
alerting work sits where it does.

**9. [openebs-4x-migration-prompt.md](openebs-4x-migration-prompt.md) — OpenEBS 3.10 to 4.x**
The chart repository in use was abandoned in December 2023, so the unpinned
version silently meant "3.10.0 forever". 4.x is an architectural change touching
every PVC in the cluster. Last not because it matters least but because it
carries the most risk and needs a verified restore path first — which is item 1.

**10. [tailnet-multi-user.md](tailnet-multi-user.md) — family on the tailnet**
Every policy rule is `src: will@`, so a second Headscale user currently gets no
access at all — their devices would register and then reach nothing, which
presents as a broken tunnel rather than an intentional deny. Needs a tag scheme,
rules for them, and a less manual way to issue keys. Low in the ordering
because nothing is broken until someone is actually added — it and the item
below are the two here driven by a new want rather than an existing defect.

**11. [pod-security-namespace-level.md](pod-security-namespace-level.md) — put pod security at the namespace**
`apps` carries no Pod Security Admission labels at all, so workloads that want
the restricted profile restate it individually and the rest are simply
unexamined. Last because nothing is broken today: the
cluster-level fallback admits these pods, and the work is auditing what each
workload actually needs rather than setting a label.

**12. [nfs-cluster-volumes.md](nfs-cluster-volumes.md) — cluster media volumes over NFS**
Each node's SMB traffic funnels through one single-threaded smbd, and NFS
from the host measured 3–15x faster for directory walks and small-file opens.
This is a speed-up, not a defect. It waits on
[smb-charset-utf8.md](smb-charset-utf8.md), because NFS shows the Latin-1 filenames
Samba translates.

## Waiting on evidence

**[generic-device-plugin-memory.md](generic-device-plugin-memory.md) — is the plugin's degradation its memory limit?**
The plugin degraded 18–24h a day from 2026-10-06 with GOMAXPROCS still pinned.
A degraded process sits at its 20Mi limit, thrashing its own code through
page cache. The limit is raised to 64Mi with nothing else changed, and the spec
says which readouts decide the question. Runs two weeks.

## Waiting on a decision

**[generic-device-plugin-upstream.md](generic-device-plugin-upstream.md) — file the issue upstream**
A draft for `squat/generic-device-plugin` about its shipped CPU limit and
`GOMAXPROCS`. Blocked on generic-device-plugin-memory above, whose result may
move the mechanism from GOMAXPROCS to the memory limit. After that, waiting on
Will to file it, since he does the filing himself.

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

## Waiting on a site visit

**[vulcanus-onsite-checks.md](vulcanus-onsite-checks.md) — cables, power and ports, in person**
The optical drive hangs on one command badly enough to drop off its SATA link,
and a disc-free reproducer now exists to measure it. Its link-down events show
8b/10b decode errors, and `sda`, one port over on the same controller, logged
link-layer failures from June to September. The checks run in order, with the
reproducer after each, and stop at the first change that helps. Since
2026-10-09 the evidence points at the drive's LibreDrive handling rather than
the cable, and rips have been clean since a fix that changed the LibreDrive
definitions in use. So the visit may not be needed. Check the spec's status
note first.


## Applications

Separate track; these do not compete with the infrastructure ordering.

| Spec | What it is |
|---|---|
| [audiobook-importing.md](audiobook-importing.md) | Getting ~440 GiB of audiobooks out of the inbox and into MusicBrainz, and moving path routing off `genres` onto `albumtypes` |
| [audiobook-cover-art.md](audiobook-cover-art.md) | A unified system for sourcing, filing and refreshing cover art. `artpath` is empty on every audiobook while 95% of the inbox carries art `fetchart` cannot see |
| [book-import-spec.md](book-import-spec.md) | Design for a CLI tool to be the single entry point into the Stump library |
| [podcast-archive-context.md](podcast-archive-context.md) | Follow-up context from replacing Podgrab with Pinepods, including the feed snapshot job |
| [smb-charset-utf8.md](smb-charset-utf8.md) | Samba runs `unix charset = ISO-8859-1`, so any filename above U+00FF fails with EIO on every share. Blocks a TV rip today; latent for any title with a curly apostrophe |
| [video-library-ingest.md](video-library-ingest.md) | Ripped films stop in `import/` and never reach the library. The fix is [reelbarrow](https://github.com/DArtagan/reelbarrow), which holds the design and the application work; this spec is the deployment half: ARM's manifest hook, `kubernetes/apps/reelbarrow/`, secrets and alerts |
| [acoustid-identification.md](acoustid-identification.md) | Identifying discs MusicBrainz has no disc ID for, by fingerprinting the audio rather than the table of contents |
| [disc-ripping.md](disc-ripping.md) | Getting ARM to rip audio CD, DVD, Blu-ray and 4K reliably into the `import/` folders. CD, DVD and Blu-ray rip end to end; 4K is unproven, and the drive intermittently drops off its SATA link |

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
- **Every line is read as context.** A session opens a spec by reading all of
  it and carries it to the end, so a line that neither helps the next session
  act nor stops it repeating a mistake costs every session after. What belongs:
  where things stand and what is next, the work still to do, the decisions that
  bind it, the wrong turns it could repeat, and the prompt.
- **Collapse finished work to what still binds.** When a phase or an item
  closes, its working comes out. What stays is what later work depends on, where
  the result is now written down, and the pull request. The commits carry the
  rest, and git keeps the old text.
- **Point at `docs/` rather than restate it.** Once work has written its
  permanent facts into `docs/`, the spec names the doc and the section. Two
  copies drift, and the spec's is the one that rots.
- **Correct in place.** When a finding is overturned, rewrite the passage to say
  what is true rather than appending a correction beneath it, so no reader has to
  reconcile a claim with its refutation. A mistake a later session could
  plausibly make again also goes under the wrong turns.
- **Evidence goes beside the spec, in `<slug>.evidence.md`.** Session journals,
  measurements, traces, timelines and transcripts: what a conclusion rests on,
  rather than the conclusion. The spec states the conclusion and names the
  evidence entry, and a session opens the evidence only to re-examine one. A
  session's log entry goes there; what it changes about where things stand goes
  in the spec. The evidence file is not a spec, has no slug of its own, and is
  deleted with its spec.
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
