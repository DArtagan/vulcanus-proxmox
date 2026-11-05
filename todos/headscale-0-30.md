# Headscale 0.30: OAuth clients, node TLS, the operator and the Talos extension

Three things the tailnet cannot do on Headscale 0.29, all of which the 0.30
changelog unlocks:

1. **OAuth clients** in place of all-access API keys and hand-issued pre-auth keys.
2. **TLS certificates for nodes**, so `tailscale cert` and `tailscale serve` HTTPS
   work.
3. **The Tailscale Kubernetes operator and the `siderolabs/tailscale` Talos
   extension.**

Asked for by the user on 2026-10-22, in these words: *"Moving to use OAuth.
Giving nodes TLS. Using the Tailscale kubernetes operator & talos extension."*
The spec is deliberately light. It exists so that the gate is not forgotten, and
the design is left to the session that picks it up.

## The gate

**Headscale v0.30.0 has to be released.** As of 2026-10-22 the
[changelog](https://github.com/juanfont/headscale/blob/main/CHANGELOG.md) heads
its section `0.30.0 (202x-xx-xx)`, the latest release is 0.29.5 (2026-10-13),
and v0.29.5 is what is deployed.

**The upgrade itself happens without anyone acting.** The `headscale`
ImagePolicy in `kubernetes/apps/headscale/image.yaml` selects
`>=0.29.0 <1.0.0`, so Flux Bot bumps to v0.30.0 within 15 minutes of the tag
being pushed. That commit is the gate opening. Its migration is one-way: it
merges the key tables into `credentials`, and going back means restoring
the nightly SQLite dump with the 0.29 image (see `docs/backups.md`, "Restore a
dump with the application version that wrote it"). Whether to pin below 0.30
until Headplane's compatibility is confirmed is an open question. Nobody has
decided it.

## What to check once 0.30 is running

These come from the changelog's breaking changes. None of them were tested
against 0.30.

- **Headplane.** 0.30 removes gRPC, changes the v1 REST API's errors to
  RFC 7807 and replaces Swagger with OpenAPI 3.1. Headplane 0.6.2-beta.5 holds an
  admin API key. Confirm it still lists and edits nodes.
- **Key formats are retired in 0.32.** API keys in the `prefix.secret` form and
  `hskey-auth-legacy-…` pre-auth keys stop working in 0.32. Any key still
  bcrypt-hashed must authenticate once on 0.30 or 0.31 to be rehashed. Check
  `headscale apikeys list` and `headscale preauthkeys list` and reissue anything
  legacy. Do it while still on 0.30, because unattended upgrades will reach 0.32
  on their own.
- **Config keys.** `grpc_listen_addr` and `grpc_allow_insecure` are now dead.
  The `tls_*`, `acme_*` and `tls_letsencrypt_*` keys are deprecated in favour of
  `tls.*`, and here they are all empty because TLS terminates at the ingress.
  `unix_socket_permission` is already the quoted `"0770"` that 0.30 requires, and
  `derp.paths` is empty.

## The three pieces

### 1. OAuth clients

Per the changelog, 0.30's v2 API authenticates with OAuth 2.0
client-credentials. A client mints short-lived tokens that are limited by scope
and by the tags they may create. An OAuth client secret also works as an auth
key (`tskey-client-…?baseURL=<headscale>`) and mints a tagged key for each node.
This is the "OAuth" meant here. OIDC login for users is a separate thing and is
not part of this spec.

Candidates, not yet evaluated: the operator's credential, which piece 3 needs;
a scoped client in place of Headplane's all-access API key, if Headplane
supports one; and the key issuance that
[tailnet-multi-user.md](tailnet-multi-user.md) has left open.

**This conflicts with a standing decision.** `docs/tailnet.md` and
`~/dotfiles/modules/tailscale` refuse a reusable pre-auth key because it is a
single point of failure for the whole tailnet. An OAuth client secret used as an
auth key is a long-lived credential that can mint keys, limited only by its
tags. Either answer that reasoning explicitly or keep human devices on
interactive registration. Do not overturn it in passing.

### 2. TLS for nodes

To enable this, set `dns.certificates.enabled` and `dns.challenge.provider`.
Headscale publishes the DNS-01 challenge itself, and `cloudflare` is one of its
providers. `immortalkeep.com` is on Cloudflare per
[cert-manager-dns01.md](cert-manager-dns01.md), whose API token is a natural
thing to share or mirror.

**The blocker is the MagicDNS domain.** `dns.base_domain` must be a public domain
we control, and ours is `forge.local`. Renaming it renames every node. On
2026-10-22, 38 references in 10 tracked files used `forge.local`: the headscale
config, the `git.forge.local` extra record and soft-serve, CoreDNS,
`ansible/hosts.yaml`, `pbs-offsite`, four docs, and `TF_VAR_proxmox_api_url` in
`.env`. `~/dotfiles` has more. The new domain must differ from `server_url`'s.
It also has to coexist with the `immortalkeep.com` split-DNS route to CoreDNS if
it sits under that domain, so check that MagicDNS's suffix wins.

**Certificate Transparency publishes node names.** Every issued certificate puts
`<node>.<base_domain>` in public CT logs. Under this repo's security policy,
internal hostnames are semi-sensitive. Raise this with the user before choosing a
domain or enabling the feature.

### 3. The operator and the Talos extension

`docs/tailnet.md` ("What does not work") rules out both. The user now wants both,
so those reasons have to be answered, not ignored:

- **Operator.** The doc rules it out because it authenticates by OAuth against
  `login.tailscale.com` and Headscale had no OAuth. 0.30's changelog says OAuth
  clients let the operator "drive Headscale unchanged". Still unverified: how the
  operator is pointed at a custom control URL, and which v2 endpoints it calls
  beyond the ones Headscale implements. It brings its own tags
  (`tag:k8s-operator`, `tag:k8s`), which will need policy rules and
  `tagOwners`. Its HTTPS Ingress depends on piece 2.
- **Talos extension.** The doc gives three reasons against it. It puts the node's
  host network on the tailnet, not MetalLB IPs or pods. `talosctl` is already
  reachable over WireGuard and the subnet route. And a node's `tailscaled` needs
  `headscale.immortalkeep.com`, which the cluster itself serves. Ask the user
  what the extension should buy, then weigh that against the circular dependency.
  Installing it changes the factory image schematic. That overlaps with
  [talos-terraform-migration-prompt.md](talos-terraform-migration-prompt.md) and
  with CLAUDE.md's required-extensions list.

When this lands, rewrite "What does not work" and the policy table in
`docs/tailnet.md`.

## Order

Do OAuth clients first, because the operator needs them. TLS is independent but
carries the domain rename, and the operator's HTTPS Ingress needs it. The Talos
extension comes last and is the one most likely to be dropped once its reasons
are weighed.

## Prompt to open with

> Headscale 0.30 is out and deployed. Read `todos/headscale-0-30.md` and
> `docs/tailnet.md` first. Start with "What to check once 0.30 is running" and
> confirm Headplane and every API and pre-auth key survived the upgrade. Then
> work through the three pieces in order. Before designing each, read
> Headscale's 0.30 docs for it, and for the operator, Tailscale's docs on custom
> control servers. Raise the reusable-credential conflict in piece 1 and the
> Certificate Transparency exposure in piece 2 with me before deciding either.
