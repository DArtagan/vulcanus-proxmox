# After provisioning the box, complete the install manually via the booted GUI
# Then complete configuration at the web GUI at https://192.168.0.107:8007/

terraform {
  required_providers {
    proxmox = {
      source = "telmate/proxmox"
      version = ">=3.0.0"
    }
  }
}

provider "proxmox" {
  pm_api_url = var.proxmox_api_url
  pm_api_token_id = var.proxmox_api_token_id
  pm_api_token_secret = var.proxmox_api_token_secret
  pm_tls_insecure = var.proxmox_tls_insecure
  pm_debug = var.proxmox_debug
}

resource "proxmox_vm_qemu" "proxmox-backup-server" {
  name = "proxmox-backup-server"
  target_node = var.proxmox_host_node
  vmid = 107
  qemu_os = "l26" # Linux kernel type
  scsihw = "virtio-scsi-pci"
  memory = var.memory
  onboot = true
  ipconfig0 = "[gw=192.168.0.1, ip=192.168.0.107/24]"
  cpu {
    type = "kvm64"
    cores = 2
    sockets = 1
  }
  network {
    id = 0
    model = "virtio"
    bridge = var.config_network_bridge
  }
  disks {
    ide {
      ide2 {
        cdrom {
          iso = var.iso_image_location
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
      virtio1 {
        disk {
          size = var.backup_disk_size
          storage = var.backup_disk_storage_pool
          backup = true
        }
      }
    }
  }
}
