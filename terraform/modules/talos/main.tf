terraform {
  required_providers {
    proxmox = {
      source = "telmate/proxmox"
      version = ">=2.9.10"
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


resource "proxmox_vm_qemu" "talos-control-plane-node" {
  count = var.control_plane_node_count
  name = "talos-control-plane-${count.index}"
  iso = var.iso_image_location
  target_node = var.proxmox_host_node
  vmid = sum([900, count.index])
  qemu_os = "l26" # Linux kernel type
  scsihw = "virtio-scsi-pci"
  memory = var.control_plane_node_memory
  cpu = "kvm64"
  cores = 4
  sockets = 1
  network {
    model = "virtio"
    bridge = var.config_network_bridge
    tag = var.config_vlan
  }
  network {
    model = "virtio"
    bridge = var.public_network_bridge
    tag = var.public_vlan
  }
  disk {
    type = "virtio"
    size = var.boot_disk_size
    storage = var.boot_disk_storage_pool
  }
  # TODO: what to do about Ceph?
  #disk {
  #  type = "virtio"
  #  size = var.ceph_mon_disk_size
  #  storage = var.ceph_mon_disk_storage_pool
  #}
}


resource "proxmox_vm_qemu" "talos-worker-node" {
  count = var.worker_node_count
  name = "talos-worker-${count.index}"
  iso = var.iso_image_location
  target_node = var.proxmox_host_node
  vmid = sum([900, count.index, var.control_plane_node_count])
  qemu_os = "l26" # Linux kernel type
  scsihw = "virtio-scsi-pci"
  memory = var.worker_node_memory
  cpu = "kvm64"
  cores = 4
  sockets = 1
  network {
    model = "virtio"
    # TODO: what to do about VLANs?
    tag = var.config_vlan
    bridge = var.config_network_bridge
  }
  network {
    model = "virtio"
    tag = var.public_vlan
    bridge = var.public_network_bridge
  }
  disk {
    type = "virtio"
    size = var.boot_disk_size
    storage = var.boot_disk_storage_pool
  }
  # TODO: what to do about Ceph?
  #disk {
  #  type = "virtio"
  #  size = var.ceph_mon_disk_size
  #  storage = var.ceph_mon_disk_storage_pool
  #}
  #disk {
  #  type = "virtio"
  #  size = var.ceph_osd_disk_size
  #  storage = var.ceph_osd_disk_storage_pool
  #}
}
