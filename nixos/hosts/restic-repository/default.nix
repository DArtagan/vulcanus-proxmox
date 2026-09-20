# The restic repository host: an unprivileged Proxmox LXC on vulcanus serving
# `rest-server --append-only` to the cluster, and running restic itself against
# the same repository over the local filesystem.
#
# See docs/backups.md for why the repository is a ZFS dataset on a container
# rather than a zvol on a VM, and why retention runs here rather than in the
# cluster.
{
  inputs,
  modulesPath,
  ...
}:
{
  imports = [
    # Sets systemd-networkd to take its address from Proxmox, leaves the
    # hostname to /etc/hostname, and drops the bootloader. Defaults are right
    # for an unprivileged container, so nothing below overrides them.
    "${modulesPath}/virtualisation/proxmox-lxc.nix"
    inputs.sops-nix.nixosModules.sops
  ];

  networking.hostName = "restic-repository";

  # Matches its host. The Kubernetes side runs on UTC and the schedule matrix in
  # docs/backups.md is stated in UTC for that reason, but a timer read here
  # should mean what the hypervisor's logs mean.
  time.timeZone = "America/Denver";

  # The same key `terraform/main.tf` hands every other container and VM.
  users.users.root.openssh.authorizedKeys.keys = [
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIyPkfTI0io9dZsJstcf29tddyrsHr9bnM8UXKtaVJwm"
  ];

  services.openssh = {
    enable = true;
    settings = {
      PasswordAuthentication = false;
      # colmena deploys as root, and there is no other account here to sudo
      # from: the container runs one service and holds no interactive users.
      PermitRootLogin = "prohibit-password";
    };
  };

  # Never changes once set. It is the release this host was first built on, not
  # the one it currently runs.
  system.stateVersion = "26.11";
}
