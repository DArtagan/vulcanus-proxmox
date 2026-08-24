# Disc ripping: reliability and accumulated drift

## Opening prompt

> Automatic Ripping Machine has completed 3 of 11 jobs successfully, and
> Blu-ray specifically is 1 in 5. Read `todos/disc-ripping-reliability.md` — it
> has the job history, the two live defects and the documentation drift, all
> verified 2026-08-24. Start with the Blu-ray failures, which need a disc
> physically loaded so the rip can be watched live; the logs from the previous
> attempts have aged out and there is no other way to see one fail. The rest is
> independent and can be done in any order.

Everything below was verified on **2026-08-24** against ARM `2.23.2`, pod
`automatic-ripping-machine-845c9b6987-bbrj5`, unless stated otherwise.

## The job history

Queried from `/root/db/arm.db` (persistent, on the worker-1 OpenEBS PVC):

| job | date | type | status | title | error |
|---|---|---|---|---|---|
| 9 | 2026-05-03 | bluray | fail | The-Rescuers-35th-Anniversary-Edition | *(none recorded)* |
| 8 | 2026-04-22 | bluray | fail | The Rescuers | *(none recorded)* |
| 7 | 2026-04-21 | bluray | fail | The Rescuers | *(none recorded)* |
| 6 | 2026-04-20 | bluray | fail | The Rescuers | Error while running MakeMKV |
| 5 | 2026-04-19 | bluray | **success** | The Rescuers | |
| 4 | 2026-04-19 | dvd | fail | not identified | Received SIGTERM |
| 3 | 2026-04-18 | dvd | **success** | Le Mans | |
| 2 | 2026-04-17 | dvd | fail | Le Mans | Error while running MakeMKV |
| 1 | 2026-04-17 | dvd | **success** | not identified | |

Tally: bluray 1 success / 4 fail, dvd 2 success / 2 fail.

Note the shape — the same disc succeeds and then fails, or fails and then
succeeds, on consecutive nights. Whatever this is, it is intermittent rather
than a disc that simply cannot be read.

## 1. Blu-ray failures

The most detailed case is **job 8** (2026-04-22, `RESCUERS_35TH_ANNIVERSARY`).
It got further than any of the others:

- `no_of_titles: 71` — MakeMKV enumerated the disc
- `title: The Rescuers`, `year: 1977`, `imdb_id: tt0076618`, `hasnicetitle: 1`
- `path: /root/video/completed/movies/The Rescuers (1977)_177682876149` allocated
- then `status: fail` with **`errors: NULL`**

So identification and path allocation both succeeded and it failed afterwards
without recording a reason. Job 6 is the opposite shape: it ran 41 seconds
(04:43:19 → 04:44:00) and died with `Error while running MakeMKV`.

**The logs are gone and this is expected, not a defect.** `LOGLIFE: 90`
(`config-map.yaml:157`) ages out `/root/logs`, and April 22 plus 90 days is
July 21. `/root/logs` *is* on the PVC and survives pod restarts — this was
checked, because the obvious guess is that a restart lost them, and it is wrong.

**Therefore this can only be diagnosed live.** Load a Blu-ray, watch
`/root/logs/<logfile>` and `makemkvcon` as it runs. Before starting anything by
hand, check no rip is in progress (`pgrep -af makemkv`) — concurrent SCSI
commands to this drive are what destabilise the ATA link, see
`docs/automatic-ripping-machine.md`.

Worth checking during that run, none of which has been established:

- Whether MakeMKV's beta key was valid at the time. `MAKEMKV_PERMA_KEY` is
  populated from `${MAKEMKV_KEY}`; a lapsed key fails at rip time, after
  identification, which matches job 8's shape.
- Free space on the target at the moment of failure. `backup_dvd` writes the
  full decrypted disc structure before HandBrake runs, so a 50 GB Blu-ray needs
  the raw copy *and* the transcode. `/root/video` is the SMB share
  (`//192.168.0.105/media/video`, 11T free today), so this is unlikely — but it
  is cheap to rule out and it would explain a silent post-identification failure.
- Whether the four consecutive attempts were a human retrying, or ARM retrying
  by itself. `ALLOW_DUPLICATES: true`, so nothing stops the same disc producing
  a new job on every insert.

## 2. A disc already in the drive when the pod starts is never seen

ARM triggers only on a udev *insert* event, which is delivered to the container's
`systemd-udevd` as described in `docs/automatic-ripping-machine.md`. There is no
scan of the current drive state at startup.

Observed accidentally on 2026-08-24: a disc had been in the drive since
2026-08-18, the ARM pod started 2026-08-20 17:22, and the database contains no
job for it at all until 14:40 on 2026-08-24, when an eject cycled the tray and
generated a fresh insert event.

So any pod restart with a disc loaded — a Flux image update, a node reboot, an
eviction — silently does nothing, and there is no signal that it happened. The
fix is a startup check of `CDROM_DRIVE_STATUS` that synthesises a job when the
tray already holds a disc; `arm-disc-wrapper.sh` in
`kubernetes/apps/automatic-ripping-machine/init-scripts.yaml` is the natural
place, since it already handles the drive-state cases for udev events.

Note when testing: read the drive with `O_NONBLOCK`. A blocking `open()` on
`/dev/sr0` auto-closes an open tray, which will confuse any manual test.

## 3. A node reboot can leave a permanently Failed ARM pod

`automatic-ripping-machine-845c9b6987-krk56` has been sitting in the namespace
since 2026-08-18T18:29:33Z — the minute worker-1 came back from the reboot in
`8d5c017`. Its status message:

```
Pod was rejected: Allocate failed due to no healthy devices present;
cannot allocate unhealthy devices devic.es/cdrom, which is unexpected
```

The kubelet tried to admit the pod before generic-device-plugin had registered a
healthy `devic.es/cdrom`. Admission failed, the pod went to `Failed`, and a
ReplicaSet does not garbage-collect Failed pods — so it stays until deleted by
hand. A replacement was created and works, so nothing is broken; the concern is
the mechanism, not this instance.

Two reasons it is worth more than a `kubectl delete`:

- It recurs on every node reboot that loses the race.
- **It couples to [generic-device-plugin-hang.md](generic-device-plugin-hang.md).**
  That plugin wedges and stops serving; if a wedge overlaps any ARM pod
  recreation, the device reports unhealthy and admission fails exactly this way.
  Whichever fix is chosen there — dropping the 50m CPU limit or adding the
  liveness probe — shortens the window this race can land in. Worth deciding
  together rather than separately.

The durable fix is probably ordering rather than deletion: ARM tolerating a
not-yet-ready device and retrying, instead of failing admission once and giving
up. Confirm what the kubelet actually offers here before designing for it.

## 4. Dead configuration

```yaml
RIPMETHOD_DVD: "PLACEHOLDER"    # config-map.yaml:208
RIPMETHOD_BR:  "PLACEHOLDER"    # config-map.yaml:209
```

Neither key is read anywhere in ARM 2.23.2 — `grep -rn RIPMETHOD /opt/arm/arm/`
returns only `RIPMETHOD`, which *is* used (`makemkv.py:752` selects the backup
path for Blu-ray, `arm_ripper.py:235` for `backup_dvd`). These two are inert.

Delete them. This is the "never expose an option whose other setting is simply
wrong" rule in `CLAUDE.md` — except worse, because these have no working setting
at all, and a future reader will reasonably assume `RIPMETHOD_BR` is what governs
Blu-ray behaviour while debugging item 1.

**A ConfigMap-only change will not reach the running pod.** See
[config-change-rollouts.md](config-change-rollouts.md); restart the Deployment
after any edit here, and do not trust `flux get kustomizations` as evidence the
change took effect.

Also consider `LOGLEVEL: DEBUG` (`config-map.yaml:153`). `arm.log` is dominated
by per-second `ServerUtil` polling of CPU, memory and disk, which is written to
the OpenEBS disk continuously and buries genuine errors. `INFO` would make item 1
materially easier to read. Left as a judgement call rather than a
recommendation — DEBUG may have been set deliberately for exactly this kind of
investigation.

## 5. Documentation drift in `docs/automatic-ripping-machine.md`

Both of these make the documented commands fail as written, which matters most
to whoever opens this spec cold and starts pasting them.

- **Namespace.** Nine commands say `-n apps` (lines 329, 385, 400, 403, 406,
  409, 415, 418, 421). ARM lives in its own `automatic-ripping-machine`
  namespace — a dedicated one, because it needs `SYS_ADMIN`.
- **Paths.** Five references to `/root/media/raw/` and `/root/media/completed/`
  (lines 271, 272, 283, 289, 421). The mount was renamed to `/root/video` in
  `ec63698`; `ls /root/` today gives `audio db logs video` and no `media`. Job 6
  (April, pre-rename) still records `/root/media/completed/...` while job 8
  records `/root/video/completed/...`, which dates the change.

This is `docs/` describing something that is not true of the running system,
which by the repository's own definition is a bug in the docs. Fix it as part of
whichever item is done first, rather than as its own task.

## What was not investigated

- Why the Blu-ray jobs failed. Nothing here identifies a cause — the evidence
  needed no longer exists. Every candidate in item 1 is a hypothesis.
- Whether the DVD failures share a cause with the Blu-ray ones. Job 4 was
  `Received SIGTERM`, which is a pod restart mid-rip and probably unrelated;
  job 2 was `Error while running MakeMKV`, which matches job 6.
- Whether the drive itself is degrading. The ATA link history in
  `docs/automatic-ripping-machine.md` gives a known failure mode with a
  documented recovery, and no `ata4` events were seen on 2026-08-24 — but the
  April window was not checked, and a marginal drive would also produce
  intermittent same-disc failures.
