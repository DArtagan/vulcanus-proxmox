# ARM runs as root, so two of upstream's scripts are replaced rather than used

## Opening prompt

> ARM runs as root with its files under `/root`, which is why this repo replaces
> upstream's `arm_user_files_setup.sh` and `armui.sh` instead of running them.
> Read `todos/arm-non-root.md` and move ARM to upstream's `arm` user and
> `/home/arm` layout, so both forks can go. It touches device permissions, the
> SMB volumes' mount options, the local volume's ownership and every `/root` path,
> and ARM restarts the moment its scripts change: do it while ARM is idle.

Verified **2026-11-08**, against ARM 2.24.4 (`a380622`), which was also
upstream's `main`.

## Why ARM runs as root

Since January 2024 (`a68ccea`, "Override everything, lets run as root", then
three commits taking out its chown and chmod steps). The commits give no reason;
what the pod shows is enough of one. Read from the running pod:

- **The drive.** `/dev/sr0` and `/dev/sg0` are `0600 root:root` inside the
  container. The `arm` user (uid 1000) is in `cdrom`, `video`, `optical` and
  `render`, and could open neither.
- **The media.** `/root/video` and `/root/audio` are cifs mounts with
  `uid=0,gid=0,file_mode=0755,dir_mode=0755`: every file reads as root's and
  only root can write. The PVs, `video-arm-pv` and `audio-rw-arm-pv` in
  `kubernetes/infrastructure/`, set no mount options beyond `rw`. On the
  fileserver every write is `piraeus` whatever the pod's uid
  ([`docs/shared-storage.md`](../docs/shared-storage.md)), so this is about the
  client's view only.
- **The local volume.** `automatic-ripping-machine-pvc` (openebs hostpath) is
  mounted twice by `subPath`, at `/root/db` and `/root/logs`, both `0777`
  directories holding root's `0644` files.
- **Every process** in the container runs as root, the UI included.

## What the forks are for

- `scripts/arm_user_files_setup.sh` replaces upstream's, the first script
  phusion's my_init runs. Upstream's moves the `arm` user to `ARM_UID`, and with
  `ARM_UID=0` `usermod -u 0` exits 4, "UID '0' already exists" (measured in the
  pod on a throwaway user), which under its `set -e` stops the container. Ours
  also links `/home/arm/logs/arm.log` and `/home/arm/.MakeMKV` to their `/root`
  places and runs `arm-source-patches.py`.
- `scripts/armui.sh` is upstream's `runsv/armui.sh` without `/sbin/setuser arm`,
  because the UI reads `/root/db`, and `/root` is `0700`.
- `scripts/51-docker-arm.rules` runs `arm-disc-wrapper.sh` as root; upstream's
  rule runs its wrapper under `setuser arm`. `arm-disc-wrapper.sh` sets
  `HOME=/root` for MakeMKV's key.

## What moving to `arm` involves

None of this has been tried.

1. **Device nodes.** Make `/dev/sr0` and `/dev/sg0` group `cdrom`, mode `0660`,
   inside the container. A step in the setup script, which my_init runs as
   root, is the obvious place; whether the generic device plugin can set this
   instead, which would be cleaner, is unread.
2. **The SMB PVs.** `uid=1000,gid=1000` (or `forceuid`/`forcegid`) in both PVs'
   `mountOptions`, and modes that let the owner write. Whether a bound PV's
   `mountOptions` can be changed in place or the PV must be recreated is
   unchecked. Both PVs are ARM's alone.
3. **The local volume.** Its files are root's: chown them to 1000 once, or give
   the pod an `fsGroup`. Check what K8up's backup of it does with the new owner.
4. **Paths.** Upstream's layout is `/home/arm/{media,music,logs,db}`
   (`scripts/docker/start_arm_container.sh`). Every `/root` path moves:
   `RAW_PATH`, `TRANSCODE_PATH`, `COMPLETED_PATH`, `LOGPATH`, `DBFILE` in the
   ConfigMap's `arm.yaml`, the Deployment's mounts, `HOME` in
   `arm-disc-wrapper.sh`, `tools/arm-stamps/stamps.py`, the tests under `tools/`,
   `docs/automatic-ripping-machine.md`, and the specs that quote paths
   (`disc-ripping`, `video-library-ingest`). reelbarrow, in its own repository,
   may hold paths too.
5. **Upstream's setup script, run as is.** It checks that `/home/arm` and
   `/etc/arm/config` are owned by `ARM_UID` and exits otherwise. `/home/arm` is
   `arm:arm` in the image; `/etc/arm/config`, an emptyDir the config-injector
   init container fills, is `root:root`, so its owner needs setting. Upstream PR #1881 fixes that check failing with a
   custom `ARM_GID`; see whether it has merged. The script also sets the
   timezone from `TZ` and copies `arm.yaml` and `apprise.yaml` only when absent,
   which ours already supplies.
6. **What stays ours.** The steps upstream's script does not do: the staging
   folder `/home/arm/arm-incoming`, running `arm-source-patches.py`, and step 1's
   device permissions. They go in a second my_init script beside upstream's,
   named to run after it, rather than replacing it. `armui.sh` and the udev
   rule's runner go back to upstream's, the rule still calling
   `arm-disc-wrapper.sh`, under `setuser arm`.

## What it buys

Upstream's setup script and UI launcher run unmodified, and paths match
upstream's docs and issues. It is not a security change of note: the container
keeps `SYS_ADMIN` for the drive and the namespace is `enforce: privileged`
([`pod-security-namespace-level.md`](pod-security-namespace-level.md)), and the
setup still runs as root under my_init.

## Doing it

Any change under `kubernetes/apps/automatic-ripping-machine/scripts/` restarts
ARM as soon as it reaches `main`, killing a job in progress, so merge with ARM
idle. Verify with a disc of each kind, DVD, Blu-ray and audio CD:

- `ps -eo user,comm` in the pod shows `makemkvcon`, `HandBrakeCLI` and `abcde`
  as `arm`, and the UI too.
- Files the job writes under `video/` and `audio/` arrive on the share as
  before (`piraeus` on the fileserver), and the beets inbox takes the CD.
- `arm.log` has ten `source-patches: applied` lines and the setup script's
  output has no ownership errors.
- MakeMKV finds its key: no "This application version is too old".
