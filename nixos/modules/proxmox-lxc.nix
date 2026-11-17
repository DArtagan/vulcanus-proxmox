# A NixOS container on vulcanus: Terraform creates it, and this gives it the
# network Proxmox does not. Each host sets networking.hostName to match
# terraform/main.tf's. See docs/nixos.md.
{ address, modulesPath, ... }:
{
  imports = [
    # Its two `manage*` defaults are overridden below.
    "${modulesPath}/virtualisation/proxmox-lxc.nix"
  ];

  # Proxmox writes no network into an `unmanaged` container, and sets only the
  # hostname, at creation. See docs/nixos.md.
  proxmoxLXC = {
    manageNetwork = true;
    manageHostName = true;
  };

  networking = {
    useDHCP = false;
    # CoreDNS, which also answers *.immortalkeep.com. See docs/network.md.
    nameservers = [ "192.168.0.202" ];
  };

  # manageNetwork turns the module's networking off rather than handing it over.
  systemd.network.enable = true;
  # networkd turns resolved on, which refuses a container's default
  # useHostResolvConf. One static nameserver needs no resolver daemon.
  networking.useHostResolvConf = false;
  services.resolved.enable = false;

  systemd.network.networks."10-eth0" = {
    matchConfig.Name = "eth0";
    address = [ "${address}/24" ];
    routes = [ { Gateway = "192.168.0.1"; } ];
    linkConfig.RequiredForOnline = "routable";
  };
}
