# PBS #2: the offsite copy of the primary's guest images, pulled over the
# tailnet. A NixOS container on mini-nas, which declares it and deploys it; this
# is its whole system. See docs/backups.md.
{
  config,
  lib,
  pkgs,
  ...
}:
let
  secrets = config.sops.secrets;
  pbs = config.services.proxmox-backup-server.package;

  pbsOffsite = pkgs.writers.writePython3Bin "pbs-offsite" {
    # ruff-format's line length and line-break style, which flake8 disputes.
    flakeIgnore = [
      "E501"
      "W503"
    ];
  } (builtins.readFile ./pbs_offsite.py);

  pushover = pkgs.writeShellScript "pushover" ''
    set -eu
    # Credentials through --config, not argv.
    ${pkgs.curl}/bin/curl -fsS -m 10 --retry 3 -o /dev/null \
      --form-string "title=$1" --form-string "message=$2" --config - <<EOF
    url = "https://api.pushover.net/1/messages.json"
    form-string = "token=$(cat ${secrets."pushover/token".path})"
    form-string = "user=$(cat ${secrets."pushover/user-key".path})"
    EOF
  '';
in
{
  imports = [ ../../modules/pbs.nix ];

  boot.isContainer = true;

  networking = {
    hostName = "pbs-offsite";
    useDHCP = false;
    # A container defaults to the host's resolv.conf. resolved takes DHCP's
    # resolvers and Tailscale's MagicDNS together instead.
    useHostResolvConf = false;
  };
  services.resolved.enable = true;
  systemd.network = {
    enable = true;
    networks."10-eth0" = {
      matchConfig.Name = "eth0";
      networkConfig.DHCP = "yes";
      linkConfig.RequiredForOnline = "routable";
    };
  };

  # For running the mirror or the freshness check by hand, which the units'
  # own PATH does not reach.
  environment.systemPackages = [ pbsOffsite ];

  forge.pbs = {
    datastore = "offsite";
    # Run by pbs-offsite-daily, after the sync and the mirror.
    pruneSchedule = null;
  };

  sops = {
    defaultSopsFile = ./secrets.sops.yaml;
    secrets = {
      "primary/token-secret".restartUnits = [ "pbs-remote.service" ];
      "healthchecks/pbs-offsite-freshness" = { };
    };
  };

  # The remote, which the module does not manage. Its token holds
  # DatastoreReader on the primary and nothing else, so nothing here can change
  # the primary; and the primary holds no credential for this store.
  systemd.services.pbs-remote = {
    description = "Reconcile PBS's remote for the primary";
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
        --host pbs-primary.forge.local
        --auth-id 'sync@pbs!pbs-offsite'
        --fingerprint f1:ef:ab:fa:c2:79:fe:64:b1:84:c6:fd:d0:d1:d1:44:a8:a8:a2:9f:02:b8:68:62:aa:dc:74:a0:7e:5f:9f:9b
        --password "$(cat ${secrets."primary/token-secret".path})"
      )
      if proxmox-backup-manager remote show primary >/dev/null 2>&1; then
        proxmox-backup-manager remote update primary "''${args[@]}"
      else
        proxmox-backup-manager remote create primary "''${args[@]}"
      fi
    '';
  };

  services.proxmox-backup-server.ensureSyncJobs.pull-primary = {
    datastore = "offsite";
    # No schedule: pbs-offsite-daily runs it, ahead of the mirror and prune.
    settings = {
      remote = "primary";
      remote-store = "primary";
      # Apart from anything mini-nas's own PVE might write here.
      ns = "vulcanus";
      # Nothing deleted on the primary reaches this store. See docs/backups.md.
      remove-vanished = false;
    };
  };

  # Sequenced rather than scheduled in order: a snapshot protected on the
  # primary but already past keep-last here would otherwise be pruned before
  # its flag arrived. A failure stops the chain, so nothing is pruned on a day
  # the flags could not be read. PBS notifies on a failed sync or prune itself;
  # the mirror is not PBS's, so it notifies here.
  systemd.services.pbs-offsite-daily = {
    description = "Pull from the primary, mirror its protection, then prune";
    after = [
      "network-online.target"
      "proxmox-backup-setup.service"
      "pbs-remote.service"
    ];
    wants = [ "network-online.target" ];
    path = [
      pbs
      pbsOffsite
    ];
    serviceConfig.Type = "oneshot";
    script = ''
      set -eu
      proxmox-backup-manager sync-job run pull-primary
      if ! pbs-offsite mirror ${secrets."primary/token-secret".path}; then
        ${pushover} "PBS #2: protection mirror failed" \
          "Nothing was pruned. See journalctl -u pbs-offsite-daily on pbs-offsite."
        exit 1
      fi
      proxmox-backup-manager prune-job run keep-last-31
    '';
  };

  systemd.timers.pbs-offsite-daily = {
    wantedBy = [ "timers.target" ];
    # Clear of vzdump's window, which ends by 11:35 UTC in winter.
    timerConfig = {
      OnCalendar = "*-*-* 12:00:00";
      Persistent = true;
    };
  };

  systemd.services.pbs-offsite-freshness = {
    description = "Assert PBS #2 holds and has verified the primary's snapshots";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    path = [ pbs ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = lib.escapeShellArgs [
        "${pbsOffsite}/bin/pbs-offsite"
        "freshness"
        secrets."primary/token-secret".path
        secrets."healthchecks/pbs-offsite-freshness".path
      ];
    };
  };

  systemd.timers.pbs-offsite-freshness = {
    wantedBy = [ "timers.target" ];
    # Six hours after the sync starts, so an ordinary day's has finished.
    timerConfig = {
      OnCalendar = "*-*-* 18:00:00";
      Persistent = true;
    };
  };

  # The release this host was first built on. Never change it.
  system.stateVersion = "26.11";
}
