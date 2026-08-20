# etcd runs on spinning disks and is ~40x slower than it should be

Verified 2026-08-17, ~20:30–20:50 UTC, the same day etcd metrics were scraped
for the first time. `etcdHighCommitDurations` fired within hours of that scrape
going live. It is correct.

## Summary

etcd's synchronous writes land on `rpool`: two raidz2 vdevs of spinning disks —
one of 7200 rpm, one of 5980 — with **no SLOG**. Every fsync waits on a ZIL
commit to rotating media. Every physical device in the host is rotational —
there is no SSD or NVMe anywhere in `vulcanus`.

Mutating API calls sit on the Kubernetes SLO line, and the spike that pushes them
over arrives nightly. On 2026-08-18 this cost two control-plane components: see
[Consequences already observed](#consequences-already-observed) below.

## Evidence

| Measure | Value | etcd's target |
|---|---|---|
| p99 backend commit | 0.42 s avg, 0.31 s floor over 6 h | < 0.025 s |
| p99 WAL fsync | 0.41 s | < 0.010 s |
| apiserver p99 PUT / PATCH | 0.97 s / 0.92 s | K8s SLO < 1 s |
| etcd slow applies | 17–46 / min | — |
| Failed proposals, pending, slow read-index | 0 / 0 / 0 | — |

Constant, not load-driven: the floor never dropped below 0.31 s across the whole
window, with no scrub or backup running.

`etcdHighFsyncDurations` stays **inactive** throughout, because its threshold is
0.5 s and the value is 0.41 s. It is ~40x etcd's target and silent. Only the
commit rule has a threshold tight enough to notice, which is worth knowing
before trusting the other twelve etcd rules to be meaningful at this scale.

Storage layout, from `zpool status rpool`:

```
rpool
  raidz2-0   4x ata-ST4000VN008-2DR166      (Seagate IronWolf 4 TB)
  raidz2-1   4x ata-HGST_HUH721010ALE604    (HGST 10 TB)
```

No `logs`, `cache` or `special` vdev. The control-plane zvol is
`sync=standard`, `volblocksize=16K`, `logbias=latency`.

## Consequences already observed

**2026-08-18: the nightly Proxmox backup killed `kube-scheduler` and
`kube-controller-manager`.** Five and seven restarts respectively, all inside one
20-minute window, with `kube-apiserver` at zero restarts throughout — this is not
the memory fault fixed the day before, and `/proc/vmstat` on the node reports
`oom_kill 0`.

The vzdump job in `/etc/pve/jobs.cfg` is scheduled `4:00`; the host runs MDT, so
it starts at 10:00 UTC. It ran 10:00:07 → 10:22:01, `mode snapshot`, every VM.
Disk-IO saturation rose on all three guests at once while their own IOPS stayed
flat or fell — worker-0's reads dropped 226→106/s — so the contention is
host-side, not any guest's workload. WAL fsync p99 went 0.25 s → **3.96 s**,
peaking while VM 900's own disk was being read (10:09:14 → 10:13:01).

The tail is what matters, over 5071 fsyncs in that window:

| bucket | fsyncs |
|---|---|
| ≤ 1.024 s | 4991 (98.4%) |
| 1–2 s | 31 |
| 2–4 s | 36 |
| 4–8.192 s | 11 |
| **> 8.192 s** | **2** |

p99.9 = 7.09 s. A renewal cycle is bounded by the renew deadline (10 s upstream)
and retries every retry period (2 s), so a stall of several seconds burns the
whole cycle and both components exit(1) on "Leaderelection lost", by design. The
lease `Put` also carries `?timeout=5 s`, which the apiserver honours by aborting
its own handler — that is how the failure reads in the logs. Where that 5 s comes
from was **not** identified: `createClients` in kube-scheduler v1.35.0 passes no
timeout, and `renew()` divides nothing. Do not build on the 5 s figure; the renew
deadline is the bound that decides whether the process survives.

### Six containers restart, not two

`kube-scheduler` and `kube-controller-manager` are the loudest but not the whole
list. Restarts inside the same 25-minute window:

| container | restarts | lease |
|---|---|---|
| `openebs-localpv-provisioner` | 6 | `openebs.io-local`, 15 s |
| `kube-scheduler` | 5 | 45 s after 2026-08-18 |
| `kube-controller-manager` | 4 | 45 s after 2026-08-18 |
| `csi-provisioner` | 4 | `smb-csi-k8s-io`, 15 s |
| `csi-resizer` | 4 | `external-resizer-smb-csi-k8s-io`, 15 s |
| `kube-state-metrics` | 1 | — (liveness probe against the apiserver) |

The four unaddressed ones sit on 15 s leases, tighter than the durations that
killed the two that were fixed. apiserver p99 for `PUT|PATCH|POST` reads **8.77 s**
across the window against 0.95 s at rest, so for 22 minutes a night the cluster
cannot reliably accept a write: storage provisioning stalls and alerting metrics
are unreliable.

**These restarts do not happen at any other time.** Bucketed hourly across 20 h,
all three of `openebs-localpv-provisioner`, `csi-provisioner` and
`kube-state-metrics` restart in exactly two hours — the backup window, and the
hour a Talos config change re-rendered the static pods. Zero in the other 18. So
`openebs-localpv-provisioner`'s **768 restarts over 127 days** are not ambient
flakiness: at ~6 per backup that is close to one nightly window per day for the
life of the pod.

This is the argument for the SLOG over per-component timeout tuning. Fixing the
disk fixes all six at once; widening leases fixes them one chart at a time and
leaves each new controller to be discovered the same way.

Note the job is invisible to this repo: it lives only in `/etc/pve/jobs.cfg`,
managed through the Proxmox UI rather than Ansible or Terraform. Identifying a
nightly 10:00 UTC event meant reading `/var/log/pve/tasks`.

A milder instance the same day, at 15:33, came from the Helm upgrade to
kube-prometheus-stack 88.4.0: the same apiserver handler timeouts on the same
lease paths with fsync at only 463 ms. Nothing restarted. **etcd here is marginal
without any backup running** — the backup is the loudest competitor for the
disks, not the only one.

**Mitigated, not fixed.** Leader-election durations on both components are now 3x
upstream (lease 45 s, renew 30 s, retry 6 s), so a multi-second stall is survived
instead of fatal. etcd still stalls every night. The SLOG below is still the fix.

Confirm that from the API rather than the machine config, because the machine
config is two layers away from what the process uses:

```bash
kubectl get lease -n kube-system kube-scheduler kube-controller-manager \
  -o custom-columns='NAME:.metadata.name,LEASE_SEC:.spec.leaseDurationSeconds'
```

Throttling the backup was considered and **declined** — user's call, 2026-08-18.
`bwlimit` on the vzdump job would have reduced the stall itself and helped every
guest, but it lives outside git and leaves the control plane just as intolerant of
the next unrelated IO spike. Do not re-propose it as an alternative to the SLOG;
it was weighed against exactly that.

Also worth knowing: `ionice priority: 7` already appears in the vzdump log and
does nothing useful. QEMU guest backups are issued via QMP by the KVM process,
not by vzdump, and ZFS schedules ZIO through its own priority classes rather than
the Linux block scheduler. Neither path sees that nice level.

### Two nights on the widened leases

Verified 2026-08-20 from Prometheus. The 45 s leases have been in effect since
2026-08-18 17:06 UTC.

| night | vzdump ran | fsync p99 peak | `kube-scheduler` | `kube-controller-manager` |
|---|---|---|---|---|
| 2026-08-18 | 10:00:07 → 10:22:01 | 6.34 s | 5 restarts | 4 restarts |
| 2026-08-19 | 10:00:04 → **12:22:11** | 5.59 s | 1 restart | 1 restart |
| 2026-08-20 | 10:00:03 → 10:22:56 | 3.86 s | 0 | 0 |

**The widening is a reduction, not a fix.** 08-19 was its first test and both
components still lost leadership once each, at ~10:15, on 45 s leases. A quiet
08-20 is a quiet ordinary night, not evidence the mitigation holds.

**The exposure window is not 22 minutes; it is however long the backup runs.** On
08-19 vzdump took 2 h 22 m, and all of the excess was `talos-worker-0`: both its
disks logged `dirty-bitmap status: created new`, so QEMU had no incremental
bitmap and vzdump read the entire 1.1 TiB off `rpool` at 155 MiB/s. The next
night the bitmap survived, the same VM reported 10.4 GiB dirty, and it finished
in 8 m 31 s. A dirty bitmap lives in the QEMU process, so any guest stop/start —
a config change, a host reboot — buys a full-read backup the following night.
etcd's fsync p99 stayed above 2 s for two and a half hours, and
`KubeAPIErrorBudgetBurn` fired four separate times between 10:30 and 18:00.

That night is also the only time `etcdHighFsyncDurations` has fired: crossing its
0.5 s threshold takes a full-read backup. `etcdHighNumberOfFailedGRPCRequests`
fired alongside it.

The other four components still restart on an ordinary night. Within 08-20's
22-minute window: `openebs-localpv-provisioner` 6, `csi-provisioner` 3,
`csi-resizer` 2. Only `kube-state-metrics` was quiet.

### The backup's other end is on `rpool` too

`vzdump` writes to storage `pbs`, which is Proxmox Backup Server running as VM
107 **on this same host**, and 107's 2 TB datastore disk is a raw file under
`/rpool/proxmox_backup_server`. The nightly job therefore reads guest zvols off
the eight spindles and writes the deduplicated result back to the same eight
spindles. The job's `--exclude 107,100` is the only thing keeping that from being
circular.

Recorded as a fact about the load, not as a proposal. Where the datastore lives
is a separate question from etcd's sync-write latency, and nothing in the fix
below depends on it.

## What has already been done

**Defragmented, 2026-08-17 20:43 UTC.** etcd reported 95 MB on disk against
20 MB in use — 78% waste, which the shipped
`etcdDatabaseHighFragmentationRatio` rule cannot report because it requires
`in_use > 100 MiB` and this database is 20 MB. `talosctl -n 192.168.0.190 etcd
defrag` took **1.04 s** and returned the file to 20 MB at 100% in use. An
`etcd snapshot` was taken first. No restarts, leader retained, zero failed
proposals.

**It changed nothing measurable.** Averaged over comparable windows, fsync p99
went 0.332 s → 0.350 s and commit p99 0.444 s → 0.452 s. apiserver p99 PUT moved
0.966 s → 0.949 s, PATCH not at all. Fragmentation was a red herring: the disks
are the whole story, and no amount of housekeeping inside etcd will change that.

Worth recording how that was nearly got wrong. The first post-defrag sample read
fsync 0.252 s, a 39% improvement, and it was one 5-minute sample against a
variable workload. Comparing averaged windows either side showed it as noise. A
single sample of a p99 over a bursty workload is not a measurement — reach for
`avg_over_time` across matched windows before claiming a delta.

Defrag remains worth repeating occasionally on its own merits — 75 MB of dead
pages is 75 MB that gets copied by every snapshot and backup — but not as a
latency fix. Three days later, on 2026-08-20, the file is back to 54 MB against
19.7 MB in use, so the regrowth rate is roughly 11 MB of dead pages a day.

**Silenced in Alertmanager**, silence ID `43b35199-356a-4304-a1e9-288980fbcd3b`,
**expiring 2026-09-17**. The alert is permanently true until the storage
changes, and at `severity: warning` it re-pages Pushover every 12 h. The expiry
is deliberate: if this is still unaddressed in a month it starts paging again
rather than being silenced into oblivion. The silence is Alertmanager runtime
state on its PVC, not in git, so it is invisible to the repo — this file is the
only record of it.

## Host hardware, verified 2026-08-20

MSI X99A GAMING PRO CARBON (MS-7A20), i7-6800K, 64 GiB, PVE 9.2.2, ZFS 2.4.2,
`rpool` at ashift 12.

- **No NVMe device anywhere.** `/sys/class/nvme` is empty.
- **The board's own M2_1 slot is the home for it**, and no PCIe adapter is
  needed. M2_1 shares its lanes with the U.2 port and the bottom x16 slot; both
  are empty, so it runs at its full PCIe 3.0 x4 off the CPU. Of the four CPU
  root ports, only 00:02.0 is linked — x8, to the GT 640 — while 00:03.0 (x16)
  and 00:01.0/00:01.1 (x4 each) all sit at x0. There are lanes to spare.
- **Slot6, PCIe 3.0 x16, free**, as the fallback if M2_1 turns out to be
  physically awkward. A single M.2 drive on a passive x4 adapter needs no
  bifurcation.
- **One free SATA port**, `ata9`, on the 00:1f.2 six-port controller. Nine of the
  ten are taken: eight pool disks and the BD-RW.
- Disks are 4x HGST HUH721010ALE604 (10 TB, 7200 rpm) and 4x ST4000VN008 (4 TB,
  5980 rpm).
- `MODULES=most` in `initramfs-tools`, and the current initrd already carries 15
  nvme modules. This matters because `rpool` is the root pool: a log vdev the
  initramfs cannot see turns a reboot into `zpool import -m` and discards
  whatever the ZIL was holding. Checked rather than assumed.

## The fix

**An SSD with power-loss protection, added to `rpool` as a SLOG.** Preferred
over the alternatives because it is a small, cheap device, it fixes sync-write
latency for every guest rather than just etcd, and it needs no VM migration or
downtime to add.

PLP is not optional. A consumer SSD without it can lose the ZIL in exactly the
power-loss event the ZIL exists to survive. Optane or a datacentre drive with
capacitors. A mirror is optional — since ZFS 0.7 a lost SLOG falls back to the
pool, so only losing the SLOG *simultaneously with* a power cut is dangerous.

Alternatives considered:

- **A small SSD pool holding only the control-plane VM's disk.** Targeted at
  etcd, but needs a VM migration and helps nothing else on the host.
- **`sync=disabled` on the etcd zvol. Do not.** It would clear the alert within
  minutes and expose etcd to corruption on power loss. This is the same shape as
  softening kube-state-metrics' liveness probe in the control-plane memory work
  — silencing the messenger rather than fixing the cause.
- **Raising the alert threshold. Do not**, for the same reason. The measured
  value is genuinely ~17x target for commits and ~40x for fsync.

### Sizing and endurance, measured rather than guessed

`/proc/spl/kstat/zfs/zil` across a quiet 30 s window, 2026-08-20:

| counter | delta over 30 s |
|---|---|
| `zil_itx_metaslab_normal_write` | 11.07 MB |
| `zil_itx_metaslab_normal_count` | 441 |
| `zil_itx_needcopy_count` | 752 |
| `zil_itx_indirect_count` | 148 |

That is **369 KB/s of ZIL writes — ~32 GB/day, ~12 TB/year** — for the whole
host, not just etcd. Double it for backup nights and any datacentre SSD's rated
endurance still clears it by more than an order of magnitude. Capacity is a
non-issue for the same reason: the ZIL holds at most a couple of transaction
groups, so single-digit GiB is ample.

The same counters confirm the SLOG will catch etcd's writes specifically. A
record reaches the log device only on the `copied` or `needcopy` path;
`indirect` records leave the data to be written to its final location and the
fsync waits on that instead. etcd's zvol is `volblocksize=16K` with
`logbias=latency`, under the 32 KiB `zfs_immediate_write_sz` cutoff, so it takes
the copied path. The `indirect` records above are the large-write datasets — the
fileserver shares — and they are not what is being fixed.

**Buy capacity anyway and partition a slice of it.** The controller spreads wear
across every block it has not been told to store, so a 240–480 GB drive carrying
a 16 GiB log partition keeps ~95% of its NAND as spare area. `blkdiscard` the
whole device first so that space is genuinely unmapped.

### Which device, decided 2026-08-20

Optane P1600X was the first choice and is **out on price** — user's call: the
cheapest available is ~$300, which is not a short- or mid-term spend. It remains
the better device long-term. Do not re-propose it at that price.

Buy instead an **M.2 NVMe with capacitor-backed power-loss protection**, into the
board's M2_1 slot:

| device | why |
|---|---|
| Micron 7450 PRO 480 GB M.2 2280 | a real datacentre drive, PLP on the datasheet, current production |
| Kingston DC1000B 240 GB M.2 2280 | cheapest current-production M.2 with PLP; its modest throughput is irrelevant at 369 KB/s |
| Kingston DC600M 480 GB, SATA | last resort — goes in `ata9`, costs ~30–50 µs more per commit and shares a controller with four pool disks |

PLP is the one non-negotiable specification, and it is checked on the datasheet
rather than the marketing page. A drive that acknowledges a write from a volatile
buffer can lose the ZIL in exactly the power cut the ZIL exists to survive, and
it does so silently.

Buy an **NVMe** module rather than a SATA-mode M.2. That is the better device
anyway, and it also sidesteps the question of whether a SATA M.2 costs one of the
board's ten SATA ports, nine of which are in use.

Bandwidth is not a consideration anywhere in this decision: 369 KB/s would be
comfortable on the PCIe 2.0 x2 the M.2 falls back to if the bottom x16 slot is
ever populated. Latency is the whole point, and that fallback path runs through
the PCH and shares DMI with the SATA controllers — so if a card does go in that
slot later, re-measure rather than assume.

### The procedure

Adding a log vdev is online: no downtime, no resilver, and `zpool remove` takes
it back out, which works for log vdevs even on a pool containing raidz.

```bash
# 1. Confirm the device, and that it is the one you think it is.
ls -l /dev/disk/by-id/ | grep -i nvme
nvme list

# 2. Hand the whole drive back to the controller, then take a small slice.
blkdiscard /dev/nvme0n1
sgdisk --zap-all /dev/nvme0n1
sgdisk -n1:0:+16G -t1:BF07 -c1:slog /dev/nvme0n1
partprobe /dev/nvme0n1

# 3. by-id, never /dev/nvme0n1 — the same reason the pool disks use it.
zpool add -o ashift=12 rpool log /dev/disk/by-id/nvme-<model>_<serial>-part1

# 4. It takes traffic within seconds.
zpool status rpool
zpool iostat -v rpool 5
grep slog /proc/spl/kstat/zfs/zil   # these counters are currently all zero
```

### How to tell it worked

The defrag lesson applies: a single 5-minute sample of a p99 over a bursty
workload is not a measurement. Compare matched windows.

- fsync p99 at rest should fall from ~0.25 s to single-digit milliseconds:
  `avg_over_time(histogram_quantile(0.99, sum(rate(etcd_disk_wal_fsync_duration_seconds_bucket[5m])) by (le))[6h:5m])`
- `etcdHighCommitDurations` should clear on its own. **Remove the Alertmanager
  silence `43b35199-356a-4304-a1e9-288980fbcd3b` when the device goes in**,
  rather than letting it expire on 2026-09-17 — the alert is the confirmation.
- The following night, `openebs-localpv-provisioner`, `csi-provisioner` and
  `csi-resizer` should restart zero times during the backup. They are the better
  signal now: the two widened components absorb a stall instead of reporting it.
- Force the hard case rather than waiting for it. Stop and start
  `talos-worker-0` so QEMU discards its dirty bitmap, and let the next backup do
  the full 1.1 TiB read. That is the load that ran 2 h 22 m and was the only one
  ever to fire `etcdHighFsyncDurations`.

## What is not known

There is **no history before 2026-08-17**, because the etcd scrape did not exist
until that afternoon. This has almost certainly been true since the cluster was
built in 2022 — the latency floor is flat from the first sample, and nothing
about that day's control-plane resize touches the sync-write path — but it
cannot be shown from data. Do not claim it as a regression or as long-standing;
say it is unmeasured before that date.

Whether slow etcd contributed to the control-plane instability fixed on
2026-08-17 is likewise unproven. That was an OOM kill on working-set size, which
is a separate mechanism. Slow etcd holding apiserver requests in flight is a
plausible aggravator and nothing more. The scheduler and controller-manager
restarts a day later are a different matter — those are measured, and the cause
is this one.

**The leader-election widening is measured, and it is not sufficient.** Both
components still restarted once each on 2026-08-19 with 45 s leases in force. It
was sized against a tail whose top bucket is unbounded — two fsyncs exceeded
8.192 s on 08-18 and how far is unknowable, because `+Inf` is where the histogram
stops — and against stalls that are bursty and correlated rather than
independent, so the retry budget buys less than the arithmetic suggests. What is
still unknown is the ceiling: how long a stall the 45 s lease does survive, and
whether a longer full-read backup than 08-19's would breach it again.

**What the SLOG does for the other four is unverified until it is in.** The
15 s-lease components restart on ordinary nights as well as long ones, so their
threshold is lower than the two widened ones; there is no measurement of how far
fsync has to fall before they stop.

## Prompt to open with

> Read `todos/etcd-disk-latency.md`. etcd on `piraeus-control-plane-0` has p99
> backend commit ~0.42 s and WAL fsync ~0.25 s at rest, against etcd targets of
> 0.025 s and 0.010 s, because `rpool` is two raidz2 vdevs of spinning disks with
> no SLOG and the host contains no SSD at all. The nightly Proxmox backup pushes
> fsync past 8 s; widening leader-election on `kube-scheduler` and
> `kube-controller-manager` reduced their restarts but did not stop them, and
> four other components on 15 s leases still restart every night. The device is
> chosen — an M.2 NVMe with capacitor-backed power-loss protection, on a passive
> adapter in the free PCIe x16 slot — and the `zpool add` procedure and its
> verification are written up in the spec. I have the drive; walk it in. Remove
> the Alertmanager silence `43b35199-356a-4304-a1e9-288980fbcd3b` at the same
> time, since it otherwise expires 2026-09-17.
