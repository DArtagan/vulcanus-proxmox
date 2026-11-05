# Narrow what mini-nas can do on vulcanus

## Opening prompt

> mini-nas pulls vulcanus's offsite backups, and the credentials it uses for that
> reach much further than the pull needs. That covers a login shell with TCP
> forwarding, `hold,send` on all of `rpool`, a second key nobody uses, and every
> `will@` tailnet grant. Read `todos/mini-nas-access.md`, then cut it down to
> what syncoid needs, logging before enforcing, and retire the spec.

Written **2026-10-25**, from mini-nas's side of a session planning a Talos VM on
mini-nas (`~/repositories/mini-nas/todos/talos-vm.md`). Will asked for it. All
the facts below were read from vulcanus and mini-nas over SSH that day.

## Why it matters

mini-nas is vulcanus's offsite copy, and it is about to run a Kubernetes cluster
with home services and AI agents. The pull design in `docs/backups.md` keeps a
compromised **vulcanus** away from its backups. Nothing yet keeps a compromised
**mini-nas** away from vulcanus, and today it would arrive with far more than
syncoid needs.

## What mini-nas holds into vulcanus, verified 2026-10-25

**The `mini-nas` account** (`ansible/sanoid.yaml`):

- uid 1001, in group `foreign-backups`, with **login shell `/bin/sh`** and a home
  directory. The password is locked (`passwd -S`: `L`), and the account has no
  sudo.
- `~mini-nas/.ssh/authorized_keys` holds **two keys, and neither carries any
  options**: no `restrict`, no `from=` and no `command=`.

  | Comment | Fingerprint | In ansible | Logins, last 2 days |
  |---|---|---|---|
  | `sanoid@mini-nas` | `SHA256:KwfUdrqkRJ88ez6PVIvWMCqSWz9ac/BxB3xhxuku/Yg` | yes | 200, all from `100.64.0.8` |
  | `root@mini-nas` | `SHA256:8euE4mHaxqEqXcyBnOa0L3Tb7c+r30l2aD3udVcsrPI` | **no** | 0 |

  The file was last modified 2026-01-09, so the second key was presumably added
  by hand. syncoid's private key (`/run/secrets/users/syncoid/ssh_private_key` on
  mini-nas) has the same fingerprint as the first key, but its own comment reads
  `will@thenixbeast`, which suggests it was generated there. **Unverified:**
  whether a copy remains on thenixbeast.

**vulcanus's sshd** (`sshd -T`): `AllowTcpForwarding yes`, `X11Forwarding yes`,
`PasswordAuthentication yes` and `PermitRootLogin yes`, listening on every
address. With no `restrict` on the key, **mini-nas can `ssh -L` through vulcanus
to anything on `192.168.0.0/24`**, which goes around the Headscale policy
altogether.

**ZFS delegation:** `zfs allow rpool` shows `user mini-nas hold,send` as
*descendent* permissions on the whole pool. mini-nas replicates four trees
(`rpool/storage`, `rpool/ROOT`, `rpool/data`, `rpool/backups/restic`; see
`syncoid.commands` in `~/repositories/mini-nas/configuration.nix`), but it can
send any dataset on `rpool`, including ones it doesn't replicate.

**The tailnet.** mini-nas (`100.64.0.8`) is a `will@` node, so every `will@`
grant in `docs/tailnet.md` applies to it. `tailscale debug netmap` on vulcanus
lists these destinations for a source range that includes mini-nas:

- `100.64.0.4`: 22 and 8006 (vulcanus SSH and the Proxmox web UI)
- `192.168.0.105:22` (fileserver) and `192.168.0.108:22` (the restic repository)
- `192.168.0.200:6443` (the Kubernetes API)
- `192.168.0.190`, `.195` and `.196`: 50000 (the Talos API)
- `192.168.0.202:53`, `192.168.0.203:80,443`, `192.168.0.205:64738` and
  `192.168.0.207:22`

Most of those also need a credential mini-nas shouldn't have. The network path is
open regardless.

syncoid needs SSH to vulcanus, and `zfs send`/`hold` on four trees. Everything
else above is surplus.

## Plan

Each step can be taken on its own. They're in order of payoff for the risk.

1. **`restrict` and `from=` on the syncoid key.** Change it to
   `restrict,from="100.64.0.8,fd7a:115c:a1e0::8" ssh-ed25519 … sanoid@mini-nas`.
   `restrict` turns off port, agent and X11 forwarding and the pty, which closes
   the route into the LAN. syncoid runs non-interactive commands, so it shouldn't
   need a pty. Check that the next hourly run of all four `syncoid-vulcanus-*`
   units on mini-nas succeeds.
2. **Remove the `root@mini-nas` key.** First confirm nothing uses it over a longer
   window than two days: `journalctl -u ssh` for its fingerprint. Then make
   `authorized_keys` declarative in `ansible/sanoid.yaml` (`exclusive: true`, with
   the options above), so a hand-added key can't come back.
3. **Narrow the delegation** to the four replicated trees:
   `zfs unallow -d -u mini-nas hold,send rpool`, then `zfs allow -d -u mini-nas
   hold,send` on each tree. (`-d` matches the descendent grant that exists now.
   Use `-ld` if syncoid also needs the trees themselves; check.) Mirror this in
   ansible. Reading properties and listing snapshots needs no delegation.
4. **Log what syncoid actually runs**, before any forced command. Put a wrapper in
   as `command=` that appends `$SSH_ORIGINAL_COMMAND` to a log and then
   `exec /bin/sh -c "$SSH_ORIGINAL_COMMAND"`. Leave it a week, to cover every
   hourly run of all four jobs, resumes, and the case where there's nothing to
   send. syncoid's commands are shell pipelines (`zfs send … | lzop | mbuffer`;
   `lzop`, `mbuffer`, `pv` and `zstd` are all installed), so the allowlist has to
   come from what's observed, not from reading syncoid's source.
5. **Enforce the allowlist.** Swap the wrapper for one that matches the logged
   command shapes against the four trees and refuses everything else. At that
   point the shell can become `/usr/sbin/nologin`, since a forced command doesn't
   use the login shell for anything but the `-c`. Check that this holds with the
   wrapper you write. Alert on refusals, because a refusal means syncoid changed
   or someone is probing.
6. **Tag mini-nas on the tailnet.** Give it `tag:mini-nas`, granted
   `tag:vulcanus-subnet:22` and nothing else on vulcanus's side, plus whatever the
   restic push in the mini-nas spec ends up needing. `will@ → tag:mini-nas` keeps
   your devices reaching it.
   - mini-nas re-registers with `--advertise-tags` (its autoconnect script is in
     `~/repositories/mini-nas/modules/tailscale`), which needs a `tagOwners`
     entry.
   - **Check before cutting over:** mini-nas offers an exit node, and a tagged
     exit node needs its own approval and a grant for `autogroup:internet` in
     Headscale's policy.
   - PBS #2 runs on mini-nas as its own tagged tailnet node
     (`modules/pbs_offsite` there), so its grant to PBS 8007 is separate and
     shouldn't need changing. It isn't in `docs/tailnet.md`'s node table yet.

## Noticed, and not part of this

vulcanus's sshd allows password authentication and root login on every address.
That deserves its own look.

## When it lands

Write the arrangement into `docs/backups.md`, under "Replication is a pull, not a
push", and the tag and grant into `docs/tailnet.md`. Then delete this spec and its
entry in `todos/README.md`, and log the slug `mini-nas-access`.
