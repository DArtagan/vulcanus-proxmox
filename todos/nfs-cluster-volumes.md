# Serve the cluster's media volumes over NFS instead of SMB

## Opening prompt

> The cluster reaches the fileserver's shares over SMB through `csi-driver-smb`,
> and one smbd process per node serves every pod on it, single-threaded. NFS
> from the host measured 3–15x faster for directory walks and small-file
> opens. Read `todos/nfs-cluster-volumes.md` and `docs/shared-storage.md`.
> Phase 1 moved the books share and is finished; *Suggested order* says what
> comes next. *The permission model* is the target every phase builds toward.
> Later phases wait on `todos/smb-charset-utf8.md` for any consumer that
> stores paths, then move the rest one PVC at a time.

Verified **2026-10-12/13**, phase 1 **2026-11-05 to 2026-11-07**. Measurements
are in [nfs-cluster-volumes.evidence.md](nfs-cluster-volumes.evidence.md).

## Where things stand

**Phase 1 is finished** (2026-11-07; spec review #29, work reviews #30, #31
and #32). Books is served to the cluster over NFS from the hypervisor at the
library tier, with Stump and bookdrop on it. The cluster's account is
`piraeus` with a password only in SOPS, the groups are `piraeus` and
`storage-read`, and restic reads books through `storage-read`. How all of it
works, where it is declared and what to do when it breaks is in
`docs/shared-storage.md`; this spec does not repeat it.

**Next is step 2 of *Suggested order*: land `smb-charset-utf8`.** Its spec was
verified 2026-09-02, so re-verify it before acting on it.

What phase 1 established that later phases rely on:

- **The default ACL holds under squash.** A pod with a 022 umask writing
  through NFS into a directory carrying `u::rwx,g::r-x,o::---` produced 0640
  files and 2750 directories, inherited two levels down (2026-11-07). Writers
  need no umask of their own for the library tier. The shared tier's default
  ACL is not tested.
- **Test a writer before cutting it over** with a throwaway static PV of the
  same source and options and a pod with the workload's security context,
  then read the result with `stat` on the hypervisor. `chown` under squash
  fails with EPERM, so images that chown their mounts at start will fail.
- **Taking a tree that restic mounts to the library tier:** set
  `acltype=posix` on its dataset in `zfs.yaml` first, since `setfacl` fails
  without it, and take group write off by hand before adding it to
  `nfs_exports`. The ownership task chowns to gid 1200 before it chmods, so a
  group-writable tree would be writable from the restic container between the
  two. photos and filesync are both mounted there.
- **Measure what an app stores before moving it**, as Stump's was: read its
  database for paths with non-ASCII or mangled names, and what is attached to
  them. Stump lost one 0% reading session and re-added 19 books under their
  real names; its *Clean library* removed the old entries.
- **Don't cut over a claim the ripper holds while it is ripping or
  transcoding.**

## The permission model

The target every phase builds toward, settled with Will on 2026-11-05 (see
*Decisions already made*). One identity writes for the cluster, guests are
one shared account, and ownership rather than Samba options decides what each
may do. The identities and the library tier as built are in
`docs/shared-storage.md`.

### Still to come

- **A dedicated guest account** replaces `nobody`, which is also NFS's default
  anonymous uid and so makes ownership ambiguous. It comes in the media phase,
  which re-owns `nobody`'s 48,823 video files anyway, and is one `guest
  account =` line in Samba. Its name is not chosen yet. Logging guests in as
  themselves is the long-term direction and needs nothing on disk to change:
  personal accounts would join the same groups.
- **`storage-write`** (new gid): the guest account and `piraeus`.

### The tiers

| tier | where | owner : group | dirs / files | guests | cluster |
|---|---|---|---|---|---|
| **library** | books; in media, the `audio` and `video` directories and everything in them except each one's `import` | `piraeus` : `storage-read` | 2750 / 0640 | read | write |
| **shared** | `audio/import`, `video/import`, `games`, and everything else in media | guest account : `storage-write` | 2775 / 0664 | write | write |

Nothing is world-writable, and the libraries are not world-readable. Under
the library rule `video/config` is library too; on 2026-11-05 it held one
guest-written JSON file, a copy of one at the media root.

What the cluster writes into media, measured 2026-11-05: the ripper into
`video/import/automatic-ripping-machine` and `audio/import` (with `UMASK:
0o002` already), youtube-dl into `video/import/youtube-dl`, pinepods into
`audio/podcasts` and `podcasts-legacy`, beets into `audio/music` and
`audiobooks`, filebot from `video/import` into `movies` and `shows`.
media-toolkit-webtop is a person's desktop running as the cluster, so the
libraries are writable from it.

### How it holds in media

- **Libraries are read-only to guests because guests are neither their owner
  nor in a group that may write.** No Samba setting carries it. `force user =
  piraeus` on `[media]` would undo it, so `[media]` carries a comment saying
  so when the media phase lands.
- **`[media]` keeps the guest identity** with `force group = storage-write`,
  so guest writes in shared areas take the write group.
- **The ownership task in `ansible/nfs.yaml` learns per-path tiers.** Today it
  holds every export to the library tier, which on media would take guests'
  write away.
- **Once the cluster is off SMB**, the cluster-only shares (`[audio-rw]`,
  `[photos]`, `[filesync]`) go, and `piraeus` keeps its Unix account as owner
  but loses its Samba login.

### Alternatives rejected, and why

- **Mapping `fileshare` into the restic container as it stood.** The group
  could write everywhere, so restic would have gained write over its own
  source. Under this model the group is read-only, which is what makes the
  mapping safe.
- **Guests forced to `piraeus` on `[media]`.** Anyone on the LAN would own
  everything, the audio library beets manages included, without logging in.
- **Library datasets mounted read-only into the fileserver.** About 2.6 TB of
  copies (movies 1.3 TB, shows 0.75 TB, podcasts 300 GB, music 190 GB,
  audiobooks 79 GB), held twice for up to two years by media's 24-month
  snapshots and its hand-made `@20210110`, on rpool and on mini-nas. filebot's
  moves from the inbox would become copies. Proxmox bind-mounts each mount
  point non-recursively, so every dataset needs its own, and the Terraform
  provider cannot make one read-only. Ownership is enforced by the kernel all
  the same.
- **Splitting `audio/import` (415 GB) into its own dataset.** It was for a
  writable inbox inside a read-only mount, which the tiers make unnecessary.

### Where this departs from a fresh design

On identity and permissions it is the design a fresh build would take. The
departures are on layout and the fileserver, each kept deliberately:

- **One `media` dataset** mixing libraries, inboxes and people's archives
  (`backups/`, `Benjammin`, `Moms Back up`, `Pictures`, `will`). A fresh build
  would separate them, perhaps a dataset per media type with `recordsize=1M`.
  Not worth the copies above. If it is ever done, do it before media's NFS
  phase, since exports follow datasets.
- **4.6 TB of inboxes under media's 24-month snapshots.** The price of keeping
  inbox and library in one dataset, so moves stay renames.
- **A privileged fileserver**, whose root is host root over every mounted
  dataset. Outside this project, with the host firewall and NFS
  authentication, which Will is taking on separately.

## Why

Large-file throughput over SMB is fine: disk-bound at ~230–265 MB/s cold.
Metadata is not. Walking `media/audio` (39,930 entries) takes 0.11 s on the
host, ~4 s over SMB from the host and 5–46 s from a pod. smbd spends about
four `stat`s, an `open`/`close`, ~15k `chdir`s and ~26k `getcwd`s on a walk of
that size, and it is CPU-bound in userspace while doing it.

**All SMB traffic from a node goes through one smbd process**, because
`csi-driver-smb` mounts each share once per node and the kernel CIFS client
multiplexes them over a single session. On worker-0 that one process serves
every share for every pod. It averaged 14% of a core over four days, but a
Plex scan pins it, and every other pod's file access then queues behind it.
The 24–46 s walks taken while it was busy are consistent with that. Causation
was not proven.

Samba tuning was measured and has run out of road. `ea support = no` (applied
2026-10-13, commit `2252aa59`) gave −23% on walks; Samba 4.19/4.22 would give
another ~30% on opens. Neither changes the one-process-per-node shape. NFS
does: the kernel server runs 16 `nfsd` threads, and it used 0.4 s of CPU for
a walk that cost smbd 3.3 s.

## Prerequisite: smb-charset-utf8

[smb-charset-utf8.md](smb-charset-utf8.md) renames the ~150 Latin-1 names to
UTF-8 and switches Samba to `unix charset = UTF-8`. After it, SMB and NFS
present byte-identical names, and nothing stored in Plex, beets or photoprism
changes meaning across the move. Do not move a consumer that stores paths
before it lands, unless what it stores against the changed paths is measured
and can be lost, as Stump's was. Read-only consumers that store nothing, such
as podbook-rebound, could go earlier.

**Mangled names change too, and the charset fix does not cover them.** Samba
shows a name Windows cannot hold (a trailing dot or space, or one of
`:*?"<>|\`) as an 8.3 alias such as `IOPOL1~3`, and NFS shows the real one.
On 2026-11-05 `rpool/storage` held three: one in books, which Stump re-added,
and two in `media/video`. Before Plex moves, find what it recorded for those
two.

## Open questions

- **One set of options for every export on a dataset.** `media`,
  `media/audio`, `media/video` and `media/games` are all one dataset. Whether
  knfsd keeps different options for different directories exported from one
  dataset apart reliably was **not verified**. Avoid depending on it, and keep
  `no_subtree_check`. Read-only comes from the PV or volume mount either way,
  since both workers run readers and writers of the same trees.
- **xattrs.** FileBot's `user.net.filebot.*` attributes on 291 video files are
  invisible over SMB since `ea support = no`. NFSv4.2 supports user xattrs
  (RFC 8276); whether this server and client pass them through was **not
  tested**. If they do, filebot regains its metadata by moving.

## Migration mechanics

A PV's volume source and a bound PVC's `volumeName` are immutable, so a share
cannot be switched in place. Create new objects beside the old ones
(`video-nfs-pv` and `video-nfs-pvc`, say), point each workload at the new
claim, and prune the old pair once nothing mounts it. Rollback is a revert.
`docs/shared-storage.md` has the PV's shape.

- Every SMB PV is `Retain`, so deleting the old PVC/PV leaves the data alone.
  Check this again before pruning anything. `docs/kubernetes.md` has the
  inventory rule.
- **Two SMB PVs of one share on one node will not unstage**
  (`docs/shared-storage.md`, *When it breaks*). `audio-rw-pv`,
  `audio-rw-arm-pv` and `audio-rw-beets-pv` all mount `audio-rw`, and
  `video-pv` and `video-arm-pv` both mount `media/video`. Expect it at every
  cutover that empties a node of such a pair. It ends when the pairs do.
- `csi-driver-smb` and the `smb-credentials` secret stay until the last SMB PV
  is gone. Desktops keep SMB permanently, so Samba stays regardless.

Current SMB claims and what declares them (Deployments and CronJobs,
2026-11-07):

| PVC | share | consumers |
|---|---|---|
| `video-pvc` | `media/video` | plex, filebot, youtube-dl, reelbarrow, media-toolkit-webtop |
| `audio-rw-pvc` | `audio-rw` | plex, podbook-rebound, pinepods, media-toolkit-webtop, podcast-feed-snapshot (CronJob) |
| `audio-rw-beets-pvc` | `audio-rw` (uid/gid 1000 mount) | beets-flask, beets-import and beets-replaygain (CronJobs) |
| `photos-pvc` | `photos` | photoprism, salamander |
| `syncthing-pvc` | `filesync/syncthing` | syncthing, rclone-dropbox (CronJob) |
| `video-arm-pvc`, `audio-rw-arm-pvc` | `media/video`, `audio-rw` | automatic-ripping-machine (worker-1) |
| `audio-pvc`, `games-pvc`, `media-pvc` | `media/audio`, `media/games`, `media` | none |

The three unused claims can be pruned rather than moved. `audio-rw-beets-pv`
exists only to set uid/gid 1000 on a CIFS mount, which NFS has no equivalent
for and does not need: beets's writes arrive as `piraeus` like everyone's, so
its claim moves to the same audio volume.

## Suggested order

1. ~~**Phase 1, books.**~~ Finished 2026-11-07; see *Where things stand*.
2. **Land `smb-charset-utf8`.**
3. **Move Plex's `video` read:** largest library, heaviest scanner, nothing to
   write. Compare scan time before and after.
4. **Move the other writers one claim at a time,** testing each image's
   startup and the modes of files it creates. photos and filesync take the
   library tier as they move, as books did, since restic reads them the same
   way.
5. **The media tiers**, once media's writers are on NFS: the guest account and
   `storage-write`, `[media]`'s `force group` and warning comment, per-path
   tiers in the ownership task, and the re-own of libraries and shared areas.
   It replaces today's 777 directories, the 25 root-owned and 13
   `nobody`-owned files in audio, and the mixed 744 modes.
6. **Retire the cluster's SMB** once nothing mounts it: `csi-driver-smb`,
   `smb-credentials`, the cluster-only shares and `piraeus`'s Samba login.
7. Bring `docs/shared-storage.md` up to date with each phase, and close the
   spec.

## Decisions already made

- **Desktops stay on SMB.** Only the cluster's volumes move.
- **One account for the cluster's storage, renamed from `rancher`** (Will,
  2026-11-05): "'rancher' is a legacy filename. If we're setting a new
  standard, we should take this opportunity to give it a better name." Named
  `piraeus`: "`piraeus` is good."
- **Host changes run after Will approves the review that holds them**
  (2026-11-05): "Once I've reviewed #29 and approved, then you can run them."
  Every phase-1 host change followed that rule.
- **Exports in `/etc/exports.d`, not `sharenfs`**; **the hypervisor's address
  in plaintext in the PV** (Will, 2026-11-05, choosing the recommended option
  each time). What the address discloses, and the host's firewall and NFS
  authentication, Will is taking on outside this project: "I'll handle it on
  my own."
- **Never world-writable** (Will, 2026-11-05): "Let's enforce never
  world-writable."
- **Guests stay one shared account for now** (Will, 2026-11-05): "Long-term it
  would be good for guests to log in as themselves, but for the
  short-to-medium term future we'll need to continue using one account for
  them all." A dedicated guest account replaces `nobody`: "confirmed."
- **Media is guest-writable except its libraries** (Will, 2026-11-05): "most
  of `/media` should be write-able by guests. It's just the select 'library'
  directories that are best as read-only (books, music, movies, etc.)" Which
  ones: "all audio folders, besides import, are libraries. Same for all
  `video` folders besides import." And `games`: "Let's align games with the
  `shared` settings."
- **Groups renamed, numbers kept** (Will, 2026-11-05): `fileshare` became
  `storage-read` ("it's perfectly all-right to rename `fileshare`"), the new
  write group is `storage-write`, and gid 1100 went from `users` to
  `piraeus`, all "confirmed."
- **No guest access to books yet** (Will, 2026-11-05): "Let's hold off on
  making books a read-only share, for now." `[books]` was removed instead
  (Will, 2026-11-07), since nothing mounted it.
- **SMB tuning from the r/OpenMediaVault thread was measured and rejected**
  (2026-10-13): `use sendfile`, `aio … size = 16384`, socket buffer options and
  `write cache size` (removed from Samba) changed nothing on this workload.
  `min receivefile size = 16384` halves smbd CPU on large writes but does not
  touch metadata. Do not revisit these as an alternative to this spec.

## Wrong turns not to repeat

- **Client mount options do not help SMB here.** `actimeo=1`, `actimeo=60` and
  `cache=loose` all walked in 3.7–4.1 s. The cost is server-side.
- **Don't benchmark a cold NFS walk against a warm SMB one.** The NFS client
  caches attributes aggressively: a second walk on the same mount took 0.04 s.
  The fair comparison is a fresh mount, which is what the evidence file
  reports.
