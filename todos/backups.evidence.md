# Backups: the evidence

What [`backups.md`](backups.md) rests on: the reconnaissance that opened the
project, the comparison that chose K8up, the capacity working, and each finished
phase as it happened — what was done, measured, restored and got wrong. Read the
spec first, and open this to re-examine one of its conclusions. A session working
a phase adds its log here, under that phase.

## The starting state — verified 2026-08-24

| | vulcanus (remote site) | mini-nas (local site) |
|---|---|---|
| Role | Proxmox VE 9.2.2, everything runs here | NixOS + Proxmox VE, replication target |
| Pool | `rpool`, 2x raidz2 (4x4 TB + 4x10 TB) | `rpool`, 2x **raidz1** (3x4 TB + 3x3 TB) |
| Usable / used | 24.5 TiB / 13.9 TiB (56%) | 12.6 TiB / 11.3 TiB (**88%**) |
| Scrub | monthly, last 2026-08-09, clean | **never, in the pool's life** |
| SSD | **none.** 8 spindles, no SLOG, no L2ARC | none |
| ZFS | 2.4.2, `raidz_expansion` **disabled** | 2.4.4, `raidz_expansion` **enabled** |
| Spare capacity | ~1 free SATA port; hot-swap bay | all 8 bays full; `spool` 1.8 TiB unimported |

### Three live failures

1. **`syncoid-vulcanus-data` has failed every run since 2026-01-14.** Five datasets
   have diverged snapshot chains and syncoid correctly refuses to clobber them.
   Offsite staleness: `vm-911-disk-0/1/2` (**all of talos-worker-1**) 2026-01-16;
   `vm-910-disk-2` 2026-01-12; `subvol-105-disk-0` (fileserver LXC rootfs, holding
   the Samba config) 2026-05-26. No `OnFailure=` on any unit, so nothing reported it.
2. **`sanoid` on mini-nas has never once succeeded**, fataling hourly since
   2026-01-09: `services.sanoid.enable = true` with no `datasets` declared generates
   an empty config file. Nothing prunes the replication target — **29,305 snapshots
   offsite against 1,467 onsite** — which is the direct cause of the 88%.
3. **mini-nas has never been scrubbed.** Nine months, 11.1 TiB, raidz1 across drives
   up to thirteen years old. A disk replaced 2026-07-28 took **2 d 21 h** to
   resilver.

### The structural problem

PBS runs as **VM 107 on vulcanus** with its 2 TB datastore as a raw file on `rpool`
— the same eight spindles holding the originals. Otherwise the healthiest component
in the estate (weekly GC succeeds, 37x dedup, every recent job `OK`), but with **no
verify job, no prune job, no sync job and no offsite copy**. Losing `rpool` loses
the primaries and every VM backup together.

`rpool/backups/borg` holds 2.14 TiB in six repositories (video 1.5T, audio 413G,
photos 144G, rancheros 76G, games 46G, syncthing 20G), each last written **December
2022**, replicated nowhere. The share root is `root:root` 0755 and the SMB user
`rancher` cannot create the repositories borgmatic expects, which is why borgmatic
has never produced a backup.

### Performance

Per [`etcd-disk-latency.md`](etcd-disk-latency.md): nightly vzdump drives etcd p99
WAL fsync from a 0.25 s floor to 3.8-8.7 s and pages on **every** run. After a guest
restart QEMU discards its dirty bitmap and the next run reads the whole 1 TB device
— 142 minutes instead of 23.

### Application state, measured

139 GB total: **122 GB on worker-0**, 17 GB on worker-1. ~27 GB is observability
data (Prometheus TSDB 12 GB, VictoriaLogs 15 GB) and 6.8 GB is borgmatic's own
regenerable cache. **Irreplaceable application state is under 100 GB.**

Databases: **pinepods** (PostgreSQL 18.6, 31 MB), **photoprism** (MariaDB 10.11,
149 MB), **salamander** (MariaDB 10.11, 364 MB). The two MariaDB instances already
write daily `.sql` dumps via PhotoPrism's *own* scheduler — undeclared in this repo,
so a future image default could silently stop them. Pinepods'
`pinepods-backups-pvc` is **empty**; its intended `pg_dump` never runs.

Fourteen SQLite databases, several in WAL mode (headscale, linkding, rustdesk) where
a naive file copy is not crash-consistent. Several PVCs hold **cryptographic
identity** that no data restore repairs: `headscale-data-pvc`
(`noise_private.key`), `syncthing-data-pvc` (`cert.pem`/`key.pem` — the device ID
*is* the key), `rustdesk-data-pvc`.

### Debris to clear

Three orphaned hostpath directories on worker-0 with no PV object (25 GB stale
PhotoPrism storage, 434 MB, 131 MB); four orphaned `traefik*` PVCs in
`infrastructure`; PBS groups `vm/200`, `vm/101` and `vm/106` pinned forever because
prune is client-side; dead guests still replicating offsite (`vm-100-disk-0` 256 GB,
`rpool/rancheros` 40 GB, `vm-101-disk-0` 6 GB).

## Why K8up, against Stash and Velero

Compared 2026-08-24 against each project's own source, API reference and licence.

| | K8up | Stash / KubeStash | Velero |
|---|---|---|---|
| Repo | `k8up-io/k8up` | `stashed/stash` | `velero-io/velero` |
| Licence | **Apache-2.0** | AppsCode-Community-1.0.0 (open core) | Apache-2.0 |
| Activity | 1,017 stars, v4.10.0 2026-07-17 | 1,424 stars, **v0.42.0 2025-10-24** | 10,253 stars, v1.18.2 |
| Licence key to install | no | **yes, even the free tier** | no |
| Engine | restic | restic | Kopia |
| Backs up K8s objects | no | yes | **yes** |
| Self-hosted repo target | **`Local` and `RestServer`** | **Enterprise only** | **object storage only** |
| Database dumps | **`backupcommand` annotation** | **Enterprise only** | exec hooks |
| Scheduled integrity check | **`Check` CRD** | — | none |
| Restore | `Restore` CRD to existing PVC | yes | by namespace/label |

All three work on the volume type. Velero's FSB documents *"hostPath volumes are not
supported. Local persistent volumes are supported"* — all 34 OpenEBS PVs here use
`spec.local` with node affinity, so they qualify. No `VolumeSnapshotClass` exists, so
every option reads the live filesystem.

- **Stash is out on licence mechanics, not quality.** Community Edition lacks
  *Database Backup, Auto-Backup, Batch Backup and Local Backend support* — both
  capabilities most needed here are behind Enterprise, contradicting the
  no-subscription constraint. It requires a renewable key for the *free* tier,
  putting a vendor licence server in the backup path, and is superseded by KubeStash
  whose feature split is not publicly documented.
- **Velero's strength is already paid for.** Flux reconstructs every object from git,
  SOPS secrets included. Its cost is real: FSB accepts object storage only, so
  self-hosting means MinIO or Garage — a new stateful service on `rpool` that joins
  the backup path and must itself be protected. No scheduled repository verification,
  and FSB backs up only volumes mounted by a running pod, currently excluding
  `audio-pvc`, `media-pvc`, `podgrab-data-pvc` and the four orphan `traefik*` PVCs.
- **K8up's honest weakness: it does not back up Kubernetes objects.** Acceptable only
  because Flux owns them. Stated explicitly so a future reader does not mistake the
  omission for an oversight.

Borgmatic stays out on mechanism: borg locks a repository to a single writer, so
per-PVC backups serialise. VolSync was assessed and is out for having no database
dump hooks and no documented integrity check.

## Capacity working

### Occupancy through the phases, projected 2026-09-01

Without the vdev expansion, at 12.6 TiB, occupancy would have peaked badly:

| After | mini-nas holds | Occupancy at 12.6 TiB |
|---|---|---|
| Phase 0 (sanoid finally prunes) | 10.3 | 82% |
| Phase 2 (+ restic replica 0.65) | 10.95 | **87%** ← the peak |
| Phase 3 | unchanged | 87% |
| Phase 4a (+ PBS #2, `rpool/data` still replicated) | 11.75 | 93% |
| Phase 4b (`vulcanus-data` retired, −1.8) | 9.95 | 79% |

93% is not a threshold quibble — it is the range where ZFS allocation degrades, which
is why Phase 4 does not run at 12.6 TiB.

### r — the fraction of vulcanus's data needing an offsite twin

| vulcanus dataset | USED (TiB) | Offsite twin |
|---|---|---|
| `rpool/storage` | 8.06 | yes, ZFS replica 8.10 |
| `rpool/data` | 1.44 | no, as ZFS — content lives in PBS |
| `rpool/proxmox_backup_server` | 1.61 | no, as ZFS — PBS *sync* carries the content |
| `rpool/ROOT/pve-1` | 0.20 | yes, 0.21 |
| `rpool/backups/restic` (new) | 0.50 | yes, 0.65 including superseded packs |
| PBS datastore #2 on mini-nas | — | 1.20 |
| **vulcanus used 11.8** | | **mini-nas needs 10.2** |

**r = 10.2 / 11.8 ~= 0.85.** The gap from 1.0 is entirely the VM layer: vulcanus pays
3.05 TiB for it (live zvols plus datastore) while mini-nas pays 1.20. Everything else
is 1:1, so there is no further slack to find.

`r` was ~0.70 before this work only because borg (2.14 TiB) and the PBS datastore
inflated vulcanus's usage without inflating mini-nas's. **That slack was an artefact
of the backups being fake.** A lower ceiling on disk-size jumps is the price of a
working 3-2-1, not a regression.

### Four rounds of upgrades

TiB throughout; constraint is mini-nas usable >= 0.68 x vulcanus usable.

| Round | Action | vulcanus | usable | mini-nas | usable | needs | |
|---|---|---|---|---|---|---|---|
| 0 | today | 4, 10 TB | 25.5 | 3x4, 3x3 TB | 12.7 | 17.3 | fails |
| 1 | +2 disks, expand both vdevs to 4-wide | 4, 10 TB | 25.5 | 4x4, 4x3 | **19.1** | 17.3 | ok |
| 2 | 4 to **8 TB**; 4 TB replaces mini-nas's 3 TB members | 8, 10 TB | 32.8 | 4x4, 4x4 | 21.8 | 22.3 | marginal |
| 3 | 10 to **16 TB**; 10 TB replaces mini-nas's 4 TB members | 8, 16 TB | 43.7 | 4x10, 4x4 | 38.2 | 29.7 | ok |
| 4 | 8 to **20 TB**; 8 TB replaces the other vdev's members | 20, 16 TB | 54.7 | 4x10, 4x8 | 49.1 | 37.2 | ok |

- **Round 0 already fails at u = 0.80.** Supported utilisation today is 58.7% and
  vulcanus sits at 56% — *at* the limit, not approaching it. Round 1 is not optional
  and costs two used disks.
- **Round 2 is the pinch point.** 8 TB (7.28 TiB) misses the 6.99 TiB ceiling by 2%,
  capping vulcanus at 78.4% rather than 80%. Acceptable; 6 TB is the comfortable
  choice.
- **After round 3 the constraint stops binding** — 38.2 TiB against 29.7 needed, and
  rounds 4+ allow ~22 TB disks. The discipline is entirely front-loaded.

See *`spool`: bays, not disks* above for why its disks cannot join either vdev, and
why its bays are wanted anyway.

## Phase A — record the spec

Create the `backups` branch as a worktree off `main`, write this spec superseding
the 2026-08-06 audit, commit with a `Project: backups` trailer. Then **the user runs
`wt review-open`** to freeze `review/backups-base` and open the draft PR —
`.config/wt.toml` notes every alias there pushes and the SSH key is
passphrase-protected, so an agent cannot unlock it. Nothing else starts until the
review exists, because the PR must be opened after the first commit and before the
first deploy.

## Phase 0 — stop the bleeding

- ~~**Key escrow first**~~ — **done 2026-09-01.** The age keypair is in the password
  manager and the local `age.agekey` was deleted. That cost no access: `.sops.yaml`
  carries three recipients and any one private half opens every file, local `sops -d`
  goes through the `thenixbeast_will` ssh-ed25519 key, and Flux decrypts from the
  in-cluster `sops-age` Secret in `flux-system`, which still holds the key.
  ~~Still outstanding: `.talosconfig` and the Talos machine secrets.~~ Closed in
  Phase 2: the machine secrets are escrowed, and the `.talosconfig`, derived from
  them, deliberately is not. See *Escrow, including what Phase 0 left open*.
- Repair the five diverged syncoid datasets — the procedure is below.
- Declare `services.sanoid.datasets` on mini-nas per the retention table. **Expect the
  first run to be long and I/O-heavy**: it destroys roughly 27,000 snapshots in one
  pass. Run it when nothing else needs the pool.
- Enable `services.zfs.autoScrub` on mini-nas, and **start a scrub by hand once
  deployed**. Until one completes, `pool-health-mini-nas` reports the pool as never
  scrubbed and stays red — which is accurate rather than noisy, but it will take days
  on 11 TiB of raidz1, so it is worth starting deliberately rather than discovering.
- Import `spool` and add the matching `fileSystems` entry — the gotcha mini-nas's own
  CLAUDE.md warns about.
- ~~**Expand both mini-nas vdevs from 3 to 4 disks**~~ — **deferred 2026-09-01**,
  pending disk purchase. Buy **2x 4 TB** and put them in `spool`'s bays; see
  *`spool`: bays, not disks* for why its own disks cannot do the job, what the
  expansion actually yields, and the swap that comes out with it. Meanwhile see
  *Operating without the vdev expansion*.
- **Import `spool`** — so its contents and true size are visible before it is
  destroyed for its bays, and so it can host PBS #2 if that phase cannot wait.
- `OnFailure=` plus a success ping on every sanoid and syncoid unit.
- Pool-health timers on both hosts.

### Measured on 2026-09-02, after deploying

Three numbers that correct estimates elsewhere in this spec.

**The offsite bloat was never in `storage`.** Pruning `photos` from ~5,783 snapshots
to 114 freed almost nothing. Snapshots of data that does not change cost essentially
nothing, so the ~1.2 TiB of excess is all in the `data` zvols, which churn — and
those are what Phase 4 retires. Any future estimate of what retention will reclaim
should be made against `data` alone.

Final, after both the prune and the diverged-dataset repair: rpool 89% → **76%**,
2.07 → **4.39 TiB free**, **2.32 TiB reclaimed**, and 30,404 → **1,083** snapshots.
The count settled *below* the ~1,230 the retention policy allows, because several
datasets do not have enough history to fill it.

That is well ahead of the ~1.2-1.4 TiB projected, because 624 GB of it was in the
diverged datasets and nothing had accounted for those. Occupancy also lands at 76%
rather than the 82% predicted in *Operating without the vdev expansion*, which buys
more headroom than that section assumes before the disks arrive.

**The first scrub in the pool's life came back clean** — `repaired 0B in 20:23:38
with 0 errors`, 2026-09-02. 11 TiB of the only offsite copy, on raidz1 across drives
up to thirteen years old. That was the largest unknown in the estate and it is now a
known.

**A scrub starves the prune.** Running concurrently, sanoid managed ~2 snapshot
destroys per minute — a ten-day pace for the backlog. With the scrub finished the
same work runs orders of magnitude faster. Sequence these rather than overlapping
them.

### Stale syncoid holds survive the prune

Five snapshots under `storage` carry `syncoid_vulcanus` ZFS holds dated 5-6 January
2026 — days before `syncoid-vulcanus-data` first failed on the 14th. They are
leftovers from interrupted sends, `userrefs: 1`, and sanoid can never destroy them:

```
cannot destroy snapshot ...@autosnap_2026-01-05_03:00:06_hourly: it's being held
```

Harmless in themselves, but they are the oldest snapshots on those datasets, so they
pin every block freed since January. On `storage`, where the data barely changes,
that costs little. Release them with `zfs release syncoid_vulcanus <snapshot>` when
convenient. None are on `data`, so they do not block the diverged-dataset repair.

Note `zfs holds -r <dataset>` does not find them — that command takes snapshot names.
`zfs get -r -t snapshot userrefs <dataset>` is the query that works.

### Do not run sanoid by hand while its timer is enabled

Sanoid serialises on `/var/run/sanoid/sanoid_pruning.lock`. A run that finds a valid
lock held by another process exits early and silently, which looks exactly like a run
that decided there was nothing to do. Worse, the staleness check compares the
lockfile's recorded command against `ps -p <pid> -o args=`, and when that comparison
fails it *unlinks the lock* — so two concurrent runs can destroy each other, one
exiting `RC=2` with `No valid lockfile found`.

This cost a long detour: a manual prune and the hourly timer fought, the timer's runs
exited in fifteen seconds each, and the fifteen-second exits were misread as sanoid
refusing to prune at all. Let the timer do the work, or stop it first.

### Why the datasets diverged: a reused VMID

Worth recording, because the names actively mislead. The offsite
`data/vm-911-*` datasets are **not** old copies of talos-worker-1. They belong to a
different guest that held VMID 911 before worker-1 existed.

| | Diverged copy | Source today |
|---|---|---|
| `vm-911-disk-0` volsize | **100 G** (80.5 G used) | **1 M** (372 K) |
| `vm-911-disk-1` volsize | **1 T** (543 G used) | **100 G** (23.7 G) |
| `vm-911-disk-2` volsize | 100 G (74.6 K) | 100 G (23.0 G) |

`usedbysnapshots` is 234 K on the 1 T volume, so essentially all 543 G is live data
in one snapshot rather than churn.

The partition tables settle what each disk was, without inference:
`vm-911-disk-0-diverged` carries Talos's boot layout (EFI / BIOS / BOOT / META /
STATE / EPHEMERAL), and `vm-911-disk-1-diverged` is a single partition spanning the
whole 1024 G — the OpenEBS volume. So the January guest ran boot on `disk-0` and its
PV data on a **1 TB** `disk-1`, where today's worker-1 runs boot on `disk-1` and PV
data on a 100 G `disk-2`. Those 543 G are **Kubernetes PVC data**, not media, and a
1.02x compressratio only says the content was already compressed.

The sequence: a guest is created at VMID 911 around 13 January with a 1 TB disk;
`syncoid-vulcanus-data` fails for the first time on the 14th, on a 477 GB full send
of `vm-911-disk-1` that dies mid-stream; the last snapshot is the 16th and the guest
goes away. Terraform then creates the real worker-1 on **2026-04-06**, reusing VMID
911 with 100 G / 100 G — `git log -S911 terraform/main.tf` shows that as the only
commit ever to mention it.

So the seven-month outage was one oversized initial send that failed, and the
orphaned datasets were then camouflaged by the VMID reuse. Nothing in them relates
to the node running today, and the mismatched `volsize` is a second reason syncoid
could not reconcile them beyond the absent common snapshot.

### Repairing the five diverged datasets

syncoid refuses to replicate into a target with no matching snapshot, correctly — it
would have to destroy the target to proceed. The repair sets the stale copy aside
rather than destroying it, so a copy exists throughout:

| Dataset under `rpool/foreign-backups/vulcanus/data/` | Offsite as of |
|---|---|
| `subvol-105-disk-0` (fileserver LXC rootfs) | 2026-05-26 |
| `vm-910-disk-2` | 2026-01-12 |
| `vm-911-disk-0` | 2026-01-16 |
| `vm-911-disk-1` | 2026-01-16 |
| `vm-911-disk-2` | 2026-01-16 |

Per dataset, on mini-nas:

1. `zfs rename <target> <target>-diverged`
2. Let the hourly timer fire, or run the syncoid unit by hand. It does a full send.
3. Confirm the new target has a snapshot from today.
4. `zfs destroy -r <target>-diverged`

Total re-seed is ~30 GB, not the ~120 GB the disk sizes suggest, because `referenced`
is far below `used` on these zvols.

Two things to expect. `-diverged` datasets have no source counterpart, so syncoid
ignores them — but `zfs-replication-freshness` will report them as stale until step 4,
which is correct and self-resolving. And the source-list comparison in that check is
what would have caught this class of failure originally: a dataset that is *missing*
rather than stale has no old snapshot to look wrong.

### What the repair found, 2026-09-03

The five datasets were re-seeded and the diverged copies destroyed, freeing ~624 GB.
`syncoid-vulcanus-data` now exits 0.

**The diverged `vm-911-*` datasets were not worker-1's.** A guest held VMID 911
before worker-1 was created on 2026-04-06, with a Talos boot disk on `disk-0` and a
**1 TB** OpenEBS volume on `disk-1` — where today's worker-1 runs boot on `disk-1`
and a 100 GB OpenEBS volume on `disk-2`. Confirmed from partition tables, not
inferred: `disk-0` carried Talos's EFI/BIOS/BOOT/META/STATE/EPHEMERAL layout, and
`disk-1` a single partition spanning the whole volume.

Its 543 GB held a January 2026 copy of the cluster's PVC data, of which **418 GB was
`pvc-b52710af` — the Loki volume that had already been deliberately deleted.** The
audit that opened this project recorded that deletion reclaiming 452 GB of chunks
retention had never removed; this was the state before it. Roughly 85 GB more was
derived or regenerable (photo thumbnails, Prometheus TSDB, borgmatic's cache), and
about 10 GB was genuine application state — the databases, configs and identity
material. Destroyed outright on the user's call: eight months stale, superseded by
K8up within weeks.

**Two lessons worth keeping.** The oldest surviving snapshot says nothing about when
a dataset was created — sanoid's 30-day retention meant January snapshots on a
filesystem dating to 2022, which was read here as a January-created guest. And a
`compressratio` of 1.02x says only that content is already compressed; it was read as
media when it was Loki chunks.

**syncoid never removes datasets from the target.** Every guest ever deleted on
vulcanus leaves its replica behind: `vm-107-disk-1`, `vm-200-disk-0/1` and
`vm-901-disk-0` have no source counterpart at all (~8.3 GB), and nothing will ever
clean them up. Phase 1's work, alongside `vm-100-disk-0` (235 GB, the stopped
rancheros guest, which *is* still replicated because it still exists at source).

### Documentation written

[`docs/backups.md`](../docs/backups.md) — the layers and the pull-not-push property,
retention on both hosts and why the offsite copy keeps more dailies, the eight checks
and the three rules shaping them, scrub scheduling and why the jitter is 15 minutes,
the capacity model and purchase equation, and the operational notes. Row added to
[`docs/README.md`](../docs/README.md).

## Phase 1 — reclaim, and settle what retains a deleted guest — **done 2026-09-18**

Destroyed what had accumulated, and put the retention of a deleted guest on the one
layer that actually holds it. What follows is what happened, not what was planned;
where the two differ the difference is called out, because Phases 2-6 read back into
this section.

**The borg tree was inspected, not deleted** — Phase 2b still owns that.
`rpool/backups/borg` holds 2.14 TiB in six repositories under `vulcanus-borgmatic/`
(`audio`, `games`, `photos`, `rancheros`, `syncthing`, `video`), none written since
December 2022.

### What was destroyed

On mini-nas, five orphaned datasets with no source counterpart — **the spec had four**.
`vm-911-disk-3` was missed: guest 911 carries disks 0-2, so the fourth is an orphan
like the rest. Identified from their partition tables, read without mounting:

| Dataset | Size | What it was |
|---|---|---|
| `vm-901-disk-0` | 6.60 G | a Talos node — EFI/BIOS/BOOT/META/STATE/EPHEMERAL, the same layout as the live `vm-900-disk-0` |
| `vm-200-disk-1` | 1.72 G | `cdrom-test` — EFI + ext4 + swap |
| `vm-200-disk-0` | 144 K | its efidisk |
| `vm-107-disk-1` | 74.6 K | a 10 G zvol with no partition table, never formatted |
| `vm-911-disk-3` | 123 K | a 1 M efidisk stub |

`vm-901-disk-0` was the last copy anywhere — no `vm/901` PBS group exists. Destroyed
regardless, because a Talos node holds no unique state: `terraform/modules/talos/`
plus `talosctl` rebuild it, which is why the failure-domain table gives guests two
copies rather than three.

On vulcanus, guests 100, 101 and 106, and `rpool/rancheros` (40.1 G of
`docker-vulcanus/` from December 2020, 92 snapshots). Then their three replicas on
mini-nas.

**Two things the destroy table did not have.** Guest 100 carried a second disk,
`scsi1: rancheros:vm-100-disk-0`, so `qm destroy` freed both. And `rpool/rancheros`
was a *registered PVE storage* — `pvesm remove rancheros` was needed as well as the
destroy, or PVE keeps a storage entry pointing at a pool that is gone. That
registration was made by hand and never appeared in `ansible/zfs.yaml` with the
others, which is why nothing in the repo removed it.

In the cluster: the four `traefik*` PVCs, and **seven** orphaned hostpath directories
on worker-0 where the spec said three — 23.7 GiB of stale PhotoPrism storage (20.5 GiB
of it regenerable thumbnail `cache/`), a second 825 M PhotoPrism store, two MariaDB
data directories at 426 M and 136 M, and three 1 M `config/db/logs` skeletons from
January 2024. Plus `pvc-b52710af`, a **Released** PV for `infrastructure/storage-loki-0`
whose directory was already gone — an object holding nothing.

Checked before deleting: the live PhotoPrism store has an `albums/album/` directory the
orphans lack, and 26,299 sidecars against their 14,811. User-created albums live in
`album/`; the orphans held only the `folder`/`moment`/`month`/`state` groupings
PhotoPrism regenerates from its index. Nothing unique was stranded.

### Guest 100 was backed up first, changing the numbers

The spec's plan was to let guest 100 go entirely — it had no PBS group, being excluded
from the job, so destroying it removed every current copy in one step. **The user chose
otherwise**: a one-off `vzdump 100 --storage pbs --mode stop` before the destroy. It
took 2h25m, transferred 512 GiB, and cost 141 GiB after dedup reused 323.61 GiB (63%).

That changes Phase 1's arithmetic permanently, and a later session reading the old
numbers would misread the result:

- **The spec predicted reports 5 → 4 → 1.** Guest 100 was to "leave the list entirely,
  having neither scope nor a group".
- **What happens is 5 → 5 → 2.** With a group, `vm/100` becomes a frozen group like any
  other retired guest and stays a standing report — permanently, since keeping the copy
  is the point.

The two remaining reports are `vm/100` (frozen, deliberate) and `vm/107` (an empty
group, live and excluded).

### The vzdump exclude list is now `107`

Trimmed after the destroys, never before: trimming first would have put three
still-existing dead VMs *in scope*, which the 04:00 run would have backed up and the
classifier would have correctly called a failure. `107` is PBS itself, whose datastore
lives on `rpool`, so backing it up is circular — the only standing reason to exclude a
guest. Recorded in [`vzdump-job-in-terraform.md`](vzdump-job-in-terraform.md), which
carries the job's full state until the provider gap closes.

### The PBS freshness assertion was built here, not in Phase 6

It had to be. Phase 1's verification is the report count moving, and the classifier
could not be watched to move if it did not exist. Phase 6 inherits a deployed check
rather than one to build.

`ansible/files/pbs_freshness.py`, installed to `/usr/local/bin/pbs-freshness` by
[`ansible/backup-monitoring.yaml`](../ansible/backup-monitoring.yaml), on a timer at
**08:00 local** — not the 06:30 the schedule matrix first carried. At 06:30 a slow
vzdump left eight minutes of margin against the 2h22m run of 2026-08-19; 08:00 gives
four hours. Reads scope from `/etc/pve/jobs.cfg`, the live guest list from
`/etc/pve/.vmlist`, and the group list from the datastore. Python rather than a Jinja
template, under `ansible/files/` rather than `tools/`, because `tools/` is for things a
person runs by hand — and `copy:` rather than `template:` means the deployed file is
the file its 22 tests run against.

**Three mechanics the spec left open, settled by measurement:**

- The config blob is `qemu-server.conf.blob` / `pct.conf.blob`, and it reads **without
  mounting and without a single-file restore**: `proxmox-backup-client restore
  <group>/<iso> qemu-server.conf.blob -` prints it to stdout in 0.24 s.
- **Nothing is encrypted.** Every file in every group reports `crypt-mode: "none"`, so
  the check runs unattended on the datastore password at
  `/etc/pve/priv/storage/pbs.pw`.
- **Containers have no `smbios1`.** The reuse comparison uses `net0`'s `hwaddr` for
  `ct/<vmid>`, which PVE generates per container and which survives a restore of the
  same container.

**Two states the spec did not anticipate:**

- **`vm/107` is an empty group, not an absent one** — `count=0`, no files,
  `snapshots vm/107` returns `[]`. The spec's 2026-09-18 note had 100 and 107 both with
  "no group at all"; only 100 did. An empty group must not read as coverage, and must
  not crash the age comparison.
- A PVE config section's keys arrive on the lines *after* its header, so the parser
  returns a list rather than generating one. A generator hands out each dict while it is
  still empty, and a caller filtering on `enabled` reads every section as unset.

### What the numbers did

| Measure | Before | After |
|---|---|---|
| vulcanus `rpool` | 28.3 T alloc, 55% | **27.7 T, 54%** |
| mini-nas `rpool` | 14.8 T alloc, 77% | **14.1 T, 73%** |
| worker-0 `/dev/vdb1` (OpenEBS) | — | **25.1 GiB freed**, now 9% of 1023.9 G |
| `zfs-replication-freshness` | red every run since inception | **`Result=success`** |
| PBS reports / failures | 5 / 0 | **2 / 0** |

**`zpool list` alone reports these destroys as no change.** ZFS frees large datasets
asynchronously: immediately after `qm destroy` the pool read identically, and
`zpool get freeing` showed 214 G draining to zero over two minutes before `allocated`
moved. Watch `freeing`, not just `allocated`, or a correct destroy looks like a failed
one.

The mini-nas figure keeps falling after the destroys as sanoid prunes snapshots that
are no longer referenced — 77% → 75% immediately, 73% within the hour.

### What this phase did *not* do

- `pvc-b52710af` aside, no other Released or unbound PV was touched.
- The borg tree stands; Phase 2b deletes it after a restore proves the replacement.
- **A backup that is still running already creates its PBS group**, timestamped at
  start. The check therefore reads an in-flight backup as fresh coverage. A job that
  started and then died every night would look healthy to `pbs-freshness` alone; the
  vzdump check enumerated in the notification budget is what closes that, and does not
  exist yet. Named here rather than assumed.

## Phase 2 — application backups

Opened 2026-09-20; built and deployed by 2026-09-22. What it built is described in
[`docs/backups.md`](../docs/backups.md) — the restic repository, Kubernetes volumes
and databases, retention, restoring, coverage — and [`docs/nixos.md`](../docs/nixos.md).
This section keeps what those do not: what measuring overturned, the figures, the
wrong turns, and what each application's own guidance says.

### What measuring overturned

The plan this section carried was written before anything was measured. Each of
these changed what was built.

1. **K8up's opt-out default put ~16 TiB of SMB volumes in scope.** All twelve SMB
   claims qualified, `borg-backups-pvc` among them. The plan named only the
   regenerable volumes. *Nothing could schedule a backup until the exclusions were
   live*, so they landed first.
2. **The `backupcommand` plan did not work for SQLite.** headscale and rustdesk have
   no shell, linkding has only Python, stump has nothing; and `.backup` needs a
   seekable destination, so it cannot stream to stdin regardless.
3. **The WAL risk was overstated for the apps named.** For headscale, rustdesk and
   syncthing the irreplaceable material is a key file, safe to copy if the Job can
   read it. The databases that need a consistent dump are linkding's, Plex's, and
   Grafana's, a fifteenth SQLite database holding an admin password no longer
   recoverable from SOPS.
4. **Schedules cannot live under `kubernetes/infrastructure/`**, whose `namespace:`
   transformer would have put all three in `infrastructure`: that namespace backed
   up three times, `apps` never, Flux Ready throughout.
5. **`pvesh get /cluster/nextid` returned 100**, the guest Phase 1 destroyed and
   whose PBS group is kept on purpose. A VMID is checked against the datastore's
   groups, not `nextid`.
6. **K8up's default uid cannot read most of the volumes** (k8up#1032). Mounting all
   22 in-scope claims read-only and walking them as 65532:0, 13 held something it
   could not read: syncthing's whole volume, headscale's `noise_private.key`, both
   PhotoPrism signing keys and its own SQL backups, every database data directory,
   `grafana.db`, Plex's `.LocalAdminToken`, headplane's `tailscaled.state`,
   RustDesk's config. K8up counts restic's exit 3 as success, so the `apps` Schedule
   would have reported Succeeded every night without them. Hence `runAsUser: 0` on
   every Schedule and Restore, and never `fsGroup`.
7. **Sizes come from `talosctl usage`**, not `kubelet_volume_stats_used_bytes`, which
   for hostpath reports the underlying filesystem: every worker-0 PVC read 90.31 GiB.

**Upstream details worth not rediscovering.** K8up's REST password field is
`passwordSecretReg`, an upstream typo; `...Ref` is silently ignored. rest-server's
append-only mode still allows deleting locks, so restic locks normally. K8up's chart
tag and operator version differ: chart 4.10.0 deploys operator v2.16.0, and the
source findings here are read at `v2.16.0`. K8up names a dump
`/<namespace>-<container><extension>`: read from source, upstream's e2e test and
k8up#1068, since the documentation does not say. k8up#910 (a failed
`backupcommand` reported Succeeded) is fixed in the running operator; its test
never checked whether a partial snapshot survives, which is why every dump goes to a
file first.

**A Pushgateway would keep one item per namespace.** K8up pushes with `POST` under a
grouping key of job, namespace and cluster, and a `POST` replaces every metric of the
same name under that key. Reproduced against Pushgateway 1.11.3: a push for linkding
removed headscale's series outright.

### Every PVC, and its route

22 in scope and 18 excluded, the live split on 2026-09-21. "Volume" alone means the
file copy suffices.

| Namespace | PVC | Route | Why |
|---|---|---|---|
| apps | `headscale-data` | Volume + PreBackupPod | `noise_private.key` is a file; the dump is insurance for the node table |
| apps | `linkding-data` | Volume + PreBackupPod | the rows are the whole value |
| apps | `plex-config` | Volume + PreBackupPod | watch state; Plex's own 3-day copies ride the volume too |
| apps | `pinepods-database` | Volume + Annotation | PostgreSQL; the file copy is only crash-consistent |
| apps | `photoprism-database`, `salamander-database` | Volume + Annotation | MariaDB; same |
| apps | `rustdesk-data` | Volume | `id_ed25519` is a file; the peer table regenerates |
| apps | `syncthing-data` | Volume | the device ID *is* `key.pem` |
| apps | `photoprism-data`, `salamander-data` | Volume | whole, per the decision above |
| apps | `rclone-dropbox-config` | Volume | holds the OAuth token |
| apps | `stump-config`, `mumble-data`, `speedtest-tracker`, `youtube-dl`, `headplane-data`, `beets-library`, `beets-flask-config`, `filebot` | Volume | journal-mode SQLite or plain files |
| apps | `pinepods-backups` | Volume | empty, made for a `pg_dump` that never ran |
| automatic-ripping-machine | `automatic-ripping-machine-pvc` | Volume | the canary, on worker-1 |
| infrastructure | `kube-prometheus-grafana` | Volume + PreBackupPod | its admin password is not in SOPS |
| apps | the twelve SMB claims | Excluded | the ZFS layer and the repo host cover them |
| apps | `borgmatic-data`, `rclone-dropbox-bisync-cache`, `pinepods-valkey` | Excluded | regenerable |
| infrastructure | Prometheus, Alertmanager, VictoriaLogs | Excluded | regenerable |

### Figures

| What | Measured |
|---|---|
| colmena spike, 2026-09-20 | a switch moved a throwaway LXC from `.199` to `.108`; "succeeds but applies nothing" did not reproduce |
| first mass-file run, 2026-09-21 | 296.58 GiB, 109,969 files, 2 h 09 m; 45.1 GiB duplicate across the three datasets; 247.59 GiB stored, compression saving 1.5% |
| first `apps` run, by hand, 2026-09-21 | 20 PVCs, 680,901 files, 66.1 GB read, 59.4 GB added, 99 min, 0 errors; dumps Job 39 s |
| first scheduled night, 2026-09-22 | `apps` volume Job 78 s, 68 MB added, 0 errors |
| a second dumps run, minutes after the first | added 308 B (headscale) to 953 KB (pinepods' 18.4 MB) |
| etcd WAL fsync p99, mean / peak, against a control | mass-file run 0.651 / 1.912 s (0.245 / 0.402 s); `apps` run 0.368 / 1.745 s (0.241 s). `etcdHighFsyncDurations` went critical on the first only; no container restarted. vzdump nights reach 3.8-8.7 s |
| coverage check | 253 MiB peak reading salamander's dump back; 11 s live, 15 s scheduled |
| first replication to mini-nas, 2026-09-22 | 302.9 GB in about 4 h at 16-28 MB/s; `restic check` clean on a read-only snapshot mount there |
| first monthly prune, 2026-10-01 | 66 s, 202 MiB repacked, 165 MiB memory peak |
| dump restores, 2026-10-12 | see below |

**The dumps restore — 2026-10-12**, the 19:00 UTC dumps of 2026-10-11, each streamed
from the repository host into a scratch pod on the live image, in a throwaway
namespace:

| Dump | Loaded into | Result |
|---|---|---|
| photoprism, 74 MB | mariadb 10.11.19 | exit 0 in 85 s; 39 tables and 270,341 rows, matching live row for row bar one `errors` row written since |
| salamander, 203 MB | mariadb 10.11.19 | exit 0 in 127 s; 39 tables and 170,317 rows, the same |
| pinepods, 18 MB | postgres 18.6, `ON_ERROR_STOP` | exit 0 in 34 s; 42 tables, matching live bar two episodes added since |
| headscale, linkding, soft-serve, grafana | sqlite3 3.53.4 | bytes equal to the snapshot's, `integrity_check` `ok`, 8 to 95 tables each |
| plex, 88.5 MB | `Plex SQLite`, from Plex's image | bytes equal, `integrity_check` `ok`. Stock sqlite3 cannot check it: no `icu_root` collation |

Each check was shown to fail. Four pages of the linkding copy overwritten at random
fail `integrity_check`; the photoprism dump cut at 30 of 74 MB stops mariadb with a
syntax error at the cut; the pinepods dump cut at 9 of 18 MB stops `psql` with exit 3.

Every snapshot carries restic's `summary`, which the coverage check's size verdicts
need. The operator's `k8up_jobs_failed_counter` has no series until a first failure.

### Escrow, including what Phase 0 left open — done 2026-09-21

Escrowed by the user into the password manager, before the repository held data:

- **The restic repository passphrase**, with the REST password beside it as a
  convenience: that protects no data and can be regenerated.
- **The Talos machine secrets**, closing Phase 0's open item. Taken from the running
  control plane with talosctl's own converter, into `/dev/shm`:

  ```
  talosctl -n 192.168.0.190 get machineconfig v1alpha1 -o jsonpath='{.spec}' \
    | talosctl gen secrets --from-controlplane-config /dev/stdin -o /dev/shm/talos-secrets.yaml
  ```

  All 14 fields matched tfstate's `talos_machine_secrets.main` by hash; the one field
  only in state, `secretboxencryptionsecret`, is empty.
- **Not the `.talosconfig`.** Its certificate is signed by the OS CA in the secrets,
  and a talosconfig minted from them with `talosctl gen config --with-secrets` was
  accepted by the control plane. It expires 2026-11-07, while the CAs run to
  2032-11-03; see [`talosconfig-renewal.md`](talosconfig-renewal.md).

With the secrets escrowed, guest images' "reconstructible from `terraform` plus
`talosctl`" — the failure-domain table's reason for two copies, not three — holds.

**Why escrow a passphrase SOPS already holds.** The Flux age key, escrowed in
Phase 0, already opened `kubernetes/k8up/secret-*.sops.yaml`. The direct entry is
the shorter chain, and independent of recipient drift — which happened here: the
NixOS rule first left its secrets file the one in 33 the Flux key could not open.
Phase 5's acceptance test, a restore from the passphrase alone, depends on it.

**Where the credentials could have leaked.** Generated with `secrets.token_urlsafe`,
and bcrypt via `htpasswd -nbB`, in one shell under `umask 077`, written straight into
the files and encrypted seconds later; only lengths were printed. Five generations,
only the fifth live. Every transcript, the scratch space, memory and every git object
were then searched from the container against `/run/secrets`: 0 hits in 144 MB, with
positive controls. The residue is freed blocks on `rpool/home`, which is unencrypted.
**Generate any future secret into `/dev/shm`.**

### Wrong turns

- **The first append-only check could not fail.** `| tail` hid the exit code and the
  flags were invalid, so both paths failed for one unrelated reason and it read as a
  pass.
- **A rationale overstated.** Commit `830e2723` justified file-first dumps because a
  dump dying halfway "could otherwise be saved as a plausible-looking snapshot". From
  the source, restic almost certainly loses that race. The file comes first anyway,
  for reasons that do not depend on it; see docs.
- **salamander did not roll on the first deploy.** Its HelmRelease takes the chart
  from Git under `reconcileStrategy: ChartVersion`, so template changes without a
  `Chart.yaml` bump are never packaged. This chart has hit it twice.
- **The ARM canary compared with `-mindepth 1`**, so it could not see that a restore
  leaves the claim's root as `0777 root:root`. syncthing's restore found it.
- **The first identity comparison printed nothing** through a diff-script bug, and
  was rerun rather than read as a pass.
- **`/proc/<pid>/io` was used to monitor the first mass-file run** and overshot,
  since restic reads its own packs back. ETAs built on it were wrong both ways.
- **`ping` raised on a malformed URL**, outside its `try`. Found by the first live
  run; fixed test-first.
- **`kubectl auth can-i get pods/log` answered "no"**, being parsed as a pod named
  `log`. `--subresource=log` answers "yes".
- **The first SQLite restores proved nothing.** Piped into `kubectl exec -i ... sh -c
  "cat > f"`, each landed as an empty file with every exit status 0, and an empty file
  passes `integrity_check`. The same pipe kept every byte when the remote shell did
  more after `cat`. Rerun comparing each file's size with the snapshot's.
- **The first MariaDB load met the image's temporary init server**, which answered
  the readiness ping and then restarted: "Server has gone away" at line 1, nothing
  loaded. A cut-dump control was also lost once to a tailnet connection reset, and
  rerun rather than counted.
- **Escrow was first argued as necessary.** It was not; it is shorter and
  drift-proof, which is enough.
- **The exclusion annotation broke VictoriaLogs' chart.** An annotation on a
  Helm-owned StatefulSet's `volumeClaimTemplates` cannot be applied to the live
  object, so every upgrade failed and rolled back, and the HelmRelease Stalled on
  chart 0.13.9. Found in review of PR #13 and fixed 2026-10-12 by deleting the
  StatefulSet with `--cascade=orphan`; Helm recreated it, upgraded to 0.13.10, and
  logs kept flowing with their history intact.

### Each application's own backup guidance — read 2026-10-12

Read once per stateful application, to see what its designers know that this
design might not. Where we already agree, nothing is noted.

- **PhotoPrism** ([backups](https://docs.photoprism.app/user-guide/backups/),
  [restore](https://docs.photoprism.app/user-guide/backups/restore/)): back up the
  originals, the index, and the storage folder. Restore the folders, then the index
  with `photoprism restore -i -f`, which takes the newest of its own dumps in
  `storage/backup/mysql`; those ride the data volume. Without an index, a rescan
  plus the YAML sidecars and album files recovers metadata and albums. A restored
  index brings back accounts and passwords as of the dump. It advises testing a dump
  on a separate instance before replacing a live index — done on 2026-10-12.
  Originals are on the photos share, covered by ZFS and the mass-file backup.
- **Plex** ([backing up](https://support.plex.tv/articles/201539237-backing-up-plex-media-server-data/),
  [repair](https://support.plex.tv/articles/repair-a-corrupted-database),
  [restoring a scheduled backup](https://support.plex.tv/articles/202485658-restore-a-database-backed-up-via-scheduled-tasks/)):
  the whole data directory, `Cache` excludable; on Linux `Preferences.xml` is inside
  it. **A restore replaces `library.db` and `library.blobs.db` as a pair**, owned by
  plex. Our dump takes only `library.db`; `library.blobs.db` (140 MB, WAL, 14,738
  blobs) exists only as a live file copy in the volume snapshot, and as Plex's own
  dated pairs, the newest up to three days old. Only Plex's SQLite can repair or
  fully check either file, which we already use.
- **linkding** ([backups](https://linkding.link/backups)): recommends its
  `full_backup` zip, and warns that copying `db.sqlite3` is not transaction-safe.
  Our `.backup` copy is transaction-safe, matching its deprecated `backup` command.
  **The database references HTML snapshots in `assets/`**, so a restore should pair
  a dump with the volume snapshot from the same full run. Favicons regenerate.
- **headscale** ([upgrade](https://headscale.net/stable/setup/upgrade/)): config,
  data directory and database; migrations run on upgrade and are one-way, so back up
  before upgrading. **Upgrades must step through every minor version.** So a dump
  restores with the headscale version that wrote it, from git history, then steps
  forward. Image automation moves one minor per release, and dumps are at most six
  hours old at any upgrade.
- **Grafana** ([back up](https://grafana.com/docs/grafana/latest/administration/back-up-grafana/)):
  configuration, plugin data and the database. It advises stopping Grafana before
  copying the SQLite file; `.backup` makes that unnecessary. Configuration is in git,
  plugins on the volume.
- **PinePods** ([server backup and restore](https://pinepods.online/docs/tutorial-basics/Backup-Restore),
  [Postgres major upgrade](https://pinepods.online/docs/Troubleshooting/PostgresMajorUpgrade)):
  a manual SQL download from Settings; its restore accepts only its own files, so ours
  restores through `psql`, as proven. A Postgres data directory is bound to its major
  version; the image policy is already minor-only for that reason. A plain `pg_dump`
  loads into any newer major, so the dump is also the route across one.

## Phase 2b — delete the borg tree — **done 2026-10-13**

### Why borgmatic never produced a backup

Two faults, either sufficient. Its config named `/mnt/repositories/<name>`, one
directory above where the six repositories live (`vulcanus-borgmatic/<name>`), so
every run since January 2023 logged "Repository ... does not exist". And the
passphrase in the SOPS Secret `borg`, unchanged since the commit that added borgmatic
on 2023-01-20, opens none of them, empty passphrase included: the repositories were
written in 2022 by the RancherOS-era borgmatic, under an older passphrase. The user
found that one in their own records on the second attempt; the first candidate was
byte-identical to the Secret.

### What the repositories held

Listed with borg 1.4.0 from the borgmatic pod, `--bypass-lock`, read-only.

| Repo | Archives | Span | Deduplicated | Newest archive, logical |
|---|---|---|---|---|
| audio | 39 | 2022-06-21 – 12-10 | 440.9 GB | 453.6 GB |
| games | 42 | 2022-06-22 – 12-10 | 45.7 GB | 60.8 GB |
| photos | 44 | 2022-06-19 – 12-10 | 146.4 GB | 150.6 GB |
| rancheros | 44 | 2022-06-19 – 12-10 | 81.0 GB | 114.4 GB |
| syncthing | 41 | 2022-07-01 – 12-10 | 19.5 GB | 25.9 GB |
| video | 37 | 2022-06-21 – 12-09 | — | 1,842 GB |

`borg info` on `video` died with `ArchiveTAMInvalid` — borg 1.4 refusing an archive
written without a TAM by an older borg. `borg list` worked on all six.

### Compared against live storage

Each regular file in each newest archive, matched first by path under its live root,
then by name and size anywhere under `rpool/storage`, then by name alone. The second
pass needed a one-off index of all 1.93 million live files (`find -printf "%s\t%f"`,
about 30 minutes); walking the tree once per repository did not finish in 30.

| Repo | By path | Moved | Renamed or retagged | No match |
|---|---|---|---|---|
| games | 60.8 GB | — | — | 0 |
| photos | 150.6 GB | — | — | 0 |
| video | 1,415 GB | 416 GB | — | 10.3 GB |
| audio | 212.5 GB | 169.9 GB | 58.0 GB | 13.2 GB |
| syncthing | 24.1 GB | 1.5 GB | — | 0.2 GB |

The unmatched remainder: a Princess Mononoke ISO and m4v where live has a newer
`.mkv`, one YouTube download, audiobook folders that exist live under beets'
naming at similar sizes, Syncthing's own index database and a handful of ebooks.
The user judged none of it worth keeping.

### rancheros, and Fallgrane

The user's one requirement from `rancheros`: *"There's only one thing from rancheros
that needs to be preserved: the "fallgrane" minecraft server and all it's consituent
files."* Nothing else in it matters.

The tree is the old `docker-vulcanus` directory. Fallgrane appears in it twice:
`minecraft/worlds/fallgrane*` (with `fallgrane_2*`) and an older copy under
`minecraft.backup.20200120/minecraft/Fallgrane*`. Sixteen archives sampled from June
to August 2022, and the newest, all carry the same 1,347 Fallgrane files outside the
dynmap tiles, newest modification 2020-03-18; the first archive carries none.

`rpool/storage/media/backups/docker-configs/minecraft` was already a live copy of the
2020 layout, matching by size except six `level.dat`/`level.dat_old` files. **The
newer `worlds/fallgrane*` — 684 files, 0.8 GB — existed only in borg.**

Extracted from the 2022-12-10 archive to
`rpool/storage/media/backups/docker-configs/minecraft.borg-2022-12-10/`, beside the
older copy rather than over it: all of `minecraft/` except the 9.7 GB of dynmap tiles,
which were already live with identical sizes, plus the archive's six differing
`level.dat*`. 29,588 of 29,588 files present at the archived size; borg authenticates
each chunk as it decrypts. 740 MB on disk, owned `1100:1100` like its neighbour. Seen
in `autosnap_2026-10-13_15:00:01_hourly` on vulcanus, and the same snapshot on
mini-nas holds all 29,588 files.

**PBS `vm/100` holds the same tree, later.** The RancherOS guest's 2026-09-18 backup
has it on `scsi0` at `/home/rancher/docker-vulcanus`; `scsi1` carries no filesystem.
Mapped with `proxmox-backup-client map`, mounted `ro,noload`: 123.9 GB and 3,514,836
files against borg's 114.4 GB and 3,506,985. 99.6% of borg's files exist there; the
13,257 that do not (2.55 GB) are churn from apps that ran on after December 2022 —
Plex metadata, Airsonic's Postgres WAL and stats, rotated logs, podgrab's rolling
backups — none under `minecraft`. Its `worlds/` and all 380 `fallgrane_2` region
files match the extracted copy by size.

### Removing it

The dataset was held in four places, each released in order: the borgmatic pod's SMB
session (Flux prune on deploy), the `[borg]` share (the fileserver playbook,
`smbcontrol reload-config`, no restart), the bind mount inside LXC 105, and the same
mount staged in the `lxc-start` process's namespace.

- **Proxmox cannot remove a mount point from a running container.**
  `vmconfig_hotplug_pending` in `PVE/LXC/Config.pm` skips `mpN` deletions, leaving them
  pending until the next start. `tofu apply` therefore left `delete mp3` pending, and
  the plan's apparent `mp3`-becomes-books renumbering did not materialise: `books`
  stayed `mp4`.
- **Unmounting inside the container is not enough.** PVE stages each mount point at
  `/var/lib/lxc/.pve-staged-mounts/mpN` in the `lxc-start` namespace and bind-mounts it
  into the rootfs from there, so `zfs destroy` stayed busy until both were unmounted
  with `nsenter -t <lxc-start pid> -m umount`. Finding it took a grep for the dataset
  name across every `/proc/*/mountinfo`.
- **`tofu apply` was targeted at `proxmox_lxc.fileserver`.** The full plan also
  replaced `local_sensitive_file.kubeconfig`, its content having drifted — unrelated to
  this phase and the file `kubectl` reads.
- `pvesm remove backups_borg` dropped the storage entry. Nothing held a snapshot hold,
  and mini-nas never had a replica.

`rpool/backups` 2.44 T → 305 G, `restic` alone. Pool allocation was still freeing
when this was written — 1.67 T left, `zfs list rpool` 13.9 → 13.4 T, 56% → 54% — and
converges on the ~11.8 T the capacity model already assumed, since borg was never
replicated and never counted against mini-nas.

### Wrong turns

- **`| tail` hid the destroy's failure.** `zfs destroy -r -v ... | tail -3` showed the
  "will destroy" lines and dropped "dataset is busy"; only the follow-up `zfs list`
  caught it. The same mistake as Phase 2's append-only check, repeated a phase later.
- **The first Fallgrane extraction went through the SMB share** and silently lost three
  of each pair of files differing only in case (`fallgrane.pending` and
  `Fallgrane.pending`). Caught by checking every listed file's size afterwards;
  redone as `borg export-tar` onto the share and `tar -x` on the host into ZFS. A
  restore that lands through SMB is not faithful, which the Phase 6 drill must not
  repeat.
- **Streaming the PBS subtree as a tar to size it** ran at ~5 MB/s, six hours for
  114 GB. Mapping the image and running `du` read only metadata.
- **`pkill -f "bash pbs-size.sh"` matched its own SSH command line** and killed the
  session. Scope a `pkill -f` pattern so it cannot match the command issuing it.
- **`find -xdev` from `/rpool/storage` returned nothing**, the files being in child
  datasets.

## Phase 3 — drop the OpenEBS disks from vzdump

Opened 2026-10-16. PR #17 had closed at the 2b boundary on 10-13, so this is a fresh
`backups` branch from `main`.

### The baseline, measured 2026-10-16

Per-VM figures from `/var/log/pve/tasks`; the job has run `--exclude 107
--performance max-workers=2` throughout. etcd figures are WAL fsync p99 over
10:00-11:30 UTC, the window evaluated at 11:30.

| night | VM 910 | 910 virtio0 / virtio1 dirty | VM 911 | job | > 0.5 s | peak | failed proposals |
|---|---|---|---|---|---|---|---|
| 10-03 | 4m59s | 2.6 / 3.7 GiB | 2m19s | 26m | 1m | 0.50 s | 0 |
| 10-04 | 6m11s | 2.9 / 5.6 GiB | 2m27s | 27m | 9m | 0.60 s | 0 |
| 10-05 | 5m34s | 2.9 / 4.1 GiB | 2m25s | 27m | 9m | 0.85 s | 0 |
| 10-06 | **49m15s** | full read, 1.10 TiB | 7m56s | 77m | **52m** | 1.00 s | 13 |
| 10-07 | 9m07s | 6.9 / 5.6 GiB | 2m45s | 30m | 10m | 0.75 s | 20 |
| 10-08 | 7m18s | 3.5 / 6.7 GiB | 2m21s | 28m | 6m | 0.85 s | 1 |
| 10-09 | **49m49s** | full read, 1.10 TiB | 8m05s | 78m | **56m** | 1.57 s | 16 |
| 10-10 | 7m06s | 3.7 / 5.7 GiB | 2m24s | 27m | 6m | 0.81 s | 3 |
| 10-11 | 7m06s | 3.9 / 4.0 GiB | 2m54s | 47m | 14m | 0.96 s | 0 |
| 10-12 | 9m10s | 5.1 / 5.7 GiB | 3m37s | 32m | 14m | 0.98 s | 0 |
| 10-13 | 14m05s | 5.4 / 14.1 GiB | 3m07s | 36m | 17m | 1.00 s | 0 |
| 10-14 | 8m56s | 5.6 / 4.4 GiB | 3m14s | 31m | 16m | 1.24 s | 17 |
| 10-15 | 9m13s | 5.4 / 6.3 GiB | 3m10s | 31m | 14m | 0.92 s | 0 |
| 10-16 | 8m42s | — | 3m24s | 31m | 16m | 0.96 s | 0 |

Worker-1's `virtio1` is 250-820 MiB dirty a night, a rounding error beside its boot
disk. On an ordinary night the OpenEBS disk is about half of VM 910's read; the large
win is the full-read nights, 1.10 TiB that becomes 100 GiB. On 10-06 and 10-09 both
workers' bitmaps were `created new` on every drive, so both QEMU processes had
restarted. For 10-09 it was the whole host: both QEMU processes and LXC 105 date from
10-09 04:26 UTC. What restarted them before 10-06 was not looked into.

The 24 h `avg_over_time` of p99, windows ending 09:50 so each holds one backup, read
0.246-0.301 s from 10-01 to 10-16: 0.267 and 0.296 for the windows holding the two
full reads. Thirty minutes of backup in 1,440 barely moves a daily mean, so this is
the net check the spec asks for, not the sensitive one; the window columns above are.

PBS datastore: 674,588,420 KiB used (643 GiB) of 2 TB, from `pvesm status`.
`rpool/proxmox_backup_server` 1.99T used, 1.61T referenced.

### What was established before applying

- **Coverage of everything on `/var/openebs`.** Every OpenEBS claim is in `apps`,
  `infrastructure` or `automatic-ripping-machine`, the three namespaces with a
  Schedule, and is either backed up or annotated out under the documented policy.
  Each `pvc-*` directory under `/var/openebs/local` on both workers matches a live PV
  (24 on worker-0, 3 on worker-1); `ndm` and `sparse` hold a few bytes each.
- **The flag applies live.** `vmconfig_update_disk` in PVE 9.2.20's
  `QemuServer.pm`, for a drive whose file is unchanged, dies `skip` only on `aio`,
  `discard`, `iothread`, `queues`, `product`, `cache`, `ssd`, `vendor` or `ro`, and
  otherwise returns 1. `backup` is not in the list, so no QEMU restart and the
  bitmaps survive. Neither VM had `backup=` set, so both were on the default of 1.
- **The plan is the flag and nothing else.** Targeted at the two workers: two
  in-place updates, `virtio1.disk.backup true -> false`. Run from the worktree with
  the main checkout's state and `TF_DATA_DIR`, `-lock=false`.
- **An untargeted apply would also touch LXC 105.** The plan wants to add `mp4`
  (books) to `proxmox_lxc.fileserver`, which already has it. Hence the targeted apply
  recommended here; the user applied untargeted instead, which queued all four
  mountpoints as pending with identical values and restarted nothing. The cause is
  under *Wrong turns*.
- **What a PBS restore does with an excluded disk**, read from the same source.
  `restore_update_config_line` writes a `backup=0` drive's line commented out;
  `restore_cleanup_oldconf` frees only the disks being restored and keeps the rest as
  `unused`. The VMA path differs — it calls `destroy_vm`, which frees every owned
  disk — but storage `pbs` takes the PBS path. Reattaching with `qm set --virtio1`
  drops the `unused` entry by itself ("remove 'unusedX' settings if we re-add a
  volume" in `write_config`); `--delete unusedN` goes through `try_deallocate_drive`
  and frees the volume, and `update_vm` processes deletes before sets.

### Two coverage-check failures, both the check misfiring

`backup-coverage` failed at 14:45 and 20:45 on 10-15 and at 14:45 on 10-16, passing
at 08:45 on 10-16 between them.

- **10-15: `apps/headscale-data-pvc: shrank from 2360120 to 242416 bytes`.** A WAL
  checkpoint. Snapshot `7bea56ab` (10-14 01:00 UTC) held a 2,233,072-byte
  `db.sqlite-wal`; `b72d72ab` a day later held 111,272 bytes of WAL and a
  `db.sqlite` grown from 94,208 to 98,304. headscale reports 8 nodes, 6 online. The
  shrink rule compares the last two snapshots, so it fired for a day and cleared
  itself. It will do this to any WAL-mode database volume.
- **10-16 14:45: the `plex-sqlite` init container `OOMKilled`, exit 137, one second
  in.** Its limit is 64 Mi and it `cp -a`s Plex's binaries and `lib`. The other runs
  that day completed the same container; why this one did not was not looked into.

Both fixed the same day, on the user's word. The shrink rule now lists both snapshots
of a volume it would fail and totals them again without `-wal`, `-shm` and
`-journal`; only a volume still halved fails, and one halved only in those files
reports. Run against the real listings, headscale's pair totals 94,280 and 98,376
bytes. The init container's limit is 256 Mi, above the 107 MiB it copies
(`Plex Media Server` 23 MiB, `lib` 84 MiB). The page-cache explanation for the OOM
is not established: at 14:45 etcd's fsync p99 was 0.26 s, worker-0's disks 11% busy
and 1.7 MiB dirty, no different from the runs that passed. The limit holds the whole
copy whatever the cause.

### Wrong turns

- **The first reattach command in `docs/backups.md` carried `--delete unused0`**, to
  tidy the entry. It would have freed the OpenEBS disk before attaching it. Caught by
  reading `update_vm` before committing.
- **LXC 105's drift was put down to `mp3`'s pending deletion**, with the spec saying
  it would clear at the next restart. It would not have. `FlattenDevicesList` in the
  provider (v3.0.2-rc07) loops `index := 0; index < len(devices)`, so slots 0, 1, 2, 4
  read back as 0, 1, 2 and books never reached state, even after an apply. The plan
  never showing `mp3` was the clue: the provider reads the API's default view, pending
  changes applied, where `mp3` is already gone and stays gone after a restart. The
  provider also builds its delete list from state alone (`processLxcDiskChanges`), and
  proxmox-api-go's `UpdateConfig` sends only what is set, so renumbering books to
  slot 3 leaves `mp4` behind until it is deleted by hand.
- **Check C was written to read returned space from `pvesm status`**, which is PBS's
  count from inside the guest. The host's raw file, without `discard`, would not have
  moved, so C would have passed on space vulcanus never got back. Caught 10-16 while
  sizing PBS #2, by setting `zfs get` on `rpool/proxmox_backup_server` beside it.

## Phase 4 — reassessed before starting, 2026-10-16

Asked whether Phase 4 could start while the mini-nas disks are still unbought. All
reads, nothing changed.

### The disk gate, against the pool as it stands

The disks are not in: mini-nas's `rpool` is still raidz1-0 and raidz1-1 at three
disks each, and `spool` is imported on the two Toshibas, empty (1.31M allocated).

| | Projected 2026-09-01 | Measured 2026-10-16 |
|---|---|---|
| mini-nas `rpool` used | 10.95 TiB (87%) | 9.89 of 12.57 TiB (79%), `zfs list` |
| `rpool/foreign-backups/vulcanus/data` | 1.44 TiB | 904 GiB, of which `vm-910-disk-1` 651G, `vm-910-disk-0` 145G, `vm-107-disk-0` 17.9G |
| PBS #2's datastore | 1.20 TiB | 643 GiB, the primary's `pvesm status` |
| peak, PBS #2 with `rpool/data` still replicated | 93% | (9.89 + 0.63) / 12.57 ≈ 84% |
| after the retirement | 79% | (10.52 − 0.87) / 12.57 ≈ 77% |

`zpool get ashift` reads 12; compression is lz4, recordsize 128K. A 16K block on
3-wide raidz1 is four data sectors over two data columns, two rows, two parity
sectors, six in all, already a multiple of two: the 2:1 ratio space accounting assumes,
so a zvol datastore costs no padding beyond its contents. lz4 gains nothing on PBS's
already-compressed chunks.

The user ruled 2026-10-16 that Phase 4 goes ahead on this, without waiting for the
disks or Phase 3's 11-16 prune.

### mini-nas's PVE

`pve-cluster`, `pvedaemon` and `pveproxy` active; `pvesh get /nodes` reports 8
CPUs, 16,640,897,024 bytes of memory, uptime ~28 days. `qm list` is empty. `ip -br
link` lists `lo`, `eth0` and `tailscale0` — no `vmbr0`, the bridge in `proxmox.nix`
matching `ens18`. `pvesm status` lists only `local`, a directory; there is no
`/etc/pve/storage.cfg`. `free -g`: 15 total, 8 used, 7 available. mini-nas's
`main.tf` carries a `proxmox_backup_server` module, commented out.

### The primary datastore's footprint on vulcanus

`rpool/proxmox_backup_server`: `usedbydataset` 1.61T, `usedbysnapshots` 385G,
`logicalused` 2.00T, `compressratio` 1.00x. `vm-107-disk-0.raw` is 2.0T apparent,
1.7T allocated by `du`. PBS's own figure, 643 GiB. `/etc/pve/qemu-server/107.conf`:
`virtio1: proxmox_backup_server:107/vm-107-disk-0.raw,backup=0,replicate=0,size=2T`,
no `discard`. sanoid applies `dailies` (`daily = 30`, autoprune) to the dataset; 30
snapshots, 09-17 to 10-16. `qm agent 107` reports the guest agent not running, so the
datastore's filesystem was not read.

The 1.61T was already in the Phase 3 baseline above; what it means, a guest
high-water mark the host never gets back, was not drawn from it until now. The user
ruled the same day that Phase 4 builds discard and trim for both datastores.

## Phase 4 — designed 2026-10-17

A design session, all reads; nothing on either host was changed. The decisions are
in the spec. This is what they rest on, and what was weighed and left.

### Read from the hosts

**vulcanus.** `pve-manager/9.2.20`, kernel `7.0.14-17-pve`. `atime=off`
(`relatime=on`) on `rpool`, inherited by `rpool/proxmox_backup_server` and
`rpool/backups/restic`. VM 107: 2 cores, 2048 MiB, `agent: 0`, guest agent not
running. The `pbs` storage entry: `datastore primary`, `server 192.168.0.107`,
`prune-backups keep-all=1`, **`username root@pam`**. The vzdump job carries
`prune-backups keep-last=31`, `exclude 107`, `schedule 4:00`. `/dev/net/tun` exists on
the host. LXC 108 is unprivileged with `nesting=1`. Guests: 103, 104, 105, 108 (LXC);
107, 900, 910, 911 (VM). sanoid: `[rpool/backups]` is `template_backups`, recursive,
with `[rpool/backups/restic]` overriding it to `dailies`. `zfs list`: `rpool` 11.8T
used; `rpool/proxmox_backup_server` 1.99T, `rpool/data` 989G, `rpool/backups/restic`
306G.

PBS groups, from `pvesh get /nodes/vulcanus/storage/pbs/content`: `ct/103`, `ct/104`,
`ct/105` 31 snapshots each, `ct/108` 26, `vm/900`, `vm/910`, `vm/911` 31 each, and
`vm/100` 1, written 2026-09-18 23:24 UTC. **No `vm/200`**: the spec dated the first
possible expiry from it, though Phase 1 had already forgotten it, along with `vm/101`
and `vm/106`. 109 is unused as a VMID and has no group.

**mini-nas.** `systemd-networkd` inactive, `dhcpcd` active, `NetworkManager` inactive.
`networkctl` lists `eth0` as `unmanaged`; `/etc/systemd/network/` holds only
`40-eth0.link`. So `proxmox.nix`'s `systemd.network` units have no effect at all, not
just the `ens18` match. `eth0` holds `192.168.1.11/24` by DHCP (`proto dhcp`, metric
1002) and several SLAAC IPv6 addresses. One global address is EUI-64, so it already
carries the MAC to every IPv6 peer. `rpool` `atime=off`,
`compression=lz4`; `zpool list` shows 19.1T raw, 78% capacity, and `zfs list` 9.90T
used, 2.67T available. `free -g`: 15 total, 4 free, 7 available, 31 swap. Tailscale:
`100.64.0.8`, `RouteAll: false` (no `--accept-routes`), offering an exit node. PVE
`9.2.10` per `pvesh get /version`; `pveversion` is not on the PATH.

### Read from the sources

Against `git.proxmox.com`, `HEAD` on 2026-10-17, `proxmox-backup` at 4.2.8-1. The
GitHub mirror `proxmox/proxmox-backup` was last pushed 2024-06-05 and is not a source.

- **Pull sync does not carry the protected flag.** `src/server/pull.rs`'s snapshot
  pull loads the manifest, indexes, chunks and client log; `.protected` appears only
  where `remove-vanished` declines to delete a protected snapshot (l. 1394, 1523,
  1824). It also strips `verify_state`, `key-fingerprint` and the sync signature from
  the copied manifest's unprotected section (l. 865-871), so a copy's verification
  state is its own store's.
- **Pull skips snapshots older than the newest local one** (`SkipReason::AlreadySynced`).
- **Prune skips protected snapshots and does not count them toward `keep-last`**:
  `pbs-datastore/src/prune.rs` marks a protected snapshot `Protected` and `continue`s
  before it reaches the keep count.
- **Forgetting a group leaves its protected snapshots**: `backup_info.rs` counts them
  as `unremoved_protected` and removes the rest; destroying a protected snapshot bails.
- **A dirty bitmap is per disk, per QEMU process.** `pve-qemu`'s
  `0027-PVE-Backup-Proxmox-backup-patches-for-QEMU.patch` finds
  `pbs-incremental-dirty-bitmap` on the disk's own block node, so a newly attached
  disk has none and is read in full (`created new`). An existing bitmap is used only
  if `proxmox_backup_check_incremental` passes; otherwise the whole bitmap is marked
  dirty (`invalid`). In `proxmox-backup-qemu`, that check
  (`check_last_incremental_csum`, `src/commands.rs`) verifies that the group's
  previous manifest holds the device's archive with the checksum this process last
  uploaded (`PREVIOUS_CSUMS`, keyed by device name) and the same size. A replaced disk
  at the same slot, a reused VMID or a pruned previous snapshot can therefore cost a
  full read, never a mixed disk. Chunks are addressed by SHA-256, so two disks share
  a chunk only where their bytes are identical.

### What reuse means, worked through

A snapshot names each disk by slot (`drive-virtio1.img.fidx`) and carries its own guest
config, so restoring any one snapshot brings back that machine as it was. A disk added
at a new slot is a new archive; a disk replaced at the same slot leaves the old one in
older snapshots under the same name. `vm/105` and `ct/105` are different groups. What
collides is retention: a reused VMID appends to the old group and `keep-last` evicts
the earlier machine within 31 runs. Protecting the earlier machine's snapshots keeps
them out of `keep-last`'s count and out of the expiry, which is why reuse detection
has to skip protected snapshots, or it would alert for as long as they are kept.

### Options weighed

- **The sync's direction.** Push needs a credential on the primary that can modify
  PBS #2 — `Remote.DatastoreModify` for removing vanished content — so a compromised
  primary could wipe the offsite copy. Pull was chosen.
- **The route between them.** The user first leaned towards routing a VM on mini-nas
  through mini-nas's own Tailscale. Raised against it: masqueraded traffic reaches
  vulcanus as mini-nas, a `will@` node, so the guest would inherit every `will@` grant
  — the Kubernetes and Talos APIs, fileserver SSH, the Proxmox UI — unless an nftables
  rule on mini-nas narrowed it. mini-nas also does not accept routes. Tagged nodes
  with one grant were chosen once both hosts became containers.
- **Configuration management.** No Terraform provider for PBS was fit for a backup
  system: `yavasura/terraform-provider-pbs` was created 2026-03-16, has one release
  (0.1.1) and no stars; `jkossis/terraform-provider-proxmox` was created 2026-05-29.
  An API reconciler run from LXC 108 was described and not chosen: it would be a
  program to write and own, doing what nixos-pbs's module does locally.
- **nixos-pbs.** AWildLeon/nixos-pbs, 7 stars, last pushed 2026-10-14; packaged at
  PBS 4.2.3 with a module declaring datastores and prune/verify/sync jobs and a VM test
  covering backup, restore, verify and GC; nixpkgs PR #543254 open since 2026-07-18,
  not a draft. proxmox-nixos still lists PBS under its Roadmap.
- **The form.** Four were compared: PBS native on mini-nas's host; a stock PBS VM;
  NixOS VMs on both hosts; and NixOS containers on both. Native on the host avoided
  mini-nas's networking work, but put two Proxmox stacks' packaging on one host, and
  left the primary a Debian VM. NixOS VMs put both under Nix but kept block-device
  datastores and the trim problem with them. Containers on datasets take the Nix
  configuration and remove the trim problem on both stores; their costs are running
  PBS doubly off the supported path, the `buildFHSEnv`-under-an-unprivileged-container
  question the spike answers, and the primary's migration.
- **`remove-vanished`.** The spec had PBS #2 mirroring the primary with it. The user
  asked in which cases a mistaken forget would remove redundancy: a wrong expiry, a
  hand forget, a retention edit, a primary rebuilt empty, and a compromised vulcanus
  holding `root@pam`'s password. All reach PBS #2 with it, none without, so PBS #2
  keeps its own retention.
- **Archiving.** A declared archive list in Nix was offered against the protected
  flag. The user asked whether a service could mirror protection between the stores,
  which the source showed sync does not do itself; that is the protection mirror.
  Mirroring only additions was offered, so that nothing on the primary could remove
  protection offsite; the user chose the full mirror.
- **mini-nas's address on the bridge.** A bridge gets its own MAC, so DHCP on it may
  lease a new address. Cloning `eth0`'s MAC was chosen first, then dropped: mini-nas's
  repository is public, and keeping the MAC encrypted means sops-nix rendering a
  networkd drop-in at activation, which networkd applies only when it creates the
  bridge. Letting the kernel copy the lowest port MAC (`MACAddress=none`) was ruled
  out, since a container's random veth MAC can be lower than `eth0`'s. The user chose
  a static address "for now"; the address is already in the repository in plaintext.
- **The two-year expiry of retired guests, dropped 2026-10-18.** Working out how to
  build it, `DELETE /admin/datastore/{store}/groups` was confirmed as the call
  (`src/api2/admin/datastore.rs`; `Datastore.Modify`, or `Datastore.Prune` on an owned
  group), with protected snapshots surviving it, and `proxmox-backup-client group
  forget` ruled out for a timer because it always asks for confirmation. Building it
  safely meant clock guards, since a clock jumped forward would select every group,
  plus boundary tests, notices, timers on both stores and probably a lag between
  them. Asked what it was for: it acts only on frozen groups, a frozen group is never
  touched by anything else (`keep-last` counts snapshots, not age), and today there is
  one, `vm/100`, a single snapshot that added 141 GiB, first eligible on 2028-09-18.
  Retired guests have come about four in five years, mostly Talos nodes that dedup
  against their siblings. It would have been the only automated deletion across both
  stores. A two-year Pushover prompt was offered in its place, and the user chose to
  build nothing: frozen groups are kept until forgotten by hand, on both stores.
- **Discard on VM 107.** No longer built: VM 107 retires at 4b, and a restart for
  `discard=on` buys nothing a destroy does not. The 10-16 ruling that freed space
  returns to the host is met by the datasets.

### Tailscale registration

`docs/tailnet.md` and `~/dotfiles/modules/tailscale` record that nodes register
interactively and that no reusable pre-auth key is kept, being a single point of
failure for the whole tailnet; `ansible/proxmox-tailscale.yaml` records that the tag is
applied server-side with `headscale nodes tag`, because advertising it forces
re-authentication. The PBS hosts follow both.

## Phase 4a — 2026-10-18 to 10-21

### mini-nas cannot run an LXC

Asked to build PBS #2 as LXC 109, the first step found that it could not exist.
`systemctl cat` on mini-nas finds no `pve-container@`, `pve-lxc-syscalld` or
`pve-container-debug@` unit, and `lxc-start` is not on the PATH; `pct` is. In
proxmox-nixos, at mini-nas's pin `fc773dcf` and at `HEAD` `b319f940`,
`modules/proxmox-ve/default.nix` imports ceph, bridges, cluster, linstor, manager,
qemu-server, rrdcached and vms; nothing imports `container.nix`. That file nests
`pve-container@` inside `pve-container-debug@`'s attribute set, puts the debug unit's
`serviceConfig` inside `unitConfig`, and points `pve-lxc-syscalld` at a Debian path
no package provides. Upstream: issue #140 and PR #142 (open since 2025-05,
conflicting; its author created a container but never started one), #192 and #244
(`pct create` failing on `mkfs.ext4`, then on `tar`). The user chose a NixOS container
in its place, and asked that the docs say it would move to an LXC should
proxmox-nixos ever support one.

### Read from the sources and the hosts

- **`proxmox-backup-manager sync-job run` and `prune-job run` block, and fail on a
  failed task**: `run_job` posts the run and `view_task_result` follows the task log
  to the end, bailing unless the exit status is `OK` or `WARNINGS`. So the daily
  chain is one oneshot.
- **A job run by hand still notifies**: `send_sync_status` and `send_prune_status`
  are called from the task itself, and a datastore's `notification-mode` defaults to
  `notification-system`.
- **`prune-job run` ignores `disable`**, which only the scheduler checks
  (`proxmox-backup-proxy.rs`), and a prune job requires a schedule. PBS #2's is
  declared disabled, with a placeholder schedule, and run by the chain.
- **`proxmox-backup-debug api` calls the local API as root** through
  `connect_to_localhost`, so the mirror sets flags here with no local credential.
  `PUT /admin/datastore/{store}/protected` takes `ns`, the backup's type, id and
  time, and `protected`.
- **The snapshot listing carries `protected` and `verification.state`**
  (`SnapshotListItem`, `pbs-api-types`).
- **A pull creates its target namespace** when missing (`check_and_create_ns`).
- **The webhook renders with Handlebars and a `json` helper**, which serialises a
  value, quotes included; the body is base64 in the config, and secrets are
  `{{ secrets.<name> }}`.
- vulcanus's `pbs` storage: `server 192.168.0.107`, `datastore primary`,
  `username root@pam`, fingerprint `f1:ef:ab:…:9f:9b`. mini-nas's DHCP lease hands it
  router and resolver `192.168.1.1`.
- **mini-nas's tofu has never been applied**: no state anywhere, and
  `pveum user list` returns only `root@pam`.
- **nixos-containers before nixpkgs `20b1ddd` (2026-09-19) fail to evaluate a
  container given by `path`**: an assertion reads `cfg.config.nix.enable`
  unguarded. mini-nas's pin (2026-10-05) carries the `opt.config.isDefined` guard.

### The VM test

A throwaway flake outside the repository: a NixOS VM on mini-nas's nixpkgs
(`494ce7fd`), a ZFS pool with `atime=off` and `rpool/pbs` at `atime=on`, chowned
350:350, and `containers.pbs-offsite` given `pbs-offsite`'s toplevel by `path`, with
`privateUsers = "pick"`, `enableTun`, a bridge, `--bind=/rpool/pbs:/mnt/pbs:idmap`
and `MemoryMax=2G`. Secrets from a test age key; Tailscale off.

- The container boots; uid 0 maps to host 431161344. bubblewrap runs inside it.
- `proxmox-backup-setup` creates the datastore: "Access time update check
  successful". The chunk store appears on the host owned by 350, so `idmap` holds.
- The remote, the sync job (`remove-vanished false`, `ns vulcanus`), the disabled
  prune job, the verify job, the webhook and the matcher all reconcile.
- A `proxmox-backup-client` backup into `vulcanus`, protection set through
  `proxmox-backup-debug api`, a restore, a verify reading `ok`, the prune job run
  (`keep protected vm/900/…`) and a GC all succeed.
- `MemoryPeak` 357,462,016 bytes (341 MiB), for a 44 KiB workload: an idle
  figure, not a sizing one.

### On mini-nas

- **nixos-facter turns DHCP on per detected NIC** (`networking.interfaces.eth0.useDHCP
  = mkDefault true`) as well as globally, so `networking.useDHCP = false` alone left
  dhcpcd running beside networkd. Both are set false.
- **networkd brings resolved, and turns resolvconf off**, so `networking.nameservers`
  would have been inert; the router is the bridge's `DNS=`, and Tailscale registers
  MagicDNS on `tailscale0`.
- Deployed with deploy-rs to `192.168.1.11`, magic rollback with a 60 s confirm,
  rather than `nh os switch`, whose activation dies with a dropped session. Confirmed
  first time. After: `eth0` enslaved, `vmbr0` routable with the static address and
  a SLAAC address from the bridge's MAC, `dhcpcd` inactive, Tailscale up, no failed
  units.
- **mini-nas's `nixos-upgrade` has never worked**: it fails daily on "file
  'nixos-config' was not found", `system.autoUpgrade` having no `flake`. So it
  cannot revert a deploy. Outside this project.
- The container: uid 0 at a picked base, DHCP `192.168.1.35`, `/rpool/pbs`'s chunk
  store owned 350 on the host, `MemoryPeak` 167,739,392 bytes idle. The setup unit
  creates the sync job though its remote is missing, so its success is not evidence
  of the remote.

### The first sync, and the restart after it — 2026-10-21

- **The sync's runs**, from `journalctl -u pbs-offsite-daily` and PBS's task list:
  10-19 16:00 UTC failed, `no such remote 'primary'`, before the remote existed;
  10-20 00:09 killed by a deploy (`Failed with result 'signal'`); 10-20 00:41 to
  10-21 16:42, `TASK OK`, "pulled 576.995 GiB in 272357 chunks (average rate:
  4.106 MiB/s)". The timer fired twice meanwhile, and its start job ran once the
  first finished: 3.027 GiB in 2,358 chunks, 12 min. Both prunes `TASK OK`, the
  second removing `vm/900/2026-09-20T10:08:44Z`, the 32nd.
- **Space.** `rpool/pbs` `used` 633,976,609,904 bytes (590.4 GiB), `logicalused`
  647,337,717,760, `compressratio` 1.02. PBS's
  `/admin/datastore/offsite/status` `used` 633,976,651,776: the dataset's `statfs`,
  so equal by construction. The primary's `pvesm status`: 660,293,136 KiB used
  (629.7 GiB). PBS #2's GC status is all zeros, no GC having run; next
  1792850400, Sat 10-24 14:00 UTC. mini-nas's `rpool` read 83% (`zpool list`
  `cap`) against the 84% peak predicted on 10-16.
- **Memory.** `MemoryPeak` on `container@pbs-offsite` 443,461,632 bytes (423 MiB)
  across the boot that ran the whole sync, `MemoryCurrent` 319 MiB at its end.
- **The container ran on Eastern time.** Inside it, `date` read EDT and
  `findmnt /etc/localtime` showed `America/New_York` from the host's tzdata mounted
  over NixOS's `/etc/localtime -> /etc/zoneinfo/UTC`: systemd-nspawn's default
  `--timezone=auto`. So the chain fired at 16:00 UTC, freshness at 22:00 UTC, and
  GC and verify would have run at 18:00 UTC, all moving an hour on 11-01. After
  `--timezone=off` (mini-nas `2a6e1d9`): `date` UTC, nothing mounted over
  `/etc/localtime`, the chain next at 10-22 12:00 UTC, GC's `next-run` Sat 14:00
  UTC. Freshness ran at boot, its persistent timer having missed 18:00 UTC.
- **Two reconcilers failed on that boot**, `proxmox-backup-setup` and
  `pbs-gui-user`, both "client error (Connect)" at 20:02:18-19, the proxy reporting
  ready at 20:02:19; every earlier boot's had succeeded. `connect_to_localhost`
  (`src/client_helpers.rs`, PBS 4.2.4) dials `localhost:8007`, the proxy, while
  both were ordered only after `proxmox-backup.service`, the API daemon, as nixos-pbs
  orders its own. Restarted by hand with the proxy up, both went active. Fixed in
  `49c5176c`. `pbs-notifications` and `pbs-remote` survived the race, their
  subcommands writing config in-process, and are ordered the same way regardless.
- **`aptupdate` failed daily** at ~02:20 since 10-19, `failed to execute "apt-get"
  "update"`, from nixos-pbs's `proxmox-backup-daily-update` timer.
  `proxmox-daily-update` checks a subscription, triggers the apt update and renews
  ACME certificates; only a list of available updates notifies. Stopped in
  `6051f90b`.
- **The mirror, setting.** `vm/900/2026-09-21T10:17:05Z` protected on the primary
  through `pvesh set … --protected 1` (read back `"protected":true`); `pbs-offsite
  mirror` printed `vm/900/2026-09-21T10:17:05Z: protected` and exited 0; PBS #2's
  listing for `vm/900` held 31 snapshots, that one alone protected.
- **The verify** started 20:05:54 UTC, "found 9 groups", "using 1 read and 4 verify
  thread(s)". Small guests verified at 10-100 MiB/s, then `vm/100`'s `drive-scsi0`
  ran minutes at ~72 IOPS and ~0.3 MB/s with the proxy near idle:
  `get_chunks_in_order` (`pbs-datastore/src/datastore.rs`) stats every chunk of an
  index to sort by inode before reading.
- **The verify passed** at 10-22 01:19:16 UTC, `TASK OK`, 0 index summaries with a
  nonzero error count. Groups began: ct/103 20:05:54, ct/104 20:06:26, ct/105
  20:07:29, ct/108 20:07:56, vm/100 20:09:04, vm/107 (empty) and vm/900 21:17:41,
  vm/910 21:27:35, vm/911 00:44:47. PBS #2's listing for `vulcanus`: 218 snapshots,
  `verification.state` `ok` on all 218.
- **`pbs-offsite-freshness` green**: started by hand at 03:45 UTC, `Result=success`,
  exit 0, no report lines.

### mini-nas's tofu, removed — 2026-10-22

The user recovered mini-nas's state from an old machine: serial 35, tofu 1.8.6,
holding the `admin` group, the sops data source and four `module.deploy` entries
from the original nixos-anywhere install, which `main.tf` no longer declared. It
predated the 2025-12-08 reinstall (`stat` of `/`): PVE held no `admin` group and no
`root@pam!terraform` token. `tofu_secrets.sops.yaml` was encrypted to one standalone
age key, `age1g4nm9uph…`, which matched none of `.sops.yaml`'s SSH keys under
`ssh-to-age`; the user recovered it too. It decrypted the file and no other file in
any local checkout or on the user's GitHub. carlpett/sops 1.1.1 builds against sops
3.9.0, without SSH-key decryption; 1.4.1 against 3.12.2, with it, prompting for a
passphrase through age's terminal UI. The file was given a fresh token and moved to
the SSH recipients (mini-nas `d74038f`), the user decrypted it with their key, and
the age key was deleted. `tofu plan` then proposed only creating `admin` and
`will@pam`. Neither had a use: mini-nas has no Unix user `will`, so a PAM-realm user
could not log in, and pvedaemon's journal since 2025-12-08 shows only `root@pam`
logins (latest 2026-04-29), root's password being set (`passwd -S`: `P`). So tofu
went entirely (`0524fa5`), the token was removed from PVE, and the state and its
backup were deleted.

### Wrong turns

- **The webhook body was built with `builtins.toJSON`**, wrapping `{{ message }}` in
  quotes, so a message carrying a quote or newline would have rendered invalid JSON.
  Read from the reconciled config in the test; the body now uses `{{ json … }}`.
- **The VM test hung at its first client call**, because the helper quoted with
  Python's `repr`, which chose double quotes, so the host's shell expanded the
  fingerprint lookup to nothing and the client waited at its trust prompt. The
  helper now quotes with `shlex`, closes stdin and carries a timeout.

- **Deploying `backups` to mini-nas undeployed `claude-warmup`**, which the host was
  running and `main` did not carry: the switch stopped `claude-warmup.timer` and
  removed its secret. mini-nas's CLAUDE.md warns of exactly this. Compare the running
  system with `main` before deploying a branch. Restored 2026-10-19 by merging
  `main`, once `claude-warmup` had landed there, into mini-nas's `backups` and
  redeploying; the timer and its secret are back. The hazard stands while the host
  runs an unmerged branch, which is why 4a's branches want merging promptly.
- **PBS #2 was built with no way to log into its web interface.** The tailnet policy
  granted `will@` port 8007 to it, and both realms the login page offers were dead
  ends: NixOS leaves root's password locked (`!` in shadow, `passwd -S` reads `L`), so
  PAM had nothing to check, and PBS's own realm held no users at all. Serving a UI is
  not the same as being able to use it; a grant to a port nobody can authenticate
  through is a grant to nothing.
- **`proxmox-backup-manager user update --password` is accepted and then
  discarded.** The deploy reported success, the oneshot reported success, and the
  login still returned 401 -- `update_user` in `src/api2/access/user.rs` drops the
  field with a comment pointing at `PUT /access/password`. What gave it away was
  `shadow.json`'s mtime never moving. The password now goes through that API, and
  the fix was proved by setting a throwaway password, confirming 401, deploying, and
  confirming 200.
- **The GUI password was edited into the wrong checkout.** `sops edit` was run in the
  repository root, which holds `main`, rather than the project's worktree, so the
  branch never saw it and `secrets.sops.yaml` parsed with four keys where the module
  declared five. Read the key paths out of the file before deploying a secret, and
  check which checkout an edit landed in.
- **The verify log was first read as 249 errors**, from a grep for `error` that
  matched every index's `(0 errors)` summary. Count `errors)` lines that are not
  `(0 errors)`.
- **The verify was taken for stalled**, its log four minutes quiet with the pool
  idle, from a misreading of when it had started; it was in the inode-sort stat
  pass. Read the process's age before calling a task stuck.
- **PR #4's diff was blamed on this session's work.** mini-nas's `claude-warmup` PR
  listed fifteen commits already in `main`, read at first as damage from the new
  worktree. It was a stale merge base: the PR opened 2026-10-16 14:45 UTC against
  `main` at `c3b36f1`, and `main` fast-forwarded to `e2a681f` thirteen minutes
  later, GitHub never recomputing. `c3b36f1..claude-warmup` is exactly the 18
  commits it showed, against 3 from `main`'s tip. Read a PR's commit list against
  its base's *current* tip before believing it.

## What phases 0 to 2b asserted

- **Phase 0** — `Result=success` on all four mini-nas units; offsite snapshot count
  falls from 29,305; newest-snapshot age under 25 h for **all thirteen** guest
  datasets, asserted per dataset rather than per job; `zpool status` shows a scrub
  scheduled; **mini-nas occupancy falls from 88% to ~82%** once sanoid prunes, which
  is the number that says the retention fix worked. `zpool list spool` confirms its
  usable size before PBS #2 is sized against it. The ~19.1 TiB figure applies only
  once the deferred expansion lands.
- **Phase 1** — done, and what it actually asserted. vulcanus 28.3 → 27.7 T
  (55% → 54%), mini-nas 14.8 → 14.1 T (77% → 73%), worker-0's OpenEBS disk 25.1 GiB
  lighter. `zpool list` alone reports none of the ZFS destroys: freeing is asynchronous,
  so `zpool get freeing` is the measure until it reaches zero. The PBS reports moved
  5 → 5 → 2, not the predicted 5 → 4 → 1, because guest 100 was backed up before being
  destroyed and so became a frozen group rather than leaving the list; 101 and 106
  crossing from *live, excluded* to *guest gone* is the first time that classification
  has been watched to move. `zfs-replication-freshness` returned `Result=success` for
  the first time since it was written, against a matched baseline of the same five
  failures on every prior run — and stayed green after the three replicas were
  destroyed, which is what says a retired guest's replica going is correct rather than
  merely quiet. The reuse report was exercised on its negative case against all ten
  groups in the estate, whose ends each carry one identity, and on its positive case
  against synthetic fixtures in the test suite, there being no group here that spans two
  machines.
- **Phase 2** — restore a canary PVC into scratch space and diff it against the live
  volume: the first restore this estate has ever performed. Restore a second one
  holding identity material (`headscale-data` or `syncthing-data`), because that is
  the case that matters. Then confirm the reconciliation job reports zero unbacked
  PVCs, and prove it in the other direction by annotating one PVC
  `k8up.io/backup: "false"` and seeing it appear.

  Five more, each asserting a number that moves for the reason claimed. A colmena
  deploy changes something observable *inside* the throwaway LXC — not "the command
  succeeded" — before anything is built on NixOS. The twelve SMB PVCs carry their
  exclusion on the live objects **while no Schedule exists**, asserted as a
  set-difference against `kubectl get pvc -A`; a check that runs after the first
  backup is not a check. `restic forget` through the rest-server URL fails while the
  same command against the local path succeeds, which is the only proof
  `--append-only` is doing anything. The repository lands near ~60 GB after the first
  `apps` run **and the second night's delta is small**, which is what says the
  thumbnail caches are a one-time cost rather than nightly churn — `restic stats`
  twice, not once. And the dumps restore rather than merely exist: each SQL dump
  loads into a scratch database, and each SQLite dump returns `ok` from `PRAGMA
  integrity_check`.

  The offsite leg has its own: `restic check` against the mini-nas replica with
  `--no-lock` and against `.zfs/snapshot/<latest>/` rather than the live dataset, or
  it verifies a mid-receive state. That is the property that justified a dataset over
  a zvol in the first place.
- **Phase 2b** — `zfs list rpool/backups`: 2.44 T → 305 G, `rpool/backups/borg`
  gone. Before the destroy, every Fallgrane file present at its archived size on live
  storage and in mini-nas's replica, 29,588 of 29,588.

## Wrong turns in design

Design errors caught during planning. Inheriting mistaken reasoning is worse than
inheriting none.

- **`rest-server` was first placed on the fileserver LXC** before checking it. 105 is
  *privileged* with *512 MB of RAM* — the second of which cannot hold restic's prune
  index. Check the container before designing for it.
- **proxmox-nixos was described as "shipping" a `proxmox-backup` module.** It does,
  but the project lists "Proxmox backup server" under its **Roadmap** and the module
  exposes only `enable` and `localIP`. Read the roadmap, not just the module list.
- **mini-nas was projected at ~70% occupancy after Phase 4.** Wrong: the snapshot
  bloat was subtracted *and* the `rpool/data` retirement was subtracted, when the
  bloat lived almost entirely *inside* `rpool/data` (2.68 TiB offsite against 1.44 at
  source). The true figure is ~80% before the vdev expansion.
- **Phase 4 was gated on the mini-nas disks for six weeks on a projection.** The 93%
  peak was computed 2026-09-01 and never re-measured, though Phase 0's own result on
  09-02 — 76% against a predicted 82% — said the projections ran high. Measured on
  10-16 the peak was 84%, with every input smaller than projected. Re-measure a gate
  set on a forecast before obeying it.
- **4-disk raidz2 was recommended for mini-nas** on the grounds that it is free
  against 3-disk raidz1 plus a spare. True, but the relevant comparison is 4-disk
  *raidz1*, against which it costs a third of the pool — and that third is what makes
  the cascade sustainable.
- **The external disk was first specified as `zfs send`,** and then as a restic
  backup *of the restic repository as files*. Both were wrong: the first gives no
  format diversity, and the second nests repositories so `--read-data` verifies only
  the outer layer and a restore needs two passphrases and two stages. `restic copy`
  is the correct primitive.
- **Photos, books and filesync were given four copies** when three was the
  requirement. Counting stopped at "how many exist" instead of "how many are
  wanted".
- **The replication freshness check was to learn orphan-against-superseded
  classification,** so that retained replicas of deleted guests could sit in the target
  tree without failing it. Two sessions of design for a lineage that leaves ZFS in
  Phase 4 — and the simpler reading was available throughout: the check already detects
  both conditions, it just calls them staleness, and that is sufficient when the replica
  is destroyed with the guest rather than retained. Ask what still needs the mechanism
  after the next phase lands, before building it.
- **A datastore-wide PBS prune was believed to expire a frozen group.** It does not:
  prune counts the buckets that contain backups, not elapsed calendar time, so
  `keep-daily 30` keeps 30 snapshots of a group frozen in April and stops. The error
  mattered twice — it was the stated reason a prune job on PBS #2 would break the
  retention decision, and it hid the fact that *no* setting on either layer expires a
  retired guest. One dry-run in the GUI settled it, against a frozen group that was
  being kept as a test fixture for an unrelated reason.
- **An `rpool/archive` tree and a two-sided rename runbook were designed** to hold
  retired guests, with an automated quarantine — three guards, a circuit breaker and a
  persistence file — to catch guests destroyed without following the runbook. Both were
  redundant: PBS and the mini-nas replica already freeze a destroyed guest's backups
  indefinitely, so the archive was a third copy of the same promise, and the quarantine
  existed to catch failures of a runbook that need not exist. The archive also collided
  with its own detection twice over — it was to be both the syncoid target for
  `rpool/archive` and the quarantine destination, so a quarantined orphan would be
  re-quarantined every run; and its datasets are frozen by design, which the freshness
  check's 26 h threshold would have failed forever.
- **VMIDs were to be never reused, and the case for dropping the rule was argued
  wrongly.** The rule went on the user's call, which stands: a guarantee kept by hand
  forever is not one worth promising. But the reasoning offered for it — that reuse is
  merely recoverable, costing a re-seed and an operator action — was reached without
  looking at what reuse does to PBS, where a group is keyed `vm/<vmid>` and
  `keep-last 31` evicts the retired guest's backups within 31 runs. Reuse is tolerable
  *and* it destroys data, which is a different thing from tolerable. The argument was
  built on the one layer that happened to be in view.
- **The copy count hid a failure-domain inversion.** App state showed three copies,
  but two of them shared `rpool` — leaving the databases at two failure domains while
  media had three. Count domains, not copies.
