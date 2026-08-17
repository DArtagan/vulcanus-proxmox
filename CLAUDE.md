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
├── apps/             # 23 application deployments (plex, photoprism, mumble, syncthing, headscale, etc.)
├── charts/           # Custom Helm charts
└── flux-customizations/  # Flux webhooks and image automation
```

Each app in `kubernetes/apps/` typically contains a `kustomization.yaml`, a `Deployment`/`HelmRelease`, a `Service`, an `Ingress`, and a `PersistentVolumeClaim`.

### Secrets Pattern

Kubernetes secrets are SOPS-encrypted YAML files committed to the repo. Flux decrypts them using the age/ssh key. To create/edit a secret:
```bash
sops kubernetes/apps/<app>/secret.yaml
```

## Key Operational Notes

- **Increase VM disk:** `qm resize <vm-id> virtio1 +<size>G` on the Proxmox host, then update `openebs_disk_size` in `terraform/main.tf` and run `tofu apply`.
- **Talos upgrades:** Upgrade one node at a time (controlplane first), incrementing minor versions. Use `talosctl --nodes <ip> upgrade --stage --image <factory-image>`.
- **Talos extensions required:** `siderolabs/iscsi-tools`, `siderolabs/qemu-guest-agent` — get images from https://factory.talos.dev.
- **talos-worker won't boot:** Check that a virtual SCSI/cdrom is attached in Proxmox VM config.

### Practices that exist because they were learned the hard way

- **Establish what actually depends on a thing before designing for compatibility with it.** Ask what reads this, and check. The promtail-to-Alloy migration was first planned around reproducing promtail's exact label set, until listing Grafana's datasources showed there had never been a Loki datasource at all — nothing had queried those logs in three years. One command, and it invalidated the whole design premise. Do that check before building for compatibility, not after.

- **When the system is running, measure it rather than reasoning about it.** A claim that can be queried should be. Predicting from a rule's structure that `KubeMemoryOvercommit` could not clear took longer than the query that showed it clearing, and was wrong because it never checked how close the value sat to the threshold. Metric *names* deserve the same treatment: `kube_endpoint_address_available` does not exist, and `kube_endpoint_info` exists as a name while kube-state-metrics emits zero series for it here, because the `endpoints` collector is not in its `--resources`. Confirm the series has data before building on it.

- **A verification step is part of the change and earns the same scrutiny.** State where each check runs from and why the number it reports moves for the reason claimed. Both halves have failed here in one sitting: `kubectl top node` was offered as proof a memory fix worked, when it counts page cache and so reads ~84% on a node with 2 GiB genuinely free; and `nc -zv` was offered against ports the tailnet policy does not grant, which fails from a roaming client no matter how healthy the service. Vantage point matters throughout this repo — LAN, tailnet and in-cluster reach different things, and the tailnet reaches only what [`docs/tailnet.md`](docs/tailnet.md) lists.

The Kubernetes-specific ones live in [`docs/kubernetes.md`](docs/kubernetes.md), which is worth reading before changing a workload. In brief:

- **"Applied" is not "in effect."** A ConfigMap-only change updates the object and leaves running pods on their old config, while Flux correctly reports healthy. It has silently misbehaved twice.
- **Probes are the only thing separating a running container from a serving app.** Default to readiness; be wary of liveness on anything that does long synchronous work.
- **Diff *rendered* manifests, not values files,** when swapping charts. Defaults are where the surprises live.
- **DaemonSet `READY` is computed from schedulable nodes,** so a missing toleration reports full health while covering fewer nodes.
- **Before deleting a stateful workload, inventory what dies with it** — reclaim policy, PVC retention policy, and Flux pruning. This cost ~131 GiB once.

## Documentation Protocol

Two directories, deliberately separated by tense.

**`docs/` — the present system.** How things work as they stand. Descriptive,
not aspirational: if something in `docs/` is not true of the running cluster,
that is a bug in the docs. See `docs/README.md`.

**`todos/` — work not yet done.** Self-contained specs, each with enough
verified context to start a session cold plus a prompt to open with. A file
existing in `todos/` means that work is outstanding. See `todos/README.md`, which
also holds the ordered priority list and guidance on writing a good spec.

**The lifecycle:**

1. Work is identified and written as a spec in `todos/`, capturing what was
   verified, when, and why the approach was chosen.
2. The work is carried out, usually in its own session opened with that spec.
3. Whatever the work leaves behind that is *permanently true* is written into
   `docs/` — the resulting architecture, conventions, operational notes.
4. The spec in `todos/` is deleted. It was scaffolding.

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

## Security Policy

This repository is public. It is intentionally shared to contribute to the community's body of knowledge. However, sensitive values must never be committed in plaintext.

**Rules:**
- **Clearly sensitive values** (passwords, API keys, tokens, private keys, secrets) must always be encrypted with SOPS before committing.
- **Semi-sensitive values** (internal hostnames, IP addresses, usernames, email addresses, domain names, service URLs) should default to SOPS encryption. If you are unsure, raise it for the user's consideration before committing.
- When adding any new value to a config file, stop and ask: could this help an attacker? If yes or maybe, use SOPS.

SOPS-encrypted files are decrypted by Flux at apply time using the age key referenced in `.sops.yaml`. To create or edit an encrypted file:
```bash
sops kubernetes/apps/<app>/secret.yaml
```

## Pre-commit Hooks

Automatically run on `git commit` via devenv: `deadnix`, `flake-checker`, `nixfmt-rfc-style`, `shellcheck`, `statix`, `tflint`, `end-of-file-fixer`, `trim-trailing-whitespace`.
