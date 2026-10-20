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
at the 2b boundary on 2026-10-13, and
[PR #19](https://github.com/DArtagan/vulcanus-proxmox/pull/19) at Phase 3's on
2026-10-16. Phase 4 is on a fresh `backups` branch from `main`, in
`.worktrees/backups`; its review opens with the first commit. mini-nas's side of it
is on a `backups` branch of that repository, under a review of its own.

## Status

| Phase | What | State |
|---|---|---|
| A | Record the spec, open the review | done 2026-09-01 |
| 0 | Stop the bleeding — replication, retention, scrub | done 2026-09-03 — [PR #3](https://github.com/DArtagan/vulcanus-proxmox/pull/3) |
| 1 | Reclaim — dead guests, orphans | done 2026-09-18 — [PR #11](https://github.com/DArtagan/vulcanus-proxmox/pull/11) |
| 2 | Application backups — K8up + restic | done 2026-10-12 — [PR #13](https://github.com/DArtagan/vulcanus-proxmox/pull/13) |
| 2b | Delete the borg tree | done 2026-10-13 — [PR #17](https://github.com/DArtagan/vulcanus-proxmox/pull/17) |
| 3 | Performance — drop the OpenEBS disks from vzdump | applied 2026-10-16, review closed; checks B-E outstanding, carried by the user |
| 4a | PBS #2 on mini-nas, NixOS container, pulling | in progress — form changed 2026-10-18; stack proven in a VM test, nothing deployed |
| 4b | The primary into a NixOS container on vulcanus | designed 2026-10-17; after 4a |
| 5 | The offline copy — external disk | not started; needs a ~$200 purchase |
| 6 | Verification, notification, runbook | not started |

What phases 0 to 2b built is in [`docs/backups.md`](../docs/backups.md): the layers,
retention, the restic repository, K8up's schedules and dumps, restoring, reporting,
capacity and the operational notes. The repository host is in
[`docs/nixos.md`](../docs/nixos.md).

**One purchase gates a later phase:** one 14-16 TB external disk for Phase 5. The 2x
4 TB for the mini-nas vdev expansion gate nothing: measured 2026-10-16, Phase 4 fits
the pool as it stands. The expansion is still wanted for growth and the later rounds.
Both deferred 2026-09-01, user's call — purchasing takes time. See *Capacity, until
the mini-nas disks arrive*.

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
- **A second PBS on mini-nas, pulling from the primary,** for the platform layer
  offsite.
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
- **A deleted guest's backups outlive it**, on both PBS stores, until forgotten by
  hand after inspecting what is there. Nothing expires them automatically
  (2026-10-18). Verbatim: *"if we build nothing - nothing is lost. The data is
  smoothly retained, and it's just on the user to delete the backups... eventually,
  once/if needed."*
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

### Phase 4's

- **Phase 4 does not wait for the mini-nas disks, nor for Phase 3's prune**
  (2026-10-16), on the capacity measured that day. Verbatim: *"it sounds like
  there's no hard limits to moving ahead with Phase 4."* Syncing before 11-16 costs
  WAN transfer and disk work, not capacity.
- **Space PBS frees returns to the host pool, on both datastores**, as part of what
  Phase 4 builds (2026-10-16). Verbatim: *"We'll want to include dealing with the
  discarding/trimming of aged-out disks as part of the system built in Phase 4."*
  Met by putting both datastores on datasets; see *Both PBS hosts are NixOS
  containers running nixos-pbs*.

Decided 2026-10-17, in designing the phase:

- **Both PBS hosts are unprivileged NixOS containers running nixos-pbs**, configured
  in this repo's `nixos/` flake and deployed by colmena — PBS #2 by mini-nas, as
  amended below. Verbatim: *"I'd like to move
  away from using Ansible. I have no interest in doing hand-configuration."* VM 107
  stays on Ansible only for what 4a needs of it.
- **PBS #2 is a NixOS container on mini-nas's host, not a PVE container** (2026-10-18),
  because proxmox-nixos cannot run LXC; see *Why PBS #2 is not an LXC*. Its system is
  built from this repo's `nixos/` flake; mini-nas declares the container and deploys
  it with `nh os switch`. Verbatim: *"I guess it's just uncompleted work then. That's
  too bad. Let's go with the NixOS container then."* The user would like it moved to
  an LXC should proxmox-nixos ever support one, and `docs/` says so.
- **PBS #2 first (4a), then the primary (4b).**
- **PBS #2 pulls, and the primary holds no credential for it.** Both are tagged
  tailnet nodes, PBS #2 in kernel mode and the primary in userspace mode; VM 107
  joins the tailnet through Ansible for 4a.
- **PBS #2 keeps its own retention**, syncing without `remove-vanished`.
- **Archiving is the protected flag**, mirrored from the primary to PBS #2 in both
  directions, setting and clearing.
- **A reused VMID's alert points at archiving**, and reuse detection ignores
  protected snapshots.
- **At 4b, retention moves into PBS and PVE's credential becomes backup-only**, in
  place of `root@pam`.
- **The primary's datastore keeps sanoid's 30 dailies.** PBS #2's has no snapshots
  and no quota.
- **One new check, `pbs-offsite-freshness`; job failures go to Pushover** through
  PBS's webhook target.
- **Two test restores from PBS #2**: `vm/900` onto mini-nas, `ct/104` onto vulcanus,
  the latter's token and grant removed afterwards.
- **mini-nas's bridge takes a static `192.168.1.11`**, deployed with `nh os switch`
  and the JetKVM as fallback.
- **`rpool/data` leaves replication in two steps**: narrowed to `vm-107-disk-0` at the
  end of 4a, retired at 4b.
- **VM 107 is destroyed after 31 nights on the new primary**, the user confirming.
- **LXC 109 on both hosts**, the primary at `192.168.0.109`; **the sync at 12:00
  UTC**.

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
| 4 | Platform images offsite — PBS #2 on mini-nas, pulling | Phase 4 |
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
| **PBS #2** | its own `keep-last 31`, synced without `remove-vanished`; protection mirrored from the primary | 31 runs — 4a |
| **PBS primary** | `keep-last 31` moves from vzdump into a PBS prune job | 31 runs — 4b |
| **External disk** | keep-monthly 12, keep-yearly 3 | 3 y |

### Schedule still to add

The estate runs four clocks, so the schedule is stated in UTC: vulcanus is
`America/Denver`, mini-nas `America/New_York`, Kubernetes and the NixOS PBS hosts UTC.
VM 107's zone is unverified, and nothing new is scheduled on it. What runs today is
under *Schedules* in `docs/backups.md`.

**vzdump's window is reserved** and nothing else touches the spindles then: it starts
at 04:00 Denver, which is 10:00 UTC in summer and 11:00 in winter, and finishes in
about 35 minutes. The PBS jobs below clear it in both.

| UTC | Job | Where |
|---|---|---|
| 12:00 | sync, then protection mirror, then prune — one chain | PBS #2, from 4a |
| 13:00 | prune, after PBS #2 has pulled | primary, from 4b |
| Sat 14:00 | GC | both PBS stores |
| Sun 14:00 | PBS verify | both PBS stores; the primary's from 4b |
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
nothing to be compared with; catching it needs a remembered inventory. PBS #2 syncs
without `remove-vanished`, so a group vanishing from the primary survives there, but
`pbs-offsite-freshness` compares against the primary's groups and does not notice
either. And **scope drift**: adding a live guest to `exclude` moves it
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

**Phase 4 adds one and removes one.** `pbs-offsite-freshness` arrives in 4a and covers
the sync and both stores' verification, asserted from the stores; PBS's job failures
go to Pushover through its webhook target, costing nothing. `syncoid-data` goes at
4b with `vulcanus-data`.

**Four are still to come after Phase 4:** vzdump, restic check, the restore drill and
the external disk. That is 17 of the 20.

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

**Phase 4 fits without the disks**, measured 2026-10-16. mini-nas's `rpool` holds
9.89 of 12.57 TiB (79%), the `rpool/data` replica is 904 GiB, and PBS #2 copies a
primary holding 643 GiB. Holding PBS #2 and a still-replicated `rpool/data` at once
peaks near (9.89 + 0.63) / 12.57 ≈ **84%**, under the interim 90% warning, and
retiring the replica leaves ≈ **77%**. Both figures take PBS #2's footprint as PBS's
own count; it is read from `rpool/pbs` after the first sync, that pool being the one
that is short. The datastore goes on mini-nas's `rpool` and stays there through the
expansion, which grows the vdevs in place.

The peak arrives with PBS #2's first sync, whenever that is. A sync before 11-16, when
Phase 3's last `virtio1` backups leave the primary, also carries the OpenEBS disks'
chunks, whose share of the 643 GiB check C measures. PBS #2's own `keep-last 31`
evicts them on the same arithmetic as the primary's, and its GC returns the space
straight to the pool.

**4b holds both primaries on vulcanus for 31 nights**: LXC 109's copy, about the
primary's 643 GiB, beside VM 107's 1.99 TiB. On 10-17 vulcanus's `rpool` held
11.8 TiB, so the overlap stays under the standing rule, and destroying VM 107 returns
the 1.99 TiB.

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
- **LXC 105 carries pending mountpoint changes until its next restart**: books moves
  from `mp4` to `mp3`, replacing the borg tree's bind mount, already released by hand.
  Proxmox cannot change a running container's mount points, and applies these before
  the next start, so the missing borg dataset cannot fail that boot; books stays at
  `/mnt/storage/books` throughout. `pct pending 105` shows them. The renumbering
  closed a gap at slot 3 that hid books from the provider's read-back; done and
  verified 2026-10-16, with no plan change for LXC 105 since. The apply after it
  drops a stale slot-4 entry from the state file.

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

Deployed and applied 2026-10-16, ahead of the 10-17 backup. `qm config` shows
`backup=0` on both workers' `virtio1` with nothing pending, and both QEMU processes
date from 10-09 04:26 UTC, so the apply restarted neither. The coverage-check fixes
went out in the same deploy.

### Verification schedule

vzdump starts at 10:00 UTC until US daylight saving ends on 11-01, and at 11:00 UTC
after — 06:00 Eastern throughout — and has been finishing by about 10:35. Each
10:00-11:30 window below moves an hour later from 11-01.

| When (UTC) | Check | Passes when |
|---|---|---|
| **10-17, after 11:00** | A. First night | `virtio1` gone from the backup, nothing restarted |
| **10-24, after 11:00** | B. The week, nights 10-17 to 10-23 | clearly better than 10-10 to 10-16 |
| 11-16 | 10-16's backup, the last holding a `virtio1`, leaves `keep-last 31` in that night's run | — |
| **first GC after 11-16, ~11-24** | C. PBS space returned | PBS's own figure well under 643 GiB |
| the morning after a worker's QEMU restarts | D. The full-read case | VM 910 far under 49 min |
| after LXC 105's next restart | E. Books on `mp3` | see below |

**A — 10-17.** From the night's task log:

```bash
ssh root@vulcanus.forge.local 'u=$(grep vzdump /var/log/pve/tasks/index | tail -1 | cut -d" " -f1); grep -hE "virtio1|virtio0: dirty-bitmap|Finished Backup of VM 91[01]" /var/log/pve/tasks/*/"$u"*'
```

`virtio1` appears for 910 and 911 only as excluded, with no `virtio1: dirty-bitmap`
line. `virtio0` reads `dirty-bitmap status: OK`; `created new` means a QEMU restart,
which makes the night check D's rather than an ordinary one. VM 910 falls below its
7-14 min baseline, reading only the boot disk. Also: the coverage check's 02:45 and
08:45 runs, the first on the new code, succeeded (`kubectl get jobs -n infrastructure
| grep coverage`), and `backup-coverage` is green on healthchecks.

**B — 10-24.** The same measures as the baseline table in the evidence, over
10:00-11:30 UTC each night, leaving out any night whose bitmap was `created new`:

| Measure | Baseline, ordinary nights | Bar |
|---|---|---|
| VM 910 duration | 7-14 min | clearly lower |
| minutes with fsync p99 > 0.5 s | 6-17 | clearly lower |
| peak p99 | 0.80-1.24 s | lower |
| failed proposals | 0-20 | no worse |
| 24 h mean of p99 | 0.246-0.301 s | no worse; it barely moves, see *Verification* |

The p99 expression is
`histogram_quantile(0.99, sum by (le) (rate(etcd_disk_wal_fsync_duration_seconds_bucket[5m])))`,
taken with `max_over_time`, `count_over_time((… > 0.5)[90m:1m])` and
`avg_over_time` evaluated at 11:30, and failed proposals as
`sum(increase(etcd_server_proposals_failed_total[90m]))`. Also: no coverage-check
failures all week, and no `OOMKilled` init container. Phase 3's review closed on
2026-10-16, ahead of B; when B passes, its results go into the evidence and the status
table marks Phase 3 done. C can be recorded after.

**C — ~11-24.** The 11-16 run prunes the last backup holding a `virtio1`; its chunks
go at PBS's next weekly GC, whose day is not recorded — PBS refuses SSH from the
workstation; the GUI shows it under Datastore → primary → Prune & GC. After it has
run, record both views:

```bash
ssh root@vulcanus.forge.local 'pvesm status --storage pbs; zfs get -H -o property,value usedbydataset,usedbysnapshots rpool/proxmox_backup_server'
```

`pvesm status` is PBS's own count of its chunks, read from inside the guest. It should
fall well under 643 GiB, and it is the figure that sizes PBS #2. The host's figures
do not follow it: `virtio1` has no `discard`, so the raw file keeps every block the
guest has ever written — 1.61 TiB on 10-16 — and sanoid's 30 dailies of
`rpool/proxmox_backup_server` held 385 GiB more. VM 107 is never trimmed — it retires
at 4b, and destroying it returns the space — so an unchanged host figure is expected
and not a failure of C.

**D — opportunistic.** A host reboot, `qm stop`/`start`, or an apply that reboots a
worker; a reboot from inside Talos keeps the QEMU process and does not count. The
largest win and the one that cannot be scheduled: before, VM 910 re-read 1.10 TiB in
49 min with fsync p99 above 0.5 s for 52-56 min, and it now reads the 100 GiB boot
disk. Run A's command the next morning.

**E — opportunistic.** `pct config 105 --current` shows books as `mp3` and neither
`mp4` nor the borg mount, the books share is still reachable, and a plan shows
nothing for LXC 105.

## Phase 4 — platform images offsite

Two stages, each landing on its own:

- **4a — PBS #2 on mini-nas**, pulling from VM 107. It proves the PBS stack where a
  failure costs only the offsite copy.
- **4b — the primary moves** from VM 107 into a container on vulcanus built from the
  same module, once 4a has run cleanly.

Not gated on the mini-nas disks; see *Capacity, until the mini-nas disks arrive*.

### Order of work

**4a**

1. mini-nas's host side: networking, the datastore dataset, the container. See
   *mini-nas's host side*.
2. **Prove the stack** in that container before building on it; see below.
3. The Headscale policy: tags and grants. Both PBS hosts register interactively.
4. VM 107, through Ansible: Tailscale as `tag:pbs-primary`, and the sync user and
   token.
5. PBS #2's datastore and jobs: the remote, the sync chain, prune, GC, verify, the
   webhook target and `pbs-offsite-freshness`.
6. The first sync and the first verify. 643 GiB crosses the WAN, at a rate not yet
   measured.
7. Both test restores, then `rpool/data` narrows to `vm-107-disk-0`.
8. `docs/backups.md`, `docs/nixos.md` and `docs/tailnet.md` for what 4a made true.

**4b** — see *4b — moving the primary*.

### Both PBS hosts are NixOS containers running nixos-pbs

[nixos-pbs](https://github.com/AWildLeon/nixos-pbs) packages PBS natively for NixOS,
built from Proxmox's sources, with a module that declares datastores and prune,
verify and sync jobs and reconciles them through `proxmox-backup-manager` on
activation. It is young — one maintainer, and nixpkgs
[PR #543254](https://github.com/NixOS/nixpkgs/pull/543254) open since 2026-07-18 — so
it is pinned to a revision and updated deliberately. Its datastore is upstream's own
chunk-store format, which bounds the risk: a stock PBS adopts a datastore it wrote.
Where the module has no option for something, a oneshot in the shared module
reconciles it through `proxmox-backup-manager`; nothing is configured by hand.

Both hosts live in this repo's `nixos/` flake beside `restic-repository`, sharing one
PBS module and differing by role. The primary is a colmena host: Terraform creates
its container from a template the flake builds, LXC 108's pattern in
`docs/nixos.md`. PBS #2 is the flake's `nixosConfigurations.pbs-offsite`, which
mini-nas's flake takes as an input and runs as `containers.pbs-offsite`, a NixOS
(systemd-nspawn) container, deployed with mini-nas's own `nh os switch`.

| | Primary (4b) | PBS #2 (4a) |
|---|---|---|
| Host | vulcanus, LXC 109 | mini-nas, NixOS container `pbs-offsite` |
| Address | `192.168.0.109`, static in Nix: PVE reaches it on the LAN | DHCP on `vmbr0`: everything reaches it over the tailnet |
| Datastore | `rpool/backups/pbs`, bind-mounted | `rpool/pbs`, bind-mounted with `idmap` |
| Snapshots | sanoid `dailies` (30), an override beside `rpool/backups/restic`'s | none |
| Tailscale | userspace networking, `tag:pbs-primary` | kernel, `enableTun`, `tag:pbs-offsite` |
| User namespace | unprivileged LXC | `privateUsers = "pick"` |

### Why PBS #2 is not an LXC

Read 2026-10-18. proxmox-nixos cannot run a PVE container, at mini-nas's pin or at
upstream `HEAD` (`b319f940`). `modules/proxmox-ve/container.nix`, which defines
`pve-container@` and `pve-lxc-syscalld`, is imported by nothing, and is malformed —
`pve-container@` is nested inside `pve-container-debug@` — and `pve-lxc-syscalld` is
not packaged. mini-nas has neither unit and no `lxc-start` on the PATH; only `pct` is
installed. Upstream: issue #140 "Make LXC works" and its PR #142, open since
2025-05 and conflicting, whose author created a container but could not start one;
issues #192 and #244 report the same. VMs are supported.

A NixOS container keeps what the LXC was chosen for: a dataset datastore, a memory
limit rather than an allocation, its own tagged tailnet node, and user namespacing.
What it changes is the deploy path.

Both are excluded from vzdump: their configuration is in git, and their datastores
are what vzdump writes to. PBS #2's dataset stays outside mini-nas's sanoid, which
covers only `rpool/foreign-backups/vulcanus/*`, and carries no quota.

**Containers on datasets, not VMs on block devices:**

- **GC returns space to the pool directly.** A datastore on a raw file or a zvol holds
  the guest's high-water mark unless discards are passed down and issued, and VM 107
  shows the cost: on 10-16 its raw file held 1.61 TiB on the host for 643 GiB of
  chunks. On a dataset, a chunk file GC deletes is space the pool has back. This is
  how the user's requirement that freed space returns to the host is met, on both
  stores.
- **The host sees the chunk store**, so the pool's own scrub covers it, the property
  that put the restic repository in a container.
- **Memory is a cgroup limit, not an allocation,** which matters against mini-nas's
  ~7 GiB available. Each starts at 2 GiB, VM 107's figure, and is measured. On
  mini-nas it is `MemoryMax` on `container@pbs-offsite.service`.

**Each datastore dataset sets `atime=on`.** Both pools are `atime=off`, and PBS's GC
marks chunks in use by their atime; its safety check refuses a datastore without it.
`relatime` is fine.

**Prove the stack before building on it**, first in a NixOS VM test on the
workstation — a host running the container as mini-nas will, on ZFS — and then in
4a's container on mini-nas:

- nixos-pbs's `buildFHSEnv` wrapper (bubblewrap) runs inside a user-namespaced
  nspawn container. It needs to create a nested user namespace.
- The datastore is created, its atime check passing.
- A backup, restore, verify and GC round trip completes against a scratch datastore.
- The container's memory peak is read.

**Where it stands, 2026-10-19.** The VM test passes all four of the above, on
mini-nas's nixpkgs; see the evidence. Done: mini-nas's `vmbr0` holds
`192.168.1.11` under networkd (`ad40a2a`), `rpool/pbs` exists, and
`containers.pbs-offsite` runs (`70967a6`) with its datastore created, ~160 MiB
idle, on DHCP `192.168.1.35`. VM 107 has Tailscale installed and `sync@pbs` with
token `pbs-offsite`, holding Datastore.Audit, Read and Verify on
`/datastore/primary` and nothing else, its secret in `/root/pbs-offsite-token-secret`
(37 bytes, mode 600); `ansible/pbs-primary.yaml` re-runs clean. The Headscale policy
carries both tags and the one grant each way it needs.

Both nodes are registered and tagged: `pbs-primary` (VM 107, `100.64.0.9`) and
`pbs-offsite` (`100.64.0.10`), named at `tailscale up --hostname` so the remote's
`pbs-primary.forge.local` resolves, and tagged server-side. The policy is proven
narrow from inside the container: 8007 open to the primary, 22 and 8006 blocked.
mini-nas's lock pins vulcanus-proxmox's `main` (`7fe184d`), so the
`--override-input` is gone and a deploy from that branch is reproducible.

**The first sync is running**, started 2026-10-20 00:40 UTC through
`pbs-offsite-daily`. The path is **direct**, not DERP-relayed (`tailscale status`
reads `direct`, 60-70 ms), and it moves **~3.3 MB/s ≈ 26 Mbit/s** — vulcanus's
uplink, since the path is direct and `rx` matches what lands in the datastore. At
that rate the primary's 643 GiB takes **~55 h**, a one-time cost; daily runs carry
a delta. Nothing rate-limits it, so it saturates the vulcanus site's upload
throughout: PBS's `traffic-control` is where a cap would go if that matters.

Outstanding after it: the first verify, `rpool/pbs`'s `used` read against PBS's own
figure, the protection mirror exercised both ways, `pbs-offsite-freshness` shown red
and green, the two test restores, then `rpool/data` narrows to `vm-107-disk-0`, and
`docs/` for what 4a made true.

mini-nas's lock pins vulcanus-proxmox's `main`, so until this branch reaches it,
mini-nas builds with `--override-input vulcanus-proxmox
path:<this checkout>/nixos`, deployed by deploy-rs for its magic rollback. That
pin sets the order in which the two branches land: this one to its `main` first,
then mini-nas's lock, then mini-nas's own `main`. Until mini-nas's lands, the host
runs an unmerged branch and a deploy from its `main` would take PBS #2 away — which
is how `claude-warmup` came to be undeployed on 10-18.

### mini-nas's host side

Read 2026-10-17; see the evidence. PVE's services run, but no guest ever has, and
no container can (2026-10-18).

- **Networking.** `proxmox.nix` declares `vmbr0` over `systemd.network`, but
  `systemd-networkd` is inactive and `dhcpcd` owns `eth0`, so none of it applies —
  the `ens18` match is the lesser fault. The fix enables networkd, turns dhcpcd off,
  enslaves `eth0` to `vmbr0` and gives the bridge **a static `192.168.1.11/24`**,
  gateway `192.168.1.1`, with the resolvers DHCP supplies today set explicitly and
  IPv6 still taken from router advertisements. The bridge's MAC differs from `eth0`'s,
  so DHCP could hand it a new address, and tofu, deploy-rs and ssh all use this one.
  The router must not lease `.11` to anything else: it is reserved there or outside
  its pool, read before the switch. Deployed with `nh os switch`, the JetKVM console
  the way back if it goes wrong.
- **The datastore**, `rpool/pbs`, created on the host with `atime=on`, owned by the
  container's pinned PBS uid, and bound into the container with `idmap`.
- **The container**, `containers.pbs-offsite` in mini-nas's configuration, on
  `vmbr0`, with `enableTun` for Tailscale.
- **Guest storage** is only `local`, a directory, and `/etc/pve/storage.cfg` does not
  exist. Nothing in 4a needs more until the `vm/900` test restore, which can restore
  onto `local`.
- **mini-nas's tofu has never been applied to this PVE** (2026-10-18): no state
  exists, and PVE holds only `root@pam` — no `will@pam`, no `admin` group, no API
  token. 4a needs no tofu now the container is NixOS's.
- The commented-out `proxmox_backup_server` module in `main.tf` and
  `tofu_modules/proxmox_backup_server` are an earlier VM attempt, and go.

### The tailnet

**PBS #2 pulls; nothing is ever pushed to it.** The primary holds no credential for
the offsite copy, so nothing on vulcanus can write to it or delete from it. A push
sync needs exactly such a credential, which is why it is not used.

Both PBS hosts are tagged tailnet nodes. The Headscale policy grants
`tag:pbs-offsite` → `tag:pbs-primary:8007`, and `will@` both hosts on 22, for colmena,
and 8007, for the GUI. In 4a the primary is still VM 107, which joins as
`tag:pbs-primary`; at 4b the tag moves to LXC 109, and `will@`'s grant to
`192.168.0.107:8007` goes with VM 107.

The primary runs Tailscale in **userspace networking**: it serves inbound connections
to its local services and needs no tun device, and the primary makes no outbound
tailnet connections. PBS #2 does — it is the one that pulls — so it runs the kernel
mode, with the tun device passed in.

**Registration is interactive, and the tag is applied server-side**, as for every
other node: `tailscale up --login-server …`, then `headscale nodes register` and
`headscale nodes tag`. No pre-auth key: a reusable one is a single point of failure
for the whole tailnet (*What does not work* in `docs/tailnet.md`), and
`ansible/proxmox-tailscale.yaml` records why the tag is not advertised from the node.
The node's state lives on the container's rootfs, so it survives deploys and reboots,
and a recreated container registers again.

### Sync, retention and archiving

**PBS #2 keeps its own retention.** The sync runs **without `remove-vanished`**, into
a `vulcanus` namespace, so no deletion on the primary reaches it, whether a mistaken
forget, a retention edit (`keep-last 3` would prune 28 snapshots per guest in one
night), a primary rebuilt with an empty datastore, or a compromised vulcanus. The
namespace keeps vulcanus's groups apart from anything mini-nas's own PVE might back
up there.

Both stores run the same policy from the shared module: `keep-last 31` per group as a
PBS prune job, a weekly GC and a weekly verify.

**A retired guest's group is kept until forgotten by hand, on both stores.** Nothing
expires it: `keep-last` counts snapshots rather than age, so prune leaves a group
that has stopped receiving backups as it is. Freeing that space is a deliberate act
after inspecting what is there, and a forget on the primary does not reach PBS #2,
so it is done on both.

**PBS #2's daily chain is sequenced, not merely scheduled in order:** sync, then the
protection mirror, then prune. A snapshot newly protected on the primary but already
past `keep-last` on PBS #2 would otherwise be pruned before its flag arrived.

**Retention moves from vzdump into PBS at 4b.** Until then vzdump's
`prune-backups keep-last=31` prunes the primary, as today. At 4b vzdump drops to
`keep-all` and the primary's own prune job takes over, and PVE authenticates as a
dedicated user that can back up and read its own groups but not prune — today it is
`root@pam`, its password in `/etc/pve/priv`. That contains a mistaken retention edit
and a leaked PVE credential. It does not stop root on vulcanus, which owns the
dataset regardless; PBS #2 is the defence there. The migration sync gives the copied
groups that user as owner, so nothing is re-owned by hand.

**Archiving is PBS's protected flag.** Prune neither removes a protected snapshot nor
counts it toward `keep-last`, and forgetting a group leaves its protected snapshots
in place. A retired guest needs none of this, since nothing prunes its group. What
needs it is the earlier machine of a reused VMID, which `keep-last` would otherwise
evict within 31 runs, and anything worth keeping through a hand forget. Archiving is
protecting the snapshots on the primary.

**Sync does not carry the flag:** a pull copies a snapshot's manifest, indexes and
chunks, but not its `.protected` marker. So PBS #2 runs a **protection mirror**,
which reads the primary's snapshot list over the sync user's read access and sets
and clears each flag locally to match, for every snapshot both stores hold. A
snapshot protected on PBS #2 that the primary no longer holds is reported, not
changed: there is nothing left to mirror it from.

**The primary's only change in 4a is for the sync**, through Ansible, retired with
VM 107: Tailscale, and a sync user with `DatastoreReader` on the primary datastore,
and its token. Ansible reaches it over SSH, which VM 107 refuses from the workstation
until the key is installed once through its console. Its verify job waits for 4b, and
it gets no `discard`: it retires rather than trims.

### Reporting

- **`pbs-offsite-freshness`**, a healthchecks check pinged from PBS #2. It passes only
  when each group's newest snapshot on the primary, once 26 h old, is on PBS #2 too,
  and when neither store holds a snapshot past the verify interval that is unverified
  or failed. Comparing newest snapshots rather than ages lets a frozen group pass,
  having nothing new to copy, while a live group fails within a day of its sync
  stopping. Asserted from the stores rather than from the jobs, as `pbs-freshness`
  is, and it dies with PBS #2, which the check's period catches. A group on PBS #2
  that the primary no longer holds is reported, not failed: it is what a hand forget
  on the primary leaves offsite. Verification state is
  per store: a pull strips `verify_state` from the manifests it copies, so PBS #2's
  comes from its own verify, which is load-bearing for that reason.
- **Job failures go to Pushover** through PBS's webhook notification target: sync,
  prune, verify, GC and the mirror. No check is spent. A GC that stops
  running is not asserted; the space it would have freed reaches pool-health.
- **`pbs-freshness` skips protected snapshots when detecting a reused VMID**, so
  archiving the earlier machine ends the alerts, and the alert tells the reader to
  archive what is worth keeping rather than to copy it out.

### Two test restores from the offsite copy

Both from PBS #2, restored unstarted with the network disconnected, then destroyed:

- **`vm/900` onto mini-nas's PVE**, the vulcanus-is-gone case, with PBS #2 added as
  storage there.
- **`ct/104` onto vulcanus**, through a read-only token on PBS #2 and a grant from
  `tag:vulcanus-subnet` to `tag:pbs-offsite:8007`, both removed once it passes. Day to
  day nothing on vulcanus reaches PBS #2; a restore onto a rebuilt vulcanus creates
  them first, which the Phase 6 runbook carries.

  It is restored at a VMID added to vzdump's `exclude` for as long as it exists. The
  job is `all 1`, so a scratch guest still present at the nightly run would be backed
  up into a group of its own, which freezes the moment the guest is destroyed and is
  then kept until someone forgets it.

### Retiring `rpool/data` from replication

Guest images leave ZFS replication for PBS in two steps.

**At the end of 4a**, once PBS #2 holds verified guest images and both restores have
passed, everything goes **except `rpool/data/vm-107-disk-0`**, VM 107's OS disk.
`/etc/proxmox-backup` lives there, and while VM 107 is the primary nothing else holds
it: datastore definitions, users, ACLs, schedules and the TLS certificate whose
fingerprint vulcanus's `storage.cfg` pins. Four changes land together, because each
alone breaks the freshness check:

- **Narrow the `vulcanus-data` syncoid command** rather than dropping it. `source`
  becomes `rpool/data/vm-107-disk-0`, `target`
  `rpool/foreign-backups/vulcanus/data/vm-107-disk-0`, and `recursive = false` — a zvol
  has no children. **No re-seed:** the target already exists and shares a snapshot
  lineage with the source, so syncoid continues incrementally. Keep the command's
  *name*: `checkNameFor` derives the healthchecks check from the unit name, so a
  rename costs a new check and a new sops secret for a command 4b retires anyway.
- **Narrow the check's source roots** at
  `~/repositories/mini-nas/modules/backup_monitoring/default.nix:183`, from
  `rpool/data` to `rpool/data/vm-107-disk-0`. The loop asserts a target counterpart for
  every dataset under each root, so leaving `rpool/data` there turns every live guest
  into a missing-counterpart note the morning after the replicas go.
- **Destroy the siblings, not the parent.** `rpool/foreign-backups/vulcanus/data` has
  to survive as the container for the retained zvol, so each sibling is destroyed
  individually, with the user confirming. Any sibling left behind is reported stale
  every day, because the age loop walks everything under
  `rpool/foreign-backups/vulcanus` with no exclusion.
- **Leave mini-nas's sanoid entry alone.** `"rpool/foreign-backups/vulcanus/data"` is
  `replica-shallow` with `recursive = true`, which goes on pruning the retained child
  unchanged. Verified 2026-09-19 — the one step here easiest to "fix" unnecessarily.

**At 4b**, with the primary's configuration in git, `vulcanus-data` retires entirely:
its syncoid command, its healthchecks check and sops secret, its source root in the
check, the retained replica and its parent, and mini-nas's sanoid entry for it.

### 4b — moving the primary

Once 4a has run cleanly:

1. **LXC 109 on vulcanus**: `rpool/backups/pbs` with its sanoid override, Tailscale in
   userspace mode, and vzdump's exclusion.
2. **A local pull from VM 107** into it, owned by the new vzdump user, then a verify.
3. **The cutover, together:** PVE's `pbs` storage repointed — server, datastore,
   credential and fingerprint — with vzdump at `keep-all`; the primary's prune,
   verify and webhook target enabled; `tag:pbs-primary` moved; PBS #2's remote
   repointed. A copy keeps each snapshot's time, so PBS #2's sync carries on where it
   was, a pull skipping whatever is older than its newest local snapshot. vzdump's
   dirty bitmap is trusted only when the group's previous snapshot holds the index
   checksum QEMU last uploaded; a copied index should carry it, and if not, each boot
   disk is read once in full.
4. **`vulcanus-data` retires**, as above.
5. **VM 107 and `rpool/proxmox_backup_server` are destroyed after 31 nights on the new
   primary**, once it holds a full `keep-last 31` of its own and PBS #2 has synced and
   verified it, with the user confirming at the time. That returns ~2 TiB to vulcanus.
6. `docs/` for what 4b made true.

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
  full reads. So the backup window itself is compared too; the measures, bars and
  dates are under Phase 3's *Verification schedule*. The full-read case, 1.10 TiB
  becoming 100 GiB, is the larger win and has no matched night until something
  restarts a worker's QEMU process.
- **Phase 4** — the stack proven before anything is built on it, as listed under
  *Both PBS hosts are NixOS containers running nixos-pbs*. Then:
  - **The offsite copy is real:** a verify passing on PBS #2, and both test restores
    from it. `pbs-offsite-freshness` goes red when a group's sync is held back and
    green when it catches up, both directions shown, because a new check that has
    only ever been green is untested.
  - **Space is read from the pools**, not from PBS: `rpool/pbs`'s `used` sits near PBS
    #2's own figure after the first sync, and falls after a GC that followed a prune.
  - **The protection mirror** sets a scratch snapshot's flag on PBS #2 after it is
    protected on the primary, clears it after it is cleared, and a protected snapshot
    past `keep-last` survives PBS #2's prune.
  - **A frozen group passes `pbs-offsite-freshness`**: `vm/100` reads green, and a
    group forgotten on the primary only is reported, not failed.
  - **Reuse detection** ignores a protected snapshot, in `test_pbs_freshness.py`.
  - **`rpool/data`'s narrowing at the end of 4a:** `zfs-replication-freshness` stays
    green across it, which is what says the source-root change and the replica
    destroy landed together rather than one without the other. Green is not enough on
    its own here, because the narrowed command must still be *doing* something:
    assert that `rpool/foreign-backups/vulcanus/data/vm-107-disk-0` gains a snapshot
    newer than the narrowing, and that it is the only dataset left under
    `.../vulcanus/data`. A syncoid command narrowed to a dataset that stopped
    receiving would pass the source-root comparison and fail nothing until the 26 h
    limit caught it the next morning.
  - **4b's cutover:** the first vzdump after it lands every in-scope guest in LXC 109,
    `pbs-freshness` reads it green under the backup-only credential, VM 107 receives
    nothing, and PBS #2's next sync pulls from LXC 109. After `vulcanus-data` retires,
    nothing remains under `.../vulcanus/data` and `syncoid-data` is deleted from
    healthchecks rather than left to go red.
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

- **PBS #2 was designed as a PVE container on a PVE that had never run a guest.**
  The design session read that no guest ever had, and went on to choose an LXC
  without asking whether proxmox-nixos could start one; it cannot. When a design
  rests on a capability nobody has exercised, check that it exists first.
- **Phase 4 was gated for six weeks on a 93% peak projected 2026-09-01** and never
  re-measured, though Phase 0 had already landed at 76% against a predicted 82%.
  Measured on 10-16 it was 84%. Re-measure a gate set on a forecast before obeying it.
- **Phase 4's trim was scoped onto block devices** — `discard=on` and a guest-side
  trim for both datastores — before anyone asked whether a datastore had to be a
  block device at all. On a dataset the problem does not exist. Ask whether the medium
  creates a problem before building around it.
- **mini-nas's missing bridge was put down to a wrong interface name.** The config
  matched `ens18` where the NIC is `eth0`, but `systemd-networkd` was not running at
  all, so the fix was never one line. Read what the running system uses, not only what
  its config declares.
- **A two-year expiry of retired guests was designed, and dropped before it was
  built.** Asked what it was for, it would have acted once, in 2028, on `vm/100`'s
  141 GiB, while being the only automated deletion across both stores. Ask what
  case a mechanism serves, and what not building it costs, before designing it.
- **Phase 3's check C read returned space from `pvesm status`**, PBS's view from
  inside the guest. vulcanus's capacity is the host's view, which without discard
  never moves, so C would have reported space the pool never got back. Read a
  capacity figure from the pool it is meant to describe.
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
