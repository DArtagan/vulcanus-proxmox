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

The three 15 s components were nonetheless tuned on 2026-08-20, because the SLOG
is deferred on price for as long as the NAND shortage lasts and they were
restarting on ordinary nights, not only bad ones. `csi-provisioner` and
`csi-resizer` now carry the same
45 s/30 s/6 s durations as the control plane; `openebs-localpv-provisioner` has
no duration knob in chart 3.10.0 and had leader election disabled instead. That
does not weaken the argument above — it is interim, and it reduces *load* rather
than making the disk faster.

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

**How far it got — measured 2026-08-19.** A bitmap-reset backup (below) ran
2h04m, roughly six times the stall that produced 5 and 7 restarts on 08-18, and
`kube-scheduler` and `kube-controller-manager` restarted **once each**; the 15 s
components in the same window restarted 19–25 times. A second bitmap-reset
backup on 08-21 ran 98 minutes and every one of them held at zero — see [Six
nights on the widened leases](#six-nights-on-the-widened-leases). Restarts are no
longer the failure mode; the nightly page is, and that comes from fsync itself.

Two things about that window are worth keeping:

- **Failed proposals are the sharp signal.** `etcd_server_proposals_failed_total`
  rose 463 against 74 on an ordinary night, and slow applies 46,405 against
  8,571. At rest both read zero, so a non-zero value means etcd genuinely could
  not commit rather than merely being slow.
- **Lease expiry is not.** `etcd_debugging_server_lease_expired_total` reads
  1,180 during the stall against 1,224 on a quiet night — flat. Those are Events
  ageing out on a 1 h TTL at ~400/h. `LeaseKeepAlive` gRPC failures come from
  etcd being *blocked*, not from leases lapsing, and reading them as expiry
  sends you after the wrong mechanism.

The threshold between a night that pages and one that does not is narrow and
empirical: 08-20 peaked at 3.81 s fsync and fired nothing, 08-19 peaked at 5.59 s
and fired everything. etcd's request timeout is somewhere between. That constant
was not pinned down, the same gap this file already records about the 5 s figure.

## The backup is ~6x worse after any VM restart

Proxmox Backup Server keeps its changed-block bitmap in the QEMU process, so any
restart of that process discards it and the next backup reads every block. The
CPU-type change on 2026-08-18 power-cycled all three VMs; the next night's job
ran **142 minutes instead of 22.9**, and worker-0 alone transferred **1.10 TiB
against ~10 GiB**. Both untouched VMs transferred identically on both nights,
which is what identifies the cause.

That is the amplifier behind every consequence in this file. It is also partly
self-inflicted: the guests never issued TRIM, so worker-0's zvol held 786 GB
referenced against 122 GB of live data, and a full read dragged ~660 GB of dead
blocks off the platters. `discard = true` is now set on every virtio disk, which
takes a reboot to activate and needs `fstrim` run by hand afterwards; see
[`docs/talos.md`](../docs/talos.md).

Trimmed 2026-08-20, worker-0 fell from 873 GB referenced to 307 GB and the
control plane from 6.93 GB to 3.14 GB — ~570 GB that a full read no longer pulls
off the platters. The trim itself cost almost nothing: fsync stayed between 0.33
and 0.50 s throughout, against a 0.25 s floor, and the whole 1 TB volume took
about four minutes. Pool free space did not move, because sanoid's 31 daily
snapshots still pin the freed blocks — `usedbysnapshots` on the OpenEBS zvol went
130 GB to 641 GB. That was expected; the backup read volume is the point.

Judge the next full read on **duration**, not on `transferred`, which reports
logical device size regardless of allocation.

### The trim's effect, against a matched control

2026-08-21 was a second full re-read: the `tofu apply` that turned `discard` on
restarted all three guests and reset their bitmaps, which was the known price of
enabling it. That makes it the control for 08-19's full re-read of the same
device.

| VM 910, full re-read | zeros detected | duration | throughput |
|---|---|---|---|
| 08-19, before the trim | 293.35 GiB (26%) | 2h03m42s | 155.1 MiB/s |
| 08-21, after the trim | **753.79 GiB (67%)** | **1h19m38s** | 240.9 MiB/s |

460 GiB of freed-but-allocated blocks became detectable zeros, and a full re-read
costs 35% less wall time. `transferred` reads 1.10 TiB on both nights, which is
exactly why it is not the measure.

Drift resumes immediately, because the guest mount carries no `discard` option
and only an explicit `fstrim` returns anything. `referenced` was 307 GB after the
08-20 trim, 279 GB three days later against 122 GB of live data, and 252 GB after
a second trim on 08-23. `kubernetes/infrastructure/fstrim.yaml` now runs monthly
on all three nodes, covering `/var` everywhere and `/var/openebs` on the workers.

The other four filesystems each returned something on 08-23, though nothing on
worker-0's scale: worker-0 `/var` 33.6 → 31.2 GB, worker-1 `/var` 15.0 → 14.1 GB
and `/var/openebs` 15.4 → 14.7 GB, control plane `/var` 3.32 → 3.08 GB. Three of
those zvols reference *less* than the guest reports using, because lz4 is on
everywhere, so a zvol sitting below the guest figure does not mean there is
nothing to trim.

## Every backup pages

Measured 2026-08-23 across six nights. `etcdHighFsyncDurations` warns above
0.5 s, goes critical above 1 s, and carries `for: 10m`. Longest **contiguous**
run above each, in the 09:50–12:50 UTC window:

| night | > 0.5 s | > 1.0 s | failed proposals | job duration |
|---|---|---|---|---|
| 08-18 | 15m | 13m | 157 | 21.9m |
| 08-19 *(bitmap reset)* | **137m** | 135m | 463 | 142.1m |
| 08-20 | 14m | 13m | 74 | 22.9m |
| 08-21 *(bitmap reset)* | **91m** | 27m | 68 | 97.8m |
| 08-22 | 16m | 14m | 68 | 23.1m |
| 08-23 | 13m | 11m | 49 | 19.1m |
| quiet, 00:00–09:00 | 0m | 0m | **0** | — |

An ordinary night spends 13–16 minutes above the threshold against a 10-minute
`for`, so the alert fires every night and distinguishes nothing. The earlier
claim in this file that crossing 0.5 s takes a full-read backup was wrong: it was
inferred from one night, and from whether the *other* alerts fired.

Note also that 08-21 — 91 minutes of storm — produced 68 failed proposals, the
same as 08-22's 14-minute ordinary incremental. A long sequential re-read is
gentler per minute than a short scattered one.

### `etcdHighNumberOfFailedGRPCRequests` cannot mean what it says here

The rule is a ratio under `sum without (grpc_type, grpc_code)`, which keeps
`grpc_method`, so each method is its own series. `LeaseKeepAlive` is a bidi
stream, and `grpc_server_handled_total` counts a stream once — when it
terminates. Working streams are never counted. In 08-23's backup window the
entire `LeaseKeepAlive` series was **five events, all `Unavailable`**: 5/5 =
100%, from five samples in three hours. At rest it is 0/0 and the ratio is NaN.

The alert therefore reads "did any lease stream break in the last five minutes",
not "is a meaningful fraction of requests failing". Upstream's own
`etcdGRPCRequestsSlow` filters `grpc_type="unary"` for this reason; this rule
does not. Filtering to unary and aggregating across methods gives a usable
signal: 0.00% at rest, 0.6–1.8% peak on ordinary nights, 2.75% on 08-19.

**Offered 2026-08-23 and not taken**, together with widening
`etcdHighFsyncDurations` to `for: 30m`, which separates 13–16m ordinary nights
from 91–137m bad ones with a clean margin. The backup tuning below was preferred
first, on the grounds that a quieter backup is worth more than a quieter alert.
Both stay available if it does not clear the nightly page.

## Backup tuning applied 2026-08-23

Two changes to the vzdump job, which lives in `/etc/pve/jobs.cfg` and outside
git — [`vzdump-job-in-terraform.md`](vzdump-job-in-terraform.md) is the attempt
to fix that, blocked on a provider release. Neither change is verified yet; the
next ordinary night's numbers decide.

- **`--exclude` now carries 101 and 106** alongside 100 and 107. Both are stopped
  scratch VMs, and a stopped VM has no QEMU process and therefore no dirty
  bitmap: it is read in full every night, permanently. 106's zvol holds 81.4K and
  was read as 32 GiB of zeros nightly. 101 accounted for the entire 10:01–10:02
  spike — fsync at 3.5 s for two minutes, off a 68-second read at 482 MiB/s.
- **`--performance max-workers=2`**, from a default of 16.

`max-workers` is a different axis from `bwlimit` and is not a way of reopening
that decision. Bandwidth is not the binding constraint: worker-1's incremental
reads 3.58 GiB at 22.6 MiB/s and still drives fsync p99 to 3.2 s, on eight
spindles that do several hundred MiB/s sequentially. What costs is the number of
scattered reads in flight, which is what `max-workers` caps. The mechanism is
written up in [`docs/talos.md`](../docs/talos.md).

The cost is a longer window: the VM phase should slow by roughly the factor the
queue depth falls, and if it stretches past ~30 minutes the exposure is longer
even though each minute is cheaper. Watch for `ignoring 'max-workers' setting` in
the task log, which is what a QEMU without `backup-max-workers` reports; the
running build is pve-qemu-kvm 11.0.0 and supports it.

Verify from the same three numbers, on a night with no VM restart behind it:

```bash
# per-VM duration and throughput, not `transferred`
ssh root@vulcanus.forge.local 'grep -h vzdump /var/log/pve/tasks/index | tail -1'
```

and, against the table above, the longest contiguous run of
`histogram_quantile(0.99, rate(etcd_disk_wal_fsync_duration_seconds_bucket[5m]))`
over 0.5 s, plus `increase(etcd_server_proposals_failed_total[3h])`. An ordinary
night before these changes reads 13–16m and 49–74.

### First night on max-workers=2 — 2026-08-24, promising and confounded

Both settings took: the log opens with `--performance 'max-workers=2' --exclude
100,101,106,107` and carries no `ignoring 'max-workers' setting` warning.

| | 08-23 (16 workers) | 08-24 (2 workers) |
|---|---|---|
| job duration | 19.1m | 45.1m |
| fsync p99 peak | 3.50 s | **0.86 s** |
| minutes > 0.5 s | 13m | 11m |
| minutes > 1.0 s | 11m | **0m** |
| failed proposals | 49 | **0** |
| slow applies | 7,337 | 10,803 |

Zero failed proposals against a floor of zero at rest, verified on the raw
counter — flat at 204 across 181 consecutive successful scrapes, so it is an
absence of failures and not an absence of data. `etcdHighFsyncDurations` fired
**warning only, for about 90 seconds** at 10:34; the critical never even went
pending. That is one Pushover notification at priority 0 instead of two, one of
which was a priority-1 critical that overrides quiet hours.

The shape is what a queue-depth cap does. 08-23 was two violent spikes to
3.5–4.8 s with the 0.24 s floor between them; 08-24 is a 45-minute plateau at
0.4–0.9 s that touches 1.02 s once. Slow applies rising while failed proposals
fall to zero says the same thing: etcd was slowed for longer and blocked hard
enough to abandon a write never.

**It is not a clean test, and the confound is self-inflicted.** The trim the
previous afternoon dirtied ~860 GiB, so worker-0's read was 85% holes at
468 MiB/s rather than scattered allocated blocks at 24.6 MiB/s. Queue depth and
access pattern both moved.

The confound does point one way, though: last night read roughly **121 GiB of
real non-zero data against 08-23's ~8 GiB** — fifteen times more — and etcd came
out better on every measure. If sparseness were doing the work, the real-data
volume would not have gone up.

**Tonight is the clean A/B.** The trim's dirt was consumed on 08-24, so 08-25 is
an ordinary small scattered incremental with `max-workers=2`, directly against
08-23's ordinary small scattered incremental with 16. Compare on the same three
numbers.

## Most of what etcd writes is leader election

Measured 2026-08-20: ~3.9 of etcd's ~4.1 writes/sec are lease renewals. Real
cluster state changes are a rounding error. Renewal follows `retryPeriod`, not
`leaseDuration`, so widening a lease alone buys stall tolerance and leaves the
write load untouched.

`openebs-localpv-provisioner` was the largest single writer at ~28% of all etcd
writes — the legacy `endpointsleases` lock writes an Endpoints object *and* a
Lease every ~2 s, for one replica with nothing to fail over to. Disabling its
leader election removes that load and the crash together: its last log line
before each exit is `LeaderElection … stopped leading`.

This matters for the SLOG decision only in that it lowers the floor. It does not
change the fsync numbers, which are a property of the disks.

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

### Six nights on the widened leases

Verified 2026-08-23 from Prometheus. The 45 s leases have been in effect since
2026-08-18 17:06 UTC, and the openebs and CSI changes since 2026-08-20.

| night | vzdump ran | fsync p99 peak | `kube-scheduler` | `kube-controller-manager` |
|---|---|---|---|---|
| 2026-08-18 | 10:00:07 → 10:22:01 | 6.34 s | 5 restarts | 4 restarts |
| 2026-08-19 | 10:00:04 → **12:22:11** | 5.59 s | 1 restart | 1 restart |
| 2026-08-20 | 10:00:03 → 10:22:56 | 3.86 s | 0 | 0 |
| 2026-08-21 | 10:00:01 → **11:37:51** | 3.94 s | 0 | 0 |
| 2026-08-22 | 10:00:03 → 10:23:09 | 5.28 s | 0 | 0 |
| 2026-08-23 | 10:00:01 → 10:19:09 | 3.50 s | 0 | 0 |

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

`etcdHighFsyncDurations` fires on **every** backup, not only on full-read ones —
see [Every backup pages](#every-backup-pages) below, which corrects an earlier
reading of this same window.

Container restarts in the same three-hour windows, which is where the 2026-08-20
changes show up:

| container | 08-19 | 08-20 | 08-21 | 08-22 | 08-23 |
|---|---|---|---|---|---|
| `openebs-localpv-provisioner` | 25 | 6 | **0** | **0** | **0** |
| `csi-provisioner` | 19 | 3 | **0** | **0** | **0** |
| `csi-resizer` | 20 | 2 | **0** | **0** | **0** |
| `kube-scheduler` | 1 | 0 | 0 | 0 | 0 |
| `kube-controller-manager` | 1 | 0 | 0 | 0 | 0 |
| `kube-state-metrics` | 16 | 0 | 2 | 1 | 1 |

08-21 is the load-bearing column: a 98-minute backup, fsync above 0.5 s for 91
minutes, and every leader-election component held. Disabling openebs' election
and widening the two CSI sidecars took that failure mode out entirely.

`kube-state-metrics` is the exception and is not a lease problem: it exits with
code 2 when its apiserver watch fails, terminating itself rather than being
killed by a probe.

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

**Kingston DC2000B**, M.2 2280, into the board's M2_1 slot. PCIe 4.0 x4, 112-layer
TLC, hardware PLP covering cached data, 0.4 DWPD, five-year warranty. Equivalents
if it is unavailable: Micron 7450 PRO M.2, Kingston DC1000B. Last resort is a
SATA drive such as the DC600M into `ata9` — ~30–50 µs more per commit, and it
shares the 00:1f.2 controller with four pool disks, which is the read storm this
is meant to be immune to. That contention is probably not real at 155 MiB/s
across eight disks, but it cannot be shown either way and NVMe sidesteps it.

**Optane at price parity was weighed and rejected.** Its ~10 µs against a
capacitor-backed NAND drive's ~30–50 µs, and its flat tail under garbage
collection, are real advantages with no occasion to appear at 369 KB/s and QD1
into a 16 GiB partition with ~95% of the NAND as spare area — there is nothing
for GC to do. With both at ~$310 the tiebreakers are warranty, current production
and replaceability, and all three favour the DC2000B. Only a *lower* Optane price
flips this; a higher one never does.

### Deferred on price, 2026-08-20 — user's call

The device is settled. What is missing is a tolerable price: the DRAM and NAND
shortage has a DC2000B 240 GB at ~$310 against the sub-$100 it normally sits at,
and **$200+ is out of range**. Waiting for the market rather than buying at the
top.

Nothing above needs re-opening when prices recover — re-check the price, not the
decision. Buy the DC2000B or whatever has replaced it in that line, confirm PLP
against the two tells below, and run the procedure.

What the delay costs, so that it stays a decision rather than a drift: three of
the four 15 s-lease components have since been widened or had leader election
turned off, leaving `kube-state-metrics`, which exits on its own when the
apiserver watch fails rather than on a lease and so is untouched by that;
`kube-scheduler` and `kube-controller-manager` survive an ordinary night but have
already failed one long one; and every guest restart buys a full-read backup the
following night, which is the load that fired `etcdHighFsyncDurations`.
`ControlPlaneContainerRestarting` is the standing watch over the control-plane
half of that, and it reports without anyone remembering to look.

PLP is the one non-negotiable specification, and it is checked on the datasheet
rather than the marketing page. A drive that acknowledges a write from a volatile
buffer can lose the ZIL in exactly the power cut the ZIL exists to survive, and
it does so silently.

Two tells separate a real one from an OEM client drive wearing a retailer's
"enterprise" category — the Micron 2500 was the near-miss that prompted writing
this down, listed under Newegg's enterprise NVMe filter while Micron files it
under `client-ssd`:

- **The protection must cover cached or in-flight writes.** "Enhanced power-loss
  data protection", or capacitors named outright. "Power loss protection for
  data-at-rest" and "power loss signal support" are the client features: they
  keep already-written NAND and the FTL tables from corrupting, and lose the
  acknowledged write that has not left the buffer.
- **Endurance quoted as DWPD.** Client datasheets quote TBW alone.

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

**Where the widened leases break is still not bounded.** The only breach since
they went in was 2026-08-19 — a 137-minute stall peaking at 5.59 s, one restart
each. Three windows since have passed clean, including 08-21's 91-minute stall
and 08-22's 5.28 s peak, so the ceiling is above both of those and below
whatever 08-19 was. Depth and duration are entangled and neither has been varied
on its own. The sizing was against a tail whose top bucket is unbounded — two
fsyncs exceeded 8.192 s on 08-18 and how far is unknowable, because `+Inf` is
where the histogram stops — and against stalls that are bursty and correlated
rather than independent, so the retry budget buys less than the arithmetic
suggests.

**Whether `max-workers=2` helps is unmeasured.** It was applied 2026-08-23 and
the argument for it — that scattered reads in flight, not bandwidth, are what
etcd waits behind — is inference from throughput figures, not from a controlled
run. The counter-case is that it lengthens the window, so a night could come out
with a lower peak and a longer time above threshold, which is worse for the
alert and better for the control plane. One ordinary night settles it.

## Prompt to open with

> Read `todos/etcd-disk-latency.md`. etcd on `piraeus-control-plane-0` has p99
> backend commit ~0.42 s and WAL fsync ~0.25 s at rest, against etcd targets of
> 0.025 s and 0.010 s, because `rpool` is two raidz2 vdevs of spinning disks with
> no SLOG and the host contains no SSD at all. The nightly Proxmox backup pushes
> fsync past 8 s. Leader-election tuning has stopped the restarts — nothing but
> `kube-state-metrics` has restarted in a backup window since 2026-08-20 — but
> every backup still spends 13–16 minutes above the alert threshold and pages.
> The device is
> chosen and the procedure written — a Kingston DC2000B in the board's M2_1 slot
> — and the whole thing is parked on the NAND shortage having put it at ~$310
> against a normal sub-$100. Check whether the price has come back; if it has,
> walk the `zpool add` in and remove the Alertmanager silence at the same time,
> since the alert is how the fix gets confirmed.
