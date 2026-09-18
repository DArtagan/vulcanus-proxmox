# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Environment

This repo uses [devenv.sh](https://devenv.sh/) for a reproducible dev environment. Enter it with:
```bash
devenv shell
```

This provides: `ansible`, `tofu` (OpenTofu), `flux`, `kubectl`, `k9s`, `sops`, `talosctl`, plus sets `KUBECONFIG` and `TALOSCONFIG` env vars automatically. A `.env` file is loaded via `dotenv.enable = true`.

## Common Commands

**Infrastructure (Terraform/OpenTofu):**
```bash
tofu plan          # Preview changes
tofu apply         # Apply infrastructure changes
```

**Kubernetes:**
```bash
k9s                                         # Interactive cluster explorer
kubectl logs <pod> -n <namespace>           # Pod logs
kubectl debug -n apps -it --copy-to=<debug-pod-name> --container=<container> <pod> -- sh  # Debug a pod
kubectl port-forward -n <ns> <pod> <local>:<remote>  # Port forwarding
```

**Distroless containers:** Loki, Prometheus and node-exporter ship distroless
images and contain no shell, so `kubectl exec … -- sh`, `wget` and `df` all fail
with `executable file not found in $PATH`. Reach their HTTP APIs from a pod that
does have a shell (the Alertmanager pod works), read disk usage from Prometheus'
`node_filesystem_avail_bytes` rather than `df`, or attach a busybox with
`kubectl debug`.

**Ansible (run from `ansible/` dir):**
```bash
# If using fish shell, use ssh-agent first:
eval (ssh-agent -c) && ssh-add ~/.ssh/id_ed25519
ansible-playbook <playbook>.yaml
```

**Secrets (SOPS/age):**
```bash
sops <file>        # Edit encrypted file
```

**Formatting:**
```bash
treefmt            # Format the whole tree
```

## Architecture

### Infrastructure Layers

1. **Proxmox hypervisor** — bare metal, runs all VMs. ZFS storage. Managed via Ansible (`ansible/`) and Terraform (`terraform/`).

2. **Talos Linux Kubernetes cluster** — provisioned by `terraform/modules/talos/`. Three nodes:
   - Control plane: `192.168.0.190` (`piraeus-control-plane-0`)
   - Worker 0: `192.168.0.195` (`piraeus-worker-0`) — primary workload node, 24 GiB RAM / 8 cores, 1 TB OpenEBS disk
   - Worker 1: `192.168.0.196` (`piraeus-worker-1`) — secondary node, 8 GiB / 4 cores, 100 GB OpenEBS disk; hosts the physical optical-drive passthrough for `automatic-ripping-machine` (vmid 911 in Proxmox)

   New nodes MUST be booted from a factory.talos.dev image that bundles the required extensions (see "Talos extensions required" below) — booting a new node from a stock ISO will leave it on a different Talos minor/patch version than the rest of the cluster, which can break Flannel VXLAN pod-to-pod traffic.

3. **GitOps (Flux CD)** — reconciles this repo's `kubernetes/` directory to the cluster. Bootstrapped via `terraform/modules/fluxcd/`. Secrets are SOPS-encrypted with age keys (`.sops.yaml`).

4. **Networking:**
   - CoreDNS at `192.168.0.202` — cluster DNS, also serves `*.immortalkeep.com` internally
   - MetalLB pool `192.168.0.201-210` — load balancer IPs
   - Nginx ingress at `192.168.0.203` — internal ingress for `*.immortalkeep.com`

5. **Storage:** OpenEBS for Kubernetes PVCs, backed by a dedicated disk on the worker VM (`terraform/main.tf`: `openebs_disk_size`). Fileserver at `192.168.0.105` provides NFS/SMB mounts for media.

### Kubernetes Directory Layout

```
kubernetes/
├── cluster/          # Flux Kustomization objects (bootstraps infrastructure & apps)
├── infrastructure/   # Platform components: metallb, openebs, cert-manager, coredns, prometheus, loki, grafana, etc.
├── apps/             # Application deployments (plex, photoprism, mumble, syncthing, headscale, etc.)
├── charts/           # Custom Helm charts
└── flux-customizations/  # Flux webhooks and image automation
```

Each app in `kubernetes/apps/` typically contains a `kustomization.yaml`, a `Deployment`/`HelmRelease`, a `Service`, an `Ingress`, and a `PersistentVolumeClaim`.

### Secrets Pattern

Kubernetes secrets are SOPS-encrypted YAML files committed to the repo. Flux decrypts them using the age/ssh key. Encrypted files are named `*.sops.yaml`: that is what the `path_regex` in `.sops.yaml` matches, and what keeps `treefmt` away from them, since reformatting an encrypted file invalidates its MAC. To create/edit a secret:
```bash
sops kubernetes/apps/<app>/secrets.sops.yaml
```

## Key Operational Notes

- **Increase VM disk:** `qm resize <vm-id> virtio1 +<size>G` on the Proxmox host, then update `openebs_disk_size` in `terraform/main.tf` and run `tofu apply`.
- **Talos upgrades:** Upgrade one node at a time (controlplane first), incrementing minor versions. Use `talosctl --nodes <ip> upgrade --stage --image <factory-image>`.
- **Talos extensions required:** `siderolabs/iscsi-tools`, `siderolabs/qemu-guest-agent` — get images from https://factory.talos.dev.
- **talos-worker won't boot:** Check that a virtual SCSI/cdrom is attached in Proxmox VM config.

### Practices that exist because they were learned the hard way

- **Establish what actually depends on a thing before designing for compatibility with it.** The promtail-to-Alloy migration was planned around reproducing promtail's exact label set until listing Grafana's datasources showed no Loki datasource had ever existed — nothing had queried those logs in three years. Check who reads a thing before building for compatibility with it, not after.

- **Establish what produces an artifact before changing what consumes it.** The mirror of the rule above. The blocklist sync's manifest was rewritten to use its image's new entrypoint before anyone checked how that image is built — a workflow pinned to a fork tag that had not moved in months, so every image it published was old source wearing a fresh timestamp and a plausible-looking revision label. A green build, a current tag and a matching label all agreed, and all three were wrong. Ask what builds an artifact, and from which ref, before trusting what its tag claims.

- **When the system is running, measure it rather than reasoning about it.** A claim that can be queried should be. Predicting from a rule's structure that `KubeMemoryOvercommit` could not clear took longer than the query proving it had, and was wrong regardless. Metric names deserve the same check — `kube_endpoint_info` exists as a name here but kube-state-metrics emits zero series for it, since its `endpoints` collector isn't enabled. Confirm a series has data before building on it.

- **A verification step is part of the change and earns the same scrutiny.** State where each check runs from and why the number it reports moves for the reason claimed. `kubectl top node` was once offered as proof a memory fix worked — it counts page cache, and read ~84% on a node with 2 GiB genuinely free. `nc -zv` was offered against ports the tailnet policy doesn't grant, which fails regardless of service health. Vantage point matters throughout this repo — LAN, tailnet and in-cluster reach different things, and the tailnet reaches only what [`docs/tailnet.md`](docs/tailnet.md) lists. A check that cannot fail is not a check.

- **Ask what is consuming a saturated resource before trying to make it faster.** etcd here has been slow since the cluster was built, and every remedy considered — a SLOG, defragmentation, wider leases — aimed at helping the disk keep up with the load, without ever asking what the load was. Nearly all of it was `openebs-localpv-provisioner` renewing an Endpoints object and a Lease every 2s to arbitrate between one replica and nobody — a quarter of etcd's total writes, removed by two lines of chart values. Profile the demand before buying more supply.

- **A counter means nothing without a control.** Lease expiries during a storage stall read 1,180 and looked like the cause of failing `LeaseKeepAlive` calls — until an ordinary night read 1,224, just as high. They were Events ageing out on a TTL either way; the real signal was failed proposals, elevated only against a known-good baseline. Take the matched sample from a healthy period before calling a number elevated, especially when it confirms what you already suspect.

The Kubernetes-specific ones live in [`docs/kubernetes.md`](docs/kubernetes.md), which is worth reading before changing a workload. In brief:

- **"Applied" is not "in effect."** A ConfigMap-only change updates the object and leaves running pods on their old config, while Flux correctly reports healthy. It has silently misbehaved twice.
- **Probes are the only thing separating a running container from a serving app.** Default to readiness; be wary of liveness on anything that does long synchronous work.
- **Diff *rendered* manifests, not values files,** when swapping charts. Defaults are where the surprises live.
- **DaemonSet `READY` is computed from schedulable nodes,** so a missing toleration reports full health while covering fewer nodes.
- **Before deleting a stateful workload, inventory what dies with it** — reclaim policy, PVC retention policy, and Flux pruning. This cost ~131 GiB once.

## Documentation Protocol

Two directories, deliberately separated by tense, plus an index of what is
finished.

**`docs/` — the present system.** How things work as they stand. Descriptive,
not aspirational: if something in `docs/` is not true of the running cluster,
that is a bug in the docs. See `docs/README.md`.

**`todos/` — work not yet done.** Self-contained specs, each with enough
verified context to start a session cold plus a prompt to open with. A file
existing in `todos/` means that work is outstanding. See `todos/README.md`, which
also holds the ordered priority list and guidance on writing a good spec.

**[`docs/project_log.md`](docs/project_log.md) — work that is finished.** The
slug each project used, when it landed, and the pull request(s) where its
review happened — a project big enough to span several review cycles lists all
of them, oldest first. The one past-tense file in `docs/`, and the registry
that stops a *retired* slug being reused for something new. A slug whose spec
is still open in `todos/` is not retired, however many review cycles it has
already been through.

**The lifecycle:**

1. Work is identified and written as a spec in `todos/`, capturing what was
   verified, when, and why the approach was chosen.
2. The work is carried out, usually in its own session opened with that spec.
3. Whatever the work leaves behind that is *permanently true* is written into
   `docs/` — the resulting architecture, conventions, operational notes.
4. The spec in `todos/` is deleted — it was scaffolding — and an entry is added
   to [`docs/project_log.md`](docs/project_log.md). Steps 3 and 4 belong in one
   commit on the project branch, so the closing change lands inside the review.

A spec phased across months does not have to reach step 4 in one sitting. It can
close and reopen its review at a phase boundary — see "A phased project spans
several reviews" under Project Workflow — as many times as it needs, staying on
steps 1 through 2 throughout. Only the phase that actually retires the spec
triggers steps 3 and 4, and that final entry in `docs/project_log.md` lists
every review the project used, not just the last one.

The point of the split is that the two ages differently. Documentation of the
present system should be corrected whenever reality moves. A work spec is a
point-in-time artefact whose value is highest the day it is written and which
becomes misleading once acted on, so it is removed rather than left to rot.

**Verification that outlives the session belongs in a rule, not a note.** Step 4
deletes the spec, so any check it left pending goes with it — and plenty of
fixes can only be confirmed by days of quiet. Neither directory is the answer:
`docs/` describes the present and `todos/` holds work not yet done, while this is
a question awaiting evidence. Encode it as an alert instead. The control plane
was resized against an apiserver whose restarts could only be shown to have
stopped by watching for a week; `ControlPlaneContainerRestarting` is that watch,
and it reports without anyone remembering to look. This is the same instinct as
"alert on the absence of recent success" in [`docs/README.md`](docs/README.md),
applied to a fix rather than a workload. Where no rule can express it, say so in
the commit message, which is the one artefact that is neither deleted nor
expected to stay true.

When writing either, record **why** and not only **what**. Decisions the user has
already made should be captured verbatim so they are not relitigated, and wrong
turns should be recorded honestly — inheriting mistaken reasoning is worse than
inheriting none.

### How it is phrased

Applies to comments and config as much as to prose here.

- **Present tense: what is, not what changed or what is missing.** No dates, no
  "previously/until now/no longer", no describing the prior state — git carries
  the delta, and a file narrating its own edit history ages into noise. Dates are
  right in `todos/` specs, which are point-in-time by design, and in commit
  messages, which are the delta. Rationale stays, in present tense: what breaks
  without this, and why the obvious alternative was not taken. That last one is
  the exception worth protecting — `CronJobHasNeverSucceeded` explaining why it
  measures from `kube_cronjob_created`, or the `version-skew` rule preferring
  `kube_pod_container_info`, guard against a reader reintroducing the bug. The
  test is whether they plausibly could. The single exception is
  [`docs/project_log.md`](docs/project_log.md), which is past tense by design —
  see [`docs/README.md`](docs/README.md) for why an index of pointers is not a
  file narrating its own edit history.
- **Write it once, and point at it.** A fact belongs in exactly one place;
  everywhere else refers to that place. Duplicating an explanation into a
  manifest comment beside the doc that already holds it doubles the maintenance
  and guarantees the two drift, at which point a reader cannot tell which is
  current. Where the file lives decides what it may say: `docs/` carries the
  explanation, and a comment carries only what a reader of *that file* needs and
  could not infer — most often the warning that stops them reintroducing a bug,
  which is the one thing worth repeating because its whole value is being where
  the mistake would be made. "See `docs/network.md`" is a complete comment.

- **Never expose an option whose other setting is simply wrong.** That is not
  configuration, it is a way to break things plus an untested evaluation path.
  Inline the value and comment why it is fixed; keep only what genuinely differs
  between call sites. Nothing here is written for a hypothetical fork, so "someone
  might want to change it" is not a reason to keep a knob. And once one is gone it
  leaves no trace — an absent setting needs no epitaph.

## Project Workflow

Work of any consequence gets a branch, and that branch is what a review sees. The
branch — not a commit range, not a session — is what defines a project, because
`main` interleaves Flux Bot's commits with everything else and no contiguous
range is ever just one project.

**The slug** is the `todos/` spec filename without `.md`. It names the branch and
appears in commit trailers. New specs drop the `-prompt`, `-spec` and `-context`
suffixes — every file in `todos/` is a spec, so the suffix says nothing. A
*retired* slug — one whose spec has been deleted and logged — is never reused
for a different project; [`docs/project_log.md`](docs/project_log.md) is the
registry. A slug whose spec is still open in `todos/` is not retired, so a
phased project keeps using its own slug across every review cycle it needs —
see "A phased project spans several reviews" below. Work too small to warrant
future notice needs none of this.

**The branch lives in a worktree**, created with `wt switch <slug>`; the
repository root stays on `main`. See [`.config/wt.toml`](.config/wt.toml).

**Commits** carry a `Project: <slug>` trailer where they are substantive.
Roll-forwards and trivial fixes need none, and one commit may carry several. The
trailer is a convenience for finding work on `main`; the branch is the authority.

**Deploying to test.** Flux reconciles from `main`, so nothing is testable until
it is there. Merge the branch into `main` as often as testing requires. **Never
merge `main` back into the branch** — the review diff is computed from the fork
point, so a back-merge drags Flux Bot's commits into it.

**Roll forward.** A change that does not work out gets more commits. Once a
commit is pushed it is never amended, rebased or squashed — `main` is reconciled
by Flux and shared across machines, so rewriting it is not a local matter.
Correcting a commit that has never left the machine is fine; the rule exists to
protect history others have already seen, and to keep the commit series intact as
the thread of development the review follows.

**The review** is a pull request opened after the first commit and before the
first deploy — GitHub cannot open one with no commits between base and head —
based on a
`review/<slug>-base` branch frozen at the fork point. That base never receives
the work, so merging into `main` does not close the PR and its diff stays exactly
this project's total change. Anything computing that diff reads the base from
its ref rather than recomputing it: `merge-base` returns the branch tip once the
work has reached `main`, which yields an empty diff. Comment threads are the review; a session reads,
replies to and resolves them with `tools/review/`.

**Closing** merges the PR into its own frozen base. That is a real merge and
leaves `main` untouched, because the code reached `main` incrementally long
before. The mechanics are the `review-open`, `deploy` and `review-close` aliases
in `.config/wt.toml`. Closing in the browser skips `review-close` and its
check that the work reached `main`, so
`.github/workflows/land-reviewed-work.yml` merges it there instead — which means
closing a review from the browser can deploy to the cluster.

**A phased project spans several reviews.** A `todos/` spec built around
phases, each landing independently over weeks, does not have to carry its
whole history in one pull request. The value of reviewing back to the very
first commit falls as a project runs longer, because most of what an old PR's
diff shows has, by then, already shipped to `main` through repeated `deploy`s
— the diff just does not reflect that, since it is computed from wherever
`review-open` froze the base, not from `main`'s current tip. Closing at a
phase boundary, before the spec is folded into `docs/` and deleted, is a
diff-hygiene checkpoint, not the project's end.

`review-close` applies exactly as it does for a finished project: the branch
must already be fully merged into `main`, and closing deletes the branch, its
worktree, and `review/<slug>-base`, on GitHub and locally. Continue the
project by branching from `main` again under the *same* slug and running
`review-open` once there is a new commit to review — the new
`review/<slug>-base` freezes at the new fork point, so the next PR's diff
covers only what remains rather than the project's entire history to date.
This is not slug reuse: the spec never left `todos/`, so the slug was never
retired, and [`docs/project_log.md`](docs/project_log.md) gets no entry until
it actually is — see the Documentation Protocol lifecycle above.

If a conflict resolved during a merge to `main` changes the project's own work,
make the equivalent edit on the branch as an ordinary commit. Otherwise the
review passes on code that is not what is deployed.

## Security Policy

This repository is public. It is intentionally shared to contribute to the community's body of knowledge. However, sensitive values must never be committed in plaintext.

**Rules:**
- **Clearly sensitive values** (passwords, API keys, tokens, private keys, secrets) must always be encrypted with SOPS before committing.
- **Semi-sensitive values** (internal hostnames, IP addresses, usernames, email addresses, domain names, service URLs) should default to SOPS encryption. If you are unsure, raise it for the user's consideration before committing.
- When adding any new value to a config file, stop and ask: could this help an attacker? If yes or maybe, use SOPS.

See [Secrets Pattern](#secrets-pattern) above for how encrypted files are named, decrypted, and edited.

## Formatting

Every formatter in the repo is a [treefmt](https://treefmt.com/) formatter,
configured in the `treefmt` block of `devenv.nix`. `treefmt` formats the tree;
entering the devenv shell runs it too, and it runs again on `git commit`.

`nixfmt`, `deadnix` and `statix` cover Nix; `yamlfmt` YAML; `ruff-format`
Python; `jsonfmt` JSON; `taplo` TOML. Terraform goes through a wrapper that
pipes `tofu fmt` into a `sed` leaving `=` un-aligned, in one pass so each file
is written at most once — a second write bumps the mtime and trips
`--fail-on-change` in the commit hook. Everything else (`*.j2`, `*.cfg`,
`*.conf`, `*.md`, `*.txt`, `*.yaml.tmpl`) gets a `whitespace` formatter that
trims trailing whitespace and ends files on one newline.

Helm chart templates under `kubernetes/charts/*/templates/` are Go templates
rather than YAML, so `yamlfmt` alone skips them; see the `treefmt` block in
`devenv.nix` for the full exclude list and why each entry is there.

`tflint` and `shellcheck` also run on `git commit`. They are linters, so they
report rather than rewrite and stay outside treefmt.

`sops-encrypted` runs there too, and refuses any `*.sops.yaml` that is not
encrypted — either missing its `sops:` block entirely, or carrying a plaintext
value under `data`/`stringData` that was added by hand after encryption. It is a
commit hook rather than a formatter because the repository is public and a
plaintext secret is exposed the moment it is pushed; rewriting history does not
unpublish it.
