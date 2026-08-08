# Backups

Findings from an audit on 2026-08-06, done incidentally while deploying Pinepods.
This is the starting point for that work.

Since then, two of the prerequisites have been met. Alerting now exists —
Alertmanager routes to Pushover, so a failing CronJob is visible rather than
silent — and `rclone-b2` has been removed. Borgmatic is still unrepaired.

## Summary

**The Kubernetes-side backup stack is not functioning.** Borgmatic fails on every
run, and no `openebs-hostpath` volume is covered by anything at the cluster
level.

Note on capacity, since covering those volumes needs somewhere to put them: the
1 TB `openebs-hostpath` disk on `piraeus-worker-0` was 69% full on 2026-08-07,
with 607 GB of it Loki chunks that retention had never deleted. That was fixed
and 452 GB reclaimed, so the disk now sits at 25% with roughly 770 GB free.
Capacity is no longer the constraint it would have been. What is actually protecting
data today is ZFS replication to an offsite server, plus a whole-blob backup of
the Proxmox worker VM.

## What is actually working

| Mechanism | Scope | Status |
|---|---|---|
| ZFS send/recv to offsite server | Fileserver datasets | Working — the real backup |
| Proxmox worker VM blob backup | Everything on `openebs-hostpath` | Working, coarse |
| `rclone-dropbox` CronJob | `syncthing-pvc` → Dropbox | Healthy, completes every 15 min |

## What is broken

### Borgmatic — every run fails

`kubernetes/apps/borgmatic/` defines five backup groups. All five fail with
`Repository /mnt/repositories/<name> does not exist` and borg exit status 13:

| Config group | Source | Expected repository |
|---|---|---|
| audio | `/mnt/audio` | `/mnt/repositories/audio` |
| games | `/mnt/games` | `/mnt/repositories/games` |
| photos | `/mnt/photoprism`, `/mnt/salamander` | `/mnt/repositories/photos` |
| syncthing | `/mnt/syncthing` | `/mnt/repositories/syncthing` |
| video | `/mnt/video` | `/mnt/repositories/video` |

None of those five repositories exist. The borg share actually contains only
older, unrelated repositories: `bonnie`, `dump`, `private`, `snippets`,
`vulcanus-borgmatic`.

### The borg share is not writable from Kubernetes

`//192.168.0.105/borg` maps to `/mnt/backups/borg` on the fileserver. Samba
declares `writeable = yes` for it, but writes fail with permission denied — both
at the share root and inside existing repository directories. The cause is
filesystem-level: the directories are `root:root` mode 755 and the SMB user
(`rancher`) has no write access.

This is almost certainly why the five repositories were never created, and it
means borgmatic could not initialise them even if pointed correctly.

It also blocked an unrelated deployment: kubelet could not create a `subPath`
directory on that share for the Pinepods `pg_dump` mount, which prevented the pod
from starting until the mount was moved.

Reproduce with:

```bash
kubectl exec -n apps deploy/borgmatic -- sh -c 'touch /mnt/repositories/.wtest'
```

### rclone-b2 targeted a retired service — removed 2026-08-07

`kubernetes/apps/rclone/b2-cron-job.yaml` copied the borg share to Backblaze B2
daily. B2 is no longer used and the last run failed. Since it read from the borg
share, whose repositories are stale, it was carrying old data offsite even when
it did work.

Deleted. The `rclone-config` Secret still carries a `b2-path` key and a b2 remote
inside `conf`; both are inert now, and can be dropped next time that file is
edited with `sops`.

### Borgmatic config uses deprecated syntax

Its logs warn that sectioned configuration (`location:`, `storage:`, `retention:`,
`consistency:`, `hooks:`) is deprecated and slated for removal, and that both
`checks` and `repositories` now expect key/value pairs rather than lists of
strings. Any repair work should modernise the config rather than patch it.

## Coverage gaps

- **No `openebs-hostpath` volume is backed up at the cluster level.** Borgmatic
  mounts only SMB-backed PVCs. Everything on the worker's local disk — app
  config, databases — depends solely on the Proxmox VM blob backup.
- **Databases are backed up as files, not dumps.** A filesystem-level copy of a
  live Postgres or MariaDB data directory is not guaranteed to restore. The only
  logical dump in the cluster is Pinepods' own scheduled `pg_dump`, and that
  currently lands on `openebs-hostpath` (`pinepods-backups-pvc`) because the borg
  share was unwritable — so it inherits the same coarse coverage.
- **No restore has been tested.** Backups that have never been restored are
  unverified by definition, and this stack has been failing silently long enough
  that nobody noticed.
- **No alerting.** Borgmatic has failed every run for an unknown period without
  surfacing anywhere, despite Prometheus, Grafana, and Loki all running in-cluster.
  This is not a matter of nobody having written the rule — see below, the metrics
  it would need do not exist.

## Alerting: CronJobs covered, Flux objects still not

Updated 2026-08-08. Two separate gaps were conflated here originally.

**CronJob failure alerting now works.** Alertmanager was enabled on 2026-08-07
and routes to Pushover. `KubeJobFailed` fires off kube-state-metrics and needs
no per-job wiring, so `beets-import`, `podcast-feed-snapshot` and
`rclone-dropbox` are covered for free. It proved itself immediately by
surfacing two failed jobs nobody knew about.

Two caveats that matter for designing the backup work:

- **`KubeJobFailed` tracks the existence of a failed Job object, not current
  health.** With `failedJobsHistoryLimit: 1`, a single transient failure keeps
  alerting until either another failure replaces it or the Job is deleted by
  hand. Observed directly: `rclone-dropbox` failed on 2026-07-31, the next three
  runs succeeded, and the alert persisted for seven days regardless.
  `ttlSecondsAfterFinished` on the job templates would make failed Jobs
  self-clean, at the cost of losing the manual-acknowledgement property.
- **Borgmatic is not covered by it at all.** It runs its own cron inside a
  Deployment rather than as a Kubernetes CronJob, so no Job object is ever
  created and `KubeJobFailed` can never see it. It was given borgmatic's native
  `pushover` hook on 2026-08-07 to compensate.

**Flux object Ready state still has no metric.** That gap is unchanged, and the
rest of this section describes it. Investigated on 2026-08-07, after a broken
`ImagePolicy` went unnoticed for sixteen hours and surfaced only because someone
happened to look.

Scraping itself is healthy. The `flux-system` PodMonitor covers all six
controllers and every target is up. The problem is which metrics they emit:

| Metric | Present |
|---|---|
| `gotk_reconcile_duration_seconds_*` | yes |
| `gotk_event_http_*`, `gotk_receiver_http_*` | yes |
| `gotk_reconcile_condition` | **no** |
| `kube_customresource_*` | **no** |

`gotk_reconcile_condition` was the gauge that exposed each Flux object's Ready
state, and it has been removed from current Flux. Upstream's replacement is
kube-state-metrics' `customResourceState` feature, which this cluster's
kube-prometheus install does not configure.

**The consequence: no metric in Prometheus can express "a Flux object is not
Ready."** Not for `ImagePolicy`, not for `Kustomization`, not for `HelmRelease`.
A Grafana alert on GitOps health cannot currently be written at all.

Two ways to close it, roughly in increasing order of effort:

1. **notification-controller `Provider` + `Alert`.** Flux-native, event-driven
   rather than metric-derived, and the controller is already running. Alerts can
   be scoped to specific kinds and severities. Least work.
2. **kube-state-metrics `customResourceState`.** Restores queryable per-object
   Ready gauges, which is what Grafana dashboards and `for:`-style flapping
   suppression actually want. More setup, but it is the only option that gives
   history rather than notifications.

These are worth doing together with, not after, the backup repair — an alert on
CronJob failure is only useful once the things that fail constantly are dealt
with, or it trains everyone to ignore it. `rclone-b2` has since been removed;
borgmatic remains (see Cleanup candidates).

### Borgmatic's own monitoring hooks

Worth knowing before designing this, since borgmatic can report on itself and
needs no Kubernetes-side plumbing. Version 1.9.14 ships hooks for:

`apprise`, `cronhub`, `cronitor`, `healthchecks`, `loki`, `ntfy`, `pagerduty`,
`pushover`, `sentry`, `uptime_kuma`, `zabbix`

`pushover` is already wired for failures. **`healthchecks` is the one to reach
for next**: a healthchecks.io account is already in use for the Alertmanager
dead-man's switch, and the free tier covers 20 checks against the one currently
used. Pointing borgmatic at a second check turns "the backup did not run at all"
into an alert, which is the failure that `on_error` cannot catch — a cron that
never fires produces no error to hook.

That distinction is the whole reason the backups were invisible: something that
stops happening emits nothing.

### Two failure shapes to design for

Both were observed directly and neither produces a symptom on its own:

- **Fails open.** A broken `ImagePolicy` breaks nothing — the Deployment keeps
  running whatever tag is committed. It simply stops updating, indefinitely.
  Structurally identical to borgmatic failing every run while everything appears
  to work.
- **Green but wrong.** During diagnosis, a policy with range `>=0.0.0` against
  `postgres` resolved to **9.6.24** and reported `Ready=True`, because the 9.x
  line was the only one still publishing three-component tags. A satisfied policy
  pointed at a nine-year-old release is indistinguishable from a healthy one in
  `kubectl get imagepolicy`. Any check must assert the resolved value, not just
  the condition.

## Questions to settle before rebuilding

1. **Is borg still wanted at all?** If ZFS replication covers the fileserver and
   the Proxmox blob covers the worker, borgmatic may be redundant rather than
   broken — in which case the fix is deletion, not repair. Its value would be
   deduplicated version history that ZFS snapshots do not provide in the same form.
2. **Does ZFS replication cover the Proxmox host pool, or only the fileserver?**
   This determines whether anything on `openebs-hostpath` is genuinely offsite.
3. **What is the recovery goal?** Losing the worker VM, losing the fileserver, and
   losing the site are three different problems with different answers.
4. **Should databases dump logically before being captured?** If so, a generic
   pre-backup dump step matters more than which tool stores the result.
5. **What should alert, and where?** A backup system that fails silently is worse
   than none, because it produces false confidence.

## Cleanup candidates

- `kubernetes/apps/rclone/b2-cron-job.yaml` — B2 retired. **Removed 2026-08-07.**
- `kubernetes/apps/borgmatic/` — repositories missing, share unwritable.
  Still outstanding, and now noisier: borgmatic gained a native `pushover` hook
  on 2026-08-07, so each failed run notifies. That is correct behaviour rather
  than a regression, but it means the repair is no longer something that can be
  quietly deferred.
