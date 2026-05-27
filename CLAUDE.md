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
