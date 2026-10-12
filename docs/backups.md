# Backups

What protects what, how long it is kept, and how a failure reaches a person.

Work still outstanding lives in [`todos/backups.md`](../todos/backups.md), not here.

## The layers

| Layer | Covers | Where it lands |
|---|---|---|
| ZFS snapshots (sanoid) | everything on `rpool` | vulcanus, in place |
| ZFS replication (syncoid) | `rpool/storage`, `rpool/ROOT`, `rpool/data`, `rpool/backups/restic` | mini-nas, offsite |
| vzdump → PBS | every guest except `107` | PBS VM 107, on `rpool` |
| restic, via K8up | every Kubernetes volume in scope, and the database dumps | the restic repository, LXC 108 |
| restic, from the repository host | `rpool/storage/{photos,books,filesync}` | the same repository |

Snapshots are the instant-rollback layer, exposed to SMB clients as VSS shadow copies
on every share except `borg`. Replication is the offsite copy. PBS is the per-guest
image backup, and the only one that restores a whole guest with a single command.
restic is the granular layer: one volume, file or database, as of any night retention
still holds.

**mini-nas pulls the restic repository too**, giving Kubernetes state a second
failure domain: `rpool/backups/restic` alone, not the borg tree beside it.

**Replication is a pull, not a push.** mini-nas holds the SSH key and runs syncoid;
vulcanus grants it `hold,send` on `rpool` and nothing more. A host that is compromised
cannot reach into its own backups, and a `zfs destroy` on vulcanus does not propagate.

### The PBS appliance

PBS is guest 107, and it is the one guest the job excludes. Its datastore is a 2 TB
chunk store, so backing it up would read that store and write the resulting chunks
back into it — never consistent, and unbounded.

`--exclude 107` is job policy living in one file on the host. The datastore disk also
carries **`backup=0`**, so a manual `vzdump 107`, a second job, or the job's eventual
move into Terraform cannot pull it in. There is no circumstance in which backing a
chunk store up into itself is correct, which is why it is a property of the disk
rather than something a job decides.

The appliance's two disks are protected differently, and the difference is what
decides how a rebuild goes:

| Disk | Holds | Protection |
|---|---|---|
| `virtio0`, 20 G, on `rpool/data` | the OS, and `/etc/proxmox-backup` | sanoid, **and replicated to mini-nas** with the rest of `rpool/data` |
| `virtio1`, 2 T, on `rpool/proxmox_backup_server` | the chunk store | sanoid dailies on `rpool` only |

**PBS's own datastore is not replicated.** `rpool/proxmox_backup_server` is excluded
from syncoid, so losing `rpool` loses the guest images and their backups together.

`/etc/proxmox-backup` is the part a reinstall cannot reconstruct: datastore
definitions, users, ACLs, prune/GC/verify schedules, and the TLS certificate whose
fingerprint is pinned in vulcanus's `/etc/pve/storage.cfg` — a rebuilt PBS presents a
new fingerprint and PVE refuses the connection until that line is updated. It rides
the replicated OS disk, so it has an offsite copy. The datastore itself needs no such
care: a chunk store is self-describing, and `datastore create` against an existing one
adopts it.

### The restic repository

LXC 108, `restic-repository`: unprivileged NixOS, deployed with colmena (see
[`nixos.md`](nixos.md)). The repository is the `rpool/backups/restic` dataset,
bind-mounted at `/mnt/backups/restic`. The host serves it to the cluster as
`rest-server --append-only` on port 8000, and runs its own restic jobs against the
local path.

**Nothing in the cluster can delete history.** rest-server refuses every delete but a
lock's, so `forget` through the served URL returns `403 Forbidden`. Retention and
anything else that deletes run on the host, against `/mnt/backups/restic`.

**The offsite copy can be verified on mini-nas alone:**

```bash
mount -t zfs -o ro rpool/foreign-backups/vulcanus/backups/restic@<snapshot> /mnt/verify
restic -r /mnt/verify --no-lock check
```

A snapshot, because the live dataset can be mid-receive; `--no-lock`, because the
mount is read-only. Like every replica it is otherwise unmounted (`mountpoint=none`,
`readonly=on`).

**The repository passphrase is the one credential whose loss makes every snapshot
unreadable.** It is escrowed in the password manager, with the REST password beside
it, so a restore needs only the passphrase and the backup media. The working copies
are SOPS-encrypted: `kubernetes/k8up/repository/secret.sops.yaml` for the cluster,
`nixos/hosts/restic-repository/secrets.sops.yaml` for the host.

## Kubernetes volumes and databases

[K8up](https://k8up.io) runs restic in the cluster, against the repository above.
The operator is `kubernetes/infrastructure/k8up.yaml`; the Schedules, one directory
per namespace, and the coverage check are in `kubernetes/k8up/`.

### What is in scope

**Every PVC in a namespace with a Schedule is backed up**, `ReadWriteMany` included,
unless the claim carries `k8up.io/backup: "false"`. Two kinds carry it:

- **Every SMB share**, covered by ZFS replication and the repository host's own
  backup. Their manifests carry the annotation without comment: this is the policy.
- **Regenerable state:** pinepods' Valkey cache, rclone's bisync state, borgmatic's
  cache, and the Prometheus, Alertmanager and VictoriaLogs volumes.

**A new share is backed up unless it is annotated**, so the coverage check fails any
in-scope `ReadWriteMany` claim: every one here is a share.

The last three are StatefulSet claims, annotated through their
`volumeClaimTemplates`. Kubernetes refuses any change to a live StatefulSet's
templates. prometheus-operator recreates the StatefulSet itself, but **VictoriaLogs'
chart upgrade fails** until the StatefulSet is deleted with `--cascade=orphan`, after
which Helm recreates it; its claim and pod carry on.

### Schedules

Every backup Job runs as root, since K8up's default uid cannot read much of what is
backed up. Times are UTC, the operator's clock; the repository host keeps
`America/Denver`, so its jobs move an hour later in winter.

| UTC | What | Where |
|---|---|---|
| 01:00 | `apps` full: every volume, and all of its dumps | K8up |
| 01:30 | `infrastructure` full: grafana's volume and dump | K8up |
| 02:00 | `automatic-ripping-machine` full | K8up |
| 02:45, 08:45, 14:45, 20:45 | the coverage check | CronJob |
| Sunday 03:00 | the K8up Check | K8up, on `apps`' `full` |
| 07:00, 13:00, 19:00 | `apps` dumps only | K8up |
| 07:30, 13:30, 19:30 | `infrastructure` dumps only | K8up |
| 08:00 (09:00 in winter) | the mass-file backup | repository host, 02:00 local |
| 09:00 on the 1st (10:00) | `forget --prune` | repository host, 03:00 local |
| 10:00 (11:00) | vzdump | vulcanus, 04:00 local |

**The dumps-only Schedules give the databases a six-hour RPO** without re-reading the
volumes. They select the label `k8up-dump: "true"`, which every dump producer carries
and no claim does.

**The K8up Check is structural**: it verifies the index and every snapshot's trees,
never the data in the packs.

**One pod per node per namespace.** K8up mounts all of a namespace's claims on one
node into a single Job pod, so one failed mount stops that namespace's whole night,
and shows as one failed Job rather than one failed claim.

### Database dumps

A file copy of a live database is crash-consistent at best, so each database worth
having is also dumped, into a restic snapshot of its own at
`/<namespace>-<container><extension>`:

| Database | Taken by | Snapshot path |
|---|---|---|
| pinepods (PostgreSQL 18) | an annotation on its pod | `/apps-database.pinepods.sql` |
| photoprism (MariaDB) | an annotation on its pod | `/apps-database.photoprism.sql` |
| salamander (MariaDB) | an annotation on its pod | `/apps-database.salamander.sql` |
| headscale, linkding, plex, soft-serve (SQLite) | a PreBackupPod | `/apps-sqlite.<app>.sqlite` |
| grafana (SQLite) | a PreBackupPod | `/infrastructure-sqlite.grafana.sqlite` |

**A relational database is dumped inside its own container**, by a
`k8up.io/backupcommand` annotation on the pod. **A SQLite database is copied by a
PreBackupPod**: a pod of our own running `keinos/sqlite3`, which mounts the claim and
takes a `.backup` copy. Each application's PreBackupPod is in its `backups.yaml`, and
their shared image automation in `kubernetes/k8up/sqlite3-image.yaml`. Either way the
extension carries the application's name, keeping every dump's path its own.

**The two kinds fail differently.** A PreBackupPod that cannot become ready stops the
namespace's whole backup, volumes included. Dumps run in their own `prebackup` Job
and stop at the first failure; it retries up to six times, rerunning every dump
before the failing one.

**Seven SQLite databases have only their volume's file copy:** stump, mumble,
speedtest-tracker, youtube-dl, headplane, beets and ARM's `arm.db`. All are
journal-mode with negligible write rates, so the copy is very likely consistent,
though beets could be torn mid-import.

### Writing a backup command

Every dump, by annotation or PreBackupPod, follows one shape:

```sh
set -e; f=$(mktemp); trap "rm -f $f" EXIT; <dump> > "$f"; test -s "$f"; cat "$f"
```

Each part closes off a failure that would otherwise pass unseen:

- **Dump to a file, then stream it.** Streamed straight to stdout, a dump that dies
  halfway has already sent a plausible-looking prefix, which restic can save as a
  complete snapshot. Under `set -e` a failed dump stops before the `cat`, so the worst
  case is a zero-byte snapshot, which the coverage check fails.
- **`test -s`** refuses an empty file: a tool that exits 0 having written nothing,
  such as `sqlite3` pointed at the wrong path.
- **The database is held only for a local write.** Streamed, the dump runs at
  restic's pace, through the apiserver and kubelet, on the nights the disks are
  busiest. Meanwhile `--single-transaction` holds MariaDB's read view open, and
  `pg_dump` holds locks that block an application's migrations.
- **`mktemp`, never a fixed path.** K8up can run two Backups at once, and with a
  shared path the second dump truncates the file the first is still streaming.

Beyond the shape:

- **Dump where the tools are.** A relational database from inside its own container,
  over the local socket. A SQLite database from a PreBackupPod, whose `.backup` copy is
  consistent under a running writer and needs the seekable file anyway.
- **Choose a format whose end can be seen**: a SQLite copy, whose header states its
  length, or plain SQL, which ends in a completion marker. A size comparison cannot
  see a dump cut short by a percent, and a truncated `pg_dump` custom-format archive
  still lists cleanly.
- **Name the application in the extension** (`k8up.io/file-extension`, or a
  PreBackupPod's `fileExtension`), since every database container here is called
  `database` and K8up names the dump after the container.
- **Label the producer `k8up-dump: "true"`**, so the dumps-only Schedules take it.

The cost is the dump sitting in the container's writable layer while it streams:
about 200 MB at most.

## Retention

sanoid on vulcanus, from [`ansible/templates/sanoid.conf`](../ansible/templates/sanoid.conf):

| Dataset | hourly | daily | monthly |
|---|---|---|---|
| `rpool` (recursive) | 36 | 30 | 24 |
| `rpool/backups` | 36 | 30 | 12 |
| `rpool/backups/restic` | 0 | 30 | 0 |
| `rpool/ROOT`, `rpool/data`, `rpool/proxmox_backup_server` | 0 | 30 | 0 |

sanoid on mini-nas, from `~/repositories/mini-nas/configuration.nix`:

| Dataset | hourly | daily | monthly |
|---|---|---|---|
| `…/vulcanus/storage` (recursive) | 24 | **60** | 24 |
| `…/vulcanus/data`, `…/vulcanus/ROOT` | 0 | 30 | 0 |
| `…/vulcanus/backups/restic` | 0 | 60 | 0 |

The offsite copy keeps **more** dailies than the source. syncoid runs
`--no-sync-snap`, so a target retaining longer than its source is the only thing that
makes a deletion discovered late still recoverable. It keeps fewer hourlies, because
"I deleted that an hour ago" is answered by the copy on vulcanus.

**`autosnap = false` on every replicated dataset.** Snapshots arrive by replication;
taking local ones leaves the target ahead of the source, and the next incremental
receive then fails without `-F`.

PBS retention is client-side: the vzdump job carries `prune-backups keep-last=31` and
applies it per guest as it backs that guest up. The PVE storage entry sets
`prune-backups keep-all=1`, so PVE never prunes the datastore itself.

Each restic snapshot is already a point-in-time copy, and the repository holds every
one its retention keeps. So `rpool/backups/restic`'s own ZFS snapshots are only for
recovering the repository itself, after a bad prune, say. restic's retention runs
on the repository host on the 1st of the month, as one policy per host-and-path
group, so every volume and every dump keeps its own history:

```
restic forget --prune --keep-last 10 --keep-hourly 24 --keep-daily 30 \
  --keep-weekly 8 --keep-monthly 24 --keep-tag decommissioned --max-repack-size 20G
```

**Tag a retired application's snapshots before its claims go.** Its group stops
receiving and ages out on the ordinary windows, and months later is when it is
wanted. On the repository host:

```bash
restic tag --add decommissioned --host <namespace> --path /data/<pvc> latest
```

`--keep-tag decommissioned` keeps it through every prune. The coverage check reports
a group whose claim or producer is gone, tagged or not.

### A retired guest keeps what a live one would

**PBS is the layer that retains a destroyed guest.** Its ZFS replica is destroyed
alongside it. A retained replica fails `zfs-replication-freshness`: that check's age
loop walks every dataset under `rpool/foreign-backups/vulcanus` with no exclusion, and
a retired guest's newest snapshot is frozen at the moment of deletion, so it trips the
26 h limit exactly as a genuinely stale one does. A check that is never green is a
check nobody reads, which is the condition that let `syncoid-vulcanus-data` fail for
seven months unnoticed.

The cost is that a retired guest sits at one failure domain — PBS on `rpool`, the same
spindles as the original — until PBS #2 exists. That is bounded and deliberate: guests
are rebuildable from `terraform` plus `talosctl`, which is why the failure-domain table
gives them two copies rather than three.

Retention itself is unchanged by deletion — a retired guest keeps what a live one
would. Freeing the space is a separate, deliberate act: inspect what is there, decide
whether it is worth keeping, remove it by hand.

**A group frozen by deletion is preserved by nothing arriving, not by any retention
setting.** That distinction matters because it is also how the preservation ends:
reissue the VMID and backups start landing in the group again, which resumes pruning
and evicts the retired machine. See *VMID reuse is tolerated, and alerted on* below.

Each layer arrives at indefinite retention differently, and both are worth knowing
before changing a retention value:

- **sanoid prunes by count, not age.** A dataset that stops receiving keeps its N most
  recent snapshots and nothing ages them out.
- **vzdump prunes only the groups it backs up.** A guest that is destroyed or excluded
  is never visited again, so its backup group freezes at whatever count it had.

Neither can be made to expire a retired guest by tuning its numbers, and neither can a
datastore-wide prune job on PBS: prune counts the buckets that *contain* backups rather
than elapsed calendar time, so `keep-daily 30` against a group frozen months ago keeps
30 of its snapshots and stops. A retired guest expires only by forgetting its PBS
group, or by a deliberate `zfs destroy`.

### VMID reuse is tolerated, and alerted on

A never-reuse rule is a guarantee that has to be kept by hand forever, so there is not
one. The price is data loss rather than inconvenience, and is worth stating plainly.

A PBS group is keyed `vm/<vmid>` with no notion of which machine wrote a given
snapshot, and the vzdump job prunes every group it touches to `keep-last 31`. So a
reused VMID appends the new guest's backups to the retired guest's group and evicts one
old backup per run: **after 31 runs the prior machine is gone from PBS entirely.**
Nothing fails, and the group simply stops being frozen.

Before reissuing a VMID, look at what its PBS group still holds. If any of it matters,
take a copy out first — reuse evicts it within 31 days.

**It goes to Pushover directly, on its own channel.** The signal is intrinsic to the
group, so nothing has to be remembered. vzdump stores the guest config in every backup,
and a VM's `smbios1` UUID is stable for that machine's life and regenerated when a new
guest is built at the same ID. A group whose oldest and newest snapshots carry different
UUIDs therefore spans two machines.

**Why not fail `pbs-freshness` instead.** healthchecks notifies on transitions, so a
check already down sends nothing further. A reuse holds for up to 31 runs, and a genuine
backup failure arriving inside that window would raise no notification at all — the
check would already be red. Keeping the channels apart means a red `pbs-freshness`
still means "a guest's backups are broken", while the decision gets its own push:

> `vm/900: VMID reused. The oldest backup is machine 31282d50-… and the newest is
> 6f685ca0-…. 12 of 30 backups still belong to the earlier machine, and the job evicts
> one per run — they are gone in 12 more runs. Copy out anything worth keeping first —
> nothing else will say so, and this clears itself when the last one goes.`

Counting costs a config-blob read per snapshot, so it happens only once the two ends
already disagree — never on a healthy group.

**The push ladder is stateless.** Pushover has no memory, so without one it would fire
every run for as long as the reuse lasts. It pushes on first detection — the run where
the new machine has exactly one backup, which is derivable rather than remembered — and
again with 7, 3 and 1 backups of the earlier machine left. A missed run can step over a
rung; the remaining rungs are why that is tolerable instead of worth a state file. When
the count cannot be taken at all it pushes every run, because a deadline of unknown
length is the one worth least silence.

**A failed push fails the check**, which inverts the rule `hc-ping` follows. A
healthchecks ping that never arrives turns its own check red on its own period, so
losing one announces itself; a Pushover push that never arrives leaves no trace
anywhere. Failing `pbs-freshness` is the only thing that makes a lost alert visible.

This costs no healthchecks check, so the enumerated twenty are unaffected.

Containers carry no `smbios1`, so for `ct/<vmid>` the comparison uses `net0`'s
`hwaddr`, which PVE generates per container and which survives a restore of the same
container.

Comparing against a remembered inventory would miss the transition whenever the state
file is lost or the check was down for it. The UUID comparison is retroactive, because
identity is carried in the content. Its reporting lifetime is exactly the decision
window: it begins on the first run after reuse and falls silent once `keep-last 31` has
evicted the last old backup, at the moment there is nothing left to decide.

## Restoring from restic

Narrow `latest` with `--host`, the namespace, and `--path`, `/data/<pvc>` for a
volume. Otherwise it names the newest snapshot in the whole repository.

- **A volume:** a K8up `Restore` into a scratch claim, or into the original with the
  application scaled to zero. Give it `podSecurityContext: {runAsUser: 0}` like the
  Schedules, or every restored file belongs to 65532. Or, on the repository host:
  `restic restore latest --host <ns> --path /data/<pvc> --target <dir>`.
- **A relational dump:** `restic dump --host apps --path <path> latest <path>`, piped
  into `mariadb`, or into `psql -v ON_ERROR_STOP=1`. The MariaDB dumps create their
  own database; pinepods' needs `pinepods_database` created first, and `psql` 17.6
  or later for the `\restrict` lines `pg_dump` 18 writes.
- **A SQLite dump:** the same `restic dump`, into a file. With the application scaled
  to zero, put the file in place and remove its `-wal` and `-shm`. Check it with
  `PRAGMA integrity_check` first; Plex's copy needs the `Plex SQLite` binary from its
  own image, for its `icu_root` collation.

**Count the bytes that arrive.** A stream piped into `kubectl exec -i` can end before
the remote command has read it, leaving an empty file and every exit status 0 — and
an empty file passes `integrity_check`. Compare the file's size with the snapshot's,
from `restic snapshots --json`.

**A restore into a fresh claim leaves the claim's root as `0777 root:root`**: K8up
restores the root's contents, not the root itself. `restic ls -l <snapshot>
/data/<pvc>` shows the original on its first line (syncthing's is `0700`, uid 1000);
apply that mode and owner afterwards.

## Reporting

Twelve healthchecks.io checks, all routed to Pushover: six fed from mini-nas and two
from the restic repository host via sops-nix secrets, three from vulcanus via
`/etc/healthchecks/` written by
[`ansible/backup-monitoring.yaml`](../ansible/backup-monitoring.yaml), and one from a
CronJob in the cluster.

| Check | Fed by |
|---|---|
| `syncoid-vulcanus-storage`, `-root`, `-data` | the syncoid units |
| `sanoid-mini-nas`, `sanoid-vulcanus` | the sanoid units |
| `pool-health-mini-nas`, `pool-health-vulcanus` | an hourly timer per host |
| `zfs-replication-freshness` | a daily timer on mini-nas |
| `pbs-freshness` | a daily timer on vulcanus |
| `restic-massfiles`, `restic-prune` | the repository host's two units |
| `backup-coverage` | the coverage CronJob, every six hours |

Three rules shape all of it, each because the obvious alternative is wrong:

**Report from `ExecStopPost`, never `ExecStartPost`.** These units are `Type=simple`,
so systemd counts them started about a second after fork — a success ping there fires
before the job has done anything, on every run whatever the outcome, and a job that
hangs holds its check green forever. `ExecStopPost` runs once the process has exited
and carries `$SERVICE_RESULT`, so one ping per run reports what actually happened. It
also makes `OnFailure=` redundant.

**A ping failure must not fail the job.** The reporting command carries `-`, so an
unreachable monitor does not mark a backup failed. Nothing is lost: a ping that does
not arrive turns the check red on its own period, which is what the period is for.

**Assert freshness from the store, not from the job.** A recursive replication job can
exit zero while carrying only part of its dataset list, and a job that stops running
emits nothing at all. `zfs-replication-freshness` therefore reads the target — newest
snapshot age per dataset, plus a comparison against the source dataset list, because a
dataset that was never replicated has no stale snapshot to look wrong. It skips
`canmount=off` containers, such as the restic replica's parent, which receive
nothing. The restic replica's syncoid unit has no check of its own and reports its
failures to `zfs-replication-freshness`.

**PBS coverage is asserted against the job's scope, and a frozen group reports rather
than fails.** `pbs-freshness` reads three things and compares them: the vzdump job's
scope from `/etc/pve/jobs.cfg` (`all 1` minus `exclude`), the live guest list from
`/etc/pve/.vmlist`, and the groups the datastore holds. Scope is read at runtime rather
than hardcoded, so a guest created tomorrow is expected without anyone editing the
check — a guest merely absent from the check's own list cannot alert as missing.

A live, in-scope guest whose newest backup is over 26 h old, or which has no group at
all, is a **failure**. Everything else is a **report**, printed but not pinged: a guest
that is gone, a live guest the job excludes, and a reused VMID — which additionally
pushes to Pushover, see above. Once a guest is gone no
backup can be taken, so its staleness carries no information, and failing on it would
make the check the always-on warning that
[the alerting rules](README.md) warn against. The reports are what the retention
decision depends on — without them, "someone inspects and decides" has nothing to
prompt it.

An empty group is distinct from an absent one and is treated as no coverage: `vm/107`
has existed with zero backups since PBS was built.

**Pool health is read from the pool.** `zfs-scrub@` exits 0 having found errors, so no
unit's exit status can report a dirty pool. The check reads `zpool status -x`, the
summed vdev error counts, capacity, and scrub age — and does not accept a resilver as
a scrub.

**Kubernetes coverage is asserted from the repository, per claim and per dump.**
`kubernetes/k8up/coverage/backup_coverage.py` lists the repository's snapshots and
compares them with every PVC, `k8up.io/backupcommand` pod and PreBackupPod in the
cluster, including in namespaces with no Schedule. Neither K8up's metrics nor a
Backup's status shows whether a claim was backed up.

It **fails**:
- a claim or dump never backed up, once past its first night;
- a volume whose newest snapshot is over 26 h old, or a dump over 7 h;
- an in-scope `ReadWriteMany` claim;
- a volume that went empty, or any empty dump;
- an item under half its previous size, from 1 MiB up;
- a current dump that is not whole, or that the store cannot serve back.

It **reports**:
- exclusions;
- new items;
- an always-empty claim;
- a group whose claim or producer is gone;
- files restic could not read on an item's latest run, normally none.

A dump is whole when a SQLite copy's size matches what its header states, or a SQL
dump ends in its completion marker.

Those counts live only in backup Job logs, which go when K8up prunes the Jobs or
their node restarts, so the summary line says how many items' counts it found. A
file deleted mid-backup counts too, which is why a nonzero count reports rather than
fails; a volume restic genuinely cannot read fails as empty or shrunk instead.

### Scrubs

vulcanus scrubs the second Sunday monthly via `/etc/cron.d/zfsutils-linux`, and TRIMs
the first. mini-nas scrubs the third Sunday at 03:20 with 15 minutes of jitter — off
the hour so it misses the hourly sanoid and syncoid runs, and a different week from
vulcanus so the two never compete for the replication window.

The 15 minutes is deliberate against a 6-hour default sized for fleets: one host with
one `zfs-scrub.service` covering both pools, running for days, cannot decorrelate
anything a multi-day operation does not already overlap, and six hours of jitter only
makes the start time unattributable. What the jitter is still for is `Persistent=yes`
— this host reboots itself via `system.autoUpgrade`, and every missed timer fires
together on the way back up.

## Capacity, and how large a disk to buy

The restic repository holds about 304 GiB: 248 GiB of mass files, mostly JPEG that
restic barely compresses, and about 59 GB of Kubernetes volumes. It grows by tens of megabytes
a night, the dumps deduplicating to what changed since the last run.

Disks cascade: vulcanus takes new larger ones, its retired vdev moves to mini-nas.
That coupling has a closed-form limit, and it decides what to buy.

Let **r** be the fraction of vulcanus's data needing an offsite twin — currently about
**0.85**, the gap from 1.0 being entirely the VM layer, where vulcanus holds both the
live zvols and the PBS datastore while mini-nas holds only a datastore.

vulcanus retires exactly four disks per vdev upgrade and yields two disks of data per
four (raidz2). What mini-nas does with them sets the ceiling:

| mini-nas vdev | Data disks per 4 received | Max size jump |
|---|---|---|
| 3-disk raidz1 + hot spare | 2 | 1.47× |
| 4-disk raidz2 | 2 | 1.47× |
| **4-disk raidz1** | **3** | **2.21×** |

4-disk raidz1 is the only geometry under which a 2× upgrade is sustainable. raidz2
looks free against 3-disk raidz1 plus a spare — both give two data disks — but against
4-disk raidz1 it costs a third of the pool. Single parity is proportionate because
**parity follows a copy's position in the hierarchy**: vulcanus is copy 1 with no local
peer; mini-nas is copy 2.

```
                (n_m - 1) * (d1 + d0)
    d_new  <=   ---------------------  -  d2        n_m = 4, r = 0.85, u = 0.80
                      2 * r * u                     =>  d_new <= 2.21 * (d1 + d0) - d2
```

`d1` is the vdev being retired, `d0` mini-nas's other vdev after the move, `d2`
vulcanus's survivor, and `u` how full vulcanus is permitted to get.

**A raidz vdev cannot be removed from a pool.** Each cascade round is `zpool replace`
on four disks in turn, after which the vdev autoexpands — not a vdev swap. On a full
chassis that means zero parity through each resilver, which a temporary USB attachment
avoids by letting `zpool replace` resilver from the disk still present.

RAIDZ expansion preserves each existing block's data-to-parity ratio; only new writes
use the wider stripe. Expect materially less than the arithmetic suggests until old
data is rewritten.

## Operational notes

**Do not run sanoid by hand while its timer is enabled.** It serialises on
`/var/run/sanoid/sanoid_pruning.lock`. A run that finds a valid lock exits early and
silently — indistinguishable from deciding there is nothing to do — and the staleness
check compares the lockfile against `ps -p <pid> -o args=`, unlinking the lock when
that comparison fails, so two concurrent runs destroy each other. Stop the timer first.

**A scrub starves a prune.** Running concurrently, sanoid manages a couple of snapshot
destroys a minute. Sequence them.

**syncoid never removes datasets from the target.** Every dataset ever deleted on
vulcanus leaves its replica on mini-nas, and nothing will clean it up.

**syncoid's own snapshots escape sanoid's retention.** Their names do not match
`autosnap_<date>_<type>`, so no rule covers them and they accumulate — a couple per
dataset.

**The oldest surviving snapshot says nothing about when a dataset was created.**
Retention truncates history, so a filesystem years old can present only recent
snapshots — or, once replication stops, only ancient ones.

**Keep restic's exclusive operations out of K8up's windows.** `prune`, `check`,
`forget` and `tag` take an exclusive lock, and K8up never retries one: a backup that
meets it fails at once, and a K8up Check exits 11. The K8up operator holds its own
Check until no backup is running, but cannot see the repository host's jobs. Check
the schedule above before running one by hand.

**Measure a restic backup's progress by the repository's growth**, against the
source's size. restic reads back each pack it writes, so `/proc/<pid>/io` counts
about twice the source.


### Repairing a diverged replica

syncoid refuses to replicate into a target that shares a name with its source but holds
no snapshot in common, and it is right to: proceeding would mean destroying the target.
The unit fails, its `OnFailure=` reports, and the replica stops advancing until someone
intervenes.

That state is reached by leaving a stale replica alone. Replication stops for whatever
reason; the source goes on snapshotting and pruning to its own schedule; and once the
newest snapshot the target still holds has aged out of the source — 30 days for
`rpool/ROOT`, around two years for `rpool/storage` — no common ancestor remains to send
from. **Staleness matures into divergence,** which is why a dataset reported late is
worth acting on rather than watching.

The repair sets the stale copy aside rather than destroying it, so a copy exists at
every point. Per dataset, on mini-nas:

1. `zfs rename <target> <target>-diverged`
2. Run the syncoid unit, or let the hourly timer fire. It sends in full, so check there
   is room for both copies before starting.
3. Confirm the new target holds a snapshot from today.
4. `zfs destroy -r <target>-diverged`

A `-diverged` dataset has no source counterpart, so syncoid ignores it — but
`zfs-replication-freshness` reports it as stale until step 4, which is correct and
resolves itself.

**Not `syncoid -F`.** It reaches the same end state by destroying the target first,
which removes the only offsite copy before its replacement exists. The set-aside copy is
also the only thing that can answer what the diverged data actually was, which is worth
knowing before concluding it was not wanted.

See [`disk_management.md`](disk_management.md) for the physical disk replacement
procedure, and [`kubernetes.md`](kubernetes.md) for what dies with a stateful workload.
