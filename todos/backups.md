# Backups: a 3-2-1-0 architecture for vulcanus + mini-nas

**This spec is maintained through implementation.** The work spans sessions, so
this is the handover artefact: every session updates the status table and what it
verified, records wrong turns honestly, and adds its log to
[`backups.evidence.md`](backups.evidence.md). That file also holds the starting
state, the K8up comparison, the capacity working, and each finished phase as it
happened.

**Documentation lands at the end of each phase, not at the end of the project.** A
phase's output is running in production the moment it deploys, and `docs/` is meant
to describe the running system, so each phase ends by writing what it made
permanently true into [`docs/backups.md`](../docs/backups.md), extending what earlier
phases left. Only deleting this spec and adding the
[`docs/project_log.md`](../docs/project_log.md) entry wait for the end.

Slug `backups`. [PR #17](https://github.com/DArtagan/vulcanus-proxmox/pull/17) closed
at the 2b boundary on 2026-10-13. Phase 3 is on a fresh `backups` branch from `main`,
in `.worktrees/backups`, under review in
[PR #19](https://github.com/DArtagan/vulcanus-proxmox/pull/19).

## Status

| Phase | What | State |
|---|---|---|
| A | Record the spec, open the review | done 2026-09-01 |
| 0 | Stop the bleeding — replication, retention, scrub | done 2026-09-03 — [PR #3](https://github.com/DArtagan/vulcanus-proxmox/pull/3) |
| 1 | Reclaim — dead guests, orphans | done 2026-09-18 — [PR #11](https://github.com/DArtagan/vulcanus-proxmox/pull/11) |
| 2 | Application backups — K8up + restic | done 2026-10-12 — [PR #13](https://github.com/DArtagan/vulcanus-proxmox/pull/13) |
| 2b | Delete the borg tree | done 2026-10-13 — [PR #17](https://github.com/DArtagan/vulcanus-proxmox/pull/17) |
| 3 | Performance — drop the OpenEBS disks from vzdump | **in progress** — applied 2026-10-16, measuring |
| 4 | Platform images offsite — PBS #2 + sync | not started; **gated on the mini-nas disks** |
| 5 | The offline copy — external disk | not started; needs a ~$200 purchase |
| 6 | Verification, notification, runbook | not started |

What phases 0 to 2b built is in [`docs/backups.md`](../docs/backups.md): the layers,
retention, the restic repository, K8up's schedules and dumps, restoring, reporting,
capacity and the operational notes. The repository host is in
[`docs/nixos.md`](../docs/nixos.md).

**Two purchases gate later phases.** 2x 4 TB for the mini-nas vdev expansion, which
Phase 4 waits on; and one 14-16 TB external disk for Phase 5. Phase 3 needs
neither. Deferred 2026-09-01, user's call — purchasing takes time. See *Capacity,
until the mini-nas disks arrive*.

---

## Why this exists

Reconnaissance on 2026-08-24 found that the Kubernetes backups had never produced a
backup, and three live failures besides: offsite replication of the VM datasets
failing every run since 2026-01-14, sanoid on mini-nas never once succeeding, and an
offsite pool never scrubbed in its life. Phases 0 to 2 closed all four.

The goal: satisfy 3-2-1-0, report when it stops working, restore one application
without rolling back a whole VM, and stop the nightly backup driving etcd into
eight-second fsyncs.

**Constraint, user's call 2026-08-24:** self-hosted only, no cloud subscription.

---

## Objectives

**Stated by the user:** 3-2-1-0 · notifications · granular restore · system
performance.

**Added during design, and accepted**

5. **Per-data-class RPO/RTO.** Media, photos and databases have different value and
   different change rates; one policy for all of them is why 7.75 TiB of re-rippable
   media consumed the offsite pool everything else needs.
6. **Assert freshness per protected object, not per job.** `syncoid-vulcanus-data`
   was red for seven months — but a green exit code would *also* have lied, because
   it was syncing eight of thirteen datasets and succeeding for those. The unit's
   result is not the backup's health.
7. **Recovery of the keys, not just the data.** Losing the age key makes every SOPS
   secret in git permanently undecryptable, and no restore of anything helps.
8. **Resistance to logical corruption, not only hardware loss.** Replication
   propagates a mistake within the hour. The defence is retention depth and an
   append-only store, not another mirror.
9. **A written, exercised runbook.** RTO is dominated by knowing what to do.

---

## Decisions already made — user's call, do not relitigate

- **Self-hosted only, no subscription.** Verbatim: *"Given that we have control over
  a couple of servers, I'd like to design a system that takes advantage of the
  hardware we've already got and saves cost by not paying a subscription."*
- **K8up** for application backups, chosen after a three-way comparison against
  Stash/KubeStash and Velero (in the evidence). Its honest weakness is that it does
  not back up Kubernetes objects, acceptable only because Flux owns them — stated so
  the omission is not mistaken for an oversight.
- **Second PBS VM on mini-nas + sync job** for the platform layer offsite.
- **Password manager** for bootstrap key escrow.
- **The restic repository lives in its own dedicated LXC**, on a ZFS dataset, so
  mini-nas can mount the replica and verify it natively.
- **Media's offsite replica on mini-nas is a primary tenet, not a lever.** Verbatim:
  *"While it is 'technically' possible to recreate — the level of effort is so great
  as to make the overall collection of data nearly priceless. Backing up that data
  should be a primary tenant of the design, not something we can compromise for the
  sake of an escape valve."* Capacity problems are solved with disks.
- **Count failure domains, not copies.** A copy on `rpool` is not a copy for
  disaster purposes.
- **Exactly three copies per class; none gets four.**
- **The external disk attaches to vulcanus** (hot-swap bay) and is **shelved away
  from the vulcanus site**.
- **mini-nas moves to 4-disk raidz1 vdevs** — the only geometry under which a 2x
  disk upgrade on vulcanus is sustainable.
- **Backup coverage is opt-out, not opt-in** (K8up's own default).
- **A deleted guest's disks retain exactly as a live guest's would**, on both layers,
  with nothing destroyed automatically — freeing that space is a deliberate act after
  inspecting what is there.
- **A reused VMID is reported and pushed to Pushover, never failed** (2026-09-19).
  Failing `pbs-freshness` would hold it down for up to 31 runs, and healthchecks
  notifies only on transitions, so a genuine backup failure inside that window would
  be silent.
- **VMIDs may be reused** — a guarantee kept by hand forever is not one worth
  promising. The cost, eviction from PBS within 31 runs, is in `docs/backups.md`.

### Phase 2's

- **The repository host is NixOS, deployed with colmena** (2026-09-20), starting the
  NixOS migration here rather than with the fileserver. Taken knowing colmena has not
  tagged a release since v0.4.0 (2023-05-15), against clan, which is far more active
  but is a framework whose scope overlaps this project's own backup layer.
- **Every backed-up PVC is backed up whole** (2026-09-20). No per-path exclusions,
  and `k8up.io/backup-restic-args` is not used. Verbatim: *"let's not bother
  excluding the PhotoPrism caches - keep it simple and back up the whole thing."*
- **Dumps: annotations for the relational three, PreBackupPods for SQLite**
  (2026-09-21). A PreBackupPod has its own network namespace and cannot reach a
  database over loopback; a ClusterIP Service per database was weighed against the
  annotations, which won as less machinery for the same result.
- **One weekly K8up Check**, on `apps`' `full` (2026-09-21). The host's
  `--read-data-subset` pass is Phase 6's.
- **The Pushgateway push is off** (2026-09-21). K8up's `POST` under one grouping key
  per namespace keeps only the last item pushed.
- **The `.talosconfig` is not escrowed** (2026-09-21), being derived from the
  machine secrets, which are.
- **The coverage check reads the store, per claim and per dump** (2026-09-22). A
  kube-state-metrics rule was weighed and left out; restic-exporter was ruled out.
  Image automation was extended to `kubernetes/k8up/` rather than moving the check
  into `infrastructure/`.
- **Content checks for every dump format, with pinepods dumped as plain SQL**
  (2026-09-22): a truncated custom-format dump still lists cleanly under
  `pg_restore --list`.
- **No homepage entry** for rest-server or K8up: entries are for services with a
  web UI.
- **A dump the store cannot serve fails the coverage check, and ages are compared
  exactly** (review of PR #13): rounded down to the hour, a missed 07:00 dumps run
  had passed, making the six-hour RPO twelve in practice.
- **The NixOS secrets are not encrypted to Flux's key** (review of PR #13). Verbatim:
  *"This can't be our reason, that's using one security context to do something
  else."*
- **One repository Secret, under a directory per namespace** (review of PR #13).
- **The repository host mounts under `/mnt`, not `/srv`** (review of PR #13), as
  the fileserver does. Verbatim: *"Yes, let's move to `/mnt` - I accept the
  change-over costs."*
- **No REST password kept in the clear on the host** (review of PR #13).
- **The repository container has 1 GiB** (review of PR #13): its recorded peak was
  290 MiB.
- **A claim holding only a database's data directory is excluded**, its dump standing
  in (review of PR #13).
- **Every WAL-mode SQLite database is dumped, and every SQLite copy is
  integrity-checked from the store**, Plex's with Plex SQLite (review of PR #13).
- **Each app's PreBackupPod lives in its own `backups.yaml`**, with one shared
  sqlite3 image under `kubernetes/k8up/` (review of PR #13).

---

## Architecture

Seven layers, unified not by one tool but by one **contract**:

> Every backup job pings a check on success. Every store has a scheduled integrity
> verification. Every protected object has a freshness assertion, evaluated **from
> the store** and independently of the job that writes it.

| Layer | What | State |
|---|---|---|
| 0 | ZFS snapshots on vulcanus (sanoid, VSS-exposed) | built |
| 1 | ZFS replication to mini-nas (syncoid pull) | built, repaired in Phase 0 |
| 2 | Application backups — K8up + restic, dedicated repo LXC | built in Phase 2 |
| 3 | Platform images — vzdump to PBS | built; Phase 3 reduces its scope |
| 4 | Platform images offsite — PBS #2 VM on mini-nas + sync | Phase 4 |
| 5 | Offline copy — external disk on vulcanus, restic | Phase 5 |
| 6 | Verification, notification, runbook, key escrow | escrow done; the rest is Phase 6 |

### Failure domains

Application state lives on `/var/openebs` = `vm-910-disk-1`, a zvol on `rpool`, so
the restic repository at `rpool/backups/restic` **shares its failure domain**.

| Class | Domain 1 | Domain 2 | Domain 3 | Fast-restore tier |
|---|---|---|---|---|
| App state | live on `rpool` | mini-nas (restic) | **external (restic, offline)** | restic local |
| Photos, books, filesync | live on `rpool` | mini-nas (ZFS) | **external (restic, offline)** | restic local |
| Media | live on `rpool` | mini-nas (ZFS) | external (restic, offline) | — |
| VM/LXC images | live zvol on `rpool` | mini-nas (PBS #2) | — *(deliberate)* | PBS local |

The local restic repository is **kept but not counted**. It is the fast-restore tier
and earns its place on the cases that actually happen — accidental deletion, a bad
upgrade, rolling back one app — which are far likelier than pool loss. At ~0.3 TiB
it is nearly free, where a local restic copy of *media* would have cost 7.5 TiB.

**VM/LXC images stay at two domains, deliberately.** They are reconstructible from
`terraform` plus `talosctl` with their data restored from restic, and from `ansible`
for the LXCs; the one irreplaceable component, the Talos machine secrets, is
escrowed.

### RPO and RTO per class

RTO assumes the runbook exists and the restore is not being invented on the spot.

| Class | RPO domain 2 | RPO domain 3 | RTO | Why |
|---|---|---|---|---|
| Databases | **6 h** | monthly | 4 h | Small, high-churn, no other source |
| App config and identity | 24 h | monthly | 4 h | Changes rarely; loss is unrecoverable |
| Photos, books, filesync | 1 h | monthly | 24 h | syncoid already hourly |
| Media | 1 h | quarterly | days | Re-servable from mini-nas in place |
| VM/LXC images | 24 h | — | 8 h | Rebuildable from IaC |

### Retention still to build

What runs today is under *Retention* in `docs/backups.md`. Still to come:

| Store | Policy | Depth |
|---|---|---|
| **PBS #2** | none of its own — sync with `remove-vanished`, mirroring the primary | tracks primary |
| **External disk** | keep-monthly 12, keep-yearly 3 | 3 y |
| **PBS primary, retired guests** | forget a group whose newest backup is over two years old | 2 y — Phase 4 |

### Schedule still to add

The estate runs four clocks, so the schedule is stated in UTC: vulcanus is
`America/Denver`, mini-nas `America/New_York`, Kubernetes UTC, and PBS VM 107's zone
is unverified — its jobs run in its own local time. What runs today is under
*Schedules* in `docs/backups.md`.

**10:00 UTC is reserved for vzdump** and nothing else touches the spindles then.

| UTC | Job | Where |
|---|---|---|
| 11:00 | PBS sync to PBS #2 | PBS |
| Sun 14:00 | PBS verify, both datastores | PBS |
| Sun 15:00 | `restic check --read-data-subset` | repository host |
| Sun 16:00 | restore drill — **not 10:00**, which in Kubernetes is vzdump's window | cluster |

The hourly syncoid pull always has one run inside the vzdump window, whatever offset
it is given. That is left alone deliberately: an hourly delta on `storage` is about a
megabyte, and skipping one hour costs more complexity than it saves.

### Freshness still to assert

The ZFS, PBS and restic assertions are built; see *Reporting* in `docs/backups.md`.
What remains:

- **The external disk:** last successful attach older than 35 days, as a
  healthchecks period.
- **A vzdump check.** A running backup already creates its PBS group, timestamped at
  start, so `pbs-freshness` reads an in-flight backup as fresh coverage. A job that
  started and died every night would look healthy to it alone.

Two things `pbs-freshness` does not catch, named rather than assumed. **A group
vanishing**: the check compares against the live guest list, so a vanished group has
nothing to be compared with; catching it needs a remembered inventory, which matters
more after Phase 4, where `remove-vanished` propagates a primary-side deletion
straight to PBS #2. And **scope drift**: adding a live guest to `exclude` moves it
from "fail if stale" to "report", making the check quieter rather than louder.

`pbs-freshness` carries two standing reports: `vm/100`, frozen and kept on purpose,
and `vm/107`, PBS itself, an empty group. Neither is a failure.

### Notification budget

The healthchecks.io free tier is 20 checks. Spending them only on what Prometheus
**cannot** see — the two hosts, PBS and the disk — is principled rather than a
bundling compromise: everything in-cluster uses Prometheus rules, plus **one**
external check the coverage CronJob pings, because Prometheus and that CronJob both
die with the cluster.

**Thirteen exist:** Watchdog, syncoid-storage, syncoid-root, syncoid-data,
sanoid-mini-nas, sanoid-vulcanus, pool-health-mini-nas, pool-health-vulcanus,
zfs-replication-freshness, pbs-freshness, restic-massfiles, restic-prune and
backup-coverage.

**Eight are still to come:** vzdump, PBS GC, PBS verify, PBS sync, PBS #2 verify,
restic check, the restore drill and the external disk.

That is 21, one over. The enumerated twenty assumed `syncoid-vulcanus-data` would
retire in Phase 4, but it narrows to `rpool/data/vm-107-disk-0` and keeps its name and
slot. So one check has to fold into another, or healthchecks — Apache-2.0 — gets
self-hosted **on mini-nas**, a third failure domain from both the cluster and
vulcanus, at no subscription cost.

All alerts land in Pushover. **PVE 9.2.2 and PBS 4.1 both support webhook
notification targets** — verified, `pvesh get /cluster/notifications/endpoints/webhook`
returns `[]` rather than an error — so vzdump and PBS ping directly, with no
scripting.

### The external disk

- **Attached to vulcanus** via the hot-swap bay, and **shelved away from the vulcanus
  site**, at the mini-nas site. If it lives where it attaches, domains 1 and 3 are
  co-located and one fire leaves a single backup copy on raidz1.
- **A restic repository**, populated by `restic backup` of `rpool/storage/*` and
  **`restic copy`** from the local repository. Not the repository backed up as files:
  a nested repository would make `--read-data` verify only the outer layer and turn a
  restore into a two-stage extraction needing two passphrases.
- **Filesystem:** a single-vdev ZFS pool with `copies=2` on metadata, for checksums
  and self-healing of metadata damage on a disk with no parity.
- **Repair path:** `restic check --read-data-subset=1/12` monthly detects damage; the
  repair is to re-copy the affected packs from the local repository or from mini-nas.
  Detection without a written repair path is not a plan.
- **Automation:** a udev rule matching `ID_SERIAL` starts a systemd unit — backup,
  copy, check, ping, then Pushover *"safe to detach."*
- **The reminder and the monitor are the same object:** a healthchecks check with a
  35-day period. Attaching pings it; not attaching turns it red and nags.
- Growth is measured, not guessed: monthly snapshots run 6.14 TiB (2024-09) to
  6.87 TiB (2026-08), **~380 GB/year**. One 14-16 TB disk carries the whole 7.5 TiB
  working set with roughly fifteen years of runway.

---

## Capacity, until the mini-nas disks arrive

The two disks that take mini-nas from 12.6 to 19.1 TiB are deferred. The capacity
model and the purchase equation are in `docs/backups.md`; the worked figures are in
the evidence.

**The standing rule: do not let vulcanus pass ~14.8 TiB used** (12.6 / 0.85). It was
at 13.9 TiB on 2026-08-24, before Phase 1's reclaim. mini-nas read 73% after Phase 1.

**Phase 4 waits for the disks.** Holding PBS #2 and a still-replicated `rpool/data`
at the same time, at 12.6 TiB, peaks at 93%, the range where ZFS allocation degrades.
Built after the expansion, PBS #2 goes straight onto the expanded `rpool` and
`vulcanus-data` retires in the same phase, leaving occupancy near
(10.95 + 1.2 − 1.8) / 17.6 ≈ **59%**. Deferring costs a format gap, not a coverage
gap: `rpool/data` still carries the guests offsite as ZFS. *Fallback, if Phase 4 has
to happen first:* PBS #2's datastore on `spool`, which keeps `rpool` off 93% but must
move when `spool` is destroyed for its bays — a `zfs send` or a full ~1.2 TiB re-sync
over the WAN. Take it only if the wait becomes long.

**mini-nas's pool-health thresholds are 90% warning / 94% critical until the
expansion**, against 80/90 everywhere else, so a warning does not fire continuously
against a predicted 82-87%. Restore 80/90 once the expansion lands.

### What to buy, and what the expansion involves

Buy **2x 4 TB** and put them in `spool`'s two bays, the only free room in the 8-bay
chassis. `spool`'s own 1.8 TiB disks cannot expand `rpool`: `zpool attach` onto a
raidz vdev needs a disk at least as large as the smallest member, 3.64 TiB in
raidz1-0 and 2.72 TiB in raidz1-1. So destroy `spool`, fit the new disks, and expand
both vdevs from 3 to 4 wide — mini-nas has `feature@raidz_expansion` enabled. The two
2 TB Toshibas become cold spares. One 4 TB disk strands ~0.9 TiB in raidz1-1 until
that vdev's older members are replaced, which is worth it for uniform disks when the
cascade later delivers 4 TB drives.

- **The gain is ~5.0 TiB, not 6.5**, because expansion keeps existing blocks' parity
  ratio: ~17.6 TiB effective at once, converging on 19.1 as old data is rewritten.
- **Expansion is slow.** It reflows the whole vdev, and a single-disk resilver here
  took 2 d 21 h, so budget days per vdev and do them one at a time.
- **All the swap is on the `spool` disks** — two 16 GiB partitions. The replacements
  need swap partitions carved by hand before joining the pool, and `disk-config.nix`
  updated to match, because disko does not apply to a live system.

**The later rounds:** round 2 is the pinch point — 8 TB disks for vulcanus cap it at
78.4% used rather than 80%, so 6 TB is the comfortable choice — and after round 3 the
constraint stops binding.

---

## Loose ends from finished phases

- **Five stale `syncoid_vulcanus` holds on `storage`**, dated 5-6 January 2026, pin
  every block freed since. Harmless but worth releasing:
  `zfs release syncoid_vulcanus <snapshot>`. `zfs holds -r <dataset>` does not find
  them; `zfs get -r -t snapshot userrefs <dataset>` does.
- **A stale grant on mini-nas:** `zfs allow` on `rpool/foreign-backups/vulcanus` names
  uid 994, where the syncoid user is 993. Cruft, not a dependency: the syncoid module
  grants what it needs around every run.
- **No restore runbook until Phase 6.** See the sequencing hazard there.
- **LXC 105 carries a pending `delete mp3`** until its next restart. That was the borg
  tree's bind mount, already released by hand; Proxmox cannot remove a mount point from
  a running container, and applies the deletion before the next start, so the missing
  dataset cannot fail that boot. `pct pending 105` shows it.
- **LXC 105's books mount is moving from `mp4` to `mp3`** (2026-10-16). With `mp3`
  gone the slots ran 0, 1, 2, 4, and the provider reads back slots `0..N-1`, so books
  never reached state and every plan added it again. `terraform/main.tf` now declares
  it at slot 3. The provider deletes only slots it holds in state, which `mp4` never
  was, so after the apply that adds `mp3`, remove the old entry by hand:
  `pct set 105 --delete mp4`. A bind mount's entry is only a config line; deleting it
  touches no data. Then a plan should show nothing for LXC 105. Both changes stay
  pending until the container restarts, and books stays mounted at the same path
  throughout.

---

## Phase 3 — performance

`backup=0` on `vm-910-disk-1` and `vm-911-disk-2` (the OpenEBS data disks). Safe only
after Phase 2, which is what makes the sequencing non-negotiable: granularity first,
then performance. Nightly vzdump drives etcd's p99 WAL fsync from a 0.25 s floor to
3.8-8.7 s; see [`etcd-disk-latency.md`](etcd-disk-latency.md). After a guest restart
QEMU discards its dirty bitmap and the next run reads the whole 1 TB device — 142
minutes instead of 23. Phase 2 added load of its own: the mass-file run took etcd's
p99 to 0.651 s mean against a 0.245 s control, which is why this phase measures the
net.

**Where it stands, 2026-10-16.** `backup = false` on `virtio1` in
`terraform/modules/proxmox_talos_vm/main.tf`, inlined rather than a variable, and the
docs written for the state it produces: *The workers' OpenEBS disks* in
`docs/backups.md`, which carries the PBS restore's reattach step, and the restart and
trim costs in `docs/talos.md`. The baseline, the coverage of `/var/openebs`, and the
source reading that says the flag applies live and what a restore does are in the
evidence.

What remains, in order:

1. ~~Deploy and apply~~ — done 2026-10-16, ahead of the 10-17 backup. `qm config`
   shows `backup=0` on both workers' `virtio1` with nothing pending, and both QEMU
   processes date from 10-09 04:26 UTC, so the apply restarted neither.
2. Confirm from the 10-17 task log that `virtio1` is excluded on both workers and
   `virtio0` reports `dirty-bitmap status: OK`, not `created new`.
3. Measure the after against the baseline in the evidence — a week of ordinary
   nights against 10-10 to 10-16; see *Verification*.
4. The PBS datastore should shrink once the last backup holding a `virtio1` leaves
   `keep-last 31` and GC runs, from 643 GiB used on 10-16. Record the figure; Phase 4's
   PBS #2 sizing and the mini-nas occupancy sums used ~1.2 TiB.

## Phase 4 — platform images offsite

**Gated on the two mini-nas disks**, and its datastore goes on the expanded `rpool`;
see *Capacity, until the mini-nas disks arrive*, including the `spool` fallback.

Second PBS VM on mini-nas's Proxmox VE — **not** the NixOS module. proxmox-nixos
lists "Proxmox backup server" under its **Roadmap**, and the module is 101 lines
exposing only `enable` and `localIP`.

Remote plus scheduled sync job, and **a verify job on the target, because PBS sync
does not verify chunks on arrival.** Add the verify job the primary datastore also
lacks — but **not a prune job on PBS #2**: it syncs with `remove-vanished` and keeps
no retention of its own, so it tracks the primary exactly and a retired guest's
frozen group is preserved rather than expired.

**On the apparent contradiction:** a VM was ruled out for the restic repository
because a zvol is opaque to the host and cannot be verified from outside. PBS #2 is a
VM with exactly that property. The difference is that PBS verifies *itself* from
inside the guest, which restic on a zvol would not. The in-guest verify job is
load-bearing, not optional, and is the reason the two decisions are consistent.

### Retiring `rpool/data` from replication — except the PBS appliance

Guest images leave ZFS for PBS, with **one exception that stays replicated**:
`rpool/data/vm-107-disk-0`, the Proxmox Backup Server VM's own OS disk. Decided
2026-09-19, user's call.

**Why the exception.** PBS #2 gives the *chunks* an offsite twin, but nothing gives
PBS #1's own configuration one. `/etc/proxmox-backup` lives on that OS disk, and
retiring `rpool/data` wholesale would leave it the only thing in the estate with one
copy that a reinstall cannot reconstruct. Keeping the one dataset costs ~18 GiB, needs
no new mechanism, and preserves a `zfs send`-back restore that skips the manual
install `terraform/modules/proxmox_backup_server/main.tf` still requires.

**What it does not buy.** If `rpool` is lost, recovery still goes *through PBS #2* —
it is a working PBS holding every backup, and reading its datastore never needs the
primary's config. The exception buys a faster rebuild of the primary, not the backups
themselves.

Four changes that must land together, because each one alone breaks the freshness
check:

- **Narrow the `vulcanus-data` syncoid command** rather than dropping it. `source`
  becomes `rpool/data/vm-107-disk-0`, `target`
  `rpool/foreign-backups/vulcanus/data/vm-107-disk-0`, and `recursive = false` — a zvol
  has no children. **No re-seed:** the target already exists and shares a snapshot
  lineage with the source, so syncoid continues incrementally.

  Keep the command's *name*. `checkNameFor` derives the healthchecks check from the
  unit name, so renaming it costs a new check and a new sops secret for no functional
  gain. A comment at the command carries the scope instead.

- **Narrow the check's source roots** at
  `~/repositories/mini-nas/modules/backup_monitoring/default.nix:183`, from
  `rpool/data` to `rpool/data/vm-107-disk-0`. The loop asserts a target counterpart for
  every dataset under each root, so leaving `rpool/data` there turns every live guest
  into a missing-counterpart note the morning after the replicas go.

- **Destroy the siblings, not the parent.** `rpool/foreign-backups/vulcanus/data` has
  to survive as the container for the retained zvol, so each sibling is destroyed
  individually — ~1.44 TiB less the ~18 GiB kept. Any sibling left behind is reported
  stale every day, because the age loop walks everything under
  `rpool/foreign-backups/vulcanus` with no exclusion.

- **Leave mini-nas's sanoid entry alone.** `"rpool/foreign-backups/vulcanus/data"` is
  `replica-shallow` with `recursive = true`, which goes on pruning the retained child
  unchanged. Verified 2026-09-19 — the one step here easiest to "fix" unnecessarily.

Afterwards PBS is the only lineage holding **guest images**. `vm-107-disk-0` is not a
guest image — it is the appliance that stores them — so it sits outside the two-year
expiry below and keeps `rpool/data`'s ordinary sanoid policy.

### Deleted guests expire at two years

Decided 2026-09-18: a group whose newest backup is older than two years is forgotten
entirely. No ladder, no thinning — user's call: *"keep it simple with just a two-year
expiration of the whole group."* Thinning would not pay for itself anyway: PBS dedups
at 37x, and a dead guest's 31 backups are one full plus thirty deltas of a machine
that stopped changing. All of the space is in the cliff.

**The predicate carries no liveness question:**

> Forget any group whose newest backup is older than two years.

A live, in-scope guest cannot have a two-year-old newest backup — if one does,
`pbs-freshness` already reports it. So the job never asks whether a guest still
exists: no source list, no SSH, and none of the false-positive path that sank the
quarantine design (see the wrong turns). A live but *excluded* guest falls under the
same rule deliberately; a two-year-old backup of a running machine is not one anyone
restores.

**It runs against the primary only.** PBS #2 syncs with `remove-vanished`, so a
forgotten group propagates on the next sync and both stores stay identical from one
policy in one place.

**Why it lands in this phase.** Until `vulcanus-data` is retired from syncoid, mini-nas
still holds guest zvols that no age rule expires: sanoid has no age term, and
`replica-shallow` carries only dailies. A two-year PBS policy shipped before that
would be fiction, because the effective retention would be the longer of the two,
which is forever. Nothing expires before March 2028 in any case — `vm/200` is the
oldest frozen group, last written April 2026.

**`vm/100` expires in September 2028, and nothing wanted goes with it.** It is the
RancherOS guest, and its root disk carries the old `docker-vulcanus` tree. The one
thing the user wanted from that tree, the Fallgrane Minecraft server, was extracted to
`rpool/storage/media` during Phase 2b and replicates with it; see the evidence.

**If this phase slips past that,** the fallback is the symmetric job on mini-nas:
destroy a replica dataset whose newest snapshot is older than two years, the same
age-only predicate. Not built now, because it is work for a lineage this phase
retires.

**PBS has no native job for this** — prune jobs thin, they do not forget — so it is a
timer and a script: list groups, compare the newest backup's timestamp, forget the
group. The GUI forgets a group in one action, so the capability is certain; the CLI or
API call that does it is not yet confirmed and should be before this is built.

## Phase 5 — the offline copy

Gated on buying one 14-16 TB external disk (~$200). Hot-swap into vulcanus; ZFS pool
with `copies=2` metadata; `restic backup` the mass files and `restic copy` the app
repository; udev-triggered automation; Pushover on completion; shelved at the
mini-nas site. Passphrase to the password manager. See *The external disk*.

## Phase 6 — verification, notification and the runbook

- A `--read-data-subset` pass monthly on both repositories, **including the offsite
  replica**, which is what makes it a verified copy rather than a hopeful one. The
  weekly K8up Check is structural only.
- The freshness assertions and checks under *Freshness still to assert* and
  *Notification budget*.
- **The automated restore drill**, weekly: restore designated canaries — one Postgres
  dump, one WAL-mode SQLite database, one config directory — into scratch space and
  assert integrity (the Postgres dump loads, `PRAGMA integrity_check` returns `ok`, a
  manifest checksum matches), then ping a check. This is the "0" in 3-2-1-0 and the
  leg nobody builds. *Restoring from restic* in `docs/backups.md` carries what the
  manual restores of 2026-10-12 learned.
- Prometheus rules for the in-cluster layer, following the `cronjob-health`
  convention. **The bucket table in `cronjob:max_seconds_without_success` has no
  weekly branch** — a weekly job lands in the 26 h bucket and would alert falsely
  every day. Extend that rule first.
- **The restore runbook in `docs/backups.md`** — objective 9's deliverable. It must
  carry the sequencing hazard: on a rebuilt cluster **Flux reconciles applications
  against freshly provisioned empty PVCs, and databases initialise before any
  restore lands.** The runbook has to suspend the relevant Kustomizations,
  restore, then resume. Likewise, we need to account for and rebuild the
  worker's OpenEBS disk up from fully empty, using K8up backups. Writing that
  ordering down before it is needed is the whole point.

---

## Verification

Each phase asserts a number that moves for the reason claimed. Checks that outlive
the session become alert rules rather than notes, per the Documentation Protocol.
What phases 0 to 2b asserted is in the evidence.

- **Phase 3** — vzdump task-log **duration** for VM 910 before and after, *and* the
  net change in `avg_over_time` of etcd fsync p99 across matched 24 h windows — not
  just the backup window, because Phase 2 added load elsewhere. `transferred` reports
  logical device size and is not the measure. The 24 h mean moves little: half an
  hour of backup in a day, it read 0.246-0.301 s across the baseline including two
  full reads. So also compare minutes above 0.5 s, peak p99 and failed proposals over
  10:00-11:30, where an ordinary baseline night reads 6-17 m, 0.8-1.24 s and 0-20.
  The full-read case, 1.10 TiB becoming 100 GiB, is the larger win and has no
  matched night until something restarts a worker's QEMU process.
- **Phase 4** — a verify job passing on the mini-nas datastore, and a test restore of
  one guest **from the offsite copy**. `zfs-replication-freshness` stays green across
  the `rpool/data` retirement, which is what says the source-root trim and the replica
  destroy landed together rather than one without the other. Green is not enough on its
  own here, because the narrowed command must still be *doing* something: assert that
  `rpool/foreign-backups/vulcanus/data/vm-107-disk-0` gains a snapshot newer than the
  retirement, and that it is the only dataset left under `.../vulcanus/data`. A syncoid
  command narrowed to a dataset that stopped receiving would pass the source-root
  comparison and fail nothing until the 26 h limit caught it the next morning. The
  two-year expiry is asserted by running it with the threshold lowered until it
  selects a known frozen group and nothing else, because at two years it correctly
  names nothing and a job that has only ever matched nothing is untested.
- **Phase 5** — `restic restore` from the external on a machine that has never seen
  it, using only the passphrase from the password manager, pulling back one database
  dump **and** one media file. A disk that has only ever been written is not a copy.
- **Phase 6** — deliberately break one job, confirm the check goes red, then confirm
  it goes green again. Both directions, per `docs/README.md`. Then walk the runbook
  end to end on a scratch namespace, including the suspend-restore-resume ordering.

---

## Wrong turns, recorded so they are not repeated

Inheriting mistaken reasoning is worse than inheriting none. The full list, with the
operational slips of each phase, is in the evidence; these are the ones the remaining
phases could repeat.

- **proxmox-nixos was described as "shipping" a `proxmox-backup` module.** It does,
  but the project lists "Proxmox backup server" under its **Roadmap** and the module
  exposes only `enable` and `localIP`. Read the roadmap, not just the module list.
- **mini-nas was projected at ~70% occupancy after Phase 4.** The snapshot bloat was
  subtracted *and* the `rpool/data` retirement was subtracted, when the bloat lived
  almost entirely *inside* `rpool/data`. Check that two savings are not the same
  bytes.
- **The external disk was first specified as `zfs send`,** and then as a restic
  backup *of the restic repository as files*. The first gives no format diversity;
  the second nests repositories. `restic copy` is the correct primitive.
- **Photos, books and filesync were given four copies** when three was the
  requirement. Counting stopped at "how many exist" instead of "how many are
  wanted".
- **The copy count hid a failure-domain inversion.** App state showed three copies,
  but two of them shared `rpool`. Count domains, not copies.
- **The replication freshness check was to learn orphan-against-superseded
  classification**, two sessions of design for a lineage that leaves ZFS in Phase 4,
  when destroying a replica with its guest made the existing check sufficient. Ask
  what still needs a mechanism after the next phase lands, before building it.
- **An `rpool/archive` tree, a rename runbook and an automated quarantine were
  designed** to hold retired guests. PBS and the mini-nas replica already freeze a
  destroyed guest's backups indefinitely, so the archive was a third copy of the same
  promise, and the quarantine existed to catch failures of a runbook that need not
  exist.
- **The case for reusing VMIDs was first argued on the one layer in view.** Reuse was
  called merely recoverable without looking at PBS, where `keep-last 31` evicts the
  retired guest. Reuse is tolerable *and* it destroys data.
- **The first append-only check could not fail.** `| tail` hid the exit code and the
  flags were invalid, so both paths failed for one unrelated reason and it read as a
  pass. Phase 6's break-it-both-ways test exists for this.
- **A restore through an SMB share silently merged files differing only in case**
  (Phase 2b, `fallgrane.pending` and `Fallgrane.pending`), with every exit status 0.
  Restore onto ZFS directly, and check every file's size against the snapshot's.
- **The first MariaDB restore met the image's temporary init server**, which answered
  the readiness ping and then restarted: "Server has gone away" at line 1, nothing
  loaded. A restore drill has to wait for the real server.

---

## Prompt to open with

> Read `todos/backups.md` in full. It is the live spec for the `backups` project,
> and its status table says which phases are done. The slug is `backups`, worked in
> `.worktrees/backups`; the header says whether the branch and its review are still
> open or the next phase starts a new one from `main`. Pick up at the first phase not
> marked done. Keep the status
> table and the verified facts current as you go, and add your session's log to
> `todos/backups.evidence.md` — this spec is the handover between sessions, so leave
> it able to start the next one cold. `wt review-open`, `wt deploy` and
> `wt review-close` push and must be run by the user, not by you.
