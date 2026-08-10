# OpenEBS 3.10 → 4.x — context and starting prompt

Written during the 2026-08-07 session that moved HelmRelease updates onto Flux
image automation. That session deliberately did **not** touch OpenEBS, because it
is a storage-layer migration rather than a version bump. This file is the
starting context for a follow-up session.

## Why this can't just be a version bump

The `openebs` HelmRelease had **no version constraint at all** — `chart.spec.version`
was absent, so Flux resolved `*`. That looked like "always latest" but the chart
repository it points at, `https://openebs.github.io/charts`, was abandoned at
**3.10.0 on 2023-12-18**. So `*` has quietly meant "3.10.0 forever" for two and a
half years.

That session pinned `version: 3.10.0` in `kubernetes/infrastructure/openebs.yaml`
to make the situation explicit. Nothing about the running cluster changed.

The live chart repository is now:

- HTTP: `https://openebs.github.io/openebs` — latest **4.5.1** (2026-06-18)
- OCI: `oci://ghcr.io/openebs/charts/openebs` — carries 4.4.0, 4.5.0, 4.5.1 only

Between 3.10 and 4.x the umbrella chart was restructured: the legacy engines
(Jiva, cStor) moved out to their own charts, Node Disk Manager was deprecated,
and LocalPV became a subchart. This is not a drop-in replacement of the
`openebs-hostpath` StorageClass, which is what everything here actually uses.

## What is running now

```
openebs/provisioner-localpv:3.5.0
openebs/node-disk-manager:2.1.0
openebs/node-disk-operator:2.1.0
```

Two StorageClasses, both provisioner `openebs.io/local`:

| Name | Reclaim | Binding | Notes |
|---|---|---|---|
| `openebs-hostpath` | **Delete** | WaitForFirstConsumer | BasePath `/var/openebs/local`. Everything uses this. |
| `openebs-device` | Delete | WaitForFirstConsumer | Unused. |

`reclaimPolicy: Delete` is the single most dangerous fact here. Any sequence that
deletes a PVC deletes the data with it.

## Where the data physically lives

`/var/openebs` is a **dedicated virtual disk**, not the boot disk, mounted by a
Talos machine-config patch. See `terraform/modules/proxmox_talos_vm/` —
`templates/openebs-disk-patch.yaml.tmpl` mounts `var.openebs_disk.device` at
`/var/openebs`, plus `files/openebs-kubelet-patch.json`. Sizes are set in
`terraform/main.tf`:

- `piraeus-worker-0` (vmid 910) — `openebs_disk = { size = "1T" }`
- `piraeus-worker-1` (vmid 911) — `openebs_disk = { size = "100G" }`

So each volume is a directory at `/var/openebs/local/<pv-name>` on whichever
worker the consuming pod was first scheduled to. Volumes are node-local and do
not move — a hostpath PV pins its pod to one node.

## PVC inventory (2026-08-07)

26 PVCs on `openebs-hostpath`. The rest of the cluster's storage is NFS/SMB from
the fileserver at 192.168.0.105 via statically-defined PVs (`*-pv.yaml`) and is
unaffected by any of this.

**infrastructure**
- `prometheus-...-prometheus-0` — **100Gi**, the big one
- `server-volume-victoria-logs-0` — 50Gi (replaced `storage-loki-0` on 2026-08-10)
- `traefik`, `traefik-external`, `traefik-internal`, `traefik-internal2` — 128Mi each

**apps** — `beets-library` 256Mi, `borgmatic-data` 10Gi, `filebot` 1Gi,
`headplane-data` 1Gi, `headscale-data` 1Gi, `linkding-data` 50Gi, `mumble-data` 1Gi,
`photoprism-data` 50Gi, `photoprism-database` 10Gi, `pinepods-backups` 5Gi,
`pinepods-database` 10Gi, `pinepods-valkey` 1Gi, `plex-config` 10Gi,
`podgrab-data` 1Gi, `rclone-dropbox-bisync-cache` 1Gi, `rclone-dropbox-config` 1Mi,
`rustdesk-data` 1Gi, `salamander-data` 50Gi, `salamander-database` 10Gi,
`speedtest-tracker` 1Gi, `stump-config` 10Gi, `syncthing-data` 1Gi, `youtube-dl` 1Gi

**automatic-ripping-machine** — `automatic-ripping-machine` 1Gi

Note the four `traefik*` PVCs in `infrastructure` are orphans from a Traefik
install that no longer exists in this repo. Confirm nothing references them and
delete them as cleanup — that also shrinks the migration surface by four.

## The prompt

> I want to migrate OpenEBS from 3.10.0 to 4.x in the vulcanus-proxmox repo.
> Read `todos/openebs-4x-migration-prompt.md` first — it has the full inventory and
> the physical layout, verified 2026-08-07.
>
> Start by planning, not editing. Specifically work out:
>
> 1. Whether the 4.x `localpv-provisioner` subchart recreates a StorageClass named
>    `openebs-hostpath` with provisioner `openebs.io/local` and BasePath
>    `/var/openebs/local`. StorageClass parameters are immutable, so if the chart
>    wants to create one that differs from the existing object, Helm will conflict
>    and the upgrade will fail partway. Determine whether existing PVs keep binding.
> 2. Whether existing PVs provisioned by 3.5.0 are still honoured by the 4.x
>    provisioner, or whether they need to be adopted / re-created. `reclaimPolicy:
>    Delete` means getting this wrong loses data.
> 3. What happens to NDM. It is deprecated in 4.x and the `ndm` resource limits
>    workaround currently in `kubernetes/infrastructure/openebs.yaml` (for
>    openebs/node-disk-manager#673) may no longer apply. `openebs-device` is unused,
>    so dropping NDM entirely is probably fine — confirm it.
> 4. A restore path. Before anything is applied I want to know how each of the 26
>    volumes gets back. Borgmatic already backs up some of this — check
>    `kubernetes/apps/borgmatic/` and `todos/backups.md` for what is actually
>    covered, and say plainly which PVCs have no backup.
>
> Propose an order that does VictoriaLogs and Prometheus last, since they are the largest
> and the least painful to lose. Don't apply cluster changes without checking with
> me first.
>
> If the migration turns out to be safe, also convert this HelmRelease to the
> `OCIRepository` + `$imagepolicy` pattern the other charts now use
> (`oci://ghcr.io/openebs/charts/openebs`), matching how `cert-manager` and the
> others are wired in `kubernetes/infrastructure/`.

## Constraints that carry over

- Talos extensions `siderolabs/iscsi-tools` and `siderolabs/qemu-guest-agent` are
  already installed. iSCSI is only needed for the replicated/Mayastor engines,
  which this cluster does not use.
- The repo is public. See the Security Policy in `CLAUDE.md`.
- The user's SSH key is passphrase-protected — they run `git push` and `sops -d`.
