# Handles creating the VMs in Proxmox for a Talos Cluster.
# Does NOT bootstrap the cluster.
# Credit: https://github.com/chippawah/proxmox-cluster-example

variable "proxmox_api_token_id" {
    description = "The ID of the API token used for authentication with the Proxmox API."
    type = string
}

variable "proxmox_api_token_secret" {
    description = "The secret value of the token used for authentication with the Proxmox API."
    type = string
}

variable "proxmox_host_node" {
    description = "The name of the proxmox node where the cluster will be deployed"
    type = string
}

variable "proxmox_api_url" {
    description = "The URL for the Proxmox API."
    type = string
}

# Setting these here so it can be used in root module's .tfvars files.
#variable "ceph_mon_disk_storage_pool" {}
#variable "proxmox_debug" {}


module "talos" {
    source = "./modules/talos"
    proxmox_host_node = var.proxmox_host_node
    proxmox_api_url = var.proxmox_api_url
    proxmox_api_token_id = var.proxmox_api_token_id
    proxmox_api_token_secret = var.proxmox_api_token_secret
    proxmox_debug = true
    control_plane_node_count = 3
    worker_node_count = 0
    #ceph_mon_disk_storage_pool = "Intel_NVME"
    iso_image_location = "local:iso/talos-1.1.0-amd64.iso"
}

#output "control_plane_mac_addrs" {
#    value = module.talos.control_plane_config_mac_addrs
#}
#
#output "worker_mac_addrs" {
#    value = module.talos.worker_node_config_mac_addrs
#}
