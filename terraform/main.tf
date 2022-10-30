# Handles creating the VMs in Proxmox for a Talos Cluster.
# Does NOT bootstrap the cluster.
# Credit: https://github.com/chippawah/proxmox-cluster-example

terraform {
  required_providers {
    proxmox = {
      source = "telmate/proxmox"
      version = ">=2.9.11"
    }
  }
}

variable "proxmox_api_token_id" {
  description = "The ID of the API token used for authentication with the Proxmox API."
  type = string
  sensitive = true
}

variable "proxmox_api_token_secret" {
  description = "The secret value of the token used for authentication with the Proxmox API."
  type = string
  sensitive = true
}

variable "proxmox_host_node" {
  description = "The name of the proxmox node where the cluster will be deployed"
  type = string
}

variable "proxmox_api_url" {
  description = "The URL for the Proxmox API."
  type = string
}

variable "proxmox_tls_insecure" {
    description = "If the TLS connection is insecure (self-signed). This is usually the case."
    type = bool
    default = true
}

variable "proxmox_debug" {
    description = "If the debug flag should be set when interacting with the Proxmox API."
    type = bool
    default = false
}

variable "public_key" {
  description = "The public key to be put recognized by containers/vms for remote connection."
  type = string
}


# Setting these here so it can be used in root module's .tfvars files.
#variable "ceph_mon_disk_storage_pool" {}
#variable "proxmox_debug" {}

provider "proxmox" {
  pm_api_url = var.proxmox_api_url
  #pm_api_token_id = var.proxmox_api_token_id
  #pm_api_token_secret = var.proxmox_api_token_secret
  pm_tls_insecure = var.proxmox_tls_insecure
  pm_debug = var.proxmox_debug
}


resource "proxmox_lxc" "fileserver" {
  target_node = "vulcanus"
  hostname = "fileserver"
  vmid = 105
  ostemplate = "local:vztmpl/ubuntu-22.04-standard_22.04-1_amd64.tar.zst"
  unprivileged = false
  memory = 512
  swap = 0
  nameserver = "192.168.0.104"
  onboot = true
  start = true

  ssh_public_keys = var.public_key

  rootfs {
    storage = "local-zfs"
    size = "8G"
  }

  mountpoint {
    key = "0"
    slot = 0
    storage = "/rpool/storage/media"
    volume = "/rpool/storage/media"
    mp = "/mnt/storage/media"
    size = "1M"
  }

  mountpoint {
    key = "1"
    slot = 1
    storage = "/rpool/storage/filesync"
    volume = "/rpool/storage/filesync"
    mp = "/mnt/storage/filesync"
    size = "1M"
  }

  mountpoint {
    key = "2"
    slot = 2
    storage = "/rpool/storage/photos"
    volume = "/rpool/storage/photos"
    mp = "/mnt/storage/photos"
    size = "1M"
  }

  mountpoint {
    key = "3"
    slot = 3
    storage = "/rpool/backups/borg"
    volume = "/rpool/backups/borg"
    mp = "/mnt/backups/borg"
    size = "1M"
  }

  network {
    name = "eth0"
    bridge = "vmbr0"
    gw = "192.168.0.1"
    ip = "192.168.0.105/24"
  }
}


module "proxmox_backup_server" {
  source = "./modules/proxmox_backup_server"
  proxmox_host_node = var.proxmox_host_node
  proxmox_api_url = var.proxmox_api_url
  proxmox_api_token_id = var.proxmox_api_token_id
  proxmox_api_token_secret = var.proxmox_api_token_secret
  iso_image_location = "local:iso/proxmox-backup-server_2.2-1.iso"
  backup_disk_storage_pool = "proxmox_backup_server"
  backup_disk_size = "500G"
}


module "talos" {
  source = "./modules/talos"
  proxmox_host_node = var.proxmox_host_node
  proxmox_api_url = var.proxmox_api_url
  proxmox_api_token_id = var.proxmox_api_token_id
  proxmox_api_token_secret = var.proxmox_api_token_secret
  proxmox_debug = true
  control_plane_node_count = 3
  worker_node_count = 1

  #ceph_mon_disk_storage_pool = "Intel_NVME"
  iso_image_location = "local:iso/talos-1.2.6-amd64.iso"
}

#output "control_plane_mac_addrs" {
#    value = module.talos.control_plane_config_mac_addrs
#}
#
#output "worker_mac_addrs" {
#    value = module.talos.worker_node_config_mac_addrs
#}
