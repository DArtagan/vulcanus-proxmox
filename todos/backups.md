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
| A | Record the spec, open the review | **done** 2026-09-01 — [PR #3](https://github.com/DArtagan/vulcanus-proxmox/pull/3) |
| 0 | Stop the bleeding — replication, retention, scrub | **done 2026-09-03.** Key escrow, retention, scrub, monitoring on both hosts, prune and diverged-dataset repair (30,404 → 1,083 snapshots, 89% → **76%**, **2.32 TiB reclaimed**), with the five datasets re-seeded — `syncoid-vulcanus-data` completed with zero errors for the first time since 2026-01-14 |
| 1 | Reclaim — dead guests, orphans | not started |
| 2 | Application backups — K8up + restic | not started |
| 2b | Delete the borg tree, after a restore is proven | not started |
| 3 | Performance — drop the OpenEBS disks from vzdump | not started |
| 4 | Platform images offsite — PBS #2 + sync | not started; **gated on the mini-nas disks** |
| 5 | The offline copy — external disk | not started; needs a ~$200 purchase |
| 6 | Verification, notification, runbook | not started |

**Two purchases gate later phases.** 2x 4 TB for the mini-nas vdev expansion, which
Phase 4 waits on; and one 14-16 TB external disk for Phase 5. Phases 0 through 3 need
neither. Deferred 2026-09-01, user's call — purchasing takes time. See *Operating
without the vdev expansion* for what changes meanwhile.

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
| **sanoid mini-nas `storage/*`** | 24 hourly, **60 daily**, 24 monthly | 2 y, deeper daily than source |
| **sanoid mini-nas `data/*`, `ROOT`** | 30 daily | 30 d, matching source |
| **restic** (app + mass files) | keep-last 10, hourly 24, daily 30, weekly 8, monthly 24, `--keep-tag decommissioned` | 2 y |
| PBS primary | keep-last 31 | 31 d *(unchanged)* |
| **PBS #2** | keep-daily 30, weekly 8, monthly 12 | 1 y |
| **External disk** | keep-monthly 12, keep-yearly 3 | 3 y |

mini-nas's daily depth deliberately exceeds vulcanus's. syncoid uses
`--no-sync-snap`, so a target retaining more than the source is what makes the
offsite copy survive a deletion discovered late — which is the whole of objective 8.

### Schedule matrix

The performance problem is partly a scheduling problem, so the schedule is part of
the design — and **the estate runs four different clocks**, so the schedule is stated
in UTC. Local time is what goes in each config; UTC is the only frame in which two
rows on different hosts can be compared.

| Host | Zone | Offset |
|---|---|---|
| vulcanus | `America/Denver` | UTC-6 (MDT) |
| mini-nas | `America/New_York` | UTC-4 (EDT) |
| Kubernetes CronJobs | **UTC** — no `timeZone` field is set anywhere in this repo | UTC |
| PBS VM 107 | unverified; its GC schedule is in its own local time | ? |
| repo LXC (new) | set it to `America/Denver` explicitly, matching its host | UTC-6 |

**10:00 UTC is reserved for vzdump** and nothing else touches the spindles then.

| UTC | Local as configured | Job | Where |
|---|---|---|---|
| hourly `:00` | — | sanoid snapshot | vulcanus |
| hourly `:15` | — | syncoid pull | mini-nas |
| 00:00 / 06:00 / 12:00 / 18:00 | same (UTC) | K8up database Schedules | cluster |
| 01:00 | same (UTC) | K8up volume Schedules | cluster |
| 08:00 | 02:00 MDT | restic mass-file backup | repo LXC |
| **10:00** | **04:00 MDT** | **vzdump to PBS** | vulcanus |
| 11:00 | 05:00 PBS-local | PBS sync to PBS #2 | PBS |
| 12:30 | 06:30 MDT / 08:30 EDT | freshness assertions | vulcanus / mini-nas |
| 09:00 1st of month | 03:00 MDT | `restic forget --prune` | repo LXC |
| Sat 13:00 | Sat 07:00 PBS-local | PBS GC | PBS |
| Sun 14:00 | Sun 08:00 PBS-local | PBS verify (both datastores) | PBS |
| Sun 15:00 | Sun 09:00 MDT | `restic check` | repo LXC |
| Sun 16:00 | same (UTC) | restore drill | cluster |
| 2nd Sun 06:24 | 00:24 MDT | ZFS scrub | vulcanus |
| 3rd Sun 07:00 | 03:00 EDT | ZFS scrub | mini-nas |

Two things the UTC view makes visible that the local-time view hid. The restore
drill was originally written as "Sun 10:00", which in Kubernetes means 10:00 **UTC**
— exactly vzdump's window; it is moved to 16:00. And the hourly syncoid pull will
always have one run inside the vzdump window whatever offset it is given, competing
for the same reads. That is left alone deliberately: an hourly delta on `storage` is
about a megabyte, and skipping a specific hour costs more complexity than it saves.

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

**Eight exist as of 2026-09-02** — Watchdog, syncoid-storage, syncoid-root,
syncoid-data, sanoid-mini-nas, sanoid-vulcanus, pool-health-mini-nas and
pool-health-vulcanus. syncoid-data is temporary and frees a slot at Phase 4.

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

## Operating without the vdev expansion

The two disks that take mini-nas from 12.6 to 19.1 TiB are deferred — user's call
2026-09-01, purchasing takes time. The work proceeds, with three adjustments.

**The steady state is fine; the transient is not.** mini-nas at 12.6 TiB supports
vulcanus up to **14.8 TiB used** (12.6 / 0.85). vulcanus is at 13.9, so the
constraint holds with ~0.9 TiB of headroom. The standing rule until disks arrive:
**do not let vulcanus pass ~14.8 TiB used.**

But occupancy through the phases, at 12.6 TiB, would have peaked badly:

| After | mini-nas holds | Occupancy at 12.6 TiB |
|---|---|---|
| Phase 0 (sanoid finally prunes) | 10.3 | 82% |
| Phase 2 (+ restic replica 0.65) | 10.95 | **87%** ← the peak |
| Phase 3 | unchanged | 87% |
| Phase 4a (+ PBS #2, `rpool/data` still replicated) | 11.75 | 93% |
| Phase 4b (`vulcanus-data` retired, −1.8) | 9.95 | 79% |

93% is not a threshold quibble — it is the range where ZFS allocation degrades, which
is why Phase 4 does not run at 12.6 TiB.

**Adjustment 1: Phase 4 waits for the disks.** The 93% peak comes from holding
PBS #2 and a still-replicated `rpool/data` at the same time. Deferring Phase 4 until
`rpool` is expanded avoids it outright, and avoids ever having to move a datastore:
PBS #2 is built directly on the expanded pool, and `vulcanus-data` retires in the
same phase. Occupancy afterwards is (10.95 + 1.2 − 1.8) / 17.6 ≈ **59%**.

Deferring costs a format gap, not a coverage gap — once Phase 0 repairs it,
`rpool/data` still carries the guests offsite, just as ZFS rather than as PBS chunks.
Nothing in Phases 2 or 3 depends on Phase 4.

*Fallback, if Phase 4 has to happen before the disks arrive:* put PBS #2's datastore
on `spool`, which is why the pool is imported. That works and keeps `rpool` at 87%,
but the datastore then has to move when `spool` is destroyed for its bays — either a
`zfs send` or a full ~1.2 TiB re-sync over the WAN. Take it only if the wait becomes
long.

**Adjustment 2: retention depth only where it is cheap.** The 60-daily figure for
mini-nas was sized against 19.1 TiB. At 12.6, apply it to `storage/*` — media barely
changes, so extra dailies cost almost nothing — and hold `data/*` at 30 daily
matching source, since those are high-churn zvols that retire in Phase 4 anyway.

**Adjustment 3: mini-nas pool-health thresholds are 90% warning / 94% critical**
until the expansion, not 80/90. A warning at 80% would fire continuously against a
*predicted* 82-87% and train everyone to ignore it, which is the anti-pattern
`docs/README.md` names directly. Restore 80/90 once the expansion lands. vulcanus
keeps 80/90 throughout.

**Nothing here changes the purchase ceiling below.** The equation governs disk sizes
at upgrade time; these adjustments govern how the work is sequenced while the pool is
small. Once expanded, the numbers in the four-round table apply unchanged.

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

### `spool`: bays, not disks

The 8-bay chassis is full, so `spool`'s two slots are the only expansion room mini-nas
has. But **`spool`'s disks cannot themselves expand `rpool`.** `zpool attach` onto a
raidz vdev requires the new disk to be at least as large as the smallest member, and
the numbers do not allow it:

| vdev | Members | Smallest | A 1.8 TiB `spool` disk? |
|---|---|---|---|
| `rpool` raidz1-0 | 3x 3.64 TiB | 3.64 TiB | too small |
| `rpool` raidz1-1 | 3x 2.72 TiB | 2.72 TiB | too small |
| `spool` | 2x 1.8 TiB | — | — |

**The bays are the resource, not the disks in them.** Destroy `spool`, buy two disks,
put them in those slots, and expand both `rpool` vdevs to 4-wide. The two 2 TB
Toshibas come out and become cold spares on a shelf, which is a better use for them
than a pool nothing reads.

Buy **2x 4 TB**. One is the correct size for raidz1-0; the other strands ~0.9 TiB in
raidz1-1 until that vdev's older members are replaced, which is worth it for having
uniform disks when the cascade later delivers 4 TB drives.

**This returns PBS #2's datastore to `rpool`**, which is fine precisely because
`rpool` is no longer small. It also removes the reason `spool` had to be imported at
all, so that decision is superseded rather than merely revised.

Three things to expect, none of them obvious:

- **Expansion does not improve the efficiency of existing data.** RAIDZ expansion
  reflows blocks onto the new disk but preserves each block's original data-to-parity
  ratio; only new writes use the wider stripe. So the gain is real but smaller than
  the naive arithmetic: raidz1-0 goes from ~1.04 to ~3.9 TiB of writable space and
  raidz1-1 from ~0.41 to ~2.5 TiB, a gain of **~5.0 TiB rather than 6.5**. Effective
  capacity is ~17.6 TiB immediately, converging on 19.1 TiB as old data is rewritten
  — which for media is essentially never, and that is acceptable.
- **Expansion is slow.** It reflows the whole vdev. A single-disk resilver here took
  2 d 21 h, so budget days per vdev and do them one at a time.
- **All the swap is on the `spool` disks** — two 16 GiB partitions, 31 GiB active.
  Removing them removes it. The replacements need swap partitions carved by hand
  before joining the pool, and `disk-config.nix` updated to match, because disko does
  not apply to a live system.

### How the cascade physically happens

Worth stating because the obvious reading is wrong: **a raidz vdev cannot be removed
from a pool.** ZFS device removal covers top-level mirrors and single disks only. So
each cascade round is not "retire a vdev and add another" — it is `zpool replace` on
each of the four disks of mini-nas's oldest vdev in turn, after which the vdev
autoexpands. The capacity figures in the four-round table are unaffected; only the
method is.

With all 8 bays full, a replace-in-place leaves the vdev **at zero parity for the
duration of each resilver** — four resilvers of roughly three days each, per round, on
single parity. Attach the replacement temporarily over USB or eSATA instead: with both
disks present, `zpool replace` resilvers from the old member and the vdev is never
degraded. Given these disk ages, that is worth the adapter.

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

- ~~**Key escrow first**~~ — **done 2026-09-01.** The age keypair is in the password
  manager and the local `age.agekey` was deleted. That cost no access: `.sops.yaml`
  carries three recipients and any one private half opens every file, local `sops -d`
  goes through the `thenixbeast_will` ssh-ed25519 key, and Flux decrypts from the
  in-cluster `sops-age` Secret in `flux-system`, which still holds the key.
  Still outstanding: `.talosconfig` and the Talos machine secrets.
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

#### Measured on 2026-09-02, after deploying

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

#### Stale syncoid holds survive the prune

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

#### Do not run sanoid by hand while its timer is enabled

Sanoid serialises on `/var/run/sanoid/sanoid_pruning.lock`. A run that finds a valid
lock held by another process exits early and silently, which looks exactly like a run
that decided there was nothing to do. Worse, the staleness check compares the
lockfile's recorded command against `ps -p <pid> -o args=`, and when that comparison
fails it *unlinks the lock* — so two concurrent runs can destroy each other, one
exiting `RC=2` with `No valid lockfile found`.

This cost a long detour: a manual prune and the hourly timer fought, the timer's runs
exited in fifteen seconds each, and the fifteen-second exits were misread as sanoid
refusing to prune at all. Let the timer do the work, or stop it first.

#### Why the datasets diverged: a reused VMID

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

#### Repairing the five diverged datasets

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

#### What the repair found, 2026-09-03

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

### Phase 1 — reclaim, and stop the accumulation

Two halves: clear what has built up, and build the mechanism that stops it building
up again. The second half matters more — Phase 0 showed this class of debris does not
merely waste space, it silently blocks replication.

**The borg tree is inspected here but deleted in Phase 2b**, after a restore proves
the replacement works.

#### What to clear

*On mini-nas, no source counterpart at all — nothing will ever remove these:*

| Dataset under `foreign-backups/vulcanus/data/` | Size |
|---|---|
| `vm-901-disk-0` | 6.60 G |
| `vm-200-disk-1` | 1.72 G |
| `vm-200-disk-0`, `vm-107-disk-1` | ~0 |

*On vulcanus, dead guests still replicated daily because the guest still exists:*

| Guest | Dataset | Size |
|---|---|---|
| 100 rancheros (stopped) | `vm-100-disk-0` | **235 G offsite, 256 G at source** |
| 100 rancheros (stopped) | `rpool/rancheros` | 40 G, not replicated |
| 101 disk-resizer (stopped) | `vm-101-disk-0` | 5.55 G |
| 106 ubuntu-desktop (stopped) | `vm-106-disk-0` | ~0 |

*Elsewhere:* three orphaned hostpath directories on worker-0 (25 G stale PhotoPrism
storage, 434 M, 131 M), four orphaned `traefik*` PVCs in `infrastructure`, and PBS
groups `vm/200`, `vm/101` and `vm/106` — pinned forever because vzdump prunes only
the groups it backs up.

#### Archival happens on vulcanus, before the guest is destroyed

**Not after.** PVE frees a zvol with `zfs destroy -r`
(`ZFSPoolPlugin.pm: zfs_request($scfg, undef, 'destroy', '-r', ...)`), so the
snapshots go with it and nothing survives to archive. No holds exist under
`rpool/data` to stop it either. The evidence is the asymmetry Phase 0 found:
`vm-200-*`, `vm-901-*` and `vm-107-disk-1` exist on mini-nas, while **every** dataset
under vulcanus's `rpool/data` maps to a live guest. Those snapshots survive only on
the replica, because syncoid never prunes the target.

Recorded so the obvious automation — react to a deletion — is not attempted. There is
also no hook to react with: PVE hookscripts fire on pre-start, post-start, pre-stop
and post-stop only.

**Retiring a guest is therefore a two-sided rename, done before `qm destroy`:**

```
vulcanus:  rpool/data/vm-911-disk-1              -> rpool/archive/vm-911-disk-1_2026-01
mini-nas:  .../vulcanus/data/vm-911-disk-1       -> .../vulcanus/archive/vm-911-disk-1_2026-01
```

`zfs rename` is a metadata operation: snapshots move with the dataset and keep their
GUIDs. So a syncoid command for `rpool/archive` finds matching snapshots on both
sides and **continues incrementally** — no full re-send, no orphan left behind, and
the existing prune and replication machinery applies to the archive with whatever
retention is chosen for it. Doing it on both sides is what avoids the orphan; a
source-only rename reads to the target as "old gone, new appeared".

This also frees the name immediately, so a new disk may reuse it without colliding.

**What this needs:**

- `rpool/archive` on vulcanus, and a `vulcanus-archive` syncoid command on mini-nas,
  so archived data keeps an offsite copy rather than existing only on one host.
- A sanoid template for the archive tree. `autosnap = no` — the snapshots are already
  there and nothing writes to an archived dataset — with retention set long.
- A runbook, next to "Removing a stateful workload" in
  [`docs/kubernetes.md`](../docs/kubernetes.md).

**VMIDs are still never reused.** The rename frees the name, but a retired ID being
reissued is what turned a routine orphan into seven months of silent blockage, and
the discipline costs nothing. Same shape as the never-reuse-a-slug registry in
[`docs/project_log.md`](../docs/project_log.md).

**Retention of what escapes still belongs on the target.** An archive on vulcanus
alone is in the same failure domain as the thing it protects against — it survives
neither losing that host nor an accidental `qm destroy` on it. Replicating
`rpool/archive` covers the deliberate case; the target-side net below covers guests
destroyed without the runbook being followed.

#### The net: two failure modes, one quarantine

For guests destroyed without the runbook being followed. With the archival step above
in place this should almost never fire, and firing means something happened outside
the intended path — which is itself worth knowing.

syncoid never removes datasets from the replication target, which produces two
problems that look alike and are not:

- **Orphan** — a target dataset whose source is gone. Wastes space indefinitely.
- **Superseded** — source and target share a *name* but no snapshot, because a disk
  was recreated or a VMID reused. **This blocks replication of the live guest**, and
  is what cost seven months on worker-1.

**Detection** extends `zfs-replication-freshness`, which already fetches the source
dataset list for its coverage assertion:

| Condition | Classification |
|---|---|
| target exists, no source counterpart | orphan |
| both exist, zero common snapshot names | superseded |

Comparing snapshot *names* is the check that matters. Age alone cannot see either
case: a superseded dataset has recent-looking snapshots of the wrong lineage, which
is exactly how January went unnoticed.

**Quarantine** is `zfs rename` into
`rpool/foreign-backups/vulcanus/archive/<name>_<date>` — the same tree the runbook
uses, so there is one place to look regardless of how something got there.

**Guards.** This runs unattended against the only offsite copy, and a naive version
would quarantine the entire replica the first time an SSH connection dropped:

- the source listing must succeed *and* return a plausible dataset count
- the condition must persist across three consecutive daily runs
- a circuit breaker — never quarantine more than three datasets in one run

#### Open decision: what happens to archived replicas

Left for whoever picks up Phase 1, because it is a data-destruction policy rather
than a mechanism. The options, with the trade each makes:

1. **Alert only, destroy by hand.** The check reports the archive's contents and their age
   so they surface as outstanding work. Nothing is destroyed unattended. Fixes the
   demonstrated defects — invisible accumulation and blocked replication — without
   adding automated deletion of backup data.
2. **Auto-destroy after 90 days**, with a `local:retain=true` ZFS property as opt-out
   and an alert before expiry. Guarantees the archive cannot grow without bound. Mirrors
   the `--keep-tag decommissioned` pattern planned for restic in Phase 2.
3. **Auto-destroy after 30 days**, same mechanism. Phase 0's 624 GB sat on a pool at
   89%, so a long window has real capacity cost.

Worth weighing against what Phase 0 actually found when it opened one of these: 81%
of the 543 GB was a Loki volume already deliberately deleted, and about 10 GB was
irreplaceable. The archive is likelier to hold expired bulk than anything wanted — but
it took an inspection to know that, which is an argument for the window being long
enough to inspect rather than for it being long.

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

**Gated on the two mini-nas disks.** Building PBS #2 while `rpool` is unexpanded and
`rpool/data` is still replicated puts the pool at 93%; waiting means the datastore is
built once, on the expanded pool, rather than built on `spool` and moved later. See
*Operating without the vdev expansion*, Adjustment 1, including the fallback if the
wait becomes long.

Second PBS VM on mini-nas's Proxmox VE — **not** the NixOS module. proxmox-nixos
lists "Proxmox backup server" under its **Roadmap**, and the module is 101 lines
exposing only `enable` and `localIP`.

**Its datastore goes on `spool`, not `rpool`** — see *Operating without the vdev
expansion*. That keeps `rpool` off 93% while the expansion is pending, and puts the
offsite VM images on different spindles from the offsite ZFS replica.

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
  scheduled; **mini-nas occupancy falls from 88% to ~82%** once sanoid prunes, which
  is the number that says the retention fix worked. `zpool list spool` confirms its
  usable size before PBS #2 is sized against it. The ~19.1 TiB figure applies only
  once the deferred expansion lands.
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
