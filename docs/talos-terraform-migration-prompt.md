# Bringing Talos and Kubernetes versions under Terraform — context and prompt

Written 2026-08-07, after discovering that three Kubernetes components had been
frozen since 2023 with nothing tracking them. Intended as the starting context
for a follow-up session. Verified that day.

## What went wrong, and why it went unnoticed for three years

`kube-proxy` was running **v1.27.7** against a **v1.35.0** control plane — eight
minor versions of skew, where Kubernetes supports one. Alongside it,
`kube-system` CoreDNS was on **v1.10.1** and flannel on **v0.22.1**. All three
dated to roughly October 2023.

The cause is that Talos manages Kubernetes through **three separate mechanisms**,
and only one of them is touched by the upgrade path this repo had been using:

| Component | Managed as | Updated by |
|---|---|---|
| kube-apiserver, controller-manager, scheduler | static pods from machine config | `talosctl upgrade-k8s`, or machine config edits |
| kubelet | Talos OS | `talosctl upgrade` |
| **kube-proxy, flannel, CoreDNS, associated RBAC** | **bootstrap manifests** | **only `upgrade-k8s`'s manifest sync** |

Upgrading Talos itself and editing machine config moves the static pods and the
kubelet. The bootstrap manifests are applied once at bootstrap and then refreshed
**only** when `upgrade-k8s` runs. `docs/talos.md` and `CLAUDE.md` both describe
the OS upgrade path; neither mentions `upgrade-k8s`. So the manifest set silently
stopped moving in 2023 while everything else advanced.

The drift also hid itself. `talosctl upgrade-k8s` takes the **lowest** component
version as the cluster's starting point, so with kube-proxy at 1.27 it refused
outright:

```
automatically detected the lowest Kubernetes version 1.27.7
unsupported upgrade path 1.27->1.35 (from "1.27.7" to "1.35.0")
```

It was reasoning about a cluster that did not exist — assuming it had to walk the
whole control plane forward from 1.27, which would have meant downgrading the
apiserver. `--from 1.35.0` overrides the detection and lets it re-render the
manifests at the real version.

### How it was fixed (2026-08-07)

```bash
talosctl --nodes 192.168.0.190 upgrade-k8s --from 1.35.0 --to 1.35.0
```

Which moved kube-proxy to v1.35.0, flannel to v0.27.4, CoreDNS to v1.13.2, and
dropped the `install-cni` sidecar. Verified afterwards: 93 pods before and after,
all three nodes Ready, and cross-node pod-to-pod traffic confirmed working over
the new flannel VXLAN — the risk `CLAUDE.md` warns about.

**This was done by hand. Nothing in git records that it happened or what version
the cluster is meant to be at.** That is the gap this document is about.

## Current Terraform state

`terraform/modules/talos/main.tf`, provider `siderolabs/talos` **0.10.0** pinned
in `terraform/main.tf`.

- `data.talos_machine_configuration` sets `talos_version` but **no
  `kubernetes_version`**, so the Kubernetes version is implicit everywhere.
- Cluster bootstrap uses `talos_machine_bootstrap`.
- Nodes are configured with `talos_machine_configuration_apply`.
- The installed Talos version is set via `machine.install.image` in
  `config_patches`, per node, in `terraform/main.tf`.

Nothing in Terraform expresses a desired Kubernetes version, so nothing can
detect that the cluster has drifted from one.

## What newer provider versions offer

Repo is on **0.10.0** (2025-12-23). Current stable is **0.11.0** (2026-04-27);
0.12.0 is in alpha. Resources available in the provider's `main`:

**`talos_cluster`** — supersedes `talos_machine_bootstrap` and takes a
`kubernetes_version`. From its documentation:

> When `kubernetes_version` changes, `talos_cluster` runs Talos's `upgrade-k8s`
> procedure: it pre-pulls images, upgrades static pods component-by-component
> with health gating, updates the kube-proxy DaemonSet, upgrades kubelet
> sequentially across nodes, **and re-applies bootstrap manifests**.

That final clause is exactly the step that had never run. This is the resource
that would have prevented the whole problem.

**`talos_machine`** — supersedes `talos_machine_configuration_apply`:

> On every `terraform refresh`, the provider reads the running Talos version and
> the active machine configuration hash from the node. If either differs from
> what Terraform last wrote, the next `terraform apply` will reconcile the
> drift — re-applying configuration or upgrading the OS as needed.

It carries `ignore_kubernetes_upgrade_drift`, which must be set to `true` so that
image tags owned by `upgrade-k8s` are excluded from drift detection and
`talos_cluster` fully owns upgrade sequencing. Without it, bumping
`kubernetes_version` on the data source re-applies component images directly and
bypasses the sequencing.

**`talos_image_factory_schematic`**, plus data sources `image_factory_urls`,
`image_factory_versions`, `image_factory_extensions_versions` and
`image_factory_overlays_versions` — these make the factory.talos.dev image
declarative. `CLAUDE.md` currently documents this as a manual step:

> New nodes MUST be booted from a factory.talos.dev image that bundles the
> required extensions … booting a new node from a stock ISO will leave it on a
> different Talos minor/patch version than the rest of the cluster, which can
> break Flannel VXLAN pod-to-pod traffic.
>
> Talos extensions required: `siderolabs/iscsi-tools`,
> `siderolabs/qemu-guest-agent` — get images from https://factory.talos.dev.

Expressing the schematic in Terraform turns that hazard into a tracked value.

Also useful: `data.talos_cluster_health` for gating dependent resources, and
`data.talos_machine_disks` for disk selection rather than hardcoded device paths.

## The prompt

> I want to bring Talos and Kubernetes version management under Terraform in the
> vulcanus-proxmox repo. Read `docs/talos-terraform-migration-prompt.md` first —
> it explains how the cluster drifted and what the newer provider offers, all
> verified 2026-08-07.
>
> Start by planning, not editing. Work out:
>
> 1. The upgrade path from provider 0.10.0 to 0.11.0 (or 0.12.x if it has gone
>    stable), and what changed in between.
> 2. How to migrate `talos_machine_bootstrap` → `talos_cluster` and
>    `talos_machine_configuration_apply` → `talos_machine` **against a live
>    cluster**. This is state migration, not a text edit — work out whether it
>    needs `removed` blocks, `import`, or `moved`, and what happens on the first
>    apply. Getting this wrong risks re-bootstrapping a running cluster, so I
>    want the failure modes spelled out before anything runs.
> 3. Where `kubernetes_version` should live so the data source and
>    `talos_cluster` stay in sync, and confirm `ignore_kubernetes_upgrade_drift`
>    is set so `upgrade-k8s` sequencing is not bypassed. The cluster is currently
>    at v1.35.0.
> 4. Whether to adopt `talos_image_factory_schematic` for the iscsi-tools and
>    qemu-guest-agent extensions, replacing the manual factory.talos.dev step in
>    CLAUDE.md.
> 5. Whether `tofu apply` becoming able to roll the CNI unattended is acceptable,
>    and if not, how to gate it. Flannel moved five minor versions in one
>    operation on 2026-08-07; that went fine, but it was done deliberately with
>    console access available.
>
> Separately and probably first, because it is small and independent: add a
> Prometheus alert for Kubernetes component version skew. kube-prometheus-stack
> is already running. An alert comparing the kube-proxy image tag against the
> apiserver version would have caught this in 2023. Declarative config prevents
> drift you apply; alerting catches drift you do not.
>
> Do not apply cluster changes without checking with me first.

## Constraints that carry over

- Follow the repo conventions in `CLAUDE.md`: no abbreviations in names, and
  Terraform variables and outputs consolidated into `main.tf`.
- Pre-commit runs `tflint` and a `terraform-no-align-equals` hook.
- JetKVM provides console and BIOS access to the Proxmox host, which is the
  recovery path if a node fails to boot.
- The repo is public. See the Security Policy in `CLAUDE.md`.
- The user's SSH key is passphrase-protected — they run `git push` and `sops -d`.
