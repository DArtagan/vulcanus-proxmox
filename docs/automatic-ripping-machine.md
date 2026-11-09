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

**Its memory limit has to hold its own code.** The binary maps ~14Mi, which is
charged to the plugin's cgroup as page cache, and the Go runtime adds ~9Mi of
anonymous memory. The cgroup's `memory.current` settles at 20–23Mi. Under
upstream's 20Mi limit the kernel evicts the plugin's code and reads it back from
disk 60–150 times a second. The process spends 99% of its CPU in the kernel, its
median garbage-collection pause rises from ~90µs to 1–18ms, `/metrics` slows from
~2ms to seconds, and the liveness probe reaps it every few hours, on every node.
The limit here is 64Mi, under which the cgroup has never reached its limit and
the plugin takes about one major fault a day.

Judge headroom by `memory.current` (`container_memory_usage_bytes`), not the
working set. The working set subtracts inactive page cache, so it read 8–14Mi on
a cgroup pinned at its limit. cAdvisor's `container_memory_failcnt` and
`container_oom_events_total` read 0 on this cgroup v2 setup even across OOM
kills, so read limit hits on the node:

```bash
talosctl -n <node-ip> processes | grep generic-device   # the plugin's pid
talosctl -n <node-ip> read /proc/<pid>/cgroup           # its cgroup path
talosctl -n <node-ip> read /sys/fs/cgroup<path>/memory.events
```

A climbing `max` count means the cgroup is pressing on its limit. A restart
whose `lastState.terminated` reads `Completed`, exit 0, is a liveness reap
rather than a clean exit: its log ends `caught interrupt`, which is the kubelet's
SIGTERM.

**Its parallelism is pinned to two.** With no CPU limit the Go runtime sizes
itself to the node's core count, and each extra P costs resident memory:
unpinned on the 8-core worker the plugin held 10–14.6Mi RSS, and pinned it holds
7.7–9.6Mi. Under the 20Mi limit, that difference decided whether the plugin's
code stayed resident. Whether the 64Mi limit alone is enough has not been tested, so the pin stays
until it is.

**It has no CPU limit, and must not get one.** A limit would also cap the
runtime's width, but under one, CFS throttling freezes the process for most of
every period and a slow `/metrics` never recovers — the plugin goes from
degraded to dead.

`DevicePluginDegraded` watches the median pause, and `DevicePluginMemoryHigh`
watches for RSS growing past 16Mi or `memory.current` passing 75% of the limit.
A transcode on the drive's node leaves the plugin's median pause near its idle
~90µs, because the job runs off CPU 0 (see "Stage 2: Transcode"). A pause that
rises only while a transcode runs points at the job's CPU affinity before the
plugin. To read the pause by hand, along with the parallelism the runtime is
actually using:

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

A udev rule mounted into the ARM container at `/etc/udev/rules.d/`, where it takes the place of upstream's rule of the same name (`kubernetes/apps/automatic-ripping-machine/scripts/51-docker-arm.rules`), fires on block device change events:

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
name, the whole DVD as its raw copy, and job titles reduced to Latin-1, which
the fileserver's Samba can write ([`todos/smb-charset-utf8.md`](../todos/smb-charset-utf8.md)),
come from edits to ARM's own source. `scripts/arm-source-patches.py` applies them at
container start. Each edit is skipped, and logged as `source-patches: cannot
apply` in `arm.log`, if an ARM update moves the text it replaces. After an image
bump, check that log for ten `applied` lines.

ARM's scripts are mounted with `subPath`, which Kubernetes never updates in
place, from a ConfigMap generated out of `scripts/` with a hash of their
contents in its name. A merged change to a script therefore rolls ARM by itself,
once, killing any job then running with `Received SIGTERM`: merge one only while
ARM is idle. `kubectl rollout restart` rolls twice: Flux's next scheduled
reconcile, up to ten minutes later, strips the annotation and rolls the pod
again. Job 101 died that way seven minutes into its backup. After one, insert a
disc only once a scheduled reconcile has passed, `flux get kustomizations apps` showing a run after the
restart and the pod's start time later than that run or unchanged by it.

### Data flow

```
Disc inserted → udev event → ARM ripper
  Blu-ray: MakeMKV backup → /root/video/raw/<Title>/BDMV/              (the whole disc, decrypted)
  DVD:     MakeMKV backup → /root/video/raw/<Title>/<Title>.iso        (the whole disc, decrypted)
           MakeMKV mkv    → /root/video/raw/<Title>/titles/*_tNN.mkv  (from the ISO, one file per title of 7 min or more)
  either:  raw copy       → ARM's database, reelbarrow_raw_copy      (the folder, its check, its titles' transcodes)
  then:    HandBrake      → /root/video/completed/<movies|tv|unidentified>/<Title (Year)>/
  always:  exporter       → /root/video/manifests/manifest-arm-<job_id>-<stage>.json  (the sidecar, every 30 s)
```

Both directories live on the SMB share (`//192.168.0.105/media/video/import/automatic-ripping-machine/`).

### Title identification gate

`MANUAL_WAIT: true` with `MANUAL_WAIT_TIME: 600` holds each job for ten minutes after identification, so a wrong title can be corrected in the ARM web UI (`arm.immortalkeep.com`). The job then proceeds with whatever title it has. A title set later, at any point before post-processing, still renames the output, so the real correction window runs to the end of the transcode. Titles come from OMDb (`METADATA_PROVIDER: "omdb"`); a DVD also has a CRC64-based disc identification.

### Stage 1: Rip (MakeMKV)

Every video disc is backed up whole and decrypted, so its menus, DVD games, bonus material and structure can always be gone back to and processed again. For a Blu-ray MakeMKV writes the full `BDMV/` structure — every clip and playlist and the BD-J menus — with the AACS data moved aside into `MAKEMKV/`. For a DVD it writes an ISO image, named after its folder. That backup is the only read of the drive. MakeMKV then extracts each title of `MINLENGTH` (seven minutes) or more from the image into `titles/` as a lossless MKV, and HandBrake works from those. Upstream ARM backs up the whole disc only for a Blu-ray, whatever `RIPMETHOD` says, so the DVD backup is one of the source edits. DVD raw folders from before it hold only the long titles' MKVs, at the top level.

The raw folder is named from the job's title when the rip starts, with the job's `stage` timestamp appended if that name is taken. A title corrected afterwards renames the completed folder and not the raw one, and ARM's own columns never record the raw path. So the patches record each job's **raw copy** in ARM's database, in a table of their own, `reelbarrow_raw_copy`: one JSON document a job, keyed by `job_id`, created as the folder is and updated as the job goes. The table declares no foreign key, because ARM's migrations rebuild `job` and leave a table they don't name alone, and it is written through ARM's own session, since a second connection would wait on ARM's uncommitted writes in the same thread. reelbarrow's exporter reads it and carries it out in the job's manifest, and the document is already in the manifest's shape: its `raw` and `titles`, as reelbarrow's design (*The raw copy's record*) and its schema define them, in ARM's container's paths. The exporter translates the paths, drops each title's `handbrake_title`, the record's own key, and checks the rest against reelbarrow's model, so a field the patches add leaves `schema` at 1, and removing one or changing what it means raises it. A missing field reads as not recorded. The document holds:

- **`folder`**, the raw folder, and **`form`**: `bluray-backup`, `dvd-image` with its **`image`**, or `mkv-titles` for a DVD ripped without a backup.
- **`check`** and **`problems`**, the backup's checks (below).
- **`titles`**, one a title (below), and **`titles_compared`**, `true` once HandBrake's scan has compared them (*Titles that repeat other titles*). Each title's **`transcode`** is recorded once ARM's `move_files` has put it in place; a move that fails is logged and not recorded.
- **`orders`**, the patches' own: how each choice among shuffles was made, for a person reading the record.

Paths are ARM's container's, under `/root/video/`. Throughout, `handbrake_title` is HandBrake's number for a title, the *N* of its `title_N.mkv`, and `disc_title` or `playlist` the disc's own.

The record's **`titles`** trace each title from the disc to its transcode, one entry a title, keyed by the disc's own number for it. For a DVD that is `disc_title`, the DVD's own title number, which finds the title in the ISO (`HandBrakeCLI -i <iso> -t <disc_title>`), and the entry has `extracted`, the path of the file MakeMKV extracted into `titles/`. A Blu-ray's transcodes are made from `BDMV/` directly, so its key is `playlist`. Each entry also has `handbrake_title`, the number HandBrake gives the title and so its transcode's `title_N.mkv`, which is how the patches find the entry again. A DVD's titles are those MakeMKV extracted, of `MINLENGTH` or more and not every title on the disc, written once extraction ends; a title MakeMKV listed and didn't save has `handbrake_title` null and the reason `not_extracted`. The `_tNN` in an MKV's name is neither number: MakeMKV counts only the titles it kept, and HandBrake numbers the files in byte order of their names. A Blu-ray's are every title HandBrake was given, skipped ones included, written once HandBrake's scan has run; lengths can't stand in for the pairing, since a disc's featurette variants can lie a second apart and share all but one clip. `titles` is absent until it is written, and stays so for a job that ended first. The record holds no title's length: MakeMKV's reported duration counts each cell in whole seconds of the IFO's clock, up to a second a cell short of what plays, and a reader that needs one reads the raw copy.

Before the record, the patches kept the same facts in the raw folder as `arm-job.json`: written as each job ran from job 76, and afterwards, from ARM's database and the job's own log, for the Blu-ray folders that held the whole disc and were provably their job's (jobs 7, 8, 23, 25 and 27). `tools/arm-stamps/raw_copies.py`, run once in ARM's container, moves each into the table, for a job with no record yet, and never replaces one. It turns each stamp's `title_map` and `duplicates` into `titles`, and takes each transcode's path from where ARM left its `title_N.mkv`. A main feature ARM renamed for the film, before the patch that keeps its name, is the one HandBrake track ARM marked `main_feature`, and it records the folder's one other `.mkv` above `extras/` as that title's transcode (jobs 25, 27, 78 and 79). An `arm-job.json` still in a raw folder is read by nothing. The 2024 folders, which have no job, and DVD folders from before job 76 have no record.

In `completed/` every transcode keeps its `title_N.mkv`, flat for a series; for a movie the title ARM takes for the main feature sits in `<Title (Year)>/`, the rest under `extras/`. That placement is ARM's guess, from HandBrake's main-feature flag or the largest file, and is often wrong on a bonus disc; what the film is gets decided downstream. Upstream ARM renames the main feature `<Title (Year)>.mkv`, which loses its title number; folders from jobs up to 83 have that shape.

These raw copies are the **archival copy**. `DELRAWFILES: false` ensures they are never deleted after transcoding. When a backup ends, it is checked two ways, and the record's `check` holds each one's result, `passed`, `failed` or `not_run`:

- **`sizes`**: every Blu-ray clip in `BDMV/CLIPINF/` has its `.m2ts` in `BDMV/STREAM/` at no less than 192 bytes × the source-packet count in its `.clpi` (a big-endian `uint32` at byte 56), or a DVD's ISO is no smaller than the volume size its own ISO 9660 descriptor declares.
- **`hashes`**: MakeMKV reported no file failing the disc's hash check as it copied.

A copy is whole when both passed. When either failed, `problems` lists each failure, and the job fails before anything is transcoded, so the notification reports an error and the disc wants re-ripping. Both are `not_run` until the backup ends, so a job that died or was abandoned during the backup leaves a partial copy that says so. A record moved from an `arm-job.json` written afterwards has `hashes` `not_run`: MakeMKV's hash check ran in a job that kept no record of it, and can't be run again.

The same check applies by hand to a backup older than the record.

### Stage 2: Transcode (HandBrake)

HandBrake transcodes from the raw copy every title longer than `MINLENGTH` (420 seconds / 7 minutes) into standalone MKV files in `COMPLETED_PATH` (`/root/video/completed/<type>/<Title (Year)>/`).

Key encoding settings (defined in the `vulcanus-handbrake-preset` custom preset):

| Setting | Value | Notes |
|---|---|---|
| Video codec | SVT-AV1 10-bit | Royalty-free, ~30-50% smaller than H.264 at equivalent quality |
| Quality | CRF 30 (constant quality) | Perceptually equivalent to x264 CRF 20 |
| Encoder preset | 4 (SVT-AV1 scale: 0=slowest, 13=fastest) | Quality-focused; expect several hours per film |
| Audio | Opus stereo 192 kbps + Opus 5.1 320 kbps | Lossless TrueHD and DTS-HD MA are re-encoded too; the raw copy keeps the originals |
| Subtitles | All tracks (English + any) | Soft subs, not burned in |
| Container | MKV | With chapter markers and metadata passthrough |
| Deinterlace | Decomb (default) | Important for interlaced DVD content |

`MAX_CONCURRENT_TRANSCODES: 1` limits to one transcode at a time — AV1 at preset 4 saturates whatever it is given, and this runs on `piraeus-worker-1`, the smaller of the two workers, alongside everything else scheduled there.

A job runs on every CPU of `piraeus-worker-1` but CPU 0, which stays free for the node's other tenants, and ARM has no CPU limit. What a transcode costs those tenants shows in their own CPU wait (`container_pressure_cpu_waiting_seconds_total` per container), not in the node's load average or CPU pressure: both count HandBrake's threads queueing behind each other for its own CPUs, and read the same whether or not anything else is waiting.

A 1080p Blu-ray encodes at about 13 fps this way, with CPUs 1–5 each ~80% busy and CPU 0 ~15%. The job's affinity reads `1-5`:

```bash
kubectl exec -n automatic-ripping-machine deploy/automatic-ripping-machine -- \
  sh -c 'grep Cpus_allowed_list /proc/$(pgrep -o HandBrakeCLI)/status'
```

The load averages near 9 through an encode, with peaks to 19 against `NodeSystemSaturation`'s threshold of 12 on six CPUs, so the alert can go pending, or fire, on the transcode alone. Whether anything else is suffering shows in the other containers' CPU wait.

`MAINFEATURE: false` means all tracks above the minimum length are transcoded, not just the longest. This is necessary for TV show discs where multiple episodes need to be extracted.

### Titles that repeat other titles

A disc often offers the same material twice: a TV disc's "play all" title is every episode end to end, a bonus disc plays its featurettes in turn or as one documentary, and a film can be offered again with featurettes cut into it. Encoding both copies costs hours, and on a movie disc the play-all is often the longest title, so the one ARM takes for the film. So before encoding, the titles are compared by what they play from the disc — a Blu-ray playlist's clips and times, a DVD title's cells in the ISO — and a title is not transcoded when the titles kept play at least 99% of it, or all of it but a still picture. Length alone decides nothing.

Titles that each play at least 99% of the other's clips are **one title**, and only one of them is transcoded:

- **Copies**, playing their clips in the same order, keep the lowest-numbered.
- **Shuffles**, playing them in different orders, are playlist obfuscation: a disc offers an episode or a film as dozens or hundreds of playlists, only one of them in order, and the player's BD-J program picks it. The PBS releases of *Downton Abbey* from season 5 on cut one episode a disc this way, and Lionsgate does it to films. MakeMKV runs the BD-J program to guess, and on job 100's disc made none. On a Blu-ray the one kept is the one whose joins match best, from 1 − SSIM between the frames either side of each join, summed. In order, a join continues a shot; out of order, nearly every join is a jump. It picked the playlists published for *Downton Abbey* S6 D1 (job 100) from 150 and 148 shuffles. It is a guess, and the one a disc cut only at scene changes would defeat, as was reported on *Moonlight*. So the notification names the title kept, its score and the runner-up's, and a wrong pick is transcoded by hand from the raw copy. A DVD's shuffles are all transcoded, as reading its frames would mean reading cells out of the ISO.
- **A shuffle with a still picture cut in** is one title too. A Blu-ray title that plays at least 95% of another and, beyond it, only pieces whose frames at their start, quarters and end all match, is grouped with it. Job 100's disc cut its 60 s anti-ripping card, "Copy detected (or a rip tool is running…)", into one decoy, which no other title plays. The 95% bounds the frames read to near-matches, so a disc without shuffles reads none.

The rest of the comparison sees only the title kept, so a play-all of a shuffled episode is still found.

A **whole** is a title played by two or more others, its **parts**, each inside it and none overlapping another. Which of the two is kept:

- **A series keeps the parts**: each episode is an item of its own in the library, and the play-all is skipped.
- **A movie disc keeps the whole** when its parts follow one another with a chapter at each join, and skips the parts. A documentary in parts stays one uninterrupted file, and its encode has a keyframe at each chapter, so a part can be cut from it later without re-encoding.
- **Parts that interleave are kept apart**, and the whole is skipped. That is the shape of a film with featurettes cut in, such as *X-Men: First Class*'s "X Marks the Spot" mode, which is not the film. On a movie disc the notification names the title skipped, because an extended cut whose added scenes the disc also offers as a deleted-scenes title has the same shape and would be transcoded by hand from the raw copy.
- **Anything else is transcoded**: a whole holding pieces too short to be titles of their own (a featurette play-all with an unlisted interstitial), a film assembled from clips the disc also offers singly, alternate cuts that overlap.

If a skipped title was the main feature, the longest title kept becomes it, which decides only which transcode sits outside `extras/`. A disc whose structure can't be read has every title transcoded, with a warning in the job log. Nothing is skipped from the raw copy, only from the transcodes.

The record's `titles` hold the outcome once HandBrake's scan has run, and `titles_compared` is then `true`. A title not transcoded has `transcode` null and the reason `played_by_kept_titles`, and what plays it is said once: a whole skipped for its parts by its own `parts`, a part of a kept whole by that whole's, and otherwise, for a copy, a shuffle, or a title inside a kept whole that isn't one of its parts, by `played_by`, the disc's number for the one title kept that plays it. Each whole carries `interleaved` and its `parts`, each with `start`, the second of the whole at which it begins, in the playback time its transcode's chapters keep, a DVD's as a Blu-ray's; which side was kept is which side has no reason. That is what cuts a kept whole into its parts, or rejoins a series disc's episodes, when a disc turns out misidentified — a series filed as a movie keeps one long file and the joins to split it at. `orders` has an entry for each group of shuffles: the title kept, with its `mismatch`, the number of `titles` in the group, and `runner_up`, the best title in another order, with its `mismatch`; both `mismatch` and `runner_up` are null when the joins couldn't be read, and the lowest-numbered was kept. A record without `titles_compared` comes from a job before it, one that ended before transcoding began, or one whose structure couldn't be read.

In ARM's track table a skipped title keeps `ripped` true and an empty `filename`: ARM sets `ripped` when it registers a title longer than `MINLENGTH`, before any transcode, so it means eligible, not transcoded. reelbarrow reads neither; each title's `transcode` in the record says what was transcoded, and where it went.

### Manifests for reelbarrow

A sidecar in ARM's pod, `manifest-exporter`, runs reelbarrow's `arm-export-manifests`. Every 30 seconds it reads ARM's database, the `job` rows and the raw copy's record, and writes a manifest for each job into `import/automatic-ripping-machine/manifests/` on the share, where reelbarrow's service finds them, rewriting one only when its job has changed. After every pass that read the database and wrote what changed, it rewrites `exporter.json` there too, so one older than a minute means the exporter is stopped or failing. How it works and what it needs are reelbarrow's `docs/exporter.md`; what this deployment gives it:

- **reelbarrow's image, on reelbarrow's image policy** (`apps:reelbarrow`), so it moves with the service that reads what it writes.
- **ARM's database read-only**, the `db` subpath of ARM's volume. The database keeps a rollback journal (`PRAGMA journal_mode` is `delete`), which SQLite reads from a read-only mount; a switch to WAL would need the mount writable.
- **Of the share, only the manifests folder**, which the kubelet creates for the mount. It never sees the library.
- **Root**, as the share's CIFS mount in this pod (`uid=0,gid=0`, modes 0755) lets no other uid write, with a read-only root filesystem and every capability dropped.
- **`--arm-path /root/video=import/automatic-ripping-machine`**: ARM's mount of its folder of the share, and that folder's path on the share. ARM's `TZ` is `Etc/UTC`, the exporter's default; setting ARM's `TZ` means passing `--arm-timezone` the same.

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

# Stop the running job: it is marked failed and the disc ejected. HandBrake
# outlives main.py, so it is stopped too. The raw copy stays.
kubectl exec -n automatic-ripping-machine <pod> -- sh -c 'pkill -TERM -f "[m]ain.py"; sleep 15; pkill -TERM -f "[H]andBrakeCLI"'

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
