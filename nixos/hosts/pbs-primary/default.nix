# The PBS primary: vzdump writes every guest's image here, and PBS #2 pulls
# from it. LXC 109 on vulcanus. See docs/backups.md.
{ config, ... }:
let
  secrets = config.sops.secrets;
  pbs = config.services.proxmox-backup-server.package;
in
{
  imports = [
    ../../modules/proxmox-lxc.nix
    ../../modules/pbs.nix
  ];

  networking.hostName = "pbs-primary";

  forge.pbs = {
    datastore = "primary";
    pruneSchedule = "13:00";
    users = {
      # PVE's storage entry. It backs up and restores its own groups, and can
      # neither prune nor forget them: retention is the prune job's alone.
      vzdump = {
        role = "DatastoreBackup";
        path = "/datastore/primary";
      };
      # PBS #2's pull, its protection mirror and its freshness check.
      sync = {
        role = "DatastoreReader";
        path = "/datastore/primary";
      };
    };
  };

  sops = {
    defaultSopsFile = ./secrets.sops.yaml;
    secrets."vm107/token-secret".restartUnits = [ "pbs-remote-vm107.service" ];
  };

  services.proxmox-backup-server.ensureSyncJobs.migrate-vm107 = {
    datastore = "primary";
    settings = {
      remote = "vm107";
      remote-store = "primary";
      # PVE's user, so after the cutover vzdump goes on appending to the
      # groups this copies.
      owner = "vzdump@pbs";
    };
  };

  # It serves PBS #2's pull and opens no tailnet connection of its own, so it
  # needs no tun device.
  services.tailscale.interfaceName = "userspace-networking";

  # The remote for the migration from VM 107, which `migrate-vm107` copies
  # when run by hand with `proxmox-backup-manager sync-job run migrate-vm107`.
  # Nothing deletes what is no longer declared: at the cutover, removing these
  # also takes `sync-job remove migrate-vm107` and `remote remove vm107`.
  systemd.services.pbs-remote-vm107 = {
    description = "Reconcile PBS's remote for VM 107";
    wantedBy = [ "multi-user.target" ];
    after = [ "proxmox-backup-proxy.service" ];
    requires = [ "proxmox-backup-proxy.service" ];
    before = [ "proxmox-backup-setup.service" ];
    path = [ pbs ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    # The secret passes through argv, as in pbs-notifications.
    script = ''
      args=(
        --host 192.168.0.107
        --auth-id 'sync@pbs!migration'
        --fingerprint f1:ef:ab:fa:c2:79:fe:64:b1:84:c6:fd:d0:d1:d1:44:a8:a8:a2:9f:02:b8:68:62:aa:dc:74:a0:7e:5f:9f:9b
        --password "$(cat ${secrets."vm107/token-secret".path})"
      )
      if proxmox-backup-manager remote show vm107 >/dev/null 2>&1; then
        proxmox-backup-manager remote update vm107 "''${args[@]}"
      else
        proxmox-backup-manager remote create vm107 "''${args[@]}"
      fi
    '';
  };

  # The release this host was first built on. Never change it.
  system.stateVersion = "26.11";
}
