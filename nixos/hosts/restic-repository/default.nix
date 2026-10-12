{
  address,
  config,
  inputs,
  modulesPath,
  pkgs,
  ...
}:
let
  # The local path, never rest-server: append-only refuses a prune's deletes.
  repository = "/srv/restic";
  passwordFile = config.sops.secrets."restic/password".path;

  hcReport = pkgs.writeShellScript "hc-report" ''
    set -eu
    url="$(cat "$1")"
    case "$url" in
      https://*) ;;
      *)
        echo "hc-report: $1 holds no https:// URL" >&2
        exit 1
        ;;
    esac
    case "''${SERVICE_RESULT:-}" in
      success) exec ${pkgs.curl}/bin/curl -fsS -g -m 10 --retry 3 -o /dev/null "$url" ;;
      *) exec ${pkgs.curl}/bin/curl -fsS -g -m 10 --retry 3 -o /dev/null "$url/fail" ;;
    esac
  '';

  # The leading "-" keeps a failed ping from failing the backup it reports on: a
  # ping that never arrives turns its check red by itself.
  report = check: "-${hcReport} ${config.sops.secrets."healthchecks/${check}".path}";
in
{
  imports = [
    # Its two `manage*` defaults are overridden below.
    "${modulesPath}/virtualisation/proxmox-lxc.nix"
    inputs.sops-nix.nixosModules.sops
  ];

  # Proxmox writes no network into an `unmanaged` container, and sets only the
  # hostname, at creation: hostName below must match terraform/main.tf's. See
  # docs/nixos.md.
  proxmoxLXC = {
    manageNetwork = true;
    manageHostName = true;
  };

  networking = {
    hostName = "restic-repository";
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

  # Like vulcanus, not UTC like the cluster. See docs/nixos.md.
  time.timeZone = "America/Denver";

  # The same key `terraform/main.tf` hands every other container and VM.
  users.users.root.openssh.authorizedKeys.keys = [
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIyPkfTI0io9dZsJstcf29tddyrsHr9bnM8UXKtaVJwm"
  ];

  services.openssh = {
    enable = true;
    settings = {
      PasswordAuthentication = false;
      KbdInteractiveAuthentication = false;
    };
  };

  sops = {
    defaultSopsFile = ./secrets.sops.yaml;
    age.sshKeyPaths = [ "/etc/ssh/ssh_host_ed25519_key" ];
    secrets = {
      "restic/password".owner = "restic";
      "rest-server/htpasswd".owner = "restic";
      # The htpasswd's credential in the clear, for testing the append-only
      # boundary by hand.
      "rest-server/password".owner = "restic";
      # Read by the report above, which runs as the unit's own user.
      "healthchecks/restic-massfiles".owner = "restic";
      "healthchecks/restic-prune".owner = "restic";
    };
  };

  services.restic.server = {
    enable = true;

    # The cluster is treated as a hostile writer. See docs/backups.md.
    appendOnly = true;

    # The bind-mounted rpool/backups/restic dataset.
    dataDir = "/srv/restic";

    # A port only; see docs/nixos.md.
    listenAddress = "8000";

    htpasswd-file = config.sops.secrets."rest-server/htpasswd".path;

    # Repository size and blob counts, scraped alongside everything else.
    prometheus = true;
  };

  # The restic service module does not open its own port.
  networking.firewall.allowedTCPPorts = [ 8000 ];

  # For maintenance by hand: check, tag, restore.
  environment.systemPackages = [ pkgs.restic ];

  services.restic.backups.massfiles = {
    inherit repository passwordFile;
    user = "restic";
    paths = [
      "/srv/storage/photos"
      "/srv/storage/books"
      "/srv/storage/filesync"
    ];
    # Between the cluster's backups and vzdump; see docs/backups.md.
    timerConfig = {
      OnCalendar = "*-*-* 02:00:00";
      Persistent = true;
    };
  };

  # A list, so it joins the module's own postStop cleanup rather than replacing it.
  systemd.services.restic-backups-massfiles.serviceConfig.ExecStopPost = [
    (report "restic-massfiles")
  ];

  # Retention for the whole repository.
  systemd.services.restic-prune = {
    description = "Apply the restic repository's retention policy";
    environment = {
      RESTIC_REPOSITORY = repository;
      RESTIC_PASSWORD_FILE = passwordFile;
      RESTIC_CACHE_DIR = "/var/cache/restic-prune";
    };
    serviceConfig = {
      Type = "oneshot";
      User = "restic";
      Group = "restic";
      CacheDirectory = "restic-prune";
      CacheDirectoryMode = "0700";
      ExecStopPost = [ (report "restic-prune") ];
    };
    # See docs/backups.md. --max-repack-size bounds what one run rewrites, and
    # so the next syncoid send and what mini-nas's snapshots pin; garbage beyond
    # it waits a month. A month's prune repacks about 200 MiB, so 20G binds only
    # after a large deletion.
    script = ''
      ${pkgs.restic}/bin/restic forget --prune --group-by host,paths \
        --keep-last 10 --keep-hourly 24 --keep-daily 30 \
        --keep-weekly 8 --keep-monthly 24 \
        --keep-tag decommissioned \
        --max-repack-size 20G
    '';
  };

  systemd.timers.restic-prune = {
    wantedBy = [ "timers.target" ];
    # Monthly, since every prune costs a ZFS send, and between the cluster's
    # backups, since it holds the exclusive lock. See docs/backups.md.
    timerConfig = {
      OnCalendar = "*-*-01 03:00:00";
      Persistent = true;
    };
  };

  # The release this host was first built on. Never change it.
  system.stateVersion = "26.11";
}
