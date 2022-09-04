variable "memory" {
    description = "The amount of memory in MiB to give the server."
    type = number
    default = 2048
}

variable "iso_image_location" {
    description = "The location of the Proxmox Backup Server iso image on the proxmox host (<storage pool>:<content type>/<file name>.iso)."
    type = string
    default = "local:iso/proxmox-backup-server.iso"
}

variable "boot_disk_storage_pool" {
    description = "The name of the storage pool where boot disk will be stored."
    type = string
    default = "local-zfs"
    #default = "SSD_Pool"
}

variable "boot_disk_size" {
    description = "The size of the boot disks. A numeric string with G, M, or K appended ex: 512M or 32G."
    type = string
    default = "10G"
}

variable "backup_disk_storage_pool" {
    description = "The name of the storage pool where backup disk."
    type = string
    default = "local-zfs"
}

variable "backup_disk_size" {
    description = "The size of the backup disk. A numeric string with G, M, or K appended ex: 512M or 32G."
    type = string
    default = "100G"
}

variable "config_network_bridge" {
    description = "The name of the network bridge on the Proxmox host that will be used for the configuration network."
    type = string
    default = "vmbr0"
}

variable "public_network_bridge" {
    description = "The name of the network bridge on the Proxmox host that will be used for the public network."
    type = string
    default = "vmbr0"
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
