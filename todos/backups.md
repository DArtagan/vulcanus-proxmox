# Backups: a 3-2-1-0 architecture for vulcanus + mini-nas

Supersedes the 2026-08-06 audit that previously occupied this file. That audit's
findings are folded in below; anything it recorded and this does not is superseded
rather than lost — `git log --diff-filter=D -- todos/backups.md` recovers it.

**This spec is maintained through implementation.** Unlike most files here it is
not written once and deleted on completion — the work spans sessions, so this is
the handover artefact. Every session updates the status table, records what was
*verified* and when, and records wrong turns honestly.

Slug `backups`. Branch `backups`, worktree `.worktrees/backups`, review base
`review/backups-base`.

## Status

| Phase | What | State |
|---|---|---|
| A | Record the spec, open the review | **in progress** (2026-09-01) |
| 0 | Stop the bleeding — replication, retention, scrub, expansion | not started |
| 1 | Reclaim — dead guests, orphans | not started |
| 2 | Application backups — K8up + restic | not started |
| 2b | Delete the borg tree, after a restore is proven | not started |
| 3 | Performance — drop the OpenEBS disks from vzdump | not started |
| 4 | Platform images offsite — PBS #2 + sync | not started |
| 5 | The offline copy — external disk | not started, needs a ~$200 purchase |
| 6 | Verification, notification, runbook | not started |

---

## Why this exists

The Kubernetes-side backup stack does not work. Borgmatic fails on every run and no
`openebs-hostpath` volume is covered at cluster level. Reconnaissance on 2026-08-24,
done to answer the five open questions the previous audit could not, turned up three
*live* failures it did not know about.

The goal: satisfy 3-2-1-0, report when it stops working, restore one application
without rolling back a whole VM, and stop the nightly backup driving etcd into
eight-second fsyncs.

**Constraint, user's call 2026-08-24:** self-hosted only, no cloud subscription.

---

## What is true — verified 2026-08-24

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

---

## Objectives

**Stated by the user:** 3-2-1-0 · notifications · granular restore · system
performance.

**Added during design, and accepted**

5. **Per-data-class RPO/RTO.** Media, photos and databases have different value and
   different change rates; one policy for all of them is why 7.75 TiB of re-rippable
   media consumes the offsite pool everything else needs.
6. **Assert freshness per protected object, not per job.** `syncoid-vulcanus-data`
   was red for seven months — but a green exit code would *also* have lied, because
   it was syncing eight of thirteen datasets and succeeding for those. The unit's
   result is not the backup's health.
7. **Recovery of the keys, not just the data.** `age.agekey` sits unencrypted in the
   repo working directory. Losing it makes every SOPS secret in git permanently
   undecryptable and no restore of anything helps.
8. **Resistance to logical corruption, not only hardware loss.** Replication
   propagates a mistake within the hour. The defence is retention depth and an
   append-only store, not another mirror.
9. **A written, exercised runbook.** RTO is dominated by knowing what to do. Nothing
   here has ever been restored.

---

## Decisions already made — user's call, do not relitigate

- **Self-hosted only, no subscription.** Verbatim: *"Given that we have control over
  a couple of servers, I'd like to design a system that takes advantage of the
  hardware we've already got and saves cost by not paying a subscription."*
- **K8up** for application backups, chosen after a three-way comparison against
  Stash/KubeStash and Velero.
- **Second PBS VM on mini-nas + sync job** for the platform layer offsite.
- **Password manager** for bootstrap key escrow.
- **List the borg archives, then delete** — deferred until after a restore is proven.
- **The restic repository lives in its own dedicated LXC.** Not the fileserver
  (privileged, 512 MB RAM); not a VM (cannot bind-mount a ZFS dataset, which is what
  makes the offsite replica independently verifiable).
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

### Why K8up, against Stash and Velero

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

---

## Architecture

Seven layers, unified not by one tool but by one **contract**:

> Every backup job pings a check on success. Every store has a scheduled integrity
> verification. Every protected object has a freshness assertion, evaluated **from
> the store** and independently of the job that writes it.

| Layer | What | State |
|---|---|---|
| 0 | ZFS snapshots on vulcanus (sanoid, VSS-exposed) | exists, keep |
| 1 | ZFS replication to mini-nas (syncoid pull) | exists, **broken** |
| 2 | Application backups — K8up + restic, dedicated repo LXC | new |
| 3 | Platform images — vzdump to PBS, scope reduced | exists, reduce |
| 4 | Platform images offsite — PBS #2 VM on mini-nas + sync | new |
| 5 | Offline copy — external disk on vulcanus, restic | new |
| 6 | Verification, notification, runbook, key escrow | new |

### Failure domains

Application state lives on `/var/openebs` = `vm-910-disk-1`, a zvol on `rpool`, so a
restic repo at `rpool/backups/restic` **shares its failure domain**.

| Class | Domain 1 | Domain 2 | Domain 3 | Fast-restore tier |
|---|---|---|---|---|
| App state | live on `rpool` | mini-nas (restic) | **external (restic, offline)** | restic local |
| Photos, books, filesync | live on `rpool` | mini-nas (ZFS) | **external (restic, offline)** | restic local |
| Media | live on `rpool` | mini-nas (ZFS) | external (restic, offline) | — |
| VM/LXC images | live zvol on `rpool` | mini-nas (PBS #2) | — *(deliberate)* | PBS local |

The local restic repository is **kept but not counted**. It is the fast-restore tier
and earns its place on the cases that actually happen — accidental deletion, a bad
upgrade, rolling back one app — which are far likelier than pool loss and are the
granularity objective itself. At ~100 GB of PVCs plus ~320 GB of mass-file archives
it is nearly free. This is the same structural objection that ruled out a local
restic copy of *media*, at 1/15th the price: there the copy cost 7.5 TiB, here 0.4.

**VM/LXC images stay at two domains, deliberately.** They are reconstructible from
`terraform` plus `talosctl` with their data restored from restic, and from `ansible`
for the LXCs; the only irreplaceable component, the Talos machine secrets, goes to
the password manager under objective 7.

### RPO and RTO per class

Objective 5 made concrete. RTO assumes the runbook exists and the restore is not
being invented on the spot.

| Class | RPO domain 2 | RPO domain 3 | RTO | Why |
|---|---|---|---|---|
| Databases | **6 h** | monthly | 4 h | Small, high-churn, no other source |
| App config and identity | 24 h | monthly | 4 h | Changes rarely; loss is unrecoverable |
| Photos, books, filesync | 1 h | monthly | 24 h | syncoid already hourly |
| Media | 1 h | quarterly | days | Re-servable from mini-nas in place |
| VM/LXC images | 24 h | — | 8 h | Rebuildable from IaC |

### Retention

Objective 8's mechanism is retention depth, so it is specified rather than implied.
It also silently sizes the restic repository and mini-nas's snapshot space.

| Store | Policy | Depth |
|---|---|---|
| sanoid vulcanus `rpool/storage` | 36 hourly, 30 daily, 24 monthly | 2 y *(unchanged)* |
| sanoid vulcanus PVE datasets | 30 daily | 30 d *(unchanged)* |
| **sanoid mini-nas** | 24 hourly, **60 daily**, 24 monthly | 2 y, deeper daily than source |
| **restic** (app + mass files) | keep-last 10, hourly 24, daily 30, weekly 8, monthly 24, `--keep-tag decommissioned` | 2 y |
| PBS primary | keep-last 31 | 31 d *(unchanged)* |
| **PBS #2** | keep-daily 30, weekly 8, monthly 12 | 1 y |
| **External disk** | keep-monthly 12, keep-yearly 3 | 3 y |

mini-nas's daily depth deliberately exceeds vulcanus's. syncoid uses
`--no-sync-snap`, so a target retaining more than the source is what makes the
offsite copy survive a deletion discovered late — which is the whole of objective 8.

### Schedule matrix

The performance problem is partly a scheduling problem, so the schedule is part of
the design. **04:00 is reserved for vzdump** and nothing else touches the spindles.

| Time (MDT) | Job | Where |
|---|---|---|
| hourly `:00` | sanoid snapshot | vulcanus |
| hourly `:15` | syncoid pull | mini-nas |
| 00:00 / 06:00 / 12:00 / 18:00 | K8up database Schedules | cluster |
| 01:00 | K8up volume Schedules | cluster |
| 02:00 | restic mass-file backup | repo LXC |
| **04:00** | **vzdump to PBS** | vulcanus |
| 05:30 | PBS sync to PBS #2 | PBS |
| 06:30 | freshness assertions (ZFS, PBS) | mini-nas / vulcanus |
| 03:00 1st of month | `restic forget --prune` | repo LXC |
| 07:00 Sat | PBS GC | PBS |
| 08:00 Sun | PBS verify (both datastores) | PBS |
| 09:00 Sun | `restic check` | repo LXC |
| 10:00 Sun | restore drill | cluster |
| 2nd Sun | ZFS scrub | vulcanus |
| 3rd Sun | ZFS scrub | mini-nas |

**Phase 2 adds spindle load where Phase 3 removes more.** K8up walks ~34 PVCs on the
OpenEBS zvol nightly and writes the delta to `rpool/backups/restic` on the same eight
spindles, and syncoid then sends it. restic does not re-read unchanged files — change
detection is mtime and size — so the nightly cost is a metadata walk plus a few GB of
delta, against vzdump's 253 GB. Not a wash, but real, which is why the windows are
deconflicted and why Phase 3's acceptance test measures the net rather than vzdump
alone.

### Repository placement — a dedicated LXC

`rest-server --append-only` in a **new unprivileged LXC**, 2-4 GB RAM, with one bind
mount of a new `rpool/backups/restic` dataset, matching the existing 103/104/105
pattern.

**Not the fileserver LXC.** 105 is **privileged**, bind-mounts every host dataset,
and has **512 MB of RAM** — `restic prune` loads the full repository index into
memory and will not fit.

**Not a VM.** The repository must be a ZFS **dataset**, because that is what lets
mini-nas mount the replica read-only and run `restic check` against it natively. An
offsite copy that cannot be independently verified is not a verified copy. A VM
cannot bind-mount a host dataset; it would need a zvol (an opaque blob, needing
something booted to verify it) or a network mount, reintroducing the CIFS semantics
this design exists to avoid.

- Outside the cluster, so the repository survives losing the cluster, and restic
  writes to a **local filesystem** — no CIFS locking, which is where a
  restic-over-SMB design would rot.
- **`--append-only` means a compromised or misbehaving cluster cannot delete its own
  backup history** (objective 8).
- Offsite by adding `rpool/backups` to syncoid's command list.
- Unprivileged, with the dataset chowned into the container's mapped uid range.
- NixOS ships `services.restic.server`, so this carries across the eventual
  fileserver migration.

**The repo LXC is also a restic client**, bind-mounting
`rpool/storage/{photos,books,filesync}` read-only into the same repository — no
network, no SMB.

**Prune is not append-only, and is not free.** restic's docs state prune *"requires
full read, write and delete access"* and cannot function against an append-only
server. So K8up's `Prune` schedule is disabled and retention runs from a systemd
timer on the repo LXC — better than the default, because retention authority sits
where the cluster cannot reach it. But `forget --prune` **rewrites pack files**, so
the ZFS send afterwards is large and mini-nas's snapshots retain the superseded
packs. Budget accordingly: prune runs monthly rather than weekly, bounded with
`--max-repack-size`, and the offsite projection carries ~0.15 TiB for superseded
packs held by mini-nas's 60-daily retention.

### Declaration policy

K8up defaults to opt-out — *"If omitted, K8up will default to `true`, unless
`$BACKUP_SKIP_WITHOUT_ANNOTATION` is set"* — and that default is **kept**, because it
means a newly added PVC is protected unless someone actively excludes it.

- **Fleet policy is central:** one `Schedule` per namespace under
  `kubernetes/infrastructure/k8up/`. Retention is not a per-application decision.
- **Exceptions live with the application:** `k8up.io/backup: "false"` on regenerable
  PVCs, and `k8up.io/backupcommand` plus `k8up.io/file-extension` on the database
  pods. Those encode app-specific knowledge and belong in the app's own directory.

**Coverage cannot be asserted from K8up's own metrics.** Its operator metrics
(`k8up_jobs_total`, `_successful_counter`, `_failed_counter`) carry `namespace` and
`jobType` but **no `pvc`** label; its per-PVC metrics are pushed to a Prometheus
Pushgateway and carry file and byte counts but **no last-success timestamp**.
Building the freshness alert on them would reproduce the "green but wrong" shape.

### Freshness assertions — objective 6, on every layer

The layer that actually failed for seven months was ZFS replication, so it gets the
same treatment as the application layer rather than a one-off Phase 0 check. Each
runs on a schedule, reads **the store**, and pings its own check.

| Layer | Assertion | Runs on |
|---|---|---|
| ZFS replication | newest snapshot age < 25 h for **every** dataset in the source list, and the target list matches the source list | mini-nas timer |
| PBS | newest snapshot age < 26 h **per guest group**, and group count equals expected | vulcanus timer |
| restic app layer | per-PVC newest snapshot age; PVCs with no snapshot at all; snapshots whose PVC no longer exists | cluster CronJob |
| External disk | last successful attach older than 35 days | healthchecks period |

A PBS sync carrying 8 of 13 groups and exiting 0 is the identical failure to
`syncoid-vulcanus-data`, and only a per-guest assertion catches it.

**Remains of decommissioned workloads.** The restic assertion's third output is the
handle. Left alone, a deleted app's snapshots quietly age out on the normal retention
window — and months later is exactly when they are wanted. On decommission, tag the
final snapshot `decommissioned`; `--keep-tag decommissioned` survives retention. The
step goes in `docs/kubernetes.md`'s existing *"Removing a stateful workload"*
section, which exists because deleting Loki cost ~131 GiB.

### Pool health — not just job exit codes

**A successful scrub unit is not a clean scrub.** `zfs-scrub@` exits 0 having found
errors, so `OnFailure=` and a success ping do not catch it. A timer on each host
pings only if all of these hold:

- `zpool status -x` reports all pools healthy — catches DEGRADED, which no scrub exit
  code reports
- read, write and cksum error counts are zero
- capacity below 80%, with a separate critical at 90%
- last scrub completed within 45 days

A pool at 88% and an unverified raidz1 are the two conditions that produced this
entire situation, and nothing currently watches either.

### Notification budget

The healthchecks.io free tier is 20 checks and the enumerated list is exactly 20, so
it is accounted for rather than assumed:

Watchdog · syncoid-storage · syncoid-root · sanoid vulcanus · sanoid mini-nas · pool
health vulcanus · pool health mini-nas · ZFS freshness · vzdump · PBS GC · PBS verify
· PBS sync · PBS #2 verify · PBS freshness · restic mass-file · restic forget/prune ·
restic check · **cluster backup dead-man's switch** · restore drill · external disk

The split is principled rather than a bundling compromise: checks are spent only on
what Prometheus **cannot** see — the two hosts, PBS, and the disk. Everything
in-cluster uses Prometheus rules, with **one** external check that the reconciliation
CronJob pings on success, because Prometheus and that CronJob both die with the
cluster.

`syncoid-vulcanus-data` retires in Phase 4, freeing a slot. If it still binds,
healthchecks is Apache-2.0 and self-hosting it **on mini-nas** puts it in a third
failure domain from both the cluster and vulcanus, at no subscription cost.

All alerts land in Pushover via the existing Alertmanager receiver; healthchecks
routes to the same place. **PVE 9.2.2 and PBS 4.1 both support webhook notification
targets** — verified, `pvesh get /cluster/notifications/endpoints/webhook` returns
`[]` rather than an error — so vzdump and PBS ping directly via a matcher, with no
scripting.

### The external disk

- **Attached to vulcanus** via the hot-swap bay, and **shelved away from the vulcanus
  site**. If it lives where it attaches, domains 1 and 3 are co-located and one fire
  leaves a single backup copy on raidz1. Carried to the mini-nas site.
- **A restic repository**, populated by `restic backup` of `rpool/storage/*` and
  **`restic copy`** from the local repo. `restic copy` rather than backing the
  repository up as files: a nested repo would make `--read-data` verify only the
  outer layer and turn a restore into a two-stage extraction needing two passphrases.
  This is the same standard that ruled out a VM for the repo LXC.
- **Filesystem:** a single-vdev ZFS pool with `copies=2` on metadata, for checksums
  and self-healing of metadata damage on a disk with no parity.
- **Repair path:** `restic check --read-data-subset=1/12` monthly detects damage; the
  repair is to re-copy the affected packs from the local repo or from mini-nas.
  Stated because detection without a written repair path is not a plan.
- **Automation:** a udev rule matching `ID_SERIAL` starts a systemd unit — backup,
  copy, check, ping, then Pushover *"safe to detach."*
- **The reminder and the monitor are the same object:** a healthchecks check with a
  35-day period. Attaching pings it; not attaching turns it red and nags. No separate
  reminder to maintain, and no way for a skipped month to pass unnoticed.
- Growth is measured, not guessed: monthly snapshots run 6.14 TiB (2024-09) to
  6.87 TiB (2026-08), **~380 GB/year**. One 14-16 TB disk (~$200) carries the whole
  7.5 TiB working set with roughly fifteen years of runway.

---

## Capacity: the cascade and the purchase ceiling

Disks cascade — vulcanus gets new larger ones, its retired vdev moves to mini-nas.
The coupling has a closed-form limit that decides how large a disk is worth buying.
**This is the durable rule and belongs in `docs/backups.md` when the work lands.**

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

### Geometry

vulcanus retires exactly **4 disks** per vdev upgrade and yields 2 data disks per 4
(4-disk raidz2). What mini-nas does with those four decides the whole ceiling:

| mini-nas vdev | Data disks per 4 received | Ratio | Max size jump *k* |
|---|---|---|---|
| 3-disk raidz1 + hot spare | 2 | 1/k | 1.47x |
| 4-disk raidz2 | 2 | 1/k | 1.47x |
| **4-disk raidz1** | **3** | **1.5/k** | **2.21x** |

**4-disk raidz1 is the only geometry under which a 2x disk upgrade is sustainable.**
raidz2 looks free when compared against 3-disk raidz1 plus a spare — both give two
data disks — but against 4-disk raidz1 it costs a third of the pool, and that third
is exactly what makes the cascade work.

Single parity is proportionate because **parity should follow a copy's position in
the hierarchy.** vulcanus is copy 1 with no local peer, so raidz2. mini-nas is copy 2
with copy 3 on the external disk, so raidz1 is an appropriate risk — backed by the
monthly scrub Phase 0 adds, which is what stops a latent error surfacing during a
2 d 21 h resilver.

**Available in place:** mini-nas runs zfs-2.4.4 with `feature@raidz_expansion`
**enabled**, so `zpool attach` grows a 3-disk raidz1 to 4 disks with no rebuild.
vulcanus has the feature `disabled` — its pool has never been `zpool upgrade`d —
which does not matter here.

### The purchase equation

```
                (n_m - 1) * (d1 + d0)
    d_new  <=   ---------------------  -  d2
                      2 * r * u
```

- `d_new` — size of the new disks for vulcanus
- `d1` — disks of the vdev being retired (the four moving to mini-nas)
- `d0` — mini-nas's other vdev after the move
- `d2` — vulcanus's surviving vdev
- `n_m` — disks per mini-nas vdev; the `2` is vulcanus's data disks per vdev
- `u` — how full vulcanus is permitted to get, 0.80

With `n_m = 4`, `r = 0.85`, `u = 0.80` this collapses to:

```
    d_new <= 2.21 * (d1 + d0) - d2
```

### Four rounds of upgrades

TiB throughout; constraint is mini-nas usable >= 0.68 x vulcanus usable.

| Round | Action | vulcanus | usable | mini-nas | usable | needs | |
|---|---|---|---|---|---|---|---|
| 0 | today | 4, 10 TB | 25.5 | 3x4, 3x3 TB | 12.7 | 17.3 | fails |
| 1 | +2 disks, expand both vdevs to 4-wide | 4, 10 TB | 25.5 | 4x4, 4x3 | **19.1** | 17.3 | ok |
| 2 | 4 to **8 TB**; 4 TB to mini-nas, retire 3 TB | 8, 10 TB | 32.8 | 4x4, 4x4 | 21.8 | 22.3 | marginal |
| 3 | 10 to **16 TB**; 10 TB to mini-nas | 8, 16 TB | 43.7 | 4x10, 4x4 | 38.2 | 29.7 | ok |
| 4 | 8 to **20 TB**; 8 TB to mini-nas | 20, 16 TB | 54.7 | 4x10, 4x8 | 49.1 | 37.2 | ok |

- **Round 0 already fails at u = 0.80.** Supported utilisation today is 58.7% and
  vulcanus sits at 56% — *at* the limit, not approaching it. Round 1 is not optional
  and costs two used disks.
- **Round 2 is the pinch point.** 8 TB (7.28 TiB) misses the 6.99 TiB ceiling by 2%,
  capping vulcanus at 78.4% rather than 80%. Acceptable; 6 TB is the comfortable
  choice.
- **After round 3 the constraint stops binding** — 38.2 TiB against 29.7 needed, and
  rounds 4+ allow ~22 TB disks. The discipline is entirely front-loaded.

`spool`'s 2x 1.8 TB disks are too small to join either `rpool` vdev, so they are
interim capacity or cold spares, not an expansion path.

---

## Phasing

### Phase A — record the spec

Create the `backups` branch as a worktree off `main`, write this spec superseding
the 2026-08-06 audit, commit with a `Project: backups` trailer. Then **the user runs
`wt review-open`** to freeze `review/backups-base` and open the draft PR —
`.config/wt.toml` notes every alias there pushes and the SSH key is
passphrase-protected, so an agent cannot unlock it. Nothing else starts until the
review exists, because the PR must be opened after the first commit and before the
first deploy.

### Phase 0 — stop the bleeding

- **Key escrow first** (objective 7, zero risk, highest consequence): `age.agekey`,
  `.talosconfig` and the Talos machine secrets into the password manager.
- Repair the five diverged syncoid datasets. Method: `zfs rename` each stale target
  aside, let syncoid do a full send, destroy the renamed copy once the new one
  completes — so an old copy exists throughout. Re-seed is ~30 GB, not the 120 GB the
  disk sizes suggest, because `referenced` is far below `used`.
- Declare `services.sanoid.datasets` on mini-nas per the retention table.
- Enable `services.zfs.autoScrub` on mini-nas.
- Import `spool` and add the matching `fileSystems` entry — the gotcha mini-nas's own
  CLAUDE.md warns about.
- **Expand both mini-nas vdevs from 3 to 4 disks** with `zpool attach`. Two disks,
  >= 3.64 TiB and >= 2.72 TiB respectively. Takes usable from 12.7 to 19.1 TiB and
  occupancy from 80% to 53%.
- `OnFailure=` plus a success ping on every sanoid and syncoid unit.
- Pool-health timers on both hosts.

### Phase 1 — reclaim

Destroy replicas of dead guests, orphan hostpath directories, orphan `traefik*`
PVCs, orphan PBS groups (`vm/200`, `vm/101`, `vm/106`). **The borg tree is inspected
here but deleted in Phase 2b.**

### Phase 2 — application backups

- New repo LXC, `rest-server --append-only`, `rpool/backups/restic` dataset.
- K8up operator; one `Schedule` per namespace with `Check`; `Prune` disabled in
  favour of the repo-LXC timer.
- `backupcommand` annotations: `pg_dump` for pinepods, `mariadb-dump
  --single-transaction` for photoprism and salamander, `sqlite3 .backup` for the
  WAL-mode databases. Declare PhotoPrism's and Salamander's dump schedules in this
  repo rather than inheriting an image default that can change silently.
- `k8up.io/backup: "false"` on the regenerable PVCs — Prometheus TSDB, VictoriaLogs,
  the rclone caches.
- A local restic timer on the repo LXC for `photos`, `books`, `filesync`.
- The reconciliation CronJob and its dead-man's-switch ping.
- Delete `kubernetes/apps/borgmatic/`.

### Phase 2b — delete the borg tree

**After** Phase 2's canary restore proves the replacement works. `borg list` each of
the six repositories, show the manifest, delete on confirmation. `games` (46 G) and
`rancheros` (76 G) may have no live counterpart at all, so the inspection is not a
formality. The passphrase is in the SOPS Secret `borg`; if it no longer opens them,
that is itself the answer.

Deliberately *not* in Phase 1: the 2.14 TiB is not needed until Phase 2, and deleting
the only copy of 2022-era data before the new system has restored anything once is
the wrong order.

### Phase 3 — performance

`backup=0` on `vm-910-disk-1` and `vm-911-disk-2` (the OpenEBS data disks). Safe only
after Phase 2, which is what makes the sequencing non-negotiable: granularity first,
then performance.

### Phase 4 — platform images offsite

Second PBS VM on mini-nas's Proxmox VE — **not** the NixOS module. proxmox-nixos
lists "Proxmox backup server" under its **Roadmap**, and the module is 101 lines
exposing only `enable` and `localIP`.

Remote plus scheduled sync job, and **a verify job on the target, because PBS sync
does not verify chunks on arrival.** Add the verify and prune jobs the primary
datastore also lacks. Retire `vulcanus-data` from syncoid.

**On the apparent contradiction:** a VM was ruled out for the restic repo because a
zvol datastore is opaque to the host and cannot be verified from outside. PBS #2 is a
VM with exactly that property. The difference is that PBS verifies *itself* from
inside the guest, which restic on a zvol would not. The in-guest verify job is
load-bearing, not optional, and is the reason the two decisions are consistent.

### Phase 5 — the offline copy

Gated on buying one 14-16 TB external disk (~$200). Hot-swap into vulcanus; ZFS pool
with `copies=2` metadata; `restic backup` the mass files and `restic copy` the app
repository; udev-triggered automation; Pushover on completion; shelved at the
mini-nas site. Passphrase to the password manager.

### Phase 6 — verification, notification and the runbook

- `restic check` weekly; a `--read-data-subset` pass monthly on both repositories,
  **including the offsite replica**, which is what makes it a verified copy rather
  than a hopeful one.
- Freshness assertions per the table above.
- **The automated restore drill**, weekly: restore designated canaries — one Postgres
  dump, one WAL-mode SQLite DB, one config directory — into scratch space and assert
  integrity (`pg_restore --list` parses, `PRAGMA integrity_check` returns `ok`, a
  manifest checksum matches), then ping a check. This is the "0" in 3-2-1-0 and the
  leg nobody builds.
- Prometheus rules for the in-cluster layer, following the `cronjob-health`
  convention. **The bucket table in `cronjob:max_seconds_without_success` has no
  weekly branch** — a weekly job lands in the 26 h bucket and would alert falsely
  every day. Extend that rule first.
- Homepage entries under Cluster, per repo convention.
- **`docs/backups.md`, including the restore runbook** — objective 9's deliverable.
  It must carry the sequencing hazard: on a rebuilt cluster **Flux reconciles
  applications against freshly provisioned empty PVCs, and databases initialise
  before any restore lands.** The runbook has to suspend the relevant Kustomizations,
  restore, then resume. Writing that ordering down before it is needed is the whole
  point.

---

## Verification

Each phase asserts a number that moves for the reason claimed. Checks that outlive
the session become alert rules rather than notes, per the Documentation Protocol.

- **Phase 0** — `Result=success` on all four mini-nas units; offsite snapshot count
  falls from 29,305; newest-snapshot age under 25 h for **all thirteen** guest
  datasets, asserted per dataset rather than per job; `zpool status` shows a scrub
  scheduled; `zpool list` reports mini-nas usable at ~19.1 TiB after expansion.
- **Phase 1** — `zpool list` on both pools.
- **Phase 2** — restore a canary PVC into scratch space and diff it against the live
  volume: the first restore this estate has ever performed. Then confirm the
  reconciliation job reports zero unbacked PVCs, and prove it in the other direction
  by annotating one PVC `k8up.io/backup: "false"` and seeing it appear.
- **Phase 2b** — `zfs list rpool/backups`.
- **Phase 3** — vzdump task-log **duration** for VM 910 before and after, *and* the
  net change in `avg_over_time` of etcd fsync p99 across matched 24 h windows — not
  just the backup window, because Phase 2 added load elsewhere. `transferred` reports
  logical device size and is not the measure. The `etcdHighFsyncDurations` silence
  expires 2026-09-17, the natural checkpoint.
- **Phase 4** — a verify job passing on the mini-nas datastore, and a test restore of
  one guest **from the offsite copy**.
- **Phase 5** — `restic restore` from the external on a machine that has never seen
  it, using only the passphrase from the password manager, pulling back one database
  dump **and** one media file. A disk that has only ever been written is not a copy.
- **Phase 6** — deliberately break one job, confirm the check goes red, then confirm
  it goes green again. Both directions, per `docs/README.md`. Then walk the runbook
  end to end on a scratch namespace, including the suspend-restore-resume ordering.

---

## Wrong turns, recorded so they are not repeated

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
- **The copy count hid a failure-domain inversion.** App state showed three copies,
  but two of them shared `rpool` — leaving the databases at two failure domains while
  media had three. Count domains, not copies.

---

## Prompt to open with

> Read `todos/backups.md`. It is the live spec for the `backups` project and carries
> a status table near the top saying which phases are done. Work is on the `backups`
> branch in `.worktrees/backups`, with its review at `review/backups-base`. Pick up
> at the first phase not marked done, and update the status table plus the verified
> facts as you go — this spec is the handover between sessions, so leave it able to
> start the next one cold. `wt review-open`, `wt deploy` and `wt review-close` push
> and must be run by the user, not by you.
