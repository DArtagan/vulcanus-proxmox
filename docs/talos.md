# Talos

## Adding a New Node

1. In `terraform/main.tf`, add a new `module "talos_worker_N"` block and a corresponding entry in the `talos` module's `worker_nodes` map. Run `tofu apply` — this creates the VM in Proxmox.
2. In your router's DNS/DHCP settings, assign a static IP to the MAC address of the new VM, matching the IP specified in the Terraform config.
3. Generate a Talos ISO from https://factory.talos.dev (bare-metal, amd64, with the extensions listed under Upgrade Talos below) and upload it to the `local` ISO repository on the Proxmox host.
4. Mount that ISO to the new VM and start it. It will boot into Talos, join the Kubernetes cluster, and install itself to disk per the applied configuration.
5. The next `tofu apply` will detect that the ISO is no longer needed and unmount it — this is expected and fine.


## Sizing the control plane

`piraeus-control-plane-0` runs on 4096 MiB (`terraform/main.tf`), sized against
`kube-apiserver`'s peak rather than its average. The gap between the two is wide
enough to be worth writing down.

The apiserver holds roughly 362 MiB of live heap and peaks around 1.33 GiB
resident. Almost none of that is data — etcd holds about 1,900 objects in total,
the largest single resource being a few hundred ReplicaSets. The multiplier is:

- **Go's garbage collector.** `GOGC` defaults to 100, so the heap grows to
  roughly twice the live set before a collection runs, and the runtime returns
  pages to the OS lazily.
- **Breadth rather than depth.** 158 distinct resource types each carry a watch
  cache, 102 of them CRDs, and 526 concurrent watches each cost a goroutine, a
  buffered channel and cached serialized state — tens of MiB in stacks alone.

A further ~560 MiB goes to Talos, etcd, kubelet and containerd outside any pod.
etcd in particular is a host service rather than a static pod, so it never
appears in `kubectl top pod` and is easy to forget when adding up demand.

Sized to the average, the node runs out and the kernel OOM-kills the largest
process, which is always the apiserver; `kube-controller-manager` and
`kube-scheduler` then exit behind it as their leader-election leases lapse.
`NodeOOMKill` and `ControlPlaneContainerRestarting` in
`kubernetes/infrastructure/prometheus-rules.yaml` are the alerts for this — the
chart's own `NodeMemoryHighUtilization` cannot see it, because `MemAvailable`
counts reclaimable page cache and so reads healthy right up to the kill.

When headroom gets tight, `GOMEMLIMIT` on the apiserver via
`cluster.apiServer.env` is the lever to reach for before more RAM. It is a soft
ceiling the Go runtime collects harder to stay under, so unlike a container
`limits.memory` it cannot itself cause a kill. A hard memory limit on the
apiserver is the wrong instrument: it converts a node-level kill into a
guaranteed cgroup kill at a threshold picked by hand.

### Leader election

`kube-controller-manager` and `kube-scheduler` run leader election with durations
3x upstream — lease 45s, renew deadline 30s, retry period 6s — set in the
control-plane config patch.

etcd's WAL fsync sits around 0.25s and reaches 8s whenever anything else touches
`rpool`, which has no SLOG; see
[`todos/etcd-disk-latency.md`](../todos/etcd-disk-latency.md). A renewal cycle is
bounded by the renew deadline and retries every retry period — 10s and 2s
upstream — so a stall of several seconds burns the entire cycle and both
components exit(1) on "Leaderelection lost". A storage hiccup takes out two
control-plane components. The nightly Proxmox backup produces exactly that stall,
and it is a second route to the same failure the sizing above describes.

The renew deadline is the number that governs this, so it is the one to size
against a measured fsync tail. Leader election also stamps a `timeout` query
param on the lease `Put`, which the apiserver honours by aborting its own handler;
that is the string the failure appears as in the logs, but it is not the bound
that decides whether the process survives.

Widening the durations costs little here. What tight defaults buy is fast
failover to a standby, and there is one replica of each: the kubelet restarts
them when they exit, and no other candidate is waiting for the lease. The
tradeoff returns if the control plane ever becomes three nodes, so the reasoning
matters more than the numbers.

The two components need different mechanisms, and reaching for the wrong one
fails silently:

- **controller-manager** takes `--leader-elect-lease-duration` and its siblings
  through `cluster.controllerManager.extraArgs`.
- **scheduler** runs with `--config`, which Talos refuses to merge, so a
  `--leader-elect-*` flag in `scheduler.extraArgs` is accepted and then ignored.
  Its durations belong in `cluster.scheduler.config.leaderElection`. Talos
  renders the whole of `scheduler-config.yaml` from that field, keeping only
  `apiVersion`, `kind` and `clientConnection.kubeconfig` for itself, so nothing
  else in the file is at risk.

Read the rendered file to confirm a change landed rather than trusting the apply:

```bash
talosctl -n 192.168.0.190 read /system/config/kubernetes/kube-scheduler/scheduler-config.yaml
```

### Control-plane metrics

Talos binds etcd, `kube-controller-manager` and `kube-scheduler` to localhost by
default, which puts them out of Prometheus' reach. The control-plane config
patch (`terraform/modules/proxmox_talos_vm/templates/control-plane-patch.yaml.tmpl`)
moves them off loopback: etcd serves metrics on `:2381`, the other two bind
`0.0.0.0` on their existing secure ports.

etcd needs one thing more. kube-prometheus' Service for it selects pods labelled
`component: etcd`, and Talos runs etcd as a host service, so the selector matches
nothing and the endpoint has to be given explicitly in
`kubernetes/infrastructure/prometheus.yaml`. Scheduler and controller-manager
need no such entry: their static pods run with hostNetwork and already carry the
labels the chart selects on.

etcd's metrics listener is plaintext and unauthenticated. It is read-only,
serves no key material and cannot mutate etcd, and reachable from the LAN is the
accepted tradeoff; the client port stays on mTLS. `machine.network.ingressFirewall`
would narrow it further if that stops being acceptable.

kube-proxy is localhost-bound and stays that way here: Talos applies it as a
bootstrap manifest rather than a control-plane static pod, so moving it is a
separate job.

### Changing a VM's memory

`tofu apply` updates the Proxmox config, and a running guest keeps the memory it
booted with — the VMs have ballooning disabled and no memory hotplug. The apply
reports success either way, so confirm the change reached the guest rather than
only the config:

```bash
ssh root@vulcanus.forge.local 'qm config 900 | grep memory'
ssh root@vulcanus.forge.local 'qm shutdown 900 --timeout 180 && qm start 900'
kubectl get node piraeus-control-plane-0 -o jsonpath='{.status.capacity.memory}{"\n"}'
```

This is the same trap as a ConfigMap-only change in
[kubernetes.md](kubernetes.md), one layer down.

Judge the result on `node_memory_MemAvailable_bytes`, not on `kubectl top node`.
`top` reports the root cgroup's working set, which counts page cache, and the
kernel fills whatever is free with cache — so a node with ample headroom still
reads above 80% and looks unchanged. The control-plane node reads 84% while
holding 2.1 GiB genuinely available and 1.7 GiB of reclaimable cache.

Restarting the control-plane VM does not stop workloads — kubelet keeps
containers running without the apiserver — but nothing schedules, no Flux
reconcile succeeds, and `kubectl` is unavailable until it returns.

## The CPU the VMs present

`cpu { type = "host" }` in `terraform/modules/proxmox_talos_vm/main.tf` passes
the hypervisor's i7-6800K through unmasked, so the guests get AVX, AVX2, FMA,
BMI1/2, LZCNT, MOVBE, PCLMULQDQ and AES-NI. There is one hypervisor, so the
usual objection to `host` — a VM pinned to a host's CPU cannot live-migrate to a
different one — does not apply. A restore of these VMs onto other hardware would
need the type changed first.

This is load-bearing, not a micro-optimisation. Compiled wheels increasingly
assume x86-64-v3 without saying so, and the failure mode is a `SIGILL` at import
with no traceback: beets-flask's `polars` dependency took the pod down while the
container stayed up and the log claimed the server was running.

**Never set the CPU through `args`.** Proxmox appends the raw `args` string after
the `-cpu` it generates from the `cpu` block, and QEMU takes the last one — so a
`-cpu` in `args` wins silently and the `cpu` block becomes decoration. `args`
also bypasses Proxmox's own model translation: `x86-64-v3` is a Proxmox construct
built from `qemu64` plus a flag list, and QEMU rejects the name outright. The
only `args` here is worker-1's optical-drive passthrough, which the Proxmox API
cannot express. To check that only one `-cpu` reaches QEMU:

```bash
ssh root@vulcanus.forge.local 'qm showcmd 910 --pretty | grep -- -cpu'
```

Check that command line rather than the apply's exit code. The telmate provider
writes options but does not remove them: a plan that reads `args = "…" -> null`
applies clean, reports success, and leaves the option in place on the VM. What
it costs is silent — the VM reboots, and comes back with the setting the plan
claimed to have dropped. `tofu plan` still shows the drift afterwards, which is
the cheapest way to catch it. Clear the option on the host instead:

```bash
ssh root@vulcanus.forge.local 'qm set 900 --delete args'
```

A CPU type change needs a full power cycle, not a reboot from inside the guest,
and the nodes go one at a time — the same discipline as a Talos upgrade. Apply it
per VM so one `tofu apply` cannot restart the whole cluster at once:

```bash
tofu apply -target=module.talos_control_plane_0
tofu apply -target=module.talos_worker_0
tofu apply -target=module.talos_worker_1
```

Confirm it reached the guest rather than only the config — the same trap as a
memory change:

```bash
kubectl run --rm -it cpucheck --image=busybox --restart=Never \
  --overrides='{"spec":{"nodeSelector":{"kubernetes.io/hostname":"piraeus-worker-0"}}}' \
  -- grep -m1 'model name' /proc/cpuinfo
```

## Maintenance

### Upgrade Talos

Rules:
* Only upgrade one machine at a time (first the controlplane at 192.168.0.190, then the worker at 192.168.0.195)
* First upgrade to the highest patch version (1.10.2 -> 1.10.9) in that minor release.  Then upgrade to the highest patch version in the next minor release (1.10.9 -> 1.11.7).  Repeat until you're at the most recent release (1.11.7 -> 1.12.4).

1. Use https://factory.talos.dev to get your image.  If you have multiple upgrades to make, after getting your first image-name, you can manipulate the URL to get subsequent versions without going through the whole wizard again. Relevant options:
  * Bare-metal Machine
  * amd64
  * Extensions:
    * siderolabs/iscsi-tools
    * siderolabs/qemu-guest-agent
2. Upgrade the machine.  `--stage` seems to help it go smoothly.
```
talosctl --nodes 192.168.0.190 upgrade --stage --image factory.talos.dev/metal-installer/58e4656b31857557c8bad0585e1b2ee53f7446f4218f3fae486aa26d4f6470d8:v1.12.4
```
3. Check that the machine is running the version specified (one way is going into the VNC console and looking at the version displayed in the dashboard's first line).


### Grow Talos volume
1. Create debug namespace: `kubectl create ns debug`
2. Allow pod in created namespace to mount the host: `kubectl label ns debug pod-security.kubernetes.io/enforce=privileged`
3. Create the debug pod: `kubectl debug node/piraeus-worker -it --image ubuntu --profile=sysadmin -n debug`
4. Now inside the debug pod: `apt-get update && apt-get install xfsprogs parted`
5. `parted`
6. Select the correct device: `select /dev/sdb`
7. # parted should warn that not all the space is used, type "Fix" and enter: `Fix`
8. Resize the partition: `resizepart 1 100%`
9. Exit parted: `quit`
10. `xfs_growfs -d /host/var/openebs`
11. You're all in the pod: `exit`
12. `kubectl delete ns/debug`

The grow command should look like:
```
root@piraeus-worker-0:/# xfs_growfs -d /host/var/openebs
meta-data=/dev/vdb1              isize=512    agcount=9, agsize=16777088 blks
         =                       sectsz=512   attr=2, projid32bit=1
         =                       crc=1        finobt=1, sparse=1, rmapbt=0
         =                       reflink=1    bigtime=1 inobtcount=0 nrext64=0
data     =                       bsize=4096   blocks=134217216, imaxpct=25
         =                       sunit=0      swidth=0 blks
naming   =version 2              bsize=4096   ascii-ci=0, ftype=1
log      =internal log           bsize=4096   blocks=32767, version=2
         =                       sectsz=512   sunit=0 blks, lazy-count=1
realtime =none                   extsz=4096   blocks=0, rtextents=0
data blocks changed from 134217216 to 268435195
```


## Troubleshooting

### talos-worker VM doesn't start, sits on booting HDD screen

In the arguments for the talos-worker VM is a virtual SCSI that expects a cdrom drive to be connected to the host.  No cdrom drive, no launch.


## References
* Grow Talos volume: https://www.agos.one/resize-additional-disks-in-siderolabs-talos-linux/
