# A self-hosted git server: Soft Serve, on the tailnet

## Opening prompt

> Stand up Soft Serve in the cluster as a private git server and a mirror of my
> GitHub repositories. SSH at `git.forge.local`, HTTPS at `git.immortalkeep.com`,
> reachable from the LAN and the tailnet and nowhere else. Read
> `todos/soft-serve.md`. The forge choice, the names, the mirror scope and the
> HTTPS path are all decided — do not reopen them. Start with the open
> verifications, since two of them decide details of the manifests.

Researched and decided **2026-09-26**, in a session that surveyed the options,
reported on them, and interviewed for requirements. Facts below say where they
were checked. Slug `soft-serve`.

## What is wanted

- **Private repositories**, and **mirrors of GitHub** so that losing GitHub or
  the account costs nothing. GitHub stays primary for public work.
- **One user.** LAN and tailnet only; nothing on the internet.
- **Git LFS.**
- **No CI now**, but nothing that rules it out.

## Decisions already made — user's call, do not relitigate

- **The forge is Soft Serve.** Verbatim: *"For the forge, I choose: soft-serve.
  Though add a note to the eventual docs that Forejo looks like the most likely
  upgrade path, should a web ui or more features be needed."* The `docs/` page
  this project leaves behind **must carry that note**.
- **Mirror every repository in scope, discovered automatically**, not a
  hand-kept list. Scope, as chosen:
  - every repository the user owns that is **not a fork**;
  - every **archived** repository, the one archived fork included;
  - every repository of the user's three organisations (`DynamicMarkdown`,
    `birthdays-today`, `green-nearby`).

  So the rule is *not a fork, or archived*. **Forks are otherwise excluded**:
  they are 25 of 92 repositories and ~3.8 GB of 4.4 GB, the `nixpkgs` fork alone
  3 GB, and mostly upstream history.
- **A repository deleted or renamed upstream keeps its mirror.** Syncing stops,
  it is reported, and it is never deleted automatically — the mirror is a
  backup, and a deletion upstream is precisely when it is wanted. A rename
  therefore yields a new mirror beside the old one.
- **Two names, one per protocol.** SSH at `git.forge.local`, HTTPS at
  `git.immortalkeep.com` through the internal ingress. Asked for by name: the
  user wanted the tailnet name to be memorable for connecting. Why HTTPS does
  not go on the same name is under [Network](#network).
- **The cluster will eventually depend on it**, for private config repositories
  Flux pulls and as a CI source for image builds. **Wire none of it now.** Keep a
  stable in-cluster Service. Soft Serve's own manifests stay in this repository,
  which Flux reads from GitHub, so bringing Soft Serve up never needs Soft Serve.

## Why Soft Serve, and what else was considered

Checked 2026-09-26 against each project's releases.

| | Footprint | Web UI | CI | Verdict |
|---|---|---|---|---|
| **Soft Serve** v0.12.2 (2026-08-07) | ~30 MB, Go | none; a TUI over SSH | server hooks only | **chosen**: smallest thing that does private repos, pull-mirrors and LFS |
| **Forgejo** v15 LTS to 2027-07-15; v16 current | ~150 MB, Go | full | Actions, runner separate; Woodpecker native | **the upgrade path** |
| Gitea 1.26 | ~150 MB, Go | full | Actions | Forgejo's near-twin; company-governed, no LTS line |
| GitLab CE | 4 GB minimum, 8 realistic | full | best in class | a third of worker-0 for one user |
| OneDev | 1–2 GB, JVM | full | built in, runs pods | vendor-driven, no registry |

Gogs, gitolite/cgit, Gerrit, SourceHut, Radicle and Tangled were set aside as
niche for this need. Tangled in particular assumes the public ATProto network,
which cuts against tailnet-only.

**What Soft Serve gives up**, so it is known before it is felt: no issues, pull
requests, wiki or registry; browsing a repository from a phone means a git
client's own viewer. If CI arrives, **Woodpecker** is the better fit for this
cluster than a Forgejo/Gitea Actions runner either way: its Kubernetes backend
runs each step as a pod, where those runners need docker-in-docker (a privileged
pod) or rootless DinD with host sysctl changes.

## What exists — verified 2026-09-26

**Upstream**, from `charmbracelet/soft-serve` source at v0.12.2:

- Image `charmcli/soft-serve`, Alpine with `git`, `bash`, `openssh`. Data at
  `/soft-serve`. Ports: SSH 23231, HTTP 23232, stats 23233, git daemon 9418.
- Configured entirely by `SOFT_SERVE_*` environment variables
  (`pkg/config/config.go`): `INITIAL_ADMIN_KEYS` (newline-separated),
  `ANON_ACCESS`, `SSH_PUBLIC_URL`, `HTTP_PUBLIC_URL`, `HTTP_TLS_*`,
  `JOBS_MIRROR_PULL`, `LFS_ENABLED`, `LFS_SSH_ENABLED`, `DB_DRIVER`,
  `DB_DATA_SOURCE`.
- Database: SQLite, `soft-serve.db?_pragma=busy_timeout(5000)&_pragma=foreign_keys(1)`.
  No WAL pragma. Postgres is supported and not wanted for one user.
- `jobs.mirror_pull` defaults to `@every 10m`.
- LFS is on by default **over HTTP only**; `lfs.ssh_enabled` defaults to false.
- `repo import` flags: `--mirror`, `--lfs`, `--lfs-endpoint`, `--private`,
  `--description`, `--name`, `--hidden`. **There is no credential flag.**
- **TLS certificates reload only on `SIGHUP`** (`cmd/soft/serve/serve.go`).

**GitHub**, from `gh` (token holds `repo`, so private repositories are
visible):

| Owner | Repos | Private | Forks | Archived | Size |
|---|---|---|---|---|---|
| `DArtagan` | 92 | 1 | 25 | 8 (7 non-fork, 1 fork) | 4.4 GB |
| three orgs | 1 each | 0 | 0 | 0 | ~100 KB each |

In scope by the rule above: **71 repositories, ~0.53 GB.**

**This repository:**

- `kubernetes/apps/linkding/` is the shape to copy: Deployment, Service,
  Ingress, `openebs-hostpath` PVC, ImagePolicy, SOPS secret, PreBackupPod.
- MetalLB pool `192.168.0.201-210` (`kubernetes/infrastructure/metallb.yaml`).
  In use: .201–.206. **.207–.210 free.** Re-check before use.
- `*.immortalkeep.com` resolves to the internal ingress `.203` from CoreDNS's
  zone file in `kubernetes/infrastructure/coredns.yaml`. Explicit records there
  override the wildcard.
- Headscale's `dns.extra_records` is `[]` in
  `kubernetes/apps/headscale/headscale-config-map.yaml`; `base_domain` is
  `forge.local`.
- The tailnet policy (`kubernetes/apps/headscale/policy-config-map.sops.yaml`,
  summarised in `docs/tailnet.md`) already grants `will@ → 192.168.0.203:80,443`.
- cert-manager's only solver is HTTP-01 on the *external* ingress class, which
  works for internal-only hosts (see `todos/cert-manager-dns01.md`).
- `nginx.ingress.kubernetes.io/proxy-body-size: "0"` has precedent in `beets`
  and `photoprism`.

## Design

### Workload

`kubernetes/apps/soft-serve/`, listed in `kubernetes/apps/kustomization.yaml`:

- **Deployment**, `strategy: Recreate`, since the claim is `ReadWriteOnce`.
  Readiness on TCP 23231.
- **Environment:**
  - `ANON_ACCESS=no-access`;
  - `SSH_PUBLIC_URL=ssh://git.forge.local`;
  - `HTTP_PUBLIC_URL=https://git.immortalkeep.com`;
  - the git daemon off — nothing should speak unauthenticated `git://`;
  - `INITIAL_ADMIN_KEYS` from `secrets.sops.yaml`: the user's public key, plus
    the mirror job's (below).
- **ImagePolicy on the `0.12.x` line.** The project is pre-1.0, where a minor
  release may break, so a minor is a decision rather than an automatic update.
  That makes this one of the policies `todos/version-notification-prompt.md`
  exists to watch.

### Storage and backup

Follows [`docs/backups.md`](../docs/backups.md); nothing new is needed.

- **`openebs-hostpath` claim in `apps`.** K8up is opt-out, so the nightly `apps`
  full Schedule picks it up unchanged, and the coverage check fails it if it
  stops appearing.
- **A PreBackupPod for `soft-serve.db`**, copied from
  `kubernetes/apps/linkding/prebackup-pod.yaml`: `keinos/sqlite3`, `.backup`
  into a `mktemp` file, `test -s`, then `cat`; label `k8up-dump: "true"`;
  extension `.soft-serve.sqlite`; `runAsUser` set to the database's owner.
- **The repositories are copied live by restic**, so a restore is
  crash-consistent. For a mirror that is harmless, since it re-fetches from
  GitHub. For a private repository, a push racing the 01:00 UTC backup could
  leave a ref pointing past the objects captured. After a restore,
  `git fsck` each private repository.

### Network

**SSH: `git.forge.local` → `192.168.0.207:22`.**

- A `LoadBalancer` Service on `.207`, port 22 → 23231, so URLs need no port.
- A Headscale `extra_records` entry: `{name: git.forge.local, type: A, value: 192.168.0.207}`.
  The address is reached through the vulcanus subnet route like every other LAN
  address.
- A policy line `will@ → 192.168.0.207:22`, with its reason, per the policy's
  one-entry-at-a-time rule; and the row in `docs/tailnet.md`.
- **Headscale must actually pick the record up.** A ConfigMap change leaves the
  running pod on its old configuration ("applied is not in effect",
  `docs/kubernetes.md`). Either roll the pod, or use `extra_records_path`, which
  Headscale watches and reloads.
- **LAN devices without Tailscale cannot resolve `git.forge.local`** — MagicDNS
  exists only on tailnet clients. Every device named in `docs/tailnet.md` runs
  Tailscale, so this costs nothing today. If it ever matters, a `git` record in
  CoreDNS's zone gives `git.immortalkeep.com` on the LAN, but it would then
  shadow the HTTPS name, so it is not done by default.

**HTTPS: `git.immortalkeep.com` → the internal ingress → 23232.**

- An `ingress-nginx-internal` Ingress with `cert-manager.io/cluster-issuer:
  letsencrypt` and `proxy-body-size: "0"` for LFS pushes.
- **No DNS record and no policy change**: the CoreDNS wildcard already answers
  `.203`, and the tailnet already reaches `.203:443`.

How the `immortalkeep.com` name works from the tailnet, since it was asked:

1. Headscale's split DNS sends `immortalkeep.com` queries to CoreDNS at `.202`.
2. CoreDNS answers the wildcard with `.203`.
3. The connection crosses the subnet router under the existing `.203:80,443` grant.
4. ingress-nginx routes by hostname.

From the internet the name reaches the *external* ingress, which has no rule for
it, and gets a 404.

**Why HTTPS is not also on `git.forge.local`:** no public CA issues for `.local`.
Headscale does not offer Tailscale's `ts.net` certificates, so the alternative is
a private CA installed on every device.

**Why Soft Serve does not terminate TLS itself**, which was the first choice:
it reloads certificates only on `SIGHUP`. cert-manager renews the Secret every
60 days, and the process would go on serving the old certificate until it
expired — the same trap as a ConfigMap, with a 30-day fuse. ingress-nginx
reloads certificates by itself.

**LFS travels over HTTPS.** Enabling `LFS_SSH_ENABLED` is optional, and needs
git-lfs 3.0 or later on each client.

### Mirroring

- **Soft Serve syncs** each imported mirror on `jobs.mirror_pull`, every 10
  minutes by default. 71 small repositories make that cheap.
- **A CronJob in `apps` does the discovery.**
  - It lists the in-scope repositories with a fine-grained, read-only GitHub
    token. A fine-grained token has exactly one resource owner. That fits
    today, because the only private repository is under `DArtagan` and the
    three org repositories are public, so the orgs can be listed without it. A
    private org repository would need a second token.
  - It runs `repo import --mirror --lfs [--private]` over SSH for any not yet
    present. The job authenticates with its own SSH key, registered through
    `INITIAL_ADMIN_KEYS` or afterwards as a user with create rights. Both keys
    and the token live in `secrets.sops.yaml`.
- **Private remotes carry the token in the clone URL**, because `repo import`
  has no credential flag. The token then sits in each private mirror's git
  config on the claim, and so in restic. Acceptable for a read-only
  fine-grained token covering one private repository today. If a better route
  turns up (a credential helper in the image, say), prefer it.
- **The job is also the watch.** A mirror sync that fails is otherwise only a
  log line. So each run compares every mirror's newest commit with GitHub's
  `pushed_at` and **fails when one is stale** beyond a margin. It also
  **reports**, without failing, repositories that are gone upstream, per the
  decision above. A failing CronJob already reaches Pushover through the
  existing alert rules. That is CLAUDE.md's "verification that outlives the
  session belongs in a rule": nobody has to remember to look.

## Open verifications

Do these first; the first two decide manifest details.

1. **The image's runtime uid** — for the PreBackupPod's `runAsUser`, and for
   whether the claim needs its ownership set.
2. **Does an `extra_records` entry resolve on the iPhone?** MagicDNS extra
   records outside a node name are what this relies on. Test from iOS before
   writing it into `docs/`.
3. **What a mirror does when its upstream disappears** — errors loudly each
   run, or goes quiet? It decides how the CronJob tells "gone" from "stale".
4. **`--lfs` on a repository with no LFS objects** is harmless, so the job can
   pass it unconditionally.

## Closing

What this leaves permanently true goes to **`docs/git.md`**:

- the names and why there are two;
- how to get access (keys, tokens);
- mirroring and its watch;
- backup and restore, including the `git fsck` step;
- **the Forgejo upgrade-path note, as the user asked**.

`docs/tailnet.md` gains the `.207:22` row. Then delete this spec and add the
`docs/project_log.md` entry, in one commit on the branch.

Check before closing:

- `ssh git.forge.local` from a tailnet client off the LAN opens the TUI.
- HTTPS clone and push with a token work from the iPhone.
- One LFS object round-trips.
- A newly created GitHub repository appears as a mirror after one CronJob run.
- The next nightly `apps` backup holds the new claim and the
  `.soft-serve.sqlite` dump, and the coverage check reports them.
