# After provisioning the box, complete the install manually via the booted GUI
# Then complete configuration at the web GUI at https://192.168.0.107:8007/

terraform {
  required_providers {
    proxmox = {
      source = "telmate/proxmox"
      version = "2.9.11"
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
  iso = var.iso_image_location
  target_node = var.proxmox_host_node
  vmid = 107
  qemu_os = "l26" # Linux kernel type
  scsihw = "virtio-scsi-pci"
  memory = var.memory
  cpu = "kvm64"
  cores = 2
  sockets = 1
  onboot = true
  # TODO: maybe this can be set if switching to cloudinit provisioning, using router DHCP for now
  #ipconfig0 = "gw=192.168.0.1, ip=192.168.0.107/24"
  network {
    model = "virtio"
    bridge = var.config_network_bridge
  }
  disk {
    type = "virtio"
    size = var.boot_disk_size
    storage = var.boot_disk_storage_pool
  }
  disk {
    type = "virtio"
    size = var.backup_disk_size
    storage = var.backup_disk_storage_pool
  }
}
