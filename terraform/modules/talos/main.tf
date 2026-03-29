terraform {
  required_version = ">= 0.12"
  required_providers {
    talos = {
      source = "siderolabs/talos"
      version = ">=0.10.0"
    }
  }
}


# --- Variables ---

variable "cluster_name" {
  description = "A name to provide for the Talos cluster."
  type = string
}

variable "cluster_endpoint" {
  description = "The endpoint for the Talos cluster."
  type = string
}

variable "control_plane_nodes" {
  description = "Map of control plane node name to its IP address and Talos config patches."
  type = map(object({
    ip_address = string
    config_patches = list(string)
  }))
}

variable "worker_nodes" {
  description = "Map of worker node name to its IP address and Talos config patches."
  type = map(object({
    ip_address = string
    config_patches = list(string)
  }))
}


# --- Talos Configuration ---

locals {
  control_plane_ips = [for node in var.control_plane_nodes : node.ip_address]
  first_control_plane_ip = local.control_plane_ips[0]
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
  endpoints = local.control_plane_ips
}

resource "talos_machine_configuration_apply" "control_plane" {
  for_each = var.control_plane_nodes
  client_configuration = talos_machine_secrets.main.client_configuration
  machine_configuration_input = data.talos_machine_configuration.control_plane.machine_configuration
  node = each.value.ip_address
  config_patches = each.value.config_patches
}

resource "talos_machine_configuration_apply" "worker" {
  for_each = var.worker_nodes
  client_configuration = talos_machine_secrets.main.client_configuration
  machine_configuration_input = data.talos_machine_configuration.worker.machine_configuration
  node = each.value.ip_address
  config_patches = each.value.config_patches
}

resource "talos_machine_bootstrap" "bootstrap" {
  client_configuration = talos_machine_secrets.main.client_configuration
  endpoint = local.first_control_plane_ip
  node = local.first_control_plane_ip
}

data "talos_cluster_kubeconfig" "main" {
  client_configuration = talos_machine_secrets.main.client_configuration
  node = local.first_control_plane_ip
}


# --- Outputs ---

output "machineconfig_controlplane" {
  value = data.talos_machine_configuration.control_plane.machine_configuration
  sensitive = true
}

output "machineconfig_worker" {
  value = data.talos_machine_configuration.worker.machine_configuration
  sensitive = true
}

output "talosconfig" {
  value = data.talos_client_configuration.main.talos_config
  sensitive = true
}

output "kubeconfig" {
  value = data.talos_cluster_kubeconfig.main.kubeconfig_raw
  sensitive = true
}
