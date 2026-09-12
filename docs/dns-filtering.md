# DNS filtering

Ad, tracker and malware blocking for the household and the family, built on
Cloudflare Zero Trust Gateway. Gateway holds the blocklists and the policy;
everything here is about how queries reach it.

## What reaches Gateway, and how

| Client | Path | Exceptions apply? |
|---|---|---|
| `vulcanus` LAN | CoreDNS → Gateway over DoT | yes |
| Cluster, infra VMs | the same CoreDNS | yes |
| Tailnet devices, on or off LAN | Headscale global nameserver → Gateway DoH | yes |
| Family iPhones, anywhere | Apple configuration profile → Gateway DoH | yes |
| Relatives' routers | AdGuard's public resolver | no |

A phone carrying the profile is covered on any network, including cellular,
which is why the profile rather than the router is the primary delivery
mechanism for family devices. The routers only cover what cannot carry a
profile: TVs, laptops on that wifi, IoT.

## The CoreDNS forward

The catch-all zone forwards to two DoT upstreams: the Gateway location first,
AdGuard's public resolver second. Three details there are deliberate and each
breaks something if changed.

**`policy sequential`.** The default is `random`, which would send roughly half
of all queries to AdGuard — which does not have this household's allowlist — so
exceptions would appear to work half the time.

**The `%servername` form.** `tls_servername` is global to a whole forward block,
so two upstreams needing different certificate names cannot both use it. The
per-destination `tls://ADDRESS%SERVERNAME` syntax is the only construction that
works, and it must not be combined with a block-level `tls_servername`.

**AdGuard is given as an IP, Gateway as a hostname.** CoreDNS resolves upstream
hostnames once at startup, so a restart while DNS is already broken cannot
resolve the Gateway hostname and the server will not come up. The fallback has
no such dependency, and the router's secondary DNS entry covers the case where
CoreDNS itself is down — see [network.md](network.md).

A fallback to a *filtering* resolver rather than an unfiltered one means an
unreachable Gateway degrades to a generic blocklist instead of to no filtering.
`vulcanus` is several states away, so the degraded mode matters more than usual.

## Gateway locations

Three: `vulcanus`, `boston`, and `mobile` for the configuration profile. Each
has its own DoH hostname, which is what attributes a query to a site in
Gateway's logs. Pointing phones at a site's endpoint would file every roaming
query under that site and make the attribution meaningless.

**A location with no source IP has to be created through the API.** The
dashboard prefills the source IPv4 of whatever network you are on and refuses
when one of your own locations already holds it. `name` is the only required
field, and `networks` takes effect only if it is non-empty *and* the IPv4
endpoint is enabled, so a location created without networks is attributed purely
by its hostname.

## Blocklists and exceptions

A daily CronJob rebuilds the lists with
[cloudflare-gateway-pihole-scripts](https://github.com/mrrfv/cloudflare-gateway-pihole-scripts).

**The ceiling is 300 lists × 1,000 entries = 300,000 domains**, measured against
the account. Cloudflare's own `account-limits` page says "Lists: 100" and is
wrong; do not lower `CLOUDFLARE_LIST_ITEM_LIMIT` to match it without
re-measuring, because that trades away two thirds of the capacity on the
strength of a page already found incorrect.

**Exceptions go in the `allowlist.txt` key of the app's ConfigMap**, which the
profile server publishes over HTTP and the CronJob fetches. It cannot simply be
mounted: `download_lists.js` unlinks and rewrites `./allowlist.txt` before
anything reads it, which fails against a read-only ConfigMap mount. The
supported mechanism is the `ALLOWLIST_URLS` environment variable, and setting it
*replaces* upstream's recommended allowlists rather than extending them — which
is why they are repeated verbatim in the CronJob and re-checked on an image bump.

**The sync job runs as root**, alone among workloads here. Its image is
`FROM node:alpine` with no `USER` and a root-owned `WORKDIR /app`, and npm runs
scripts from the package root regardless of `workingDir`, so the lists it
downloads have nowhere else to go. `runAsNonRoot` or a read-only root filesystem
there produces a job that cannot write its own inputs. Capabilities are dropped
regardless.

## The Apple configuration profile

Served at `dns.immortalkeep.com` and installed by hand on family devices.

- `AllowFailover` is true. This is an ad blocker, not a security control, and
  failing closed means a relative with no working internet who deletes the
  profile and is never covered again.
- `ProhibitDisablement` is false, so a captive portal the exclusions miss still
  leaves an escape hatch that does not involve phoning for help.
- Captive portals are handled by an `OnDemandRules` entry with
  `Action: EvaluateConnection` and `DomainAction: NeverConnect`. There is no key
  for excluding domains. The trailing catch-all `Connect` rule is required —
  without it nothing uses the DoH server at all.
- The nginx config registers `application/x-apple-aspen-config` for
  `.mobileconfig`. iOS decides whether to offer a profile from the Content-Type
  alone, and nginx's stock `mime.types` has no entry for it.

## Encrypted artefacts have plaintext originals

Two files embed a Gateway DoH hostname, which is enough for anyone holding it to
resolve through this account's policies, so both are SOPS-encrypted. An
encrypted file diffs as ciphertext and stops being reviewable, so each has a
plaintext template beside it and is rendered by a script rather than edited:

| Template | Rendered | Script |
|---|---|---|
| `kubernetes/infrastructure/coredns-servers.yaml.template` | `coredns-servers.sops.yaml` | `tools/coredns/render-servers-secret.sh` |
| `kubernetes/apps/cloudflare-gateway/adblock.mobileconfig.template` | `adblock.mobileconfig.sops.yaml` | `tools/cloudflare-gateway/render-profile-secret.sh` |

**Edit the template, never the `.sops.yaml`.** Two copies of the same content
drifting apart is the failure this arrangement invites, which is why rendering
is a script and not a documented procedure.

The profile has its own Secret rather than sharing the credentials file for two
reasons: rendering needs only `sops --encrypt` and the public keys, so anyone
with the repo can regenerate it; and the sync CronJob consumes the credentials
with `envFrom`, which turns every key into an environment variable —
`adblock.mobileconfig` is not a valid name, so kubelet skips it and logs
`InvalidEnvironmentVariableNames`.

## Knowing it works

Two alerts cover genuinely different failures and neither substitutes for the
other.

**`dns-canary`** asks whether filtering is happening at all. Every 15 minutes it
resolves a control domain through CoreDNS and expects an answer, and a known ad
domain and expects nothing routable — no answer, or the `0.0.0.0` sentinel that
Gateway and AdGuard both return. It exits non-zero on either failure and so
rides `CronJobNotSucceeding` without needing a rule of its own.

The assertion is absolute rather than comparative on purpose. An earlier version
compared the ad domain's answer against an unfiltered resolver and treated a
difference as success; `doubleclick.net` is geo-distributed and rotates, so the
two disagreed constantly while nothing was filtered, and the check passed at
exactly the moment it existed to fail.

**`CronJobNotSucceeding`** on the sync job asks whether the blocklist is still
being maintained. A failed sync does not stop filtering — the existing lists and
policy stay in Cloudflare — so the canary keeps passing while the list goes
stale. That is the correct severity, and it is why both exist.
