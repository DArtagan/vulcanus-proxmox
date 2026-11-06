# Serve the cluster's media volumes over NFS instead of SMB

## Opening prompt

> The cluster reaches the fileserver's shares over SMB through `csi-driver-smb`,
> and one smbd process per node serves every pod on it, single-threaded. NFS
> from the host measured 3–15x faster for directory walks and small-file
> opens. Read `todos/nfs-cluster-volumes.md`. Phase 1 moves the books share,
> with bookdrop and Stump on it, and its section below is where to start. *The
> permission model* is the target every phase builds toward. Later phases wait
> on `todos/smb-charset-utf8.md` for any consumer that stores paths, then move
> the rest one PVC at a time.

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
  (`/home/rancher/docker-vulcanus`). It becomes **`piraeus`**, the cluster's
  name: mini-nas is getting a Talos cluster, which would then get an account of
  its own rather than share this one. `cluster`, `k8s-storage` and `svc-files`
  were the alternatives.
- **Both groups are renamed in the same window, numbers kept.** gid 1100, the
  account's primary group, is `users` in `samba_groups`: the playbook
  renumbers Debian's own `users` group, normally 100, to 1100. It becomes
  `piraeus`. gid 1200, `fileshare`, becomes `storage-read`, the read tier of
  *The permission model*. Until the media phase, `[media]` still writes
  through it.
- **The rename touches** the fileserver's account, groups and Samba entry, the
  `valid users` and `force group` lines in `ansible/templates/smb.conf.j2`,
  `samba_groups` and `samba_users` in `ansible/fileserver.yaml`, the gid's
  name in `ansible/nfs.yaml`, the cluster's `smb-credentials`, and the rsync
  example in `docs/beets.md`. No desktop.

**What one squashed identity risks, and what it cannot.** With `sec=sys` the
server trusts the node, not the pod. `apps` carries no Pod Security labels, so
the cluster's default applies: `baseline` enforced, `restricted` only warned
(measured 2026-11-05 by server dry runs: a privileged pod was refused, a pod
with an inline `nfs` volume admitted). Anything able to create a pod there could
therefore mount any export reachable from its node, as that export's squashed
uid. Separate uids per export would therefore
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

### What is written, and what has run

- `ansible/nfs.yaml`, on the hypervisor: re-owns the books dataset to
  1100:1200 (directories `u+rwx,g+rwxs,o+rx`, files gaining `ug+rw,o+r`, and
  world-write the one bit removed), serves NFSv4.2 only through `/etc/nfs.conf.d/cluster.conf`, and
  exports `rpool/storage/books` to the two workers, squashed to 1100:1200, from
  `/etc/exports.d/cluster.exports`. exports.d won over `sharenfs` for readable
  per-client options, and because `sharenfs` is how the unnoticed LAN-wide
  export happened. `ansible/zfs.yaml` holds `sharenfs=off` on `rpool/storage`,
  which every dataset beneath inherits. **Both ran on 2026-11-05**; see
  *Applying it*, step 1.
- `ansible/fileserver.yaml` and `smb.conf.j2`: the account is `piraeus`, its
  primary group `piraeus` and the 1200 group `storage-read`. Its password
  comes from `smb-credentials` itself, through `community.sops.load_vars`, so
  the Secret the cluster mounts with is the only copy; the playbook's header
  says how to run it with a passphrase-protected key. The task setting it
  changes an existing password, which the old one never did, and keeps it out
  of the process list and the output. Ran 2026-11-05.
- A new `smb-credentials` (`piraeus` and the new password, generated
  2026-11-05). An earlier draft also kept the password in a worktree's
  gitignored `.env` for Ansible; that copy went with the worktree, so nothing
  outside the Secret holds it. Deployed 2026-11-05.
- `books-nfs-pv` and `books-nfs-pvc`, Stump and bookdrop on the claim, and
  `rancher-key` removed. `docs/tailnet.md`, `docs/network.md`, `docs/beets.md`
  and CLAUDE.md corrected. Deployed 2026-11-05; `books-pvc` and `books-pv`
  pruned after.

### Applying it

The password change is the delicate part. The kernel's CIFS client keeps the
credentials a share was mounted with and reuses them to reconnect, and
`csi-driver-smb` mounts each share once per node, unmounting it only when no pod
on that node uses it. Once Samba's password changes, every existing SMB mount in
the cluster keeps working only until its next reconnect, after a fileserver
restart or a network blip, and then fails. So the change is one short window,
and ends with every SMB consumer stopped at once. On 2026-11-05 that was 14 pods
across both workers: beets-flask, filebot, media-toolkit-webtop, photoprism,
pinepods, plex, reelbarrow, salamander, stump, syncthing and youtube-dl on
worker-0; bookdrop, podbook-rebound and automatic-ripping-machine on worker-1.
List them again before starting, and do not start while ARM is ripping.

1. **The export** (`ansible-playbook zfs.yaml nfs.yaml`). **Done
   2026-11-05**, and a second run changed nothing. `exportfs -v` shows the one
   path to 192.168.0.195 and .196 with `all_squash`, `/proc/fs/nfsd/versions`
   reads `-3 +4 -4.0 -4.1 +4.2`, and nothing under books is off 1100:1200. A
   pod on each worker with bookdrop's security context, mounting through a
   throwaway static PV with the same source and options as `books-nfs-pv`,
   wrote a directory and a file: both landed 1100:1200 under the setgid group,
   at 2755 and 644 from the pod's 022 umask, which the ownership task widens
   to 2775 and 664 on its next run. `chown` was refused with EPERM. Probes
   removed. `/etc/exports.d/zfs.exports`, ZFS's own file for `sharenfs`, was
   42 bytes before the first run and empty after it; what it held was not
   read.
2. **The window. Done 2026-11-05**, with three surprises recorded after the
   steps.
   1. On the fileserver, first `getent group users fileshare` and
      `id rancher`: the playbook implies `users` is gid 1100, which was not
      checked on the host. Then `usermod -l piraeus -d /nonexistent rancher`,
      `groupmod -n piraeus users`, `groupmod -n storage-read fileshare` and
      `pdbedit -x -u rancher`, then `ansible-playbook fileserver.yaml`, which
      asks once for the SSH key's passphrase to decrypt `smb-credentials`.
      The playbook describes the end state and does not rename; run before
      the renames, it fails on uid 1100 or gid 1100 being taken, which is
      safe. Debian's standard `users` group (gid 100) is then absent; nothing
      in this repository uses it.
   2. Deploy (`wt step deploy`): Flux applies the new `smb-credentials`, the
      NFS volume and claim, and the rest.
   3. Scale every SMB consumer to zero, wait until none has a pod left, and
      scale each back. Each node then mounts its shares afresh, with
      `piraeus`. A rolling restart is not enough: old and new pods overlap on a
      node, so its mount never goes. Suspend the `apps` and
      `automatic-ripping-machine` Flux Kustomizations first, so neither
      reverts the scaling, and the CronJobs that mount SMB
      (`beets-replaygain`, `podcast-feed-snapshot`, `rclone-dropbox`), letting
      any running job finish: an interrupted rclone bisync can need
      `--resync`. Done when `smbstatus -b` in the fileserver shows no session
      from either worker; resuming Flux restores the rest.

   What the window met:
   - **`usermod -l` refuses while a process runs as the user**, and smbd's
     per-node sessions (pid 343 for worker-0) run as uid 1100. The login was
     renamed by editing `/etc/passwd` and `/etc/shadow`, and the
     `storage-read` member lists in `/etc/group` and `/etc/gshadow`, with
     backups in `/root/`; `pwck` and `grpck` were clean after. The home
     directory is still `/home/rancher`: `usermod -d /nonexistent piraeus`
     is still to run, now that the old sessions are gone.
   - **The playbook could not decrypt under Ansible** with a
     passphrase-protected key; see the header of `ansible/fileserver.yaml`.
   - **Two SMB volumes of one share deadlock kubelet's unstaging.**
     `audio-rw-pv` and `audio-rw-arm-pv` mount `//192.168.0.105/audio-rw` at two staging
     paths on worker-1, which share one CIFS superblock, and kubelet refused
     each with "still mounted by other references", naming the other. Both
     were unmounted by hand through the `csi-smb-node` pod on that node
     (`umount <staging path>/globalmount`), after which kubelet finished.
     See *Migration mechanics*.
3. **Check. Done 2026-11-05.** Every consumer came back ready. New sessions
   from both workers authenticated as `piraeus`, and a probe written and
   removed through `audio-rw` landed as 1100:1100, `[audio-rw]` having no
   `force group`. Stump, after a scan, has the 18 books and 4 series under
   their real names, `READY`. One more book moved with them:
   `If at First . . ` ends in a space, which Windows names cannot, so Samba
   showed it as the mangled `IOPOL1~3`. Stump marked the old entries
   `MISSING`, but left the 10 books under the 4 old-name series `READY`;
   its *Clean library* removes them. `books-pvc` and `books-pv` were then
   pruned, after a check that no pod referenced `books-pvc`.
4. **Tighten books to the library tier** of *The permission model*, in this
   order, so backups never lose access:
   1. Map gid 1200 into the restic container: `lxc.idmap` lines in
      `/etc/pve/lxc/108.conf`, `root:1200:1` in `/etc/subgid`, a
      `storage-read` group at gid 1200 in
      `nixos/hosts/restic-repository` with `restic` in it, and a container
      restart. The Terraform provider cannot write raw `lxc.*` lines, and
      whether its later updates to container 108 keep them was **not
      checked**; check before relying on them. Done when a restic run reads
      books with the other bits still set.
   2. Test POSIX default ACLs (`acltype=posix` on `rpool/storage/books`,
      default `g::r-X,o::---`) against a probe written over NFS and one over
      SMB. On a POSIX filesystem a default ACL replaces the umask at
      creation, and knfsd should honour it under squash; **neither is tested
      here**. If it fails, writers' umasks and the ownership task carry the
      modes instead.
   3. Then books goes to 2750/0640, `[books]` gets `force group =
      storage-read`, `create mask = 0640` and `directory mask = 2750`, and the
      ownership task enforces it.
5. Write what is then true into `docs/`: where exports are declared, the
   identity and permission model, the `hard` mount behaviour under what to
   do when it breaks.

Done when a file written by bookdrop through NFS lands as 1100:1200 with the
group bit set, Stump lists the 18 books under their real names, an SMB write
as the renamed account still works on another share, and books is 2750/0640
with restic still reading it.

## The permission model

The target every phase builds toward, settled with Will on 2026-11-05 (see
*Decisions already made*). One identity writes for the cluster, guests are
one shared account, and ownership rather than Samba options decides what each
may do.

### Who is who

- **The cluster is `piraeus`** (uid 1100), always: NFS squashes every request
  to it, and until the cluster leaves SMB it logs in as `piraeus`.
- **Guests are one dedicated guest account**, replacing `nobody`, which is
  also NFS's default anonymous uid and so makes ownership ambiguous. It comes
  in the media phase, which re-owns `nobody`'s 48,823 video files anyway, and
  is one `guest account =` line in Samba. Its name is not chosen yet. Logging
  guests in as themselves is the long-term direction and needs nothing on disk
  to change: personal accounts would join the same groups.
- **`storage-read`** (gid 1200, `fileshare` renamed): the guest account and
  restic, which reaches it through the idmap in phase 1's step 4.
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

### How it holds

- **Libraries are read-only to guests because guests are neither their owner
  nor in a group that may write.** No Samba setting carries it. `force user =
  piraeus` on `[media]` would undo it, so `[media]` carries a comment saying
  so when the media phase lands.
- **`[media]` keeps the guest identity** with `force group = storage-write`,
  so guest writes in shared areas take the write group.
- **Setgid directories** keep each tier's group on everything created beneath.
- **New-file modes** come from POSIX default ACLs if phase 1's test bears them
  out. Otherwise the inbox writers need a 002 umask (the ripper has one;
  youtube-dl would need it), and the ownership task widens or narrows the
  rest on each run.
- **The ownership task in `ansible/nfs.yaml` learns per-path tiers** in the
  media phase. Today it applies one rule to every export.
- **Once the cluster is off SMB**, the cluster-only shares (`[audio-rw]`,
  `[photos]`, `[books]`, `[filesync]`) go, and `piraeus` keeps its Unix account
  as owner but loses its Samba login. `[borg]` needs its own decision first:
  borgmatic writes there over SMB today, and `borg` should not be exported
  under this identity.

### Alternatives rejected, and why

- **Mapping `fileshare` into the restic container as it stood.** The group
  could write everywhere (files `ug+rw`, directories `g+rwxs`), so restic would
  have gained write over its own source. Under this model the group is
  read-only, which is what makes the mapping safe.
- **Guests forced to `piraeus` on `[media]`.** Anyone on the LAN would own
  everything, the audio library beets manages included, without logging in.
- **Library datasets mounted read-only into the fileserver.** About 2.6 TB of
  copies (movies 1.3 TB, shows 0.75 TB, podcasts 300 GB, music 190 GB,
  audiobooks 79 GB), held twice for up to two years by media's 24-month
  snapshots and its hand-made `@20210110`, on rpool and on mini-nas. filebot's
  moves from the inbox would become copies. Proxmox bind-mounts each mount
  point non-recursively (`mount -o bind`, `PVE/LXC.pm`), so every dataset needs
  its own, and the Terraform provider cannot make one read-only. Ownership is
  enforced by the kernel all the same.
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
  dataset, `borg` included. Outside this project, with the host firewall and
  NFS authentication, which Will is taking on separately.

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

**Mangled names change too, and the charset fix does not cover them.** Samba
shows a name Windows cannot hold (a trailing dot or space, or one of
`:*?"<>|\`) as an 8.3 alias such as `IOPOL1~3`, and NFS shows the real one.
On 2026-11-05 `rpool/storage` held three: one in books, which Stump re-added,
and two in `media/video`. Before Plex moves, find what it recorded for those
two.

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

Every cluster export is `all_squash,anonuid=1100,anongid=1200`, and *The
permission model* settles what owns what. Still to handle per writer:

- **New files' modes.** With a 022 umask in the pod, files come out 644/2755
  (measured in phase 1). Default ACLs, tested in phase 1, or per-writer umask;
  see *How it holds*.
- **`chown` under squash fails with EPERM** (measured in phase 1).
  linuxserver-style images chown their mounts at start. Test each writer's
  startup against the NFS volume before cutting it over.

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
- **Where it is declared:** `/etc/exports.d/cluster.exports`, from
  `ansible/nfs.yaml`, with `sharenfs=off` on the datasets. It keeps plain
  exports(5) syntax and per-client options readable. `sharenfs` would tie each
  export to its dataset, which matters only if one is renamed or recreated, and
  was how the unnoticed LAN-wide export happened.

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
- **Two SMB PVs of one share on one node will not unstage.** They share a
  CIFS superblock, so kubelet sees each staging mount as held by the other
  and retries forever. `audio-rw-pv`, `audio-rw-arm-pv` and
  `audio-rw-beets-pv` all mount `audio-rw`, and `video-pv` and
  `video-arm-pv` both mount `media/video`. Expect it at every cutover that
  empties a node of such a pair, and unmount the staging paths by hand as in
  phase 1's window. It ends when the pairs do.
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
which NFS has no equivalent for and does not need: beets's writes arrive as
`piraeus` like everyone's, so its claim moves to the same audio volume.

## Suggested order

1. **Phase 1, books**, as above, through its tightening to the library tier.
   It declares the first export in Ansible, settles the identity, and tests
   default ACLs and the restic mapping on the smallest tree. It comes ahead of
   Plex because a writer tests the identity design, which a read-only consumer
   cannot, and bookdrop could not write without it.
2. Land `smb-charset-utf8`.
3. Move Plex's `video` read: largest library, heaviest scanner, nothing to
   write. Compare scan time before and after.
4. Move the other writers one claim at a time, testing each image's startup and
   the modes of files it creates. photos and filesync take the library tier as
   they move, as books did, since restic reads them the same way.
5. **The media tiers**, once media's writers are on NFS: the guest account and
   `storage-write`, `[media]`'s `force group` and warning comment, per-path
   tiers in the ownership task, and the re-own of libraries and shared areas.
   It replaces today's 777 directories, the 25 root-owned and 13
   `nobody`-owned files in audio, and the mixed 744 modes.
6. **Retire the cluster's SMB** once nothing mounts it: `csi-driver-smb`,
   `smb-credentials`, the cluster-only shares and `piraeus`'s Samba login,
   after the `[borg]` decision.
7. Write the result into `docs/`.

## Decisions already made

- **Desktops stay on SMB.** Only the cluster's volumes move.
- **Books first, as phase 1** (Will, 2026-11-05): "Yes, let's make that the
  first part of the nfs-cluster-volumes project."
- **One account for the cluster's storage, renamed from `rancher`** (Will,
  2026-11-05): "'rancher' is a legacy filename. If we're setting a new
  standard, we should take this opportunity to give it a better name." Named
  `piraeus`: "`piraeus` is good."
- **The host changes run after Will approves the review** (2026-11-05): "Once
  I've reviewed #29 and approved, then you can run them."
- **The cleanup found here belongs in phase 1** (Will, 2026-11-05): "we want
  this all documented so we can address it. Phase 1 sounds like a great place."
- **Exports in `/etc/exports.d`, not `sharenfs`**; **the hypervisor's address
  in plaintext in the PV**; **SMB mounts restaged by scaling every consumer to
  zero and back**, not by rebooting workers (Will, 2026-11-05, choosing the
  recommended option each time). What the address discloses, and the host's
  firewall and NFS authentication, Will is taking on outside this project:
  "I'll handle it on my own."
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
- **Groups renamed, numbers kept** (Will, 2026-11-05): `fileshare` becomes
  `storage-read` ("it's perfectly all-right to rename `fileshare`"), the new
  write group is `storage-write`, and gid 1100 goes from `users` to
  `piraeus`, all "confirmed."
- **No guest access to books yet** (Will, 2026-11-05): "Let's hold off on
  making books a read-only share, for now."
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
