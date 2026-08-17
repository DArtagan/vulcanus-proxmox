# Network

## IP Address Inventory

### Infrastructure

| IP | Hostname | Description |
|----|----------|-------------|
| 192.168.0.1 | — | Router / gateway |
| 192.168.0.105 | fileserver.immortalkeep.com | NFS/SMB file server (LXC on Proxmox) |
| 192.168.0.107 | proxmox-backup-server.immortalkeep.com | Proxmox Backup Server |
| 192.168.0.111 | vulcanus.immortalkeep.com | Originally our Minecraft server |

### Kubernetes Cluster (Talos Linux)

| IP | Hostname | Role | Resources |
|----|----------|------|-----------|
| 192.168.0.190 | piraeus-control-plane-0.immortalkeep.com | Control plane | 3 GiB RAM, 2 cores |
| 192.168.0.195 | piraeus-worker-0.immortalkeep.com | Worker (primary) | 24 GiB RAM, 8 cores, 1 TB OpenEBS |
| 192.168.0.196 | piraeus-worker-1.immortalkeep.com | Worker (optical drive) | 8 GiB RAM, 4 cores, 100 GB OpenEBS |
| 192.168.0.200 | piraeus-api.immortalkeep.com | Cluster VIP (virtual) | — |

### MetalLB Service IPs (pool: 192.168.0.201–210)

| IP | Service |
|----|---------|
| 192.168.0.201 | ingress-nginx-external |
| 192.168.0.202 | CoreDNS |
| 192.168.0.203 | ingress-nginx-internal (default ingress class) |
| 192.168.0.204 | RustDesk (hbbs + hbbr) |
| 192.168.0.205 | Mumble (voice chat) |
| 192.168.0.206 | Syncthing (sync protocol) |
| 192.168.0.207–210 | Available |

Check the router's DHCP range before widening the pool past .210.

## DNS Architecture

```
                            ┌─────────────┐
                            │   Router    │
                            │ DHCP server │
                            └──────┬──────┘
                                   │ Hands out DNS:
                                   │  Primary:   192.168.0.202 (CoreDNS)
                                   │  Secondary: 1.1.1.1       (Cloudflare)
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
    ┌─────────▼──────────┐  ┌─────▼──────┐  ┌─────────▼──────────┐
    │   LAN Devices      │  │ Infra VMs  │  │  Tailnet Devices   │
    │ (TVs, IoT, guests) │  │ (fileserver)│  │ (laptop, phone)    │
    └─────────┬──────────┘  └─────┬──────┘  └─────────┬──────────┘
              │                    │                    │
              │ All DNS            │ All DNS            │ Headscale split DNS
              │                    │                    │ routes immortalkeep.com
              ▼                    ▼                    ▼
        ┌──────────────────────────────────────┐
        │          CoreDNS (192.168.0.202)     │
        │                                      │
        │  immortalkeep.com zone:              │
        │    *.immortalkeep.com → 192.168.0.203│
        │    (+ explicit infra host records)   │
        │                                      │
        │  Everything else:                    │
        │    Forward to Cloudflare (1.1.1.1)   │
        └──────────────────────────────────────┘
```

### DNS Resolution by Client Type

**LAN devices** (via router DHCP): CoreDNS is the primary DNS server. All
`*.immortalkeep.com` queries resolve to the internal ingress (192.168.0.203).
If CoreDNS is down (cluster restart), devices fall back to Cloudflare — internet
keeps working, but `*.immortalkeep.com` resolves to the public IP and hits the
external ingress instead. Internal-only services become unreachable by name,
which is expected since the cluster hosting them is also down.

**Tailnet devices** (via Headscale): Headscale's split DNS configuration routes
`immortalkeep.com` queries to CoreDNS (192.168.0.202), ensuring tailnet clients
always get the internal ingress IP rather than the public record.

This depends on `vulcanus` advertising `192.168.0.0/24` to the tailnet. Both
192.168.0.202 and the 192.168.0.203 it answers with are LAN addresses, so
without that route a remote client hands the query to its own local gateway and
it is dropped — split DNS alone is not enough, because the address CoreDNS
returns has to be routable too. Which of those routed addresses a client may
actually reach is a separate question, answered by the Headscale policy. See
[tailnet.md](tailnet.md).

**Cluster-internal** (pods): Uses kube-dns for service discovery. Unrelated to
the CoreDNS LoadBalancer service.

### Hairpinning

Hairpinning (traffic looping out to the public IP and back in) is not a concern
because every client type that resolves `*.immortalkeep.com` does so through
CoreDNS, which returns the internal ingress IP directly. LAN devices that fall
back to Cloudflare during a CoreDNS outage will hit the external ingress, which
is the correct degraded behavior.


## Service Exposure

All services use `*.immortalkeep.com` hostnames. The ingress class determines
whether a service is reachable from the LAN only (internal) or also from the
internet (external).

### Dual Ingress (internal + external)

| Hostname | Service | Notes |
|----------|---------|-------|
| headscale.immortalkeep.com | Headscale | VPN control plane, must be public |
| plex.immortalkeep.com | Plex | Media streaming |
| photoprism.immortalkeep.com | PhotoPrism | Photo management |
| linkding.immortalkeep.com | Linkding | Bookmarks |
| podbook.immortalkeep.com | Podbook | Podcast manager |
| trello-randomizer.immortalkeep.com | Trello Randomizer | Trello automation |
| demo.immortalkeep.com | Demo | Showcase site (also serves immortalkeep.com root) |

### Internal Only

| Hostname | Service | Notes |
|----------|---------|-------|
| headplane.immortalkeep.com | Headplane | Headscale admin UI |
| homepage.immortalkeep.com | Homepage | Dashboard |
| grafana.immortalkeep.com | Grafana | Monitoring dashboards |
| prometheus.immortalkeep.com | Prometheus | Metrics |
| syncthing.immortalkeep.com | Syncthing | File sync |
| filebot.immortalkeep.com | Filebot | Media organizer |
| pinepods.immortalkeep.com | Pinepods | Podcast archive and player |
| podgrab.immortalkeep.com | Podgrab | Podcast downloader (scaled to zero, superseded by Pinepods) |
| youtube.immortalkeep.com | youtube-dl | Video downloader |
| media-toolkit.immortalkeep.com | Media Toolkit Webtop | Desktop environment |
| arm.immortalkeep.com | Automatic Ripping Machine | DVD ripper |
| botamusique.immortalkeep.com | Botamusique | Music bot (disabled) |

### Non-HTTP Services (dedicated LoadBalancer IP)

No service uses the ingress controllers for TCP/UDP any more. Each L4 service
has its own MetalLB address with `externalTrafficPolicy: Local`, which preserves
the client address and puts the pod directly behind the address with no proxy in
the path.

| IP | Port | Protocol | Service |
|----|------|----------|---------|
| 192.168.0.204 | 21115 | TCP | RustDesk hbbs (NAT type test) |
| 192.168.0.204 | 21116 | TCP + UDP | RustDesk hbbs (rendezvous, ID registration, hole punching) |
| 192.168.0.204 | 21117 | TCP | RustDesk hbbr (relay) |
| 192.168.0.205 | 64738 | TCP + UDP | Mumble (voice chat) |
| 192.168.0.206 | 22000 | TCP + UDP | Syncthing (sync protocol) |

Mumble and Syncthing moved here on 2026-08-07. They had been going through
ingress-nginx's `tcp:`/`udp:` ConfigMap keys, which are not part of the Ingress
API but an nginx `stream` block bolted onto an HTTP controller. nginx closes a
UDP stream after one response datagram, so Mumble clients had to tick "force
TCP" to be heard at all and Syncthing never negotiated QUIC — every connection
reported `tcp-server`.

Syncthing's 21027 was dropped rather than moved. It is LAN discovery, which
works by broadcast within a subnet; the pod's broadcast domain is the cluster
network, so announcements cannot cross in either direction no matter how it is
exposed. It had also been mapped as TCP against a UDP-only Service port, so it
had never worked.

Syncthing keeps its ClusterIP Service for the web UI, which stays behind the
ingress with TLS. `syncthing.immortalkeep.com` therefore still resolves to the
internal ingress via the wildcard; sync traffic uses `syncthing-sync`.

### Router Port Forwarding

| Service | External port | Internal IP | Internal port | Protocol |
|---------|---------------|-------------|---------------|----------|
| HTTP (redirect to HTTPS) | 80 | 192.168.0.201 | 80 | TCP |
| HTTPS (external ingress) | 443 | 192.168.0.201 | 443 | TCP |
| RustDesk hbbs NAT test | 21115 | 192.168.0.204 | 21115 | TCP |
| RustDesk hbbs rendezvous | 21116 | 192.168.0.204 | 21116 | TCP |
| RustDesk hbbs hole punching | 21116 | 192.168.0.204 | 21116 | UDP |
| RustDesk hbbr relay | 21117 | 192.168.0.204 | 21117 | TCP |
| Mumble | 64738 | 192.168.0.205 | 64738 | TCP |
| Mumble | 64738 | 192.168.0.205 | 64738 | UDP |
| Syncthing sync | 22000 | 192.168.0.206 | 22000 | TCP |
| Syncthing QUIC | 22000 | 192.168.0.206 | 22000 | UDP |

Mumble and Syncthing previously forwarded to **192.168.0.201**; those rules need
repointing to .205 and .206. Any rule forwarding **21027** should be deleted.

**Never forward:**

| IP | Why |
|----|-----|
| 192.168.0.202 | CoreDNS. Exposing it publishes an open resolver, which will be abused for DNS amplification. |
| 192.168.0.203 | Internal ingress. It serves the same hostnames without the external class's intent, bypassing the internal/external split. |

### No Ingress

| Service | Notes |
|---------|-------|
| borgmatic | Backup orchestration |
| rclone | Cloud sync (Dropbox, B2) |
| dnsomatic | DDNS updater |
| beets | Music library CronJob |
| tinyproxy | HTTP proxy (disabled) |


## Decisions and Rationale

**CoreDNS as primary LAN DNS (not sole DNS):** CoreDNS is handed out as the
primary DNS server via router DHCP, with Cloudflare (1.1.1.1) as secondary.
This gives consistent `*.immortalkeep.com` resolution for all LAN devices while
providing a safety net — if the Kubernetes cluster restarts, general internet
DNS continues working via the Cloudflare fallback.

**No immortalkeep.local rewrite:** An earlier TODO proposed rewriting
`immortalkeep.com` to `immortalkeep.local` inside CoreDNS to prevent
hairpinning. This was rejected because all client types already resolve through
CoreDNS (directly or via Headscale split DNS), so hairpinning doesn't occur.
The rewrite would have introduced a second domain that all apps would need to
know about, adding complexity for no gain.

**RustDesk bypasses ingress-nginx:** Every other non-HTTP service is exposed
through the ingress controllers' `tcp:`/`udp:` port maps. RustDesk is not,
for two reasons. First, ingress-nginx renders `proxy_responses 1` for every UDP
stream, which closes the UDP session after a single reply — but RustDesk's
UDP 21116 is a long-lived channel over which `hbbs` *pushes* punch-hole
requests to the controlled machine, so inbound sessions would never start.
Second, `hbbs` hands the source address it observes to the other peer as the
hole-punch target; behind nginx that is the controller pod IP, which is
unroutable, so every session would silently fall back to the relay. A dedicated
MetalLB IP with `externalTrafficPolicy: Local` avoids both. Raising
`proxy-stream-responses` globally would have fixed only the first problem, at
the cost of changing mumble's and syncthing's UDP behaviour.

**forge.local for Headscale MagicDNS:** The `.local` TLD is technically reserved
for mDNS (RFC 6762), but this works in practice because Tailscale clients use
their own DNS resolver, not the system's mDNS stack. Kept as-is to avoid
re-registering all tailnet devices. May be revisited when deploying Tailscale on
the Talos nodes, at which point `forge.home.arpa` (RFC 8375) is a better choice.
