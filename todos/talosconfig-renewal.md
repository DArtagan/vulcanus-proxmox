# Keep the talosconfig certificate from lapsing unnoticed

## What this is about

`talosctl` authenticates with the client certificate in `.talosconfig`, and that
certificate is valid for one year. The current one was issued
2025-11-07 05:34:54 UTC and **expires 2026-11-07 05:34:54 UTC**.

Terraform renews it, but lazily and silently: only a `tofu apply` run within a
month of expiry does it, and nothing announces that the month has begun. Miss
the window and `talosctl` fails with a certificate error until the next apply --
most likely discovered at a moment it is needed, since `talosctl` is the tool for
when the cluster itself is broken.

Found 2026-09-21 during the backups project's escrow work (objective 7 in
[`backups.md`](backups.md)).

## How the renewal works -- verified 2026-09-21

Read from the source of the provider version the repository locks
(`siderolabs/talos` 0.10.0 in `terraform/.terraform.lock.hcl`),
`pkg/talos/talos_machine_secrets_resource.go`:

- The certificate is `module.talos.talos_machine_secrets.main.client_configuration`.
  `.talosconfig` is written from it by `local_sensitive_file.talosconfig`.
  Confirmed by hash rather than by reading the wiring: the certificate in the
  file is byte-identical to the one in state.
- `ModifyPlan` parses that certificate and, if
  `NotAfter.Before(now + 1 month)`, marks all three `client_configuration`
  fields unknown, so the plan shows an update.
- `Update` rebuilds the secrets bundle from state and signs a new client
  certificate with the clock at now: same CA, new certificate. It is local
  cryptography and never contacts the cluster.
- `Read` is a no-op.

| | |
|---|---|
| Renewal window opens | **2026-10-07 05:34:54 UTC** (expiry less one month) |
| Certificate expires | **2026-11-07 05:34:54 UTC** |
| What renews it | any `tofu apply` whose plan includes `module.talos.talos_machine_secrets.main` |
| After expiry | the same condition still holds, so the next apply renews it. Access lapses; it is not lost |

Upstream 0.12.0, released 2026-09-21, carries the same logic unchanged.

**The kubeconfig is a different case.** `data.talos_cluster_kubeconfig` issues a
fresh client certificate on every plan, which is why
`local_sensitive_file.kubeconfig` shows as replaced on every run. The apply of
2026-09-20 made it valid to 2027-09-20, so it lapses only after a full year
without an apply.

## A wrong turn, recorded

The first account of this, in the backups session on 2026-09-21, said the
certificate was "minted once when that resource was created and never renewed",
and that 7 November was the day `talosctl` would lose the cluster. Both were
wrong. The claim was built from the certificate's dates plus the observation that
the apply of 2026-09-20 had not touched it -- which is exactly what a renewal
window that has not yet opened looks like. Reading `ModifyPlan` settled it. The
date is when access lapses *if no apply lands in the final month*, and a single
apply restores it even after that.

## What to do

**1. Once, inside the window, renew it.** Any plain `tofu apply` does it. When
the cluster is the thing that is broken, the targeted form renews without
touching the nodes, because `-target` pulls in dependencies but not dependents,
so none of the `talos_machine_configuration_apply` resources are planned:

```
tofu apply -target=module.talos.talos_machine_secrets.main -target=local_sensitive_file.talosconfig
```

Its scope was measured on 2026-09-21 as a plan rather than inferred from graph
rules. It refreshes `talos_machine_secrets.main`, reads
`data.talos_client_configuration.main` and refreshes
`local_sensitive_file.talosconfig`, and touches nothing else: no guest, no node.

**2. Make the window announce itself.** Recommended: a check in `devenv.nix`'s
`enterShell` that reads the client certificate's `NotAfter` from `$TALOSCONFIG`
and, once inside the renewal window or past expiry, prints the date and the
command above. Entering the shell already runs `treefmt`, so the hook exists. It
fires whenever someone opens the repository's shell, which is when the fix is at
hand. It can be tested now against a throwaway certificate with a short validity,
rather than waiting for the real window.

Its honest limit: it says nothing if nobody opens the shell during the month.
That is acceptable only because a lapse is recoverable with one apply. If that
changes, this needs a real alert.

Alternatives considered, and why not:

- **A calendar reminder.** Needs setting again every year, which is the same
  remembered obligation the problem already is.
- **A Prometheus rule.** The certificate is a *client* certificate, held only on
  the workstation and in tfstate. Nothing in the cluster can see it unless
  Terraform publishes it somewhere, which is more machinery than the problem
  warrants.
- **`talosctl config new`, or any hand-minted talosconfig.** Reverted by the next
  apply, because `local_sensitive_file.talosconfig` writes the copy in state back
  over the file.
- **A longer validity.** The provider exposes no TTL for this certificate.

**3. Write it into [`docs/talos.md`](../docs/talos.md):** how the talosconfig
renews, the targeted command, and the two traps below. That is what this work
leaves permanently true.

## Traps

- **Never `tofu apply -replace=module.talos.talos_machine_secrets.main`** to
  refresh the certificate. It regenerates every CA and secret in the bundle,
  producing a new cluster PKI that the running nodes do not trust.
- **A hand-minted talosconfig is reverted** by the next apply, as above.

## Recovery when tfstate itself is lost

The machine secrets are escrowed in the password manager (2026-09-21, backups
Phase 2). A working talosconfig can be minted from them without tfstate and
without the cluster's help:

```
talosctl gen config piraeus https://192.168.0.200:6443 --with-secrets talos-secrets.yaml -t talosconfig -o talosconfig
talosctl --talosconfig talosconfig -e 192.168.0.190 -n 192.168.0.190 version
```

Verified 2026-09-21: a talosconfig minted this way from the same secrets was
accepted by the control plane. `-e` is required because the generated file
carries `endpoints: []`. All four CAs in the secrets expire 2032-11-03. That is
the escrow's real shelf life, and a separate, distant problem.

## Decisions already made -- user's call, 2026-09-21

- **Escrow the machine secrets, not the talosconfig.** Taken after it was shown
  that the talosconfig is derived from the secrets (its certificate is signed by
  the OS CA inside them) and expires within the year, while the secrets can mint
  a new one at any time until 2032.

## Verification, when it is done

- Inside the window, `tofu plan` shows `client_configuration` changing on
  `module.talos.talos_machine_secrets.main`. This is the first time the
  `ModifyPlan` reading is confirmed live rather than read from source. **The
  matched control is already taken**: the targeted plan above, run on
  2026-09-21 outside the window, reported `No changes`.
- After the apply, `.talosconfig`'s `NotAfter` sits about a year out, its issuer
  is unchanged, and `talosctl version` still reaches the node.
- The warning fires against a throwaway certificate inside its window and stays
  silent against the real one before 2026-10-07: both directions.

## Prompt to open with

> Read `todos/talosconfig-renewal.md`. The client certificate in `.talosconfig`
> expires 2026-11-07 05:34:54 UTC, and the `siderolabs/talos` provider renews it
> only when an apply lands within a month of expiry, with nothing announcing
> that window. Confirm the provider version is still 0.10.0 and the renewal
> logic still reads as the spec describes. Then add the `enterShell` warning,
> tested in both directions against a throwaway certificate. If today is inside
> the window, run the targeted renewal and verify the new `NotAfter`. Finish by
> writing the renewal mechanism and both traps into `docs/talos.md`. Never
> `-replace` the machine secrets resource.
