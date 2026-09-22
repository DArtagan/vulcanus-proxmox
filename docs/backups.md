# Backups

What protects what, how long it is kept, and how a failure reaches a person.

Work still outstanding lives in [`todos/backups.md`](../todos/backups.md), not here.

## The layers

| Layer | Covers | Where it lands |
|---|---|---|
| ZFS snapshots (sanoid) | everything on `rpool` | vulcanus, in place |
| ZFS replication (syncoid) | `rpool/storage`, `rpool/ROOT`, `rpool/data` | mini-nas, offsite |
| vzdump → PBS | every guest except `107` | PBS VM 107, on `rpool` |
| restic, via K8up | every Kubernetes volume in scope, and seven database dumps | the restic repository, LXC 108 |
| restic, from the repository host | `rpool/storage/{photos,books,filesync}` | the same repository |

Snapshots are the instant-rollback layer, exposed to SMB clients as VSS shadow copies
on every share except `borg`. Replication is the offsite copy. PBS is the per-guest
image backup, and the only one that restores a whole guest with a single command.
restic is the granular layer: one volume, one file, or one database, as it stood on
any night retention still holds.

**The restic repository is not replicated yet**, so it shares `rpool`'s spindles with
the volumes it protects. Until it is, the only offsite copy of Kubernetes state is the
raw `rpool/data` zvol syncoid carries, which restores whole or not at all.

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

LXC 108, `restic-repository`: unprivileged, NixOS, deployed with colmena (see
[`nixos.md`](nixos.md)). It serves `rest-server --append-only` on port 8000 to the
cluster, and runs restic itself against the same repository through the local
filesystem. The repository is the `rpool/backups/restic` dataset, bind-mounted at
`/srv/restic`.

**A dataset on a container, not a zvol on a VM.** A dataset is what lets mini-nas
mount the replica read-only and run `restic check` against it natively. An offsite
copy that cannot be verified independently is not a verified copy. A VM cannot
bind-mount a host dataset, so it would need a zvol, an opaque blob that needs
something booted to verify it, or a network mount, which brings back the CIFS
locking a restic repository must not sit on. The container is outside the cluster,
so the repository survives losing it. It is not the fileserver LXC either: 105 is
privileged and has 512 MB, and `restic prune` loads the whole index into memory.

**The container shifts every uid by 100000.** NixOS gives the `restic` user uid 291,
so the dataset is owned by 100291 on the host (`ansible/zfs.yaml`). Without that,
rest-server cannot write to its own repository.

**The cluster is a hostile writer by design.** K8up holds credentials that a
compromised or misbehaving workload could use to erase its own history.
`--append-only` refuses every delete except of lock files. So `forget` through the
served URL returns `403 Forbidden`, and the same `forget` against the local path
succeeds. It is also why retention runs on the host, as a local client, and never in
the cluster.

**The repository passphrase is the one credential whose loss makes every snapshot
unreadable.** It is escrowed in the password manager, with the REST password beside
it for convenience. Its working copies are SOPS-encrypted:
`kubernetes/k8up/secret-*.sops.yaml` for the cluster, and
`nixos/hosts/restic-repository/secrets.sops.yaml` for the host. The escrow is the
shorter chain: a restore needs the passphrase and the backup media, rather than a
repository checkout, sops, a key, and knowing which field to read.

## Kubernetes volumes and databases

[K8up](https://k8up.io) runs restic inside the cluster, against the repository
above. The Schedules and the coverage check live in `kubernetes/k8up/`; the operator
is `kubernetes/infrastructure/k8up.yaml`.

### What is in scope

**K8up's default is opt-out.** A Schedule backs up every PVC in its namespace,
`ReadWriteMany` ones included, unless the claim carries `k8up.io/backup: "false"`. So
exclusion is a statement made on the claim itself, and two kinds carry it:

- **The SMB shares**, every one of them `ReadWriteMany`. ZFS replication already
  covers them, and the repository host reads photos, books and filesync from
  `rpool/storage` directly. Through a Schedule, restic would read them over CIFS
  onto the same spindles. **`borg-backups-pvc` must never be backed up**: it is the
  4 Ti borg tree.
- **Regenerable state:** pinepods' Valkey cache, rclone's bisync state, borgmatic's
  cache, and the Prometheus, Alertmanager and VictoriaLogs volumes.

Three of those claims come from StatefulSet `volumeClaimTemplates`. The template
carries the annotation, but it is immutable on a live StatefulSet and never reaches
a claim that already exists. So a rebuild that recreates those StatefulSets needs
the annotation set once by hand:

```bash
kubectl annotate -n infrastructure pvc k8up.io/backup=false \
  prometheus-kube-prometheus-kube-prome-prometheus-db-prometheus-kube-prometheus-kube-prome-prometheus-0 \
  alertmanager-kube-prometheus-kube-prome-alertmanager-db-alertmanager-kube-prometheus-kube-prome-alertmanager-0 \
  server-volume-victoria-logs-0
```

**A new share joins the backup set silently**, by the same default. The coverage
check fails any in-scope `ReadWriteMany` claim, because every one here is a share.

### Schedules

One file per namespace in `kubernetes/k8up/`, with no `namespace:` line in its
kustomization. A Schedule backs up *the namespace it lives in*, and kustomize's
`namespace:` is a transformer rather than a default: it would rewrite every Schedule
into one namespace, leave the others backed up by nothing, and Flux would report
Ready.

**`schedule-defaults.yaml` is patched onto every Schedule**, so two things are
written once:

- **The backend, and so the repository string, is byte-identical in every
  Schedule.** The operator hashes that string to find other namespaces' jobs, and a
  Check or Prune waits only for the jobs it can see.
- **Every Job runs as root.** Otherwise it runs as the image's uid 65532. As that
  user, 13 of the 22 volumes in scope hold something it cannot read: syncthing's
  whole volume, headscale's `noise_private.key`, both PhotoPrism signing keys,
  `grafana.db`, all three database data directories. restic then exits 3 and K8up
  counts exit 3 as success, so the backup reports Succeeded without them
  ([k8up#1032](https://github.com/k8up-io/k8up/issues/1032)). **Never `fsGroup`**,
  the other half of that issue's workaround. K8up mounts claims read-only in the
  container but not at the volume source, and for a `local` volume kubelet re-owns
  the whole tree when the backup is the only pod mounting it. PostgreSQL then
  refuses to start on its group-writable data directory.

**The schedule, in UTC**, the operator's clock. The repository host keeps
`America/Denver`, like vulcanus, so its jobs move an hour later in winter:

| UTC | What | Where |
|---|---|---|
| 01:00 | `apps` full: every volume, and all six of its dumps | K8up |
| 01:30 | `infrastructure` full: grafana's volume and dump | K8up |
| 02:00 | `automatic-ripping-machine` full | K8up |
| 02:45, 08:45, 14:45, 20:45 | the coverage check | CronJob |
| Sunday 03:00 | the K8up Check | K8up, on `apps`' `full` |
| 07:00, 13:00, 19:00 | `apps` dumps only | K8up |
| 07:30, 13:30, 19:30 | `infrastructure` dumps only | K8up |
| 08:00 (09:00 in winter) | the mass-file backup | repository host, 02:00 local |
| 09:00 on the 1st (10:00) | `forget --prune` | repository host, 03:00 local |
| 10:00 (11:00) | vzdump | vulcanus, 04:00 local |

The full Schedules are staggered because K8up's exclusivity is per namespace: three
at once would be three restic writers on eight spindles. **The dumps-only Schedules
meet a six-hour database RPO without re-reading every volume.** A Backup's
`labelSelectors` narrow the claims, annotated pods and PreBackupPods it picks up
alike, and the label `k8up-dump: "true"` sits on the dump producers and never on a
claim.

**One K8up Check, on `apps`' `full`.** A K8up Check is structural only: `CheckSpec`
has no options, so it can never read pack data. There is one repository, so a Check
on every Schedule would run the same check three times. The operator holds a Check
until no backup Job on the same repository string is running. It is blind to the
repository host's own jobs, though, so timing alone keeps it clear of those.

**A namespace's volumes on one node go through one pod.** Classic scheduling groups
claims by node, so every `apps` claim on worker-0 mounts into a single Job pod. One
mount that fails means nothing in that namespace is backed up that night. That shows
as one failed Job, not as one failed claim.

### Database dumps

A volume copy of a live database is crash-consistent at best. So each database with
state worth having is also dumped, and the dump streams into restic as a snapshot of
its own at `/<namespace>-<container><extension>`:

| Database | Route | Snapshot path |
|---|---|---|
| pinepods (PostgreSQL 18) | annotation, in its `database` container | `/apps-database.pinepods.sql` |
| photoprism (MariaDB) | annotation, in its `database` container | `/apps-database.photoprism.sql` |
| salamander (MariaDB) | annotation, in its `database` container | `/apps-database.salamander.sql` |
| headscale, linkding, plex (SQLite) | PreBackupPod | `/apps-sqlite.<app>.sqlite` |
| grafana (SQLite) | PreBackupPod | `/infrastructure-sqlite.grafana.sqlite` |

**Two routes, because the tools live in different places.** The relational
databases dump beside their own servers, over the local socket, through a
`k8up.io/backupcommand` annotation on the pod. The SQLite applications have no shell
or no `sqlite3`, so a PreBackupPod of our own brings `keinos/sqlite3`, mounts the
claim, and takes a `.backup` copy. That copy is consistent under a running writer.
The extension carries the application's name because every database container here
is called `database`, and without it three databases would share one restic path
([k8up#1068](https://github.com/k8up-io/k8up/issues/1068)).

**Every command writes to a file of its own first, and streams only a checked one:**

```sh
set -e; f=$(mktemp); trap "rm -f $f" EXIT; <dump> > "$f"; test -s "$f"; cat "$f"
```

- A failed dump streams nothing, so the worst case is a zero-byte snapshot the
  coverage check sees, never a plausible prefix.
- `test -s` catches a tool that exits 0 having written nothing.
- The database is held only for a local write, not for as long as restic takes to
  ingest through the apiserver.
- `mktemp`, never a fixed path: K8up does not serialise Backups, and with a shared
  path an overlapping dump truncates the file the first one is still streaming.

**pinepods is dumped as plain SQL**, not custom format, because a plain dump ends in
a completion marker the coverage check reads. It restores with `psql` 17.6 or later,
because `pg_dump` 18 brackets its output in `\restrict`/`\unrestrict`.

**The blast radius differs by route.** PreBackupPods carry an all-or-nothing
readiness gate: one that cannot become ready stops the whole namespace's backup,
volumes included. Dumps run in their own `prebackup` Job, separate from the volume
Jobs, and stop at the first failure. That Job retries up to six times, each time
rerunning every dump before the failing one.

**`INSECURE_ALLOW_PODEXEC_SPDY_FALLBACK` stays unset.** K8up's old SPDY streaming
silently dropped the ends of dumps ([k8up#1109](https://github.com/k8up-io/k8up/issues/1109)),
and the flag's own help text warns of silent corruption.

**Seven SQLite databases get a file copy and no dump:** stump, mumble,
speedtest-tracker, youtube-dl, headplane, beets, and ARM's `arm.db`. All are
journal-mode rather than WAL, with negligible write rates, so a file copy is very
likely consistent. "Very likely" is the honest word, and beets could be torn
mid-import. A known limit, not an oversight.

### The operator

- **Leader election is off and the Deployment is `Recreate`.** With leader election
  on, controller-runtime renews its Lease every two seconds against etcd's spinning
  disks. The comment in `k8up.yaml` carries the rest.
- **The push of per-item stats to a Pushgateway is off** (`BACKUP_PROMURL: ""`).
  K8up groups those pushes by namespace, and each push replaces the previous one, so
  a Pushgateway would hold one item per namespace. The snapshot `summary` holds the
  same numbers per item, and the coverage check reads them there.
- **Its metrics carry the Backup's namespace in `exported_namespace`**, because the
  scrape's own `namespace` is the operator's. **`k8up_schedule_last_job_succeeded`
  never covers a backup:** only the Check, Prune, Restore and Archive controllers
  update it.

## Retention

sanoid on vulcanus, from [`ansible/templates/sanoid.conf`](../ansible/templates/sanoid.conf):

| Dataset | hourly | daily | monthly |
|---|---|---|---|
| `rpool` (recursive) | 36 | 30 | 24 |
| `rpool/backups` | 36 | 30 | 12 |
| `rpool/ROOT`, `rpool/data`, `rpool/proxmox_backup_server` | 0 | 30 | 0 |

sanoid on mini-nas, from `~/repositories/mini-nas/configuration.nix`:

| Dataset | hourly | daily | monthly |
|---|---|---|---|
| `…/vulcanus/storage` (recursive) | 24 | **60** | 24 |
| `…/vulcanus/data`, `…/vulcanus/ROOT` | 0 | 30 | 0 |

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

restic retention runs on the repository host, monthly, as one policy for every
host-and-path group:

```
restic forget --prune --keep-last 10 --keep-hourly 24 --keep-daily 30 \
  --keep-weekly 8 --keep-monthly 24 --keep-tag decommissioned --max-repack-size 20G
```

Every volume and every dump is its own group, so each keeps its own history.
Nothing in the cluster can prune: append-only refuses it.

**A retired application's snapshots are kept by tagging them.** Its group stops
receiving, so it ages out on the ordinary windows, and months later is exactly when
it is wanted. On decommission, tag its final snapshots on the repository host:

```bash
restic tag --add decommissioned --host <namespace> --path /data/<pvc> latest
```

`--keep-tag decommissioned` then keeps it through every prune. The coverage check
reports a group whose claim or producer is gone, tagged or not.

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

`latest` must be narrowed with `--host` and `--path`, or it names the newest snapshot
in the whole repository, which will not contain what is wanted. The host is the
namespace, and the path is `/data/<pvc>` for a volume.

- **A volume:** a K8up `Restore` into a scratch claim or the original, with
  `podSecurityContext: {runAsUser: 0}` like the Schedules, or every restored file
  belongs to 65532. Restoring in place means scaling the application to zero first.
  Or restore on the repository host with `restic restore latest --host <ns> --path
  /data/<pvc> --target <dir>`.
- **A relational dump:** `restic dump --host apps --path <path> latest <path>`, piped
  into `psql` or `mariadb`.
- **A SQLite dump:** the same `restic dump`, into a file. Scale the application to
  zero, put the file in place, and remove its `-wal` and `-shm`.

**A restore into a fresh claim does not reproduce the claim's own root directory.**
K8up restores the *contents* of `/data/<pvc>`, so the root keeps the fresh
directory's `0777 root:root`, where syncthing's was `0700` and uid 1000. The
snapshot records the original: `restic ls -l <snapshot> /data/<pvc>` shows it on its
first line. Apply that mode and owner after the restore. A restore into the original
claim keeps the root it already has.

**Both canaries restored exact.** ARM's volume came back byte-identical: all 47
entries matched in path, type, mode, owner, size, sub-second mtime and SHA-256.
syncthing's identity -- `cert.pem`, `key.pem`, `config.xml`, and the HTTPS pair --
came back identical, where only its live index databases had moved on.

## Reporting

Twelve healthchecks.io checks, all routed to Pushover. Six are fed from mini-nas via
sops-nix secrets, three from vulcanus via `/etc/healthchecks/` written by
[`ansible/backup-monitoring.yaml`](../ansible/backup-monitoring.yaml), two from the
restic repository host via sops-nix, and one from a CronJob in the cluster.

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
dataset that was never replicated has no stale snapshot to look wrong.

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
K8up's own signals cannot say it. Its job counters carry no claim label, a Backup
reports Succeeded when restic read nothing, and no metric records a backup's last
success. So `kubernetes/k8up/coverage/backup_coverage.py` lists the repository's
snapshots through rest-server and compares them with the cluster. It covers every
PVC, every pod carrying `k8up.io/backupcommand` and every PreBackupPod,
cluster-wide. It reads nothing from git, where some live claims have no literal
declaration, and nothing from the namespaces that have a Schedule, which could never
see a namespace created later. Its shape and its tests follow `pbs-freshness`.

It **fails**:
- a claim or dump never backed up, once past its first night;
- a volume whose newest snapshot is over 26 h old, or a dump over 7 h;
- an in-scope `ReadWriteMany` claim;
- a volume that went empty, or any empty dump;
- an item under half its previous size, from 1 MiB up;
- a current dump that is not whole.

It **reports**:
- exclusions;
- new items;
- an always-empty claim;
- a group whose claim or producer is gone;
- restic's per-item count of unreadable files.

A dump is judged whole from the store, by its own format:
- A SQLite copy's header states page size and page count, whose product must equal
  the snapshot's byte count.
- A SQL dump must carry its completion marker in its last kilobyte.

k8up#1109 once cut about 1% off dumps with no error anywhere, far inside what a size
comparison can see.

The unreadable-file count exists only in each backup Job's log, so it lasts only as
long as the Job's pods: until K8up prunes them, or their node restarts. The summary
line says how many items' counts were read, so "none read" never looks like "all
clean".

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

The restic repository holds about 302 GiB. The mass files account for 247.6 GiB of
it, since restic's compression saves little on JPEG. The first full run of the
Kubernetes volumes added 59.4 GB. An ordinary night adds tens of megabytes: the
volumes' first scheduled night added 68 MB. Each six-hourly dumps run adds about
what changed since the last one, because uncompressed dumps deduplicate.

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

**Do not run restic's exclusive operations inside a K8up window.** `prune`, `check`,
`forget` and `tag` take an exclusive lock. K8up never passes `--retry-lock`, so a
backup that meets the lock fails at once, and a K8up Check that meets one of the
host's exits 11. The schedule above keeps them apart. A one-off by hand should look
at it first.

**`/proc/<pid>/io` does not measure a restic backup's progress.** restic writes each
pack to a temporary file and reads it back to copy and verify it, so `read_bytes` and
`rchar` both count its own traffic: 617 GiB of `rchar` against 296 GiB of source on
the first mass-file run. The repository's growth against the source's size is the
measure that means something.


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
