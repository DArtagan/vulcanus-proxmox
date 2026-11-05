# Proxmox Backup Server, as both PBS hosts run it: the primary and PBS #2 differ
# only in their role's own module. See docs/backups.md.
{
  config,
  inputs,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.forge.pbs;
  pbs = config.services.proxmox-backup-server.package;
  secrets = config.sops.secrets;

  # Pushover's JSON API, filled from the endpoint's own secrets at send time.
  # Written out rather than built with toJSON: each value has to be the `json`
  # helper's output, unquoted, or a message carrying a quote or a newline
  # renders as invalid JSON.
  pushoverBody = ''{"token":{{ json secrets.token }},"user":{{ json secrets.user }},"title":{{ json title }},"message":{{ json message }}}'';
in
{
  imports = [
    inputs.nixos-pbs.nixosModules.proxmox-backup-server
    inputs.sops-nix.nixosModules.sops
  ];

  options.forge.pbs = {
    datastore = lib.mkOption {
      type = lib.types.str;
      description = "The datastore's name, which PVE's storage entry and remotes refer to.";
    };

    pruneSchedule = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      description = ''
        When the prune job runs, or null for a job that is run only by the role's
        own sequence, as PBS #2's is after its sync.
      '';
    };
  };

  config = {
    services.proxmox-backup-server = {
      enable = true;
      # Built against nixos-pbs's own nixpkgs, so it is the build its cache holds.
      package = inputs.nixos-pbs.packages.${pkgs.stdenv.hostPlatform.system}.proxmox-backup-server-fhs;
      openFirewall = true;

      ensureDatastores.${cfg.datastore} = {
        # The bind-mounted dataset. PBS refuses a non-empty directory at
        # creation, and the path cannot change afterwards.
        path = "/mnt/pbs";
        gcSchedule = "sat 14:00";
      };

      # keep-last counts snapshots rather than age, so a retired guest's group
      # is never pruned away; it is kept until forgotten by hand.
      ensurePruneJobs.keep-last-31 = {
        inherit (cfg) datastore;
        # PBS requires a schedule even on a disabled job, and `prune-job run`
        # ignores `disable`.
        schedule = if cfg.pruneSchedule == null then "daily" else cfg.pruneSchedule;
        disable = cfg.pruneSchedule == null;
        settings.keep-last = 31;
      };

      ensureVerifyJobs.weekly = {
        inherit (cfg) datastore;
        schedule = "sun 14:00";
        settings = {
          ignore-verified = true;
          outdated-after = 30;
        };
      };
    };

    # Anything reconciling through PBS's CLI waits for the proxy, not only the
    # API daemon nixos-pbs orders it after: `connect_to_localhost` dials 8007,
    # which the proxy serves, so a boot that starts the proxy a second late
    # fails the reconcile with `client error (Connect)`.
    systemd.services.proxmox-backup-setup = {
      after = [ "proxmox-backup-proxy.service" ];
      requires = [ "proxmox-backup-proxy.service" ];
    };

    # proxmox-daily-update checks a subscription, renews ACME certificates and
    # runs apt: there is no subscription, no ACME domain and no apt, so all it
    # does is leave a failed aptupdate task in the GUI every day.
    systemd.timers.proxmox-backup-daily-update.enable = false;

    # Pinned, because it owns the datastore on the host: the dataset is chowned
    # to it there.
    users.users.proxmox-backup-server.uid = 350;
    users.groups.proxmox-backup-server.gid = 350;

    sops = {
      age.sshKeyPaths = [ "/etc/ssh/ssh_host_ed25519_key" ];
      secrets = {
        "pushover/token".restartUnits = [ "pbs-notifications.service" ];
        "pushover/user-key".restartUnits = [ "pbs-notifications.service" ];
        "gui/root-password".restartUnits = [ "pbs-gui-user.service" ];
      };
    };

    # Every error PBS raises -- sync, prune, verify, GC -- goes to Pushover. The
    # module manages datastores and jobs but not notification targets.
    systemd.services.pbs-notifications = {
      description = "Reconcile PBS's Pushover notification target";
      wantedBy = [ "multi-user.target" ];
      after = [ "proxmox-backup-proxy.service" ];
      requires = [ "proxmox-backup-proxy.service" ];
      path = [
        pbs
        pkgs.coreutils
      ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };
      # The secrets pass through argv, which every process here could read. Only
      # PBS's own daemons and Tailscale run in this container.
      script = ''
        b64() { base64 -w0; }
        args=(
          --url https://api.pushover.net/1/messages.json
          --method post
          --header "name=Content-Type,value=$(printf application/json | b64)"
          --body "$(printf '%s' ${lib.escapeShellArg pushoverBody} | b64)"
          --secret "name=token,value=$(b64 < ${secrets."pushover/token".path})"
          --secret "name=user,value=$(b64 < ${secrets."pushover/user-key".path})"
        )
        if proxmox-backup-manager notification endpoint webhook show pushover >/dev/null 2>&1; then
          proxmox-backup-manager notification endpoint webhook update pushover "''${args[@]}"
        else
          proxmox-backup-manager notification endpoint webhook create pushover "''${args[@]}"
        fi

        args=(--target pushover --match-severity error)
        if proxmox-backup-manager notification matcher show errors >/dev/null 2>&1; then
          proxmox-backup-manager notification matcher update errors "''${args[@]}"
        else
          proxmox-backup-manager notification matcher create errors "''${args[@]}"
        fi
      '';
    };

    # The GUI login, in PBS's own realm rather than PAM. `root@pam` cannot serve
    # it: NixOS leaves root's password locked, so PAM has nothing to check, and
    # giving the account one to satisfy a web form widens what a password reaches.
    # The module manages datastores and jobs but not users.
    systemd.services.pbs-gui-user = {
      description = "Reconcile PBS's root@pbs login";
      wantedBy = [ "multi-user.target" ];
      after = [ "proxmox-backup-proxy.service" ];
      requires = [ "proxmox-backup-proxy.service" ];
      path = [
        pbs
        pkgs.coreutils
        pkgs.gnugrep
      ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };
      # The password passes through argv, as the notification secrets do: only
      # PBS's own daemons and Tailscale run in this container.
      script = ''
        if ! proxmox-backup-manager user list --output-format json | grep -q '"root@pbs"'; then
          proxmox-backup-manager user create root@pbs
        fi
        # `user update --password` is a no-op upstream -- update_user discards the
        # field and says to come here -- so the password always goes through this
        # API, which is what writes the shadow store.
        password=$(cat ${secrets."gui/root-password".path})
        proxmox-backup-debug api set /access/password \
          --userid root@pbs --password "$password"
        proxmox-backup-manager acl update / Admin --auth-id root@pbs
      '';
    };

    # Registered by hand, once, there being no pre-auth key to automate it with
    # (docs/tailnet.md says why). The node's name is load-bearing: PBS #2's remote
    # reaches the primary by it, so it is set here rather than left to the
    # container's hostname, and the tag is applied server-side because advertising
    # one forces re-authentication.
    #
    #   tailscale up --login-server https://headscale.immortalkeep.com \
    #     --hostname <pbs-primary|pbs-offsite>
    #   headscale nodes register --user will --key <the key it prints>
    #   headscale nodes tag -i <id> -t tag:<pbs-primary|pbs-offsite>
    services.tailscale.enable = true;

    # Schedules are stated in UTC across the estate's four clocks, and PBS's
    # calendar events are in local time. See docs/backups.md.
    time.timeZone = "UTC";

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
  };
}
