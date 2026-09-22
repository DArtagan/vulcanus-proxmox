# NixOS hosts

What runs NixOS, how it is built and deployed, and the traps specific to running it
in a Proxmox container. The other containers are managed by Ansible.

| Host | Role | Where |
|---|---|---|
| `restic-repository` | the restic repository: `rest-server --append-only` and two local restic jobs. See [`backups.md`](backups.md) | LXC 108 on vulcanus, `192.168.0.108` |

## Layout

Everything is in `nixos/`:

- **`flake.nix`** holds one `hosts` attrset. Each entry feeds three outputs from
  the same module: the colmena node, `nixosConfigurations`, and the LXC template
  under `packages`. They must not diverge. A template built from different modules
  than the deployment expects is how a host becomes unmanageable the first time
  anyone tries to change it.
- **`hosts/<name>/default.nix`** is the host.
- **`hosts/<name>/secrets.sops.yaml`** holds its secrets, for sops-nix.

colmena comes from its upstream flake, not from nixpkgs, which still ships 0.4.0
from May 2023. `flake.lock` is the pin, so it moves only when someone runs
`nix flake update`. That colmena evaluates the flake's **`colmenaHive`** output and
errors if only `colmena` is present, so the flake has both.

## Deploying a change

```bash
cd nixos && nix develop --command colmena apply --on <host>
```

Then check the change took effect *inside* the host: a unit's state, a file, a
listening port. A successful apply is not that check. From off the LAN, SSH to the
host needs its grant in the tailnet policy. See [`tailnet.md`](tailnet.md).

## Creating a host

1. Write `hosts/<name>/default.nix` and add it to `hosts` in `flake.nix`.
2. Build its template with `nix build ./nixos#<name>`. Upload it to vulcanus as
   `local:vztmpl/nixos-<name>.tar.xz`, a stable name: the build's own filename
   carries the nixpkgs revision, and Terraform would read every flake update as a
   new template.
3. Create the container in `terraform/main.tf`: `ostype = "unmanaged"`,
   unprivileged, a `hostname` matching `networking.hostName`, and
   `lifecycle.ignore_changes = [ostemplate]`. Terraform creates it once; colmena
   owns it afterwards.
4. Once it has booted, give it its secrets. Derive its age recipient on the host
   with `ssh-to-age -i /etc/ssh/ssh_host_ed25519_key.pub`. Add it to the host's rule
   in `.sops.yaml`, run `sops updatekeys` on its secrets file, then deploy. Nothing
   can be encrypted to a host before its host key exists, so this step always
   follows the first boot.

## Secrets

sops-nix decrypts at activation using an age identity it derives from the host's SSH
host key. **The host's recipient must be written in age form**, from `ssh-to-age`, not
as the `ssh-ed25519` key. sops writes an `ssh-ed25519` stanza for an SSH recipient,
but sops-install-secrets imports a native age identity, which opens only an `X25519`
stanza. The result is "0 successful groups" at activation.

The NixOS rule comes first in `.sops.yaml`, since sops takes the first rule that
matches. It carries no `encrypted_regex`, because a sops-nix file has no
`data`/`stringData` to restrict it to. Every file is also encrypted to the humans'
keys and to Flux's, so any one private half still opens every file. Adding a host
must not silently narrow that.

## NixOS in a Proxmox container

All three networking traps present as *the container simply has no network*:

- **Proxmox writes no network configuration into an `unmanaged` container**, which
  is what a NixOS container is. At the module's defaults `eth0` comes up with no
  address and nothing ever fixes it.
- **`proxmoxLXC.manageNetwork = true` turns the module's networking off; it does
  not hand networking over.** `systemd.network.enable` must be set explicitly, or a
  `systemd.network.networks` entry is inert.
- **systemd-networkd pulls in systemd-resolved**, which asserts against the
  `networking.useHostResolvConf` every container defaults to. So set
  `useHostResolvConf = false`, and disable resolved: nothing here needs a caching
  resolver.

**The hostname comes from Proxmox**, from `--hostname` at creation, and a switch does
not rename the running container. So `networking.hostName` must match what Terraform
creates it with, and `manageHostName = true`. Left false, the module forces the name
to `""`.

**The firewall is on by default.** A service's port stays closed until it is listed
in `networking.firewall.allowedTCPPorts`, as rest-server's 8000 was.

**`services.restic.server.listenAddress` takes a port only.** The module uses socket
activation, and a `host:port` value trips an assertion.

**Hosts keep `America/Denver`**, like vulcanus, so a timer means what the
hypervisor's logs mean. Kubernetes schedules are UTC. [`backups.md`](backups.md)
carries both side by side.
