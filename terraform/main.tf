terraform {
  required_providers {
    flux = {
      source = "fluxcd/flux"
      version = "1.7.4"
    }
    github = {
      source = "integrations/github"
      version = "6.7.5"
    }
    proxmox = {
      source = "telmate/proxmox"
      version = "3.0.2-rc05"
    }
    talos = {
      source = "siderolabs/talos"
      version = "0.9.0"
    }
    tls = {
      source = "hashicorp/tls"
      version = "4.1.0"
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

variable "github_token" {
  type = string
  sensitive = true
  description = "Github Personal Access Token for flux's use."
}

variable "commit_author" {
  type = string
  description = "Name of the person associated with that Github token."
}

variable "commit_email" {
  type = string
  description = "Email of the person associated with that Github token."
}


# Setting these here so it can be used in root module's .tfvars files.
#variable "ceph_mon_disk_storage_pool" {}
#variable "proxmox_debug" {}


data "github_repository" "main" {
  full_name = "dartagan/vulcanus-proxmox"
}

provider "github" {
  owner = "dartagan"
  token = var.github_token
}

# Note: the first time this was run, to set the `args` value, which is a
# root-only setting, the `_api_token` lines above were commented out and
# PM_USER and PM_PASS environment variables were set with root's
# credentials.  Then they're toggled back on afterwards

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
  backup_disk_size = "2T"
}


module "talos" {
  source = "./modules/talos"
  proxmox_host_node = var.proxmox_host_node
  #proxmox_api_url = var.proxmox_api_url
  #proxmox_api_token_id = var.proxmox_api_token_id
  #proxmox_api_token_secret = var.proxmox_api_token_secret
  #proxmox_debug = true
  control_plane_node_count = 1
  worker_node_count = 1
  worker_node_cpus = 8
  worker_node_memory = 24576
  worker_boot_disk_size = "100G"
  openebs_disk_size = "1T"
  cluster_name = "piraeus"
  cluster_endpoint = "https://192.168.0.200:6443"
  control_plane_ip_start = "192.168.0.190"
  worker_ip_start = "192.168.0.195"

  # ISO first used to create the cluster. From here on out, use `talosctl upgrade`.
  iso_image_location = "local:iso/talos-1.2.6-amd64.iso"
}

resource "local_sensitive_file" "kubeconfig" {
  content = module.talos.kubeconfig
  filename = "../.kubeconfig"
}

resource "local_sensitive_file" "talosconfig" {
  content = module.talos.talosconfig
  filename = "../.talosconfig"
}

module "fluxcd" {
  source = "./modules/fluxcd"
  branch = "main"
  commit_author = var.commit_author
  commit_email = var.commit_email
  github_owner = "dartagan"
  github_token = var.github_token
  kubeconfig_path = local_sensitive_file.kubeconfig.filename
  repository_name = data.github_repository.main.name
  target_path = "kubernetes/cluster"
}
