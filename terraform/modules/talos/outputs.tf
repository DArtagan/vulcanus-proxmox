#output "worker_node_config_mac_addrs" {
#  value = {for vm, config in proxmox_vm_qemu.talos_worker_node : vm => config.network[0].macaddr}
#}
#
#output "control_plane_config_mac_addrs" {
#  value = {for vm, config in proxmox_vm_qemu.talos_control_plane_node : vm => config.network[0].macaddr}
#}

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
