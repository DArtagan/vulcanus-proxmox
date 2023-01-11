terraform {
  required_version = ">= 0.12"
  required_providers {
    proxmox = {
      source = "telmate/proxmox"
      version = ">=2.9.11"
    }
    talos = {
      source = "siderolabs/talos"
      version = "0.1.0-alpha.10"
    }
  }
}

provider "proxmox" {
  pm_api_url = var.proxmox_api_url
  #pm_api_token_id = var.proxmox_api_token_id
  #pm_api_token_secret = var.proxmox_api_token_secret
  pm_tls_insecure = var.proxmox_tls_insecure
  pm_debug = var.proxmox_debug
}

# Note: the first time this was run, to set the `args` value, which is a
# root-only setting, the `_api_token` lines above were commented out and
# PM_USER and PM_PASS environment variables were set with root's
# credentials.

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
  #ipconfig0 = "[gw=192.168.0.1, ip=192.168.0.${ sum([190, count.index]) }/24]"
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
    size = var.boot_disk_size
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
  cores = 3
  sockets = 1
  args = "-cpu kvm64,+cx16,+lahf_lm,+popcnt,+sse3,+ssse3,+sse4.1,+sse4.2"
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
    size = var.boot_disk_size
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


resource "talos_machine_secrets" "machine_secrets" {}

resource "talos_machine_configuration_controlplane" "machineconfig_control_plane" {
  cluster_name = var.cluster_name
  cluster_endpoint = var.cluster_endpoint
  machine_secrets = talos_machine_secrets.machine_secrets.machine_secrets
}

resource "talos_machine_configuration_worker" "machineconfig_worker" {
  cluster_name = var.cluster_name
  cluster_endpoint = var.cluster_endpoint
  machine_secrets = talos_machine_secrets.machine_secrets.machine_secrets
}

resource "talos_client_configuration" "talosconfig" {
  cluster_name = var.cluster_name
  machine_secrets = talos_machine_secrets.machine_secrets.machine_secrets
  endpoints = local.control_plane_endpoints
}

resource "talos_machine_configuration_apply" "control_plane_config_apply" {
  talos_config = talos_client_configuration.talosconfig.talos_config
  machine_configuration = talos_machine_configuration_controlplane.machineconfig_control_plane.machine_config
  for_each = toset(local.control_plane_endpoints)
  endpoint = local.control_plane_endpoints[index(local.control_plane_endpoints, each.value)]
  node = local.control_plane_endpoints[index(local.control_plane_endpoints, each.value)]
  config_patches = [
    templatefile("${path.module}/templates/control-plane-patch.yaml.tmpl", {
      hostname = format("%s-control-plane-%s", var.cluster_name, index(local.control_plane_endpoints, each.value))
    })
  ]
}

resource "talos_machine_configuration_apply" "worker_config_apply" {
  talos_config = talos_client_configuration.talosconfig.talos_config
  machine_configuration = talos_machine_configuration_worker.machineconfig_worker.machine_config
  for_each = toset(local.worker_endpoints)
  endpoint = local.worker_endpoints[index(local.worker_endpoints, each.value)]
  node = local.worker_endpoints[index(local.worker_endpoints, each.value)]
  config_patches = [
    templatefile("${path.module}/templates/worker-patch.yaml.tmpl", {
      hostname = format("%s-worker-%s", var.cluster_name, index(local.worker_endpoints, each.value))
    }),
    file("${path.module}/files/worker-patch.json"),
  ]
}

resource "talos_machine_bootstrap" "bootstrap" {
  talos_config = talos_client_configuration.talosconfig.talos_config
  endpoint = var.control_plane_ip_start
  node = var.control_plane_ip_start
}

resource "talos_cluster_kubeconfig" "kubeconfig" {
  talos_config = talos_client_configuration.talosconfig.talos_config
  endpoint = var.control_plane_ip_start
  node = var.control_plane_ip_start
}
