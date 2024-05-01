terraform {
  required_version = ">= 0.12"
  required_providers {
    proxmox = {
      source = "telmate/proxmox"
      version = "2.9.11"
    }
    talos = {
      source = "siderolabs/talos"
      version = "0.3.4"
    }
  }
}

resource "proxmox_vm_qemu" "control_plane_node" {
  count = var.control_plane_node_count
  name = "talos-control-plane-${count.index}"
  iso = var.iso_image_location
  target_node = var.proxmox_host_node
  vmid = sum([900, count.index])
  qemu_os = "l26" # Linux kernel type
  scsihw = "virtio-scsi-pci"
  memory = var.control_plane_node_memory
  cpu = "kvm64"
  cores = 2
  sockets = 1
  args = "-cpu kvm64,+cx16,+lahf_lm,+popcnt,+sse3,+ssse3,+sse4.1,+sse4.2"
  onboot = true
  # TODO: maybe this can be set if switching to cloudinit provisioning, using router DHCP for now
  #ipconfig0 = "[gw=192.168.1.1, ip=192.168.1.${ sum([190, count.index]) }/24]"
  network {
    model = "virtio"
    bridge = var.config_network_bridge
    #tag = var.config_vlan
  }
  # TODO: why two bridges/vlans?
  #network {
  #  model = "virtio"
  #  bridge = var.public_network_bridge
  #  tag = var.public_vlan
  #}
  disk {
    type = "virtio"
    size = var.control_plane_boot_disk_size
    storage = var.boot_disk_storage_pool
  }
}


resource "proxmox_vm_qemu" "worker_node" {
  count = var.worker_node_count
  name = "talos-worker-${count.index}"
  iso = var.iso_image_location
  target_node = var.proxmox_host_node
  vmid = sum([910, count.index])
  qemu_os = "l26" # Linux kernel type
  scsihw = "virtio-scsi-pci"
  memory = var.worker_node_memory
  cpu = "kvm64"
  cores = var.worker_node_cpus
  sockets = 1
  # CPU options are special for talos.  SCSI and drive options are to attach the CD drive to the worker VM.  `addr=0x6` because 6 was the first spare PCI address after doing guess-and-check.
# The `/dev/sg` device number is very inconsistent, seems to need updating every restart.
  args = "-cpu kvm64,+cx16,+lahf_lm,+popcnt,+sse3,+ssse3,+sse4.1,+sse4.2 -device virtio-scsi-pci,id=scsi0,bus=pci.0,addr=0x6 -drive file=/dev/sg4,if=none,format=raw,id=drive-hostdev0,readonly=on -device scsi-generic,bus=scsi0.0,channel=0,scsi-id=0,lun=0,drive=drive-hostdev0,id=hostdev0"
  onboot = true
  # TODO: maybe this can be set if switching to cloudinit provisioning, using router DHCP for now
  network {
    model = "virtio"
    bridge = var.config_network_bridge
    #tag = var.config_vlan
  }
  #network {
  #  model = "virtio"
  #  tag = var.public_vlan
  #  bridge = var.public_network_bridge
  #}
  # TODO: why two bridges/vlans?
  #network {
  #  model = "virtio"
  #  bridge = var.public_network_bridge
  #  tag = var.public_vlan
  #}
  disk {
    type = "virtio"
    size = var.worker_boot_disk_size
    storage = var.boot_disk_storage_pool
  }
  disk {
    type = "virtio"
    size = var.openebs_disk_size
    storage = var.openebs_disk_storage_pool
  }
}


locals {
  control_plane_ip_root = join(".", slice(split(".", var.control_plane_ip_start), 0, 3))
  control_plane_ip_zero = split(".", var.control_plane_ip_start)[3]
  control_plane_endpoints = [
    for i in range(length(proxmox_vm_qemu.control_plane_node)):
      join(".", [
        local.control_plane_ip_root,
        sum([local.control_plane_ip_zero, i])
      ])
  ]

  worker_ip_root = join(".", slice(split(".", var.worker_ip_start), 0, 3))
  worker_ip_zero = split(".", var.worker_ip_start)[3]
  worker_endpoints = [
    for i in range(length(proxmox_vm_qemu.worker_node)):
      join(".", [
        local.worker_ip_root,
        sum([local.worker_ip_zero, i])
      ])
  ]
}


resource "talos_machine_secrets" "main" {}

data "talos_machine_configuration" "control_plane" {
  cluster_name = var.cluster_name
  cluster_endpoint = var.cluster_endpoint
  machine_type = "controlplane"
  talos_version = talos_machine_secrets.main.talos_version
  machine_secrets = talos_machine_secrets.main.machine_secrets
}

data "talos_machine_configuration" "worker" {
  cluster_name = var.cluster_name
  cluster_endpoint = var.cluster_endpoint
  machine_type = "worker"
  talos_version = talos_machine_secrets.main.talos_version
  machine_secrets = talos_machine_secrets.main.machine_secrets
}

data "talos_client_configuration" "main" {
  cluster_name = var.cluster_name
  client_configuration = talos_machine_secrets.main.client_configuration
  endpoints = local.control_plane_endpoints
}

resource "talos_machine_configuration_apply" "control_plane_config_apply" {
  client_configuration = talos_machine_secrets.main.client_configuration
  machine_configuration_input = data.talos_machine_configuration.control_plane.machine_configuration
  for_each = toset(local.control_plane_endpoints)
  node = local.control_plane_endpoints[index(local.control_plane_endpoints, each.value)]
  config_patches = [
    templatefile("${path.module}/templates/control-plane-patch.yaml.tmpl", {
      hostname = format("%s-control-plane-%s", var.cluster_name, index(local.control_plane_endpoints, each.value))
    })
  ]
}

resource "talos_machine_configuration_apply" "worker_config_apply" {
  client_configuration = talos_machine_secrets.main.client_configuration
  machine_configuration_input = data.talos_machine_configuration.worker.machine_configuration
  for_each = toset(local.worker_endpoints)
  node = local.worker_endpoints[index(local.worker_endpoints, each.value)]
  config_patches = [
    templatefile("${path.module}/templates/worker-patch.yaml.tmpl", {
      hostname = format("%s-worker-%s", var.cluster_name, index(local.worker_endpoints, each.value))
    }),
    file("${path.module}/files/worker-patch.json"),
  ]
}

resource "talos_machine_bootstrap" "bootstrap" {
  client_configuration = talos_machine_secrets.main.client_configuration
  endpoint = var.control_plane_ip_start
  node = var.control_plane_ip_start
}

data "talos_cluster_kubeconfig" "main" {
  client_configuration = talos_machine_secrets.main.client_configuration
  node = var.control_plane_ip_start
}
