# Tailnet

Self-hosted Tailscale, coordinated by [Headscale](https://headscale.net) running
in-cluster at `headscale.immortalkeep.com`. MagicDNS domain is `forge.local`, so
every node is `<hostname>.forge.local`.

Headscale is an independent reimplementation of the Tailscale control server,
not an official one and not a reference implementation — Tailscale's clients are
open source, the control plane is not. It scopes itself to single-tailnet,
self-hosted use, which is why some SaaS-only machinery does not work against it
(see [What does not work](#what-does-not-work)).

## Nodes

| Tailnet IP | Name | Owner | Notes |
|---|---|---|---|
| 100.64.0.1 | jetkvm-vulcanus | `will@` | Out-of-band console for the hypervisor |
| 100.64.0.2 | thenixbeast | `will@` | Desktop, NixOS |
| 100.64.0.3 | steamdeck | `will@` | Steam Deck, Jovian-NixOS |
| 100.64.0.4 | vulcanus | `tag:vulcanus-subnet` | Hypervisor; **subnet router** |
| 100.64.0.5 | theguide-iphone17 | `will@` | iOS, not managed by Nix |
| 100.64.0.6 | headplane-agent | `headplane@` | Headscale admin UI's agent |
| 100.64.0.7 | jetkvm-mini-nas | `will@` | Out-of-band console |
| 100.64.0.8 | mini-nas | `will@` | Offers an exit node |

Node key expiry is disabled on every node, so nothing is forcibly logged out
while the control server is unreachable.

## Reaching internal services

`vulcanus` advertises `192.168.0.0/24` to the tailnet. It is the router because
it is already on the tailnet, already on the LAN (`192.168.0.111` on `vmbr0`),
and already has to be up for anything else to work — it is the hypervisor, so it
adds no new failure domain.

That route is what makes internal names usable off the LAN. Headscale's split
DNS sends `immortalkeep.com` to CoreDNS at `192.168.0.202`, and CoreDNS answers
`*.immortalkeep.com` with the internal ingress at `192.168.0.203`. **Both
addresses are on the LAN.** Without the route a remote client sends the DNS
query to its own local gateway, which drops it, and nothing resolves — split DNS
alone is not enough, because the address CoreDNS returns has to be routable too.

Clients need `--accept-routes`. On the NixOS hosts this is pinned by
`my.tailscale.acceptRoutes` in `~/dotfiles/modules/tailscale`; the iPhone is set
by hand. Tailscale declines to install a route matching a host's own interface
subnet, so devices on the LAN are unaffected.

Configured by `ansible/proxmox-tailscale.yaml`, which also sets
`net.ipv4.ip_forward` — Proxmox ships it off because VM traffic crosses `vmbr0`
as a bridge rather than being routed, but a subnet router does need it.

## The access policy

**Advertising a route is not granting access to it.** What the tailnet may
actually reach is set by the Headscale policy in
`kubernetes/apps/headscale/policy-config-map.yaml`, and it is deliberately
narrow:

| Source | Destination | Why |
|---|---|---|
| `will@` | `will@:*` | Personal devices reach each other on any port |
| `will@` | `tag:vulcanus-subnet:22,8006` | SSH and the Proxmox web UI on the hypervisor |
| `will@` | `192.168.0.105:22` | SSH to the fileserver LXC. Shell only — its NFS and SMB exports stay LAN-side |
| `will@` | `192.168.0.200:6443` | Kubernetes API, so `kubectl` and `k9s` work while roaming |
| `will@` | `192.168.0.190:50000` | Talos API, so `talosctl` works while roaming — the tool needed when the cluster is the thing that is broken |
| `will@` | `192.168.0.202:53` | CoreDNS, or no internal name resolves |
| `will@` | `192.168.0.203:80,443` | Internal ingress — one door to every HTTP service |

Everything else on the LAN is routed but denied. Mumble, RustDesk and Syncthing
sync keep using their public port forwards rather than the tailnet.

It is narrow on purpose. Opening a policy up and tightening it later is a thing
that does not actually get done, so this one grows one entry at a time, each
with a reason.

### Running Terraform from the tailnet

`vulcanus.forge.local` is a tailnet node in its own right (`100.64.0.4`), not
something reached through the subnet router, so the Proxmox API answers on
`https://vulcanus.forge.local:8006`. `TF_VAR_proxmox_api_url` in `.env` decides
which path `tofu` takes: the hostname works from anywhere, a `192.168.0.x`
address only from the LAN, since the policy grants the API by tag rather than by
that address.

The Talos half of an apply is separate — `talos_machine_configuration_apply`
talks to `192.168.0.190:50000`, which the table above covers.

### Expanding it

When something on the LAN is unreachable and you think it should not be:

1. **Reproduce it against the address, not the name**, so DNS is not in the
   picture:
   ```bash
   nc -vz 192.168.0.105 22
   ```
2. **Check whether the route is even there.** A missing route looks exactly like
   a policy denial from the client's point of view:
   ```bash
   ip route get 192.168.0.105     # want: dev tailscale0, not via the local gateway
   ```
   If it goes via the local gateway, the problem is `--accept-routes` or route
   approval, not the ACL. Confirm approval with `headscale nodes list-routes`.
3. **Read the filter the client was actually given.** This is the compiled
   policy as the client sees it, and is authoritative:
   ```bash
   tailscale debug netmap | jq '.PacketFilter'
   ```
   If the destination does not appear in any `Dsts`, it is the ACL.
4. **Add the narrowest entry that covers it** to `policy-config-map.yaml` — a
   specific IP and port, not a subnet and not `*` — with a comment saying what
   needed it.
5. **Validate before applying.** `headscale policy check` connects to the server
   *before* it parses the file, so a syntax error and an unreachable server look
   identical; it is not a useful offline linter. Start a throwaway headscale
   against the file instead, which does validate both syntax and semantics
   (unknown tags, unknown users):
   ```bash
   podman run --rm -v "$PWD:/hs:Z" docker.io/headscale/headscale:v0.29.3 \
     serve --config /hs/config.yaml
   ```
   Reaching the DERP or database stage means the policy loaded. A bad policy
   fails at `initializing policy manager` with the offending rule named.
6. **Apply, then restart.** The ConfigMap is mounted un-hashed, so Flux updating
   it does **not** restart headscale — see [kubernetes.md](kubernetes.md):
   ```bash
   kubectl rollout restart deployment/headscale -n apps
   ```
7. **Confirm from the client** that `PacketFilter` grew the entry. Clients pick
   up a new filter on their next netmap poll, not instantly.

## Resilience: WireGuard is the break-glass path

Headscale runs in the cluster and is published through `ingress-nginx-external`.
**The control plane for the VPN is served by the thing the VPN is used to
reach.** A cluster outage therefore takes out new tailnet registrations and any
peer that needs a fresh netmap.

The plain WireGuard tunnel on LXC `192.168.0.103` is independent of Kubernetes
and is the way in when that happens. It is **permanent infrastructure, not a
leftover** — do not retire it on the grounds that Tailscale now covers the same
ground, because the one case where it matters is the case where Tailscale
cannot.

Three things keep a cluster outage survivable, and none of them should be
"improved" without understanding what they buy:

- **DERP comes from Tailscale's public map**
  (`controlplane.tailscale.com/derpmap/default`), and the embedded DERP server
  is `enabled: false`. Self-hosting DERP would put relaying and STUN inside the
  same failure domain.
- **Node key expiry is disabled**, so an outage cannot cascade into nodes
  logging themselves out.
- **`headscale.immortalkeep.com` resolves via public DNS**, pinned in the split
  DNS config to `1.1.1.1`/`1.0.0.1` by a longest-suffix match that beats the
  `immortalkeep.com` rule. Without it, resolving the control server needs the
  tailnet and the tailnet needs the control server — a deadlock that strands
  any node whose netmap blips, permanently.

Running headscale outside the cluster would remove the circular dependency
properly. It is not done because WireGuard already covers the case, and moving
it is a larger change than the problem justifies.

## What does not work

- **The Tailscale Kubernetes operator.** It authenticates by OAuth against
  `login.tailscale.com` and drives Tailscale's admin API, with no option for a
  custom control server; Headscale closed the request as wontfix. Exposing a
  cluster Service to the tailnet means a hand-rolled sidecar with a pre-auth
  key. Nothing here needs one — the subnet route plus the ingress covers it.
- **`siderolabs/tailscale` on the Talos nodes.** Not installed, deliberately.
  It would put the *node's host network* on the tailnet, not MetalLB service IPs
  or pods, so it does nothing for the services above; the `talosctl` access it
  buys is already covered twice, by WireGuard and by the subnet route with its
  policy entry for port 50000. And a
  node's `tailscaled` has to reach `headscale.immortalkeep.com`, which the
  cluster itself serves — so it is least available exactly when it would be
  wanted.
- **A reusable pre-auth key.** Fresh machines are registered interactively
  instead; a long-lived reusable key is a single point of failure for the whole
  tailnet. See `~/dotfiles/modules/tailscale/README.md`.
