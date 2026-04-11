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
| 192.168.0.204–210 | Available |

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
| kubernetes.immortalkeep.com | Kubernetes Dashboard | Cluster UI |
| syncthing.immortalkeep.com | Syncthing | File sync |
| filebot.immortalkeep.com | Filebot | Media organizer |
| podgrab.immortalkeep.com | Podgrab | Podcast downloader |
| youtube.immortalkeep.com | youtube-dl | Video downloader |
| media-toolkit.immortalkeep.com | Media Toolkit Webtop | Desktop environment |
| arm.immortalkeep.com | Automatic Ripping Machine | DVD ripper |
| botamusique.immortalkeep.com | Botamusique | Music bot (disabled) |

### Non-HTTP Services (TCP/UDP passthrough via both ingress controllers)

| Port | Protocol | Service |
|------|----------|---------|
| 64738 | TCP + UDP | Mumble (voice chat) |
| 22000 | TCP + UDP | Syncthing (sync protocol) |
| 21027 | UDP | Syncthing (discovery) |

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

**forge.local for Headscale MagicDNS:** The `.local` TLD is technically reserved
for mDNS (RFC 6762), but this works in practice because Tailscale clients use
their own DNS resolver, not the system's mDNS stack. Kept as-is to avoid
re-registering all tailnet devices. May be revisited when deploying Tailscale on
the Talos nodes, at which point `forge.home.arpa` (RFC 8375) is a better choice.
