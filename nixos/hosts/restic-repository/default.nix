# The restic repository host: rest-server, append-only, for the cluster, and
# restic's own jobs against the same repository on the local filesystem. See
# docs/backups.md.
{
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

  # Reports a unit's outcome to healthchecks.io from ExecStopPost. The contract
  # is ansible/templates/hc-report.j2's, kept in step with that and the mini-nas
  # copy by hand, since this host can import neither. See docs/backups.md.
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

  # The leading "-" keeps a failed ping from failing the job it reports on.
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

  # These three, or the container has no network. See docs/nixos.md.
  systemd.network.enable = true;
  networking.useHostResolvConf = false;
  services.resolved.enable = false;

  systemd.network.networks."10-eth0" = {
    matchConfig.Name = "eth0";
    address = [ "192.168.0.108/24" ];
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
      # colmena deploys as root; there is no other account.
      PermitRootLogin = "prohibit-password";
    };
  };

  sops = {
    defaultSopsFile = ./secrets.sops.yaml;
    # The host key, a recipient in .sops.yaml, so nothing need be copied here.
    age.sshKeyPaths = [ "/etc/ssh/ssh_host_ed25519_key" ];
    secrets = {
      "restic/password".owner = "restic";
      "rest-server/htpasswd".owner = "restic";
      # The htpasswd's credential in the clear, so this host can test its own
      # append-only boundary. Anything that reads it can read the passphrase.
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

  # The module does not open its own port. Without this the cluster's Jobs time
  # out while a test from inside the container passes.
  networking.firewall.allowedTCPPorts = [ 8000 ];

  # For maintenance by hand: check, tag, restore.
  environment.systemPackages = [ pkgs.restic ];

  # Bind-mounted from rpool/storage; terraform/main.tf says why they are safe
  # though writable.
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
    # No pruneOpts, or the module prunes after every backup, and every prune is
    # a rewrite mini-nas receives and pins for 60 days. Retention is monthly,
    # below.
  };

  # A list, so it joins the module's own postStop cleanup rather than replacing it.
  systemd.services.restic-backups-massfiles.serviceConfig.ExecStopPost = [
    (report "restic-massfiles")
  ];

  # Retention for the whole repository, grouped by host and path.
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
      ${pkgs.restic}/bin/restic forget --prune \
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
