# Every certificate renews through the public IP, including the internal-only ones

## Opening prompt

> cert-manager has a single HTTP-01 solver, so all 24 certificates renew only
> if Let's Encrypt can reach port 80 through the public A record. That includes
> fourteen hostnames that are otherwise internal-only — the solver names the
> *external* ingress class, so each of them is briefly served publicly on every
> renewal. Read `todos/cert-manager-dns01.md`. DNS-01 removes the dependency
> and unlocks a wildcard; the viability question that would have killed it is
> already answered.

Verified **2026-09-11**.

## What exists

`kubernetes/infrastructure/cert-manager.yaml` — one `ClusterIssuer`, one solver:

```yaml
kind: ClusterIssuer
metadata:
  name: letsencrypt
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    solvers:
      - http01:
          ingress:
            ingressClassName: ingress-nginx-external
```

cert-manager runs in the **`infrastructure`** namespace, not `cert-manager` —
which is where a `ClusterIssuer`'s secrets must live, since it reads them from
cert-manager's cluster resource namespace. Chart is an `OCIRepository` pinned
`>=1.21.1 <2.0.0`.

24 `Certificate` objects, all Ready on 2026-09-11.

## The defect

HTTP-01 means Let's Encrypt must reach
`http://<host>.immortalkeep.com/.well-known/acme-challenge/…` from the public
internet. That needs the public A record correct, port 80 forwarded, and
`ingress-nginx-external` healthy — every 60 days, for every certificate.

**The part that is not obvious: this applies to internal-only services too.**
The solver names `ingress-nginx-external`, so cert-manager stands up its
challenge ingress there regardless of which class the certificate's own ingress
uses. Fourteen hostnames are internal-only and still hold public certificates:

`alertmanager`, `arm`, `beets`, `filebot`, `grafana`, `headplane`,
`media-toolkit`, `pinepods`, `podgrab`, `prometheus`, `salamander`,
`speedtest`, `syncthing`, `youtube`.

Each is briefly resolvable and served on the public ingress during its own
renewal, and each fails to renew if the public path is broken — so the
internal/external split that `docs/network.md` describes does not hold during
certificate issuance.

## What DNS-01 changes

Validation moves to a `_acme-challenge` TXT record in Cloudflare. No inbound
connection, so renewals stop depending on the public A record, on port 80, and
on the ingress controller being healthy. No transient public exposure of
internal hostnames. And wildcards become possible — HTTP-01 cannot issue them —
so `*.immortalkeep.com` plus the apex would replace 24 certificates with one.

### Verified, because it would have killed the idea

The zone is a CNAME chain: `*.immortalkeep.com` is a CNAME, so the obvious
worry is that a `_acme-challenge` TXT gets shadowed by the wildcard and DNS-01
can never validate.

**It does not.** Measured 2026-09-11 by creating
`_acme-challenge-probe.immortalkeep.com TXT` through the API: it resolved after
about 15 seconds from both `brad.ns.cloudflare.com` and `1.1.1.1`, returning
the planted value rather than following the wildcard. An explicit record means
the name exists, and a wildcard only applies to names that do not. The probe
record was deleted.

Worth knowing for whoever runs this: an immediate query after creation returns
nothing. That is propagation, not shadowing, and it is exactly the observation
that could make someone abandon a working approach. cert-manager's own
`--dns01-recursive-nameservers` self-check exists for this and should be left
on.

### The token

cert-manager needs `Zone - DNS - Edit` and `Zone - Zone - Read` — precisely the
*Edit zone DNS* template, the same scope `cloudflare-ddns` already uses. Nothing
is missing from what this account can already issue.

**Use a separate token and Secret**, in `infrastructure`. Not because the scope
differs but so revoking one does not silently break the other; and a
`ClusterIssuer` cannot read the `cloudflare-ddns` Secret in `apps` anyway.

## Why this is worth doing before the ingress migration

`todos/ingress-nginx-migration-prompt.md` already carries this coupling as a
sub-task — the solver names `ingressClassName: ingress-nginx-external`, which
the Gateway API migration has to rework. Landing DNS-01 first deletes that
question instead of answering it, and removes one moving part from the riskiest
item on the list: replacing the internet-facing entry point on a controller
that no longer gets security patches.

## Plan

Nothing is at stake until the last step. The existing issuer keeps working
throughout, in the same run-both-then-switch shape the DDNS cutover used.

1. Create a Cloudflare API token, *Edit zone DNS* template, scoped to
   `immortalkeep.com`, no expiry. SOPS it into `infrastructure` as
   `cloudflare-api-token.sops.yaml`. The `sops-encrypted` pre-commit hook will
   refuse it if it is not encrypted.
2. Add a **second** `ClusterIssuer`, `letsencrypt-dns01`, alongside the
   existing one. Nothing references it yet.
3. Point one low-stakes internal ingress at it — `youtube` or `podgrab` — by
   changing its `cert-manager.io/cluster-issuer` annotation. Confirm the
   challenge TXT appears and is cleaned up, and that the certificate issues.
   A failure here costs one internal service its certificate, and rolling the
   annotation back reissues via HTTP-01.
4. Decide wildcard or per-host. A wildcard collapses 24 certificates into one
   and stops leaking the service list through certificate transparency logs,
   which currently publishes every internal hostname. It also means one
   failure affects everything, and `demo`/apex still needs its own entry since
   a wildcard does not cover the apex.
5. Migrate the rest, then delete the HTTP-01 solver and confirm renewals still
   work with port 80 closed from outside — the check that proves the
   dependency is actually gone rather than merely unused.
6. Fold into `docs/network.md`, which currently says nothing about how
   certificates are issued.

## Adjacent, not part of this

- Certificate transparency logs already publish all 24 hostnames, including
  every internal-only one. A wildcard would stop *future* leakage; what is
  already logged is public permanently.
- `docs/network.md` has no section on certificate issuance at all.
