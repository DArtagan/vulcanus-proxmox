# DNS-O-Matic is shutting down and it is the only thing updating our public DNS

## Opening prompt

> DNS-O-Matic shuts down in 30 days. It is the only thing keeping the public A
> records for `immortalkeep.com` pointed at a WAN IP that changes without
> warning, and every way back into the house — Headscale, the WireGuard
> break-glass tunnel, cert-manager's HTTP-01 renewals — resolves through those
> records. Read `todos/dnsomatic-replacement.md`. The replacement is
> `favonia/cloudflare-ddns` talking to the Cloudflare API directly with a
> zone-scoped token; the care is all in the cutover, because a mistake here
> removes the ability to fix the mistake.

Verified **2026-09-04**.

## What exists

`kubernetes/apps/dnsomatic/` — three files, unchanged in substance since
2023-01-14:

```yaml
image: ammmze/dns-o-matic          # no tag; one of the few unpinned images here
env:
  - name: DELAY
    value: "5m"
  - name: IP_ADDR_PROVIDER
    value: "https://api.ipify.org"
envFrom:
  - secretRef:
      name: dnsomatic-credentials   # USERNAME, PASSWORD, HOST=dynamic
```

It posts the detected IP to DNS-O-Matic. **DNS-O-Matic holds a Cloudflare Global
API Key** — full account access, every zone, including the power to change
nameservers — and writes the record on our behalf. That credential lives in a
third party's database and is not in this repo.

The WAN IP changed from 174.29.1.69 to 71.218.26.45 between 2026-09-04 and
2026-09-08, and dnsomatic tracked it — so the update path works today, and four
days is a realistic interval between changes rather than a hypothetical one.

Live state at the pod, 2026-09-04:

```
OLD IP: 174.29.1.69
NEW IP: 174.29.1.69
IP is still the same, not updating
```

## What the zone looks like

Enumerated through the Cloudflare API on 2026-09-08. **The zone is a CNAME
chain over a single A record**, not the set of A records an external `dig`
suggests:

| Type | Name | Target |
|---|---|---|
| A | `dynamic.immortalkeep.com` | the WAN IP — **the only record that holds it** |
| CNAME | `immortalkeep.com` | `dynamic.immortalkeep.com` |
| CNAME | `*.immortalkeep.com` | `immortalkeep.com` |
| CNAME | `demo.immortalkeep.com` | `immortalkeep.com` |
| A | `status.immortalkeep.com` | 192.0.2.1, **proxied** — a placeholder for a redirect rule |

This is what `HOST=dynamic` in the dnsomatic secret means: DNS-O-Matic updates
`dynamic` and the rest of the zone follows. Cloudflare answers with flattened A
records, so from outside every name resolves to an address and the indirection
is invisible — `dig` cannot see it at all.

**So `DOMAINS` is `dynamic.immortalkeep.com`, and nothing else.** Pointing the
updater at `immortalkeep.com` or `*.immortalkeep.com` would have it create A
records over those CNAMEs, collapsing the indirection the zone is built on.
`status` is proxied and belongs to a redirect rule; it stays out.

There are no AAAA records, so `IP6_PROVIDER=none`.

Nameservers are `brad.ns.cloudflare.com` / `nina.ns.cloudflare.com` — Cloudflare
is already authoritative. Nothing in this repo touches the Cloudflare API today;
there is no token, no `external-dns`, no terraform provider.

`headscale.immortalkeep.com` has no record of its own; it reaches the WAN IP
through the wildcard like everything else, so the single A record carries it
too. This was the open question the enumeration answered, and it answered it
the opposite way to what `dig` implied.

## Why this is not a routine swap

A stale record is not a cosmetic outage. Every remote path runs through it:

- **Headscale.** `docs/tailnet.md` pins `headscale.immortalkeep.com` to public
  DNS (1.1.1.1/1.0.0.1) deliberately, to avoid the bootstrap deadlock where
  resolving the control server needs the tailnet and the tailnet needs the
  control server. A stale record breaks new registrations and any node needing a
  fresh netmap. Both JetKVMs are tailnet nodes, so the out-of-band console for
  the hypervisor is behind the same door.
- **WireGuard on LXC 192.168.0.103**, the documented break-glass path, is
  reached at the same WAN IP.
- **cert-manager is HTTP-01 only** — `kubernetes/infrastructure/cert-manager.yaml`
  has a single solver on `ingress-nginx-external`. A record that stays stale
  eventually fails every TLS renewal in the cluster.
- Flux's GitHub webhook, Plex, PhotoPrism, Linkding, Stump, Podbook, demo/apex.

There is **no alert on any of it**. `prometheus-rules.yaml` contains nothing
touching DNS or dnsomatic, there is no blackbox exporter and no `Probe` object.
dnsomatic is a Deployment, so `CronJobNotSucceeding` cannot see it. A silent
updater failure plus an ISP IP change is the lockout scenario, and today it
would pass unnoticed until something visibly broke.

## Decisions already made, by the user

- **In-cluster**, replacing `dnsomatic` in place. Running it on the WireGuard
  LXC instead — outside the Kubernetes failure domain — was offered and
  declined.
- **No second DDNS provider** as an independent recovery anchor. Offered and
  declined; keep the change minimal.
- **No healthchecks.io check.** The budget is squeezed by the parallel backups
  work. UptimeRobot already probes `immortalkeep.com`, `plex.immortalkeep.com`
  and `mumble.immortalkeep.com` from outside, which catches the failure that
  actually matters.
- **No five-minute push notifications.** "Once, when it goes down, would be
  fine. Repeatedly is not." This is what rules out favonia's `SHOUTRRR`
  notifier — see below.

## The replacement

`favonia/cloudflare-ddns` v1.17.0 (released 2026-07-28). Go, ~10 MiB, caches
Cloudflare API responses, actively maintained. Needs a token with the *Edit zone
DNS* template scoped to the one zone (`Zone:DNS:Edit` + `Zone:Zone:Read`) — a
real improvement on handing a Global API Key to a third party.

### Ruled out, with reasons, so they are not re-proposed

**Cloudflare Tunnel, and proxying `headscale`.** `cloudflared` strips the
`Upgrade` header on POST requests (cloudflare/cloudflared#883, open since 2023)
and Tailscale's TS2021 handshake is a WebSocket over POST. Headscale behind a
tunnel or an orange cloud does not work — juanfont/headscale#2379. Records stay
grey-cloud.

**Running it as a CronJob.** This was the tempting option and it is wrong.
`UPDATE_CRON=@once` exists, and a `*/5 * * * *` schedule lands in the 5400s
bucket of `cronjob:max_seconds_without_success`, so the existing
`CronJobNotSucceeding` and `CronJobHasNeverSucceeded` rules would appear to
cover it for free. But in `@once` mode `realMain` returns 0 unconditionally —
read `cmd/ddns/ddns.go`: it calls `updater.UpdateIPs(...)`, hands the result to
the heartbeat and notifier, then returns 0 without ever consulting it. Only
config and startup errors return 1. A revoked token would produce a Successful
Job every five minutes forever and the alert would be structurally blind. **A
check that cannot fail is not a check.** It stays a Deployment.

**favonia's `SHOUTRRR` notifier.** It would cover the one gap below, and it is
quiet in normal operation (`notifier.Message.IsEmpty()` gates sending). But it
goes straight to Pushover, bypassing Alertmanager, so a persistently failing
updater pushes every five minutes with no grouping or `repeat_interval`. Ruled
out by the user's instruction above. `HEALTHCHECKS`, `UPTIMEKUMA` and `SHOUTRRR`
all stay unset.

## Monitoring

Two layers, both already paid for.

**UptimeRobot** — unchanged, no new configuration. It probes three public
hostnames from outside; a stale record makes all three fail. End-to-end, correct
vantage point, indifferent to *why* the record is wrong.

**One Prometheus rule**, `DDNSUpdaterDown`:

```
kube_deployment_status_replicas_available{namespace="apps", deployment="cloudflare-ddns"} == 0
or absent(kube_deployment_status_replicas_available{namespace="apps", deployment="cloudflare-ddns"})
```

Verified against the live cluster on 2026-09-04, all three directions: healthy
(`dnsomatic`) returns empty, scaled-to-zero (`podgrab`) fires, and the absent
case fires — confirmed by running the expression as written, which matched
because `cloudflare-ddns` did not exist yet. 43 series exist for the metric, so
the kube-state-metrics collector is enabled.

`absent` is the half that matters most: the Deployment being removed outright
leaves no series, and `== 0` has nothing to compare, so the loudest form of this
failure is the one the bare comparison cannot see. Scope it to the one
deployment by name — `podgrab` is intentionally scaled to zero and a rule
matching any zero-replica Deployment would fire on it forever. Routing through
Alertmanager gives `repeat_interval: 12h`, so it notifies once and stays quiet.

**Known gap, accepted.** A process that is Ready but failing every Cloudflare
call — a revoked token is the realistic case — is not covered. The record stays
correct until the ISP changes the IP, at which point UptimeRobot catches it.
That is no worse than today, which has no monitoring at all. Mitigation is a
non-expiring token. Closing it properly needs a healthchecks.io slot or a small
exporter comparing the public record to the WAN IP; revisit if a slot frees up.

## Cutover

Zero-gap by construction: the new updater runs **alongside** dnsomatic until
proven. Both write the same value to the same records, which is idempotent —
there is no conflict and no window with nothing updating. Flux reconciles from
`main`, so steps 2, 4 and 6 are each a merge.

1. Create the Cloudflare token, *Edit zone DNS* template, scoped to
   `immortalkeep.com` only, **no expiry**. Put it in
   `kubernetes/apps/cloudflare-ddns/credentials.sops.yaml` with `sops`.

   Check it by listing the zone, not with `/user/tokens/verify`. That endpoint
   is user-scoped and returns `success: false` for a token restricted to a
   single zone, which reads as a broken token when nothing is wrong.
2. Deploy with `DOMAINS=ddns-canary.immortalkeep.com` and nothing else. Nothing
   resolves through that name, so a misconfiguration has no blast radius.

   **Confirm via the API, not `dig`.** The `*` wildcard already answers for
   `ddns-canary.immortalkeep.com` with the correct WAN IP, so a dig returns the
   right answer whether or not the updater created anything — a check that
   cannot fail. List the records instead and look for an explicit `ddns-canary`
   entry:
   ```
   curl -s -H "Authorization: Bearer $TOKEN" \
     "https://api.cloudflare.com/client/v4/zones/$ZONE/dns_records?type=A&name=ddns-canary.immortalkeep.com" \
     | jq -r '.result[] | "\(.name)\t\(.content)"'
   ```
3. **The acceptance test — run 2026-09-10, passed.** Break the canary
   (`192.0.2.1`) and confirm the updater corrects it.

   Result: `📡 Updated an outdated A record for ddns-canary.immortalkeep.com`.
   That is the *update* path on an existing record, which is what taking over
   `dynamic` requires — the earlier creation of the canary only proved it could
   add a record that was not there.

   **What the run revealed about caching, which matters more than the test.**
   The first cycle after the break reported `already up to date (cached)` and
   did not look at the record at all. `internal/api/cloudflare.go` keeps
   `listRecords` in a `ttlcache` keyed on domain name with TTL
   `CACHE_EXPIRATION`, default 6h. So:

   - **Out-of-band drift is not corrected for up to 6 hours.** If something
     other than the updater changes the record, it will not notice until that
     cache entry expires.
   - **An actual WAN IP change is always tracked within one cycle.** The
     detected address is compared against the cached record value, so a new IP
     mismatches immediately and triggers the update. The cache never delays the
     case this project exists for.

   Because of that first point, the break test only completes promptly after a
   `kubectl rollout restart`, which empties the cache and forces a fresh read.
   That is how it was run, and it is worth knowing before anyone repeats it and
   concludes from a quiet five minutes that the updater is broken.
4. Point `DOMAINS` at `dynamic.immortalkeep.com` — the one record that holds
   the WAN IP. Both updaters now write the same value to the same record. Read
   the result back from the API, and re-list the whole zone afterwards: the
   updater **creates** records it cannot find, so a name that does not exist
   yields a new junk A record rather than an error, and over a CNAME it would
   shadow the indirection instead.
   ```
   TOKEN=$(kubectl get secret cloudflare-ddns -n apps \
     -o jsonpath='{.data.CLOUDFLARE_API_TOKEN}' | base64 -d)
   ZONE=$(curl -s -H "Authorization: Bearer $TOKEN" \
     "https://api.cloudflare.com/client/v4/zones?name=immortalkeep.com" \
     | jq -r '.result[0].id')
   curl -s -H "Authorization: Bearer $TOKEN" \
     "https://api.cloudflare.com/client/v4/zones/$ZONE/dns_records?per_page=200" \
     | jq -r '.result[] | "\(.type)\t\(.name)\t-> \(.content)"' | sort
   ```
   The expected end state is the table above, unchanged except that `dynamic`
   is now maintained by `cloudflare-ddns`.
5. Prove `DDNSUpdaterDown` fires — scale to zero, wait past `for: 15m`, confirm
   Pushover, scale back, confirm it resolves.
6. Remove `kubernetes/apps/dnsomatic/` and its kustomization line.
7. **Revoke the Cloudflare Global API Key** in the DNS-O-Matic account, then
   delete the account. Easiest step to forget and the one with the most security
   value: that key is full-account access sitting inside a service that is
   shutting down.
8. Fold what is permanently true into `docs/network.md`, add the
   `docs/project_log.md` entry, delete this spec.

If any step goes wrong, the record can be corrected by hand in the Cloudflare
dashboard, which is reachable from anywhere and does not depend on the house
being reachable. dnsomatic runs throughout steps 1–5, so the live records are
never unattended.

## Adjacent findings, deliberately not bundled

- **cert-manager is HTTP-01 only.** The token this project adds is one
  permission short of enabling a DNS-01 issuer, which would make certificate
  renewal independent of the public record and of inbound port 80. Its own spec.
- **Proxying some subdomains.** Worth noting up front that proxying does *not*
  reduce the dependency on this record — an orange-cloud record still holds the
  origin IP. The win is that the residential IP stops being published, which
  matters because this repo is public. `linkding`, `stump`, `podbook`,
  `trello-randomizer`, `demo`/apex and `flux-webhook` are candidates;
  `headscale` (Upgrade-on-POST), `plex` (video through the free-plan CDN is
  against Cloudflare's terms) and the L4 services are not. Note `PROXIED` is a
  domain expression, not a bool, and applies only to records the updater
  creates.
- `dns.immortalkeep.com` on the homepage dashboard has no ingress rule and no
  CoreDNS carve-out, so it falls through the wildcard to the internal ingress
  and 404s. `status.immortalkeep.com` in `coredns.yaml` is the pattern that
  fixes it.
- The WireGuard break-glass path is missing from `docs/network.md`'s
  port-forward table — the server listens on UDP 51820 at 192.168.0.103. The
  safety net for this very project is undocumented.
- Branch `cloudflare-gateway` and its worktree are an ancestor of `main` with
  zero unique commits. Prunable.
