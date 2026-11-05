# Serve the cluster's media volumes over NFS instead of SMB

## Opening prompt

> The cluster reaches the fileserver's shares over SMB through `csi-driver-smb`,
> and one smbd process per node serves every pod on it, single-threaded. NFS
> from the host measured 3–15x faster for directory walks and small-file
> opens. Read `todos/nfs-cluster-volumes.md`. Phase 1 moves the books share,
> with bookdrop and Stump on it, and its section below is where to start. Later
> phases wait on `todos/smb-charset-utf8.md` for any consumer that stores
> paths, then move the rest one PVC at a time.

Verified **2026-10-12/13**, phase 1 **2026-11-05**. Measurements are in
[nfs-cluster-volumes.evidence.md](nfs-cluster-volumes.evidence.md).

## Phase 1: books, with bookdrop and Stump

The first share to move. bookdrop (`todos/bookdrop.md`) is the reason: it is
the first writer into the books share, and on 2026-11-05 no write could land
there at all.

### Why books cannot be written today

A pod with bookdrop's security context and `books-pvc` mounted read-write got
`EACCES` on `mkdir` at the library root. The client allowed it (the CIFS mount
shows `root:1000`, `dir_mode=0775`, and the pod runs as gid 1000). The
fileserver did not: the tree is owned by **uid 1001**, mode 755, and `[books]`
writes as `rancher` (1100). NFS alone would not change this, since writes
squashed to 1100 meet the same 755 directories.

uid 1001 is `mini-nas` on the hypervisor and unknown in the fileserver LXC. The
account exists for replication: `ansible/sanoid.yaml` creates it for mini-nas to
pull with `zfs hold,send`, which never depends on file ownership, so it does not
need to own anything here. 1001 is also `will` on the desktop. Every entry in the
tree dates from 2026-05-26 23:24 local time, during the commits that set up
Stump, and nothing under it has changed since June. The likeliest story is a
copy from the desktop that preserved its uid; git does not record the copy, so
that is inference. `docs/beets.md` describes the same trap on `audio-rw`: a copy
that keeps another uid leaves a tree the share's account cannot write.

### Stump can move with bookdrop

The charset prerequisite below does not hold for books, measured from Stump's
source at v0.1.11 and its database on 2026-11-05:

- **Stump writes nothing to the library in practice.** Its one share-writing
  feature is RAR-to-ZIP conversion, on for this library, which holds no RAR
  files. Uploads are off; the rest goes to its cache under `/config`.
- **It stores paths, and 18 books and 4 series have non-ASCII ones.** Their
  names are UTF-8 on disk, so SMB, converting from Latin-1, shows them as
  mojibake (`China MiÃ©ville`), which is what Stump recorded. NFS shows the real
  bytes, so Stump would see those books as missing and add them anew.
- **What that loses is one reading session, at 0%.** Favourites, bookmarks,
  annotations, reviews and tags are empty for every book.

Moving Stump in the same phase puts the whole tree on one protocol in the
cluster, and corrects those 18 names in Stump.

### The cluster's identity

- **uid 1100 is the cluster's account already.** Desktops connect
  anonymously, which `map to guest = Bad User` makes `nobody`, and `nobody` is a
  valid user only on `[media]`. `[books]`, `[photos]`, `[filesync]` and
  `[audio-rw]` accept only `rancher`, and the only login as `rancher` is the
  cluster's `smb-credentials`. People and the cluster are therefore separate
  already: people as guests on `[media]`, the cluster as 1100.
- **Keep the number, rename the account.** Squashing is by number, so every file
  owned by 1100 stays correct. `rancher` is a name from the Rancher era
  (`/home/rancher/docker-vulcanus`). Candidates, with Will to choose:
  `piraeus` (the cluster's name; mini-nas is getting a Talos cluster, which would
  then get its own account rather than share this one), `cluster`,
  `k8s-storage`, `svc-files`. The group stays `fileshare` (1200), which
  `nobody` shares on `[media]`.
- **The rename touches** the fileserver's account and its Samba entry, the
  `valid users` lines in `ansible/templates/smb.conf.j2`, `samba_users` in
  `ansible/fileserver.yaml`, the cluster's `smb-credentials`, and the rsync
  example in `docs/beets.md`. No desktop.

**What one squashed identity risks, and what it cannot.** With `sec=sys` the
server trusts the node, not the pod, and `apps` enforces no Pod Security, so
anything able to create a pod there could mount any export reachable from its
node, as that export's squashed uid. Separate uids per export would therefore
isolate nothing between cluster workloads. The boundaries are which claims each
workload mounts and whether read-only, exporting single datasets rather than
`rpool/storage` whole, `restricted` enforced on `apps`
([pod-security-namespace-level.md](pod-security-namespace-level.md)), which
refuses inline `nfs` volumes, and ZFS snapshots with the offsite pull, which no
pod reaches, for recovery. A separate identity does matter between people and
the cluster, which already holds, and for backups: `borg` should not be exported
to the cluster under this identity. What one identity costs is knowing which app
wrote a file. It gains that `root_squash` refuses `chown` and setuid, and that
the ownership trap above stops recurring.

### Cleanup found on the way, in this phase

- **The Samba password is `rancher`, in plaintext, in this public repository**
  (`samba_users` in `ansible/fileserver.yaml`), and the cluster's
  `smb-credentials` is exactly `rancher`/`rancher`. SMB is reachable from the
  LAN. Replace it with a generated password held in SOPS, at the same time as
  the rename, so each place changes once.
- **`rancher-key`**, `kubernetes.io/ssh-auth` Secrets in `apps` and
  `infrastructure`, added 2023-01-07 ("Plex plus file transfer"). Nothing in the
  repository or the cluster references either. Delete them.
- **`docs/tailnet.md`** says the fileserver has NFS exports (see *The host's
  exports start from nothing*).

### The work

1. Choose the account's name. Rename uid 1100 with a new password in SOPS,
   and update `smb-credentials` with it.
2. Re-own the books dataset to 1100:1200, directories `2775` so new files
   take the group, everything world-readable for restic, which reads as the
   unprivileged `restic` user. An Ansible task holds it there, with a comment on
   where 1001 came from.
3. Export `rpool/storage/books` only, declared in Ansible, to the two workers,
   `all_squash,anonuid=1100,anongid=1200`, NFSv4.2. Decide between `sharenfs` and
   `/etc/exports.d` here (see *Exports*).
4. A static `books-nfs-pv` and `books-nfs-pvc` (with `k8up.io/backup: "false"`),
   bookdrop on it read-only until its filing goes live, Stump on it, then
   `books-pvc` and `books-pv` pruned once nothing mounts them. `Retain` keeps the
   data either way.
5. Delete `rancher-key`; correct `docs/tailnet.md`.

Done when a file written by bookdrop through NFS lands as 1100:1200 with the
group bit set, Stump lists the 18 books under their real names, and an SMB
write as the renamed account still works on another share.

## Why

Large-file throughput over SMB is fine: disk-bound at ~230–265 MB/s cold.
Metadata is not. Walking `media/audio` (39,930 entries) takes 0.11 s on the
host, ~4 s over SMB from the host and 5–46 s from a pod. smbd spends about
four `stat`s, an `open`/`close`, ~15k `chdir`s and ~26k `getcwd`s on a walk of
that size, and it is CPU-bound in userspace while doing it.

**All SMB traffic from a node goes through one smbd process**, because
`csi-driver-smb` mounts each share once per node and the kernel CIFS client
multiplexes them over a single session. On worker-0 that one process (pid 343
in the fileserver container) serves `audio-rw`, `media`, `books`, `borg`,
`filesync` and `photos` for every pod. It averaged 14% of a core over four
days, but a Plex scan or a borgmatic run pins it, and every other pod's file
access then queues behind it. The 24–46 s walks taken while it was busy are
consistent with that. Causation was not proven.

Samba tuning was measured and has run out of road. `ea support = no` (applied
2026-10-13, commit `2252aa59`) gave −23% on walks; Samba 4.19/4.22 would give
another ~30% on opens. Neither changes the one-process-per-node shape. NFS
does: the kernel server runs 16 `nfsd` threads, and it used 0.4 s of CPU for
a walk that cost smbd 3.3 s.

## The host's exports start from nothing

`rpool/storage` and `rpool/storage/media` had `sharenfs=on`, which exported
`/rpool/storage` and every child dataset **read-write to any LAN address**
(`*(sec=sys,rw,...)`). With `sec=sys` the server believes whatever uid a
client claims. Nothing used it: `/proc/net/rpc/nfsd` showed zero calls
between the 2026-10-08 boot and these measurements. Nothing in this repo set
it either, so it was out-of-band state from some earlier experiment.

It was turned off on 2026-10-13 with
`zfs set sharenfs=off rpool/storage rpool/storage/media`. `exportfs -v` is now
empty, and both datasets carry `sharenfs=off` as a local property.
`nfs-kernel-server` is still installed and running, with no exports. The
exports this project adds must be declared in Ansible, so out-of-band state
like that one cannot come back unnoticed.

`docs/tailnet.md` (the `192.168.0.105:22` row) says the fileserver LXC has
"NFS and SMB exports". It has no NFS: nothing in it listens on 2049. Correct
that row when this lands, if not before.

## What is already proven

- **Talos mounts NFS with nothing added.** A throwaway pod on worker-0 mounted
  `192.168.0.111:/rpool/storage/media/audio` as `nfs4`, `vers=4.2`, through
  kubelet. Both kernels have `nfs` and `nfs4`, and no extension or CSI driver
  is needed. Talos's docs say the NFS client ships in its kubelet image.
- **Use static `nfs` PersistentVolumes, not inline `nfs:` volumes.** An inline
  volume trips the cluster's `restricted` Pod Security warning ("restricted
  volume type nfs"). A PVC does not, and a PV is also the only place mount
  options can be set (Kubernetes volumes docs).
- **The server belongs on the host, not in the fileserver LXC.** The host
  already has `nfs-kernel-server` 2.8.3 installed and running, and the
  datasets live there. Running knfsd inside an LXC is widely reported as
  unsupported on Proxmox, but **that was not checked here**. It was not needed,
  since the host works.
- **Filename encoding diverges between the protocols.** Samba translates the
  Latin-1-stored names to UTF-8 for clients, while NFS returns the raw bytes.
  1 of 3,000 paths taken from the SMB view did not exist over NFS. Any app
  that stored a path read over SMB would lose that file after the move.

## Prerequisite: smb-charset-utf8

[smb-charset-utf8.md](smb-charset-utf8.md) renames the ~150 Latin-1 names to
UTF-8 and switches Samba to `unix charset = UTF-8`. After it, SMB and NFS
present byte-identical names, and nothing stored in Plex, beets or photoprism
changes meaning across the move. Do not move a consumer that stores paths
before it lands, unless what it stores against the changed paths is measured
and can be lost: Stump's was, and it moves in phase 1. Read-only consumers that store nothing (borgmatic,
podbook-rebound) could go earlier, but borgmatic's archives would show every
Latin-1 name as renamed.

## The design work

### Identity: who owns what an NFS client writes

Samba decides ownership today. Kubernetes writes arrive as:

- `nobody:fileshare`, files 0664 / dirs 0775, on `[media]` (force user and
  group, create and directory masks);
- `rancher` (uid 1100) on `[audio-rw]`, `[photos]`, `[books]`, `[filesync]`
  and `[borg]`.

The pods themselves run as root (filebot and pinepods with `PUID=0`, ARM,
beets, salamander) or as image-defined users (Stump `PUID=1000`, syncthing,
photoprism). Over NFS the pod's own uid and umask would reach the disk.

On-disk ownership, 2026-10-12: almost everything is `1100:1100`. `video` is
mostly `65534:1200` (48,823 files, 0664), and `books` is `1001:1100`. Modes are
mixed, including a lot of 0744.

The recommended starting point is `all_squash,anonuid=1100,anongid=1200` on
every cluster export. Every request becomes rancher:fileshare, which can write
to both the rancher-owned trees and the group-writable `nobody:fileshare`
video files. Open questions for the session:

- **New files' modes.** With a 022 umask in the pod, files come out 644/755,
  and SMB guests (who are `nobody`) could not modify them. Options: ZFS POSIX
  default ACLs (`acltype=posix` is currently `off` on these datasets) or
  per-app umask. Measure what each writer actually produces.
- **`chown` and `chmod` under squash fail with EPERM.** linuxserver-style
  images chown their mounts at start. Test each writer's startup against the
  NFS volume before cutting it over.
- **`books` is owned by 1001**, the hypervisor's `mini-nas` account, which owns
  it by uid collision, not use. Phase 1 re-owns it to 1100.

### Exports

- **Restrict exports by client address** to the workers, 192.168.0.195 and
  192.168.0.196. kubelet mounts from the node's address, so this is enough to
  keep the rest of the LAN out. The control plane mounts nothing.
- **Use NFSv4.2 only, on TCP 2049.** pve-firewall is disabled, so nothing else
  is needed on the host.
- **Read-only is a client-side property.** An export's options apply per
  (path, client), and both workers run readers and writers of the same trees.
  Plex reading `video` read-only and filebot writing it share one export entry
  for 192.168.0.195, so read-only comes from the PV/volumeMount.
- **One set of options for every export on a dataset.** `media`, `media/audio`,
  `media/video` and `media/games` are all one dataset. Whether knfsd keeps
  different options for different subdirectory exports of one dataset apart
  reliably was **not verified**. Avoid depending on it, and keep
  `no_subtree_check` (exports(5) default).
- **Where it is declared.** Either `sharenfs=<opts>` on the datasets through
  `community.general.zfs` in `ansible/zfs.yaml`, or `sharenfs=off` plus an
  Ansible-managed `/etc/exports.d/*.exports`. Undecided. The second uses plain
  exports(5) syntax and keeps per-client options readable. The first keeps
  exports tied to datasets, which matters if a dataset is renamed or recreated.

### Failure behaviour

NFS mounts are `hard` by default, so a stopped server stalls processes rather
than returning errors. The SMB PVs are `soft`. The server is the hypervisor
the workers run on, so the outage where they differ is an `nfs-server` restart
or a misconfigured export, not a host loss. Keep `hard`, because `soft` NFS can
corrupt writes. Expect a stuck export to show up as pods hanging in `D` state,
not as I/O errors.

### xattrs

FileBot's `user.net.filebot.*` attributes on 291 video files are invisible over
SMB since `ea support = no`. NFSv4.2 supports user xattrs (RFC 8276) and both
kernels are recent. Whether this server and client pair actually pass them
through was **not tested**. If they do, filebot regains its metadata by moving.

## Migration mechanics

A PV's volume source and a bound PVC's `volumeName` are immutable, so a share
cannot be switched in place. Create **new** objects alongside the old ones
(`video-nfs-pv` and `video-nfs-pvc`, say) and point each workload at the new
claim. Rollback is then a revert, and Flux prunes the old pair once nothing
references it.

- Every SMB PV is `Retain`, so deleting the old PVC/PV leaves the data alone.
  Check this again before pruning anything. `docs/kubernetes.md` has the
  inventory rule.
- **Every new PVC must carry `k8up.io/backup: "false"`**, as every SMB claim
  does. Without it, the backup system would try to copy terabytes of media
  that ZFS snapshots and replication already cover (`docs/backups.md`). The
  coverage check lists exclusions, so it is visible in its report.
- `csi-driver-smb` and the `smb-credentials` secret stay until the last SMB PV
  is gone. Desktops keep SMB permanently, so Samba stays regardless.

Current SMB claims and consumers (running pods, 2026-10-12):

| PVC | share | consumers |
|---|---|---|
| `video-pvc` | `media/video` | plex (ro), borgmatic (ro), filebot, youtube-dl, media-toolkit-webtop |
| `audio-rw-pvc` | `audio-rw` | plex (ro), borgmatic (ro), podbook-rebound (ro), pinepods, media-toolkit-webtop, podcast-feed-snapshot |
| `audio-rw-beets-pvc` | `audio-rw` (uid/gid 1000 mount) | beets-flask, beets-replaygain |
| `audio-pvc` | `media/audio` | beets-import CronJob |
| `photos-pvc` | `photos` | photoprism, salamander, borgmatic (ro) |
| `books-pvc` | `books` | stump |
| `syncthing-pvc` | `filesync/syncthing` | syncthing, borgmatic (ro) |
| `games-pvc` | `media/games` | borgmatic (ro) |
| `borg-backups-pvc` | `borg` | borgmatic |
| `media-pvc` | `media` | none found |
| `video-arm-pvc`, `audio-rw-arm-pvc` | `media/video`, `audio-rw` | automatic-ripping-machine (worker-1) |

`borg` is `rpool/backups/borg`, outside `rpool/storage`, and would need its own
export. `audio-rw-beets-pv` exists only to set uid/gid 1000 on a CIFS mount,
which NFS has no equivalent for. Its replacement is whatever the identity design
settles on.

## Suggested order

1. **Phase 1, books**, as above. It declares the first export in Ansible and
   settles the identity. It comes ahead of Plex because a writer tests the
   identity design, which a read-only consumer cannot, and bookdrop could not
   write without it.
2. Land `smb-charset-utf8`.
3. Move Plex's `video` read: largest library, heaviest scanner, nothing to
   write. Compare scan time before and after.
4. Move the other writers one claim at a time, testing each image's startup and the
   modes of files it creates.
5. Write the result into `docs/`: where exports are declared, the identity
   rule, and the `hard` mount behaviour under "what to do when it breaks".
   Correct `docs/tailnet.md`.

## Decisions already made

- **Desktops stay on SMB.** Only the cluster's volumes move.
- **Books first, as phase 1** (Will, 2026-11-05): "Yes, let's make that the
  first part of the nfs-cluster-volumes project."
- **One account for the cluster's storage, renamed from `rancher`** (Will,
  2026-11-05): "'rancher' is a legacy filename. If we're setting a new
  standard, we should take this opportunity to give it a better name." The
  name is still to choose.
- **The cleanup found here belongs in phase 1** (Will, 2026-11-05): "we want
  this all documented so we can address it. Phase 1 sounds like a great place."
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
