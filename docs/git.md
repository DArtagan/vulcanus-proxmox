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
| SSH | `git.forge.local` | `192.168.0.207:22`, a LoadBalancer of its own | a Headscale `extra_records` entry |
| HTTPS | `git.immortalkeep.com` | the internal ingress, `192.168.0.203:443` | the CoreDNS wildcard |

```bash
git clone git.forge.local:<repo>
git clone https://git.immortalkeep.com/<repo>.git
```

- **SSH has its own address so URLs need no port.** Soft Serve listens on 23231;
  the Service maps 22 to it.
- **HTTPS is not also on `git.forge.local`**, because no public CA issues for
  `.local` and Headscale does not offer Tailscale's `ts.net` certificates. The
  alternative is a private CA on every device.
- **Soft Serve does not terminate TLS itself.** It reloads certificates only on
  `SIGHUP`, so it would carry on serving a renewed certificate's predecessor
  until that expired. ingress-nginx reloads certificates by itself.
- **From the internet** the HTTPS name reaches `ingress-nginx-external`, which
  has no rule for it and answers 404.
- **LAN devices without Tailscale cannot resolve `git.forge.local`**: MagicDNS
  exists only on tailnet clients. Every device in [tailnet.md](tailnet.md) runs
  Tailscale. A `git` record in CoreDNS would fix that, but it would shadow the
  HTTPS name, which the wildcard answers.
- **In-cluster**, the `soft-serve` Service answers on 22 and 80. It is the stable
  address for when Flux or CI come to depend on Soft Serve. Soft Serve's own
  manifests stay in this repository, which Flux reads from GitHub, so bringing
  Soft Serve up never needs Soft Serve.

The Headscale record is read at startup, so changing it needs Headscale
restarted; see [tailnet.md](tailnet.md).

## Access

Nothing is anonymous: `ANON_ACCESS=no-access`, and the unauthenticated `git://`
daemon is off.

**SSH keys.** The admin keys are `initial-admin-keys` in `secrets.sops.yaml`: the
user's two machines and the mirror job. Soft Serve reads that list on every
access check, so adding a key is an edit and a pod restart. Removing one is not
enough on its own, because the first boot also stored the keys in the database:

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

`token create` prints the token on stdout and its "Access token created" line on
stderr, in no fixed order over SSH; a script capturing the token reads stdout
alone.

**LFS goes over HTTPS**, the default. Its uploads are whole objects in one
request, hence `proxy-body-size: "0"` on the Ingress; nginx's default is 1 MB.
LFS over SSH is off and would need git-lfs 3.0 or later on every client.

## Mirroring

Every GitHub repository that is **not a fork, or is archived**, across
`DArtagan` and the three organisations `DynamicMarkdown`, `birthdays-today` and
`green-nearby`, is mirrored as `github/<owner>/<repo>`. Forks are excluded
because they are mostly upstream history and most of the size — the `nixpkgs`
fork alone is 3 GB. The rule lives in `mirror_plan.py`.

Two things keep the mirrors current:

- **Soft Serve syncs every mirror itself**, every 10 minutes (`jobs.mirror_pull`).
- **The `soft-serve-mirror` CronJob, hourly at :17, discovers and watches.** Its
  initContainer asks GitHub's API for the repositories in scope. The main
  container, in Soft Serve's own image because it has git and ssh, imports any
  not yet mirrored and compares every mirror's heads and tags with GitHub's.

**The job is the watch.** A mirror that stops syncing is otherwise only a log
line. The job fails when a mirror's refs differ from GitHub's and GitHub's
`pushed_at` is over an hour old, when an import fails, and when GitHub lists
nothing at all, since an empty plan would check nothing and pass.
`CronJobNotSucceeding` then reports it within three hours. It compares refs
rather than dates because deleting a branch moves `pushed_at` without adding a
commit, and GitHub deletes merged pull-request branches by itself; a date
comparison would call an in-sync mirror stale forever after the first merge.

**A repository deleted or renamed upstream keeps its mirror.** The mirror is a
backup, and a deletion upstream is precisely when it is wanted. The job reports
it as `kept github/<owner>/<repo>: gone from GitHub or out of scope` and does not
fail. A rename produces a new mirror beside the old one: Soft Serve runs git with
`http.followRedirects=false`, so the old mirror stops at GitHub's redirect
rather than following it. Soft Serve logs a sync error for each such mirror every
ten minutes, indefinitely. That noise is expected.

**Every import runs under `timeout`.** In v0.12.2 a failed `repo import` never
returns: `ImportRepository` ends in `return <-repoc, <-done`, and only a
successful clone sends on `repoc`. Each failure also leaves a goroutine blocked
in the server until it restarts.

### The GitHub token

A fine-grained, read-only token for `DArtagan`: all repositories, Contents read
(Metadata read comes with it). It is `github-token` in `secrets.sops.yaml`.

- **It reaches git through a credential helper, never a URL.** The `gitconfig`
  ConfigMap, pointed at by `GIT_CONFIG_GLOBAL` in both Soft Serve and the job,
  answers `https://github.com` from the mounted Secret. Git asks only when GitHub
  answers 401, so public repositories never see the token, and mirror remotes
  stay plain `https://github.com/...` URLs. A token inside a URL would be written
  into each private mirror's git config on the claim, and from there into
  restic.
- **The organisations are listed anonymously.** A fine-grained token has one
  resource owner, and GitHub refuses it with 403 on every other owner's
  endpoints, public ones included. Their repositories are all public, and three
  calls an hour sit well inside the anonymous limit of 60. A private
  organisation repository would need a token of its own.
- **`GIT_TERMINAL_PROMPT=0`** is set in both. There is no terminal, but GitHub
  answers 401 for a repository that is gone, and git must never wait on a
  prompt.
- **An expired or revoked token fails the job**, at its first step, so it
  surfaces through the same alert.

To rotate it, replace `github-token` with `sops`, then restart the Deployment
rather than rely on the kubelet refreshing the mounted file under a running pod.
The CronJob reads the Secret afresh each run, and its next run proves the new
token: the plan step authenticates with it, and the private mirror's refs are
compared against GitHub through it.

## Backup and restore

Covered by the ordinary `apps` Schedules; see [backups.md](backups.md).

- **The claim**, `soft-serve-data-pvc`, in the nightly full backup: every
  repository, the LFS objects, and the SSH host key under `ssh/`, so a restore
  keeps clients' `known_hosts` valid and a rebuild without one does not.
- **The database**, `/apps-sqlite.soft-serve.sqlite`, by a PreBackupPod, nightly
  and in each dumps-only slot. Soft Serve's image runs as root, so the
  PreBackupPod does too: it is the database's owner.

**The repositories are copied live**, so a restored claim is crash-consistent. A
mirror re-fetches from GitHub on its next sync. A private repository pushed to
while the backup ran can hold refs pointing past the objects captured, so after
a restore check each one that is not a mirror:

```bash
kubectl exec -n apps deploy/soft-serve -- sh -c '
  for r in $(find /soft-serve/repos -path /soft-serve/repos/github -prune -o -name "*.git" -type d -prune -print); do
    git -C "$r" fsck --no-progress || echo "FSCK FAILED: $r"
  done'
```

## Versions

The image is pinned to the `0.12.x` line, not a major: Soft Serve is pre-1.0,
where a minor release may break, so crossing one is an edit to `image.yaml`. The
CronJob runs the same image under the same policy. On an upgrade, check whether
the import hang above is fixed; the `timeout` is harmless either way.

The image sets no user and runs as root. The `apps` namespace's `restricted`
warning flags it; `baseline`, which is what is enforced, admits it.
