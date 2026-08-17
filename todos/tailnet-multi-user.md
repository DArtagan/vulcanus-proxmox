# Family on the tailnet

Put other people on the tailnet as their own Headscale users, with a way to
issue and register keys that does not require the admin to be at a terminal.

Stated by the user on 2026-08-17: *"I'd like to start putting my family on the
tailnet, creating users for them and whatnot. To accomplish this I think I'll
need to implement a web UI or something that allows me to issue/register new
keys more easily."* Split out of the subnet-router work deliberately, which
took only the tagging it strictly needed.

## Where things stand

Verified 2026-08-17.

- **Two users exist:** `will` (id 1, created 2024-09-28) and `headplane`
  (id 2, created 2026-02-24, for the admin UI's agent). Everything human is
  `will@`.
- **One tag exists:** `tag:vulcanus-subnet`, on vulcanus only. Named for the
  node rather than the role so a second subnet router cannot inherit its
  `autoApprovers` entry. Introduced by the subnet-router work; no other node is
  tagged.
- **The policy is `will@`-only.** Every rule in
  `kubernetes/apps/headscale/policy-config-map.yaml` has `"src": ["will@"]`.
  **A new user therefore gets no access at all** — not to the internal ingress,
  not to other devices, not even to their own second device. The policy has to
  grow with the first person added, or their devices will register and then
  reach nothing, which will look like a broken tunnel rather than an
  intentional deny.
- **Headplane 0.6.2-beta.5 is deployed** at `headplane.immortalkeep.com`,
  internal-only. It is an admin UI, not a self-service portal — it assumes the
  operator, so it does not answer this on its own.
- **Registration is interactive by design.** `~/dotfiles/modules/tailscale`
  deliberately carries no `--authkey`: *"A reusable Headscale pre-auth key is a
  single point of failure for the whole tailnet, so fresh machines are
  registered interactively."* Whatever gets built should issue **per-user,
  single-use, expiring** keys rather than overturning this.

## What the work is

1. **A user per person**, not shared logins — otherwise ACLs cannot distinguish
   them and the whole point is lost.
2. **A tag scheme.** At minimum something like `tag:family` for their devices,
   so rules are written against a role rather than an enumeration of people.
   Decide whether personal devices stay user-owned (`autogroup:self`-style
   reasoning) or become tagged; headscale 0.29 has performance warnings on
   `autogroup:self`, worth checking before relying on it.
3. **Policy rules for them.** Almost certainly narrower than `will@` — Plex and
   little else. Note that granting `192.168.0.203:443` grants *every*
   `*.immortalkeep.com` HTTP service at once, because the internal ingress is a
   single door and the policy cannot see Host headers. If family access needs to
   be per-service, that is an argument for routing them via the **external**
   ingress instead, which already has a per-host allowlist. Resolve this before
   writing rules; it is the one real design question here.
4. **Key issuance.** Options, unevaluated: extend/replace Headplane, a small
   self-hosted form that shells out to `headscale preauthkeys create --user X
   --ephemeral --expiration 1h`, or OIDC (headscale supports it, which would
   move user management out of headscale entirely and is the most scalable
   answer if there is already an IdP).
5. **Re-tagging.** Server-side with `headscale nodes tag -i <id> -t <tag>`,
   which does **not** force re-registration — the reason vulcanus was tagged
   that way rather than with `--advertise-tags`.

## Watch out for

- **Tagging a node removes it from its owner.** vulcanus needed an explicit
  `tag:vulcanus-subnet:22` rule to keep SSH working. Expect the same for every
  node that gets tagged.
- **The ConfigMap is mounted un-hashed**, so a policy change needs
  `kubectl rollout restart deployment/headscale -n apps`. Flux will report
  healthy without it.
- **`headscale policy check` is not an offline validator** — it connects to the
  server before parsing, so a syntax error is indistinguishable from an
  unreachable server. Validate by starting a throwaway headscale against the
  file; `docs/tailnet.md` has the recipe.
- **Clients pick up a new filter on their next netmap poll**, so "it did not
  work" immediately after applying may just be timing. Confirm with
  `tailscale debug netmap | jq '.PacketFilter'` on the client.

## Prompt to open with

> I want to add family members to my Headscale tailnet as their own users, each
> with their own devices, and I need a less manual way to issue and register
> keys than doing it from a terminal. Read `todos/tailnet-multi-user.md` and
> `docs/tailnet.md` first. Start by settling the question that spec flags as the
> real design decision: whether family devices reach services through the
> internal ingress (one ACL rule grants everything behind it) or the external
> one (already per-host), because that determines the whole shape of the policy.
