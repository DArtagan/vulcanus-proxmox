# Backups: a 3-2-1-0 architecture for vulcanus + mini-nas

Supersedes the 2026-08-06 audit that previously occupied this file. That audit's
findings are folded in below; anything it recorded and this does not is superseded
rather than lost — `git log --diff-filter=D -- todos/backups.md` recovers it.

**This spec is maintained through implementation.** Unlike most files here it is
not written once and deleted on completion — the work spans sessions, so this is
the handover artefact. Every session updates the status table, records what was
*verified* and when, and records wrong turns honestly.

**Documentation lands at the end of each phase, not at the end of the project.** A
deliberate departure from the usual lifecycle, for the same reason this spec is
maintained rather than written once: a phase's output is running in production the
moment it deploys, and leaving its permanent facts in a file marked "not yet done"
strands them — `docs/` is meant to describe the running system, and between phases it
would not. So each phase ends by writing what it made permanently true into
[`docs/backups.md`](../docs/backups.md), extending what earlier phases left rather
than replacing it. Only deleting this spec and adding the
[`docs/project_log.md`](../docs/project_log.md) entry wait for the end.

Slug `backups`. Branch `backups`, worktree `.worktrees/backups`, review base
`review/backups-base`.

## Status

| Phase | What | State |
|---|---|---|
| A | Record the spec, open the review | **done** 2026-09-01 — [PR #3](https://github.com/DArtagan/vulcanus-proxmox/pull/3) |
| 0 | Stop the bleeding — replication, retention, scrub | **done 2026-09-03.** Key escrow, retention, scrub, monitoring on both hosts, prune and diverged-dataset repair (30,404 → 1,083 snapshots, 89% → **76%**, **2.32 TiB reclaimed**), with the five datasets re-seeded — `syncoid-vulcanus-data` completed with zero errors for the first time since 2026-01-14 — [PR #3](https://github.com/DArtagan/vulcanus-proxmox/pull/3), merged 2026-09-18 |
| 1 | Reclaim — dead guests, orphans | **done 2026-09-18.** Five orphaned datasets, guests 100/101/106, `rpool/rancheros`, three replicas, four PVCs, seven hostpath dirs and three PBS groups destroyed; vulcanus 28.3→**27.7 T**, mini-nas 77→**73%**, worker-0 **25.1 GiB** back. `zfs-replication-freshness` **green for the first time since inception**; `pbs-freshness` built and deployed here rather than in Phase 6, reports 5→2, zero failures — [PR #11](https://github.com/DArtagan/vulcanus-proxmox/pull/11) |
| 2 | Application backups — K8up + restic | **in progress**, opened 2026-09-20 — [PR #13](https://github.com/DArtagan/vulcanus-proxmox/pull/13). **Steps 0–4 done 2026-09-21:** exclusions live (no RWX claim in scope), repo LXC 108 on NixOS serving append-only (403 on `forget` through the URL, proven), escrow complete, mass-file first run done (296.6 GiB in 2 h 09 m, 247.6 GiB stored), K8up operator installed with no Schedules. **Step 5 done 2026-09-21:** all seven dumps (annotations for the relational databases, PreBackupPods for SQLite) taken by one-off dumps-only Backups and verified in the store. **Every Schedule must set `runAsUser: 0`**: as K8up's default uid 65532 the Job cannot read 13 of 22 volumes, and reports Succeeded anyway (see *Checked against K8up #910 and #1032*). Next: step 6 |
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
- **A deleted guest's disks retain exactly as a live guest's would**, on both layers,
  with nothing destroyed automatically — freeing that space is a deliberate act after
  inspecting what is there.

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
| **PBS #2** | none of its own — sync with `remove-vanished`, mirroring the primary | tracks primary |
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
| 12:30 | 08:30 EDT | ZFS replication freshness | mini-nas |
| 14:00 | 08:00 MDT | PBS freshness | vulcanus |
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
| ZFS replication | newest snapshot age < 25 h for **every** dataset in the source list, and the target list matches the source list. A replica whose guest is destroyed reads as staleness, which is why one is destroyed with its guest rather than retained | mini-nas timer |
| PBS | newest snapshot age < 26 h for every guest **the job is scoped to back up** — see below | vulcanus timer |
| restic app layer | per-PVC newest snapshot age; PVCs with no snapshot at all; snapshots whose PVC no longer exists | cluster CronJob |
| External disk | last successful attach older than 35 days | healthchecks period |

A PBS sync carrying 8 of 13 groups and exiting 0 is the identical failure to
`syncoid-vulcanus-data`, and only a per-guest assertion catches it.

**The PBS assertion needs the job's scope, not just the group list.** Staleness alone
cannot tell a broken backup from a guest that no longer exists, and a check that fires
on every retired guest forever is the always-on warning
[`docs/README.md`](../docs/README.md) warns against. Scope comes from
`/etc/pve/jobs.cfg` — `all 1` minus `exclude` — read at runtime rather than hardcoded,
so a guest created tomorrow is expected without anyone remembering to add it. That is
the same guard as *a target that is never created cannot alert as down*.

| Guest | Group | Verdict |
|---|---|---|
| live, **in scope** | newest snapshot older than 26 h | **fail** — backups broken for this guest |
| live, **in scope** | no group at all | **fail** — never backed up |
| guest gone | group frozen | *report* — a pending retention decision |
| live, **excluded** | frozen or absent | *report* — a coverage statement |
| any | oldest and newest snapshot carry different guest UUIDs | *report*, **plus a Pushover push** — the VMID was reused and the prior machine is being evicted, with a deadline |

A frozen group never fails. Neither does a reused VMID — it takes a third route,
decided 2026-09-19, user's call: reported here and pushed to Pushover directly, on a
stateless ladder. Failing the check would have held it down for up to 31 runs, and
healthchecks only notifies on transitions, so a genuine backup failure inside that
window would have been silent. Once a guest is gone no backup can be taken, so its
staleness carries no information; what carries information is a guest still in scope
going stale, which is the PBS analogue of syncoid succeeding on eight datasets of
thirteen. The reports are what the retention decision above depends on — without them
"someone inspects and decides" has nothing to prompt it.

Built and deployed in Phase 1 rather than Phase 6, because Phase 1's verification is
this classifier being watched as it moves. It is `ansible/files/pbs_freshness.py` on a
daily 08:00 timer on vulcanus. Two corrections the first real run produced: `vm/107` is
an *empty* group rather than an absent one, and an in-flight backup already creates its
group, so a job that starts and dies nightly reads as fresh coverage here.

**The reuse row is the one that needed no remembered state.** A group that stops being
frozen because its VMID was reissued looks identical to a healthy group from the live
guest list alone, and the eviction it starts is silent — see *VMID reuse is tolerated,
and reported* in Phase 1 for why the UUID comparison catches it and a day-over-day
inventory would not.

Two things this shape still does not catch, named rather than assumed. **A group
vanishing** — retention keeps frozen groups forever, so a disappearance is notable,
but a check comparing against the *live* guest list has nothing to compare a vanished
group against; catching it needs a remembered inventory. That matters more after Phase 4,
where `remove-vanished` propagates a primary-side deletion straight to PBS #2. And
**scope drift** — adding a live guest to `exclude` moves it from "fail if stale" to
"report", making the check quieter rather than louder, which is why excluded-but-live
guests are reported even though they are not failures.

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

**Eleven exist as of 2026-09-21** — Watchdog, syncoid-storage, syncoid-root,
syncoid-data, sanoid-mini-nas, sanoid-vulcanus, pool-health-mini-nas and
pool-health-vulcanus from Phase 0; pbs-freshness from Phase 1; restic-massfiles
and restic-prune from Phase 2. syncoid-data narrows rather than retires at Phase 4
and keeps its slot.

The split is principled rather than a bundling compromise: checks are spent only on
what Prometheus **cannot** see — the two hosts, PBS, and the disk. Everything
in-cluster uses Prometheus rules, with **one** external check that the reconciliation
CronJob pings on success, because Prometheus and that CronJob both die with the
cluster.

`syncoid-vulcanus-data` **does not retire** in Phase 4 and frees no slot: it narrows to
`rpool/data/vm-107-disk-0` and keeps its name, so the enumerated 20 has no slack left.
If that binds, healthchecks is Apache-2.0 and self-hosting it **on mini-nas** puts it in
a third failure domain from both the cluster and vulcanus, at no subscription cost.

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

Phase status is maintained in the table above as the work proceeds, and **every phase
ends by updating [`docs/backups.md`](../docs/backups.md)** with whatever it made
permanently true — see the header for why that is per-phase rather than saved for the
end of the project.

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
  ~~Still outstanding: `.talosconfig` and the Talos machine secrets.~~ Closed
  in Phase 2, 2026-09-21: the machine secrets are escrowed, and the
  `.talosconfig` deliberately is not, being derived from them. See *Escrow,
  including what Phase 0 left open*.
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

#### Documentation written

[`docs/backups.md`](../docs/backups.md) — the layers and the pull-not-push property,
retention on both hosts and why the offsite copy keeps more dailies, the eight checks
and the three rules shaping them, scrub scheduling and why the jitter is 15 minutes,
the capacity model and purchase equation, and the operational notes. Row added to
[`docs/README.md`](../docs/README.md).

### Phase 1 — reclaim, and settle what retains a deleted guest — **done 2026-09-18**

Destroyed what had accumulated, and put the retention of a deleted guest on the one
layer that actually holds it. What follows is what happened, not what was planned;
where the two differ the difference is called out, because Phases 2-6 read back into
this section.

**The borg tree was inspected, not deleted** — Phase 2b still owns that.
`rpool/backups/borg` holds 2.14 TiB in six repositories under `vulcanus-borgmatic/`
(`audio`, `games`, `photos`, `rancheros`, `syncthing`, `video`), none written since
December 2022.

#### What was destroyed

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

#### Guest 100 was backed up first, changing the numbers

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

#### The vzdump exclude list is now `107`

Trimmed after the destroys, never before: trimming first would have put three
still-existing dead VMs *in scope*, which the 04:00 run would have backed up and the
classifier would have correctly called a failure. `107` is PBS itself, whose datastore
lives on `rpool`, so backing it up is circular — the only standing reason to exclude a
guest. Recorded in [`vzdump-job-in-terraform.md`](vzdump-job-in-terraform.md), which
carries the job's full state until the provider gap closes.

#### The PBS freshness assertion was built here, not in Phase 6

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

#### What the numbers did

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

#### What this phase did *not* do

- `pvc-b52710af` aside, no other Released or unbound PV was touched.
- The borg tree stands; Phase 2b deletes it after a restore proves the replacement.
- **A backup that is still running already creates its PBS group**, timestamped at
  start. The check therefore reads an in-flight backup as fresh coverage. A job that
  started and then died every night would look healthy to `pbs-freshness` alone; the
  vzdump check enumerated in the notification budget is what closes that, and does not
  exist yet. Named here rather than assumed.

### Phase 2 — application backups

Opened 2026-09-20. The design below supersedes the bullet list this section
carried, which was written before anything was measured. Six of its assumptions
were wrong; each is recorded under *What Phase 2 measured* so the reasoning is
inherited rather than the conclusion alone.

**Decisions taken 2026-09-20, user's call:**

- **The repo LXC is NixOS, deployed with colmena** — starting the NixOS migration
  here rather than with the fileserver. Taken knowing colmena has not tagged a
  release since v0.4.0 (2023-05-15) and nixpkgs ships that tag, against clan,
  which is far more active and has a calver release policy but is a framework
  whose scope overlaps this project's own backup layer.
- **Dumps come from K8up `PreBackupPod`s**, not `backupcommand` annotations and
  not CronJobs.
- **Every backed-up PVC is backed up whole.** No per-path exclusions anywhere —
  not PhotoPrism's `cache/`, not Plex, not syncthing's index. A PVC is either in
  or out, and `k8up.io/backup-restic-args` is not used. Verbatim: *"let's not
  bother excluding the PhotoPrism caches - keep it simple and back up the whole
  thing."*
- **`borg-backups-pvc` must never be backed up.** Verbatim: *"We must not
  re-back-up borg-backups-pvc during this. That would take far too much hard
  drive capacity, and we're going to blow it away in a later phase anyway."*

**The order the work lands in.** Flux reconciles from `main`, so each step is
independently mergeable and safe on arrival, and the first cluster-visible
changes are inert:

| # | Lands | Flux-visible | Risk at merge |
|---|---|---|---|
| 0 | colmena-in-LXC spike — throwaway container, prove a deploy applies | no | none; **gates step 3** |
| 1 | branch, first commit, `wt review-open` | no | none |
| 2 | **all exclusions** — 12 SMB PVCs + the regenerable ones | yes | **inert**, no operator exists |
| 3 | `nixos/`, repo LXC, rest-server, repo initialised, local timers, escrow | no | none |
| 4 | K8up operator + CRDs, **zero Schedules** | yes | inert |
| 5 | seven PreBackupPods | yes | inert without a Schedule |
| 6 | `automatic-ripping-machine` Schedule only | yes | tiny — 1 GiB, worker-1 |
| 7 | `apps` + `infrastructure` Schedules | yes | the first-backup burst |
| 8 | coverage CronJob + dead-man's-switch ping | yes | low |
| 9 | canary restore, homepage, docs | — | — |
| 10 | offsite: syncoid + freshness roots, in `~/repositories/mini-nas` | other repo | capacity |

`kubernetes/apps/borgmatic/` is deleted **after Phase 2b**, not here: 2b needs
`borg list` against six repositories and borgmatic's Deployment is the only borg
client deployed.

#### What Phase 2 measured

Each of these contradicts something this spec asserted, and each changes what
gets built.

**1. K8up's opt-out default puts ~16 TiB of SMB volumes in scope.**
`listAndFilterPVCs` admits a PVC if it is RWX **or** RWO, so all twelve
SMB-backed static PVCs qualify — including `borg-backups-pvc`, the 4 Ti handle on
`/rpool/backups/borg`. A default-on Schedule in `apps` would restic the borg
repositories, over CIFS, onto the spindles that hold them. This spec named only
"Prometheus TSDB, VictoriaLogs, the rclone caches"; the SMB set is larger and
more urgent, and is excluded for a different reason — not regenerable, but
already covered by the ZFS layer and by the repo LXC reading `rpool/storage`
directly. *Nothing may schedule a backup until those exclusions are live.*

**2. The `backupcommand` plan does not work, for three independent reasons.**
`headscale` and `rustdesk/hbbs` have **no `/bin/sh` at all**; `linkding` has only
`python3`; `stump` has nothing — so `sqlite3 .backup` cannot run where this spec
said it would. `.backup` writes through the SQLite backup API and needs a
*seekable* destination, so it cannot stream to K8up's stdin pipe regardless. And
K8up names a stdin dump `/<namespace>-<containerName>`, while `photoprism`,
`salamander` and `pinepods` each have a container named `database` — all three
would write `/apps-database.sql`, one restic path for three databases, which
`forget --group-by host,paths` would rotate against each other.

**3. `PreBackupPod` defeats all three**, because the container is one we define:
any image, any name, mounting the PVC. The dump also becomes a first-class restic
snapshot, so the coverage assertion sees it directly — where a CronJob writing a
file onto the PVC leaves a *stale* dump invisible. Its cost is a real one:
`allDeploymentsAreReady` is all-or-nothing, so one PreBackupPod that cannot
become ready blocks the whole namespace's backup, volumes included, and
`backupAnnotatedPods` returns on first error. Build headscale's first, at 304 KB,
before any Schedule exists.

**4. The spec overstates the WAL risk for exactly the apps it names.** For
headscale, rustdesk and syncthing the irreplaceable material is a *key file* —
`noise_private.key`, `id_ed25519`, `cert.pem`/`key.pem` — and a file copy of a
key file is safe, provided the backup Job can read it. Under K8up's defaults it
cannot: see *Checked against K8up #910 and #1032*. The databases that genuinely
need a consistent dump are **linkding** (two WAL DBs holding the only copy of
its rows), **plex** (watch state) and **grafana** — a fifteenth SQLite database
this spec does not count, holding an admin password that `prometheus.yaml`
records as no longer recoverable from SOPS.

**5. Per-namespace Schedules cannot live under `kubernetes/infrastructure/`.**
That directory's `kustomization.yaml` sets `namespace: infrastructure`, and
kustomize's `namespace:` is a *transformer, not a default* — it overrides a
namespace an object declares for itself. A `Schedule` backs up the namespace it
lives in, so the three Schedules this spec places there would all arrive in
`infrastructure`: that namespace backed up three times, and **`apps` — every
database and all the identity material — backed up zero times**, with Flux
reporting `Ready` throughout. They go in a new top-level `kubernetes/k8up/` with
no `namespace:` line, on its own Flux Kustomization, following the precedent
`kubernetes/cluster/automatic-ripping-machine.yaml` set. Fleet policy stays
central because one directory still owns every Schedule.

**6. `pvesh get /cluster/nextid` returns a VMID that must not be used.** It
returns **100** — the guest Phase 1 destroyed after a deliberate one-off vzdump,
whose `vm/100` PBS group is frozen and kept on purpose. PVE has no memory of it.
Use a VMID never issued, confirmed against the datastore's group list
(`vm/{100,107,900,910,911}`, `ct/{103,104,105}`) rather than against `nextid`.

**Sizes, measured with `talosctl usage`** — and *not* from
`kubelet_volume_stats_used_bytes`, which for hostpath volumes reports the
underlying filesystem, so every worker-0 PVC reads an identical 90.31 GiB and
every SMB PVC reads 7537 GiB. worker-0 holds 76 GiB across 25 OpenEBS volumes, of
which `salamander-data` (27.88 GB) and `photoprism-data` (26.49 GB) are 71% — and
93% of *those* is regenerable thumbnail `cache/`. Backed up whole per the decision
above, the app layer is ~60 GB and the repository ~360 GB once the mass files
join it, against this spec's 0.50 TiB budget. The cost is near-entirely one-time:
PhotoPrism writes a thumbnail once per photo per size and leaves it alone, and
restic's change detection is mtime and size, so the nightly delta is new photos
only. What it buys is a restore with no "wait for 22 GB of thumbnails to
regenerate" step.

**Two upstream details worth not rediscovering.** K8up's `RestServerSpec` field
is `passwordSecretReg`, not `...Ref` — an upstream typo, and the correct-looking
spelling is silently ignored. And `rest-server --append-only` refuses deletes for
every object type *except* `locks`, so restic's locking works normally while
`forget` still cannot run through it: the reasoning below holds, now with the
mechanism confirmed rather than assumed.

#### Every K8up backup route -- decided 2026-09-21

Four routes. Every snapshot's host is its **namespace** -- the backup Job's
`$HOSTNAME`, which the operator sets to it -- and each route has its own path
shape, which is what the coverage assertion keys on.

| Route | Captures | Path in the repository | Mechanism |
|---|---|---|---|
| **Volume** | a PVC's files as they are | `/data/<pvc-name>` | the PVC mounted read-only into K8up's backup Job, one Job per node |
| **Annotation** | a relational dump, run beside its server | `/<namespace>-<container><extension>` | `k8up.io/backupcommand` on the app's pod; K8up execs it and streams stdout to `restic --stdin` |
| **PreBackupPod** | a SQLite copy, taken by a pod of our own | `/<namespace>-<container><extension>` | a `PreBackupPod` mounting the PVC and carrying `sqlite3`; K8up starts it, execs it, removes it |
| **Excluded** | nothing | -- | `k8up.io/backup: "false"` on the PVC |

**Every PVC, and its route.** 22 in scope, 18 excluded -- the live split
verified on 2026-09-21. "Volume" alone means the file copy is sufficient:

| Namespace | PVC | Route | Why |
|---|---|---|---|
| apps | `headscale-data` | Volume + PreBackupPod | `noise_private.key` is a file; the dump is insurance for the node table |
| apps | `linkding-data` | Volume + PreBackupPod | the rows are the whole value |
| apps | `plex-config` | Volume + PreBackupPod | watch state; Plex's own 3-day copies ride the volume too |
| apps | `pinepods-database` | Volume + Annotation | PostgreSQL. The live datadir's file copy is only crash-consistent; the dump is what a restore uses |
| apps | `photoprism-database` | Volume + Annotation | MariaDB; same |
| apps | `salamander-database` | Volume + Annotation | MariaDB; same |
| apps | `rustdesk-data` | Volume | `id_ed25519` is a file; the peer table regenerates |
| apps | `syncthing-data` | Volume | the device ID *is* `key.pem` |
| apps | `photoprism-data`, `salamander-data` | Volume | sidecars, thumbnails, PhotoPrism's own SQL dumps -- whole, per the no-per-path-exclusions decision |
| apps | `rclone-dropbox-config` | Volume | holds the OAuth token, so not regenerable |
| apps | `stump-config`, `mumble-data`, `speedtest-tracker`, `youtube-dl`, `headplane-data`, `beets-library`, `beets-flask-config`, `filebot` | Volume | journal-mode SQLite or plain files; see the known limit below |
| apps | `pinepods-backups` | Volume | empty, made for a `pg_dump` that never ran; retire it once the annotation dump is proven |
| automatic-ripping-machine | `automatic-ripping-machine-pvc` | Volume | the step 6 canary, on worker-1 |
| infrastructure | `kube-prometheus-grafana` | Volume + PreBackupPod | its admin password is no longer in SOPS |
| apps | the twelve SMB claims, incl. `borg-backups` | Excluded | the ZFS layer and the repo host's own job cover them |
| apps | `borgmatic-data`, `rclone-dropbox-bisync-cache`, `pinepods-valkey` | Excluded | regenerable |
| infrastructure | Prometheus TSDB, Alertmanager, VictoriaLogs | Excluded | regenerable |

**The dump path format is verified four ways**, not assumed:

- **Source** at `v2.16.0`: `fmt.Sprintf("/%s-%s", hostname, pod.ContainerName)`,
  unchanged on `master`.
- **Upstream's own end-to-end test**, which restores
  `/k8up-e2e-subject-subject-container.txt`.
- **Issue #1068**, a user's real log and `restic snapshots` output showing
  `/internal-main.sql` under host `internal`.
- **The documentation**, which does not state it at all.

It gets confirmed in this cluster when step 5's first dump lands.

**The application goes in the extension.** #1068 describes our exact collision:
pinepods, photoprism and salamander all name their database container
`database`, so all three would write `/apps-database.sql` and rotate each other
out of retention. The workaround two users report there is taken here over a
ClusterIP Service per database (user's call, 2026-09-21). `k8up.io/file-extension`
is free text, so it can carry the application's name:

| Source | Route | Path |
|---|---|---|
| pinepods | Annotation | `/apps-database.pinepods.pgdump` |
| photoprism | Annotation | `/apps-database.photoprism.sql` |
| salamander | Annotation | `/apps-database.salamander.sql` |
| headscale | PreBackupPod | `/apps-sqlite.headscale.sqlite` |
| linkding | PreBackupPod | `/apps-sqlite.linkding.sqlite` |
| plex | PreBackupPod | `/apps-sqlite.plex.sqlite` |
| grafana | PreBackupPod | `/infrastructure-sqlite.grafana.sqlite` |

The PreBackupPods follow the same rule even though their container name is ours
to choose, so one parse serves the coverage assertion for both mechanisms. The
container says what produced a dump; the extension says whose it is.

**Why annotations for the relational three, and PreBackupPods for SQLite.** The
annotation runs `pg_dump` or `mariadb-dump` inside the database container, beside
its server and over its socket. That is the documented Application-Aware Backups
pattern, and it needs no address. A PreBackupPod is its own pod with its own
network namespace, so for those three it would have needed a Service per database
to reach them. The databases do listen on all addresses and no NetworkPolicy
applies in `apps` (measured 2026-09-21), so it would have worked -- but it is
more machinery for the same result.

The SQLite applications are the opposite case. headscale and rustdesk have no
shell, and linkding and stump have no `sqlite3`, so the tool has to arrive in a
pod of our own.

The blast radius also differs. Only PreBackupPods carry the all-or-nothing
readiness gate that can block a namespace's volume backups. A failed annotation
dump fails its own Job and leaves the volume Jobs running.

**How each dump is taken:**

- **pinepods:** `pg_dump -Fc -Z0`. Custom format so `pg_restore --list` can verify
  it in Phase 6's drill; `-Z0` because a compressed dump changes wholesale every
  run and restic could not deduplicate it.
- **photoprism, salamander:** `mariadb-dump --single-transaction`. InnoDB, so the
  read is consistent without locking. Plain SQL, which deduplicates well.
- **SQLite:** `sqlite3 <db> ".backup $f"`, then `cat`. The backup API
  copies pages, so it is consistent under concurrent writers, reads through the
  WAL, and is indifferent to Plex's custom ICU collations. `VACUUM INTO`, which
  rebuilds indexes, can trip on those. `.backup` needs a seekable destination,
  hence the file first. The result is a SQLite database, restored by putting it
  back in place.
- **Every command** runs `test -s` before its `cat`, so it exits non-zero on
  empty output. Otherwise a silently failing dump writes a zero-byte snapshot
  that satisfies the coverage assertion.
- **Every command's file comes from `mktemp`**, never a fixed path. K8up does
  not serialise Backups, so two can exec into one container at once. With a
  shared path, the later dump truncates the file the earlier one is still
  streaming, and the earlier one exits 0 with a partial dump. A harness
  reproduced that on 2026-09-21 as exactly half of a 20 MB dump, and the same
  harness streamed both runs whole under `mktemp`.

**Two per-application findings behind the table:**

- **Plex already backs up its own database** every three days
  (`library.db-2026-09-19` and so on), as PhotoPrism does. The volume backup
  captures those consistent copies. But it is a Plex setting, not declared here,
  so the dump is ours.
- **linkding's second database, `tasks.sqlite3`, is its Huey task queue**
  (`SqliteHuey`, `results: False`). Only `db.sqlite3` is dumped; the queue rides
  the volume backup.

**Cadence: two Schedules for each namespace with dumps.** A K8up Backup does
volumes and dumps together. But `labelSelectors` narrow all three things it picks
up -- PVCs, annotated pods and PreBackupPod templates. Verified at `v2.16.0` in
`fetchPVCs`, `fetchCandidatePods` and `fetchPreBackupPodTemplates`.

| Schedule | Namespace | UTC | Selects | Covers |
|---|---|---|---|---|
| full | `apps` | 01:00 | everything | every in-scope volume and all six dumps |
| dumps | `apps` | 07:00, 13:00, 19:00 | label `k8up-dump: "true"` | the six dumps; no volume is read |
| full | `infrastructure` | 01:30 | everything | grafana's volume and dump (the rest there is excluded) |
| dumps | `infrastructure` | 07:30, 13:30, 19:30 | the same label | grafana's dump |
| full | `automatic-ripping-machine` | 02:00 | everything | its one volume -- the step 6 canary |
| check | `apps` (on the full Schedule) | Sunday 03:00 | the repository | structural `restic check`; the only K8up `Check` |

Six-hourly dumps meet the 6-hour database RPO without re-reading ~56 GB of
volumes four times a day. The label goes on the three annotated pod templates
and on the four PreBackupPod objects, never on a PVC.

The full Schedules are staggered because K8up's exclusivity is per namespace:
three at once would be three restic writers on eight spindles. Everything stays
clear of 08:00 (mass files, on the repo host), 09:00 on the 1st (the prune, which
takes an exclusive lock) and 10:00 (vzdump).

**Where each piece lives** -- fleet policy central, exceptions with the
application:

- **Schedules, and the three namespaced repository Secrets:**
  `kubernetes/k8up/`. It has its own Flux Kustomization depending on
  `infrastructure`, and no `namespace:` line.
- **Annotations and the `k8up-dump` label:** each application's own manifests --
  `pinepods/deployment.yaml`, `photoprism/deployment.yaml`, and the photoprism
  chart's deployment template for salamander. Each is a pod-template change, so
  one rollout each, once.
- **PreBackupPods:** beside their application -- `kubernetes/apps/` for
  headscale, linkding and plex, and `kubernetes/infrastructure/` for grafana,
  where that directory's namespace transformer is exactly right.
- **Exclusions:** on the PVCs, where they already are.

**Retention and integrity stay off the cluster.** No Schedule carries a `Prune`,
since append-only refuses it. The repo host's monthly prune applies one policy
per host-and-path group, and every dump path is its own group.

**Decided 2026-09-21, user's call: one weekly K8up `Check`**, on the `apps`
full Schedule, Sunday 03:00 UTC -- after the night's backups, which the locker
makes it wait out anyway. The host's `--read-data-subset` pass stays in Phase 6.
The question was first framed as a lock
contention risk, which was wrong: K8up already prevents it (below). What a K8up
`Check` is, read from the source at `v2.16.0`:

- **Structural only, always.** `CheckSpec` has no field for check options, and
  the wrapper runs a bare `restic check` plus global flags. So it can never pass
  `--read-data` or `--read-data-subset`. It verifies the index, the pack list
  and that every snapshot's trees resolve -- never the bytes inside the packs.
  restic's own help says the same.
- **Coordinated with every K8up job on the repository, cluster-wide.** Check,
  Prune and Restore are *exclusive*. The operator's locker lists running Jobs by
  a hash of the repository string, with no namespace filter, and runs an
  exclusive job only when none is active. A Backup, in turn, will not start
  while one runs. A Check that is turned away waits, retrying every 30 s; it
  does not fail.
- **The repository string must therefore be byte-identical in every
  Schedule.** Otherwise the hashes differ and the namespaces stop seeing each
  other's jobs.
- **Blind to the repo host's own jobs.** The nightly mass-file backup and the
  monthly prune are not K8up Jobs. `check` takes an exclusive restic lock and
  exits 11 if the repository is already locked, and K8up never passes
  `--retry-lock`, so a clash fails at once. Timing, not the locker, keeps a
  K8up `Check` clear of 08:00 and of 09:00 on the 1st.
- **Reported through** the `Check` object's conditions and the operator's
  `k8up_jobs_{total,successful,failed}_counter` and
  `k8up_schedule_last_job_succeeded`. The absence-of-success rule would be
  "no successful check in 8 days", with the usual caveat that a check which has
  never run leaves no series to alert on.
- **One is enough.** There is one repository, so a `Check` on each of the three
  Schedules would be three identical checks, run one after another.

So the two checks are complementary rather than alternatives. A weekly K8up
`Check` is structural, coordinated, and exercises the served read path from the
cluster. The repo host's `restic check --read-data-subset` is the only one that
can catch damaged data, and belongs to Phase 6 unless brought forward.

**Restoring, by route** -- the runbook's spine, written now so Phase 6 starts
from it. `latest` must be narrowed with `--host` and `--path`, or it names the
newest snapshot in the whole repository, which will not contain the file.

- **Volume:** a K8up `Restore` into a scratch PVC or the original, or
  `restic restore latest --host <ns> --path /data/<pvc>` on the repo host.
- **Relational:** `restic dump --host apps --path <path> latest <path>`, piped
  into `pg_restore` or `mariadb`.
- **SQLite:** the same `restic dump` into a file. Scale the application to zero,
  put the file in place, and remove its `-wal` and `-shm`.

Six databases get file-level treatment and no dump — `stump`, `mumble`,
`speedtest-tracker`, `youtube-dl`, `headplane`, `beets`. All are journal-mode
rather than WAL with negligible write rates, so a file copy is very likely
consistent; "very likely" is the honest word, and `beets` could be torn
mid-import. A known limit, not an oversight.

#### The NixOS host, and the risk that gates it

`nixos/` does not exist yet, so this is the first NixOS machine and the first
colmena host. `services.restic.server` carries what is needed — `appendOnly`,
`privateRepos`, `htpasswd-file`, `prometheus`, and a dedicated `restic` user —
with one trap: it uses systemd socket activation, so `listenAddress` takes a port
only and a `host:port` value trips an assertion. sops-nix replaces the `.env` +
`lookup('env', ...)` pattern Ansible uses on vulcanus, which is a net improvement
since those healthchecks URLs are currently plaintext in a gitignored file.

**`nixos-rebuild` inside a Proxmox LXC is reported to succeed while applying
nothing.** If that holds here, colmena cannot manage this host and the OS decision
unwinds. So the first thing built is not the repository: it is a throwaway
container, a trivial colmena deploy, and a check that the change took effect
*inside* the container. Only then does anything else get built on it. If it
fails, fall back to Ubuntu + Ansible and revisit NixOS with the fileserver.

Pin colmena explicitly, and say in a comment whether it tracks the nixpkgs
package or a `main` revision. An untagged dependency on the host holding every
application backup is a standing obligation worth naming rather than inheriting.

##### What the spike found, 2026-09-20

**It applies.** A throwaway unprivileged LXC was created from the flake's own
template, given a config declaring a different address, and switched: the
interface moved from `192.168.0.199` to `192.168.0.108`, the route, resolver and
unit set followed, nothing was left failed. The reported "succeeds but applies
nothing" failure did not reproduce. **The NixOS decision stands**, and the
container was destroyed afterwards -- before the 04:00 vzdump, so it never
became a PBS group.

Three things had to be got right first, none of them obvious, and all three
present as *the container simply has no network*:

- **Proxmox writes no network configuration into an `unmanaged` container**,
  which is what a NixOS container is. At the LXC module's defaults `eth0` comes
  up DOWN with no address and nothing ever fixes it.
- **`proxmoxLXC.manageNetwork = true` does not hand networking over — it turns
  the module's networking block off entirely.** `systemd.network.enable` has to
  be asked for separately, or a `systemd.network.networks` entry is inert and
  `systemd-networkd.service` does not exist at all.
- **Enabling networkd pulls in systemd-resolved**, which asserts against the
  `networking.useHostResolvConf` that every container config defaults to on.
  It is `mkDefault`, so it is overridable; nothing here needs a caching resolver.

**The hostname goes the other way.** Proxmox does set it, from `--hostname` at
creation, and it wins: a switch writes `/etc/hostname` but does not rename the
running UTS namespace, so the two disagree until the container restarts. The
value in `networking.hostName` must therefore match the one Terraform creates the
container with. Left at `manageHostName = false` the module forces
`networking.hostName` to `""`, which also names the system derivation
`nixos-system-unnamed-...`.

**colmena cannot reach the container from a roaming workstation, and this is not
a colmena problem.** `docs/tailnet.md` says it plainly -- *"advertising a route
is not granting access to it"* -- and the policy grants `will@` a list of
`host:port` pairs, of which `192.168.0.105:22` is the only container. With sshd
up and the right key installed, `192.168.0.108:22` timed out from the tailnet
exactly as that predicts. **Deploying from off-LAN needs a grant added to
`kubernetes/apps/headscale/policy-config-map.sops.yaml`**, alongside the
fileserver's, and a row in `docs/tailnet.md`. That is an access-control change
and it is the user's call, so it is recorded here rather than made.

#### What step 3 built, and measured -- 2026-09-21

The repository host is LXC 108, `restic-repository`, on NixOS. It serves
`rest-server --append-only` on 8000, and runs two jobs of its own against the
local path:

- `restic-backups-massfiles`, nightly at 02:00 local.
- `restic-prune`, at 03:00 on the 1st of each month.

Each reports to healthchecks.io from `ExecStopPost` with the `hc-report` contract.

What was checked rather than assumed:

- **The append-only boundary, both ways.** `forget` through the served URL is
  refused with `403 Forbidden`; the same `forget` against the local path
  succeeds. An earlier attempt at this check was itself broken: `| tail` hid the
  exit code, and the flags were invalid, so both paths failed for one unrelated
  reason and it read as a pass.
- **Read access to what it backs up.** Nothing is unreadable as the `restic`
  user in photos (62,026 entries) or filesync (57,448). A dry-run of `books`
  processes 985 files and exits 0.
- **The prune path, end to end.** Run for real against the empty repository, it
  succeeded, and systemd recorded its ping command at `status=0`.
- **The rendered units, not the Nix.** The massfiles unit carries two
  `ExecStopPost` lines: the module's own `postStop` cleanup, then the report.

**Two figures the next steps need:**

- **The dry-run's rate was ~55 MiB/s** (1.54 GiB in 28 s), and that is without
  encrypting or writing anything. The first mass-file run reads ~300 GB, so it
  takes two hours or more. Started at 02:00, it would still be running when
  vzdump begins at 04:00. Every run after it is a small delta.
- **The container runs restic 0.19.1; the K8up operator bundles 0.19.0**
  (`go.mod` at `v2.16.0`). Compatible: the repository reports format version 2,
  which every restic since 0.14 reads and writes identically, and restic never
  changes a repository's format without an explicit `restic migrate`.

#### The first mass-file run -- 2026-09-21, 15:45-17:54 UTC

Started by hand in the afternoon rather than left to the 02:00 timer. That was
the plan's own rule for a first big run, and the dry-run's rate put a 02:00 start
still running at the 04:00 vzdump.

| | |
|---|---|
| Processed | 296.58 GiB, 109,969 files, in 2 h 08 m 46 s |
| Added | 251.47 GiB unique -- **45.1 GiB was duplicate content** across the three datasets, stored once |
| Stored | 247.59 GiB; restic's compression saved only 1.5%, as expected for JPEG |
| Result | success; snapshot `82daf60d`; completion ping `status=0`; `restic check` clean in 4.9 s |

**etcd, against the same window a day earlier:** mean WAL fsync p99 0.651 s
against 0.245 s, and peak 1.912 s against 0.402 s. `etcdHighFsyncDurations`
reached **critical**, and its warning and `etcdHighCommitDurations` fired too.
**No container anywhere restarted.** For proportion, vzdump nights reach
3.8-8.7 s. Every later run is a delta, so this is the heaviest read the job will
ever make; the 02:00 run on 2026-09-22 is its first ordinary night, and its
duration and added size are the figures to check.

**`/proc/<pid>/io` does not measure a restic backup's progress.** The progress
monitor used `read_bytes`, and it overshot the data's logical size -- as did
`rchar`, which reached 617 GiB against 296 GiB of source. restic buffers each
pack in a temporary file, then reads it back to copy it into the repository and
to verify it, so both counters include restic's own traffic. ETAs built on them
were wrong in both directions. The repository's size against the source's
per-inode `du` is the measure that means something.

Two suspects for the overshoot were ruled out on the way, both worth knowing:
`snapdir` is `hidden` on all three datasets, so restic never walks
`.zfs/snapshot`, and none of them contains a single hard-linked file.

**`restic check` takes an exclusive lock** in 0.19. Harmless at 5 seconds, but
Phase 6's weekly check must not overlap a backup.

#### Step 4, the operator -- 2026-09-21

Installed with no Schedules and verified on the live objects, not the manifests:
`Recreate` on the Deployment (Flux applied the post-renderer),
`BACKUP_ENABLE_LEADER_ELECTION=false` in the running pod, **no K8up Lease in the
cluster at all**, nine CRDs, the chart's `k8up-cleanup` hook run and removed, and
`up{job="k8up-metrics"} = 1`. The operator landed on worker-1.

**A version label corrected.** This spec cited K8up source as "v4.10.0". That
is the Helm chart's tag. The chart deploys operator image `v2.16.0`, built from a
different commit, two behind. Those two commits touch only `Chart.yaml`,
`README.md` and `values.yaml`, so every finding here holds for the code that
runs.

#### Step 5 was not what the plan said it was

The plan had the relational PreBackupPods "connect over the pod's own loopback".
They cannot: a PreBackupPod has its own network namespace. Two ways through were
weighed -- a ClusterIP Service per database, or `backupcommand` annotations with
the file-extension workaround from #1068 -- and the annotations were chosen (user's
call, 2026-09-21). The result is recorded under *Every K8up backup route*.

#### Step 5, deployed -- 2026-09-21

Two one-off `Backup` objects, one each in `apps` and `infrastructure`, with the
dumps-only label selector and `runAsUser: 0`, applied by hand rather than
committed. Each ran only its `prebackup` Job. The selector kept every volume Job
out, as `fetchPVCs` said it would. The dumps ran in pod-name order, and each
landed in the store at its own path:

| Path | Bytes | Content |
|---|---|---|
| `/apps-database.pinepods.pgdump` | 18,362,976 | `PGDMP` |
| `/apps-database.photoprism.sql` | 72,829,425 | ends `-- Dump completed on 2026-09-21` |
| `/apps-database.salamander.sql` | 203,273,294 | ends `-- Dump completed on 2026-09-21` |
| `/apps-sqlite.headscale.sqlite` | 90,112 | `SQLite format 3` |
| `/apps-sqlite.linkding.sqlite` | 1,150,976 | `SQLite format 3` |
| `/apps-sqlite.plex.sqlite` | 88,185,856 | `SQLite format 3` |
| `/infrastructure-sqlite.grafana.sqlite` | 4,161,536 | `SQLite format 3` |

The bytes and headers were read back on the repo host with the restore spine's
own `restic dump --host <ns> --path <p> latest <p>`, so that command is proven,
not just written. Every snapshot carries restic 0.19.0's `summary`, which the
coverage assertion's emptiness check needs.

**Deduplication, measured.** A second run minutes later added 308 B for
headscale, 443 B for linkding, 11 KB for Plex's 88 MB, 942 KB for photoprism's
72.8 MB and 953 KB for pinepods' 18.4 MB. So each run of the uncompressed
dumps costs the repository what changed since the last run, not the dumps'
size.

**salamander did not roll on the first deploy.** Its HelmRelease takes the chart
from Git under Flux's default `reconcileStrategy: ChartVersion`, which
repackages only when `Chart.yaml`'s version moves. The annotations went in
without a bump. So the HelmRelease stayed Ready on artifact `1.10`, and the pod
from 2026-09-19 carried no `backupcommand`. The first dumps run simply did not
list it. The `1.11` bump fixed it, and the second run took salamander's dump.
Any change to `kubernetes/charts/photoprism/templates/` needs the same bump.
This chart has hit it once before.

**The operator's metrics, as scraped.** Only `k8up_jobs_total` and
`k8up_jobs_successful_counter` have series so far. `k8up_jobs_failed_counter`
appears only after a first failure, and `k8up_schedule_last_job_succeeded`
only once a Schedule exists. The Backup's namespace is in
**`exported_namespace`**, because the scrape's own `namespace` label,
`infrastructure`, where the operator runs, takes the plain name. Step 8's rules
must group by `exported_namespace`.

**Every item logs an `ERROR`:** `prometheus send failed` to
`http://127.0.0.1/`. That is the operator's default `BACKUP_PROMURL`, a
Pushgateway address. Nothing here listens on it, and the push is harmless to the
backup. `SendPrometheus` skips an empty URL, but a Backup's `promURL: ""` falls
back to the operator default. So only the operator-wide setting can turn it off.

A Pushgateway would not be worth running for these metrics:

- **It would keep one item per namespace.** K8up pushes after every volume and
  every dump, with `Add()`, an HTTP `POST`, under the grouping key `job`,
  `instance` (the namespace) and `cluster`. The PVC is only a label. The
  Pushgateway's documented rule is that a `POST` replaces every metric of the
  same name under the same grouping key. Reproduced against Pushgateway
  1.11.3 with K8up's exact grouping path: a push for linkding removed
  headscale's series from `/metrics` outright, while the same two pushes
  with the item in the grouping key kept both. Items finish seconds apart
  and Prometheus here scrapes every 30 s, so most items would never be
  scraped at all. What survives each night is whichever PVC or dump pushed
  last.
- **Everything else it carries is already in the store.** The pushed values are
  files and directories new, changed and unmodified per item. The snapshot
  `summary` holds all of that per PVC and per dump, with history, and the
  coverage assertion reads it there.
- **The one number unique to the push is `last_errors`,** restic's count of
  unreadable files: the exit-3 case from #1032. Overwritten per namespace, it
  would be unreliable exactly when it matters, since a clean push from the next
  PVC erases a failed one. Otherwise that count exists only in the Job's own log,
  on restic's `backup finished` line for each item. Nothing here alerts on logs.

#### Checked against K8up #910 and #1032 -- 2026-09-21

Two upstream issues about backups reported as succeeded when they were not, read
against the `v2.16.0` source and measured where the cluster could answer.
[#1032](https://github.com/k8up-io/k8up/issues/1032), still open, changes
steps 6 to 8. [#910](https://github.com/k8up-io/k8up/issues/910), fixed, leaves
the dumps as they are, for better reasons than the ones first given.

**#1032: a volume the Job cannot read is backed up as empty, and reported
Succeeded.** restic exits 3 when it saves a snapshot but could not read some
files, and `restic/cli/command.go` treats exit 3 as success. The count of
unreadable files goes only to a webhook (`statsURL`) or a Pushgateway
(`promURL`), and neither is configured here. The Job runs as the image's
`USER 65532`, group 0, unless the Schedule sets a `podSecurityContext`. The
chart's own `podSecurityContext` value belongs to the operator's pod, not the
Jobs'.

Measured by mounting all 22 in-scope PVCs read-only, as the Job does, and
walking each one as 65532:0 with no supplementary groups. 13 of the 22 hold
something that identity cannot read:

| PVC | What 65532 cannot read |
|---|---|
| `syncthing-data` | **the whole volume**: its root is `0700` uid 1000, so all 24 entries, 140 MB, device identity included. #1032's own case |
| `headscale-data` | **`noise_private.key`** (`0600` root), the one file the table above calls irreplaceable |
| `photoprism-data`, `salamander-data` | `config/keys/signing.key` and PhotoPrism's own `backup/mysql/*.sql` (`0600` root): 218 MB and 610 MB |
| `pinepods-database` | `pgdata/` (`0700` uid 999): 1,570 entries, 90 MB |
| `photoprism-database`, `salamander-database` | the MariaDB datadir's files and schema directories: 453 MB and 688 MB |
| `plex-config` | `cert-v2.p12`, `.LocalAdminToken` and nine others (`0600` uid 1000) |
| `kube-prometheus-grafana` | `grafana.db` (`0660` 472:472) and the `png`, `csv` and `pdf` directories |
| `headplane-data` | `agent/tailscaled.state`, the agent node's identity, and four related files |
| `rustdesk-data` | `.config/rustdesk/RustDesk.toml` |
| `speedtest-tracker` | `keys/cert.key`, `log/logrotate.status` |
| `filebot` | `filebot/` and two root dotfiles |

Fully readable: `automatic-ripping-machine`, both beets PVCs, `linkding-data`,
`mumble-data`, `pinepods-backups`, the rclone config, `stump-config`,
`youtube-dl`. Under the defaults, the `apps` Schedule would have reported
Succeeded every night while missing most of the material this phase exists to
protect.

**What follows, for step 6 onward:**

- **Every Schedule, Backup and Restore sets `podSecurityContext: {runAsUser: 0}`.**
  Root reads all of it. The Job container has no `securityContext` of its own,
  so it keeps the runtime's default capabilities, `CAP_DAC_OVERRIDE` among them,
  and the probe walked as root every path 65532 could not. `apps` carries no
  pod-security labels, and the cluster default admitted the probe as root; the
  other two namespaces enforce `privileged`. A Restore needs the same, or every
  restored file belongs to 65532.
- **Never `fsGroup`**, the other half of the workaround offered in #910. K8up
  marks the container's mount read-only but not the volume source. For a `local`
  volume, kubelet re-owns the whole tree when no other pod has it mounted
  (`pkg/volume/local/local.go`: "Volume owner will be written only once on the
  first volume mount"). So a backup that ran while an application was scaled
  down would recursively change its data's group and add group write, and
  PostgreSQL refuses to start on a group-writable data directory.
- **Step 6's canary cannot catch this**: the ARM volume is fully readable as
  65532. The first `apps` run carries the check instead. `restic ls` must show
  `noise_private.key` under headscale and `key.pem` under syncthing, and
  syncthing's volume snapshot must hold its 24 entries.
- **The coverage assertion (step 8) gains an emptiness check.** Even as root,
  exit 3 can still happen: a file deleted mid-scan, or an I/O error. The store
  keeps no error count, but restic has written a `summary` into each snapshot
  since 0.17, carrying `total_files_processed` and `total_bytes_processed`. So
  the assertion flags a volume snapshot with no files, or one that shrank
  sharply from its predecessor. That is option 1 from #1032, done from the
  store, and it is what the whole-volume case would have produced. A handful of
  skipped files stays invisible: a known limit.

**#910: a failed `backupcommand` was saved and reported Succeeded.** It was
fixed in [#1027](https://github.com/k8up-io/k8up/pull/1027), released in
`v2.11.2`. The fix is the `os.Exit(1)` in `pod_exec.go` already cited, so the
running operator has it. Its end-to-end test asserts only that the Backup is
marked Failed, and uses a command that prints nothing. So upstream has never
tested whether a snapshot of partial output survives.

**A rationale corrected.** Commit `830e2723` justified writing dumps to a file
first because "a dump that died halfway could otherwise be saved as a
plausible-looking snapshot." That is true of the code and overstates the odds.
`/usr/local/bin/k8up restic` is PID 1 in the Job, so its exit takes restic down
with the container. Between `Close()` and the exit sits one log call. In that
window restic would still have to upload its last pack, its index and the
snapshot file to the rest-server. It almost certainly loses. That is reasoned
from the source, not measured. The file comes first anyway, for reasons that do
not depend on the race:

- **Any failure streams nothing.** Even a lost race leaves, at worst, a
  zero-byte snapshot that the size check sees, never a plausible prefix. That
  holds whatever K8up's exit path becomes. #910's other proposal, deleting the
  snapshot when the command fails, can never work here, because the rest-server
  is append-only. Prevention at the source is this repository's only option.
- **`test -s` catches what #1027 cannot see**: a command that exits 0 having
  written nothing. `sqlite3` pointed at a wrong path is the realistic case.
- **The database is held only as long as a local write.** Streamed directly,
  the dump runs at restic's pace, through the apiserver and kubelet, on nights
  the spindles are busiest. For that whole time, `--single-transaction` holds
  MariaDB's read view open, and `pg_dump` holds a lock on every table that
  blocks an application's schema migration.

Its cost is the dump sitting in the container's writable layer until it has
been streamed: 203 MB at most (salamander), 88 MB for Plex's `library.db`. None
of the dumping containers has an ephemeral-storage limit or a memory-backed
`/tmp` (checked 2026-09-21). The one failure it cannot cover is the stream
breaking during `cat`, after a good dump, from an apiserver or kubelet restart.
For that there is only #1027's exit, racing restic, and behind it the size
check. Testing the commands showed what such a break looks like. A `kubectl
exec` of Plex's dump from the workstation had its connection reset on the
tailnet path, with no apiserver restart, after 86.7 of 88.2 MB. The result
started with a valid `SQLite format 3` header, and only the exit status said it
was short. The retry matched the live database byte for byte.

**#1027 also gave every backup Job a `backoffLimit` of 6**
(`BACKUP_GLOBAL_BACKOFF_LIMIT`). The dumps run in their own `prebackup` Job and
stop at the first failure. So a dump that fails every time runs that Job seven
times, and each attempt redoes every dump listed before it and none after. The
volume Jobs carry `SKIP_PREBACKUP` and are unaffected.

#### Escrow, including what Phase 0 left open -- done 2026-09-21

Escrowed by the user into the password manager, before the repository held any
data:

- **The restic repository passphrase**, with the REST password beside it. The
  passphrase is what matters. The REST password protects no data and can be
  regenerated, so its entry is a convenience.
- **The Talos machine secrets**, closing the item Phase 0 recorded as *"Still
  outstanding"*. Taken from the running control plane rather than from tfstate:

  ```
  talosctl -n 192.168.0.190 get machineconfig v1alpha1 -o jsonpath='{.spec}' \
    | talosctl gen secrets --from-controlplane-config /dev/stdin -o /dev/shm/talos-secrets.yaml
  ```

  This uses talosctl's own converter rather than a hand-written reshape of the
  provider's field names. All 14 fields were checked equal, by hash, to tfstate's
  `talos_machine_secrets.main`; the one field present only in state,
  `secretboxencryptionsecret`, is empty. It writes to `/dev/shm` so the plaintext
  never lands on the workstation's unencrypted disk.
- **Not the `.talosconfig` -- deliberately; user's call, 2026-09-21.** It is
  derived from the secrets: its certificate is signed by the OS CA inside them.
  It also expires 2026-11-07, while the CAs run to 2032-11-03. This was read back,
  not assumed: a talosconfig minted from the secrets with
  `talosctl gen config --with-secrets` was accepted by the control plane. The
  expiry is a small problem of its own, specced in
  [`talosconfig-renewal.md`](talosconfig-renewal.md).

With the secrets escrowed, the failure-domain table's reason for giving guest
images two copies rather than three -- that they are "reconstructible from
`terraform` plus `talosctl`" -- holds for the first time.

**Why escrow a passphrase SOPS already holds.** The question was asked directly,
and the first answer overstated the case. Before the escrow, the passphrase was
already recoverable: Phase 0 escrowed the Flux age key, which opens
`kubernetes/k8up/secret-*.sops.yaml`. The direct entry buys two things.

- **The shortest chain.** A restore needs the passphrase and the backup media,
  rather than a repo copy, sops, a key, and knowing which field to read.
- **Independence from recipient drift -- and that drift is not hypothetical.**
  The NixOS rule added in this phase left the NixOS secrets file as the one file
  in 33 that the Flux key could not open. That silently made Phase 0's "any one
  private half opens every file" untrue. The rule carries `*flux` again, applied
  with `sops updatekeys`.

Phase 5's acceptance test -- restore using only the passphrase -- depends on the
direct entry.

**How the credentials were generated, and where they could have leaked.**
`secrets.token_urlsafe` (the kernel's CSPRNG) for both passwords, and bcrypt via
`htpasswd -nbB`. All inside one shell with `umask 077`, written straight into the
files and encrypted with `sops -e -i` seconds later; only lengths were ever
printed. It took five generations. Only the fifth is live, and none of the others
ever protected data.

Checked 2026-09-21 by streaming every candidate location to the container and
searching against `/run/secrets` there, so only counts came back. Result: 0 hits
in 144 MB -- every transcript in the project, the scratch space, memory, and
every git object including unreachable ones -- with positive controls confirming
the search could find a hit. There is no audit logging and there are no snapshots
on the workstation.

**The residue is freed blocks.** The plaintext existed for seconds on
`rpool/home`, which is unencrypted and copy-on-write, so freed blocks may still
hold it. They are recoverable only by raw-disk forensics. **Generate any future
secret into `/dev/shm`**, so it never touches that disk.

#### The coverage assertion

Reads **the store**, not K8up's metrics, which carry no `pvc` label and no
last-success timestamp. Grouping is `(host, paths[0])`: `HOSTNAME` is set to the
namespace, so per-PVC identity lives entirely in `paths` — `/data/<pvc-name>` for
a volume, `/<namespace>-<containerName>` for a dump.

It **enumerates from the cluster API, cluster-wide** — not from git, and not from
the namespaces that happen to have a Schedule. Six live PVCs have no literal
declaration in git (salamander's two, the three StatefulSet `volumeClaimTemplates`
and grafana), so a git-derived list is wrong on arrival; and a per-namespace walk
cannot see a namespace created later, which is the "a target that is never created
cannot alert as down" trap again. It reads the `k8up.io/backup` annotation itself
so an excluded PVC reads as a coverage *statement* rather than a failure, and it
reports a PVC that is **newly in scope**, since the opt-out default means a new
SMB share joins the backup set silently.

It cannot assert validity, only freshness and existence — a zero-byte dump would
pass until Phase 6's restore drill. Hence the non-zero-exit-on-empty rule above,
and comparing each dump snapshot's *size* against its previous run rather than
only its age. Follow `ansible/files/pbs_freshness.py`: a pure-function classifier
with a stdlib `unittest` suite beside it.

#### Two things this phase does not close

- **No restore runbook until Phase 6.** The capability arrives here, the procedure
  does not — and the sequencing hazard is specific: on a rebuilt cluster Flux
  reconciles applications against freshly provisioned *empty* PVCs and databases
  initialise before any restore lands, so the Kustomizations must be suspended
  first.
- **Offsite is step 10, not a property of the phase.** Until it lands the
  repository sits on `rpool`, the same eight spindles as the originals — so the
  failure-domain table's "app state, domain 2 = mini-nas (restic)" is not true
  until then. The converse dependency is real and this spec states it backwards:
  **Phase 4's `rpool/data` retirement is gated on step 10**, because until the
  restic replica exists `rpool/data/vm-910-disk-1` is the only thing carrying PVC
  data offsite.

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
does not verify chunks on arrival.** Add the verify job the primary datastore also
lacks — but **not a prune job on PBS #2**: see *Retention, and what ends it*, which has
it sync with `remove-vanished` and no prune of its own, so it tracks the primary exactly
and a retired guest's frozen group is preserved rather than expired.

#### Retiring `rpool/data` from replication — except the PBS appliance

Guest images leave ZFS for PBS, with **one exception that stays replicated**:
`rpool/data/vm-107-disk-0`, the Proxmox Backup Server VM's own OS disk. Decided
2026-09-19, user's call.

**Why the exception.** PBS #2 gives the *chunks* an offsite twin, but nothing gives
PBS #1's own configuration one. `/etc/proxmox-backup` — datastore definitions, users,
ACLs, prune/GC/verify job schedules, and the TLS certificate whose fingerprint is
pinned in vulcanus's `/etc/pve/storage.cfg` — lives on that OS disk. Retiring
`rpool/data` wholesale leaves it as the only thing in the estate with one copy that a
reinstall cannot reconstruct; everything else ends this phase with two. Keeping the one
dataset costs ~18 GiB against a 19.1 TiB pool, needs no new mechanism, and preserves a
`zfs send`-back restore that skips the manual install
`terraform/modules/proxmox_backup_server/main.tf` still requires ("complete the install
manually via the booted GUI").

**What it does not buy.** If `rpool` is lost, recovery still goes *through PBS #2* — it
is a working PBS holding every backup, and reading its datastore never needs the
primary's config. The exception buys a faster rebuild of the primary, not the backups
themselves. It is cheap insurance against rebuild friction, not a second lineage.

Four changes that must land together, because each one alone breaks the freshness
check:

- **Narrow the `vulcanus-data` syncoid command** rather than dropping it. `source`
  becomes `rpool/data/vm-107-disk-0`, `target`
  `rpool/foreign-backups/vulcanus/data/vm-107-disk-0`, and `recursive = false` — a zvol
  has no children. **No re-seed:** the target already exists and shares a snapshot
  lineage with the source, so syncoid continues incrementally.

  Keep the command's *name*. `checkNameFor` derives the healthchecks check from the
  unit name, so renaming it to match its narrower scope costs a new check and a new
  sops secret for no functional gain. A comment at the command carries the scope
  instead.

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

Afterwards PBS is the only lineage holding **guest images**, which is what makes the
two-year cliff the whole of *Retention, and what ends it* rather than half of it: there
is no longer a ZFS copy of a guest whose indefinite retention would make the shorter
policy fiction. `vm-107-disk-0` is not a guest image — it is the appliance that stores
them — so it sits outside the cliff and keeps `rpool/data`'s ordinary sanoid policy.

#### Deleted guests expire at two years

Decided 2026-09-18, and the automatic half of *Retention, and what ends it*: a group
whose newest backup is older than two years is forgotten entirely. No ladder, no
thinning — user's call: *"keep it simple with just a two-year expiration of the whole
group."*

Thinning would not pay for itself in any case. PBS dedups at 37x and a dead guest's 31
backups are one full plus thirty deltas of a machine that stopped changing, so the
deltas are nearly free. All of the space is in the cliff.

**The predicate carries no liveness question:**

> Forget any group whose newest backup is older than two years.

A live, in-scope guest cannot have a two-year-old newest backup — if one does, that is
a failure the PBS freshness assertion already reports. So the job never asks whether a
guest still exists: no source list, no SSH, and none of the false-positive path that
sank Phase 1's quarantine. A live but *excluded* guest falls under the same rule
deliberately; a two-year-old backup of a running machine is not one anyone restores.

**It runs against the primary only.** PBS #2 syncs with `remove-vanished`, so a
forgotten group propagates on the next sync and both stores stay identical from one
policy in one place. That is the `remove-vanished` residual risk above working as
intended rather than against us.

**Why it lands in this phase and not in Phase 1.** Until `vulcanus-data` is retired
from syncoid — which happens here — mini-nas still holds guest zvols that no age rule
expires: sanoid has no age term, and `replica-shallow` carries only dailies, so there
is no ladder to decay through. A two-year PBS policy shipped before that is fiction of
exactly the kind *Retention, and what ends it* warns against, because the effective
retention would be the longer of the two, which is forever. The two land together so
the figure is true when it is written down.

Nothing expires before March 2028 in any case — `vm/200` is the oldest frozen group
and was last written in April 2026 — so the wait costs nothing.

**If this phase slips past that.** The fallback is the symmetric job on mini-nas:
destroy a replica dataset whose newest snapshot is older than two years, same age-only
predicate, same absence of a liveness question. Not built now, because it is work for a
lineage this phase retires.

**PBS has no native job for this** — prune jobs thin, they do not forget — so it is a
timer and a script: list groups, compare the newest backup's timestamp, forget the
group. The GUI forgets a group in one action, so the capability is certain; the CLI or
API call that does it is not yet confirmed and should be before this is built.

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
- Freshness assertions per the table above — **the PBS one already exists**, built in
  Phase 1; what remains is the restic layer and the external disk.
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
  twice, not once. And the dumps restore rather than merely exist: `pg_restore
  --list` parses, the MariaDB dumps load into a scratch database, each SQLite dump
  returns `ok` from `PRAGMA integrity_check`.

  The offsite leg has its own: `restic check` against the mini-nas replica with
  `--no-lock` and against `.zfs/snapshot/<latest>/` rather than the live dataset, or
  it verifies a mid-receive state. That is the property that justified a dataset over
  a zvol in the first place.
- **Phase 2b** — `zfs list rpool/backups`.
- **Phase 3** — vzdump task-log **duration** for VM 910 before and after, *and* the
  net change in `avg_over_time` of etcd fsync p99 across matched 24 h windows — not
  just the backup window, because Phase 2 added load elsewhere. `transferred` reports
  logical device size and is not the measure. The `etcdHighFsyncDurations` silence
  expires 2026-09-17, the natural checkpoint.
- **Phase 4** — a verify job passing on the mini-nas datastore, and a test restore of
  one guest **from the offsite copy**. `zfs-replication-freshness` stays green across
  the `rpool/data` retirement, which is what says the source-root trim and the replica
  destroy landed together rather than one without the other. Green is not enough on its
  own here, because the narrowed command must still be *doing* something: assert that
  `rpool/foreign-backups/vulcanus/data/vm-107-disk-0` gains a snapshot newer than the
  retirement, and that it is the only dataset left under `.../vulcanus/data`. A syncoid
  command narrowed to a dataset that stopped receiving would pass the source-root
  comparison and fail nothing until the 26 h limit caught it the next morning. The two-year expiry is
  asserted by running it
  with the threshold lowered until it selects a known frozen group and nothing else,
  because at two years it correctly names nothing and a job that has only ever matched
  nothing is untested.
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

---

## Prompt to open with

> Read `todos/backups.md`. It is the live spec for the `backups` project and carries
> a status table near the top saying which phases are done. Work is on the `backups`
> branch in `.worktrees/backups`, with its review at `review/backups-base`. Pick up
> at the first phase not marked done, and update the status table plus the verified
> facts as you go — this spec is the handover between sessions, so leave it able to
> start the next one cold. `wt review-open`, `wt deploy` and `wt review-close` push
> and must be run by the user, not by you.
