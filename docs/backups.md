# Backups

What protects what, how long it is kept, and how a failure reaches a person.

Work still outstanding lives in [`todos/backups.md`](../todos/backups.md), not here.

## The layers

| Layer | Covers | Where it lands |
|---|---|---|
| ZFS snapshots (sanoid) | everything on `rpool` | vulcanus, in place |
| ZFS replication (syncoid) | `rpool/storage`, `rpool/ROOT`, `rpool/data` | mini-nas, offsite |
| vzdump → PBS | every guest except `107` | PBS VM 107, on `rpool` |

Snapshots are the instant-rollback layer, exposed to SMB clients as VSS shadow copies
on every share except `borg`. Replication is the offsite copy. PBS is the per-guest
image backup, and the only one that restores a whole guest with a single command.

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
and evicts the retired machine. See *VMID reuse is tolerated, and reported* below.

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

### VMID reuse is tolerated, and reported

A never-reuse rule is a guarantee that has to be kept by hand forever, so there is not
one. The price is data loss rather than inconvenience, and is worth stating plainly.

A PBS group is keyed `vm/<vmid>` with no notion of which machine wrote a given
snapshot, and the vzdump job prunes every group it touches to `keep-last 31`. So a
reused VMID appends the new guest's backups to the retired guest's group and evicts one
old backup per run: **after 31 runs the prior machine is gone from PBS entirely.**
Nothing fails, and the group simply stops being frozen.

Before reissuing a VMID, look at what its PBS group still holds. If any of it matters,
take a copy out first — reuse evicts it within 31 days.

**The report, for when nobody looks.** The signal is intrinsic to the group, so nothing
has to be remembered. vzdump stores the guest config in every backup, and a VM's
`smbios1` UUID is stable for that machine's life and regenerated when a new guest is
built at the same ID. A group whose oldest and newest snapshots carry different UUIDs
therefore spans two machines, and `pbs-freshness` says so.

Containers carry no `smbios1`, so for `ct/<vmid>` the comparison uses `net0`'s
`hwaddr`, which PVE generates per container and which survives a restore of the same
container.

Comparing against a remembered inventory would miss the transition whenever the state
file is lost or the check was down for it. The UUID comparison is retroactive, because
identity is carried in the content. Its reporting lifetime is exactly the decision
window: it begins on the first run after reuse and falls silent once `keep-last 31` has
evicted the last old backup, at the moment there is nothing left to decide.

## Reporting

Nine healthchecks.io checks, all routed to Pushover. Six are fed from mini-nas via
sops-nix secrets, three from vulcanus via `/etc/healthchecks/` written by
[`ansible/backup-monitoring.yaml`](../ansible/backup-monitoring.yaml).

| Check | Fed by |
|---|---|
| `syncoid-vulcanus-storage`, `-root`, `-data` | the syncoid units |
| `sanoid-mini-nas`, `sanoid-vulcanus` | the sanoid units |
| `pool-health-mini-nas`, `pool-health-vulcanus` | an hourly timer per host |
| `zfs-replication-freshness` | a daily timer on mini-nas |
| `pbs-freshness` | a daily timer on vulcanus |

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
that is gone, a live guest the job excludes, and a reused VMID. Once a guest is gone no
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
