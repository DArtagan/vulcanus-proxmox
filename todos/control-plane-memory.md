# The control plane is memory-starved and restarts several times a day

Verified 2026-08-16. Opened as "kube-state-metrics keeps restarting on its
liveness probe" — that framing was wrong, and the section below records why so
it is not re-derived.

## Summary

`piraeus-control-plane-0` is a 3 GiB VM with 2.28 GiB allocatable. `kube-apiserver`
alone holds a 1.6 GiB working set, the node sits at **83% memory**, and
`kube-apiserver` has been OOM-killed (exit 137). When it dies,
`kube-controller-manager` and `kube-scheduler` lose their leader-election leases
and exit 1 behind it.

Averaged over 30 days, control-plane containers restart **9.27 times per day**.

Nothing is user-visible today: the cluster serves, workloads run, and the node
reports `MemoryPressure False`. What it costs is the monitoring layer's
credibility, which is the thing every other item on this list depends on.

## Evidence

All verified 2026-08-16, roughly 22:15 EDT.

| Fact | Value |
|---|---|
| Control-plane VM | `memory = 3072`, `cores = 2` (`terraform/main.tf:208-209`) |
| Node allocatable memory | 2396212Ki ≈ 2.28 GiB |
| Node memory in use | 1942Mi — **83%** |
| Node memory available | 0.97 GiB |
| `kube-apiserver` working set | 1.595 GiB — 70% of allocatable, on its own |
| `kube-apiserver` resources | `requests: {cpu: 200m, memory: 512Mi}`, **no limit** |
| `kube-apiserver` restarts | 3 total, last exit code **137** |
| `kube-controller-manager` restarts | **453** |
| `kube-scheduler` restarts | **468** |
| Control-plane restarts, 30d average | **9.27/day** |

Because `kube-apiserver` carries no memory *limit*, exit 137 is a node-level
kill rather than a cgroup limit — the kernel picks the largest process on a full
node, which is always the apiserver.

**It is not a leak.** Working set 7 days ago was 1.694 GiB against 1.595 GiB now.
The apiserver is simply larger than the VM it was given.

Everything resident on that node, by working set:

| Pod | GiB |
|---|---|
| `kube-apiserver` | 1.595 |
| `alloy` | 0.379 |
| `kube-controller-manager` | 0.251 |
| `csi-smb-controller` | 0.120 |
| `metallb-speaker` | 0.116 |
| `kube-flannel` | 0.097 |
| `kube-scheduler` | 0.078 |
| `coredns` | 0.068 |

That totals ~2.7 GiB of working set on a VM with 2.28 GiB allocatable. The node
is structurally oversubscribed, not transiently unlucky.

## Why the kube-state-metrics framing was wrong

kube-state-metrics restarts 16 times, exiting 2 after logging
`Failed to contact API server for /livez: got 0`, and its liveness probe has
failed 188 times in 8 days. It looks like a bad probe — KSM's `/livez` calls the
API server, so an unreachable apiserver kills an otherwise healthy KSM, and
`docs/kubernetes.md` already warns about exactly this shape of liveness probe.

Tempting, and wrong. KSM's probe is reporting accurately: the API server really
is unavailable at those moments. Restarts in the 24 hours to 2026-08-16 show the
blast radius is far wider than KSM:

| Pod | Restarts in 24h |
|---|---|
| `csi-smb-controller` | 7 |
| `openebs-localpv-provisioner` | 7 |
| `kube-controller-manager` | 6 |
| `kube-scheduler` | 4 |
| `alloy` | 2 |
| `kube-state-metrics` | 2 |
| `metallb-speaker` | 1 |
| `kube-apiserver` | 1 |

`openebs-localpv-provisioner` has 746 lifetime restarts, `csi-smb-controller` 76.
Softening KSM's probe would have hidden the one component that was telling the
truth and left the other seven restarting.

This is the mirror image of
[generic-device-plugin-hang.md](generic-device-plugin-hang.md), where a liveness
probe is the fix. Here a liveness probe is the messenger.

## Why this is worth doing

It corrupts the signal the rest of this list is built on. Concretely, it already
produced a false page: every KSM restart drops and re-creates its metric series,
which resolves and re-fires whatever alert was pending. That is what turned a
single transient `rclone-dropbox` Dropbox failure into repeated Pushover
notifications over four days, and it will do the same to `CronJobNotSucceeding`
and to anything else built on kube-state-metrics.

Any alerting work is only as trustworthy as the exporter underneath it.

## The fix

**Raise the control-plane VM to 6144 MiB** in `terraform/main.tf:208`, then
`tofu apply`. One line.

Two things to settle first:

1. **Does the Proxmox host have the headroom?** Not verified — SSH to
   `vulcanus.forge.local` needs the passphrase-protected key, which this session
   could not use. Check with:
   ```bash
   ssh root@vulcanus.forge.local 'free -g; qm list'
   ```
   The two workers hold 24576 and 8192 MiB (`terraform/main.tf:224,239`), so the
   host is already carrying ~35 GiB of guests before this change.
2. **`tofu apply` on a memory change restarts the VM**, which means control-plane
   downtime. Workloads keep running — kubelet does not need the apiserver to keep
   containers alive — but nothing can be scheduled, no Flux reconcile will
   succeed, and `kubectl` is unavailable until it comes back. Do it deliberately,
   not mid-session.

Alternatives, if host RAM turns out to be the binding constraint:

- Cap `alloy` on the control plane. It is the second-largest consumer at 0.379
  GiB and is a log shipper, not a control-plane component. Cheapest partial win.
- Tune apiserver watch cache sizes via a Talos config patch. Fiddly, and 1.6 GiB
  is not unreasonable for this cluster — this treats the symptom.
- Give `kube-apiserver` a memory limit. **Do not.** It converts a node-level OOM
  into a guaranteed cgroup OOM at a threshold picked by hand, and the apiserver
  is the last thing that should be killed predictably.

## Verify afterwards

- `kubectl top node piraeus-control-plane-0` well under 83%.
- `sum(increase(kube_pod_container_status_restarts_total{namespace="kube-system"}[24h]))`
  trending to zero over the following days.
- KSM restart count stops advancing; `kubectl describe` no longer accrues
  `Liveness probe failed: HTTP probe failed with statuscode: 503`.
- Check whether the currently-firing `KubeMemoryOvercommit` clears. Not confirmed
  to be the same root cause, but it is the obvious candidate and it is one of only
  two alerts currently reaching Pushover.

## Prompt to open with

> Read `todos/control-plane-memory.md`. `piraeus-control-plane-0` is a 3 GiB VM
> where kube-apiserver alone holds a 1.6 GiB working set, so it gets OOM-killed
> and takes controller-manager and scheduler down with it — 9.27 control-plane
> container restarts a day, which also restarts kube-state-metrics and corrupts
> alerting. First confirm the Proxmox host has headroom
> (`ssh root@vulcanus.forge.local 'free -g; qm list'` — I will need to run any
> SSH or `tofu apply` myself, my key is passphrase-protected). Then raise
> `memory` for `talos_control_plane_0` in `terraform/main.tf` to 6144 and plan
> the apply, bearing in mind it restarts the control-plane VM.
