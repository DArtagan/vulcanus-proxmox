# Git server

[Soft Serve](https://github.com/charmbracelet/soft-serve) runs in `apps` as a
private git server and a mirror of GitHub, so losing GitHub or the account costs
nothing. GitHub stays primary for public work. One user, reachable from the LAN
and the tailnet and nowhere else. Manifests are in `kubernetes/apps/soft-serve/`.

**Forgejo looks like the most likely upgrade path, should a web UI or more
features be needed.** Soft Serve is the smallest thing that does private
repositories, pull mirrors and LFS, and what it gives up is known: no issues,
pull requests, wiki or registry, and no web UI — its interface is a TUI over
SSH, so browsing from a phone means a git client's own viewer. If CI is ever
wanted, Woodpecker suits this cluster better than a Forgejo Actions runner either
way: its Kubernetes backend runs each step as a pod, where an Actions runner
needs docker-in-docker.

## Names

Two, one per protocol:

| Protocol | Name | Address | How it resolves |
|---|---|---|---|
| SSH | `git.forge.local` | `192.168.0.207:22`, a LoadBalancer of its own | a Headscale extra record |
| HTTPS | `git.immortalkeep.com` | the internal ingress, `192.168.0.203:443` | the CoreDNS wildcard |

```bash
git clone git.forge.local:<repo>
git clone https://git.immortalkeep.com/<repo>.git
```

- **SSH has its own address so URLs need no port.**
- **HTTPS is not also on `git.forge.local`**, because no public CA issues for
  `.local` and Headscale does not offer Tailscale's `ts.net` certificates. The
  alternative is a private CA on every device.
- **TLS terminates at the ingress, not in Soft Serve**, which reloads
  certificates only on `SIGHUP` and so would carry on serving a renewed
  certificate's predecessor until it expired.
- **From the internet** the HTTPS name reaches the external ingress, which has
  no rule for it and answers 404.
- **LAN devices without Tailscale cannot resolve `git.forge.local`**: MagicDNS
  exists only on tailnet clients. Every device in [tailnet.md](tailnet.md) runs
  Tailscale. A `git` record in CoreDNS would fix that, but it would shadow the
  HTTPS name, which the wildcard answers.
- **In-cluster**, the `soft-serve` Service in `apps` is the stable address for
  when Flux or CI come to depend on Soft Serve. Soft Serve's own manifests stay
  in this repository, which Flux reads from GitHub, so bringing Soft Serve up
  never needs Soft Serve.

## Access

Nothing is anonymous, and there is no unauthenticated `git://`.

**SSH keys.** The admin keys are `initial-admin-keys` in `secrets.sops.yaml`:
the user's two machines and the mirror job. Adding one is an edit and a pod
restart. Removing one is not enough on its own, because the first boot also
stored the keys in Soft Serve's database:

```bash
ssh git.forge.local user remove-pubkey admin '<key>'
```

**HTTPS tokens.** Git over HTTPS takes any username and an access token as the
password:

```bash
ssh git.forge.local token create --expires-in 1y <name>
ssh git.forge.local token list
ssh git.forge.local token delete <id>
```

**LFS works over HTTPS**, not SSH.

## Mirroring

Every GitHub repository that is **not a fork**, and **every archived one**,
forks included, across `DArtagan` and the three organisations `DynamicMarkdown`,
`birthdays-today` and `green-nearby`, is mirrored as `github/<owner>/<repo>`.
Other forks are excluded because they are mostly upstream history and most of
the size — the `nixpkgs` fork alone is 3 GB.

A mirror carries every branch and tag, new ones included, and syncs every ten
minutes. It follows GitHub exactly, so a branch deleted or force-pushed upstream
is deleted or rewritten in the mirror too; what the mirror preserves is the
repository, should it go.

**The `soft-serve-mirror` CronJob, hourly, discovers and watches.** It imports
any repository in scope that is not yet mirrored, and compares every mirror's
branches and tags with GitHub's. A mirror that stops syncing is otherwise only a
log line, so the job fails — and `CronJobNotSucceeding` reports it within three
hours — when a mirror has drifted from GitHub for over an hour, when an import
fails, or when GitHub lists nothing at all, since an empty list would check
nothing and pass.

**A repository deleted or renamed upstream keeps its mirror.** The mirror is a
backup, and a deletion upstream is precisely when it is wanted. The job reports
it as `kept github/<owner>/<repo>: gone from GitHub or out of scope` and does not
fail. A rename produces a new mirror beside the old one, since Soft Serve does
not follow GitHub's redirect. Soft Serve logs a sync error for each such mirror
every ten minutes, indefinitely; that noise is expected.

### The GitHub token

A fine-grained, read-only token for `DArtagan`: all repositories, Contents read
(Metadata read comes with it). It is `github-token` in `secrets.sops.yaml`.

- **It never appears in a URL.** Git asks for it only when GitHub refuses a
  request, so public repositories never see it, and it is not written into any
  repository's configuration or, from there, into the backups.
- **It covers `DArtagan` only.** GitHub refuses a fine-grained token on any
  other owner's resources, public ones included, so the organisations are listed
  without it. Their repositories are all public; a private one would need a
  token of its own.
- **An expired or revoked token fails the job** at its first step, so it
  surfaces through the same alert.

To rotate it, replace `github-token` with `sops`, then restart the Deployment.
The job's next run proves the new token.

## Backup and restore

Covered by the ordinary `apps` Schedules; see [backups.md](backups.md).

- **The claim**, `soft-serve-data-pvc`, in the nightly full backup: every
  repository, the LFS objects, and the SSH host key, so a restore keeps clients'
  `known_hosts` valid and a rebuild without one does not.
- **The database**, `/apps-sqlite.soft-serve.sqlite`, nightly and in each
  dumps-only slot.

**The repositories are copied live**, so a restored claim is crash-consistent. A
mirror re-fetches from GitHub on its next sync. A repository of your own pushed
to while the backup ran can hold refs pointing past the objects captured, so
after a restore check each one that is not a mirror:

```bash
kubectl exec -n apps deploy/soft-serve -- sh -c '
  for r in $(find /soft-serve/repos -path /soft-serve/repos/github -prune -o -name "*.git" -type d -prune -print); do
    git -C "$r" fsck --no-progress || echo "FSCK FAILED: $r"
  done'
```
