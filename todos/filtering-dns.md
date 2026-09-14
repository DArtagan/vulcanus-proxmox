# Ad-blocking, privacy and security DNS for the household and the family

Self-managed DNS filtering — the thing nextdns.io sells — covering the LAN, the
cluster, the tailnet, family iPhones when they are away from home, and four
relatives' houses. Auto-updating blocklists, and exceptions that can be added
without ceremony.

Branch `filtering-dns`. See **Where this stands**.

**Topology, because it drives the risk model.** `vulcanus` — the Proxmox host,
the Talos cluster, CoreDNS on `192.168.0.202`, the `192.168.0.0/24` LAN that
`docs/network.md` describes — is housed **at Will's brother's house, many states
away**. `mini-nas`, the offsite ZFS replication target, is the machine housed
**with Will**. The two Gateway locations are named for the two sites.

Everything that follows about CoreDNS therefore changes DNS for a household
nobody involved can walk into. A wrong upstream there is not an inconvenience;
it is someone else's internet, fixed remotely or not at all.

## Why this is not simply a NextDNS subscription

Assessed 2026-09-10 against every hosted option, on the one constraint that
actually binds: **attributing a plain-IPv4 DNS query from a dynamic-IP remote
router with no hardware on site.** The four relatives' routers accept only static
IPv4 DNS server entries — confirmed on two of the four — have no IPv6, and offer
DDNS for Dyn.com and No-IP.com only.

Every vendor's IPv4 resolvers are anycast addresses shared across all customers,
so a plain-DNS query can be attributed to an account only by source IP, which
moves with every ISP renewal.

| Option | Why not |
|---|---|
| NextDNS, $19.90/yr | Refuses remote linking as policy: "updating IP has to be done from the same IP to be linked". `link-ip.nextdns.io` links the *caller's* address and takes no `myip=`. Needs a device at each site anyway, and charges for the privilege. |
| Control D, $30–60/yr | "Legacy DNS resolvers require the source IP of the client to be known to Control D." Same wall. |
| AdGuard DNS Personal, ~$30/yr | $2.49/mo + VAT, 10M requests, 20 devices — but dedicated IPv4 is Team-only, so it does not address the remote routers at all. |
| AdGuard DNS Team, ~$120/yr | Dedicated IPv4 per profile is the only true structural fix, but Team grants **2** dedicated IPs against four sites. |
| uBlockDNS | Disqualified on trust before features: one operator, recently-registered domain, no funding model, and a name trading on uBlock Origin. No account model, so no custom allowlist. |
| Stacking free tiers | Four accounts to babysit, which is the pattern rejected below. |

**The category is unusually mortal, which is itself a reason to prefer an
incumbent.** dns0.eu — run by the NextDNS founders, 62 servers across 27 cities —
shut down in October 2025 for lack of resources. Mullvad's free resolver, from a
well-funded company, was announced dead on 2026-09-03 and goes dark 2026-11-02.

**Cloudflare Gateway wins because one shared ruleset is enough.** Its DNS policies
are account-global; per-person rules would need WARP enrolment plus Access
identity on every device. Will decided a single shared ruleset is sufficient,
which removes the only gap that disqualified it — and with that gap gone,
NextDNS's headline advantage is worth nothing.

Accepted knowingly: blocklists cap at 100 lists × 1,000 entries = 100,000 domains
on non-Enterprise, which comfortably fits oisd small or Hagezi Normal; and DNS
logs retain 24h on the free plan.

**Self-hosting the central filter was considered and rejected.** AdGuard Home in
the cluster behind a public DoH endpoint would be free with unlimited lists and
logs, and its client-ID-in-path feature would even give per-person profiles. But
it puts every relative's DNS behind this house's internet and power, and a
roaming iPhone on cellular would resolve across the country instead of to the
nearest anycast PoP. That fails "responsive" and "works on the go". AdGuard Home
earns its place at the edge, not the centre.

## Decisions already made, not to be relitigated

- **One shared ruleset**, not per-person profiles. This is what makes Cloudflare
  viable; reopening it reopens the vendor choice.
- **A central IP-updater was rejected.** The alternative was a No-IP hostname per
  router plus a CronJob here pushing addresses into Gateway locations — no
  hardware anywhere. Will's reasoning, verbatim:

  > I'd rather not have this infrastructure dependent on me actively clicking a
  > confirmation every 30 days. If I forget to do that, everything gets torn
  > down. And it looks like it's only 1 hostname on the free plan — so I'd be
  > creating an account for each router. No thanks — I'd rather manage running a
  > small device/server at each house. I have a few spare ones on hand, I can
  > wire them with Tailscale and monitoring. More up-front work but also more
  > scaleable.

- **Public resolver first, boxes as they earn one.** All four routers get a
  conservative public filtering resolver immediately; a house gets a box when it
  needs real control. Build and prove **one** box before deploying any.
- **Per-house exceptions matter a lot** to Will — that is the whole justification
  for the boxes, and it is why the box software is AdGuard Home rather than
  CoreDNS.
- **AdGuard Home boxes run declarative with UI overrides allowed**
  (`mutableSettings = true`): git settings merge on start and win, a web-UI change
  persists until the next rebuild. Chosen so an urgent unblock can happen while
  on the phone, without the four boxes drifting apart.
- **The DoH subdomain is stored SOPS-encrypted and consumed via the HelmRelease's
  `valuesFrom`.** See the wrong turn below for what this replaced and what it
  costs.
- **The public resolver is AdGuard's**, `94.140.14.14` / `94.140.15.15`. Chosen
  for low false-positive rate over maximum blocking: at a house with no box there
  is no way to allowlist anything, and a breakage ends with someone switching
  their DNS back to automatic and losing all filtering. It is also the only
  free no-account candidate that actually blocks ads.
- **Two Gateway locations exist**, `vulcanus` and `boston`, rather than the one
  the earlier plan assumed. Per-location DoH endpoints give per-site attribution
  in Gateway's logs for free.
- **The CGPS fork stays**, and the image-tag automation is to be offered upstream
  as a pull request rather than carried privately forever.
- **Will has remote access to his brother's router**, so the DHCP secondary can
  be changed without anyone being on site. That is what makes the CoreDNS change
  safe to attempt at all.
- **A third Gateway location, `mobile`, for the config profile.** A roaming
  iPhone pointed at the `vulcanus` endpoint files its queries under that site and
  destroys the per-site attribution the locations exist for.

  **It has to be created through the API, not the dashboard.** The UI prefills
  the source IPv4 of whatever network you are sitting on, and refuses with *"A
  network overlapping the requested IP range is in use by another location
  belonging to this account"* when one of your own locations already holds that
  address — which `boston` does, since that is where you create things from.
  Note the wording: Cloudflare's other variant of this error says "another
  gateway customer" and means a stranger on your ISP range. This one means you.

  A source IP is not needed at all here. `name` is the only required field, and
  `networks` is documented as taking effect *"only if it is non-empty and the
  IPv4 endpoint is enabled for this location"*, so a location created without
  networks is attributed purely by its own DoH hostname — which is all a roaming
  phone ever uses.

## The coverage model

A relative's iPhone gets the full Gateway ruleset from the config profile no
matter what their router does. Phones are where most family browsing happens and
where support calls come from, so the router only covers the residue.

| What | How | Exceptions work? |
|---|---|---|
| `vulcanus` LAN (brother's house) | CoreDNS → Gateway DoT | Yes |
| Cluster and infra VMs | Same CoreDNS | Yes |
| Will's own LAN (`boston`) | Router → box → Gateway DoH | Yes, plus local overrides |
| Tailnet devices, on and off LAN | Headscale global nameserver → Gateway DoH | Yes |
| Family iPhones, anywhere | `.mobileconfig` → Gateway DoH | Yes |
| A house with an AdGuard Home box | Router → box → Gateway DoH | Yes, plus local overrides |
| A house without one | Router → public resolver | No |

## Where this stands

Four commits on `filtering-dns`, plus one of Will's:

1. **Recovered the stashed work.** The app was built April–May 2026 and never
   committed; all of it lived in `stash@{0}`, referenced by nothing and one
   `git stash drop` from being lost. Nine files, committed unmodified so later
   edits read as a diff against the original design.
2. **Dropped the post-build substitution scaffolding**, for the reason below.
3. **Wrote this spec.**
4. **Fixed the allowlist and hardened the profile server** — two defects that
   would each have presented as the feature quietly not existing. See below.

Will has since created the Zero Trust token, renamed the secret to
`secret.sops.yaml`, and created **two** Gateway DNS locations, `vulcanus` and
`boston`. The `adblock.mobileconfig` inside the secret already carries the
`vulcanus` DoH endpoint.

Nothing is wired up. `kubernetes/apps/cloudflare-gateway/` is not in
`kubernetes/apps/kustomization.yaml`, and `coredns.yaml` is untouched — so
merging this branch to `main` today deploys nothing and breaks nothing. Wiring
the app in is safe and independent of the CoreDNS work: it would deploy the
profile server and fix the homepage tile. The CoreDNS change is the one that
must not reach `main` before the Secret holds a real subdomain.

## Wrong turns, recorded so they are not repeated

**The stash's substitution mechanism was unsafe and is gone.** It set
`postBuild.substituteFrom` on the *infrastructure* Kustomization to inject
`${CLOUDFLARE_DOH_SUBDOMAIN}` into `coredns.yaml`. Flux runs envsubst over every
manifest under the Kustomization's path, and `./kubernetes/infrastructure`
carries 45 `$` occurrences that are not variables — `{{ $value }}` and
`{{ $labels.* }}` throughout `prometheus-rules.yaml`, `"$1"` in the version-skew
`label_replace`, `extract: '$version'` in five ImagePolicies, and `$ORIGIN` /
`$TTL` in CoreDNS's own authoritative zone file. It would have blanked every
alert annotation, stopped every ImagePolicy extracting a version, and corrupted
the zone file of the server it was added to configure — silently, with Flux
reporting healthy throughout.

**`valuesFrom.targetPath` was evaluated as the fix and rejected.** It would have
kept the Corefile in plaintext and injected only the hostname, but
helm-controller has open bugs merging `values` and `valuesFrom` when `targetPath`
contains an array index (fluxcd/helm-controller#281, fluxcd/flux2#2330), and
`targetPath` parses `--set`-style, which mangles the multi-line `configBlock`.

**The chosen design's cost, accepted:** Helm replaces lists rather than merging
them, so the entire `servers:` block has to live in the encrypted Secret. The
Corefile's diffs become ciphertext churn and stop being reviewable. Weighed
against the subdomain being a usable open resolver for any reader of this public
repo — it grants no account access, but burns query quota and pollutes the 24h
logs.

**Do not branch this work from the stash's base commit `1e282ca`.** That is where
the stash applies cleanly, and it is 312 commits behind: `coredns.yaml` alone has
moved 104 insertions and 16 deletions since, including the
`status.immortalkeep.com` carve-out and chart 1.47.0. Recovering there yields a
stale Corefile that no back-merge is permitted to repair. The branch is based on
`main`; only the nine new files came across verbatim.

**The allowlist could never have worked, and failed silently in two ways at
once.** The CronJob mounted the ConfigMap's `allowlist.txt` read-only at
`/app/allowlist.txt` — exactly the path `download_lists.js` unlinks and rewrites
at the start of every run. `unlink` against a bind-mounted file returns `EBUSY`,
so `npm start` would have aborted before touching Cloudflare; and had it
succeeded, the download would have replaced the mounted file seconds later. The
file was empty, so nothing was ever exempted and nothing failed loudly.
Upstream's actual mechanism is the `ALLOWLIST_URLS` env var, which *replaces*
the recommended lists rather than extending them — hence the twelve URLs now
repeated verbatim in `blocklist-sync-cron-job.yaml` with the household's own file appended,
served over HTTP by the profile pod.

**Two things in the app read as removable and are not.** `command:` overrides an
ENTRYPOINT that ignores its arguments, writes a crontab and runs `crond` in the
foreground forever; under `concurrencyPolicy: Forbid` that Job never completes
and every later run is skipped in silence. And the `.mobileconfig` MIME type in
the nginx config is what makes iOS offer the profile at all — nginx's stock
`mime.types` has no entry for it. Both now carry comments saying so.

**The sync CronJob runs as root and that is deliberate.** Its image is
`FROM node:alpine` with no `USER`, `WORKDIR /app` owned by root, and npm runs
scripts from the package root regardless of `workingDir`, so the lists it
downloads have nowhere to go but a root-owned directory. `runAsNonRoot` or
`readOnlyRootFilesystem` there yields a job that cannot write its own inputs;
an `emptyDir` over `/app` shadows the application code. Capabilities are dropped
regardless. The real fix is a `USER` and a writable working directory upstream —
worth bundling into the same pull request as the tag automation.

## Verified on 2026-09-10

- `stash@{0}` exists, based on `1e282ca`, 13 files. Branch `cloudflare-gateway`
  and its worktree have zero unique commits — a bookmark for the stash's base,
  prunable now that the content is committed.
- `kubernetes/apps/cloudflare-gateway/secret.yaml` still decrypts against the
  three age recipients in `.sops.yaml` unchanged, so the account ID, API token
  and signed `.mobileconfig` inside it survive. **Not** verified by decrypting it
  — only by comparing recipients. The token dates from April 2026 and may have
  been revoked.
- `.sops.yaml`'s creation rule matches `[^/]+\.sops\.yaml$` only. `secret.yaml`
  predates that convention and does not match, so it is not auto-encrypted on
  edit. Any new secret must be named `*.sops.yaml`; this one is worth renaming to
  `credentials.sops.yaml` to match `cloudflare-ddns/`.
- `flux-customizations` Kustomization exists with SOPS decryption configured, as
  does `infrastructure`.
- **`treefmt` reformats `secret.yaml`** — it rewrote the `sops:` block from 4-space
  to 2-space indent while leaving every ciphertext value byte-identical. Harmless
  to SOPS, whose MAC covers values rather than layout, but it will fight with
  sops' own output format and produce noise. Not investigated further.
- `*.immortalkeep.com` in the CoreDNS zone file answers `192.168.0.203`, so
  `dns.immortalkeep.com` already routes to the internal ingress. No carve-out is
  needed; the tile 404s only because the app is undeployed.
- `CronJobNotSucceeding` keys on `kube_cronjob_info`'s schedule label rather than
  on names, so the sync job is alerted on automatically once deployed.
- **A tightened Cloudflare limit would be noticed, in about 26 hours.** Traced
  through the upstream code on 2026-09-11: a 400 from the API fails
  `response.ok`, `fetchRetry` rethrows once its attempts are exhausted,
  `createZeroTrustListsOneByOne` logs the list name and rethrows, and the
  top-level `await` in `cf_list_create.js` rejects, so node exits non-zero, the
  `npm start` `&&` chain stops and the Job fails. A partial failure — eighty
  lists created, then the cap — throws the same way.

  What this does *not* do is stop filtering. A failed sync leaves the existing
  lists and the Gateway policy in place, so the blocklist merely goes stale and
  `dns-canary` keeps passing. That is the correct severity, and it means the
  canary and the CronJob alert cover genuinely different failures: the canary
  catches filtering that has stopped, the CronJob alert catches a blocklist that
  has stopped being maintained. Neither substitutes for the other.
- `kubernetes/apps/kustomization.yaml` sets `namespace: apps`, so the CronJob
  reaches the profile Service as `http://cloudflare-gateway-profile/`.
- **The list ceiling is 300 lists × 1,000 entries = 300,000 domains**, measured
  against the account on 2026-09-11. 1,001 entries is rejected with *"list size
  is limited to 1000 items"*; the 301st list is rejected with *"2017 Maximum
  number of lists reached"*. Both probes cleaned up after themselves.

  **Cloudflare's own documentation is wrong about this.** `account-limits`
  states "Lists: 100", which would make the ceiling 100,000. The account
  enforces 300. The 1,000-entry figure is right, and the rejection of 1,001
  confirms a Standard plan, since Enterprise is documented at 5,000.

  So `CLOUDFLARE_LIST_ITEM_LIMIT: "300000"` in the recovered CronJob is exactly
  correct and stays. Earlier revisions of this spec called it speculative and
  invented, and three commit messages repeat that. They were wrong: the value
  came from CGPS's README, which had measured the real ceiling, and the doubt
  came from trusting vendor documentation over a figure someone had actually
  tested. Worth remembering the next time the two disagree.

  Headroom is academic either way: oisd small is roughly 50,000 domains, about
  50 lists, so the sync will sit near 17% of the cap. The one brittleness is
  that 300000 leaves no room for any other list in the account — nothing else
  uses Gateway lists today, and `CronJobNotSucceeding` catches it if that
  changes.
- `kubectl kustomize` builds clean for both `kubernetes/apps/cloudflare-gateway/`
  and `kubernetes/infrastructure/`. Beyond the list measurement above, nothing
  here has been run against a live cluster.
- `origin/review/filtering-dns-base` is at `878bcad`, but the branch's own first
  parent is `95ebb58`. Because `dnsomatic-replacement` landed on `main` before
  the PR was opened, the review diff currently includes two commits belonging to
  that project. Repointing the base ref to `95ebb58` makes the diff exactly this
  project's work.

## What is blocked, and on what

The token now exists, but **an agent session cannot read it.** `sops -d` is
refused by the harness, there is no `~/.config/sops/age` key file and no
`SOPS_AGE_*` in the environment, so the token, the account ID and the
`adblock.mobileconfig` are all unreadable from here. Two consequences: the
measurements below need either Will running them or the token placed somewhere
readable — `.env` already carries the Proxmox credentials and is the established
spot — and **the captive-portal exclusions cannot be added to the profile**,
because editing them means decrypting it.

Separately, `git fetch` and `git push` need `ssh-agent` loaded
(`eval (ssh-agent -c) && ssh-add ~/.ssh/id_ed25519`). Without it the CGPS fork
cannot be synced with upstream either — both its remotes are SSH.

1. **Record the DoH subdomains** for the `vulcanus` and `boston` locations. Only
   `vulcanus` is needed for the cluster-side work; `boston` is for a box.
2. **Measure the real location cap.** Documented as 250 account-wide; third-party
   sources claim 3 on the free plan, and those sources are AI-generated and
   contradict each other. Five locations — home plus four houses — would give
   per-house attribution in Gateway's logs. Fall back to one shared endpoint if
   the cap is really 3.
3. **Confirm Headscale accepts a Gateway DoH URL** in `nameservers.global`. The
   commented-out NextDNS equivalent at
   `kubernetes/apps/headscale/headscale-config-map.yaml:240-242` shows the field
   takes a URL; confirm Cloudflare's form resolves.

## Then, in order

**Ordering is a safety property here, not tidiness.** `vulcanus` is at Will's
brother's house, so these steps run in this order or not at all:

1. **Router DNS at the brother's house**, over the remote access Will has, set
   to exactly:

       Primary DNS:   192.168.0.202    (CoreDNS — unchanged)
       Secondary DNS: 94.140.14.14     (AdGuard, replacing 1.1.1.1)

   **Only the secondary changes.** An earlier revision of this spec wrote the
   value as "94.140.14.14 / 94.140.15.15", meaning AdGuard's pair, and it was
   read as "put AdGuard in the DNS slots" — which was tried, and takes CoreDNS
   out of the path entirely: `*.immortalkeep.com` then follows the public
   wildcard to the WAN IP, so internal-only hosts have no rule to land on and
   everything else depends on hairpin NAT.

   The secondary's job is narrow. It is the backstop for CoreDNS being *down*,
   including the bootstrap case where CoreDNS restarts while DNS is already
   broken and cannot resolve its own Gateway upstream hostname. Moving it off
   `1.1.1.1` means that fallback is still filtered rather than wide open. It is
   not meant to carry normal traffic, and the allowlist does not apply to
   anything that goes through it.

   It must be in place *before* CoreDNS changes, not after.
2. **Confirm the canary fails** against the unmodified setup, for the right
   reason. Baseline taken 2026-09-12 over the WireGuard tunnel: the control
   domain resolves through CoreDNS, and `doubleclick.net` comes back with six
   routable Google addresses, so the run fails saying filtering is not in effect.
   That is correct — nothing is filtering yet. Seeing it fail now is what makes
   its later pass mean something.

   This step earned its keep: running it exposed a false pass in the canary's
   first version, which compared the blocked domain's answer against an
   unfiltered resolver and treated *differing* as success. `doubleclick.net` is
   geo-distributed and rotates, so the two resolvers disagreed constantly while
   nothing was filtered, and the check reported healthy at exactly the moment it
   existed to fail. The assertion is now "resolves to nothing routable" — empty,
   NXDOMAIN, or the `0.0.0.0` sentinel that Gateway and AdGuard both return.
3. **Render the Secret and flip `coredns.yaml`** to `valuesFrom`, deleting its
   inline `servers:` block. One commit.
4. **Watch the canary turn from inconclusive to blocking.** That transition is
   the end-to-end proof, and it is the only one in this project.

Then the rest:

**Cluster side.** Move the `servers:` block of `kubernetes/infrastructure/coredns.yaml`
into a SOPS Secret consumed by `valuesFrom`, with server block 1's forward
pointing at the Gateway DoT hostname. Leave block 2 (authoritative
`immortalkeep.com`) and block 3 (`status.immortalkeep.com`) alone — block 3 is
also the pattern to copy if anything ever needs to bypass the filter.

Swap Headscale's four `nameservers.global` entries for the Gateway DoH URL.
**Do not disturb** the `headscale.immortalkeep.com` split-DNS pin to 1.1.1.1 — it
breaks the bootstrap deadlock in `docs/tailnet.md` and must resolve without the
tailnet. The ConfigMap is mounted un-hashed, so Flux updating it does not restart
the pod: `kubectl rollout restart deployment/headscale -n apps`.

**Close the router-DHCP bypass.** `docs/network.md` hands LAN clients
`192.168.0.202` primary and `1.1.1.1` secondary. Resolvers query secondaries
opportunistically rather than only on failure, so filtering at CoreDNS is
bypassable by design today. Point the secondary at the public filtering resolver
instead. This costs nothing: `1.1.1.1` already fails to resolve internal-only
hosts during a cluster outage, since it answers `*.immortalkeep.com` from the
public wildcard and lands on the external ingress.

**The iOS profile app** is the primary delivery mechanism for family devices, so
it deserves the most care. Hardening and the MIME registration are done. One
fix remains: **captive-portal exclusions**. Earlier versions of this spec called
for a `ProhibitedDomains` key. There is no such key — that was invented. Apple's
`com.apple.dnsSettings.managed` payload excludes domains through an
`OnDemandRules` entry with `Action: EvaluateConnection` and
`DomainAction: NeverConnect`, followed by a catch-all `Connect` rule without
which nothing uses the DoH server at all.

`adblock.mobileconfig.template` in the app directory is the drafted profile,
with `DOH-SUBDOMAIN` to substitute. It also sets `AllowFailover` true — an ad
blocker should fail open, because the alternative is a relative with no working
internet who deletes the profile and is never covered again — and leaves
`ProhibitDisablement` false so a portal the exclusions miss still has an escape
hatch.

`dns.immortalkeep.com` needs **no** CoreDNS carve-out — an earlier version of
this spec said it did, wrongly. The zone file's `*.immortalkeep.com` wildcard
already answers 192.168.0.203, which is the internal ingress the app's Ingress
binds to. The homepage tile 404s only because the app is not deployed; wiring it
into `kubernetes/apps/kustomization.yaml` is the whole fix. Carve-outs are for
names that must reach *public* DNS, like `status.immortalkeep.com`.

**The four routers** get the same conservative public filtering resolver, chosen
for low false-positive rate rather than maximum blocking — without a box there is
no way to allowlist anything, and a breakage ends with someone switching their
DNS back to automatic and losing all filtering. AdGuard's public resolver
(`94.140.14.14` / `94.140.15.15`) is the recommendation: free, no account, no
attribution, immune to dynamic IPs, and the only candidate that actually blocks
ads. Cloudflare's `1.1.1.2`/`1.1.1.3` and Quad9 are the lower-breakage,
no-ad-blocking alternatives.

**The pilot box.** Burn it in on the home LAN first, with one test device pointed
at it — not as the LAN's DNS server, which is CoreDNS's job — then physically
deliver it to the first relative's house. AdGuard Home via NixOS
`services.adguardhome`, `upstream_dns` set to that box's Gateway DoH endpoint,
`bootstrap_dns` set to the public resolver (**required**, and for a real reason:
the box must resolve the Gateway hostname before it can speak DoH to it; the
NixOS module asserts on its absence). No local blocklist subscriptions —
blocking stays central, and adding them fragments the one shared ruleset this
design exists to protect. Bind DNS to the LAN interface and the web UI to the
tailnet or localhost; `docs/network.md` already carries the open-resolver warning
for CoreDNS.

At that house: router primary DNS = the box's LAN address via a DHCP reservation,
or a reboot silently takes the site off filtering. Router secondary stays on the
public resolver, so a dead box leaves the house resolving and still filtered,
just without the exceptions.

Deploy with `nixos-rebuild --target-host` over the tailnet. **Colmena is not
stood up anywhere** — `~/dotfiles` is a flake with three personal hosts and no
Colmena config — and a DNS project should not be the reason a fleet manager gets
bootstrapped; the module ports over unchanged when that lands. The box needs a
tagged-node grant in `policy-config-map.yaml`, where every rule is `src: will@`
today. Whether the remote boxes end up bare metal, VMs or containers is an open
implementation detail; the NixOS module is portable across all three, so it is
not worth deciding now. Architecture still is: NixOS on x86_64 is unremarkable,
on an ARM SBC it is a much larger job.

**Monitoring.** The failure that matters is not "the box is down" but "this house
stopped being filtered", and a dead box looks healthy from inside the house
because the router's secondary answers. Have the box test itself on a timer:
resolve a known-blocked domain through its own AdGuard Home and expect a block,
*and* resolve a control domain and expect an answer. Both halves matter — the
control query alone passes on a completely unfiltered resolver. Push the result
and alert on absence of recent success. Push rather than scrape: the cluster
appears to have no route into the tailnet, since `vulcanus` advertises
`192.168.0.0/24` *to* the tailnet and no Talos node is a tailnet member. Confirm
that before designing around it.

**The blocklist sync.** The recovered CronJob runs
`mrrfv/cloudflare-gateway-pihole-scripts` daily against the Zero Trust token.
The allowlist mechanism is fixed and seeded; exceptions go in the
`allowlist.txt` key of `config-map.yaml`. The loop is git → Flux → next run, so
consider running it more often than daily and write the manual-trigger command
somewhere findable under pressure. The image comes from a personal fork at
`~/repositories/cloudflare-gateway-pihole-scripts` whose only purpose is three
lines adding a `YYYYMMDDHHmmss` tag for the Flux ImagePolicy to sort on; it is
four commits ahead of upstream `c32bbe0` and needs re-syncing, upstream being
active as of 2026-09-03. Consider dropping the fork and pinning upstream by
digest instead — though Will's preference is to keep the fork and offer the tag
automation upstream as a pull request.

**No new alert rule is needed.** `CronJobNotSucceeding` in `prometheus-rules.yaml`
keys on the schedule label of `kube_cronjob_info` rather than on CronJob names,
deliberately, so a newly added CronJob lands in a bucket without being named.
`30 10 * * *` falls in the daily catch-all and pages 26 hours after a missed
success. It covers this job the moment it deploys.

## Out of scope

- **The remaining relatives' routers.** Two are done; Will is handling the rest
  outside this project and they are not tracked here.

Per-person rulesets; boxes two through four until a house earns one; serving
`immortalkeep.com` to the relatives (needs family-user ACL work — that is
`tailnet-multi-user.md`); standing up Colmena; DNS-01 for cert-manager (adjacent,
the DDNS token is one permission short, but it belongs to
`dnsomatic-replacement.md`).

## The prompt

> Continue the `filtering-dns` project on its branch. Read
> `todos/filtering-dns.md` first — the vendor decision, the rejected
> alternatives, and three mechanisms that were removed because they could not
> work are all recorded there, and none of it should be reopened.
>
> The Zero Trust token lives in `kubernetes/apps/cloudflare-gateway/secret.sops.yaml`,
> which an agent session cannot decrypt; I have also put `CLOUDFLARE_API_TOKEN`
> and `CLOUDFLARE_ACCOUNT_ID` in `.env`. My Gateway DoH subdomains are:
> `vulcanus` = <...>, `boston` = <...>.
>
> Start from "What is blocked, and on what": measure the real list and location
> caps against the account rather than trusting the documented numbers, then do
> the cluster-side plumbing — the `servers:` block into a SOPS Secret behind
> `valuesFrom`, and the Headscale nameserver swap.
>
> Two things to keep in mind. Merging to `main` deploys, so the CoreDNS change
> must not reach `main` until the Secret holds a real subdomain — wiring the app
> into `kubernetes/apps/kustomization.yaml` is separately safe and fixes the
> homepage tile. And run `eval (ssh-agent -c) && ssh-add ~/.ssh/id_ed25519`
> first if anything needs to fetch or push.
