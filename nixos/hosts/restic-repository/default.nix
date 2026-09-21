# The restic repository host: an unprivileged Proxmox LXC on vulcanus serving
# `rest-server --append-only` to the cluster, and running restic itself against
# the same repository over the local filesystem.
#
# See docs/backups.md for why the repository is a ZFS dataset on a container
# rather than a zvol on a VM, and why retention runs here rather than in the
# cluster.
{
  config,
  inputs,
  modulesPath,
  pkgs,
  ...
}:
let
  # Both jobs below reach the repository through the local filesystem, never
  # through rest-server: append-only refuses the deletes a prune needs, and a
  # local read is cheaper for the backup too.
  repository = "/srv/restic";
  passwordFile = config.sops.secrets."restic/password".path;

  # Reports a unit's real outcome to healthchecks.io from ExecStopPost, where
  # systemd has set $SERVICE_RESULT because the job has actually exited. The
  # contract is ansible/templates/hc-report.j2's, and it is kept in step with that
  # and with the mini-nas copy by hand -- three copies, since this host cannot
  # import either. See docs/backups.md for why ExecStartPost is never used.
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
    # Drops the bootloader and switches on systemd-networkd. Its two `manage*`
    # defaults are overridden below.
    "${modulesPath}/virtualisation/proxmox-lxc.nix"
    inputs.sops-nix.nixosModules.sops
  ];

  # Both default to false, which hands the hostname and the address to Proxmox.
  # Proxmox writes neither into a container whose `ostype` is `unmanaged`, which
  # is what a NixOS container is created as -- so left at the defaults the
  # interface never comes up at all, and `networking.hostName` below is forced
  # to "" behind your back, which also names the system derivation "unnamed".
  #
  # The hostname is the one thing Proxmox really does set, from `--hostname` at
  # container creation, and it wins: a switch writes /etc/hostname but does not
  # rename the running UTS namespace. So `networking.hostName` below must match
  # the hostname `terraform/main.tf` creates the container with, or the two
  # disagree until the next restart.
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

  # Required explicitly: `manageNetwork` above turns the LXC module's own
  # networking block off entirely rather than handing it over, so without this
  # there is no networkd and the `systemd.network` entry below is inert.
  systemd.network.enable = true;

  # Enabling networkd pulls in systemd-resolved, which asserts against the
  # `useHostResolvConf` that every container config defaults to on. Nothing
  # here needs a caching resolver: one nameserver, written once by resolvconf.
  networking.useHostResolvConf = false;
  services.resolved.enable = false;

  systemd.network.networks."10-eth0" = {
    matchConfig.Name = "eth0";
    address = [ "192.168.0.108/24" ];
    routes = [ { Gateway = "192.168.0.1"; } ];
    linkConfig.RequiredForOnline = "routable";
  };

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

  sops = {
    defaultSopsFile = ./secrets.sops.yaml;
    # The container's own host key, which is also a recipient in .sops.yaml.
    # Nothing has to be copied onto the machine for it to decrypt at activation.
    age.sshKeyPaths = [ "/etc/ssh/ssh_host_ed25519_key" ];
    secrets = {
      "restic/password".owner = "restic";
      "rest-server/htpasswd".owner = "restic";
      # The same credential as the bcrypt above, in the clear. It is what lets
      # this host check its own append-only boundary -- that `forget` through
      # the served URL is refused while the same command against the local path
      # succeeds. No exposure it does not already have: anything that can read
      # this directory already holds the repository passphrase beside it.
      "rest-server/password".owner = "restic";
      # Read by the report above, which runs as the unit's own user.
      "healthchecks/restic-massfiles".owner = "restic";
      "healthchecks/restic-prune".owner = "restic";
    };
  };

  services.restic.server = {
    enable = true;

    # The cluster is a hostile writer by design: K8up holds credentials that a
    # compromised or misbehaving workload could use to erase its own history.
    # Append-only is what stops that, and it is why retention cannot run
    # through this server -- `forget` needs delete access, so it runs as a
    # local-filesystem client on this host instead. See docs/backups.md.
    appendOnly = true;

    # The bind-mounted rpool/backups/restic dataset. A dataset rather than a
    # zvol so mini-nas can mount the replica read-only and verify it natively.
    dataDir = "/srv/restic";

    # A port, not an address: the unit is socket-activated and asserts against
    # anything starting with a colon.
    listenAddress = "8000";

    htpasswd-file = config.sops.secrets."rest-server/htpasswd".path;

    # Repository size and blob counts, scraped alongside everything else.
    prometheus = true;
  };

  # restic itself, for the repository's own maintenance: init, check, and the
  # retention pass that append-only refuses to serve.
  environment.systemPackages = [ pkgs.restic ];

  # The mass-file datasets, bind-mounted from rpool/storage. See
  # terraform/main.tf for why the mounts are writable in principle and safe in
  # practice.
  services.restic.backups.massfiles = {
    inherit repository passwordFile;
    user = "restic";
    paths = [
      "/srv/storage/photos"
      "/srv/storage/books"
      "/srv/storage/filesync"
    ];
    # 02:00 here is 08:00 UTC in summer: clear of the cluster's 01:00 UTC
    # backups and finished well before vzdump at 04:00. docs/backups.md carries
    # the schedule in UTC.
    timerConfig = {
      OnCalendar = "*-*-* 02:00:00";
      Persistent = true;
    };
    # No pruneOpts. The module runs `forget --prune` after every backup when it
    # has them, and a prune rewrites pack files, so mini-nas would receive the
    # rewrite in the next hourly send and pin it for 60 days. Retention is the
    # monthly job below instead.
  };

  # A list, so it joins the module's own postStop cleanup rather than replacing it.
  systemd.services.restic-backups-massfiles.serviceConfig.ExecStopPost = [
    (report "restic-massfiles")
  ];

  # Retention for the whole repository -- the cluster's snapshots and the ones
  # above alike, grouped by host and path as restic does by default.
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
    # The policy in todos/backups.md's retention table. --keep-tag keeps the
    # final snapshot of a decommissioned workload past the normal window, since
    # months later is exactly when it is wanted.
    #
    # --max-repack-size bounds what one run rewrites, and with it the next
    # syncoid send and what mini-nas's snapshots pin; garbage beyond it waits a
    # month. 20G is a first figure, not a measured one: revisit once the first
    # few prunes report how much they left behind.
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
    # Monthly, not weekly: every prune costs a large ZFS send. 03:00 on the 1st
    # is 09:00 UTC, between the cluster's backups rather than inside them, since
    # a prune holds the repository's exclusive lock for its whole run.
    timerConfig = {
      OnCalendar = "*-*-01 03:00:00";
      Persistent = true;
    };
  };

  # Never changes once set. It is the release this host was first built on, not
  # the one it currently runs.
  system.stateVersion = "26.11";
}
