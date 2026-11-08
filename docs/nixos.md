# NixOS hosts

What runs NixOS, how it is built and deployed, and the traps of running it in a
Proxmox container. The other containers are managed by Ansible.

| Host | Role | Where |
|---|---|---|
| `restic-repository` | the restic repository: `rest-server --append-only` and two local restic jobs. See [`backups.md`](backups.md) | LXC 108 on vulcanus, `192.168.0.108` |
| `pbs-offsite` | PBS #2, the offsite copy of the guest images. See [`backups.md`](backups.md) | a NixOS container on mini-nas, DHCP on its LAN; `100.64.0.10` on the tailnet |

## Layout

Everything is in `nixos/`:

- **`flake.nix`** lists each colmena host once, with its module and address. colmena
  deploys from it, and builds each host's LXC template from the same module. A host
  that another machine runs, as mini-nas runs PBS #2, is a `nixosConfigurations`
  output instead.
- **`hosts/<name>/default.nix`** is the host.
- **`hosts/<name>/secrets.sops.yaml`** holds its secrets, for sops-nix.
- **`modules/`** holds what more than one host shares: `pbs.nix` is PBS as both PBS
  hosts run it, the role's own module adding the rest.

**nixos-pbs is pinned to a revision** in the input's URL, so `nix flake update` cannot
move it: one maintainer and young, it moves only when its URL is edited, after reading
what changed. Its nixpkgs is left its own rather than following the flake's, so its
packages are the builds its binary cache holds, rather than ones built here.

## Deploying a change

```bash
colmena apply --on <host>
```

From the devenv shell, anywhere in the repository.

Then check the change took effect *inside* the host: a unit's state, a file, a
listening port. A successful apply is not that check. From off the LAN, SSH needs the
host's grant in the tailnet policy; see [`tailnet.md`](tailnet.md).

### PBS #2

mini-nas's flake takes this one as its `vulcanus-proxmox` input and runs
`nixosConfigurations.pbs-offsite` as `containers.pbs-offsite`, a systemd-nspawn
container, so a change reaches it in three steps:

1. Merge it to `main` here: `wt deploy`.
2. In `~/repositories/mini-nas`, `nix flake update vulcanus-proxmox`, and commit the
   lock.
3. Deploy mini-nas from that checkout:
   ```bash
   nix run --inputs-from . deploy-rs -- .#mini-nas --hostname 192.168.1.11 \
     --confirm-timeout 60 --skip-checks
   ```

A change to the container restarts it, which aborts a running sync or verify: deploy
clear of PBS #2's 12:00 UTC chain and Sunday's verify, and see
[`backups.md`](backups.md) for checking that no task is running. Its commands run from
mini-nas with `nixos-container run pbs-offsite -- <command>`.

PBS #2's secrets follow *Creating a host*'s step 4, its host key being the container's
own: it lives on the container's root filesystem, so a deploy keeps it and only a
recreated container needs its recipient replaced.

## Creating a host

1. Write `hosts/<name>/default.nix` and add it, with its address, to `hosts` in
   `flake.nix`.
2. Build its template with `nix build ./nixos#<name>`. Upload it to vulcanus as
   `local:vztmpl/nixos-<name>.tar.xz`, a stable name: the build's own filename
   carries the nixpkgs revision, and Terraform would read every flake update as a
   new template.
3. Create the container in `terraform/main.tf`: `ostype = "unmanaged"`,
   unprivileged, a `hostname` matching `networking.hostName`, and
   `lifecycle.ignore_changes = [ostemplate]`. Terraform creates it once; colmena
   owns it afterwards.
4. Once it has booted, give it its secrets: nothing can be encrypted to a host
   before its host key exists. Derive its age recipient on the host with
   `ssh-to-age -i /etc/ssh/ssh_host_ed25519_key.pub`, add it to the host's rule in
   `.sops.yaml`, run `sops updatekeys` on its secrets file, then deploy.

## Secrets

sops-nix decrypts at activation with an age identity derived from the host's SSH
host key. **The host's recipient must be in age form**, from `ssh-to-age`, not the
`ssh-ed25519` key: sops writes an `ssh-ed25519` stanza for an SSH recipient, which
the derived identity cannot open, and activation fails with "0 successful groups".

The NixOS rule comes first in `.sops.yaml`, since sops takes the first rule that
matches and the Kubernetes rule would encrypt only `data`/`stringData`, which a
sops-nix file does not have. Every file is also encrypted to the humans' keys, so a
rebuilt host, with a new host key, can have its file re-encrypted to it.

## NixOS in a Proxmox container

All three networking traps present as *the container simply has no network*:

- **Proxmox writes no network configuration into an `unmanaged` container**, which
  is what a NixOS container is. At the module's defaults `eth0` comes up with no
  address and nothing ever fixes it.
- **`proxmoxLXC.manageNetwork = true` turns the module's networking off rather than
  handing it over.** Set `systemd.network.enable` too, or a
  `systemd.network.networks` entry is inert.
- **systemd-networkd pulls in systemd-resolved**, which asserts against the
  `networking.useHostResolvConf` every container defaults to. Set it false and
  disable resolved; nothing here needs a caching resolver.

**The hostname comes from Proxmox**, from `--hostname` at creation, and a switch does
not rename the running container. So set `manageHostName = true` and a
`networking.hostName` matching what Terraform creates; left false, the module forces
the name to `""`.

**The firewall is on.** A service's port stays closed until it is listed in
`networking.firewall.allowedTCPPorts`, as rest-server's 8000 is.

**`services.restic.server.listenAddress` takes a port only.** The module uses socket
activation, and a `host:port` value trips an assertion.

**Hosts keep `America/Denver`**, like vulcanus, so a timer means what the
hypervisor's logs mean. Kubernetes schedules are UTC; [`backups.md`](backups.md) lists
both side by side. **PBS #2 is the exception**, on UTC, for the reason `backups.md`
gives under its section.
