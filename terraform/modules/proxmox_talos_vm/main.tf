terraform {
  required_version = ">= 0.12"
  required_providers {
    proxmox = {
      source = "telmate/proxmox"
      version = ">=3.0.0"
    }
  }
}


# --- Variables ---

variable "name" {
  description = "The name of the VM in Proxmox."
  type = string
}

variable "hostname" {
  description = "The Talos/Kubernetes hostname for this node. Defaults to the VM name if not set."
  type = string
  default = null
}

variable "vmid" {
  description = "The Proxmox VM ID."
  type = number
}

variable "target_node" {
  description = "The Proxmox node to deploy the VM on."
  type = string
}

variable "memory" {
  description = "Memory in MiB."
  type = number
  default = 4096
}

variable "cores" {
  description = "Number of CPU cores."
  type = number
  default = 2
}

variable "ip_address" {
  description = "Static IP address for the VM (without CIDR, e.g. 192.168.0.190)."
  type = string
}

variable "gateway" {
  description = "Network gateway."
  type = string
  default = "192.168.0.1"
}

variable "network_bridge" {
  description = "Proxmox network bridge."
  type = string
  default = "vmbr0"
}

variable "boot_disk_size" {
  description = "Boot disk size (e.g. 10G, 100G)."
  type = string
  default = "10G"
}

variable "boot_disk_storage_pool" {
  description = "Storage pool for the boot disk."
  type = string
  default = "local-zfs"
}

variable "iso_image_location" {
  description = "ISO image location on the Proxmox host. Set to empty string to skip attaching a cdrom."
  type = string
  default = ""
}

variable "extra_args" {
  description = "Additional QEMU args to append (e.g. for cdrom passthrough)."
  type = string
  default = ""
}

variable "start_at_node_boot" {
  description = "Whether the VM should start when the Proxmox node boots."
  type = bool
  default = true
}

variable "is_control_plane" {
  description = "Whether this is a control plane node. Adds VIP config patch."
  type = bool
  default = false
}

variable "control_plane_vip" {
  description = "VIP address for control plane nodes."
  type = string
  default = "192.168.0.200"
}

variable "openebs_disk" {
  description = "OpenEBS disk configuration. Set to null to skip. When set, adds disk partition, kubelet mount, and extension patches."
  type = object({
    size = string
    storage_pool = optional(string, "local-zfs")
    mountpoint = optional(string, "/var/openebs")
    device = optional(string, "/dev/vdb")
  })
  default = null
}

variable "include_udev_workaround" {
  description = "Include the persistent-storage udev rules workaround."
  type = bool
  default = false
}

variable "uefi" {
  description = "Use OVMF UEFI BIOS instead of SeaBIOS. Adds an EFI disk using the boot disk storage pool."
  type = bool
  default = true
}

variable "host_cdrom_passthrough" {
  description = "Pass through the Proxmox host's first physical optical drive to the VM. Cannot be done via the Proxmox API for SCSI slots, so this injects raw QEMU args that attach a scsi-cd device on the existing virtio-scsi-pci bus (virtioscsi0). The guest kernel attaches sr_mod (/dev/sr0) and sg (/dev/sg0) for full MMC command access including tray eject."
  type = bool
  default = false
}


# --- VM Resource ---

locals {
  talos_cpu_args = "-cpu kvm64,+cx16,+lahf_lm,+popcnt,+sse3,+ssse3,+sse4.1,+sse4.2"
  # Proxmox does not support SCSI CD-ROM host passthrough via its API (scsi0: cdrom,media=cdrom
  # is rejected; scsi0: /dev/sr0 attaches as scsi-hd, not scsi-cd). Raw QEMU args bypass
  # the API and attach a proper scsi-cd on the virtio-scsi-pci bus Proxmox already creates.
  # The drive is opened read-only to handle pressed (read-only) optical media.
  # Proxmox only instantiates the virtio-scsi-pci controller when at least one SCSI disk
  # is assigned through the Proxmox API. Since no such disk exists here, we must create the
  # controller ourselves so that scsi-cd has a bus to attach to.
  cdrom_args = var.host_cdrom_passthrough ? "-device virtio-scsi-pci,id=scsihw0 -drive file=/dev/sr0,if=none,id=drive-cdrom0,format=raw,media=cdrom,readonly=on -device scsi-cd,drive=drive-cdrom0,bus=scsihw0.0,id=cdrom0" : ""
  full_args = join(" ", compact([local.talos_cpu_args, var.extra_args, local.cdrom_args]))
  hostname = var.hostname != null ? var.hostname : var.name
}

resource "proxmox_vm_qemu" "main" {
  name = var.name
  target_node = var.target_node
  vmid = var.vmid
  qemu_os = "l26"
  bios = var.uefi ? "ovmf" : "seabios"
  scsihw = "virtio-scsi-pci"
  memory = var.memory
  args = local.full_args
  agent = 1
  skip_ipv6 = true
  start_at_node_boot = var.start_at_node_boot
  startup_shutdown {
    order = -1
    shutdown_timeout = -1
    startup_delay = -1
  }
  ipconfig0 = "[gw=${var.gateway}, ip=${var.ip_address}/24]"
  cpu {
    type = "kvm64"
    cores = var.cores
    sockets = 1
  }
  network {
    id = 0
    model = "virtio"
    bridge = var.network_bridge
  }
  dynamic "efidisk" {
    for_each = var.uefi ? [1] : []
    content {
      efitype = "4m"
      storage = var.boot_disk_storage_pool
    }
  }
  disks {
    dynamic "ide" {
      for_each = var.iso_image_location != "" ? [1] : []
      content {
        ide2 {
          cdrom {
            iso = var.iso_image_location
          }
        }
      }
    }
    virtio {
      virtio0 {
        disk {
          size = var.boot_disk_size
          storage = var.boot_disk_storage_pool
          backup = true
        }
      }
      dynamic "virtio1" {
        for_each = var.openebs_disk != null ? [var.openebs_disk] : []
        content {
          disk {
            size = virtio1.value.size
            storage = virtio1.value.storage_pool
            backup = true
          }
        }
      }
    }
  }
}


# --- Config Patches ---

locals {
  base_patch = templatefile("${path.module}/templates/base-patch.yaml.tmpl", {
    hostname = local.hostname
  })

  control_plane_patch = var.is_control_plane ? templatefile("${path.module}/templates/control-plane-patch.yaml.tmpl", {
    vip = var.control_plane_vip
  }) : null

  openebs_disk_patch = var.openebs_disk != null ? templatefile("${path.module}/templates/openebs-disk-patch.yaml.tmpl", {
    device = var.openebs_disk.device
    mountpoint = var.openebs_disk.mountpoint
  }) : null

  openebs_kubelet_patch = var.openebs_disk != null ? file("${path.module}/files/openebs-kubelet-patch.json") : null

  udev_patch = var.include_udev_workaround ? file("${path.module}/files/udev-persistent-storage-patch.yaml") : null

  config_patches = compact([
    local.base_patch,
    local.control_plane_patch,
    local.openebs_disk_patch,
    local.openebs_kubelet_patch,
    local.udev_patch,
  ])
}


# --- Outputs ---

output "ip_address" {
  description = "The static IP address of the VM."
  value = var.ip_address
}

output "config_patches" {
  description = "Talos config patches generated for this VM."
  value = local.config_patches
}
