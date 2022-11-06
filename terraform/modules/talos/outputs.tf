#output "worker_node_config_mac_addrs" {
#  value = {for vm, config in proxmox_vm_qemu.talos_worker_node : vm => config.network[0].macaddr}
#}
#
#output "control_plane_config_mac_addrs" {
#  value = {for vm, config in proxmox_vm_qemu.talos_control_plane_node : vm => config.network[0].macaddr}
#}

output "machineconfig_controlplane" {
  value = talos_machine_configuration_controlplane.machineconfig_control_plane.machine_config
  sensitive = true
}

output "machineconfig_worker" {
  value = talos_machine_configuration_worker.machineconfig_worker.machine_config
  sensitive = true
}

output "talosconfig" {
  value = talos_client_configuration.talosconfig.talos_config
  sensitive = true
}

output "kubeconfig" {
  value = talos_cluster_kubeconfig.kubeconfig.kube_config
  sensitive = true
}
