# Migrating off ingress-nginx — context and starting prompt

Written 2026-08-07, during the session that put HelmRelease chart versions under
Flux image automation. Everything here was verified that day. Intended as the
starting context for a follow-up session.

## Why this is not optional

From ingress-nginx's own README:

> **Retiring.** Best-effort maintenance will continue until March 2026.
> Afterward, there will be no further releases, no bugfixes, and no updates to
> resolve any security vulnerabilities that may be discovered.

That date has passed. The controller is unmaintained and will receive **no
further security patches**.

The README is equally blunt about new deployments:

> If you are not already using ingress-nginx, you should not be deploying it as
> it is not being developed. Instead you should identify a Gateway API
> implementation and use it.

This matters more here than for most clusters, because `ingress-nginx-external`
on 192.168.0.201 is an internet-facing entry point. In the same session we
upgraded it off controller **v1.12.0**, which is affected by CVE-2025-1974
("IngressNightmare") — an unauthenticated RCE in the admission controller, fixed
in v1.12.1. That class of bug will now go unfixed.

**InGate, the announced successor, is archived and marked `[EOL]`** with no
releases ever published. There is no drop-in replacement.

## Current state (2026-08-07)

Two releases from one chart, both on **4.15.1 / controller v1.15.1**, defined in
`kubernetes/infrastructure/ingress-nginx-reverse-proxy.yaml`. They share one
`OCIRepository` at `oci://registry.k8s.io/ingress-nginx/charts/ingress-nginx`,
with chart versions bumped in git by Flux image automation.

| Release | IP | Class | Default | Purpose |
|---|---|---|---|---|
| `ingress-nginx-internal` | 192.168.0.203 | `ingress-nginx-internal` | yes | `*.immortalkeep.com` on the LAN and tailnet |
| `ingress-nginx-external` | 192.168.0.201 | `ingress-nginx-external` | no | internet-facing |

**29 Ingress objects** across `apps`, `infrastructure`, `automatic-ripping-machine`
and `flux-system`. Roughly a quarter have an `-external` twin — the same host
declared twice against the two classes. That duplication is worth removing during
the migration rather than reproducing.

Everything is plain HTTP routing with TLS from cert-manager via the
`letsencrypt` ClusterIssuer and the `cert-manager.io/cluster-issuer` annotation.
There is no ModSecurity, no Lua, no snippet annotations, and no auth-url — the
migration surface is genuinely small.

`kubernetes/infrastructure/basic-auth.yaml` and `grafana-auth.yaml` exist; check
whether any Ingress still references them before assuming they are dead.

### The L4 problem is already solved

As of the same session, **no service uses ingress-nginx for TCP/UDP any more.**
Mumble and Syncthing moved to dedicated LoadBalancer Services (.205 and .206,
joining rustdesk on .204), and the `tcp:`/`udp:` blocks were removed entirely.

This matters for the migration: those ConfigMap keys were never part of the
Ingress API, and they are the one thing that would not have had a clean
equivalent. Gateway API's `TCPRoute`/`UDPRoute` would now cover them if you ever
want to route L4 through a gateway again, but nothing depends on it today.

## Where the ecosystem landed

**Gateway API** is the successor, and it is ready in a way it was not a year ago.
Verified against the v1.6.1 CRD manifests (released 2026-07-16): **`TCPRoute`,
`UDPRoute`, `TLSRoute` and `GRPCRoute` are all in the _standard_ channel**, not
experimental. The experimental channel now only adds `XBackend`,
`XBackendTrafficPolicy` and `XMesh`.

So L4 routing is first-class and stable, and there is no longer a reason to
choose between "standards-track" and "can route my non-HTTP traffic".

## Choosing an implementation

**Traefik is ruled out** on the user's direct experience: previously ran it here
and moved to ingress-nginx specifically to get away from it — poor developer
experience, mismanaged 1→2→3 migrations that created significant hassle, and a
UI that delivered nothing. Characterised as a once-useful project damaged by
being turned into enterprise software. Do not propose it again.

There are also four orphaned `traefik*` PVCs in `infrastructure` (128Mi each)
left over from that install. Delete them as cleanup; nothing references them.

Candidates worth evaluating:

- **Envoy Gateway** — full Gateway API including TCPRoute/UDPRoute, CNI-agnostic
  so it does not care that this cluster runs Flannel, actively developed, and
  not a vendor's on-ramp to a paid product. The default recommendation.
- **Cilium** — the strongest Gateway API implementation, but it *is* the CNI.
  Adopting it means replacing Flannel on Talos, which is a much larger change
  and touches the thing that took a Kubernetes component resync to modernise.
  Worth considering only as a deliberate networking project, not as an ingress
  migration.
- **nginx-gateway-fabric** — F5's Gateway API implementation. Closest to
  familiar nginx semantics, which is worth something given nginx config
  knowledge already exists here. Smaller community than Envoy Gateway.

## The prompt

> I want to migrate off ingress-nginx in the vulcanus-proxmox repo. Read
> `docs/ingress-nginx-migration-prompt.md` first — it has the verified state as
> of 2026-08-07 and the constraints.
>
> Start by planning, not editing. I want to decide between Envoy Gateway and
> nginx-gateway-fabric before anything is written, so give me a real comparison
> against this cluster specifically rather than a feature matrix. Traefik is
> ruled out; the reasons are in the doc.
>
> Things to work out in the plan:
>
> 1. How the two ingress classes map onto Gateway API. The internal/external
>    split is currently two controllers with two LoadBalancer IPs; in Gateway API
>    that is probably two Gateways, possibly on one GatewayClass. Confirm which,
>    and whether the duplicated `-external` Ingress objects can collapse into one
>    HTTPRoute with two parentRefs.
> 2. How cert-manager issues certificates for Gateways. It supports Gateway API,
>    but confirm the annotation or Certificate wiring and that the existing
>    `letsencrypt` ClusterIssuer and its HTTP-01 solver still work — the solver
>    currently names `ingressClassName: ingress-nginx-external`, which will need
>    changing.
> 3. A migration order that does not require a flag day. Gateway API can run
>    alongside ingress-nginx on its own GatewayClass and its own MetalLB address,
>    so hosts move one at a time. Propose which host goes first — something
>    low-stakes, `demo` or `speedtest` rather than `plex`.
> 4. Whether the Gateway controller chart is published as an OCI artifact, so it
>    can join the OCIRepository + $imagepolicy automation the other
>    infrastructure charts now use. See `kubernetes/infrastructure/cert-manager.yaml`
>    for the established pattern.
>
> Do not apply cluster changes without checking with me first. DNS is the thing
> to be careful with: `*.immortalkeep.com` wildcards to 192.168.0.203 in the
> CoreDNS zone in `kubernetes/infrastructure/coredns.yaml`, so moving the
> internal entry point means editing that zone and bumping its SOA serial.

## Constraints that carry over

- MetalLB pool is `192.168.0.201-210`; .201–.206 are in use, leaving four.
  **Check the router's DHCP range before widening the pool.**
- CoreDNS on .202 serves `*.immortalkeep.com` internally and is the LAN's primary
  DNS, with 1.1.1.1 as the router's secondary. Editing the zone means bumping the
  serial in `kubernetes/infrastructure/coredns.yaml`.
- Every HelmRelease now sets `install`/`upgrade` `remediation.retries: 3`, so a
  failed chart rolls itself back rather than stalling. Keep that on anything new.
- The repo is public. See the Security Policy in `CLAUDE.md`.
- The user's SSH key is passphrase-protected — they run `git push` and `sops -d`.
