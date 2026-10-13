# Serve the cluster's media volumes over NFS instead of SMB

## Opening prompt

> The cluster reaches the fileserver's shares over SMB through `csi-driver-smb`,
> and one smbd process per node serves every pod on it, single-threaded. NFS
> from the host measured 3–15x faster for directory walks and small-file
> opens. Read `todos/nfs-cluster-volumes.md`. Declaring the exports in
> Ansible comes first, and `todos/smb-charset-utf8.md` must land before any
> writer moves. Then design the identity mapping, prove it on one read-only
> consumer, and move the rest one PVC at a time.

Verified **2026-10-12/13**. Measurements are in
[nfs-cluster-volumes.evidence.md](nfs-cluster-volumes.evidence.md).

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
present byte-identical names, and nothing stored in Plex, beets, photoprism or
Stump changes meaning across the move. Do not move a consumer that stores
paths before it lands. Read-only consumers that store nothing (borgmatic,
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
- **`books` is owned by 1001.** Find what that uid is (mini-nas replication?)
  before squashing writes there to 1100.

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

1. Declare the exports in Ansible: worker addresses only, squashed, v4.2.
2. Land `smb-charset-utf8`.
3. Move one read-only consumer first, Plex's `video` read: largest library,
   heaviest scanner, nothing to write. Compare scan time before and after.
4. Move the writers one claim at a time, testing each image's startup and the
   modes of files it creates.
5. Write the result into `docs/`: where exports are declared, the identity
   rule, and the `hard` mount behaviour under "what to do when it breaks".
   Correct `docs/tailnet.md`.

## Decisions already made

- **Desktops stay on SMB.** Only the cluster's volumes move.
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
