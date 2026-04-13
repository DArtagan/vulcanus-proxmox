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

## ATA Link Stability

The Pioneer drive has a known failure mode: if multiple processes issue conflicting SCSI commands to the drive simultaneously, the ATA link can destabilise and the drive drops off the bus entirely. Symptoms:

- `dmesg` shows `ata4: link is slow to respond` followed by `ata4: hardreset failed`
- `/dev/sr0` and `/dev/sg3` disappear
- The drive is visible in the BIOS (SATA Port 10) but the OS cannot communicate with it

Recovery requires a **full power cycle** — not just a reboot. A reboot does not power-cycle SATA devices on this system. Shut down with `systemctl poweroff`, wait 15–30 seconds, then power on.

To avoid this:
- Only one VM or process should ever have the drive open at a time
- Do not create test VMs that also pass through `sg3` while the ARM VM is running (see: the `cdrom-test` VM incident)
- The squat.ai/cdrom exclusive device resource enforces this at the Kubernetes level, but it does not prevent other QEMU VMs on the Proxmox host from also opening the sg device
- **Before running any manual `makemkvcon` command inside the ARM pod, verify no rip is in progress:**
  ```bash
  kubectl exec -n apps <arm-pod> -- pgrep -af makemkv
  ```
  If anything is returned, do not proceed until it finishes.

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
