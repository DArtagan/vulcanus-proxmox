# Shared storage

The datasets under `rpool/storage` on the hypervisor — `media`, `photos`,
`filesync` and `books` — and how people, the cluster and the backups reach them.
What protects them is in [backups.md](backups.md); the disks beneath them in
[disk_management.md](disk_management.md).

## Who reaches what, and how

| client | protocol | served by | datasets |
|---|---|---|---|
| desktops on the LAN | SMB, as guests | the fileserver LXC, `192.168.0.105` | `media` |
| the cluster | NFS | the hypervisor, `192.168.0.111` | `books` |
| the cluster | SMB, through `csi-driver-smb` | the fileserver LXC | `media`, `photos`, `filesync` |
| restic | a bind mount | — | `photos`, `books`, `filesync`, into LXC 108 |

**People use SMB and always will.** The cluster's volumes are moving to NFS one
claim at a time, because one smbd process serves every pod on a node and
metadata-heavy work (a Plex scan, a directory walk) queues every other pod behind
it. NFS comes from the hypervisor itself, which holds the datasets, so the
fileserver is not in the cluster's path for anything that has moved.

The fileserver is a privileged container that bind-mounts `media`, `filesync`
and `photos`. It does not mount `books`, which nothing reaches over SMB.

## Identities

| name | id | who it is |
|---|---|---|
| `piraeus` | uid 1100, primary group `piraeus` (1100) | the cluster. Every NFS request is squashed to it, and it is the only SMB login on the cluster's shares. Its password is in `kubernetes/infrastructure/smb-credentials.sops.yaml` and nowhere else. |
| `storage-read` | gid 1200 | may read the libraries and nothing more. Members: `piraeus`, `nobody`, and `restic` in the restic container. |
| `nobody` | uid 65534 | desktops. `[media]` takes anonymous connections as this guest. |

The names exist on the fileserver and, for `storage-read`, in the restic
container. The hypervisor has none: its files carry the numbers alone.

**With NFS's `sec=sys`, the server trusts the node, not the pod.** Anything that
can start a pod with a claim on an export writes as `piraeus`, so what separates
workloads is which claims each mounts and whether read-only, not uids. That is
why the cluster has one identity rather than one per app: separate ones would
look like isolation without being it. The cost is that a file does not say which
app wrote it. The gain is that squashing refuses `chown` and setuid, and that no
tree can end up owned by an account its writers are not.

## Libraries

A **library** is a tree the cluster writes and everything else at most reads.
`books` is one.

- Owned `piraeus:storage-read`, directories `2750`, files `0640`: the owner reads
  and writes, the group reads, nobody else does either.
- Directories are setgid and carry a default ACL of `u::rwx,g::r-x,o::---`, so
  whatever creates an entry, and whatever its umask, it comes out `2750` or
  `0640`. Over NFS the umask is the pod's own, so without this every writer would
  need configuring.
- `ansible/nfs.yaml` holds every export to this, on every run: it re-owns
  anything owned otherwise, resets modes, and sets missing default ACLs. Running
  it is safe on a library and **wrong on any tree that is not one**, since it
  would take every other account's access away.

`photos` and `filesync` are not libraries yet. They are world-readable, and
restic reads them through that. `ansible/zfs.yaml` keeps both free of
world-writable entries.

## How restic reads

The restic container is unprivileged, so host ids reach it shifted by 100000 and
host-owned files appear as `nobody`. One host id is passed through unshifted:
gid 1200, so `restic`, in `storage-read` inside, reads the libraries through the
group. Four `lxc.idmap` lines in `/etc/pve/lxc/108.conf` and `root:1200:1` in
`/etc/subgid` do it, written by `ansible/zfs.yaml`.

The container mounts its trees read-write, since the Terraform provider cannot
mark a mount read-only. **It is read-only because the group grants read and
nothing more**, and because nothing it mounts is world-writable. Give
`storage-read` write on anything mounted into it, and the backup host can change
its own source.

## Where it is declared

| what | where |
|---|---|
| exports, server options, library tier | `ansible/nfs.yaml` |
| `sharenfs=off` on `rpool/storage`, `acltype=posix` on libraries, restic's id mapping, no world-writable entries in restic's other mounts | `ansible/zfs.yaml` |
| accounts, groups, Samba | `ansible/fileserver.yaml`, `ansible/templates/smb.conf.j2` |
| the fileserver's and restic container's mounts | `terraform/main.tf` |
| the cluster's volumes | `kubernetes/infrastructure/*-pv.yaml`, `kubernetes/apps/*-pvc.yaml` |

Exports are a file under `/etc/exports.d`, not ZFS's `sharenfs`. `sharenfs` is
inherited by every child dataset, and set on `rpool/storage` it once exported the
whole tree read-write to the LAN with nothing in this repository saying so;
`zfs.yaml` holds it off. Exports reach only the two workers, as NFSv4.2.

`fileserver.yaml` decrypts `smb-credentials` to set the Samba password, which
SOPS cannot do under Ansible with a passphrase-protected key; its header says how
to run it.

## Adding a dataset

In this order; each step needs the ones before it. Skip any a dataset does
not need.

1. **The dataset.** A `community.general.zfs` task in `ansible/zfs.yaml`, with
   its `pvesm add dir` handler, and `acltype: posix` if it will be a library.
   Run `zfs.yaml`. Snapshots and the offsite copy then follow with nothing
   more: sanoid snapshots `rpool` recursively, mini-nas pulls `rpool/storage`
   recursively, and `zfs-replication-freshness` flags a source dataset that
   never reaches the target ([backups.md](backups.md)).
2. **People, over SMB.** A `mountpoint` on the fileserver in
   `terraform/main.tf`, in the next slot (they must stay contiguous), and a share in
   `ansible/templates/smb.conf.j2`; then `tofu apply` and `fileserver.yaml`.
   If Proxmox holds the mountpoint as pending (`[pve:pending]` in
   `/etc/pve/lxc/105.conf`), restart the fileserver, which drops every SMB
   session for a few seconds.
3. **restic.** A `mountpoint` on container 108 in `terraform/main.tf`, the path
   in `services.restic.backups.massfiles.paths` in
   `nixos/hosts/restic-repository`, `tofu apply` and `colmena apply --on
   restic-repository`, and a restart if the mountpoint is pending. From then
   on, whatever `storage-read` or the world may write in the dataset, the
   container may too (*How restic reads*): list it in `zfs.yaml`'s
   never-world-writable task, or make it a library.
4. **The cluster, over NFS**, once it is a library. If restic already mounts
   it, take group write off by hand first (`find <path> -perm -g+w -exec chmod
   g-w {} +`): the ownership task gives the tree gid 1200 before it tightens
   the modes. Then add it to `nfs_exports` in `ansible/nfs.yaml`, take it out of
   `zfs.yaml`'s never-world-writable task, and run `nfs.yaml`. Then the
   cluster's volume, below.

Removing a mountpoint is not the reverse of adding one; see *When it breaks*.

## Giving the cluster an NFS volume

1. The dataset exported, as in step 4 above.
2. A static `nfs` PersistentVolume naming the hypervisor by address and the
   dataset's path, `Retain`, with `nfsvers=4.2`; and a PersistentVolumeClaim bound
   to it by `volumeName`, carrying `k8up.io/backup: "false"` like every claim on
   these datasets ([kubernetes.md](kubernetes.md)). Use a PV, not an inline `nfs:`
   volume, which Pod Security's `restricted` refuses.
3. Test the workload's startup on it before cutting over: images that `chown`
   their mounts fail, since squashing refuses it.

A PV's source cannot change, so moving a claim from SMB means a new PV and PVC
beside the old ones, pointing the workload at the new claim, and pruning the old
pair once nothing mounts it.

## When it breaks

- **Pods hang, unkillable, in `D` state on a share.** NFS mounts are `hard`: a
  stopped or misconfigured server stalls I/O rather than failing it. Check
  `systemctl status nfs-server` and `exportfs -v` on the hypervisor. Do not make
  the mount `soft`, which turns a stall into corrupted writes.
- **A write fails with `EACCES`.** Look at the tree on the hypervisor, `stat -c
  '%u:%g %a'`, not through the client, which reports its own view. A library
  is put right by running `nfs.yaml`. On an SMB share, the usual cause is a copy
  that kept another uid ([beets.md](beets.md)).
- **A path an app stored no longer exists.** SMB and NFS do not always present the
  same name. Samba converts the fileserver's Latin-1 names and shows names Windows
  cannot hold (a trailing dot or space, `:*?"<>|\`) as 8.3 aliases like
  `IOPOL1~3`; NFS shows the bytes on disk. An app that recorded a path over one
  sees the file as missing over the other.
- **A node's SMB mounts will not unstage after its pods are gone**, kubelet
  logging "still mounted by other references". Two PVs of the same share on one
  node share one CIFS mount underneath, and kubelet counts each as holding
  the other. Unmount both staging
  paths by hand from that node's `csi-smb-node` pod (`umount
  <staging path>/globalmount`), and kubelet finishes.
- **`usermod` refuses `piraeus`**, "currently used by process". smbd runs each
  node's session as it for as long as the cluster mounts anything over SMB. Edit
  `/etc/passwd` (and `/etc/shadow`, `/etc/group`, `/etc/gshadow` as needed)
  directly, with a backup, and check with `pwck -r` and `grpck -r`.
- **restic fails on the libraries after container 108 was recreated or
  restored.** The `lxc.idmap` lines are raw configuration that a new container
  does not have. Run `zfs.yaml` and restart the container.
- **A container keeps a mount that was deleted.** A restart applies a
  container's pending values but not its pending deletes. Stop it, `pct set
  <vmid> --delete <mpN>`, and start it.
