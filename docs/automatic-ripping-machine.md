# Automatic Ripping Machine

[Automatic Ripping Machine (ARM)](https://github.com/automatic-ripping-machine/automatic-ripping-machine) is a containerised DVD/Blu-ray/CD ripping pipeline. Insert a disc; it rips, transcodes, and files it away without further interaction.

This document covers how ARM is integrated into this cluster — from physical drive to finished file — and why certain decisions were made.

## Architecture Overview

```
Physical drive (Pioneer BDR-212U)
        │ SATA
        ▼
Proxmox host (vulcanus)
  /dev/sr0  — block device (sr_mod)
  /dev/sg3  — SCSI generic device
  /dev/optical-drive-sg → sg3  — stable symlink (udev rule)
        │ QEMU scsi-generic passthrough
        ▼
Talos VM: piraeus-worker-1 (192.168.0.196, vmid 911)
  /dev/sr0, /dev/sg0  — optical drive as seen by guest kernel
  /dev/disk/by-id/ata-PIONEER_BD-RW_BDR-212U → sr0  — how the plugin identifies it
        │ devic.es/cdrom device plugin
        ▼
ARM pod (automatic-ripping-machine namespace)
  /dev/sr0, /dev/sg0  — device plugin injects these into the container
```

## The Physical Drive

A **Pioneer BDR-212U** Blu-ray writer is connected via SATA to the Proxmox host on SATA Port 10 (`ata4` in the Linux kernel). The Proxmox host's kernel creates two device nodes:

- `/dev/sr0` — the block-level CD-ROM interface (`sr_mod`)
- `/dev/sg3` — the SCSI generic interface (`sg` module)

The SCSI generic interface is what matters: it allows raw SCSI commands to be issued to the drive, which MakeMKV requires for DVD/Blu-ray.

A udev rule in `ansible/proxmoxer.yaml` creates a stable symlink:

```
/dev/optical-drive-sg → sg3
```

This is matched by drive model name (`BD-RW   BDR-212U`) rather than device number, because `sg` enumeration order can change at boot. The Proxmox host also has a KVM virtual optical device that would otherwise match a type-based rule.

## QEMU Passthrough: scsi-generic, not scsi-cd

The VM is defined with `host_cdrom_passthrough = true` in `terraform/main.tf`, which adds raw QEMU args in `terraform/modules/proxmox_talos_vm/main.tf`:

```
-device virtio-scsi-pci,id=scsihw0,max_sectors=256
-drive file=/dev/optical-drive-sg,if=none,id=drive-cdrom0,format=raw
-device scsi-generic,bus=scsihw0.0,channel=0,scsi-id=0,lun=0,drive=drive-cdrom0,id=cdrom0
```

**Why `scsi-generic` and not `scsi-cd`?**

QEMU's `scsi-cd` device emulates a CD-ROM but does not implement several DVD/Blu-ray-specific SCSI commands:

- `GET CONFIGURATION` — disc type detection
- `READ DVD STRUCTURE` — DVD metadata
- `REPORT KEY` / `SEND KEY` — CSS (Content Scramble System) key exchange for decryption

Without these, MakeMKV reports `dtype=0` (unknown disc type) and fails to open DVDs. `scsi-generic` passes all SCSI commands through to the physical drive unchanged, giving MakeMKV full access.

`readonly` is intentionally **not** set. CSS decryption requires `SEND KEY`, which is a write-direction SCSI transfer. The disc itself is physically read-only; the flag only controls SCSI write commands to the drive, not the media.

Proxmox does not support `scsi-generic` passthrough through its API, so it must be done via raw QEMU args. The virtio-scsi-pci controller must also be created manually here because Proxmox only creates it automatically when a SCSI disk is assigned via the API.

Inside the guest, the Talos kernel recognises the device as SCSI type 5 (optical), loads `sr_mod`, and creates `/dev/sr0` and `/dev/sg0`.

### Transfer size: `max_sectors=256`

The host accepts at most **128 KiB per command** for the drive
(`/sys/block/sr0/queue/max_hw_sectors_kb` on vulcanus). libata sets that for an
ATAPI device, so it is a property of the drive's attachment and not a tunable.

`scsi-generic` does not tell the guest about it. The guest instead takes the
virtual controller's limit, which defaults to `0xFFFF` sectors — 32 MiB. MakeMKV
reads `/dev/sr0` with `O_DIRECT` in 384 KiB requests, the host's `sg` driver
refuses each with `EINVAL`, and QEMU hands that back as a SCSI failure:

```
sr 2:0:0:0: [sr0] tag#190 Sense Key : Illegal Request [current]
sr 2:0:0:0: [sr0] tag#190 Add. Sense: Invalid field in cdb
sr 2:0:0:0: [sr0] tag#190 CDB: Read(10) 28 00 00 08 46 9a 00 00 c0 00
critical target error, dev sr0, sector 2169448 op 0x0:(READ) ...
```

MakeMKV recovers and the rip still completes, but the log gets ~1.5 of these a second for the length of every rip. That fills
`dmesg` in a few minutes and pushes out any real medium error before anyone
looks.

**These are not copy protection.** A drive refusing an unauthenticated read of
a CSS or AACS sector reports ASC `0x6F`, *Read of scrambled sector without
authentication*. The tell for this fault is the transfer length in the CDB
(`c0` = 192 blocks = 384 KiB): every failure has the same length and it is over
the host limit.

`max_sectors=256` on `virtio-scsi-pci` gives the guest the host's real limit.
It caps the controller rather than one device, so it covers `SG_IO` as well as
block reads. It does not depend on a udev rule firing at the right moment. With
it in effect, the guest's `max_hw_sectors_kb` for `sr0` reads `128`; without it,
`32766`.

## Kubernetes: Device Plugin and Security Policy

ARM runs in its own `automatic-ripping-machine` namespace, labelled
`pod-security.kubernetes.io/enforce: privileged`
(`kubernetes/apps/automatic-ripping-machine/namespace.yaml`). The container is
not itself `privileged: true` — it adds only the `SYS_ADMIN` capability:

- **SG_IO** (raw SCSI ioctls used by MakeMKV) works without privileges. It is just an `ioctl` call on a device file. Default seccomp does not block it.
- **udev events** reach the container's udevd without privileges (explained below).

The **squat.ai generic device plugin** (`kubernetes/infrastructure/devices.yaml`) runs as a privileged DaemonSet in the `infrastructure` namespace. It exposes `/dev/sr0` and `/dev/sg0` on the node as an allocatable Kubernetes resource called `devic.es/cdrom`.

### How the plugin picks the *real* drive

The plugin does not match on `/dev/sr0`. Every Talos VM exposes a QEMU virtual
DVD-ROM at `/dev/sr0` for its install ISO, so matching the bare path made the
control plane advertise a phantom `devic.es/cdrom` backed by the install ISO.
Because ARM has no `nodeSelector` and schedules purely on that resource, it could
be placed on the control plane and bound to the ISO instead of the burner.

The device group therefore matches on hardware identity instead:

```yaml
groups:
  - paths:
      - path: /dev/disk/by-id/ata-PIONEER_BD-RW_*
        mountPath: /dev/sr0
      - path: /dev/sg0
```

`/dev/disk/by-id/` distinguishes them — `ata-PIONEER_BD-RW_BDR-212U` on worker-1
versus `ata-QEMU_DVD-ROM_QM00003` on the control plane. A group is only
advertised when **all** of its non-optional paths match, so a node without the
Pioneer advertises nothing.

Two consequences worth knowing:

- **Nothing is pinned to a node.** Move the drive to another VM in
  `terraform/main.tf` and the resource follows it, with ARM following the
  resource. There is no hostname to keep in sync on either side.
- **Other hardware extends the same way.** A GPU would be another `--device`
  group; the DaemonSet stays on every node and self-selects by what is present.

Note the plugin passes the *unresolved* symlink as the device's `HostPath`
(`deviceplugin/path.go` does not call `EvalSymlinks`) and relies on the
container runtime to resolve it. containerd 2.1.6 does so correctly — verified
2026-08-14 by restarting ARM so it took a fresh allocation, then confirming
both `/dev/sr0` and `/dev/sg0` inside the container report
`PIONEER / BD-RW   BDR-212U`.

If a future runtime change ever breaks this, the fallback is to list
`/dev/sr0` and `/dev/sg0` as the mounted paths and keep the by-id entry purely
as a match predicate:

```yaml
groups:
  - paths:
      - path: /dev/sr0
      - path: /dev/sg0
      - path: /dev/disk/by-id/ata-PIONEER_BD-RW_*
        mountPath: /dev/cdrom-discriminator
```

### Verifying the passthrough

Checking a *running* ARM pod proves nothing — a device already granted to a
live container is never withdrawn, so it will look correct even if the config
is broken. The device ID is a sha1 over the matched host paths, so a fresh
allocation is what exercises the current config. Restart ARM while it is idle,
then compare against this known-good baseline:

```
sr0 vendor/model/rev : PIONEER / BD-RW   BDR-212U / 1.01   maj=11 min=0  (block)
sg0 vendor/model     : PIONEER / BD-RW   BDR-212U          maj=21 min=0  (char)
```

```bash
kubectl exec -n automatic-ripping-machine deploy/automatic-ripping-machine -- sh -c '
for f in vendor model rev; do printf "sr0 %s: " "$f"; cat /sys/block/sr0/device/$f; done
for f in vendor model; do printf "sg0 %s: " "$f"; cat /sys/class/scsi_generic/sg0/device/$f; done
stat -c "%n %F maj=%t min=%T" /dev/sr0 /dev/sg0'
```

**`sr0` and `sg0` must report the same vendor/model.** That is what proves both
resolve to the same underlying SCSI device; `/dev/sg0` carries the SCSI commands
that control the drive, so a mismatch matters more than anything else here.
`udevadm info` on either device also shows a shared `DEVPATH` parent
(`.../target2:0:0/2:0:0:0/...`), which confirms the same thing independently.

`makemkvcon` works as a check from `kubectl exec`, which sets `HOME=/root`, once
the registration key is in `/root/.MakeMKV/settings.conf`. ARM writes it at the
start of every job. In a pod where no job has run yet, register it first with
`/opt/arm/scripts/update_key.sh <key>`. Without a key, an expired MakeMKV
refuses with "This application version is too old". With no disc loaded,
`makemkvcon -r --cache=1 info disc:9999` sends the drive no LibreDrive
commands. ARM's own drive listing shows
an empty maker/model for the same underlying reason it always has: udev's
`ata_id`/`cdrom_id` helpers run in the host's udevd, not in the container, so
`ID_MODEL`/`ID_VENDOR` are never populated there. Read identity from sysfs
instead, as above.

### The resource domain, and how it can break

The resource is `devic.es/cdrom`. The `devic.es` half is the plugin's
**domain**, which `devices.yaml` leaves implicit so that it takes upstream's
default.

This is worth knowing because **upstream has changed that default before**. The
image pinned here until 2026-08-14 defaulted to `squat.ai`, and bumping it
renamed the resource to `devic.es/cdrom` — dropping `squat.ai/cdrom`, which ARM
still requested at the time, to `allocatable: 0` on every node. ARM kept
running, because a device already granted to a live container is not withdrawn,
so nothing appeared broken. The pod would have gone `Pending` forever on its
next restart. The repo moved to `devic.es` on 2026-08-14 to match the default.

If ARM is ever `Pending` on `Insufficient devic.es/cdrom` after an image bump,
this is the first thing to check:

```bash
kubectl get nodes -o custom-columns='NODE:.metadata.name,CDROM:.status.allocatable.devic\.es/cdrom'

# the plugin image is distroless, so scrape it from a pod that has a shell:
kubectl exec -n infrastructure alertmanager-kube-prometheus-kube-prome-alertmanager-0 \
  -c alertmanager -- wget -qO- http://<plugin-pod-ip>:8080/metrics \
  | grep generic_device_plugin_devices
```

The metric reports the resource name the plugin is actually advertising. If it
no longer matches ARM's `resources.limits`, either realign the two or pin the
old name with `--domain <domain>` in `devices.yaml`.

The ARM deployment claims this resource:

```yaml
resources:
  limits:
    devic.es/cdrom: 1
```

This is an exclusive resource — only one pod can hold it at a time. This is why the deployment uses `strategy: Recreate` rather than `RollingUpdate`: a rolling update would try to start the new pod before terminating the old one, but the new pod can never schedule because the old pod still holds the device.

### Keeping the plugin healthy

The plugin is the drive's only way into the cluster. While it restarts,
`devic.es/cdrom` is withdrawn, and an ARM pod admitted in that window is rejected
with `UnexpectedAdmissionError` and stays `Failed` — a ReplicaSet never deletes
one. A replacement starts normally once the plugin re-registers, so delete the
`Failed` pod by hand; it holds nothing. A node reboot can lose the same race,
since the kubelet re-admits pods before the plugin has registered.

**Its parallelism is pinned to two, and must stay pinned.** With no CPU limit
the Go runtime sizes itself to the node's core count. On the 4- and 8-core
workers that left the plugin degrading every few hours: its median
garbage-collection pause rose from ~55µs to 11–18ms, `/metrics` slowed from ~2ms
to seconds, and the liveness probe reaped it. The 2-core control plane never
degraded. Pinned to two, all three nodes run identical ~55µs pauses regardless
of core count. Why a wider runtime degrades intermittently, and what makes a
scrape wait on it, are not established; the pause and the scrape latency move
together, which is enough to use one as the readout for the other.

**It has no CPU limit, and must not get one.** A limit would also cap the
runtime's width, but under one, CFS throttling freezes the process for most of
every period and a slow `/metrics` never recovers — the plugin goes from
degraded to dead.

`DevicePluginDegraded` watches the median pause. To read it by hand, along with
the parallelism the runtime is actually using:

```bash
kubectl exec -n infrastructure alertmanager-kube-prometheus-kube-prome-alertmanager-0 \
  -c alertmanager -- wget -qO- http://<plugin-pod-ip>:8080/metrics \
  | grep -E '^go_gc_duration_seconds\{quantile="0.5"\}|^go_sched_gomaxprocs_threads'
```

Read the pause, not the latency of a fetch. A one-off fetch usually lands
between the slow moments and reads healthy on a degraded plugin, and through
`kubectl port-forward` it measures the apiserver tunnel — about 240ms — rather
than the plugin.

## Automatic Disc Detection

### How udev events reach the container

The Linux kernel broadcasts `NETLINK_KOBJECT_UEVENT` messages to **all network namespaces**, not just the initial one. The ARM container's `systemd-udevd` (from the phusion/baseimage base image) binds a `NETLINK_KOBJECT_UEVENT` socket on startup and receives kernel device events, including disc insertion and ejection on `/dev/sr0`, without any special privileges.

### The host polling problem

With scsi-generic passthrough, both the Proxmox host kernel and the Talos guest kernel manage the drive. When a disc is inserted, the drive queues a media change notification in response to `GET EVENT STATUS NOTIFICATION` SCSI commands. Whichever kernel polls first reads and clears that notification.

By default, the **host kernel polls first** (its `sr_mod` has `events_poll_msecs` set to a positive value). The guest kernel, which by default has `events_poll_msecs=-1` (relying on async notification which doesn't propagate through virtio-scsi), never sees the insertion event.

Ejection events propagate reliably despite this, because tray-open causes pending SCSI commands to fail — a condition both kernels detect independently without consuming a shared event.

### The fix: split polling ownership

Two udev rules redirect event ownership to the guest:

**1. Proxmox host** (`ansible/proxmoxer.yaml`, rule file `99-optical-drive-sg.rules`):

```
SUBSYSTEM=="block", ATTRS{model}=="BD-RW   BDR-212U", ATTR{events_poll_msecs}="0"
```

This disables host kernel polling for the Pioneer, leaving insertion events in the drive's queue.

**2. Talos worker-1** (`terraform/modules/proxmox_talos_vm/files/cdrom-passthrough-patch.yaml`, applied via `machine.udev.rules`):

```
ACTION=="add", KERNEL=="sr[0-9]*", SUBSYSTEM=="block", ATTR{events_poll_msecs}="2000"
```

This enables guest kernel polling every 2 seconds when `sr0` is created at boot. The guest kernel now becomes the sole consumer of disc insertion events, which propagate to the ARM container's udevd within 2 seconds of disc insertion.

### The udev rule and wrapper script

A udev rule mounted into the ARM container (`kubernetes/apps/automatic-ripping-machine/init-scripts.yaml`) fires on block device change events:

```
ACTION=="change", SUBSYSTEM=="block", KERNEL=="sr[0-9]*", RUN+="/usr/local/bin/arm-disc-wrapper.sh %k"
```

The rule calls `arm-disc-wrapper.sh` (also in the ConfigMap, mounted at `/usr/local/bin/`). This script checks `CDROM_DRIVE_STATUS` via ioctl before invoking ARM:

```
CDS_NO_INFO  = 0  (don't invoke)
CDS_NO_DISC  = 1  (don't invoke)
CDS_TRAY_OPEN = 2 (don't invoke — eject event)
CDS_DRIVE_NOT_READY = 3 (don't invoke)
CDS_DISC_OK  = 4  (invoke ARM)
```

Only `CDS_DISC_OK` proceeds to call `/opt/arm/scripts/docker/docker_arm_wrapper.sh`, which calls `python3 /opt/arm/arm/ripper/main.py -d sr0` directly inside the container. All other states are logged to `arm.log` and exit cleanly.

Before handing over, the wrapper exports `HOME=/root`. udev starts it with no
`HOME`, and MakeMKV keeps everything it needs under `$HOME/.MakeMKV`: the
registration key, the LibreDrive drive definitions (`SDF.bin`) and the UHD
keys. The last two it downloads from its own server. Without `HOME`, every job
runs MakeMKV unregistered on its built-in definitions, and an expired MakeMKV
refuses every disc. `/root/.MakeMKV` is container-local, so the first job after
each pod restart downloads them again, and needs internet access to do it.

This prevents the eject event (which the container does receive) from triggering a spurious rip attempt.

## Rip and Transcode Pipeline

Once ARM detects a disc and identifies it (via the manual identification gate or automatic metadata lookup), it runs through a two-stage pipeline: rip, then transcode.

The disc stays in the drive, locked, until the whole job is complete, transcode
included, and is ejected once at the end. An open tray is therefore the signal
that a disc is finished. That, HandBrake encoding each title once and skipping
the titles others already play, a movie's main feature keeping its `title_N`
name, and the whole DVD as its raw copy come from edits to ARM's own source. `arm-source-patches.py` (`init-scripts.yaml`) applies them at
container start. Each edit is skipped, and logged as `source-patches: cannot
apply` in `arm.log`, if an ARM update moves the text it replaces. After an image
bump, check that log for nine `applied` lines.

### Data flow

```
Disc inserted → udev event → ARM ripper
  Blu-ray: MakeMKV backup → /root/video/raw/<Title>/BDMV/              (the whole disc, decrypted)
  DVD:     MakeMKV backup → /root/video/raw/<Title>/<Title>.iso        (the whole disc, decrypted)
           MakeMKV mkv    → /root/video/raw/<Title>/titles/*_tNN.mkv  (from the ISO, one file per title of 7 min or more)
  either:  arm-job.json   → /root/video/raw/<Title>/arm-job.json       (the job that made the folder)
  then:    HandBrake      → /root/video/completed/<movies|tv|unidentified>/<Title (Year)>/
```

Both directories live on the SMB share (`//192.168.0.105/media/video/import/automatic-ripping-machine/`).

### Title identification gate

`MANUAL_WAIT: true` with `MANUAL_WAIT_TIME: 600` holds each job for ten minutes after identification, so a wrong title can be corrected in the ARM web UI (`arm.immortalkeep.com`). The job then proceeds with whatever title it has. A title set later, at any point before post-processing, still renames the output, so the real correction window runs to the end of the transcode. Titles come from OMDb (`METADATA_PROVIDER: "omdb"`); a DVD also has a CRC64-based disc identification.

### Stage 1: Rip (MakeMKV)

Every video disc is backed up whole and decrypted, so its menus, DVD games, bonus material and structure can always be gone back to and processed again. For a Blu-ray MakeMKV writes the full `BDMV/` structure — every clip and playlist and the BD-J menus — with the AACS data moved aside into `MAKEMKV/`. For a DVD it writes an ISO image, named after its folder. That backup is the only read of the drive. MakeMKV then extracts each title of `MINLENGTH` (seven minutes) or more from the image into `titles/` as a lossless MKV, and HandBrake works from those. Upstream ARM backs up the whole disc only for a Blu-ray, whatever `RIPMETHOD` says, so the DVD backup is one of the source edits. DVD raw folders from before it hold only the long titles' MKVs, at the top level.

The raw folder is named from the job's title when the rip starts, with the job's `stage` timestamp appended if that name is taken. A title corrected afterwards renames the completed folder and not the raw one, and ARM's database never records the raw path. So each raw folder carries **`arm-job.json`**, written as the folder is created, which names the job that made it: `job_id` (ARM's job table and web UI), `label`, `disctype`, `crc_id` (a DVD's; null for a Blu-ray), `stage` and `start_time` (UTC), under `"schema": 1`. Join on `job_id`, never on the folder's name. reelbarrow reads the stamp, so it is a contract: a field added leaves `schema` at 1, and removing a field or changing what one means raises it. A reader ignores fields it doesn't know and takes a missing one as not recorded, which is how a stamp from before `duplicates` reads. Throughout, `handbrake_title` is HandBrake's number for a title, the *N* of its `title_N.mkv`, and `disc_title` or `playlist` the disc's own. Every stamp on the share has this shape; a raw folder restored from a ZFS snapshot older than 2026-10-26 can carry an earlier one, with `titles` for `extracted` and `title` for `handbrake_title` in `duplicates`, which is to be rewritten before anything reads it.

For a DVD the stamp's `extracted` lists the titles MakeMKV extracted from the ISO, so those of `MINLENGTH` or more and not every title on the disc, and maps each through its three numberings, written once extraction ends: `disc_title`, the DVD's own title number, which finds the title in the ISO (`HandBrakeCLI -i <iso> -t <disc_title>`); `mkv`, its file in `titles/`; `length`, MakeMKV's own duration, which counts each cell it keeps in whole seconds of the IFO's clock and so runs short of what plays, by up to a second a cell plus 0.1% on NTSC (22.8 s on job 76's 30-cell play-all), so a reader that needs a title's length reads the ISO; and `handbrake_title`, the number HandBrake gives it and so its transcode's `title_N.mkv`, null if MakeMKV listed the title but didn't save it. The `_tNN` in an MKV's name is neither of the others: MakeMKV counts only the titles it kept, and HandBrake numbers the files in byte order of their names. `extracted` is null for a Blu-ray, whose transcodes are made from `BDMV/` directly, and until a DVD's extraction has finished. A Blu-ray's map is `playlists` instead, written with `duplicates` once HandBrake's scan has run: each `handbrake_title` HandBrake was given, against the `playlist` it plays. Lengths can't recover it, since a disc's featurette variants can lie a second apart and share all but one clip. A Blu-ray stamp without it predates it, or comes from a job whose structure couldn't be read. Folders older than the stamp have none, and for those ARM's job log is the only record, for its 90-day life (`Processing files to:` from `makemkv.makemkv`).

In `completed/` every transcode keeps its `title_N.mkv`, flat for a series; for a movie the title ARM takes for the main feature sits in `<Title (Year)>/`, the rest under `extras/`. That placement is ARM's guess, from HandBrake's main-feature flag or the largest file, and is often wrong on a bonus disc; what the film is gets decided downstream. Upstream ARM renames the main feature `<Title (Year)>.mkv`, which loses its title number; folders from jobs up to 83 have that shape.

These raw copies are the **archival copy**. `DELRAWFILES: false` ensures they are never deleted after transcoding. When a backup ends, it is checked against the disc's own record of its size, and the stamp's `complete` says the result:

- **`true`**: every Blu-ray clip in `BDMV/CLIPINF/` has its `.m2ts` in `BDMV/STREAM/` at no less than 192 bytes × the source-packet count in its `.clpi` (a big-endian `uint32` at byte 56), or a DVD's ISO is no smaller than the volume size its own ISO 9660 descriptor declares; and MakeMKV reported no file failing the disc's hash check.
- **`false`**: one of those failed. `problems` lists each failure, and the job fails before anything is transcoded, so the notification reports an error and the disc wants re-ripping.
- **`null`**: the check never ran, because the job died or was abandoned during the backup. The backup is partial.

The same check applies by hand to a backup older than the stamp.

### Stage 2: Transcode (HandBrake)

HandBrake transcodes from the raw copy every title longer than `MINLENGTH` (420 seconds / 7 minutes) into standalone MKV files in `COMPLETED_PATH` (`/root/video/completed/<type>/<Title (Year)>/`).

Key encoding settings (defined in the `vulcanus-handbrake-preset` custom preset):

| Setting | Value | Notes |
|---|---|---|
| Video codec | SVT-AV1 10-bit | Royalty-free, ~30-50% smaller than H.264 at equivalent quality |
| Quality | CRF 30 (constant quality) | Perceptually equivalent to x264 CRF 20 |
| Encoder preset | 4 (SVT-AV1 scale: 0=slowest, 13=fastest) | Quality-focused; expect several hours per film on 6 CPU cores |
| Audio | Opus stereo 192 kbps + Opus 5.1 320 kbps | Lossless TrueHD and DTS-HD MA are re-encoded too; the raw copy keeps the originals |
| Subtitles | All tracks (English + any) | Soft subs, not burned in |
| Container | MKV | With chapter markers and metadata passthrough |
| Deinterlace | Decomb (default) | Important for interlaced DVD content |

`MAX_CONCURRENT_TRANSCODES: 1` limits to one transcode at a time — AV1 at preset 4 saturates whatever it is given, and this runs on `piraeus-worker-1`, the smaller of the two workers, alongside everything else scheduled there.

`MAINFEATURE: false` means all tracks above the minimum length are transcoded, not just the longest. This is necessary for TV show discs where multiple episodes need to be extracted.

### Titles that repeat other titles

A disc often offers the same material twice: a TV disc's "play all" title is every episode end to end, a bonus disc plays its featurettes in turn or as one documentary, and a film can be offered again with featurettes cut into it. Encoding both copies costs hours, and on a movie disc the play-all is often the longest title, so the one ARM takes for the film. So before encoding, the titles are compared by what they play from the disc — a Blu-ray playlist's clips and times, a DVD title's cells in the ISO — and a title is not transcoded when the titles kept play at least 99% of it. Length alone decides nothing.

A **whole** is a title played by two or more others, its **parts**, each inside it and none overlapping another. Which of the two is kept:

- **A series keeps the parts**: each episode is an item of its own in the library, and the play-all is skipped.
- **A movie disc keeps the whole** when its parts follow one another with a chapter at each join, and skips the parts. A documentary in parts stays one uninterrupted file, and its encode has a keyframe at each chapter, so a part can be cut from it later without re-encoding.
- **Parts that interleave are kept apart**, and the whole is skipped. That is the shape of a film with featurettes cut in, such as *X-Men: First Class*'s "X Marks the Spot" mode, which is not the film. On a movie disc the notification names the title skipped, because an extended cut whose added scenes the disc also offers as a deleted-scenes title has the same shape and would be transcoded by hand from the raw copy.
- **Anything else is transcoded**: a whole holding pieces too short to be titles of their own (a featurette play-all with an unlisted interstitial), a film assembled from clips the disc also offers singly, alternate cuts that overlap.

If a skipped title was the main feature, the longest title kept becomes it, which decides only which transcode sits outside `extras/`. A disc whose structure can't be read has every title transcoded, with a warning in the job log. Nothing is skipped from the raw copy, only from the transcodes.

The stamp's `duplicates` records the outcome, once HandBrake's scan has run: `skipped` lists each title not transcoded (`handbrake_title`; `playlist` for a Blu-ray or `disc_title` for a DVD; and `reason`), and `wholes` each whole found, with `kept` (`parts`, `whole` or `both`), `interleaved`, and its `parts`, each with `start`, the second of the whole at which it begins, in the playback time its transcode's chapters keep, a DVD's as a Blu-ray's. That is what cuts a kept whole into its parts, or rejoins a series disc's episodes, when a disc turns out misidentified — a series filed as a movie keeps one long file and the joins to split it at. Both lists are empty when nothing repeats. A stamp without `duplicates` comes from a job before it, one that ended before transcoding began, or one whose structure couldn't be read.

In ARM's database, and so in a manifest's track rows, a skipped title keeps `ripped` true and an empty `filename`. ARM sets `ripped` when it registers a title longer than `MINLENGTH`, before any transcode, so it means eligible, not transcoded. A title has a transcode when its `filename` is set.

## ATA Link Stability

The drive intermittently stops answering a command and drops off its SATA link,
usually in the first seconds of a `makemkvcon` run. The command is LibreDrive's
handshake read (`READ BUFFER` buffer `0x77`). It is served by the extension
MakeMKV uploads into the drive's RAM, and once hung the drive ignores link
resets until its power is removed. The cause is not established, and the
investigation is in `todos/disc-ripping.md`. Symptoms:

- The host logs `ata4.00: exception Emask 0x0 ... frozen` about 30 s after the
  hang, then either `EH complete` (recovered) or `hardreset failed` /
  `reset failed, giving up` / `disable device`
- A disabled drive can keep `/dev/sr0` and `/dev/optical-drive-sg` on the host,
  and its `/dev/sr0` and `devic.es/cdrom: 1` in the guest. Only the host's
  `ata4` lines show whether it is alive
- After a link-level failure (`SError ... 10B8B DevExch`, `SATA link down`),
  the device is detached and `/dev/sr0`, `/dev/sg3` and the symlink disappear

### Prevention

Nothing on the host probes the drive while QEMU holds it. `ansible/proxmoxer.yaml`
deploys a `/etc/smartd.conf` that names each data disk explicitly rather than
using `DEVICESCAN`, so `smartd` never touches `/dev/sr0`.

Additional precautions:
- Only one VM or process should ever have the drive open at a time
- Do not create test VMs that also pass through `sg3` while the ARM VM is running (see: the `cdrom-test` VM incident)
- The devic.es/cdrom exclusive device resource enforces this at the Kubernetes level, but it does not prevent other QEMU VMs on the Proxmox host from also opening the sg device
- **Before running any manual `makemkvcon` command inside the ARM pod, verify no rip is in progress:**
  ```bash
  kubectl exec -n automatic-ripping-machine <arm-pod> -- pgrep -af makemkv
  ```
  If anything is returned, do not proceed until it finishes.

Note: SATA Aggressive Link Power Management (ALPM) was investigated as a potential cause but is already set to `max_performance` on all ports — it is not a factor.

### Guest-side scanners

Nothing inside the Talos guest may probe `/dev/sr0` on a schedule either. The
drive answers a read of block 0 only where the disc has a 2048-byte data mode;
an audio CD has none, so every attempt fails with `Illegal mode for this track`
after ~15ms. `blkid` tries enough offsets that a single pass costs 5.9 seconds
against ~2ms on a virtual disk, and anything repeating that holds the drive at
95% utilisation and ~0.85 cores of iowait indefinitely — on a node with four.

This is why OpenEBS Node Disk Manager is disabled in
`kubernetes/infrastructure/openebs.yaml`. Its `path-filter` names `/dev/sr0`
and always did; the filter is simply applied after every probe has already read
the device, and a filtered device is never cached, so each pass re-probes from
scratch. A filter that runs after the read is not a filter, and reading the
config is not enough to tell.

Ejecting is not a way out. The tray opens, the scanner's next `open()`
auto-closes it, and the grinding resumes within seconds — so a scanner has to
be stopped before the drive can be cleared.

### Recovery

A drive the host has disabled comes back only with a **full power cycle** of the
host. A reboot doesn't remove the drive's power. `ansible/proxmoxer.yaml` also
deploys `/usr/local/bin/optical-drive-recovery.sh` (stop VM 911, rescan the
SCSI bus), but a delete-and-rescan has never brought a hung drive back.

Before powering off, check every guest has `onboot=1`, no vzdump or `zfs send` is
running (the offsite replication runs hourly), and `zpool status -x` is clean. No
Kubernetes drain is needed. Then `systemctl poweroff`, leave it fully off for at
least 30 seconds, and power on through JetKVM. Afterwards check `ata4: SATA link
up`, `/dev/optical-drive-sg`, and that worker-1 advertises `devic.es/cdrom: 1`.
An ARM pod left in `Error` by the reboot is harmless once its replacement is
`Running`.

### Monitoring

`ansible/proxmoxer.yaml` deploys an `optical-drive-ata-monitor` systemd service that watches the kernel log for ata4 link events. When a disconnect is detected, it captures a diagnostic snapshot to `/var/log/optical-drive-monitor/` including the trigger line, recent ata4 messages, device node state, and which processes held the drive open.

## Replication Guide

To replicate this setup:

1. **Proxmox host prep**: Run `ansible/proxmoxer.yaml`. This creates the stable `/dev/optical-drive-sg` symlink and disables host-side polling for the optical drive.

2. **VM provisioning**: Set `host_cdrom_passthrough = true` for the worker VM in `terraform/main.tf`. Run `tofu apply`. This attaches the drive via scsi-generic and applies the Talos machine config udev rule for `events_poll_msecs=2000`.

3. **Device plugin**: `kubernetes/infrastructure/devices.yaml` (squat.ai generic device plugin) must be deployed and the plugin pod must be running on the worker node.

4. **ARM deployment**: `kubernetes/apps/automatic-ripping-machine/` deploys the ARM pod. The `devic.es/cdrom: 1` resource limit, `strategy: Recreate`, and the udev rule + wrapper script ConfigMap mounts are all required.

5. **Verify**: With a disc in the drive, run:
   ```bash
   kubectl exec -n automatic-ripping-machine <arm-pod> -- makemkvcon --robot info disc:0
   ```
   You should see the drive identified as `BD-RE PIONEER BD-RW BDR-212U` with `Using LibreDrive mode` and a valid disc title.

## Debugging Reference

### Best external references

- **MakeMKV forum** (`forum.makemkv.com`) — authoritative source for LibreDrive behaviour, drive-specific quirks, and `makemkvcon` settings. Search by drive model or error message. The LibreDrive overview thread is at `t=18856`.
- **ARM GitHub** (`github.com/automatic-ripping-machine/automatic-ripping-machine`) — source for ARM's disc detection logic (`arm/models/job.py`), identify flow (`arm/ripper/identify.py`), and MakeMKV wrapper (`arm/ripper/makemkv.py`).

### Useful commands for live diagnosis

```bash
# Find the running ARM pod
kubectl get pods -n automatic-ripping-machine -l app=automatic-ripping-machine

# Pod logs (startup, udev events, ARM process output)
kubectl logs -n automatic-ripping-machine <pod>

# Check for active rip processes — do this before running any manual makemkvcon command
kubectl exec -n automatic-ripping-machine <pod> -- pgrep -af makemkv

# Query the ARM job database directly
kubectl exec -n automatic-ripping-machine <pod> -- python3 -c "
import sqlite3; conn = sqlite3.connect('/root/db/arm.db'); conn.row_factory = sqlite3.Row
c = conn.cursor(); c.execute('SELECT job_id,title,disctype,status,errors,logfile FROM job ORDER BY job_id DESC LIMIT 10')
[print(dict(r)) for r in c.fetchall()]"

# Read a specific job log (logs live in /root/logs/ inside the pod)
kubectl exec -n automatic-ripping-machine <pod> -- tail -100 /root/logs/<logfile>

# Check disc/drive state
kubectl exec -n automatic-ripping-machine <pod> -- makemkvcon --robot info disc:0

# Watch rip progress
kubectl exec -n automatic-ripping-machine <pod> -- ls -lh /root/video/raw/<job-dir>/
```

## Troubleshooting

### Drive has dropped off the ATA bus

**Symptoms**: ARM stops detecting discs; `eject` commands fail with `No such file or directory`; `/dev/sr0` and `/dev/sg3` are missing on the Proxmox host.

**Confirm it**: SSH into the Proxmox host and check:

```bash
# Drive should appear here if connected at the block level
ls /dev/sr0 /dev/sg3 /dev/optical-drive-sg

# Check whether ata4 failed to initialise
dmesg | grep ata4
# Bad: "ata4: hardreset failed" / "ata4: reset failed, giving up"
# Good: "ata4: SATA link up"

# Verify the BIOS still sees it (run in BIOS under Settings → System Status → SATA Port 10)
# If the BIOS sees "PIONEER BD-RW BDR-212U", the physical connection is intact.
# If the BIOS shows nothing, check SATA power and data cables on the drive.
```

**Recovery**: A full power cycle is required — a reboot alone does not power-cycle SATA devices on this system.

```bash
# On the Proxmox host:
systemctl poweroff
```

Wait at least 30 seconds after the machine goes dark, then power it back on, after the checks in **Recovery** above. The drive will re-enumerate at boot and `/dev/sr0`, `/dev/sg3`, and `/dev/optical-drive-sg` will reappear.
