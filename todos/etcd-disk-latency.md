# etcd runs on spinning disks and is ~40x slower than it should be

Verified 2026-08-17, ~20:30–20:50 UTC, the same day etcd metrics were scraped
for the first time. `etcdHighCommitDurations` fired within hours of that scrape
going live. It is correct.

## Summary

etcd's synchronous writes land on `rpool`: two raidz2 vdevs of 7200 rpm
spinning disks, **no SLOG**. Every fsync waits on a ZIL commit to rotating
media. Every physical device in the host is rotational — there is no SSD or
NVMe anywhere in `vulcanus`.

The cluster works. It is not failing, and nothing is user-visible. But mutating
API calls sit on the Kubernetes SLO line, so there is little headroom before a
load spike pushes them over.

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
latency fix.

**Silenced in Alertmanager**, silence ID `43b35199-356a-4304-a1e9-288980fbcd3b`,
**expiring 2026-09-17**. The alert is permanently true until the storage
changes, and at `severity: warning` it re-pages Pushover every 12 h. The expiry
is deliberate: if this is still unaddressed in a month it starts paging again
rather than being silenced into oblivion. The silence is Alertmanager runtime
state on its PVC, not in git, so it is invisible to the repo — this file is the
only record of it.

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
plausible aggravator and nothing more.

## Prompt to open with

> Read `todos/etcd-disk-latency.md`. etcd on `piraeus-control-plane-0` has p99
> backend commit ~0.42 s and WAL fsync ~0.41 s, against etcd targets of 0.025 s
> and 0.010 s, because `rpool` is two raidz2 vdevs of 7200 rpm disks with no
> SLOG and the host contains no SSD at all. apiserver p99 PUT is 0.97 s against
> a 1 s SLO. The fix is an SSD with power-loss protection added as a SLOG; help
> me choose one and plan `zpool add rpool log <dev>`. Note the Alertmanager
> silence expires 2026-09-17.
