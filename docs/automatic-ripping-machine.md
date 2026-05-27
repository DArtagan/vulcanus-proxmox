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
        │ squat.ai/cdrom device plugin
        ▼
ARM pod (apps namespace)
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
-device virtio-scsi-pci,id=scsihw0
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

## Kubernetes: Device Plugin and Security Policy

ARM runs in the `apps` namespace, which enforces **PodSecurity Admission (PSA) `baseline`**. This blocks `privileged: true` containers. ARM does not need it:

- **SG_IO** (raw SCSI ioctls used by MakeMKV) works without privileges. It is just an `ioctl` call on a device file. Default seccomp does not block it.
- **udev events** reach the container's udevd without privileges (explained below).

The **squat.ai generic device plugin** (`kubernetes/infrastructure/devices.yaml`) runs as a privileged DaemonSet in the `infrastructure` namespace. It exposes `/dev/sr0` and `/dev/sg0` on the node as an allocatable Kubernetes resource called `squat.ai/cdrom`.

The ARM deployment claims this resource:

```yaml
resources:
  limits:
    squat.ai/cdrom: 1
```

This is an exclusive resource — only one pod can hold it at a time. This is why the deployment uses `strategy: Recreate` rather than `RollingUpdate`: a rolling update would try to start the new pod before terminating the old one, but the new pod can never schedule because the old pod still holds the device.

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

This prevents the eject event (which the container does receive) from triggering a spurious rip attempt.

## Rip and Transcode Pipeline

Once ARM detects a disc and identifies it (via the manual identification gate or automatic metadata lookup), it runs through a two-stage pipeline: rip, then transcode.

### Data flow

```
Disc inserted → udev event → ARM ripper
  1. MakeMKV backup_dvd → /root/media/raw/<Title>/     (full decrypted disc structure)
  2. HandBrake transcode → /root/media/completed/<Title>/*.mkv  (Plex-ready files)
```

Both directories live on the SMB share (`//192.168.0.105/media/video/import/automatic-ripping-machine/`).

### Title identification gate

`MANUAL_WAIT: true` with a long timeout (1 year) pauses the pipeline after ripping to allow manual title identification via the ARM web UI (`arm.immortalkeep.com`). Title metadata is resolved using OMDb (`METADATA_PROVIDER: "omdb"`) or alternatively via CRC64-based disc identification. The transcode step only proceeds after identification is confirmed.

### Stage 1: Rip (MakeMKV)

`RIPMETHOD: "backup_dvd"` tells MakeMKV to create a full decrypted backup of the disc structure — `VIDEO_TS/` for DVDs, `BDMV/` for Blu-rays. This preserves all original tracks, menus, extras, and chapter structure. The output goes to `RAW_PATH` (`/root/media/raw/<Title>/`).

These raw backups are the **archival copy**. `DELRAWFILES: false` ensures they are never deleted after transcoding.

### Stage 2: Transcode (HandBrake)

HandBrake processes the raw backup and transcodes all tracks longer than `MINLENGTH` (420 seconds / 7 minutes) into standalone MKV files in `COMPLETED_PATH` (`/root/media/completed/<Title>/`).

Key encoding settings (defined in the `vulcanus-handbrake-preset` custom preset):

| Setting | Value | Notes |
|---|---|---|
| Video codec | SVT-AV1 10-bit | Royalty-free, ~30-50% smaller than H.264 at equivalent quality |
| Quality | CRF 30 (constant quality) | Perceptually equivalent to x264 CRF 20 |
| Encoder preset | 4 (SVT-AV1 scale: 0=slowest, 13=fastest) | Quality-focused; expect several hours per film on 6 CPU cores |
| Audio | Opus stereo 192 kbps + Opus 5.1 320 kbps | TrueHD and DTS-HD MA passthrough when present on source |
| Subtitles | All tracks (English + any) | Soft subs, not burned in |
| Container | MKV | With chapter markers and metadata passthrough |
| Deinterlace | Decomb (default) | Important for interlaced DVD content |

`MAX_CONCURRENT_TRANSCODES: 1` limits to one transcode at a time — AV1 at preset 4 is CPU-intensive and the worker node has 6 allocated cores.

`MAINFEATURE: false` means all tracks above the minimum length are transcoded, not just the longest. This is necessary for TV show discs where multiple episodes need to be extracted.

## ATA Link Stability

The Pioneer drive has a known failure mode: if multiple processes issue conflicting SCSI commands to the drive simultaneously, the ATA link can destabilise and the drive drops off the bus entirely. Symptoms:

- `dmesg` shows `ata4: link is slow to respond` followed by `ata4: hardreset failed`
- `/dev/sr0` and `/dev/sg3` disappear
- The drive is visible in the BIOS (SATA Port 10) but the OS cannot communicate with it

### Prevention

The primary source of conflicting SCSI commands is `smartd`. Proxmox installs and enables `smartd` with `DEVICESCAN`, which periodically probes all SCSI/ATA devices including `/dev/sr0`. While QEMU holds `/dev/sg3` open for the guest VM, these probes create the command conflict that destabilises the ATA link. `ansible/proxmoxer.yaml` deploys a `/etc/smartd.conf` that excludes `/dev/sr0` via the `!` prefix:

```
DEVICESCAN -d removable -n standby -m root -M exec /usr/share/smartmontools/smartd-runner !/dev/sr0
```

Additional precautions:
- Only one VM or process should ever have the drive open at a time
- Do not create test VMs that also pass through `sg3` while the ARM VM is running (see: the `cdrom-test` VM incident)
- The squat.ai/cdrom exclusive device resource enforces this at the Kubernetes level, but it does not prevent other QEMU VMs on the Proxmox host from also opening the sg device
- **Before running any manual `makemkvcon` command inside the ARM pod, verify no rip is in progress:**
  ```bash
  kubectl exec -n apps <arm-pod> -- pgrep -af makemkv
  ```
  If anything is returned, do not proceed until it finishes.

Note: SATA Aggressive Link Power Management (ALPM) was investigated as a potential cause but is already set to `max_performance` on all ports — it is not a factor.

### Recovery

If the drive disconnects, first try the software recovery script deployed to the host by `ansible/proxmoxer.yaml`:

```bash
/usr/local/bin/optical-drive-recovery.sh
```

This stops VM 911, attempts a SCSI bus rescan, then restarts the VM if the drive reappears. Note that the AHCI controller is shared with all ZFS disks (sda-sdh), so a controller reset is not attempted.

If software recovery fails, a **full power cycle** is required — not just a reboot. A reboot does not power-cycle SATA devices on this system. Shut down with `systemctl poweroff`, wait 15–30 seconds, then power on.

### Monitoring

`ansible/proxmoxer.yaml` deploys an `optical-drive-ata-monitor` systemd service that watches the kernel log for ata4 link events. When a disconnect is detected, it captures a diagnostic snapshot to `/var/log/optical-drive-monitor/` including the trigger line, recent ata4 messages, device node state, and which processes held the drive open.

## Replication Guide

To replicate this setup:

1. **Proxmox host prep**: Run `ansible/proxmoxer.yaml`. This creates the stable `/dev/optical-drive-sg` symlink and disables host-side polling for the optical drive.

2. **VM provisioning**: Set `host_cdrom_passthrough = true` for the worker VM in `terraform/main.tf`. Run `tofu apply`. This attaches the drive via scsi-generic and applies the Talos machine config udev rule for `events_poll_msecs=2000`.

3. **Device plugin**: `kubernetes/infrastructure/devices.yaml` (squat.ai generic device plugin) must be deployed and the plugin pod must be running on the worker node.

4. **ARM deployment**: `kubernetes/apps/automatic-ripping-machine/` deploys the ARM pod. The `squat.ai/cdrom: 1` resource limit, `strategy: Recreate`, and the udev rule + wrapper script ConfigMap mounts are all required.

5. **Verify**: With a disc in the drive, run:
   ```bash
   kubectl exec -n apps <arm-pod> -- makemkvcon --robot info disc:0
   ```
   You should see the drive identified as `BD-RE PIONEER BD-RW BDR-212U` with `Using LibreDrive mode` and a valid disc title.

## Debugging Reference

### Best external references

- **MakeMKV forum** (`forum.makemkv.com`) — authoritative source for LibreDrive behaviour, drive-specific quirks, and `makemkvcon` settings. Search by drive model or error message. The LibreDrive overview thread is at `t=18856`.
- **ARM GitHub** (`github.com/automatic-ripping-machine/automatic-ripping-machine`) — source for ARM's disc detection logic (`arm/models/job.py`), identify flow (`arm/ripper/identify.py`), and MakeMKV wrapper (`arm/ripper/makemkv.py`).

### Useful commands for live diagnosis

```bash
# Find the running ARM pod
kubectl get pods -n apps -l app=automatic-ripping-machine

# Pod logs (startup, udev events, ARM process output)
kubectl logs -n apps <pod>

# Check for active rip processes — do this before running any manual makemkvcon command
kubectl exec -n apps <pod> -- pgrep -af makemkv

# Query the ARM job database directly
kubectl exec -n apps <pod> -- python3 -c "
import sqlite3; conn = sqlite3.connect('/root/db/arm.db'); conn.row_factory = sqlite3.Row
c = conn.cursor(); c.execute('SELECT job_id,title,disctype,status,errors,logfile FROM job ORDER BY job_id DESC LIMIT 10')
[print(dict(r)) for r in c.fetchall()]"

# Read a specific job log (logs live in /root/logs/ inside the pod)
kubectl exec -n apps <pod> -- tail -100 /root/logs/<logfile>

# Check disc/drive state
kubectl exec -n apps <pod> -- makemkvcon --robot info disc:0

# Watch rip progress
kubectl exec -n apps <pod> -- ls -lh /root/media/raw/<job-dir>/
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

Wait 15–30 seconds after the machine goes dark, then power it back on. The drive will re-enumerate at boot and `/dev/sr0`, `/dev/sg3`, and `/dev/optical-drive-sg` will reappear.
