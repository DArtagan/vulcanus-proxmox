# Disc ripping: the evidence

What [`disc-ripping.md`](disc-ripping.md) rests on: the survey that opened the
project, each defect as it was first found, the finished phases as they happened,
and the progress log, which holds the drive investigation entry by entry. Read the
spec first, and open this to re-examine one of its conclusions. A session adds its
log entry at the end; what the entry changes about where things stand goes in the
spec.

Defects and entries here are as written at the time, with corrections marked
where they were later overturned. The spec carries what is true now.

## What this supersedes, and where it was wrong

This replaces `todos/disc-ripping-reliability.md` (written 2026-08-24, deleted
when this file was created). Its factual observations were sound but three of its
framing premises were wrong, and inheriting them would have sent the work in the
wrong direction:

- **"Blu-ray is 1 success in 5, DVD 2 in 4."** Those "successes" produced no
  file. `find /root/video/completed -type f` finds **nothing under `movies/`** —
  job 3 (Le Mans, "success") went from "rip complete, starting transcode" to
  "processing complete" in **1 second**; job 5 (The Rescuers, "success") took
  **38 seconds** for 28 tracks. Both ran before commit `25aa385` (2026-04-20)
  fixed the HandBrake preset argument, so HandBrake failed instantly on every
  title and ARM recorded success anyway. The only video file any job ever
  produced is job 1's `completed/unidentified/not identified/not identified.mkv`
  (7.4 GB), and that is MakeMKV's own output from before transcode was turned
  back on in `491441f`. **The true score is 0 of 12 jobs producing a transcoded
  file**, which is a different and more tractable problem than an intermittent
  one.
- **"The logs are gone, so this can only be diagnosed live."** Not so. The
  Proxmox host has a **persistent journal going back to 2023-10-22**, the ARM
  `notifications` table holds a full narrative of every job, and the partial
  output files on the share are timestamped. Between them the April failures are
  now largely explained without needing a disc loaded. Check those three before
  concluding evidence is missing.
- **"Whatever this is, it is intermittent."** It is not. The same three
  mechanisms fire every time; they just present differently depending on how far
  the job got.

Two of its items are still live and carried forward below: the dead
`RIPMETHOD_DVD`/`RIPMETHOD_BR` keys (D10) and the documentation drift (D11). Its
item 2 (a disc already in the drive at pod start is never seen) is carried
forward as D2b. Its item 3 (a node reboot can leave a `Failed` ARM pod) is
carried forward as D9.

One claim it repeated from `docs/` is now **disproven**: see D7.

## How a disc travelled, as surveyed 2026-08-24

```
disc inserted
   │  guest kernel polls sr0 every 2s  (events_poll_msecs=2000)
   ▼
udev "change" event → container udevd → 51-docker-arm.rules
   │  arm-disc-wrapper.sh: proceed only if CDROM_DRIVE_STATUS == 4
   ▼
python3 /opt/arm/arm/ripper/main.py -d sr0
   │  identify.identify()  → parse_udev() reads ID_CDROM_MEDIA_* → disctype
   │  check_for_wait()     → BLOCKS for MANUAL_WAIT_TIME  ← see D1
   ▼
 ┌─ music ──→ abcde → /root/audio/<Artist> <Album>/NN - Track.flac
 │                    = //192.168.0.105/audio-rw/import  → beets-flask watches
 │
 └─ dvd/bluray ─→ MakeMKV backup_dvd → /root/video/raw/<Title>/
                  HandBrake          → /root/video/transcode/<type>/<Title>/
                  move               → /root/video/completed/<type>/<Title>/
                    = //192.168.0.105/media/video/import/automatic-ripping-machine/
                      → nothing watches this  ← see "Where the media lands"
```

Sizing that matters: `piraeus-worker-1` is **4 cores / 8 GiB**, of which 2 GiB is
locked in hugepages (`vm.nr_hugepages: 1024` in
`terraform/modules/proxmox_talos_vm/files/openebs-kubelet-patch.json`), leaving
**5.5 GiB allocatable**. It also hosts the entire `flux-system` control plane,
both ingress-nginx controllers, CoreDNS, MetalLB and VictoriaLogs.

## Cross-cutting defects, as first found

These apply to every media type. Phase 0 exists to close them.

### D1 — `MANUAL_WAIT` wedges the drive permanently. **This is the headline.**

`utils.check_for_wait()` is called from `main.py:117` **unconditionally, for
every disc type**, before any ripping. With `MANUAL_WAIT: true` and
`MANUAL_WAIT_TIME: 31536000`, a job that nobody manually identifies in the web UI
**sleeps for a year holding the drive**.

While it sleeps, `SystemDrives.job_id_current` stays set, so
`utils.py:787` raises `RipperException("Job already running on /dev/sr0")` for
**every subsequent insert**. The only way out is to open the ARM UI and abandon
the job.

This is not hypothetical — it is the state of the machine right now:

```
job 12  2026-08-24 14:41:01  music  "Mànran The Test"  status=waiting  stop_time=NULL
        process 1019 (main.py -d sr0) still alive, sleeping in check_for_wait
        drive record: job_id_current=12
        CDROM_DRIVE_STATUS = 2  (tray open — the CD was removed at 15:12)
```

The next disc the brother inserts will fail. And the same trap explains most of
the `notifications` history:

| when | what |
|---|---|
| 2026-04-19 05:18 | `Job: 366 was Abandoned!` — someone unwedged, then job 5 could start |
| 2026-04-22 03:37–03:38 | three × `Job already running on /dev/sr0` |
| 2026-05-04 00:10 | a CD insert rejected — `Job already running`, blocked by job 9 from the night before |
| 2026-05-06 02:37 | `Job: 1981 was Abandoned!` — unwedged again, then job 10 |

**A pod restart does not clear it, and it is worth knowing why before trying.**
Three pieces of ARM conspire:

- `duplicate_run_check()` raises at `main.py:201`, while `clean_old_jobs()` — the
  thing that would mark the zombie failed — only runs at `main.py:232`. The
  guard fires first, so the new job dies before the cleanup it needed.
- `SystemDrives.processing` is `job_current is not None`. It does not look at
  the job's *status*, so marking a job failed does not free the drive.
- `update_job_status()`, which does call `release_current_job()` for a finished
  job, runs from `ui/settings/settings.py:115` — a settings **page view**. It is
  not part of startup.

So the drive stays held until someone abandons the job through the UI, which
calls `job.eject()` and releases it. That is the only path.

**Fix direction.** Not `MANUAL_WAIT: false` — commit `588af26` (2026-04-17,
"Always wait for manual identification of DVD, if unknown") records a deliberate
intent to keep a correction window. Keep the wait, bound it: a
`MANUAL_WAIT_TIME` of a few hundred seconds gives a real window and then lets the
job proceed on the automatic identification. Confirm the exact number with Will.

**A short wait loses less than it looks like it does**, which is what makes this
easy. `MANUAL_WAIT_TIME` bounds only the *blocking* window, not the correction
window. `arm_ripper.py:81` re-derives the final directory from `job.title_manual`
**after** the transcode, so a title set at any point before post-processing still
lands the files under the corrected name:

```python
if job.title_manual:
    utils.delete_raw_files([final_directory])
    job_title = utils.fix_job_title(job)
    final_directory = os.path.join(job.config.COMPLETED_PATH, type_sub_folder, job_title)
```

For a Blu-ray that is a real correction window of five or six hours, whatever
`MANUAL_WAIT_TIME` says.

Whatever the number, ARM must never be left in a state where a disc that nobody
touches blocks the next one.

### D2 — every insert fires two jobs, and the first one fails

`arm-disc-wrapper.sh` gates on `CDROM_DRIVE_STATUS == 4` (disc present, tray
closed). The drive reports that **before the TOC is readable**, so the first ARM
process finds no `ID_CDROM_MEDIA_*` properties at all and dies:

```
job 11  14:40:55 → 14:40:58   disctype=unknown   "Could not determine disc type"
job 12  14:41:01 → ...        disctype=music     (same disc, 6 seconds later)
```

`Job.parse_udev()` (`arm/models/job.py:170`) sets `disctype` purely from
`ID_CDROM_MEDIA_BD` / `ID_CDROM_MEDIA_DVD` / `ID_CDROM_MEDIA_TRACK_COUNT_AUDIO`.
Verified 2026-08-25 with the tray open: `udevadm info /dev/sr0` reports many
`ID_CDROM_*` **drive capability** flags (`ID_CDROM_BD=1` etc.) and **no
`ID_CDROM_MEDIA_*` keys whatsoever**. Those two families are easy to confuse and
only the `_MEDIA_` ones say what is in the tray.

Same shape on 2026-05-06 (job 10) and 2026-04-09. It also litters the share:
**76 of the 161 directories in `/root/video/raw/` are empty**, including 18
named `Puccini_-_Manon_Lescaut_<stage>`.

**Fix direction.** Extend `arm-disc-wrapper.sh`
(`kubernetes/apps/automatic-ripping-machine/init-scripts.yaml`) to require at
least one `ID_CDROM_MEDIA_*` property before invoking ARM, retrying with a short
backoff. That is the same file and the same gatekeeper pattern already there for
the tray-open case, so it is a small change.

### D2b — a disc already in the tray when the pod starts is never seen

There is no scan of drive state at startup; ARM triggers only on a udev *insert*
event. Observed 2026-08-24: a disc had been in the drive since 2026-08-18, the
pod started 2026-08-20 17:22, and no job existed for it until an eject/insert
cycle at 14:40 on 2026-08-24. Any pod restart with a disc loaded silently does
nothing.

**This is harder than it looks, and the obvious fix does not work.**
`Job.parse_udev()` reads the `ID_CDROM_MEDIA_*` properties from the **udev
database**, via pyudev. That database lives in the container's `/run/udev` and
is empty at container start, so it has nothing about a disc that was loaded
before the pod existed — replaying the *call* is not enough, the properties have
to be there.

The natural way to populate them is `udevadm trigger`, and it is not available
(verified 2026-08-25):

```
sr0: Failed to write 'change' to '/sys/.../block/sr0/uevent': Read-only file system
```

`/sys` is mounted `ro` because the container is not privileged — it carries only
`SYS_ADMIN`. Probing with `/usr/lib/udev/cdrom_id` directly would tell the
*wrapper* what is in the tray, but would not put anything in the database for
ARM to read, so ARM would still fail.

That leaves **cycling the tray** as the only mechanism that generates a genuine
kernel media-change event and so populates the database — and that is **ruled
out** (Will, 2026-08-25). The drive sits in a server enclosure with a door, left
open only while someone is deliberately feeding it discs and closed the rest of
the time. A pod or node restart happens overwhelmingly when nobody is there, so
an automatic `eject` would drive the tray into a closed door.

So ARM cannot be made to notice the disc by itself. What it *can* do is stop
being silent about it: `CDROM_DRIVE_STATUS` works fine — it is only the udev
*properties* that are missing — so a startup check can tell that a disc is
loaded, see that no job is running for it, and say so. Then the person who put
the disc in learns they need to open the tray and close it again, which is the
action the enclosure requires anyway.

That is the remaining shape of D2b, and it is worth little until D5 gives ARM
somewhere to say it. Do them together.

Note when testing: open `/dev/sr0` with `O_NONBLOCK`. A blocking `open()`
auto-closes an open tray.

### D3 — a job that dies mid-transcode records nothing about why

`utils.clean_old_jobs()` (`utils.py:702`) runs at UI start, finds every job whose
recorded PID no longer exists, and sets `status = fail` — **without setting
`errors` and without setting `stop_time`**. That exact signature (fail /
`errors` NULL / `stop_time` NULL) is jobs 7, 8 and 9. It means the ripper process
died without its `finally:` block running, so nothing about the cause survives.

What *is* known about job 8 (2026-04-22), reconstructed from file timestamps:

```
03:32:41  job starts        04:37:45  "rip complete. Starting transcode."
04:50     title_43.mkv  (526 s source)      13 min
06:35     title_44.mkv  (1842 s source)    105 min
06:48     title_46.mkv  (633 s source)      13 min
08:10     title_70.mkv  (4633 s source — the film)   82 min, 312 MB
          → then nothing. title_71 (4628 s) never written.
```

So the rip was fine, 43 GB of BDMV landed, and the transcode was roughly 85%
through when the process vanished. The pod IP moved from `10.244.4.245` (job 7)
to `10.244.4.246` (job 8), which shows the pod was recreated between them.

**The cause is not established.** Candidates, in order of plausibility:

- **Node memory pressure.** ARM requests 1.5 GiB and limits 12 GiB on a node with
  5.5 GiB allocatable — see D6. As Burstable with a huge usage-over-request
  ratio it is the first eviction candidate, and it has no PriorityClass.
- **Eviction or SIGKILL after the grace period.** ARM *does* handle SIGTERM
  (job 4 recorded `Received SIGTERM` correctly), so a clean eviction would have
  been recorded. A SIGKILL 30 s later would not.
- **Node reboot.** The Proxmox journal shows VM 911 was *not* stopped or
  restarted on 2026-04-21/22, so this happened inside the guest if at all.

Ruled out: the nightly Proxmox backup. It runs at **04:00 America/Denver** and
VM 911's slice of it took **69 seconds** on 2026-04-22 (04:22:33 → 04:23:42),
finishing before job 8 died. The 22-minute figure in
[etcd-disk-latency.md](etcd-disk-latency.md) is a different guest.

**Fix direction.** Two parts, and the second matters more than the first:
make `clean_old_jobs` distinguishable from a real failure so the next occurrence
is diagnosable, and alert on it. A job that dies silently after four hours of
transcoding is invisible to everyone until someone opens the UI.

### D4 — ARM reports success when HandBrake produced nothing

Jobs 3 and 5, above. `handbrake_all()` iterates tracks and a per-track failure
does not fail the job; `main.py` falls through to `JobState.SUCCESS`. Whatever
else changes, **the completion check must be "a file exists and is non-trivial",
not "the pipeline reached the end"** — that is the data-integrity priority
expressed as a test.

The same hole exists on the audio side. `utils.rip_music()` carries an upstream
`# TODO check output and confirm all tracks ripped; find "Finished\.$"` and
today trusts abcde's exit code alone.

### D5 — nothing tells anyone what happened, and the disc is never ejected

- `APPRISE: ""`, `PB_KEY`/`PO_USER_KEY`/`PO_APP_KEY` empty, `EMBY_REFRESH: false`.
  Every notification ARM generates goes to its own database table and nowhere
  else. The repo already runs Pushover for borgmatic
  (`kubernetes/apps/borgmatic/pushover-secret.yaml`,
  `kubernetes/infrastructure/notification-secrets.yaml`), so the credential and
  the pattern exist.

  **`APPRISE` is not the route to use.** ARM has native Pushover:
  `utils.py:70` adds `pover://<PO_USER_KEY>@<PO_APP_KEY>` to its apprise object
  whenever `PO_USER_KEY` is non-empty, with no apprise.yaml involved. The
  commented `PUSHOVER_USER`/`PUSHOVER_TOKEN` at the bottom of `apprise.yaml` are
  a second, redundant path — leave them alone.

  `config-map.yaml` now reads `PO_USER_KEY: "${PUSHOVER_USER_KEY}"` and
  `PO_APP_KEY: "${PUSHOVER_APP_TOKEN}"`, so **the two must be added to
  `api-keys.yaml` before this is pushed**. The `config-injector` init container
  builds envsubst's shell-format from the names actually present in the
  environment, so a missing variable is left in the file *verbatim* rather than
  blanked — ARM would then treat the literal string `${PUSHOVER_USER_KEY}` as a
  user key. `utils.py:73` wraps `apobj.notify()` in a bare `except`, so that
  costs notifications rather than rips: it logs "Failed sending notifications"
  once per event and carries on. Harmless, but indistinguishable from working.
- `AUTO_EJECT: false` — set deliberately in `a5d3b49` (2026-04-10, "Disable
  auto-eject while troubleshooting continues") and never turned back on. With it
  off, `Job.eject()` releases the job from the drive but leaves the disc in.
  `UNIDENTIFIED_EJECT: false` likewise.

For a person standing at the machine, the tray opening is the only signal that
does not require a browser. Both of these are directly on the self-service
priority.

### D6 — the resource limits are above the node's capacity, so they do nothing

```yaml
resources:
  limits:   { devic.es/cdrom: 1, cpu: 6000m, memory: 12Gi }
  requests: { cpu: 500m, memory: 1.5Gi }
```

`piraeus-worker-1` has 4 cores and 5.5 GiB allocatable. A 12 GiB limit never
fires, so instead of a recorded container OOM-kill (restartable, visible in
`NodeOOMKill`) the kernel picks a victim at node level — possibly one of the
Flux controllers or an ingress controller sharing the node. A 6000m CPU limit on
a 4-core node likewise never throttles.

Bring both under the node's real capacity. This is a targeted fix for the
mechanism in D3, not general hardening.

**The first correction was also wrong, and the error is worth keeping.** Dropping
the limit to 4Gi looked like it satisfied this, because 4Gi is comfortably under
the VM's 8 GiB. It is not under *allocatable*, which was 5.26 GiB — and with the
rest of the node's workload resident, ARM could never reach 4Gi before the node
died. The lesson is that "the node's real capacity" means
`kubectl get node -o jsonpath='{.status.allocatable.memory}'` minus what else is
already resident on it, never the VM's RAM. Sized against the VM, a limit reads
as generous while being unreachable, and the symptom is indistinguishable from
having set no limit at all. See the 2026-09-04 entry in the progress log for the
measurement that exposed it.

### D7 — `makemkvcon` **is** usable as a diagnostic. `docs/` says otherwise, and is wrong.

`docs/automatic-ripping-machine.md:160` states that `makemkvcon` reports "This
application version is too old" and is therefore unusable as a check. The real
cause is that `/root/.MakeMKV/settings.conf` **does not exist** outside a job:
ARM writes it in `makemkv.prep_mkv()` at rip time and nowhere else, so a manual
invocation runs unregistered and falls back to expired beta behaviour.

Registering the purchased key by hand fixes it (verified 2026-08-25):

```bash
kubectl exec -n automatic-ripping-machine deploy/automatic-ripping-machine -- sh -c \
  'bash /opt/arm/scripts/update_key.sh "$(python3 -c "import yaml;print(yaml.safe_load(open(\"/etc/arm/config/arm.yaml\"))[\"MAKEMKV_PERMA_KEY\"])")"'

kubectl exec -n automatic-ripping-machine deploy/automatic-ripping-machine -- \
  makemkvcon -r --cache=1 info disc:9999
# DRV:0,1,999,0,"BD-RE PIONEER BD-RW   BDR-212U 1.01 ALDL017235WL","","/dev/sr0"
```

Real vendor, model, firmware and serial — no version complaint. **This is the
main diagnostic instrument for phases 2–4 and it was believed unavailable.**
`update_key.sh` itself has a bash bug (`line 52: ((: > 0 : syntax error`) but
still writes a valid `settings.conf` and exits 0.

Before running it, check nothing is ripping: `pgrep -af makemkv` in the pod.
Concurrent SCSI commands to this drive are what destabilise the ATA link.

### D8 — the disc stays locked in the drive for the whole transcode

> **Wrong — corrected 2026-10-05, and decided the other way.** The running
> image ejects at the end of the MakeMKV stage. `makemkv.makemkv()` calls
> `job.eject()` (`/opt/arm/arm/ripper/makemkv.py:760`) before
> `rip_visual_media` starts the transcode, and by then `main.py`'s call does
> nothing. Job 43 ejected at 02:59:10 UTC and HandBrake started after it. Job 39
> ejected at 06:47 in a job that ran until 11:00. Which image introduced this
> was not checked. Will has decided the disc stays locked through the
> transcode and ejects only when processing is complete (see **Decisions already
> made**). So the current behaviour is the defect, and the options below are
> superseded.

**Fix:** the `eject-after-processing` edit in `arm-source-patches.py`
(`init-scripts.yaml`) drops that call. To verify it on the first video job after
deploy, ARM's log should show one `eject` line, after
`ARM processing complete`, not after `Exiting MakeMKV processing`.

`arm_ripper.rip_visual_media()` runs MakeMKV, then calls `start_transcode()`
inline, then moves files. `Job.eject()` is only reached from `main.py`'s
`finally:` block, **after all of that**. So the tray does not open until the
transcode is finished.

The transcode does not need the disc. Once `backup_dvd` has written the raw
`BDMV`/`VIDEO_TS` to the share, HandBrake reads from there
(`transcode_in_path = makemkv_out_path`) and never touches `/dev/sr0` again.

The cost is throughput, and it lands squarely on the self-service priority.
Measured on job 8: the rip took **65 minutes** (03:32 start → 04:37 "rip
complete") and the transcode a further **4–5 hours**. So someone feeding a stack
of discs gets **one disc per ~6 hours** when the drive is genuinely free after
one. Whoever is loading discs has no way to know which phase the machine is in
without opening the web UI.

**Options, for Will to choose between:**

- Eject after the rip stage and let the transcode run on. Needs a change to
  ARM's flow — the `BASH_SCRIPT` hook (`config-map.yaml:328`, currently `""`)
  fires post-processing, which is too late.
- Decouple entirely: `SKIP_TRANSCODE: true` so ARM only rips and files the raw
  backup, with transcoding as a separate workload that drains a queue. This also
  removes the transcode from the drive's critical path, makes D3 much less
  costly when it fires, and means a mid-transcode death loses cheap CPU time
  rather than the disc's turn in the drive. It is the larger change.

Do not settle this before phase 3 has produced one complete Blu-ray. The measured
numbers above come from a job that never finished.

### D9 — a node reboot can leave a permanently `Failed` ARM pod

`automatic-ripping-machine-845c9b6987-krk56` has sat in the namespace since
2026-08-18T18:29:33Z:

```
Pod was rejected: Allocate failed due to no healthy devices present;
cannot allocate unhealthy devices devic.es/cdrom, which is unexpected
```

The kubelet admitted the pod before generic-device-plugin registered a healthy
`devic.es/cdrom`. A ReplicaSet does not garbage-collect `Failed` pods, so it
stays until deleted. Nothing is broken — a replacement was created and works —
but it recurs on every reboot that loses the race.

### D10 — dead configuration

```yaml
RIPMETHOD_DVD: "PLACEHOLDER"    # config-map.yaml:208
RIPMETHOD_BR:  "PLACEHOLDER"    # config-map.yaml:209
```

Neither is read anywhere in ARM 2.23.2. `RIPMETHOD` (no suffix) *is* used —
`makemkv.py:752` selects the backup path for Blu-ray, `arm_ripper.py:235` for
`backup_dvd`. Delete the two placeholders: they have no working setting at all,
and a reader debugging Blu-ray will reasonably assume `RIPMETHOD_BR` governs it.

**Deleting them from the ConfigMap does not remove them from the running
config,** which was assumed and is wrong (verified 2026-08-25, after the change
reconciled). `config/config.py:33-38` loads our rendered file, loads ARM's
shipped `setup/arm.yaml` template, merges with `arm_config.update(cur_cfg)` so
our values win — and then **rewrites our file in place** from the merged result
plus `ui/comments.json`. A key we remove comes back from upstream's defaults,
and our own comments never survive into `/etc/arm/config/arm.yaml` at all.

So this achieved the thing actually worth achieving — nobody reading *this repo*
is misled any more — and nothing else. `RIPMETHOD_DVD: "PLACEHOLDER"` in the
running file is upstream's default, and only upstream can drop it. Do not
re-attempt the deletion expecting a different outcome; the fix is on the Upstream
list.

Also consider `LOGLEVEL: DEBUG` (`config-map.yaml:153`). `arm.log` is dominated
by per-second `ServerUtil` polling of CPU, memory and disk, written continuously
to the OpenEBS disk, and it buries genuine errors. `INFO` would make phases 2–4
materially easier to read — but DEBUG may have been set deliberately for exactly
this kind of investigation, so it is Will's call.

**A ConfigMap-only change will not reach the running pod.** See
[config-change-rollouts.md](config-change-rollouts.md). Restart the Deployment
after any edit here, and do not treat `flux get kustomizations` as evidence the
change took effect.

### D11 — `docs/automatic-ripping-machine.md` no longer describes the running system

- **Namespace.** Nine commands say `-n apps` (lines 329, 385, 400, 403, 406,
  409, 415, 418, 421). ARM has its own `automatic-ripping-machine` namespace,
  because it needs `SYS_ADMIN`.
- **Paths.** Five references to `/root/media/raw/` and `/root/media/completed/`
  (lines 271, 272, 283, 289, 421). The mount was renamed to `/root/video` in
  `ec63698`; `ls /root/` gives `audio db logs video`.
- **`makemkvcon` unusable** (line 160) — disproven, see D7.
- **Audio passthrough** (line 298, "TrueHD and DTS-HD MA passthrough when
  present on source") — see phase 3; the preset does not do this.

By this repository's own definition a `docs/` file that is not true of the
running cluster is a bug. Fix it as part of whichever phase touches it.

## Phase 0 — unwedge and instrument

Prerequisite for everything else. Nothing here needs a disc.

1. ~~**Clear job 12** so the drive accepts a disc again.~~ Done 2026-08-25 via
   the UI's abandon endpoint, the only path that releases the drive:
   `curl 'http://<pod-ip>:8080/json?mode=abandon&job=12'` — the parameter is
   `job`, not `j_id`, and the UI does not answer on 127.0.0.1, only on the pod
   IP. The stale `Failed` pod from D9 was deleted at the same time.
2. ~~**Bound `MANUAL_WAIT_TIME`**~~ (D1). Now 600.
3. ~~**Harden `arm-disc-wrapper.sh`**~~ (D2). It now requires an
   `ID_CDROM_MEDIA_*` property, polling for up to 60s, and takes a lock that is
   inherited through the `exec` so it is held for the life of the job. Covered
   by `tools/arm-disc-wrapper/test_wrapper.py`. **D2b is descoped** — automatic
   tray movement is ruled out, so what remains is telling the user a disc is
   sitting unseen, which needs item 5 first.
4. ~~**Bring the resource limits under the node's capacity**~~ (D6). Now
   3000m / 4Gi.
5. ~~**Wire notifications**~~ (D5). Done 2026-08-25 — `PO_USER_KEY` and
   `PO_APP_KEY` are substituted in the running container, from
   `PUSHOVER_USER_KEY` / `PUSHOVER_APP_TOKEN` in `api-keys.yaml`. Two things
   worth keeping: `sops -d` cannot run unattended here, because the age
   recipient is a passphrase-protected SSH key and sops has no tty, so editing
   that file is always Will's to do. And the Secret is read by the
   `config-injector` init container through `envFrom`, so **nothing restarts ARM
   when it changes** — getting it into the cluster is not enough, and a
   `kubectl rollout restart` was needed to make it take effect.
6. ~~**Delete `RIPMETHOD_DVD` / `RIPMETHOD_BR`**~~ (D10). `LOGLEVEL` stays at
   `DEBUG` deliberately for the duration of phases 1–3: the per-second
   `ServerUtil` noise is the price of having detail when a rip fails. Revisit
   once phase 3 has produced a complete Blu-ray.
7. ~~**Add an alert**~~ — `RipperRestarted` and `OpticalDriveUnavailable` in
   `kubernetes/infrastructure/prometheus-rules.yaml`. Per `CLAUDE.md`,
   verification that outlives the session belongs in a rule, and D3's cause can
   only be identified by catching the *next* occurrence.
8. ~~**Restart the Deployment** and confirm the ConfigMap actually reached the
   process~~ (D10). Confirmed 2026-08-25 against the running container:
   `MANUAL_WAIT_TIME: 600`, `AUTO_EJECT: true`, and the new wrapper with its
   `MEDIA_TIMEOUT` and `flock`. The check found two things reading the ConfigMap
   alone would not have — the `RIPMETHOD_*` keys came back (see D10) and the
   Pushover placeholders were still literal, because the secret had not been
   pushed yet.

Housekeeping, low priority, do not let it block the phases: 76 empty directories
in `/root/video/raw/`, and three 43 GB copies of The Rescuers' BDMV (129 GB) from
jobs 5, 7 and 8.

## Phase 1 — Audio CD

The best-wired path in the system and the right one to prove first.

`abcde` 2.9.3 + `cdparanoia` + `flac` are all present in the container. The
config (`config-map.yaml:358`, mounted via `ABCDE_CONFIG_FILE`) writes
`OUTPUTDIR="/root/audio/"`, which is `//192.168.0.105/audio-rw/import` — **the
beets-flask inbox**, watched with a 30 s debounce and auto-tagged. So a CD rip
already lands somewhere that catalogues itself. Confirm that end to end rather
than assuming it.

Open questions for this phase:

- **Integrity. There is no read-quality signal at all** — corrected 2026-08-26,
  having previously been written here as "cdparanoia reports it and nothing reads
  it". It does not report it. `CDPARANOIAOPTS` is empty in abcde and we set
  nothing, and cdparanoia suppresses its progress display when stderr is not a
  terminal — `-e/--stderr-progress` exists precisely to "force output of progress
  information to stderr (for wrapper scripts)", which is what abcde is. What the
  job log actually contains per track is:

  ```
  Ripping from sector 0 (track 1 [7:34.69])
  outputting to /home/arm/abcde.xxxxxxx/track01.wav
  Done.
  ```

  No status symbols, no error summary, nothing. So a scratched disc yields
  patched or silence-filled audio and the job still reports success, because
  `rip_music()` checks only abcde's exit code (D4).

  **Fixed 2026-08-26**: `CDPARANOIAOPTS="-e"`. The job log now carries
  cdparanoia's per-track status. `;-(` (gave up correcting) and `:-(` (scratch
  detected) are the two that mean the audio may be wrong; `:-|` and `:-/` are
  jitter and drift that were corrected.

  Deliberately not paired with `-z`: plain `--never-skip` retries a bad sector
  indefinitely and would hold the drive until someone noticed. `-z=N` bounds it
  and `-X` aborts outright — pick one once real discs have reported something,
  rather than guessing a retry count now.

  **`-e` is the machine-readable callback stream, not the smiley progress bar** —
  that is `-E`. Each line is `##: <code> [<action>] @ <sector>`, which is the
  better of the two here because it can be counted rather than eyeballed. The
  config comment said smilies until 2026-08-29; it was written from
  `cdparanoia --help`'s legend without checking which format the flag selects.

  **Baseline for a clean disc**, from job 14, an 11-track CD:

  | callback | count |
  |---|---|
  | `-2 [wrote]` | 188899 |
  | `0 [read]` | 14512 |
  | `1 [verify]` | 1068 |
  | `9 [overlap]` | 225 |
  | `-1 [finished]` | 11 |

  `overlap` is paranoia adjusting its read overlap, not an error. Anything else
  — scratch, skip, readerr, fixup dropped/duped, repair, backoff — means the
  audio may be wrong. That is the threshold: **any callback outside those five
  is worth failing on.** It costs 5.4 MB of job log per disc.

  **Nothing reads it yet.** D4 still stands: `rip_music()` checks abcde's exit
  code alone. Making the job fail on a bad read is now a well-defined change
  rather than a guess, and it needs the `BASH_SCRIPT` hook or a fork.

  Only then is the `whipper` question worth answering. AccurateRip verifies the
  read against other people's rips of the same pressing, which is a stronger
  claim than cdparanoia's "I did not notice a problem" — but it is a move off
  ARM entirely, and the cheap signal should be exhausted first.
- **AccurateRip.** Not available — `whipper` and `cdrdao` are absent from the
  container. If Will wants EAC-grade verification this is the "radical
  improvement" candidate, and it means running the CD path outside ARM. Raise it
  as a choice; do not assume it.
- `EJECTCD=y` in abcde, so the tray opens on a music rip regardless of
  `AUTO_EJECT` — the one media type that already gives a physical done signal.
- The inbox is shared with audiobooks and the podcast archive. See
  [audiobook-importing.md](audiobook-importing.md) for how `genres` routes there.

**Test disc:** the Mànran *The Test* CD from job 12 — already MusicBrainz-matched,
so it exercises the happy path.

### The manual wait is dead time on an audio CD, and cannot be shortened

Found 2026-08-26 during the first run of phase 1. Three upstream defects compose,
all three now on the Upstream list:

- The disc *is* identified early — `logger.py:35` calls `Job.identify_audio_cd()`
  to name the log file, which resolves the MusicBrainz title before anything else
  runs. Job 13's title was `Mànran The Test` from the first second.
- But `job.label` stays empty, because the only thing that sets it for music sits
  behind `if mounted:` and an audio CD has no filesystem — and `notify_entry`
  reads `label`, not `title`. Hence "Found music CD: None" on a disc ARM had
  already named.
- And the UI renders no identification controls at all for a music job, so
  `title_manual` can never be set, so `check_for_wait()` can never break early.

Net effect: **every audio CD waits the full `MANUAL_WAIT_TIME` doing nothing a
person could act on, and the notification cannot tell them which disc it is.** At
600s that roughly doubles the wall-clock time per disc, against a rip of ~10–15
minutes. For someone feeding a stack it is the dominant cost.

Config cannot fix this — `MANUAL_WAIT` is global and the DVD case genuinely wants
it. Fixing it locally would mean the forked image.

**Will's decision, 2026-08-26: do not fork for this.** "That time cost is not a
worry to us in the day-to-day." It stays on the Upstream list, where the fix is
small and defensible — use `job.title` in the music branch of `notify_entry`,
include the job link as the video branch does, and skip the wait for disc types
the UI offers no override for. Do not re-propose a fork on the strength of the
wait alone; something else would have to justify it.

### The handoff to beets is racy, and this rip is stuck in the inbox

Found 2026-08-26 on job 13, the first complete audio rip. **The rip itself was
perfect; the import never happened.** The album is still in `/audio/import/` and
absent from the beets library.

Two independent defects compose. The evidence, in order:

| time | what |
|---|---|
| 19:24:46 | abcde writes `01 - MSR.flac` into `/root/audio/Mànran The Test/` — the beets-flask inbox |
| 19:25:16 | beets-flask's 30s debounce fires. `folder` row created for a directory holding **one track**, session `60ee9477` created |
| 19:27–19:37 | nine more tracks land. Every watchdog event logs `skipping enqueue` |
| ~19:37 | abcde's `embedalbumart` moves `cover.jpg` into `albumart_backup/`. beets-flask, scanning, hits `FileNotFoundError: /audio/import/Mànran The Test/cover.jpg` |
| 19:38:01 | ARM reports success. Session `60ee9477` is still `NOT_STARTED` with **no task ever created** |
| 19:38:25 | `albumart_backup/` is a *new* path with no session, so it **is** enqueued as `import_auto` — a folder containing one JPEG and no audio |

**(a) ARM writes incrementally into a watched directory.** abcde encodes each
track straight to its final path, and gaps between tracks run 60–170s — far
longer than `debounce_before_autotag: 30`. So beets-flask always sees a partial
album, and `cover.jpg` moving out from under it mid-scan is the same race in a
second form.

**(b) beets-flask never retries an `IMPORT_AUTO` that did not start.**
`watchdog/inbox.py:186-201`: `should_enqueue` is true only when no session
exists for the path, and only `PREVIEW` re-enqueues on a hash change. So one
early, failed session pins the folder permanently. Three other albums imported
cleanly at 17:51 the same day, so this is the race and not a broken installation.

**Half of (a) is already fixed.** `embedalbumart` is out of abcde's `ACTIONS`,
so `cover.jpg` stays beside the audio instead of being embedded and then moved
into `albumart_backup/` mid-scan. That removes the `FileNotFoundError` and the
JPEG-enqueued-as-an-album, and gives the conventional layout that beets'
`fetchart` reads as a filesystem source.

**The partial-album race is closed** — `arm-audio-handoff.sh` in
`init-scripts.yaml`, wired as `BASH_SCRIPT`, with tests in
`tools/arm-audio-handoff/`.

abcde now writes to `/home/arm/arm-incoming/`, **container-local**. Will ruled
out the alternative — mounting the whole audio share so a staging sibling of
`import/` could be renamed into place — on data-integrity grounds: it would widen
ARM's write access from `import/` to `music/`, `audiobooks/` and everything else
on the share, and upstream ARM has a history of surprising permission and
directory behaviour. That is why our scripts are hot-plugged in by mount rather
than trusted to the image.

Container-local staging means the handoff is a copy, not a rename, so the
destination files grow — which would recreate the very race. The script gets
around it by copying each track to `.<name>.part` and renaming only once all of
them have landed, using two independent beets-flask properties at once: its
watchdog drops events whose basename starts with `.`, and a file counts as audio
only when its *name* ends in an audio extension.

**A dotted staging directory does not work, and it was tested rather than
assumed.** Two probes under `/audio/import/`, one dotted and one not, were
enqueued identically as `import_auto`. `disk.py:135`'s `ignore_globs` skip
governs the folder walk used for listing, not the watchdog's per-event enqueue
path. An earlier single probe returned "no session created" for an unrelated
reason — a websocket `ConnectionError` in the handler — and without the matched
control would have read as proof.

`inbox.py:108` is **not** the mechanism, despite reading like it: the check is
`os.path.basename(fullpath).startswith(".")` on the *event* path, so
`.arm-incoming/Album/01.flac` has basename `01.flac` and passes straight through.
It does apply to a dotted *filename*, which is why the script uses one.

abcde has no usable post-run hook — `do_postprocess` is commented out, and the
body runs inside a subshell ending in `exit 0`. So the trigger is ARM's
`BASH_SCRIPT`, which fires on *every* notification and therefore matches the
completion message `Music CD: <title> processing complete.` Because the drive is
exclusive, only one album can be in staging, so the script moves whatever it
finds rather than parsing the title out.

**Colliding album names are disambiguated, not refused.** Every disc MusicBrainz
cannot identify is called `Unknown Artist Unknown Album`, so two unidentified
discs in a row collide — which is not a corner case once a stack is being fed in.

The first version refused the handoff and kept the album in staging. That looks
safe and is not, for two reasons found on 2026-08-29 by reproducing it:

- Staging is container-local, so a retained album dies at the next pod restart.
- abcde rips the *next* disc into that same directory. An 8-track disc landing on
  a retained 11-track one leaves tracks 1–8 from the new disc and 9–11 from the
  old — an album that looks complete and is two discs. Silent corruption, worse
  than either losing it or failing loudly.

So the destination gets a ` (2)`, ` (3)` suffix and the album always lands, and
staging is cleared on every success. The one path that still keeps a rip is a
failed copy, and it moves the directory aside to `<album>.failed-<epoch>` for the
same reason — that was the last route by which the next disc could merge into an
existing album.

> **Loose end, deliberately left 2026-08-31.** The disambiguation is covered by
> sixteen unit tests and has **never run against a real rip** — the fixture needs
> a second disc MusicBrainz cannot identify, and one was not to hand. Re-ripping
> the *same* unidentified disc exercises the identical path if a second is hard
> to find. Until then, the expected behaviour on a colliding rip is
> `Unknown Artist Unknown Album (2)` beside the existing folder, and a
> `handed off … to …(2)` line in `arm.log`. If instead the log says `refusing to
> overwrite`, the container is running a stale script — see below.

**Proven on a real disc, 2026-08-29.** Job 14: eleven tracks handed off in one
step, and beets-flask created its session **70 seconds after** the handoff,
reaching `PREVIEW_COMPLETED` rather than the `NOT_STARTED` dead-end job 13 hit.
It saw a complete album. No `.part` files left behind.

**(b) belongs to `~/repositories/beets-flask/todos/`**, not here.

**Resolved for this album, 2026-08-26.** Neither supported route worked: session
delete is beets-flask's own `session-delete-circular-dependency` bug, and opening
the candidate view crashes the UI with `can't access property "asis_candidate", a
is undefined` — `candidateSelector.tsx:93` dereferences `task.asis_candidate`
and a never-started session has zero tasks, so `task` is `undefined`. A folder
whose import is stuck is therefore unreachable from the UI as well.

What worked: move `albumart_backup/cover.jpg` up to `cover.jpg`, remove the empty
subdirectory, and **rename the folder**. The session lookup is
`get_by_hash_and_path(hash=None, path=…)`, so a new path gets a new session. It
enqueued on its own and imported 40 seconds later.

All of this is written up for upstream in
`~/repositories/beets-flask/todos/inbox-auto-import-never-retries.md`.

## Phase 2 — DVD

`RIPMETHOD: "backup_dvd"` makes MakeMKV write the full decrypted `VIDEO_TS`, and
`DELRAWFILES: false` keeps it. **That raw backup is the archival copy and the
integrity anchor** — as long as it is intact, a bad transcode is recoverable.
Protect that property in any change.

*Corrected 2026-10-20: it never did. ARM backs up the whole disc only for a
Blu-ray; a DVD's raw copy is its titles of seven minutes or more as MKVs. See the
2026-10-20 entry.*

Known-good reference: job 3 (Le Mans, 2026-04-18) ripped correctly and only the
transcode was broken, so this phase starts from a working rip.

Watch for: `MINLENGTH: 420` / `MAINFEATURE: false` means every title over seven
minutes is transcoded, which on a TV-episode disc is what you want and on a movie
disc yields the film plus decoys.

## Phase 3 — Blu-ray

Do this on The Rescuers, which has 43 GB of verified-good BDMV already on the
share — the rip does not need repeating, only the transcode.

Concrete shape of the work, from job 8's track table: 71 playlists, of which
`MINLENGTH: 420` selects **5**. Titles 70 (4633 s) and 71 (4628 s) are the same
film via two playlists; 44, 46 and 43 are extras. Measured throughput was roughly
**1.3× realtime**, so a Blu-ray is a **4–5 hour** transcode, not the multi-day
job it first looks like. That is well within a pod's uptime — which is why D3
(silent death) matters more than encoder speed. It is also why D8 matters: the
disc is locked in the drive for all of it.

**The code path, read from the source and cross-checked against the DVD runs.**
Worth writing down because it is not what the config file suggests. `RIPMETHOD`
reads `backup_dvd`, and `rip_with_mkv` (`arm_ripper.py:235`) turns that into
"always use MakeMKV" for *every* disc type — the DVD comment about `mkv` mode
never applies. What differs is the MakeMKV *mode*, chosen at `makemkv.py:753`:

- **Blu-ray** → `makemkv_backup`, a full BDMV backup (~43 GB). HandBrake then
  title-scans that structure natively.
- **DVD** → `makemkv_mkv`, extracting each title to its own MKV. HandBrake then
  scans the *directory*, treating each file as a title.

Both then land in `handbrake_all`, because the `handbrake_mkv` branch requires
`RIPMETHOD == "mkv"` exactly and `backup_dvd` does not match. So the Blu-ray is
not entering untested code — it reaches the same transcode function the two TV
discs and the phase 2 movie already proved, by a different route.

**April got much further than "MakeMKV failed" suggests.** The leftover transcode
directories show titles 43, 44, 46 and 70 encoded on 2026-04-22 before the job
died, and 43 and 44 on 2026-04-21. That matches this section's predicted
selection of five titles from 71 almost exactly, and means the failure was late
in transcode rather than at the rip. It also left a partial `title_44.mkv`
(150 MB on the 21st, 539 MB when redone on the 22nd) — an instance of D4, a
half-written file with nothing marking it incomplete.

Three stale `The Rescuers` raw backups (129 GB) and four stale transcode
directories are still on the share from those attempts. The share has 11 TB free
so they are not urgent, but they are why this job's folders carry a stage suffix,
and any cleanup should happen after this phase closes, not during it.

Two things to settle here:

- **The duplicate main feature — answered by running it, and the answer is not
  the obvious one.** ARM keeps *both* 77-minute titles: one becomes the feature,
  the other is filed under `extras/`. What it picks is the surprise:

  | | duration | size |
  |---|---|---|
  | `The Rescuers (1977).mkv` | 77.2m | 1435 MB |
  | `extras/title_71.mkv` | 77.1m | **1719 MB** |

  The copy promoted to feature is the **smaller** one. `skip_transcode_movie`
  does select the largest file, but it only runs when `track.source ==
  "MakeMKV"`, and a Blu-ray reaches `handbrake_all` with tracks registered by
  HandBrake's own scan — so `move_files_post` takes the other branch and trusts
  HandBrake's `main_feature` flag instead. At a fixed CRF a 20% larger file means
  more retained detail, so the better encode is the one sitting in `extras/`.
  Downstream cannot tell them apart by name, and 3.1 GB is being spent on one
  film. This is the ingest's problem to resolve, not the ripper's, and it is
  recorded in [video-library-ingest.md](video-library-ingest.md).
- **Audio.** `docs/` claims TrueHD/DTS-HD MA passthrough. The preset does not do
  it: `AudioCopyMask` lists `copy:truehd` and `copy:dtshd`, but **both
  `AudioList` entries specify `AudioEncoder: opus`**, and HandBrake only passes
  through when a track entry itself requests `copy`. So lossless audio is
  re-encoded to Opus in the transcode. Nothing is lost — the raw BDMV keeps it —
  but the docs are wrong and the intent recorded in the notes was passthrough.
  Decide which behaviour is wanted, then make the preset and the docs agree.

**ATA link, for context and not as a live worry.** The Pioneer's known failure
mode did fire during this era: on 2026-04-21 21:33–21:36 MDT (= 2026-04-22
03:33–03:36 UTC, the first four minutes of job 8) the host logged seven `ata4.00:
exception` / `hard resetting link` cycles at 30-second intervals — ATAPI commands
timing out — ending with `limiting speed to UDMA/66`. Every one recovered. The
matched control: **since the host power-cycled on 2026-05-04 there have been zero
ata4 events in 111 days of uptime**, and the 2026 total is five days with
exceptions, all in April. Treat the drive as healthy but instrument the next
Blu-ray rip with `journalctl -f | grep ata4` on the host.

> **Superseded — 2026-10-05.** The quiet spell was one host boot, on kernel
> 7.0.0-3, and ended with job 27. April also had device disables needing a
> reboot and a PHY-level link-down, not only recoveries. See
> [the 2026-10-05 entry](#2026-10-05--the-drives-history-since-april-and-what-each-era-ran).

## Where the media lands, and the asymmetry that matters — 2026-08-24

| ARM output | share path | what picks it up |
|---|---|---|
| `/root/audio/` | `//192.168.0.105/audio-rw/import` | **beets-flask**, watchdog, 30 s debounce, auto-tag |
| `/root/video/completed/` | `//192.168.0.105/media/video/import/automatic-ripping-machine/` | **nothing** |

Plex mounts only `movies`, `shows`, `music` and `audiobooks` as read-only
subPaths (`kubernetes/apps/plex/deployment.yaml`), so it cannot see `import/` at
all. **Filebot** is the only workload that mounts the whole video share and can
see both sides, but it is a manual web UI — no watch folder, no AMC script, no
cron. So today the video half of the pipeline ends in a folder a person has to
visit.

Will's framing was that downstream processing is "mostly outside the scope of
this project, unless there's opportunity for radical improvement". This
asymmetry is that opportunity, and it is the single change that would make video
rips as hands-off as CD rips are. **Do not start it before phases 1–3 produce a
file worth filing** — automating the handoff of output that does not yet exist is
the wrong order.

ARM already has a `homepage` entry with a `siteMonitor` but no widget
(`kubernetes/apps/homepage/config-map.yaml`, `Ripping` group). If a job-status
widget is available it would serve the self-service priority directly.

## Progress log

Append one entry per session. Newest last.

### 2026-08-24/25 — survey, and this spec

Read the whole pipeline end to end and rewrote the prior spec around what the
evidence actually shows. Corrected three of its premises (see "What this
supersedes"). Found D1 (the wedge) as the mechanism behind most of the failure
history, and D7 (`makemkvcon` is usable) which unblocks phases 2–4. Ran
`update_key.sh` in the pod to register the MakeMKV key — the only mutation made;
it is what ARM itself does at rip time and it persists on the pod's ephemeral
`/root/.MakeMKV`, so it will be gone after the next restart.

No phase started. The drive is wedged on job 12 and the tray is open.

### 2026-08-25 — phase 0, most of it

Unwedged the drive and closed D1, D2, D6 and D10; added the two alerts. Details
sit against each numbered item in phase 0 above. Two things did not land:

- **D5 notifications are blocked on the SOPS passphrase**, so ARM still tells
  nobody anything. The Alertmanager path is live, which covers "the ripper died"
  but not "your disc finished".
- **D2b needs a decision** — the fix turns out to require cycling the tray,
  because `/sys` is read-only in the container and the udev database cannot be
  populated any other way.

The `arm-disc-wrapper.sh` change has tests (`tools/arm-disc-wrapper/`, 12 cases,
`python3 -m unittest discover tools/arm-disc-wrapper`). They extract the script
from the ConfigMap rather than keeping a second copy, and they were written
against the *old* script first: two failed for the right reason before the fix.
The lock test was itself checked by neutering the `flock` and watching two
invocations get through, because a concurrency test that passes either way is
worth nothing. Both alert expressions were run against live Prometheus, came back
empty in the healthy state, and were each re-run in a deliberately-false variant
to confirm they can fire at all.

**Nothing is applied to the cluster yet.** Flux is what puts these in place, so
until the Deployment restarts the running pod still has
`MANUAL_WAIT_TIME: 31536000` — the drive will wedge again on the very next disc.

### 2026-08-25 — decisions on D2b and D5, and an upstream list

Will ruled out automatic tray movement: the drive is in an enclosure whose door
is closed whenever nobody is deliberately ripping, so an eject on pod start would
hit it. D2b therefore drops from "replay the insert" to "say that a disc is
sitting unseen", which is worth nothing until ARM can say anything at all — so it
now depends on D5 rather than being independent of it.

D5's route turned out to be simpler than assumed: no `APPRISE` file, because ARM
speaks Pushover natively at `utils.py:70`. `config-map.yaml` now carries the two
placeholders and the secret half is the only thing outstanding.

Added the **Upstream** section. Will is willing to run a forked image where a fix
cannot be made in config, so the list is worth keeping properly rather than
working around each item silently. Ten items so far; the first three are one
report, since they compose into the wedge.

### 2026-08-25 — phase 0 reconciled, and one assumption disproved

Pushed and reconciled. The running container has `MANUAL_WAIT_TIME: 600`,
`AUTO_EJECT: true`, limits of 3000m/4Gi, and the new wrapper. Both alert rules
loaded healthy, and `RipperRestarted` went to `firing` on the restart that
delivered them — an unplanned but better proof that it fires than the falsified
variant it was tested against.

Two things the check caught that reading the ConfigMap would not have:

- **ARM rewrites `/etc/arm/config/arm.yaml`**, so deleting `RIPMETHOD_DVD` and
  `RIPMETHOD_BR` did not remove them. D10 is corrected above and it is now an
  Upstream item. This is the "applied is not in effect" rule with a new variant:
  not a stale process, but a process that edits the file it was given.
- **The Pushover placeholders were still literal**, because the secret was in
  the working tree and not yet pushed.

Phase 0 is done bar D5's secret. **Phase 1 can start**: put the Mànran CD back
in and watch a music rip end to end.

### 2026-08-25 — phase 0 complete

The Pushover secret landed and ARM was restarted to pick it up; `PO_USER_KEY`
and `PO_APP_KEY` are substituted in the running container.

One thing learned in the doing, recorded above: a Secret consumed through
`envFrom` on an init container has nothing watching it, so it needs a manual
rollout.

**Phase 1 is next and needs a person at the machine**: the Mànran CD back in the
drive, enclosure door open. Four things to watch, in order —

- whether the media gate lets it through first time, i.e. one job rather than a
  failed `Could not determine disc type` beside a good one;
- whether Pushover actually delivers, which has never been tested end to end;
- whether abcde's FLAC lands in `/root/audio/<Artist> <Album>/` and beets-flask
  picks it up inside its 30s debounce;
- what cdparanoia reports per track. Nothing reads that today, and it is the
  input to the `whipper` decision Will deferred until there was real data.

### 2026-08-26 — phase 1: the rip works, the handoff did not

Job 13 is **the first ARM job ever to produce a verified file.** Ten FLACs,
363 MB, all ten pass `flac -t`, tags clean (artist/album/title/date/track/total
plus ReplayGain), and the tray opened by itself. Every phase 0 fix held: one job
rather than two, the wait released at 600s, and both Pushover notifications
arrived — the first end-to-end confirmation that ARM can tell anyone anything.

The import did not happen, for reasons entirely downstream of the rip. Written
up above and, for the parts that are upstream's, in the beets-flask fork's
todos. The album is in the library now.

Three findings worth carrying into phases 2–4:

- **"Success" still needs checking against output**, and now there is a way:
  `flac -t` for audio. D4 remains the rule.
- **The audio path has no read-quality signal**, which is not what this spec said
  before today. `CDPARANOIAOPTS="-e"` is the cheap fix and it comes before any
  `whipper` decision.
- **whipper has no post-run hook and will not get one** —
  [whipper-team/whipper#394](https://github.com/whipper-team/whipper/issues/394)
  asked for exactly this, citing "adding the release to beets" as the use case,
  and was closed with a "Rejected" label. That is not an argument against
  whipper: adopting it means writing a wrapper to invoke it anyway, and a
  wrapper is a superset of a hook. The hook question only matters when bolting
  it into someone else's pipeline.

### 2026-08-29 — phase 1 closes

Job 14, an unrelated CD, ran unattended while the site was unreachable and did
every part of it right.

- **One job**, straight to `CD` — the media gate again.
- **`-e` produced real read data**, and the disc read clean: only `read`,
  `verify`, `wrote`, `overlap` and `finished`. That is the baseline recorded
  above, and it is what a threshold can now be set against.
- **The handoff worked.** Eleven tracks moved in one step, and beets-flask's
  session was created 70s later at `PREVIEW_COMPLETED` — it saw a complete
  album, where job 13's saw one track and died.
- **The tray opened** on its own.

It stopped at preview rather than importing, which is **correct**: MusicBrainz
returned HTTP 404 for disc ID `me52FDJAZbLLImDQaZV1kydxfSI-`, so the album is
`Unknown Artist / Track 1…11` and beets is waiting for a person. An
unidentifiable disc should wait, not guess. Nothing to fix — but note the rip
still succeeds and still lands, so the failure mode is "needs a human", not
"lost".

`getalbumart` fetched nothing, for the same reason: no identification, nothing
to look art up by.

**Phase 2 is next: DVD.** There is a known-good raw rip on the share already —
job 3's Le Mans — so the transcode can be exercised without re-reading a disc.
Watch for D4: a DVD that reports success having produced nothing is exactly what
happened before, and `find /root/video/completed -type f` is the check.

### 2026-08-31 — phase 1 closed, with one thing unproven

Deployed and verified in the running container: the collision fix, the abort
path moving staging aside, `CDPARANOIAOPTS="-e"`, `ACTIONS` without
`embedalbumart`, `OUTPUTDIR` on container-local staging, and `BASH_SCRIPT`.

**The restart was not optional and the check is what caught it.** After Flux
reconciled, the cluster ConfigMap held the new script while the running container
still had the old one, because `init-scripts` is mounted with `subPath`. Recorded
in "Where things stand" — it is a harder version of
[config-change-rollouts.md](config-change-rollouts.md), since a subPath mount
never corrects itself.

**The loose end** is the collision fix never having run against a real rip. Noted
above with what to look for. Everything else in phase 1 is proven on real discs.

Carried into phase 2, from what phase 1 cost:

- **"Success" means nothing without checking the output.** For audio that was
  `flac -t`; for video it is `find /root/video/completed -type f`. Jobs 3 and 5
  reported success having produced no file at all, and that went unnoticed for
  four months.
- **Watch the notification ordering.** `utils.notify` runs `bash_notify` before
  sending to Pushover, so "processing complete" goes out whether or not the
  post-processing worked. Fine for the video path, which has no handoff — but it
  means the message is a statement about ARM's pipeline, not about files landing.
- **A DVD holds the drive for the whole transcode**, not just the rip (D8), so
  the throughput question phase 1 never had to face arrives here.

### 2026-09-01 — phase 2 begins

A DVD is in the drive. The tray was open — `eject -t /dev/sr0` on the Proxmox
host closes it, which is the same manual step D2b leaves in place deliberately,
since automatic tray movement is ruled out by the enclosure door.

Job 15 identified cleanly, which is a better start than the CDs had:

| | |
|---|---|
| title | `The-Hallelujah-Trail` |
| year | 1965 |
| IMDb | `tt0059250` |
| video_type | movie |
| CRC64 | `c664091933fa7782` |
| label | `HALLELUJAH_TRAIL` |

One job, disc type `dvd`, `hasnicetitle`, OMDb hit on the first try. The
1337server CRC64 lookup ran first and OMDb supplied the metadata.

What to check when it finishes, in order of what has actually gone wrong before:

1. **`find /root/video/completed -type f`.** Non-empty is the only proof. Jobs 3
   and 5 reported success with nothing there.
2. **How many files, and are two of them the same film.** `MINLENGTH: 420` and
   `MAINFEATURE: false` mean every title over seven minutes is transcoded; on the
   Blu-ray that produced two near-identical main features (D8's phase 3 note).
3. **Whether the raw backup survived** — `DELRAWFILES: false`, so
   `/root/video/raw/HALLELUJAH_TRAIL*` should hold the full `VIDEO_TS`. That is
   the archival copy and the thing that makes a bad transcode recoverable.
4. **How long the drive was held.** The disc is locked in for rip *and*
   transcode (D8), so the tray will not open until the whole job ends.

### 2026-09-01 — the video path has been broken by a stalled image bump

Jobs 15 and 16, the same DVD twice, both `Error while running MakeMKV`, exit code
253. MakeMKV's own message:

```
MSG:5021  This application version is too old.  Please download the latest
          version at http://www.makemkv.com/ or enter a registration key to
          continue using the current version.
```

**Root cause: ARM has not been updated since 2026-04-16, and nothing said so.**

`32a1918` moved ARM into its own namespace that day and changed the marker to
`{"$imagepolicy": "automatic-ripping-machine:automatic-ripping-machine"}`. Flux's
`Setters` strategy only resolves ImagePolicies in **the same namespace as the
ImageUpdateAutomation**, and the only two automations live in `apps` and
`infrastructure`. So the `apps` automation scans this file, cannot resolve the
marker, and reports `repository up-to-date`. Nothing errors, and the ImagePolicy
reports the newest tag correctly the entire time:

| | |
|---|---|
| ImagePolicy `latestRef.tag` | 2.24.3 |
| deployment pins | 2.23.2 |
| last automated bump | `0bf421b`, 2026-04-02 |
| ARM releases missed | 2.24.0, 2.24.1, 2.24.2, 2.24.3 |

ARM is the **only** app in `kubernetes/apps/` whose marker is not `apps:`, which
is why it is the only one affected. This is a concrete instance of
[version-notification-prompt.md](version-notification-prompt.md) — and the cost
of that class of bug, which that spec argues about in the abstract, is five
months of every DVD and Blu-ray failing.

Fixed by giving ARM its own `ImageUpdateAutomation`, scoped to this app's
directory so it cannot race the `apps` one, plus a manual bump to 2.24.3 so it is
testable now rather than at the next reconcile.

**Confirmed.** Job 18 on 2.24.3 (MakeMKV 1.18.4) ripped and transcoded The
Hallelujah Trail end to end — the first video file ARM has ever produced. A
single patch release of MakeMKV was the whole difference; 1.18.3 refused every
disc, 1.18.4 refuses none.

### What was ruled out first, so it is not re-tested

Each of these was checked and is **not** the cause:

- **The key is not installed too late.** `prep_mkv` runs `update_key.sh` before
  the first `makemkvcon` call, in that order, confirmed in the job log.
- **`update_key.sh`'s bash bug is harmless.** Line 52 does throw
  `((: > 0 : syntax error` on a missing settings file, but it sits inside an
  `if` condition where `set -e` does not apply, so the script continues and
  writes the key. Verified by running it.
- **The key is accepted for drive listing.** With it installed,
  `makemkvcon -r --cache=1 info disc:9999` identifies the drive with no
  complaint. The version check only fires when a disc is actually opened, which
  is why an empty tray cannot reproduce this.
- **It is not the first MakeMKV run in a fresh container.** Deleting
  `settings.conf`, and separately deleting all of `settings.conf`,
  `update.conf` and the 3 MB `_private_data.tar`, then replaying ARM's exact
  sequence: both succeed. This theory fitted the April pattern well and was
  wrong.
- **Warm MakeMKV state changes nothing.** Job 16 ran with all of it present and
  failed identically.

One live thread: **`MAKEMKV_PERMA_KEY` being populated disables ARM's beta-key
updater**, by ARM's own documentation of the setting. The updater works — it
fetches the current month's `T-` key from the forum, verified 2026-09-01. Will
holds a real licence, so the intended fix is the newer version rather than
falling back to beta keys; but if a current MakeMKV still rejects the stored
key, the stored *value* is the next thing to check, since a purchased key is
perpetual and should not be refused.

### 2026-09-01 — phase 2 done: the first video file ARM has ever produced

Job 18, The Hallelujah Trail (1965), on ARM 2.24.3 / MakeMKV 1.18.4.

| stage | | |
|---|---|---|
| identified | 05:57 | OMDb, `tt0059250`, `hasnicetitle` |
| manual wait | 05:57 → 06:07 | 600s, released on its own |
| MakeMKV `backup_dvd` | 06:07 → 07:54 | 1h47m, 7.1 GB of `VIDEO_TS` |
| HandBrake | 07:54 → 09:13 | 1h19m |
| success | 09:14 | tray ejected on its own |

**Three hours seventeen minutes from insert to done, and the drive was held for
all of it** (D8). Verified rather than assumed, because status alone has lied
before:

```
/root/video/completed/movies/The-Hallelujah-Trail (1965)_178824223068/The-Hallelujah-Trail (1965).mkv
  1,293,281,925 bytes — av1 video, 2× opus stereo, dvd_subtitle, 9332s
```

`ffprobe` confirms it decodes. The 7.1 GB raw backup survives, so a bad
transcode is recoverable without re-reading the disc.

**The multi-title worry did not materialise.** Two tracks passed `MINLENGTH`,
13 seconds apart — the same near-duplicate shape the Blu-ray showed. ARM's
`skip_transcode_movie` picked the largest and moved only that, renaming it to
`<Title> (<Year>).mkv`; the other was discarded with the transcode directory.
For a movie that is the behaviour you want, and it is better than the Blu-ray
note in D8 predicted. Whether it still holds for a TV disc, where every title
matters, is untested.

**Housekeeping left behind.** Eight empty directories under
`completed/movies` and `transcode/movies`, from the failed jobs 15–17 and the
April Rescuers/Le Mans runs. They also cause the `_<stage>` suffix on the
output path, because `check_for_dupe_folder` finds the name taken. Harmless,
untidy, and it means the tidy path `The-Hallelujah-Trail (1965)` is occupied by
an empty directory while the real film sits in the suffixed one.

**Phase 3 is Blu-ray**, and the case for it is now much stronger than it was:
the April Blu-ray failures were on 2.23.2, and every one of them may have been
this same MakeMKV expiry. The Rescuers' 43 GB raw backup is still on the share,
so the transcode half can be exercised without re-reading the disc — but the
rip half needs the disc, and is now worth retrying.

## Phase 2b — a TV disc

**Why it is 2b and not phase 5.** The numbered phases are physical formats —
CD, DVD, Blu-ray, UHD. Movie versus series is a *content* axis that crosses DVD,
Blu-ray and UHD alike, so it does not want its own place in that sequence.
Running it on DVD, the format just proven, means any failure isolates to the
multi-title path rather than to the format.

**What ARM does differently**, read from the source rather than assumed:

- `convert_job_type("series")` returns `"tv"`, so the output lands in
  `completed/tv/<Title>/` — **not** `completed/movies/`.
- `move_files_post` (`arm_ripper.py:198`) takes the series branch and calls
  `move_files(..., is_main_feature=False)` for **every** track. There is no
  largest-file selection, which is the behaviour a TV disc needs.
- `move_files` (`utils.py:210`) sets `extras_path = movie_path` for a series —
  "for series there are no extras" — so every episode lands flat in one
  directory.
- Because nothing is the main feature, nothing gets renamed to
  `<Title> (<Year>).mkv`. **Every episode keeps its `title_N.mkv` name.**

That last point is the one worth running the disc to confirm, because it decides
[video-library-ingest.md](video-library-ingest.md): ARM knows the *show* but
cannot know which title is which episode. A mover built on ARM's own metadata,
which is enough for a film, cannot file TV. Only content matching — FileBot, or
a person — can.

**What to check:**

1. Does `VIDEOTYPE: "auto"` actually resolve `series`? If it comes back `movie`,
   everything above is moot and the disc files as one film plus extras.
2. Do all episodes transcode? `MINLENGTH: 420` passes a 22-minute episode
   comfortably; the risk is at the short end, not the long.
3. Does `title_N` ordering match episode order? If it reliably does, a mover
   could map positionally — worth knowing even though it is fragile.
4. `completed/tv/` versus the library's `/video/shows/`. The names differ, which
   is another thing any ingest has to reconcile.
5. Time. The movie ran ~2× realtime on transcode; a 4-episode disc is roughly
   90 minutes of video, so budget a couple of hours all-in and remember the
   drive is held throughout (D8).

### 2026-09-02 — two more discs, two unrelated failures

Neither is a regression, and neither is the MakeMKV expiry fixed on 2026-09-01.

**Job 20, The Sylvester and Tweety Mysteries — phase 2b, blocked by the
fileserver.** ARM identified it correctly as `video_type: series`, which is the
first confirmation that `VIDEOTYPE: "auto"` resolves a TV disc. It then failed
creating its output directory:

```
OSError: [Errno 5] Input/output error:
  '/root/video/transcode/tv/The-Sylvester-and-Tweety-Mysteries (1995–2002)'
```

Not ARM's fault. Samba runs `unix charset = ISO-8859-1`, so any character above
U+00FF fails to write on every share — OMDb returns a series' year range with an
**en dash**. Measured boundary and migration plan in
[smb-charset-utf8.md](smb-charset-utf8.md). It also explains why `Mànran` worked
earlier: `à` is inside Latin-1, by luck.

The `tv/` destination predicted from the source is confirmed, so the rest of the
phase 2b plan still stands — it just cannot run until the name can be written.
**Worked around**, so phase 2b can proceed while the charset migration waits:
`arm-title-charset.sh` in `init-scripts.yaml`, tested in
`tools/arm-title-charset/`.

`clean_for_filename` is not the tool for it, despite being the obvious candidate.
It is applied to *titles* only (`identify.py:141`, `:274`) while the en dash
arrives through `job.year`, which `fix_job_title` interpolates raw — and it
*strips* rather than replaces, turning `1995–2002` into `19952002`, a different
year.

So the patch wraps `fix_job_title` itself, the one funnel every output path goes
through, mapping the punctuation metadata providers actually emit and dropping
what has no Latin-1 form. `1995–2002` becomes `1995-2002`; `Ocean's Eleven`
keeps its apostrophe as ASCII; `Mànran` is untouched, because the share accepts
Latin-1 and that name already exists in the library.

It is appended rather than edited into the function body, since every call site
resolves the name at call time, so rebinding the module global reaches all of
them without depending on internals. **It refuses loudly and exits non-zero if
`def fix_job_title` is not found** — ARM auto-updates now, and a patch that
silently stops applying would present as a TV rip dying on EIO again with
nothing pointing back to it.

**Job 19, An American Tail: Fievel Goes West — a bad disc, probably.**

```
MakeMKV v1.18.4 started        ← no version complaint; the 2.24.3 fix holds
Failed to open disc
Call to MakeMKV failed with code: 11
```

Different from every earlier failure. Identification succeeded, which means the
disc mounted and `pydvdid` read `VIDEO_TS` — so it is readable as a filesystem
and MakeMKV specifically could not open it for decryption. No `ata4` events on
the host during the job, so the drive is not at fault.

That leaves the disc: physical damage, or a protection scheme MakeMKV 1.18.4
does not handle. Universal DVDs of that era used ARccOS deliberate-bad-sector
protection. **Not yet diagnosed** — retry it, clean it, and if it fails again
compare against another disc from the same studio before concluding anything.

### 2026-09-03 — phase 2b: a TV disc rips, and shows what TV costs

Job 21, The Sylvester & Tweety Mysteries Season 1 Disc 1. The charset patch did
its job — the folder is `tv/The-Sylvester-and-Tweety-Mysteries (1995-2002)` with
an ASCII hyphen, where job 20 died on the en dash.

**10 hours 47 minutes**, 15:44 → 02:31, with the drive locked throughout (D8).
14.6 GB ripped in 3h48m, then 7 hours of transcoding.

Nine files landed in `completed/tv/`, and eight of them are right:

| file | duration | size |
|---|---|---|
| `title_0.mkv` | 21m | 239 MB |
| **`title_1.mkv`** | **168m** | **1.88 GB** |
| `title_2.mkv` … `title_8.mkv` | 20–21m | 215–252 MB |

**`title_1.mkv` is the disc's "play all" title** — every episode concatenated,
2h48m, transcoded in full at a cost of about three and a half hours and 1.88 GB
that duplicates the other eight files exactly.

`MINLENGTH: 420` passes it, `MAINFEATURE: false` transcodes it, and nothing in
ARM distinguishes a compilation from an episode: both are simply titles over the
threshold. The series branch of `move_files_post` has no equivalent of
`skip_transcode_movie`'s largest-file selection, and it should not — for a TV
disc every title genuinely might matter.

**Two consequences.**

**Phase 3 is affected.** A Blu-ray TV set will have the same shape with larger
files, and 3.5 hours of wasted encoding per disc is worse there. Worth deciding
whether to filter play-all titles before running many discs — a compilation is
recognisable as *a title whose length is close to the sum of the others*, which
ARM's track table has the data for even if ARM does not use it.

**It settles [video-library-ingest.md](video-library-ingest.md).** Positional
mapping is not merely fragile, it is wrong: `title_1` is the play-all, so the
numbering is `episode 1, compilation, episode 2, episode 3…`. Any mover assuming
`title_N` → episode N files seven of eight episodes under the wrong number and
files a 2h48m compilation as an episode. Only content matching can sort this
out.

**Left as it is, deliberately.** The play-all is not deleted: it is a legitimate
artefact of the disc, `DELRAWFILES: false` keeps the raw copy regardless, and
deciding what to discard belongs with the ingest work rather than here.

Also confirmed: the destination is `completed/tv/` while the Plex library is
`/video/shows/`, so the ingest has a folder-name reconciliation to do as well.

### 2026-09-04 — disc 2 of the same season, and the play-all heuristic measured

Job 22, `SYLVESTER_TWEETY_MYSTERY_D2`, 04:29 → 11:36 UTC (**7h07m**), six titles,
success with no errors. The format itself raised nothing new — the value of this
disc is that it is the *second* of a set, which tests two things one disc cannot.

**The play-all heuristic is now measured, not proposed.** Phase 2b suggested a
compilation is recognisable as a title whose length is close to the sum of the
others. Across both discs it holds to within a tenth of a minute:

| disc | play-all | sum of the other titles | delta | episodes |
|---|---|---|---|---|
| 1 | 168.8 min | 168.9 min | 0.1 min | 8 |
| 2 | 105.7 min | 105.8 min | 0.1 min | 5 |

Two discs is a small sample and the rule needs a guard — a disc holding exactly
one episode makes "the sum of the others" meaningless, and a genuine feature-length
title on a mixed disc could sit near the sum by coincidence. But the separation is
wide enough to act on: the play-all is ~8× the length of any single episode, and
the agreement with the sum is three orders of magnitude tighter than the gap to
the next-longest title. Filtering it would have saved **3h30m on disc 1 and
roughly 2h10m on disc 2** — around 40% of each job's transcode time, for output
that duplicates the rest of the disc exactly.

**Multi-disc sets do not merge, and the numbering restarts.** This is the finding
that matters for ingest. Disc 2 did not join disc 1's folder; `have_dupes` was
true, so `check_for_dupe_folder` appended the job's stage to make a second
directory:

```
completed/tv/The-Sylvester-and-Tweety-Mysteries (1995-2002)                 <- disc 1, title_0..title_8
completed/tv/The-Sylvester-and-Tweety-Mysteries (1995-2002)_178840975618    <- disc 2, title_0..title_5
```

Both discs number from `title_0`, so the season now has two `title_0.mkv`, two
`title_1.mkv` and so on, in sibling directories distinguished only by an opaque
stage number. Nothing in the tree records that these are discs 1 and 2 of one
season, or which came first — the stage integer happens to sort correctly, but it
is a timestamp of when the job ran, not a disc ordinal, and re-ripping disc 1
tomorrow would sort it last.

The disc *label* is the only place the ordinal survives: `SYLVESTER_TWEETY_MYSTERY_D1`
and `..._D2`, held on the job row, not in the filesystem. Any ingest that wants to
reassemble a season has to read it from the database, and even then `D1`/`D2` is a
convention of this particular publisher rather than something that can be relied on
generally.

This compounds the positional-mapping conclusion rather than changing it. It was
already true that `title_N` does not map to episode N within a disc, because the
play-all sits at `title_1`. It is now also true that the same filename means
different episodes in different folders of the same season. **Content matching is
not one option among several for TV ingest; it is the only thing that can work.**
Recorded in [video-library-ingest.md](video-library-ingest.md).

**Still unproven: multi-disc *movies*.** A Lord of the Rings Extended Edition
spans two discs holding halves of *one* film, which is a different problem again —
there the two parts must be concatenated or presented as one item, and the
largest-file selection in `skip_transcode_movie` would pick a main feature per
disc rather than recognising the pair. Worth ripping one when a phase has room;
it is a movie-path question, so it does not block phase 3.

### 2026-09-04 — phase 3: the Blu-ray rips, then the node kills the transcode

Job 23, The Rescuers. MakeMKV backed the disc up cleanly — **43 GB of BDMV in
57 minutes**, identified as `bluray`/`movie`, 71 titles found. Then the transcode
died at 02:31:12, and the cause is one no earlier guess had reached.

**Talos' node-level OOM controller killed three pod cgroups in 1.5 seconds** —
ARM first (13 processes), then `victoria-logs`, then `metallb-speaker`. ARM
restarted 18 seconds later with the transcode gone.

**ARM never reached its own 4Gi limit.** It was at 2.9Gi. The *node* ran out:

| time | node MemAvailable | ARM RSS | ARM page cache |
|---|---|---|---|
| 01:22–02:14 (MakeMKV backup) | ~3.1 GB steady | ~400 MiB | 1.6 GiB |
| 02:16 (transcode starts) | 2.7 GB | 805 MiB | 1.2 GiB |
| 02:26 | 994 MB | 2551 MiB | 130 MiB |
| 02:30 | 699 MB | 2836 MiB | 89 MiB |
| 02:31:11 | **SIGKILL to three cgroups** | | |

It is genuine anonymous memory, not a page-cache accounting artefact — the third
column is the proof. The kernel evicted ARM's page cache from 1.6 GiB to 89 MiB
trying to feed the encoder, and still could not keep up.

**The sizing error, which is D6 one level down.** `piraeus-worker-1` is an 8 GiB
VM, but Talos reserves enough that only **5.26 GiB is allocatable**. The 4Gi
container limit set when D6 was closed was chosen against the VM size, not
against allocatable, and with ~3.4 GiB used by everything else on that node ARM
could never reach 4Gi. An unreachable limit is not a limit: the node dies first,
and the container OOM is never recorded — which is why the job sat marked
`transcoding` with `errors: None` and nothing running. D3, produced by D6.

**Why phases 1–2b passed.** The matched control, from the same metric over the
two TV discs:

| workload | resolution | peak container RSS |
|---|---|---|
| jobs 21+22, DVD | 720×480 | **1242 MiB** across two full jobs |
| job 23, Blu-ray | 1920×1080 | **3008 MiB and still climbing** when killed |

Six times the pixels, and `VideoOptionExtra` is empty so SVT-AV1 preset 4 10-bit
runs with unbounded parallelism. DVD resolution kept every earlier phase under
the ceiling; Blu-ray is the first workload that goes over it.

**This is probably what killed the April Blu-rays, but that is not proven.** The
signature matches closely: April's job 8 left `title_43.mkv` complete and
`title_44.mkv` partial, and job 23 died at *exactly the same point* — same title
complete, next one truncated, no error recorded, late in transcode. Same node,
same 8 GiB, same encoder. April's ARM logs and Prometheus data are both long
gone, so the match is a signature and not a cause. Recorded so it is not
re-derived, and not stronger than the evidence.

**The instrumentation worked.** `RipperRestarted` went pending 02:36 and fired
02:41–03:26, which is how the restart was noticed at all. Phase 0 earning its
keep, and the reason this was diagnosed the same morning rather than in April.

#### The fix

`piraeus-worker-1` goes from **8192 to 16384 MiB** (`terraform/main.tf`), taking
allocatable from 5.26 GiB to **13.1 GiB**. The Proxmox host has room: 62 GiB
total, 38 GiB committed to running VMs, and 19.2 GiB of ZFS ARC that gives way
under pressure. The provider restarted the VM in place — `0 added, 1 changed,
0 destroyed` — so the guest saw the new memory immediately.

ARM's limits move with it: **request 1.5Gi → 4Gi, limit 4Gi → 8Gi**. Both halves
matter. The limit is now a number the node can honour, so an over-running encode
dies as a recorded container OOM instead of taking `victoria-logs` and
`metallb-speaker` down with it. The request is sized to the encode rather than to
the idle daemon, because the OOM controller picks victims by usage-over-request
and ARM at 2.9 GiB against a 1.5 GiB request was the obvious candidate.

#### What survives, and what phase 3 still owes

The **43 GB raw BDMV backup is complete and intact**, so the disc does not need
re-ripping — only the transcode. `title_43.mkv` finished at 149.5 MB (within
0.2% of April's 149.3 MB, which is a pleasing sign the encode is deterministic);
`title_44.mkv` is a 25 MB partial, another instance of D4.

The track table confirms this section's original prediction exactly: **5 titles
selected from 71**, being `title_70` (77.2m) and `title_71` (77.1m) — the same
feature via two playlists — plus extras at 30.7m, 10.6m and 8.8m. So the
duplicate-main-feature question is still open and still needs deciding, and
neither feature title has been encoded yet.

#### On 4K, asked while sizing the VM

**16 GiB is right for Blu-ray and marginal for UHD.** SVT-AV1's own documentation
says memory scales with `--lp` (cores targeted), resolution, bit depth, lookahead
distance and hierarchical levels. The published figures for 4K 10-bit are 14 GB
at 8 vCPU, 24 GB at 40 vCPU and 48 GB at 112 cores — the scaling with core count
matters here, because worker-1 has only 4. Measured 1080p peak is >3 GiB, and 4K
is 4× the pixels with linear scaling in the frame buffers, which extrapolates to
roughly 12–16 GiB against 13.1 GiB allocatable.

Two levers exist that cost no RAM, and phase 4 should choose deliberately rather
than inherit the 1080p preset:

- **Cap `--lp`** through `VideoOptionExtra`. The largest single memory lever, paid
  for in encode speed.
- **Do not transcode UHD at all.** Independent of memory, CRF 30 AV1 discards
  most of what makes a UHD disc worth having, and HDR10+/Dolby Vision metadata
  often does not survive HandBrake. A remux keeps the stream intact.

The VM is not being sized for this today: phase 4 is still blocked on whether the
BDR-212U can obtain volume keys at all, and the honest number comes from
measuring a completed 1080p encode and scaling it.

### D12 — a disc with no volume label is confidently misidentified, and only the person holding it can tell

A Blu-ray whose UDF label was never set reports the literal placeholder string
`LOGICAL_VOLUME_ID`. ARM passes that to its identification step, which fuzzy-matches
it against OMDb and returns a real film — in the observed case *The Adventures of
Mary-Kate and Ashley: The Case of the Logical i Ranch* (1994, `tt0282341`),
presumably off the word "Logical". The job is then created with
`hasnicetitle: 1`, meaning ARM considers the identification good and will rip,
transcode, name and file the disc under that title without hesitating.

**Why this is worse than an ordinary failure.** The Pushover notification reads
`Found disc: The-Adventures-of-Mary-Kate-and-Ashley--The-Case-of-the-Logical-i-Ranch.
Disc type is bluray.` — well-formed, plausible, and indistinguishable from a
correct identification. Nothing about it signals a problem. **Only the person who
physically put the disc in the tray can tell it is wrong**, and only if they read
the notification and recognise the title as not theirs. That makes it the sharpest
failure mode yet for the self-service goal, because the whole point of the
notification is that Will's brother can load discs without anyone watching the
cluster.

The ten-minute `MANUAL_WAIT` is the existing remedy, and it does work — the job
sits in `waiting` and the title can be corrected in the web UI at
`/jobdetail?job_id=<id>` before the rip starts. But it only helps someone who
already suspects the identification is wrong.

**The exact mechanism**, read from `arm/ripper/identify.py`, because it is more
actionable than the symptom.

`identify_bluray` (line 96) reads the disc's own metadata from
`BDMV/META/DL/bdmt_eng.xml`. **This disc has an empty `META/DL/` directory**, so
the open raises `OSError` and control reaches the fallback at line 106 — which
upstream itself marks with `# Maybe call OMdb with label when we can't find any
ident on disc ?`. That fallback takes `job.label`, replaces underscores with
spaces and title-cases it, giving `Logical Volume Id`, then **returns `True`**.
Returning true is the defect: the caller cannot distinguish *identified from disc
metadata* from *guessed from a label*, so `hasnicetitle` is set either way and the
OMDb search that follows matches on the word "Logical".

**Blu-rays have no content-based fallback at all, and DVDs do.** `identify_dvd`
(line 149) computes `pydvdid.compute()` over the mounted disc and queries
`1337server.pythonanywhere.com` with the CRC64, which maps a disc *hash* to a
title and is immune to labels entirely. Nothing equivalent runs for Blu-ray.
So the disc type least likely to carry a useful label is the one with no fallback
when the label is useless — which is why this surfaced on a Blu-ray and never on
a DVD.

Worth noting a second oddity found while reading this: on the successful path,
`bluray_year` is taken from the **file mtime of `bdmt_eng.xml`** (line 130), not
from any metadata field. That is the disc's authoring date standing in for the
film's release year, and it will be wrong for every catalogue re-issue.

**The signal ARM has and does not use.** A label that is exactly a known
placeholder is not weak evidence, it is *no* evidence, and the two are being
treated identically. `LOGICAL_VOLUME_ID`, `DVD_VIDEO`, `LOGICAL_VOLUME_ID`-style
authoring defaults and bare `44069507_VOLUME_ID` numeric IDs should suppress
`hasnicetitle` and force the unidentified path rather than seeding a title search.
That is a small, well-bounded change: a deny-list check before identification runs,
setting the job to unidentified instead of accepting whatever OMDb returns.

Worth noting the two UDF fields disagree, which is its own small trap for anyone
diagnosing this: `blkid` reads the *Logical Volume Identifier* and reported
`LOGICAL_VOLUME_ID`, while the kernel's UDF driver reads the *Volume Identifier*
and logged `Mounting volume '44069507_VOLUME_ID'`. Both are placeholders, but a
check written against one field will not see the other.

**Related but distinct: the disc was not the one expected.** The volume timestamp
reads 2007/09/04, and The Rescuers 35th Anniversary is a 2012 release, so the
disc in the tray is independently confirmed as a different one. That is a physical
swap during the hours the tray sat open, not a misread — worth separating from
D12 itself, which is about what ARM does with an unlabelled disc however it got
there.

Direct reads of the BDMV return `Illegal Request / Invalid field in cdb` and
`critical target error`. That is ordinary AACS protection refusing plain reads,
not disc damage; MakeMKV decrypts and is unaffected. Recorded because the kernel
log looks alarming and will otherwise be re-investigated.

> **Wrong — corrected 2026-09-21.** A protection refusal is ASC `0x6F`, not
> `Invalid field in cdb`. These are oversized reads that the host rejects; see
> [the 2026-09-21 entry](#2026-09-21--the-invalid-field-in-cdb-flood-is-a-transfer-size-limit-not-protection).
> The CDB lengths from this disc weren't recorded, so it isn't proven that this
> instance had the same cause. It carries the same signature, though.

#### How it resolved, and the cheap check that falls out

The disc was **The Polar Express** (2004) — a Warner Blu-ray released
2007-10-30, authored 2007-09-04, eight weeks before release. Every measured
property agrees: 1:39:56 against a stated 1h40m runtime, 1080p **VC-1** exactly as
Warner encoded it, 2.40:1 scope, and standard-definition extras as early Warner
discs carried. It had been sitting in the wrong case, which is why it arrived
labelled as a Rescuers disc.

**The authoring timestamp is not the release date, and searching it as one finds
nothing.** Eight weeks separated the two here. The timestamp is still useful — it
bounds the release from below and pins the era — but only as a range.

**A human misidentified this disc too, and the same check caught both.** The
title offered from the physical case was *The Rescuers Down Under*, which runs
77 minutes against this disc's 99:56, and no 77-minute title existed anywhere on
it. That is the same evidence that exposed OMDb's guess, applied to a human's.
Worth stating plainly because it generalises:

> **Compare the main title's runtime against the runtime of the film it is
> claimed to be.** A disc holding a 100-minute feature is not a 77-minute film,
> whoever says otherwise. The check costs one lookup, needs no disc metadata, and
> is independent of whatever produced the title — OMDb, a label, or a person
> reading a sleeve.

This belongs with the placeholder deny-list rather than replacing it: the
deny-list stops a bad title being *generated*, and the runtime check catches a
bad title from any source, including the manual-override path the deny-list would
push unlabelled discs onto. Neither subsumes the other.

### D13 — an unreadable disc region wedges the drive indefinitely, and the failure restarts itself

Three separate faults, all triggered by one marginal disc. They compound, and
only the first is about the disc at all.

**A blocked read has no timeout.** On job 30 the log runs from 17:27 to 00:10
with *nothing in between* and the output file stops growing at 18:28. MakeMKV was
not retrying and reporting — it sat blocked on a single read for **5 hours 42
minutes**, holding the drive, and came loose only when somebody opened the tray.
Job 31 stalled 95 minutes before MakeMKV gave up on its own. Nothing crashes and
nothing restarts, so `RipperRestarted` cannot fire: this is invisible to every
alert that exists. It is the `MANUAL_WAIT` wedge of D1 again, arrived at from a
different direction, and the same consequence — the drive is held and nobody
knows.

**Failure re-triggers itself, without bound.** When the drive faults it drops the
medium, which fires a udev event, which starts a fresh job on the same disc:

```
job 30 fails 00:10:32  ->  job 31 starts 00:11:08
job 31 fails 03:37:51  ->  job 32 starts 03:38:44
```

Roughly 3.5 hours per cycle, forever, until a person intervenes. The media-property
gate and flock from D2 correctly admit each of these: a disc really is present and
no other job holds the drive. Nothing in the chain asks whether *this disc has
just failed*.

> **Wrong — corrected 2026-09-22.** Job 32 was not Field of Dreams. It was
> *The Sylvester and Tweety Mysteries* disc 2 (label `SYLVESTER_TWEETY_MYSTERY_D2`),
> loaded by hand. It was abandoned in error while in `waiting`, on the assumption
> it was a retry, without its title being checked — which is the `Received
> SIGTERM` on its record. It was re-run as job 33, which succeeded. Job 31 is the
> same disc as job 30 and started 36 seconds after job 30's `TRAY OPEN` error, but
> nothing recorded whether a person reinserted it. No instance of a failure
> starting a new job on its own has been observed.

**A failed secondary title discards a good primary one.** The worst of the three,
because it destroys work that succeeded. ARM selected two titles over
`MINLENGTH`; title 0 (105.1m, the film) ripped perfectly on both attempts, and
title 1 (88.4m) was unreadable. ARM fails the whole job, so the feature is never
transcoded and never leaves `raw/`. On marginal media that is exactly the wrong
trade — one bad extra throws away the film.

#### What the disc and drive actually did

The drive's own report, which is the part worth keeping:

```
Sense Key : Not Ready       Add. Sense: Medium not present - tray open
Sense Key : Hardware Error  Add. Sense: Internal target failure
```

`Internal target failure` is the drive reporting an unrecoverable fault of its
own, after which it drops the disc entirely. **The drive is healthy** — the
control is unambiguous:

| disc | MakeMKV read errors |
|---|---|
| Shrek the Third (DVD) | 0 |
| The Great Race (DVD) | 0 |
| The Rescuers (Blu-ray) | 0 |
| Field of Dreams, attempt 1 | 11 |
| Field of Dreams, attempt 2 | 11 |

Throughput agrees: title 0 read at 1.07 MB/s against 1.10–1.35 MB/s on the discs
either side of it, so the drive performed normally right up to the moment it
stopped.

**It is not a scratch.** The two attempts failed at *different* offsets — 141 MB
and 44 MB into `VTS_02_1.VOB` — where a fixed defect fails at a fixed sector. The
disc was later cleaned and inspected and looks undamaged, which points at disc
rot or a manufacturing defect in the reflective layer rather than anything on the
surface.

#### Two diagnostic gaps this exposed

**Kernel logs are not persisted anywhere.** Alloy collects pod logs only, so
`victoria-logs` has nothing from the kernel; the evidence for job 30's failure
window is gone for good. Everything above had to be reconstructed from ARM's own
log and from throughput controls.

**CSS refusals flood the ring buffer.** Reading a protected DVD generates a
constant stream of `Illegal Request / Invalid field in cdb` — 588 in one seven
minute window — which is *normal* and means nothing, but rolls `dmesg` so fast
that its useful history is minutes deep. Any real medium error is pushed out
before anyone can look. Capture the kernel log while a failure is still fresh, or
it will not be there.

> **The diagnosis above is wrong; the consequence stands — corrected
> 2026-09-21.** The flood is neither CSS nor normal: every entry is a 384 KiB
> read over the host's 128 KiB limit, and it has a fix; see
> [the 2026-09-21 entry](#2026-09-21--the-invalid-field-in-cdb-flood-is-a-transfer-size-limit-not-protection).
> "588" is also not a rate. It is how many of these 13-line entries fit in the
> ring buffer, and a second capture on 2026-09-21 held exactly 588 as well.

---

### 2026-09-17 — phase 3 closes: a Blu-ray end to end

Job 27, The Rescuers, **02:51 → 12:34 (9h42m)**, 71 titles scanned, 5 selected,
success. This is the same disc whose transcode the node OOM killed on 2026-09-04,
run again after worker-1 went to 16 GiB, and it completed without incident. The
resize is therefore confirmed by the workload that exposed the problem rather
than by a proxy.

> **Wrong — corrected 2026-10-05.** Not without incident. The host logged 12
> `ata4` command timeouts across both `makemkvcon` runs, 03:02–03:08 UTC. Every
> one recovered, but libata stepped the drive down to UDMA/33. The info scan
> ended `Failed to open disc`, and the backup then succeeded. See
> [the 2026-10-05 entry](#2026-10-05--the-drives-history-since-april-and-what-each-era-ran).

Two DVDs followed without drama — Shrek the Third (job 28, 4h17m) and The Great
Race (job 29, 5h33m) — so the DVD path is steady across repeat use, which it had
not been before phase 0.

### 2026-09-19/20 — Field of Dreams, and three faults behind one error

A DVD failed twice and took most of two nights doing it. The disc is at fault,
but almost nothing about the *outcome* was, and the three defects it exposed are
recorded as [D13](#d13--an-unreadable-disc-region-wedges-the-drive-indefinitely-and-the-failure-restarts-itself).

**What was lost is small and now bounded.** ARM selected two titles: the feature
at 105.1m and a second at 88.4m. Only the second is unreadable, and nothing was
queued behind it — the remaining thirteen titles are 24–35 second menu stingers
plus one 2:27 clip, all under `MINLENGTH`. So the loss is exactly one 88-minute
title.

What that title *is* stayed unresolved, because identifying it requires reading
the disc, which is the thing that fails. It is 4:3, 29.97 fps, 31 chapters, in
its own title set, using 41 cells against the feature's 48 — an 85% ratio
tracking its 84% duration ratio, at essentially the same chapter density. That
reads as an alternate cut or second feature rather than a bonus documentary, but
it is inference and should not be written down as more.

**It is not the commentary, and that is worth stating because it was the obvious
guess.** The commentary is an alternate *audio track* on the feature itself, not
a separate title: HandBrake's scan shows source track 3 as plain `AC3, 2.0 ch`
where tracks 1 and 2 are `Dolby Surround` — the signature of a flat stereo
commentary recording. It was captured with the film and survives into the
transcode as output audio track 4. A commentary would also run the film's length,
not 88 minutes.

#### Recovering the film without the disc

The feature had ripped correctly *twice*, and ARM had thrown both away by failing
the job. Running HandBrake by hand over the surviving raw MKV, with ARM's own
preset and command shape, produced the file ARM would have:

```
completed/movies/Field-of-Dreams (1989)/Field-of-Dreams (1989).mkv
980 MB · 105.6 min · AV1 · 4 audio (incl. commentary) · 2 subtitle tracks
```

Write to a `.part` name and rename only on a zero exit, so a partial cannot be
mistaken for a finished file — D4 is still live and this path has no ARM job
tracking it.

**Two independent rips are a stronger integrity check than any single one.**
Jobs 30 and 31 each produced the feature, and their video streams hash
identically:

```
MD5=263d67e648a8fa091b47e5bd322aef4e   (job 30)
MD5=263d67e648a8fa091b47e5bd322aef4e   (job 31)
```

The files differ by one byte of container metadata and not at all in content. Use
this whenever a disc has been ripped more than once; it costs one demux per copy
and proves far more than a decode pass, which only shows the file is internally
consistent. Both source and output also decode clean — every message ffmpeg emits
is the same benign muxer `dts` warning from writing DVD subtitles to a null
muxer, and zero are decode errors.

**Aspect handling is correct, and looks wrong at a glance.** The output is
718x358, which is not an error: the source is a 4:3 letterboxed transfer and
HandBrake auto-cropped the bars (60 top, 62 bottom, asymmetric because the
transfer is). With `SAR 8:9` it displays 1.783:1 against a nominal 1.85:1, so it
**under**-cropped by about 4% — a few black lines kept rather than picture cut,
which is the safe direction. HandBrake also detected the true film rate of 23.976
where ARM's database records the DVD's nominal 29.97, and produced 151,917 frames
= exactly 105.6 minutes.

### 2026-09-21 — the `Invalid field in cdb` flood is a transfer-size limit, not protection

Raised as "worker-1's logs are constantly spammed trying to mount sr0". Nothing
mounts sr0, and nothing outside ARM touches the drive:

- **Only MakeMKV holds the drive.** During job 36, `makemkvcon` was the only
  process with it open: `/dev/sr0` with flags `0140000` (`O_DIRECT`, read-only)
  and `/dev/sg0` read-write. `/proc/mounts` had no sr0.
- **There is no background reader.** `node_disk_reads_completed_total{device="sr0"}`
  on worker-1 was exactly zero between jobs over 2026-09-18 → 21, and non-zero
  only inside the windows of jobs 29–36. That is unlike the NDM scanner in
  `docs/automatic-ripping-machine.md`, which read the drive continuously. Talos's
  `DiscoveredVolume sr0` was at version 23, meaning one probe per media change.

The failures themselves:

- All 588 in the ring buffer were `Read(10)` with transfer length `0xc0`
  (384 KiB), marching in a fixed 384-block stride.
- All 588 had the same sense: `Illegal Request / Invalid field in cdb`.
- The rate was ~1.5/s, about equal to sr0's whole block-read rate at the time.
  So nearly every block-layer read MakeMKV made failed.
- The host's limit (`/sys/block/sr0/queue/max_hw_sectors_kb` on vulcanus) is 128.
  The guest's is 32766, which is virtio-scsi's default `max_sectors=0xFFFF` in
  2048-byte blocks. `scsi-generic` never passes the host limit through.
- A request over the host limit gets `EINVAL` from the host's `sg` driver, which
  QEMU returns to the guest as `Invalid field in cdb` with `DID_OK`.

A protection refusal would have been ASC `0x6F`, and would have depended on
which sector was read rather than how many. The two D12/D13 notes calling this
normal are marked wrong where they stand.

**Fix:** `max_sectors=256` on the `virtio-scsi-pci` device in
`terraform/modules/proxmox_talos_vm/main.tf`. The mechanism is in
`docs/automatic-ripping-machine.md`. It needs a QEMU restart of worker-1, so it
takes effect on the next `tofu apply` made between rips.

**Still to confirm, once applied:**

1. ~~Guest `max_hw_sectors_kb` for sr0 reads `128`, not `32766`.~~ Confirmed
   2026-09-21 after `tofu apply` restarted VM 911. `max_sectors_kb` is also
   `128`, `events_poll_msecs` is still `2000`, and ARM's fresh allocation
   reports the Pioneer on both `sr0` and `sg0`.
2. ~~A full rip leaves `dmesg` free of `Invalid field in cdb`.~~ Confirmed
   2026-09-22 on job 39 (Around the World in 80 Days, DVD). The first 47 minutes
   of bulk reads had zero `Invalid field in cdb` in a ring buffer holding
   everything since boot. sr0 did 12.8 block reads/s averaging exactly
   **128.0 KiB**. MakeMKV still opens `/dev/sr0` `O_DIRECT` and asks for
   384 KiB; the guest kernel now splits each into three requests the host
   accepts. The only sr0 errors are six single `Read of scrambled sector
   without authentication` (ASC `0x6F`), at insertion and at the rip's start.
   That is a real protection refusal, and useful as a control for what one
   looks like.
3. Throughput: job 39 read 1.68 MB/s through sr0 and wrote title 0's 4.8 GB at
   a matching rate. That's against ~1.1 MB/s on job 36 and 1.07–1.35 MB/s on
   the discs before it. This is one disc against another, so it's suggestive,
   not proven. It is still about 1.2x DVD, far under the drive's rating, so
   whatever limits rip speed is somewhere else. Compare against job 36. Job 36 wrote its first two titles,
   3.96 GB, between 13:36:15 and 14:34:47, about 1.1 MB/s, while every block
   read failed. Whether the fix changes rip speed is open. Don't claim either
   way until a rip has been measured.

### 2026-09-22 — the drive stops answering its SATA link

Observations only, kept for future troubleshooting. No cause has been
established, and none is proposed here.

Times are UTC unless marked Denver (UTC−6), which is what the host's journal
uses. `max_sectors=256` from the 2026-09-21 entry was in effect throughout.

#### Sherlock Holmes in the 22nd Century — jobs 37 and 38

- Both inserts logged `Starting ARM for Data Disk on sr0 with File System udf`.
  They're the only two `Data Disk` lines in the wrapper log. Every other insert
  since 2026-09-01 logged `Starting ARM for DVD on sr0`, including the ones
  after it. The job record still reads `disctype: dvd`.
- **Job 37.** Manual wait began 00:57:12. The wrapper logged six `a job already
  holds /dev/sr0, skipping` between 01:00:00 and 01:00:04. The job received
  `SIGTERM` at 01:01:37 while still in `check_for_wait`.
- **Job 38.** `makemkvcon ... info --cache=1 disc:9999` started 01:11:00. The host
  logged the sequence below from 01:11:31. At 01:12:32 MakeMKV reported
  `Internal error - Operation result is incorrect (132)` while reading
  `BD-RE PIONEER BD-RW BDR-212U 1.01 ALDL017235WL` — the drive, not a file — at
  offsets 32768, 524288 and 1048576, then `Failed to open disc`, then
  `Unknown device - '/dev/sr0'`. Exit code 11. This job never logged `Using
  direct disc access mode`.

Host kernel, Denver time:

```
19:11:31  ata4.00: exception Emask 0x0 SAct 0x0 SErr 0x0 action 0x6 frozen
19:11:31  ata4.00: cmd a0/00:00:00:04:00/00:00:00:00:00/a0 tag 9 pio 16388 in
19:11:31  ata4.00: status: { DRDY }
19:11:31  ata4: hard resetting link
19:11:37  ata4: link is slow to respond, please be patient (ready=0)
          ... hard resetting link / slow to respond, x4 over 55 s ...
19:12:31  ata4: hardreset failed
19:12:31  ata4: reset failed, giving up
19:12:31  ata4.00: disable device
19:12:32  sr 3:0:0:0: [sr0] ... FAILED Result: hostbyte=DID_BAD_TARGET
```

#### The drive after the disable

- Every SCSI command to `sr0` returned `DID_BAD_TARGET`. That included
  `Prevent/Allow Medium Removal` and `Start/Stop Unit 1b 00 00 00 02 00` — an
  eject — at 19:12:35 and 19:17:39 Denver.
- `CDROM_DRIVE_STATUS` read `2` (tray open) on both the host and the guest, with
  the disc physically in a closed tray. There were no `ID_CDROM_MEDIA_*` udev
  properties.
- Nothing in the ARM container held `/dev/sr0`, and nothing had it mounted.
- Reported from the house: the disc wouldn't eject with the drive's own button,
  the ARM UI's eject button, or `eject /dev/sr0` on the VM.

#### Recovery attempts, in order

1. **Delete and rescan, VM stopped.** With worker-1 drained, VM 911 stopped and
   nothing holding `/dev/sg3`:
   `echo 1 > /sys/class/scsi_device/3:0:0:0/device/delete`, then
   `echo "- - -" > /sys/class/scsi_host/host3/scan`. It failed the same way:
   `link is slow to respond (ready=0)` x3, `limiting SATA link speed to
   <unknown>`, `hardreset failed`, `reset failed, giving up`. Neither `/dev/sr0`
   nor `/dev/optical-drive-sg` came back.
2. **VM 911 in that state.** `qm showcmd 911` opens
   `-drive file=/dev/optical-drive-sg`, which did not exist. Starting it wasn't
   attempted.
3. **Cold power cycle.** Worked. `poweroff` on the host, at least 30 s fully off,
   then power on through JetKVM. On boot: `ata4: SATA link up 1.5 Gbps`,
   `ATAPI: PIONEER BD-RW BDR-212U, 1.01`, `configured for UDMA/100`, and
   `/dev/optical-drive-sg -> sg3` came back. VM 911 started through `onboot`,
   and all nodes came up Ready. Worker-1 was still cordoned from the drain
   until someone uncordoned it.

Checked before the power cycle, for anyone repeating it: every VM and CT had
`onboot=1`, no vzdump or `zfs send` was running, and `zpool status -x` was
healthy.

After the power cycle, before anything had opened the drive, the drive's own
button ejected the Sherlock disc.

#### Around the World in 80 Days — job 39, after the power cycle

- Logged `Starting ARM for DVD on sr0`.
- The first `makemkvcon` (the info scan, ~04:50) ran with no `ata4` event.
- The second, the rip, started at 05:11:46. At 05:12:18 the host logged:

  ```
  ata4.00: exception Emask 0x0 SAct 0x0 SErr 0x0 action 0x6 frozen
  ata4.00: cmd a0/00:00:00:04:00/00:00:00:00:00/a0 tag 15 pio 16388 in
  ata4.00: status: { DRDY }
  ata4: hard resetting link
  ata4: EH complete
  ```

  MakeMKV logged `Using direct disc access mode` at 05:12:23. It then ripped
  7311 MB with no `MSG:2003` read errors.
- ARM's auto-eject at 06:47:07 (`eject --verbose --cdrom --scsi /dev/sr0`)
  succeeded. The record shows `ejected: 1`, the drive read `2` afterwards, and the
  host logged no `sr0` or `ata4` lines around it.

#### The two `ata4` events side by side

| | `makemkvcon` start | `ata4` exception | offset | reset |
|---|---|---|---|---|
| job 38, Sherlock | 01:11:00 | 01:11:31 | 31 s | failed, device disabled |
| job 39, Around the World | 05:11:46 | 05:12:18 | 32 s | `EH complete` within the second |

Both had the same exception line, the same `cmd a0/...` ATAPI packet command
with a 16,388-byte PIO transfer, and `status: { DRDY }`. Job 39 logged `Using
direct disc access mode` 5 s after its exception. Job 38 never logged it.

#### Reference data

- **Negotiated link speed** was 1.5 Gbps on every boot checked: 2026-05-04,
  09-15, 09-16 and 09-21.
- **Count of `ata4` lines** matching `ata4.*(exception|reset|disable)`: 34 on the
  boot holding the Sherlock event, and 0 on the boot after the power cycle up to
  job 39's event.
- **The 09-16 boot** logged 12 `ata4.00: exception` lines that day. The host
  booted at 2026-09-15 23:35 and again at 09-16 00:20 Denver. ARM pod `8b4g8`
  failed `UnexpectedAdmissionError ... no healthy devices` at 09-15 23:39 Denver.
  Nothing was recorded about why the host booted twice.
- **Job 19** (An American Tail, exit code 11, 2026-09-01 19:45–19:56 Denver)
  has no `ata4` or `sr0` kernel lines in its window. The same query returns
  other kernel lines for that window and finds the 09-21 events, so the empty
  result is real.

#### Where the evidence lives

- The host's kernel journal persists across boots:
  `journalctl _TRANSPORT=kernel --since ... --until ...` on vulcanus. Note that
  `journalctl -k` implies the current boot only.
- Alloy doesn't ship the guest's kernel log, so `talosctl -n 192.168.0.196
  dmesg` holds only worker-1's current ring buffer.
- `qm showcmd 911 --pretty` shows the QEMU arguments actually in use, rather than
  what Terraform declares.
- `sr0` on the host is `3:0:0:0`, on `host3`, which is `ata4`.
  `/dev/optical-drive-sg` is a udev symlink matched on the model string, so it
  follows the drive to whatever `sg` node it gets.

#### The drive's own button during a job

- `dev.cdrom.lock` is `1` on worker-1.
- ARM unmounts at the end of `identify()` (`identify.py:93`, via `os.system`, so
  it never appears in the job log), before `check_for_wait`.
- During a rip, `makemkvcon` holds `/dev/sr0` open. See the 2026-09-21 entry.
- What held the drive during job 37's manual wait was not identified.

### 2026-09-22/30 — two more SATA link losses, then eight days without the drive

Observations only, continuing the 2026-09-22 entry. No cause has been
established, and none is proposed here. Host times are Denver.

**Job 40** — *Around the World in 80 Days* disc 2, after disc 1 (job 39) had
completed. The info scan reached `Using direct disc access mode` in 7 s and ran
23 minutes without incident. The rip's `makemkvcon` started 13:55:42 UTC; 32 s
later the host logged the same `Emask 0x0` exception on the same 16,388-byte
PIO command as jobs 38 and 39, four failed link resets, and `disable device`.
ARM's eject at 13:57:16 UTC left no `Start/Stop Unit` in the host log; the job
still records `ejected: 1`. All 14 commands after the disable returned
`DID_BAD_TARGET`. Recovered by cold power cycle (no drain).

After that boot, `eject -t /dev/sr0` on the host returned `CD-ROM tray close
command failed: No such file or directory`.

**Job 41** — the same disc, reinserted from the host after the power cycle. The
disc gate logged `no disc in /dev/sr0 (status=2), skipping` during the eject and
`Starting ARM for DVD on sr0` after the close. A different signature this time:

```
16:54:18  ata4.00: exception Emask 0x10 SAct 0x0 SErr 0x4090000 action 0xe frozen
16:54:18  ata4.00: irq_stat 0x00400040, connection status changed
16:54:18  ata4: SError: { PHYRdyChg 10B8B DevExch }
16:54:18  ata4.00: cmd a0/00:00:00:04:00/00:00:00:00:00/a0 tag 14 pio 16388 in
16:54:19  ata4: SATA link down (SStatus 0 SControl 300)
16:54:25  ata4: SATA link down ... limiting SATA link speed to <unknown>
16:54:30  ata4: SATA link down (SStatus 0 SControl 3F0)
16:54:30  ata4.00: disable device
16:54:30  ata4.00: detaching (SCSI 3:0:0:0)
```

Unlike the earlier disables, the device was detached: `/dev/sr0`,
`/dev/optical-drive-sg` and `3:0:0:0` disappeared from the host. Inside the
guest, `/dev/sr0` remained and the device plugin kept advertising
`devic.es/cdrom: 1`. MakeMKV reported `Scsi error - HARDWARE ERROR:INTERNAL
TARGET FAILURE`, then `Unknown device - '/dev/sr0'`.

| job | `ata4` exception | into the `makemkvcon` run | outcome |
|---|---|---|---|
| 38 | `Emask 0x0`, link up | 31 s (1st run) | disabled |
| 39 | `Emask 0x0`, link up | 32 s (2nd run) | recovered |
| 40 | `Emask 0x0`, link up | 32 s (2nd run) | disabled |
| 41 | `Emask 0x10`, `PHYRdyChg 10B8B DevExch`, link down | ~35 s after the wait | disabled and detached |

**After job 41.** The drive stayed absent for eight days. The host was powered
off cleanly at 2026-09-30 17:24 (`systemd-poweroff`) and booted 17:27, when
`ata4` linked at 1.5 Gbps and identified the BDR-212U. From that boot to
2026-10-03 there are no `ata4` exceptions — but also no ARM jobs, so the quiet
is not evidence either way. Disc 2 of *Around the World* is still unripped.

### 2026-10-04 — job 42, with the host's SCSI commands traced

Observations only, continuing the two entries above. No cause has been
established, and none is proposed here.

*Around the World in 80 Days* disc 2 again, the fifth attempt on it. This time
the host traced every SCSI command sent to the drive.

#### How the trace was taken, to repeat it

On vulcanus, in a private trace instance filtered to the drive's SCSI host
(`host3`, which is `ata4`), streamed to a file, started during the manual wait:

```
T=/sys/kernel/tracing/instances/arm-sata
mkdir $T && echo 8192 > $T/buffer_size_kb
for e in scsi_dispatch_cmd_start scsi_dispatch_cmd_done scsi_dispatch_cmd_error scsi_dispatch_cmd_timeout; do
  echo "host_no == 3" > $T/events/scsi/$e/filter; echo 1 > $T/events/scsi/$e/enable
done
for e in ata_eh_link_autopsy ata_eh_link_autopsy_qc ata_link_hardreset_begin ata_link_hardreset_end; do
  echo 1 > $T/events/libata/$e/enable
done
echo 1 > $T/tracing_on
nohup sh -c "cat $T/trace_pipe > /var/tmp/arm-sata/trace-<job>.txt" &
```

Remove it afterwards with `echo 0 > $T/tracing_on`, kill the `cat`, then
`rmdir $T`. Each event records the raw CDB, and the `done` events also record
the result and sense key. All guest commands arrive through QEMU, so the trace
can't tell which guest process sent a given command.

While idle, the only traffic is `GET EVENT STATUS NOTIFICATION` (`0x4A`) every
2 s, the guest kernel's media polling, and every one completed `DID_OK`.

#### Timeline

`makemkvcon ... info --cache=1 disc:9999` started at 20:16:25 UTC, which is
host trace time ≈ 334228.8. Times below are host trace seconds.

| trace time | command | raw CDB | result |
|---|---|---|---|
| 334229.240 | `MODE SELECT(10)`, 0x4E0 bytes | `55 10 00 00 00 00 00 04 e0 00` | rejected, valid sense |
| 334229.256 | `WRITE BUFFER`, buffer `0xB0` | `3b 02 b0 00 0e 20 00 00 10 00` | good |
| 334229.260 | `READ BUFFER`, buffer `0x77` | `3c 02 77 00 00 00 00 00 20 00` | good |
| 334230.886 | `MODE SELECT(10)`, 0x740 bytes | `55 10 00 00 00 00 00 07 40 00` | rejected, valid sense |
| **334230.901** | **`READ BUFFER`, buffer `0x77`** | **`3c 02 77 12 10 00 00 00 04 00`** | **good, 9.5 ms** |
| 334230.99 – 334232.61 | `READ(10)` ×~30, LBAs 0–528, 62089 | | good |
| 334232.61 – 334233.98 | `INQUIRY`, `GET CONFIGURATION`, `READ DISC STRUCTURE`, `READ TOC`, `READ DISC INFORMATION`, `READ CAPACITY`, `READ BUFFER` `0xF1`/`0xB0`/`0xF4` | | good, or rejected with valid sense |
| 334234.138 | `SET CD SPEED` | `bb 00 ff ff ff ff 00 00 00 00 83 00` | good |
| 334234.555 | `MODE SELECT(10)`, 0x740 bytes | `55 10 00 00 00 00 00 07 40 00` | rejected, valid sense |
| **334234.574** | **`READ BUFFER`, buffer `0x77`** | **`3c 02 77 12 10 00 00 00 04 00`** | **no completion** |
| 334265.046 | — | | SCSI timeout on that command, 30.5 s after it started |
| 334265.054 | libata | | `eh_action=RESET`, `err_mask=TIMEOUT` |
| 334265 – 334320 | `ata_link_hardreset` ×4 | | each `rc=-16` |

The host kernel logged the exception at 20:17:03 UTC with the same signature as
jobs 38–40 (`Emask 0x0`, `cmd a0/00:00:00:04:00/00:00:00:00:00/a0 ... pio 16388
in`), then `reset failed, giving up` and `disable device` at 20:18:03.
`/dev/optical-drive-sg` remained on the host. Job 42 ended `fail` at 20:18:06.

#### Observed

- The command in progress when the drive stopped responding was `READ BUFFER`
  (`0x3C`, mode `02`) to buffer ID `0x77`. `READ BUFFER` reads the device's own
  buffers rather than the medium.
- The identical CDB completed in 9.5 ms 3.7 s earlier in the same run. Both
  times, the command immediately before it was the same rejected
  `MODE SELECT(10)` with a 0x740-byte parameter list.
- The drive stopped responding about 9 s after `makemkvcon` started, at trace
  time 334234.57. The kernel's exception came 30.5 s after that, when the
  command timed out. Jobs 38–40 had no trace. Their exceptions came 31–32 s after
  `makemkvcon` started, which doesn't show when those drives stopped
  responding.
- Up to the hang, the only commands read from the disc were the single-block
  and 16-block `READ(10)`s in the timeline. No title data had been read.
- The run produced 11 libata autopsies with `err_mask=0` and `SENSE_VALID`
  (commands the drive rejected with sense data), and one with
  `err_mask=TIMEOUT`.

> **Wrong — corrected 2026-10-05.** Two `makemkvcon` runs, not one. ARM's log
> has `info --cache=1 disc:9999` from 20:16:23 to 20:16:30 (`Failed to open
> disc`), then `info --cache=1 disc:0` from 20:16:31. The hang at 334234.57 is
> ≈ 20:16:32.5 UTC, about 1.5 s into the `disc:0` run. The rows from 334232.61 on
> are that run's start-up. Between `SET CD SPEED` and the hung pair, the table
> also leaves out `READ DISC STRUCTURE` ×2, `INQUIRY` and `READ BUFFER 0xF1`.
> The last two matter. See
> [job 43](#2026-10-05--job-43-a-traced-run-that-completed-around-one-recovered-hang).

#### Running count of `makemkvcon` runs and `ata4` events, jobs 37–42

| disc | runs | `ata4` events |
|---|---|---|
| *Around the World* disc 2 | 4 (job 40 ×2, 41, 42) | 3, all disabled |
| *Around the World* disc 1 | 2 (job 39) | 1, recovered |
| *Sherlock Holmes in the 22nd Century* | 1 (job 38) | 1, disabled |

Job 37 (Sherlock) ended in its manual wait and never ran `makemkvcon`. Jobs
37–42 are every ARM job since 2026-09-22. Before that, the host logged 12 `ata4`
exceptions on 2026-09-16 and none afterwards until job 38.

The trace, the condensed command list and the host kernel log are in
`captures/arm-sata/` in the `disc-ripping` worktree. The raw trace is also on
vulcanus at `/var/tmp/arm-sata/trace-job42.txt`. No trace exists of a run that
completed, so there is nothing yet to compare the command sequence against.

### 2026-10-05 — the drive's history since April, and what each era ran

Observations only, continuing the three entries above. No cause has been
established, and none is proposed here. Host times are Denver.

The host was cold power-cycled after job 42 and booted 2026-10-05 19:17:
`ata4` linked at 1.5 Gbps, `/dev/optical-drive-sg -> sg3`, worker-1 Ready, and
`devic.es/cdrom: 1` allocatable.

#### The libata `cmd` line names the transfer

`cmd a0/FF:00:00:LM:LH/...` is an ATAPI PACKET command. `FF` bit 0 is the DMA
flag, and `LH:LM` is the byte count the host asked for. The number after
`pio`/`dma` adds libata's 16 KiB ATAPI drain buffer to it. A length that is not
a multiple of 16 is forced to PIO.

- `00:00:00:04:00 ... pio 16388` is a **4-byte** data-in. In job 42's whole run,
  the only command with a 4-byte data-in is `READ BUFFER 3c 02 77 12 10 00 00 00
  04 00`, the one the drive hung on.
- `01:00:00:20:00 ... dma 16416` is a **32-byte** data-in. Job 42 sent five such
  commands: `READ BUFFER 3c 02 77 00 00 00 00 00 20 00`, `GET CONFIGURATION`,
  and three `READ DISC STRUCTURE`. That signature does not single one out.

This identifies commands only as far as other runs send the same command set as
job 42, which is unconfirmed until a second run is traced.

#### Reading the trace: rejected commands look like timeouts

In the `arm-sata` trace, a command rejected with sense data (CHECK CONDITION)
appears as `scsi_dispatch_cmd_timeout` about 8 ms after it starts. This isn't
a timeout. libata handles every ATAPI CHECK CONDITION by aborting into its error
handler to fetch the sense data, and that abort passes through the SCSI timeout
tracepoint. So each rejected `MODE SELECT(10)` in job 42 put the port through
libata EH just before the `READ BUFFER` that followed it. A real timeout is the
same event 30 s or more after the start.

#### Every `ata4` exception since the drive was passed through

From `journalctl _TRANSPORT=kernel --since 2026-04-01 | grep ata4` on vulcanus.
The one earlier `ata4` exception, 2024-12-23, is a disk's `FLUSH CACHE EXT`, from
before the drive was on that port.

| host time | signature | ARM job | outcome |
|---|---|---|---|
| 04-09 14:33 | `dma`, ×1 | none, before job 1 | disabled |
| 04-12 10:23 – 20:29 | `pio` ×4, `dma` ×1 (`Emask 0x10`, `SErr 0x10000`) | none; host booted 3 times that day | 3 disabled, 2 recovered |
| 04-17 15:21 | `pio` | 2, *Le Mans*, fail | disabled; job 3, same disc, succeeded |
| 04-19 22:43 | `pio`, `Emask 0x10`, `SErr 0x4890000`, link down | 6, *The Rescuers*, fail | disabled; same signature as job 41 |
| 04-21 21:33 – 21:36 | `pio` ×1, `dma` ×6 | 8, *The Rescuers*, fail | all recovered, UDMA/66 |
| 09-16 21:02 – 21:08 | `dma` ×12, every ~30.5 s | 27, *The Rescuers*, success | all recovered, UDMA/66 then UDMA/33 |
| 09-21 19:11 | `pio` | 38 | disabled |
| 09-21 23:12 | `pio` | 39 | recovered |
| 09-22 07:56 | `pio` | 40 | disabled |
| 09-22 16:54 | `pio`, `Emask 0x10`, link down | 41 | disabled, detached |
| 10-04 14:17 | `pio` | 42 | disabled |

That makes 32 exception lines: 12 `pio 16388` and 20 `dma 16416`, and nothing
else. Job 27's 12 timeouts covered both of its `makemkvcon` runs. The info scan
ended `Failed to open disc`, and the backup then ripped the disc. Its phase 3
entry is corrected above.

#### What was different in each era

The host kernel is from `journalctl --list-boots`. QEMU is the binary VM 911 was
started with, from `/var/log/apt/history.log` and the `qmstart`/`qmreboot`
tasks in `/var/log/pve/tasks/index`. MakeMKV is from the job logs, which reach
back only to 2026-08-24. A job is counted if its log shows a `makemkvcon`
start.

| era | host kernel | VM 911 QEMU | `max_sectors` | MakeMKV | jobs running `makemkvcon` | jobs with events |
|---|---|---|---|---|---|---|
| 04-09 → 05-03 | 6.17.4-2, then 6.17.13-2 | 10.1.2 | default | unknown | among 1–9 | 2, 6, 8, plus non-ARM use on 04-09 and 04-12 |
| 09-01 → 09-04 | 7.0.0-3 | 11.0.0-3 | default | 1.18.3 → 1.18.4 | 15–19, 21–23, 25, 26 (10) | none |
| 09-16 → 09-21 | 7.0.2-6 | 11.0.0-3 | default | 1.18.4 | 27–31, 33–36 (9) | 27 |
| 09-21 13:36 → | 7.0.2-6, then 7.0.14-17 from 22:07 | 11.0.3-3 | 256 | 1.18.4 | 38–42 (5) | all 5 |

- The 7.0.0-3 boot ran 05-04 → 09-15, but ARM's only `makemkvcon` use in it
  was 09-01 → 09-04. The "111 days without an event" in the phase 3 section was
  mostly idle time.
- The VM 911 restart at 09-21 13:36 was the `tofu apply` for `max_sectors=256`.
  It also picked up QEMU 11.0.3-3, installed 09-18. Job 38 is the only job on
  the new QEMU with the old kernel.
- The discs are not spread across eras. The five jobs since 09-21 used
  *Sherlock* and both *Around the World* discs, none of which ran in an earlier
  era. *The Rescuers* is the only disc seen in three eras. On 6.17.13-2, job 5
  ripped it without an event and jobs 6 and 8 had events; jobs 7 and 9 had none,
  but their logs have aged out, so whether they reached `makemkvcon` is unknown.
  Job 23 on 7.0.0-3 was clean, and job 27 on 7.0.2-6 had 12 events. It is
  intermittent within one configuration, so job 23 alone doesn't clear 7.0.0-3.
- `lpm-pol 1` (max_performance) and a 1.5 Gbps link were the same on every boot.
  Boots on 7.0 also print `Features: DIPM`, and 6.17 boots don't.
- 7.0.0-3 is still installed (`/boot/vmlinuz-7.0.0-3-pve`), but
  `proxmox-boot-tool kernel list` no longer auto-selects it.

#### For the control trace

A completed run on a disc that ripped cleanly in September (*Shrek the Third*,
*The Great Race*, *Robin Hood*, either *Sylvester and Tweety* disc) is what the
2026-10-04 entry asked for. It also splits the disc question from the
configuration question. If such a disc now hangs, the disc isn't what changed.
Trace it with the method in the 2026-10-04 entry.

### 2026-10-05 — job 43, a traced run that completed around one recovered hang

Observations only, continuing the entries above. No cause has been established,
and none is proposed here. Times are UTC.

*Meat Loaf*, label `MEAT_LOAF_DISC_1`, a DVD not ripped before, titled
*Three Bats Live* during the manual wait. It isn't the September-clean
disc the previous entry asked for. It was loaded while ARM was idle, so no job
started until the tray was cycled from the host (`eject`, then `eject -t`,
02:20:20–35, at Will's direction). Job 43 started 02:20:44. The trace used the
2026-10-04 method, running from before the tray cycle to after the final eject.
It is in `captures/arm-sata/trace-job43-full.txt` in the `disc-ripping` worktree,
and on vulcanus at `/var/tmp/arm-sata/trace-job43.txt{,.zst}`. Trace time is
printk monotonic plus about 0.3 s. The exception at monotonic 4118.79 was
02:25:06.70.

| run | command | ARM log | outcome |
|---|---|---|---|
| 922 | `info disc:9999` | 02:24:27 – 02:24:34 | `Failed to open disc` |
| 929 | `info disc:0` | 02:24:35 – 02:26:19 | hung ~1.8 s in, for 30.2 s. One hard reset, `rc=0` in 0.31 s, then `configured for UDMA/100` and `EH complete`. Direct disc access at 02:25:10, 2 titles |
| 947 | `mkv` | 02:26:19 – 02:59:10 | `LibreDrive mode (v02.1)`, `Copy complete. 2 titles saved`: 6.54 GB and 0.28 GB in 32.9 min. Zero `MSG:2003` |

That was the only `ata4` exception. ARM ejected at 02:59:10 (see D8) and HandBrake
started after.

#### Every `READ BUFFER 0x77 @0x121000` in both traces

Each `makemkvcon` run opens with `READ BUFFER 0xB0 @0x0E20` (16 bytes). Some runs
then send what is called *init* here: `MODE SELECT(10)` 0x4E0 (rejected), then
`WRITE BUFFER 0xB0 @0x0E20` (16 bytes), then `READ BUFFER 0x77` (32 bytes).
Every instance of the 4-byte `READ BUFFER 0x77 @0x121000` comes 12–24 ms
after a rejected `MODE SELECT(10)` 0x740.

| trace | run | init earlier in the run | sent just before the `MODE SELECT` 0x740 | result |
|---|---|---|---|---|
| job 42, 334230.90 | `disc:9999` | yes | `READ DISC STRUCTURE`, then 5 ms | 9.5 ms |
| job 42, 334234.57 | `disc:0` | no | `INQUIRY` (96), `READ BUFFER 0xF1` (48), then 227 ms | no completion; resets failed |
| job 43, 4084.84 | 922 `disc:9999` | yes | `READ DISC STRUCTURE`, then 6 ms | 8.9 ms |
| job 43, 4088.85 | 929 `disc:0` | no | `INQUIRY` (96), `READ BUFFER 0xF1` (48), then 237 ms | 30.2 s; reset succeeded |
| job 43, 4193.50 | 947 `mkv` | yes | `READ DISC STRUCTURE`, then 6 ms | 9.8 ms |
| job 43, 4195.67 | 947 `mkv` | yes, 2.8 s before | `READ DISC STRUCTURE`, then 6 ms | 9.8 ms |

- The two hangs are the only two instances with no init in their run, and the
  only two preceded by `INQUIRY`, `READ BUFFER 0xF1` and a ~230 ms pause. The
  last 14 commands before each hang are byte-identical between job 42 and job 43.
  That is 2 hangs among 6 instances, so it is an exact split, not an established
  condition.

  > **Broken by job 44, two entries on.** The same 14 commands, with no init,
  > completed in 9.6 ms.
- Each run without init started about 1 s after a run that had sent it, with
  no power cycle or reset between. Run 947 came after the link reset and sent
  init again.

#### Where each event since 09-21 falls in its job's `makemkvcon` runs

Run start times are from ARM's job logs. A timeout's hang began about 30.5 s
before the exception was logged. Job 41's link-down is not a timeout, so its
timestamp is the event itself.

| job | runs before the hung one since the last power cycle | hung run | hang, s into the run |
|---|---|---|---|
| 38 | none in this job; job 36's runs, ~7 h earlier | `disc:9999` | ~3.5 |
| 39 | `disc:9999`, `disc:0` (both clean) | `mkv`, 21 min after `disc:0` | ~1.5, recovered |
| 40 | job 39's, then this job's `disc:0` | `mkv` | ~1.5 |
| 41 | `disc:9999` | `disc:0` | ~1, link down |
| 42 | `disc:9999` | `disc:0` | ~1.5, traced |
| 43 | `disc:9999` | `disc:0` | ~1.8, traced, recovered |

- Every event since 09-21 sits early in a run, where that run's first
  `MODE SELECT` 0x740 / `READ BUFFER 0x77` pair falls in both traces. Only jobs 42 and 43
  confirm the command. For the rest it is timing alone.
- No run that was the first on the drive after a power cycle has hung: the
  `disc:9999` runs of jobs 39, 41, 42 and 43. The run straight after that one hung
  in 3 of 4 cases (41, 42, 43). Job 39's `disc:0` was clean, and its `mkv` run hung.
- Whether a run sends init seems to depend on the drive's state rather than the
  run type, since 947 sent it and 929 didn't. That is inferred from two
  traces, not checked against MakeMKV.

### D14 — every DVD title is transcoded twice

Found 2026-10-06 while job 43 transcoded, and verified against the job database,
job logs and `completed/`.

**Mechanism.** On the DVD path, `makemkv_mkv` registers one track per extracted
title, numbered from 0 (`makemkv.py:1055`, `source = "MakeMKV"`). Then
`handbrake_all` calls `get_track_info`, which registers the same titles again from
HandBrake's scan of the raw directory, numbered from 1 (`handbrake.py:307`,
`source = "HandBrake"`). `handbrake_all` then encodes every track in
`job.tracks` with `-t <track_number>` to `title_<track_number>.mkv`. The result is
two passes:

1. With MakeMKV's labels. Track 0 becomes `-t 0`, which is HandBrake's "scan
   all titles" value: it exits in under a second and writes nothing. Tracks
   1…N−1 then encode HandBrake titles 1…N−1. MINLENGTH is checked against the
   MakeMKV track's length, which belongs to a different title.
2. With HandBrake's labels, titles 1…N are encoded again to the same filenames.
   This overwrites pass 1's output.

Every DVD job in the database that reached transcode has equal MakeMKV and
HandBrake track counts (jobs 3, 18, 21, 22, 28, 29, 33–36, 43). Job 39 has 8
and 7. Logs for jobs 21, 28, 36, 39 and 43 all show the instant `-t 0` exit
and the second pass.

**Cost.** Each title over MINLENGTH except the last is encoded twice, and the
drive stays locked throughout (see the decision on D8). Job 43's main title
(144 min) was encoded 02:59–05:15. A second encode of that title then
truncated it at 05:15:55 and started over. Job 43 ended `success` at
07:27:57, with the main feature and the short title in `extras/`.

**Not established.** Whether the final output is ever incomplete. Pass 2 covers
every title, so it should not be. But job 39's `completed/` holds the main
feature and one extra (`title_2.mkv`, 42 MB), while seven of its titles were over
MINLENGTH. That is unexplained. Raw MKVs are kept in every case
(`DELRAWFILES: false`), so nothing is lost at source.

Blu-ray goes through `makemkv_backup`, which registers no MakeMKV tracks; every
Blu-ray job in the database has HandBrake tracks only. Single-title DVDs pay
only the `-t 0` no-op.

**Fix:** the `d14-encode` and `d14-move-series` edits in
`arm-source-patches.py` (`init-scripts.yaml`). `handbrake_all` encodes only the
tracks HandBrake's scan registered. A series moves only those tracks too, because
MakeMKV's tracks keep their raw names, and `find_matching_file` would
fuzzy-match those onto the transcodes. Movies keep their current route
through `skip_transcode_movie`. To verify on the first multi-title DVD after
deploy, the log should show no `-t 0` command, each title transcoded once, and
`completed/` holding every title over MINLENGTH.

### 2026-10-06 — job 44: the hung command sequence, sent again, completes

Observations only. Times are UTC.

*Shrek the Third* (`SHREK_THE_THIRD`), which job 28 ripped without an event in
September. It was loaded at 03:22 while job 43 was transcoding, and the disc
wrapper logged `a job already holds /dev/sr0, skipping`. The wrapper's lock
belongs to the job until it ends, transcode included, so a disc loaded during a
transcode starts nothing and needs a tray cycle afterwards. Under the D8
decision, the tray stays shut until the job ends, so this can't arise. The tray
was cycled from the host at 07:28:59, after job 43 ended at 07:27:57. There was no
power cycle after job 43's runs. The trace ran from 03:19 to after the eject:
`captures/arm-sata/trace-job44-full.txt`, and on vulcanus
`/var/tmp/arm-sata/trace-job44.txt{,.zst}`.

| run | command | ARM log | outcome |
|---|---|---|---|
| 2641 | `info disc:0` | 07:39:36 – 07:42:13 | LibreDrive and direct disc access at 07:39:42, 5 titles |
| 2677 | `mkv` | 07:42:13 – 08:05:36 | `Copy complete. 5 titles saved`, 5.7 GB in 23.4 min, zero `MSG:2003` |

There was no `disc:9999` run, as in job 40: ARM reused the stored disc number.
The host logged no `ata4` lines, and the trace has no timeouts or resets.

- Neither run sent init. The drive had last received it in job 43's run 947.
  All four `READ BUFFER 0x77 @0x121000` checks completed in 9.6–10.0 ms.
- The first check, in `disc:0` at trace time 22991.47, followed the same 14
  commands, byte for byte, as the two hangs in jobs 42 and 43: `INQUIRY`, then
  `READ BUFFER 0xF1`, then a 295 ms pause (227 and 237 ms in the hangs), then
  `MODE SELECT(10)` 0x740.
- So neither the host-side command sequence nor the absence of init decides
  whether the drive answers. Whatever differs between this check and the two
  hangs, a host SCSI trace doesn't see it.

### 2026-10-06 — the physical layer: SError, task-file status, and the neighbouring port

Observations only, from the host journal since 2026-04-01 and `smartctl`. Host
times are Denver.

- **The two PHY-level drops logged 8b/10b decode errors.** Of 33 `ata4`
  exceptions, 30 are `Emask 0x0`, `SErr 0x0`: a command timeout with a clean
  link. The other three are `Emask 0x10`. On 04-12 that was `SErr 0x10000`
  (`PHYRdyChg`). On 04-19 (job 6) it was `SErr 0x4890000` (`PHYRdyChg 10B8B
  LinkSeq DevExch`), and on 09-22 (job 41) `SErr 0x4090000` (`PHYRdyChg 10B8B
  DevExch`). `10B8B` means the host received symbols that would not decode,
  which is corruption on the wire.
- **On every timeout the drive's status was `{ DRDY }`**: ready, with neither
  BSY nor DRQ set. When libata gave up, the drive was not reporting itself
  busy with the command or waiting to transfer data.
- **`ata1`, the first port on the same sSATA controller (`00:11.4`), logged
  link-layer failures 06-07 → 09-06**, none since. These were `interface fatal
  error`, `SError: { UnrecovData Handshk }` on `WRITE FPDMA QUEUED`, then a link
  reset, on 17 days. `ata1` is `sda`, a 10 TB HGST in `rpool`. `rpool` is
  healthy, and the 2026-09-13 scrub repaired 0 B. No other port logged errors in
  that period.
- **Lifetime `UDMA_CRC_Error_Count` is not specific to this controller:**
  `sda` 31, `sdd` 49 (`00:1f.2` port 1), `sde` 1, `sdf` 1, all others 0. SMART
  doesn't date them.
- The optical drive doesn't support the SATA PHY event counter log (GP log
  0x11), so its own receive-side error counts can't be read. `sda`'s counters
  are clean since the 2026-10-05 power-on.
- Port `00:1f.2` ata-5 has no device, so the drive could move to the other
  controller without unplugging anything else.

### 2026-10-06 — the hang reproduced with no disc, no MakeMKV and no VM

A single `READ BUFFER 0x77 @0x121000` hung the drive when sent from the host:
`sg_raw -r 4 -t 30 /dev/optical-drive-sg 3c 02 77 12 10 00 00 00 04 00`. Host
times are Denver. The trace is `captures/arm-sata/trace-repro-full.txt`, and on
vulcanus `/var/tmp/arm-sata/trace-repro.txt`. It added libata's per-command
events (`ata_qc_issue`, `ata_qc_complete_*`, `ata_port_freeze/thaw`,
`ata_eh_*`).

**Conditions.** ARM was idle, and the tray had been open since job 44's eject at
02:05, so no disc was loaded. The guest's 2-second media poll was the only other
traffic. The drive had last run MakeMKV in job 44, about 70 minutes earlier, with
no power cycle since job 43. About six minutes before the test,
`smartctl -d sat -l sataphy /dev/sg3` sent ATA PASS-THROUGH commands to the drive.
It answered normally ("not supported"), but the timing wasn't recorded, and
it is a confound for this single run.

**What happened.** At trace time 50506.063 libata issued the packet command to
the drive (`ata_qc_issue`, PIO, byte count 4). Nothing came back: no completion
and no interrupt. At 50536.465 (09:18:44), 30.4 s later, it timed out with
status `{ DRDY }`, the signature of every MakeMKV hang. Four hard resets each
ended `rc=-16` with the link `slow to respond (ready=0)`, and at 09:19:44 the
drive was disabled. No `MODE SELECT`, `INQUIRY` or other command preceded it in
the same second.

So the hang needs neither MakeMKV's surrounding commands nor QEMU and the guest.
A host-originated command alone is enough, at least in the drive's state at the
time. In job 44 the same command completed four times, each 12–24 ms after
MakeMKV's rejected `MODE SELECT(10)` 0x740. Whether that preceding command
matters, and whether the drive needs to have run MakeMKV since power-on, are the
next things to test. Both need a power cycle per hang.

#### Context gathered on the way

- **7.0.0-3's clean record is weak evidence.** That boot lasted 134 days
  (2026-05-04 → 09-15), but ARM used the drive in only 4 of them (09-01 → 09-04):
  10 jobs and 17 `makemkvcon` runs. The next kernel had events in about 1 of 18
  runs on a similar mix of ordinary DVDs. At that rate, 17 clean runs happen by
  chance about 38% of the time (0.95¹⁷). The problem discs never ran on 7.0.0-3.
  So the kernel A/B test in the 2026-10-05 entry has little to start from.
- **`optical-drive-ata-monitor`** (`ansible/proxmoxer.yaml`) has captured every
  failure since 09-21 in `/var/log/optical-drive-monitor/` on vulcanus. In each
  of them the only process holding `/dev/sg3` was QEMU (`kvm`), plus `sg_raw` for
  this test. No host-side process was probing the drive during the September
  and October events.
- **`smartd` has excluded the drive since 2026-05-04** (`48ae4169`, on the
  theory that host SCSI probes caused the April failures). The quiet 7.0.0-3
  boot began the same day, and the events came back in September with the
  exclusion still in place.

The physical checks this points at are collected in
[vulcanus-onsite-checks.md](vulcanus-onsite-checks.md).

### 2026-10-08 — on a cold drive the hung command is refused, not hung

Observations only. Host times are Denver. Trace:
`/var/tmp/arm-sata/trace-baseline-cold.txt` on vulcanus.

The host was cold power-cycled after the 2026-10-06 reproduction and booted
18:49:56. At 19:04:58, 901 s after boot, the same lone
`sg_raw -r 4 -t 30 /dev/optical-drive-sg 3c 02 77 12 10 00 00 00 04 00` was sent.
Nothing MakeMKV-like had reached the drive since power-on. A disc (`SANDLOT43`)
was loaded, and the only other traffic was the guest's media polling and a
`blkid` of the disc.

The drive answered in 0.7 ms with `status { DRDY DSC ERR }`, `ILLEGAL REQUEST /
Invalid field in cdb` (ASC 0x24). There was no timeout, reset or `ata4`
kernel line.

So buffer `0x77` at `0x121000` isn't addressable on a freshly powered drive.
It becomes addressable after something in MakeMKV's start-up, most plausibly
the LibreDrive init (`MODE SELECT(10)` 0x4E0, `WRITE BUFFER 0xB0`), which
is when MakeMKV starts sending it. Every hang so far has been in that state.
The 2026-10-06 reproduction followed MakeMKV runs with no power cycle between.

Consequences:

- The disc-free reproducer needs the drive to have been through a MakeMKV run
  since power-on. It can't be primed from the host alone: the trace records
  CDBs, not data-out payloads, so the init's parameter data is unknown.
- The data-out payloads were not captured, so whether a `MODE SELECT` must
  directly precede the read (as in every MakeMKV instance) is still open.

### 2026-10-08 — job 45: a first run after power-on hangs, then the link fails mid-command

Observations only. Host times are Denver, trace times are seconds since the
18:49:56 boot. Trace: `captures/arm-sata/trace-job45-full.txt`, and on vulcanus
`/var/tmp/arm-sata/trace-job45.txt`. It holds both the 2026-10-08 cold test above
and this job.

*The Sandlot* (`SANDLOT43`), a DVD not ripped before, loaded before the power
cycle. ARM was running with the eject and D14 edits applied (verified in its log
at 01:04:43 UTC). The tray was cycled from the host at 01:10:23 UTC, and job 45
started at 01:10:45. `makemkvcon info disc:9999` started at 01:20:56 UTC, the
first `makemkvcon` since power-on. The only earlier command of its kind was the
cold test's refused `READ BUFFER 0x77 @0x121000`, 16 minutes before.

| trace time | command | result |
|---|---|---|
| 1863.544 | `MODE SELECT(10)` 0x4E0 | rejected with sense, as in every run |
| 1863.556 | `WRITE BUFFER 0xB0 @0x0E20`, 16 bytes | good, 3.7 ms |
| **1863.566** | **`READ BUFFER 0x77 @0`, 32 bytes** (`3c 02 77 00 00 00 00 00 20 00`) | **no completion** |
| 1893.615 | timeout after 30.0 s, `status { DRDY }`, `cmd ... dma 16416 in` | one hard reset, `rc=0`, link up, `configured for UDMA/100` |
| 1893.97 – 1894.02 | MakeMKV repeats the init: `READ BUFFER 0xB0`, `MODE SELECT` 0x4E0 (rejected), `WRITE BUFFER 0xB0`, `TEST UNIT READY` | good |
| **1894.022** | **`READ BUFFER 0x77 @0`, 32 bytes, again** | 17 ms later: port frozen, `Emask 0x50`, `SError { HostInt 10B8B DevExch }`, `err_mask=ATA_BUS SYSTEM` |
| 1894.06 – 1906 | hard resets | link down (`SStatus 0`) ×3, `disable device`, `detaching (SCSI 3:0:0:0)` |

MakeMKV reported `HARDWARE ERROR:INTERNAL TARGET FAILURE`. Job 45 ended `fail` at
01:21:43 UTC, and `/dev/sr0`, `/dev/optical-drive-sg` and `3:0:0:0` disappeared
from the host.

- **The 32-byte form of the command hangs too.** This is the first trace of the
  `dma 16416` signature, which accounts for 20 of the 33 earlier exception lines
  (April, and all 12 on 2026-09-16). Job 42 sent five commands with that
  transfer length, so those events were never pinned to one command. Here it is
  `READ BUFFER 0x77`, in its init form, straight after `WRITE BUFFER 0xB0`.
- **A first run after power-on hung.** That ends the 2026-10-05 observation
  that none had. The cold test 16 minutes earlier is a confound, though it was
  refused in 0.7 ms with no reset.
- **The link failed at the physical layer while that command was in flight.**
  `10B8B` means the host received symbols that would not decode, and it arrived
  17 ms after the re-sent read, with nothing else outstanding. Every PHY-level
  drop on record happened under a command with a buffer-`0x77` signature:
  2026-04-12 (`dma 16416`), 04-19 and 09-22 (`pio 16388`), and now this one,
  confirmed by the trace. A fault in a cable or connector has no reason to pick
  one command. So the link corruption looks driven by what the drive does with
  this read. That doesn't rule out a marginal link that tips over when it does.

### 2026-10-08 — job 46: the init read hangs on every attempt, then MakeMKV refuses to run

Observations only. Host times are Denver, trace times are seconds since the
19:31:16 boot. Trace: `captures/arm-sata/trace-job46-full.txt`, and on vulcanus
`/var/tmp/arm-sata/trace-job46.txt`.

*The Sandlot* again, straight after the cold power cycle that job 45 needed.
Nothing was sent to the drive from the host beyond the drive-status ioctl and
the tray cycle (01:39:43 UTC). Job 46 started at 01:40:05, and
`makemkvcon info disc:9999` at 01:50:18, the first `makemkvcon` since power-on.

- **Every LibreDrive init hung, 6 of 6.** Each attempt was the same sequence:
  `MODE SELECT(10)` 0x4E0 (rejected), `WRITE BUFFER 0xB0` (good, ~5 ms), then
  `READ BUFFER 0x77 @0`, 32 bytes, with no completion. It timed out after
  30.0–30.3 s, a single hard reset recovered the link, and MakeMKV repeated the
  init. libata stepped the drive down to UDMA/66 after the third. There was no
  link-down this time. The signature matches job 27's twelve `dma 16416`
  timeouts at ~30.5 s intervals on 2026-09-16.
- **MakeMKV then refused to run.** At 01:53:27 it reported `MSG:5021 "This
  application version is too old. Please download the latest version ... or
  enter a registration key"` and exited 253. ARM failed the job with `Error while
  running MakeMKV` and ejected. Job 45 logged the same `MSG:5021` at 01:21:43,
  after its link failure. See D15.
- **The two may be related, and this can't tell.** Tonight's runs are the only
  ones on record where every init read hung. They are also the only ones with
  MakeMKV past its version expiry. Whether an expired MakeMKV sends a different
  init payload is unknown, because the trace doesn't record data-out. Against
  that: job 27's identical loop on 2026-09-16 predates the expiry, and the
  4-byte form hung under a current MakeMKV many times.
- After the job, the drive was still on the bus with the tray open. No reproducer
  attempt was made: init never completed in this run, so the drive's LibreDrive
  state, which the reproducer needs, is unknown.

### D15 — MakeMKV 1.18.4 has expired, so nothing can be ripped

Found 2026-10-08 (jobs 45 and 46). `makemkvcon` 1.18.4 exits 253 with
`MSG:5021 "This application version is too old"` after its drive scan. ARM
fails every disc with `Error while running MakeMKV`.

- **It isn't D7.** `/root/.MakeMKV/settings.conf` exists, written by ARM's
  `prep_mkv` at job start. Its `app_Key` is the purchased `M-` key, byte-identical
  to `MAKEMKV_PERMA_KEY` in `arm.yaml`. The registered key doesn't stop the
  refusal.
- **What changed:** MakeMKV 2.0.0 was released 2026-09-20 (release notes: "Almost
  no changes, just a version bump"). Jobs 43 and 44 (2026-10-05/06) ran 1.18.4
  without the message. When exactly 1.18.4 expired is not known.
- **No published image has 2.0.0.** Upstream `arm-dependencies` moved to 2.0.0
  on 2026-10-03 (#594), but has published no image since 1.8.0 (2026-07-06). The
  `2.24.4` ARM image was re-pushed 2026-10-08 (`sha256:90aee72b…`), from the
  same revision as the running one (`sha256:b1d0d577…`, 2026-10-03), and both
  install MakeMKV from the same 2026-07-06 layer, i.e. 1.18.4. Pulling it would
  change nothing.
- The image automation can't fix this until upstream publishes a newer image.
  Nothing in the cluster reports that MakeMKV refuses to run, beyond ARM's
  per-job failure notification.

> **Cause found, and it's not the version — 2026-10-08.** The registered key
> works, but jobs never see it. udev starts `arm-disc-wrapper.sh` with no
> `HOME` (`systemd-udevd`'s environment is `PATH=/sbin:/bin` alone), and ARM's
> wrapper adds only `PATH` from `/etc/environment`. makemkvcon finds its key
> through `$HOME/.MakeMKV`. Measured in the pod with the tray empty:
> `makemkvcon -r --cache=1 info disc:9999` with `HOME=/root` lists the drive
> and doesn't refuse; the same command under `env -u HOME` returns `MSG:5021`.
> Jobs ran unregistered all along, which only mattered once 1.18.4 expired.
> The same is why D7's manual runs needed the key registered by hand.
>
> **Fix:** `export HOME=/root` in `arm-disc-wrapper.sh` before it hands over to
> ARM. With no disc loaded, makemkvcon sent no LibreDrive init and caused no
> `ata4` event, so this check is safe to repeat.

### 2026-10-09 — job 47: the first registered run, clean; the host reproducer still hangs after it

Observations only. Host times are Denver. Trace:
`captures/arm-sata/trace-job47-full.txt`, and on vulcanus
`/var/tmp/arm-sata/trace-job47.txt`.

**Job 47**, *The Sandlot*, with the D15 `HOME` fix deployed. There was no power
cycle after job 46: the drive had failed six LibreDrive inits and was still
limited to UDMA/66. The tray was closed from the host at 02:12:42 UTC.

- MakeMKV ran registered: no `MSG:5021`, and `Using LibreDrive mode (v02.1)` in
  both the `disc:0` and `mkv` runs. Since nothing before the D15 fix set `HOME`,
  **this is the first ARM job on record to run MakeMKV registered.**
- **Every buffer-`0x77` read completed:** the init-form `READ BUFFER 0x77 @0`
  once, in 8.9 ms (it had hung in 8 of 8 attempts in jobs 45 and 46, same disc,
  same drive state), and the 4-byte `@0x121000` five times, in 9.1–9.9 ms. There
  were no `ata4` exceptions for the whole job.
- It ripped 1 title, 4.03 GB in 19 min, with no read errors. Job 47 ended
  `success` at 03:58:41 UTC.
- **The D8 and D14 fixes verified end to end:** no eject after the rip (drive
  status 4 and `ejected: 0` through the transcode); one HandBrake encode,
  `title_1`, with no `-t 0` pass; and one eject, straight after `ARM processing
  complete`. `completed/` holds `The-Sandlot (1993).mkv`, 1.2 GB.

**Host reproducer after it.** At 21:59:03 Denver, with ARM idle and the tray
open after the eject, the lone
`sg_raw -r 4 -t 30 /dev/optical-drive-sg 3c 02 77 12 10 00 00 00 04 00` hung:
the exception came at 21:59:34, the resets failed, and the drive was disabled
at about 22:00. So the reproducer hangs after a registered run as it did after
an unregistered one (2026-10-06). Two of two post-MakeMKV attempts hung, and the
one cold attempt was refused.

What this does and doesn't show:

- **Every MakeMKV-originated hang on record ran unregistered**, the April and
  2026-09-16 events included, because jobs never had a `HOME`. Unregistered
  runs also completed many times (jobs 28–36, 43, 44), so registration can't be
  the whole story. One registered run is too few to call, but it is the first
  run since 2026-09-21 with no event at all on a drive that had just been
  failing.
- **The host reproducer may be a different trigger.** In every MakeMKV
  instance, the 4-byte read follows a rejected `MODE SELECT(10)` 0x740 by
  12–24 ms. The host's lone read has no such predecessor. Its 2-of-2 hang rate
  makes it a usable measure for the on-site cable and port checks. But it can't
  test the registration question, and it may not model what MakeMKV hits.
- **Registration is probably not the cause** (Will, 2026-10-09): "As far as I
  know, there is no functional difference between running it with a 'real'
  license vs a free 'beta' license. Also, there's the fact that the hang was
  triggered by a host-issued command." The host's lone read hangs the drive with
  MakeMKV not involved at all. Job 47's clean run is most likely one of the
  clean runs this drive has always had, like jobs 43 and 44. Don't build on
  registration without a reason that answers both points.
- The reproducer at 21:59:03 was sent deliberately, as the planned post-job
  attempt. The tray was open with no disc. ARM ejected at 21:58:40, the drive
  read status `2` (tray open) at 21:58:50, and the guest's `TEST UNIT READY`
  failed with sense from the eject on until then.

The `kubectl rollout restart` used to deploy D15's wrapper change was undone
seconds later. The deployment went back to its previous ReplicaSet, most likely
Flux reconciling away the `restartedAt` annotation, but that left a fresh pod
anyway, which mounted the new scripts.

### 2026-10-09 — job 48: *Around the World* disc 2 rips for the first time

Observations only. Host times are Denver. Trace: `/var/tmp/arm-sata/trace-job48.txt`
on vulcanus, running until the job ends.

*Around the World in 80 Days* disc 2, its fourth job. Jobs 40, 41 and 42 each
hung once and none got past the start of a rip. MakeMKV's output names confirm
the disc: `AROUND THE WORLD IN 80 DAYS DISC 2-*`. The host was cold
power-cycled after job 47's reproducer and booted at 22:25:16. Nothing was sent
to the drive from the host before the tray was closed at 04:41:40 UTC. Job 48
started 04:41:58 UTC.

| run | ARM log (UTC) | outcome |
|---|---|---|
| `info disc:9999` | 04:52:09 – 04:52:17 | `Failed to open disc`, as usual |
| `info disc:0` | 04:52:18 – 05:15:44 | LibreDrive and direct disc access at 04:52:43, 5 titles |
| `mkv` | 05:15:45 – 07:18:13 | `Copy complete. 5 titles saved`, 9.49 GB in 2 h 02 min, zero `MSG:2003` |

- No `ata4` exceptions for the whole rip. All six buffer-`0x77` reads (one init
  form, five 4-byte) completed in under 10 ms.
- The `disc:0` run, where jobs 41 and 42 hung, and the start of the `mkv` run,
  where job 40 hung, both passed without incident.
- The tray stayed shut into the transcode (status 4, `ejected: 0`), and
  `handbrake_all` began with `title_1`, HandBrake's numbering.

On this disc that makes 9 `makemkvcon` runs and 3 hangs: two runs each in jobs
40, 41 and 42, each job ending in a hang, then three clean in job 48. Of the 11
jobs since the 2026-09-21 restart, 44, 47 and 48 had no `ata4` event; 39 and 43
had one that recovered; 38, 40, 41, 42, 45 and 46 lost the drive.

Job 48 ended `success` at 10:57:45 UTC, and verifies D14 on a multi-title DVD.
`handbrake_all` encoded titles 1–5 once each (07:18 → 10:57), with no `-t 0`
pass and no second pass. `completed/` holds the main feature (`title_1`, 69 min)
and titles 2–5 in `extras/`. The one eject came after `ARM processing complete`.
There were no `ata4` exceptions through the end of the job.

### 2026-10-09 — research: what buffer `0x77` is, and an SDF difference between jobs

Will, 2026-10-09: "Sounds like it's most statistically likely that it's related
to the firmware / LibreDrive — let's start digging in to that." He also warned
that firmware updates often remove LibreDrive compatibility, and that this
drive was bought from Billycar11 on the MakeMKV forums because LibreDrive-capable
internal drives are now hard to get. Firmware changes stay declined.

**What LibreDrive does to the drive.** MakeMKV's library uploads "a small
software (a firmware extension) into drive volatile memory". It exposes an
alternative read interface, and the flashed firmware itself isn't changed. A
power-off removes it. The SCSI commands used to install and drive it aren't in
MakeMKV's code. They come from a per-firmware data blob, `SDF.bin`, which the
library interprets (mike admin,
https://forum.makemkv.com/forum/viewtopic.php?f=19&t=18856). That fits the hang
needing a power cycle: the code that stops answering lives in RAM.

**What the hung command is.** freemkv-unlock, an open-source reimplementation,
classifies `READ BUFFER` mode 2 buffer `0x77` (and mode 1 buffer `0x44`) as the
LibreDrive "unlock handshake" read. A genuine reply carries the marker `MMkv` at
bytes 12–16
(https://github.com/freemkv/freemkv-unlock/blob/cfe2efa667f7f7cc86580713cf86b219767e6d55/src/ld/cdb.rs).
So every traced hang is the drive's RAM extension failing to answer its own
handshake. Its changelog describes Pioneer support as "runtime H8S hooks" and
says it verifies "resident hooks before reuse". That describes its own
implementation, not a known MakeMKV bug, but it is the situation our hangs
cluster in: runs after the first since power-on.

**The drive's firmware.** A stock BDR-212 on 1.01 reports LibreDrive as
"Possible, not yet enabled"; Billycar11 sells a flash kit to enable it
(https://forum.makemkv.com/forum/viewtopic.php?t=32926). This drive reports
`1.01` and runs `LibreDrive mode (v02.1 id=DFAA920B9011)`, so it is very
likely flashed. That hasn't been checked against MakeMKV's drive-info
"Firmware type" line. Pioneer firmware can't be downgraded (same thread),
which is another reason to leave it alone.

**No matching reports found.** Nothing read describes this symptom, the
LibreDrive handshake hanging until the SATA link is lost. The search was narrow:
the main Pioneer LibreDrive thread
(https://forum.makemkv.com/forum/viewtopic.php?t=27378) has ~136 pages and only
two were read. "Makemkv hangs OS" (t=21841) is unrelated: the whole host froze,
and later with HandBrake too.

**An SDF difference between jobs, found on the way.** MakeMKV logged
`MSG:3338 "Downloading latest SDF to /root/.MakeMKV ..."` in jobs 47 and 48 only.
None of jobs 15–46 ever downloaded it. Those had no `HOME` (D15), so MakeMKV had
nowhere to keep it and presumably fell back to the definitions built into the
binary. Jobs 47 and 48 are also the only two clean jobs on a drive that had just
been failing. Both kinds of run report the same `LibreDrive mode (v02.1
id=DFAA920B9011)`, and the downloaded archive (`_private_data.tar`) doesn't
expose an SDF version, so whether the definitions for this drive actually
differ is not established. Against it: jobs 18–36 also ran without a download
and were mostly clean, and the host's lone read hung after job 47.
`/root/.MakeMKV` is container-local, so the SDF is fetched again by the first
job after each pod restart, which needs internet access.

**The SDF versions differ** (checked 2026-10-09). MakeMKV 1.18.4 ships
`sdf_000000a5.bin` (2,101,200 bytes) in `/usr/local/share/MakeMKV/appdata.tar`.
Jobs 47 and 48 downloaded `sdf_000000a6.bin` (2,119,472 bytes) into
`/root/.MakeMKV/_private_data.tar`. So jobs 15–46 ran SDF `0xA5` and jobs 47–48
ran `0xA6`. Both files are encrypted (entropy 7.999 bits/byte), so no strings
are readable. `sdftool` (a symlink to `makemkvcon`) prints only the version and
an "Identification SDF" from either file: with a dummy drive (`-n`), even with
`-t DFAA920B9011`, it reports "No drive-specific logic found". Whether this
drive's entry changed between `0xA5` and `0xA6` therefore needs the real drive
(`sdftool -d /dev/sr0 -f <file> --info`), which runs the SDF's firmware
identification against it. Copies of both files are in `captures/sdf/`.

**Against the drive, the visible parts are identical** (2026-10-09, traced).
`sdftool -d /dev/sr0 -v -f <file> --info` ran once with each version. Each sent
only identification commands: `INQUIRY`, `GET CONFIGURATION`, and `READ BUFFER`
`0xF1` and `0xB0`, as MakeMKV sends in every run. It sent no `MODE SELECT`, no
`WRITE BUFFER` and no buffer `0x77`, and caused no `ata4` event. With no disc it
is safe to repeat. The two outputs differ only in the version line. For this
drive both select a "Drive Specific SDF" reporting:

- Drive platform `RS8F01`; firmware name **`PIONEER BDR-212T`**; firmware type
  **Original (unpatched)**; version `1.01/ID83`. Autodetect string
  `pioneer:SAT :8F01:ID83: :PIONEER :BD-RW BDR-212U1.01: 20/07/21`.
- Status Enabled; DVD all regions, BD raw data read, BD raw metadata read,
  unrestricted read speed: all Yes.
- Status message `Using LibreDrive mode (v02.1 id=DFAA920B9011)`, the same in
  both.

So the drive reports `BDR-212U` but runs stock BDR-212T firmware `1.01/ID83`.
That fits a cross-flash, presumably Billycar11's, onto firmware LibreDrive
supports, rather than a patched firmware. Contrast the stock BDR-212V on
1.01/`ID06` reported as "Possible, not yet enabled".

`--info` prints only the descriptive strings, not the command program the entry
runs, so this doesn't prove the logic is unchanged. The identical `id` weakly
suggests the same drive-specific entry, if `id` identifies that entry, which is
unconfirmed. `sdftool -d /dev/sr0 -f <file> help`, which would list the entry's
commands, stalled in the tool for 120 s with no output after identifying the
drive normally, both times. The SDF lead is therefore weakened but not closed.
`captures/sdf/sdftool-info-drive-a6.txt` holds the full output.

### 2026-10-10 — job 49: *Sherlock* rips for the first time

Observations only. Trace: `/var/tmp/arm-sata/trace-job49.txt` on vulcanus,
running until the job ends.

*Sherlock Holmes in the 22nd Century* (label `Sherlock`), whose one earlier run
(job 38) lost the drive about 30 s in. There was no power cycle since the
2026-10-08 22:25 boot. Since then the drive had run job 48 (clean) and the two
`sdftool --info` identifications. The tray was closed from the host at
12:16:42 UTC. As in jobs 37 and 38, the wrapper logged `Starting ARM for Data
Disk ... udf`, and the job records `dvd`.

| run | ARM log (UTC) | outcome |
|---|---|---|
| `info disc:0` | 12:27:05 – 12:27:39 | LibreDrive and direct disc access at 12:27:12, 6 titles |
| `mkv` | 12:27:39 – 12:46:12 | `Copy complete. 6 titles saved`, 4.13 GB in 18.5 min, zero `MSG:2003` |

There was no `disc:9999` run, and no `ata4` exception.

- **Neither run sent the LibreDrive init** (`MODE SELECT` 0x4E0, `WRITE
  BUFFER 0xB0`, 32-byte `READ BUFFER 0x77 @0`). The extension was presumably
  still resident from job 48. The four 4-byte checks all completed in
  9.3–10.3 ms, each 12–25 ms after a rejected `MODE SELECT(10)` 0x740.
- **The 4-byte check read a different address:** `3c 02 77 12 20 00 00 00 04
  00`, offset `0x122000`. Every earlier traced run read `0x121000`, including
  jobs 47 and 48 on SDF `0xA6`, each of which ran the init once. Why the address
  moved isn't known. Candidates are where the extension sits after a reuse
  rather than a fresh install, or something the `sdftool` runs changed. Not
  investigated.
- **ARM identified the disc as the BBC series:** the transcode path is
  `tv/Sherlock (2010-2017)/`. That is D12's failure: the label `Sherlock` is
  ambiguous and nobody corrected it in the manual wait. It also makes this the
  first job through the `d14-move-series` edit.

Job 49 ended `success` at 14:40:29 UTC, and verifies `d14-move-series`.
`handbrake_all` encoded titles 1–6 once each (12:46 → 14:40). `move_files` was
called for HandBrake's `title_1.mkv` … `title_6.mkv` only, and none of MakeMKV's
`A1_t0x.mkv` names were matched onto them. All six are in
`completed/tv/Sherlock (2010-2017)/`, a folder named for the wrong series
(above). The one eject came after `ARM processing complete`. There were still no
`ata4` exceptions on the 2026-10-08 22:25 boot, across jobs 48 and 49 and the
`sdftool` tests.

Since the `HOME` fix, and so SDF `0xA6`, jobs 47, 48 and 49 have all run with no
`ata4` event, on the two discs with the worst record. From the 2026-09-21
restart up to the fix, 1 of 9 jobs did (44). That favours the SDF change, or
something else the `HOME` fix changed, but it doesn't establish it. `0xA5` ran
mostly clean in jobs 18–36. The `--info` entries for this drive are identical
in both versions. Job 49 never ran the init where jobs 45 and 46 hung. And the
host's lone read hung after job 47.

### 2026-10-20 — what `raw/` holds: DVDs, Blu-ray completeness, and the pairing

Prompted by Will's objective of a DRM-free duplicate of each whole disc. All reads
were from the running ARM pod, reelbarrow's read-only `/video` mount and the
host journal. The scripts and their output are under `captures/bd-completeness/`,
`captures/dvd-inventory/` and `captures/raw-pairing/`, which don't outlive the
checkout; the figures below are what the conclusions rest on.

#### DVD raw is per-title MKV

`makemkv()` (`makemkv.py:753`, 2.24.4) calls `makemkv_backup` only when
`disctype == "bluray"`, whichever of `backup` or `backup_dvd` is set; a DVD goes to
`makemkv_mkv`, which runs `mkv dev:… all --minlength=420`. Upstream `main` has the
same line, and the latest release is 2.24.4 (2026-10-03). Every one of the 72 raw
folders holding MKVs is 720×480 by `ffprobe`, so all are DVDs, and none holds a
`VIDEO_TS`.

MakeMKV can back up a DVD: forum reports have `backup` writing a decrypted ISO,
with the region set to all, and no option for a folder (t=29072, t=29877,
t=33453). Not tried here.

The transcode path for a DVD is `handbrake_all`, which runs `HandBrakeCLI -i
<raw folder> -t 0 --scan` and numbers titles from the result. HandBrake's folder
scan lists regular files only, skipping subfolders (`libhb/batch.c`, `S_ISREG`
at line 108 on `master`).

#### Blu-ray backups against their own clip info

For each of the 51 folders with `BDMV/PLAYLIST`, every `CLIPINF/*.clpi` was read
for its source-packet count (big-endian `uint32` at byte 56) and compared with its
`STREAM/*.m2ts` at 192 bytes a packet. The disc identity is reelbarrow's nav hash
(SHA-1 over `PLAYLIST/*`, `index.bdmv`, `MovieObject.bdmv`).

| backup | clips present | GB present / expected | what is missing |
|---|---|---|---|
| Alien3 Ww | 109 / 116 | 39.2 / 46.3 | 4 clips of both cuts (145, 115 min), 3 small |
| Aliens Ww | 118 / 119 | 40.3 / 49.6 | one 9.3 GB clip in both cuts (154, 137 min) |
| Despicable Me | 879 / 887 | 42.0 / 45.3 | 8 clips, 3 in the 96-min feature |
| Dune | 15 / 16 | 1.7 / 97.0 | the feature, one 95.4 GB clip (155 min) |
| Edge Of Tomorrow | 47 / 49 | 5.1 / 39.8 | the feature (29.6 GB, 113.5 min), a 42.6-min extra |
| Mummy 3Tode | 57 / 62 | 15.0 / 45.5 | the feature (29.7 GB, 111.8 min), 4 extras |
| Now You See Me | 109 / 111 | 49.5 / 49.7 | 2 clips (265 MB) in all ~130 branching playlists |
| Scott Vs World R_170830686107 | 28 / 157 | 1.0 / 49.3 | partial; complete copy `Scott Vs World R` |
| X-Men - Days Of Future Past | 531 / 537 | 23.8 / 45.9 | partial; complete copy `…_170710742835` |
| X-Men Days Of Future Past | 31 / 112 | 1.0 / 23.8 | partial, last file short; complete copy `…_170713795611` |
| The-Polar-Express | 6 / 27 | 0.4 / 14.3 | partial; complete copy `The-Adventures-of-Mary-Kate-and-Ashley--…` |

The other 40 are complete, the five *Rescuers* copies included.

*The Polar Express.* Job 25 identified the disc as *The Adventures of Mary-Kate and
Ashley* from its `LOGICAL_VOLUME_ID` label, backed it up whole to that name
(07:43–08:03 UTC, 2026-09-04), and was given the title `The-Polar-Express` by hand,
which renamed only its completed folder. Job 26 put the disc in again at 14:03,
logged its destination as `raw/Unidentified 2007 Bluray 44069507`, and died on
SIGTERM at 14:05:55. Its files, written 14:04:40–14:05:40, are in
`raw/The-Polar-Express`; how that folder got its name isn't known.

*The seven with no complete copy*, all written 2024-02-09 to 2024-04-11:

- **Not hash-check failures.** MakeMKV keeps a file that fails its hash check and
  reports `Backup done but N files failed hash check`
  ([t=37286](https://forum.makemkv.com/forum/viewtopic.php?t=37286), whose log is
  an ALIEN3_WW backup), and an admin describes the backup as the way to *get* a
  damaged clip ([t=23552](https://forum.makemkv.com/forum/viewtopic.php?t=23552)).
- **Not moved elsewhere on the share.** Every file under `/video` (50,888) was
  sized; none matches a missing clip's exact expected size, apart from one
  studio-logo clip that is identical on several discs.
- **Not explained by the drive.** In 2024 the drive was on `ata6`. Its exceptions
  near these backups are at 2024-02-04 11:02 and 17:17 MST, and 2024-04-10 22:30
  and 22:52 MDT. The second fits the 18-hour *X-Men* partial, whose last write was 16:16 MST,
  mid-file, after a five-hour gap. The April pair came 20–40 minutes before
  *Dune*'s backup started. Nothing coincides with the other backups.
- **Five were written and then removed.** In the four complete backups checked
  (*Batman Begins*, *Alien Ww*, *Coraline*, *Fifth Element*), `STREAM/`'s mtime
  equals its last `.m2ts` write. Measured from each backup's last file, it is
  later for *Mummy 3* (25 s), *Aliens* (11 min), *Edge of Tomorrow* (24 min),
  *Dune* (4 h 38 min) and the first *X-Men* (13 h 12 min). *Alien3*, *Despicable
  Me* and *Now You See Me* show no later change. In all seven, no missing clip has
  a `MAKEMKV/CMAP/*.cmap`, and every present one does.
- **One coincidence, uninterpreted.** The first *X-Men*'s `STREAM/` changed at
  11:03:18 MST on 2024-02-04, 43 s after the `dma 6144` exception at 11:02:35,
  while the second *X-Men* disc's backup was running.
- **ARM's 2024 record is gone.** The oldest job log is 2026-08-24, and the
  database starts at 2026-04-17.

#### The drive in 2024, and since 2026-10-10

The host journal holds 35 `ata6.00: cmd a0/` exceptions between 2024-01-14 and
2024-12-22: 21 `pio 16388`, 12 `dma 16416`, one `dma 6144` and one `pio 16392`.
So 33 of 35 carry the two signatures every `ata4` event since April has.

Since 2026-10-10 the journal holds no `ata4` exception. The boot is still
2026-10-08 22:25, the SDF archive still holds `sdf_000000a6.bin`, and jobs 50–72
ran in that time: with 47–49, 26 jobs in a row.

#### DVD inventory and the job↔raw pairing

The database holds 57 DVD jobs, 36 distinct `crc_id`s; job 58 (*Von Ryan's
Express*) has none. The 2024 raw folders add 35 discs by label and content. Both
lists, with the seven Blu-rays above, are `disc-rerip-list.txt`, untracked in the
main checkout.

The database holds no raw path. Each job log's `makemkv.makemkv: Processing files
to:` line does: 50 of 72 jobs on 2026-10-20, with 13 never reaching a rip and the
logs of jobs 1 and 3–10 gone. A folder's `_<12 digits>` suffix is the job's
`stage` column, which matches 23 folders.

### 2026-10-21 — phase 2c built, with D16 and D17, and proven as far as no drive allows

The previous review closed at PR #24 the same day, and this branch was cut fresh
from `main`. Job 75 (*Pinky and the Brain* D2, a DVD) was transcoding
throughout, so nothing was deployed and nothing touched the drive.

**Scope** (Will): D16's check goes in with phase 2c; a `failed hash check`
report fails the job; the manual test backup waits for job 75.

**What ARM does with backup messages.** `makemkv_backup` runs
`collections.deque(run(cmd, OutputType.MSG), maxlen=0)`, discarding every message.
`MakeMKVOutputChecker` raises only on 5003 (a title failed) and on 5004 reporting
none saved, and logs a handful of other codes. The hash-check message (`Backup
done but %1 files failed hash check`, present in the 1.18.4 binary) is not among
them. ARM's `RipperException` from inside
`makemkv()` reaches the notification as `Error while running MakeMKV`, since
`rip_visual_media` wraps it; the detail is in the job log and the stamp.

**What a DVD backup is given.** t=29674: on 1.17.2, `backup disc:/dev/sr0 ./`
into an empty folder fails with `Folder ./ already contains a backup`, whatever the
folder or filesystem; on 1.17.4, `backup disc:0 /root/makemkv/backup.iso` on a
DVD writes there (`MSG:5072 … "file:///root/makemkv/backup.iso"`, `MSG:5070
Backup done`). t=29072 has a path without a trailing separator becoming the ISO's
name. ARM's `setup_rawpath` creates the folder first, so the patch passes
`<folder>/<folder name>.iso`.

**The local harness.** `automaticrippingmachine/automatic-ripping-machine:2.24.4`
under podman, no devices, `update_key.sh` writing the month's beta key once
`/root/.MakeMKV` points at `/home/arm/.MakeMKV` (the script reads the latter;
without it MakeMKV refuses with `MSG:5021 … too old`). `backup` with an `iso:`
source fails, `Backup source must start with "disc:"`, so the backup itself can
only be tried on the drive. A synthetic DVD built with ffmpeg, dvdauthor and
genisoimage (`captures/makemkv-local/scripts/make-dvd.sh`; titles of 430, 450 and
30 s, 92,104,704 bytes, its descriptor declaring 44,973 sectors of 2048, exactly
the file):

- `info --cache=1 --noscan iso:… --minlength=420`: `TCOUNT:2`, `B1_t00.mkv` 0:07:09
  and `A2_t01.mkv` 0:07:29.
- `mkv --noscan … iso:… all <dir> --minlength=420`: `MSG:3025` skips the 30 s
  title, then `MSG:5014 Saving 2 titles`, `MSG:5005 2 titles saved`, `MSG:5036
  Copy complete. 2 titles saved.` No 5004.
- The patched `makemkv.py` (all five edits applied by the real
  `arm-source-patches.py` to 2.24.4 source fetched from the tag, matching the
  running copy but for `eject-after-processing`) imports in the image.
  `_arm_backup_dvd` with `_arm_backup` stubbed to copy the synthetic ISO gave the
  layout `Synth-whole.iso`, `arm-job.json`, `titles/{A2_t01,B1_t00}.mkv`, a stamp
  with `"complete": true`, and track rows `('MakeMKV', '0', 429, 'B1_t00.mkv',
  True)`, `('MakeMKV', '1', 449, 'A2_t01.mkv', True)`. Truncated to 40 MiB:
  `RipperException: … Synth-short.iso is 41943040 bytes of 92104704`, stamp
  `"complete": false`, no `titles/`, no track rows.

**Tests.** 32 in `tools/arm-source-patches/test_patches.py`. Six mutations each
failed at least one: the ISO and clip comparisons moved a byte, `--noscan`
dropped, the hash-check match changed, the ISO path replaced by the folder, and a
`.tmp` left behind.

**Job 75 for comparison.** `info disc:0` 13:29:42–13:30:08 UTC and `mkv
dev:/dev/sr0 all` 13:30:08–14:42:03, both with `Using LibreDrive mode (v02.1
id=DFAA920B9011)`; 7 titles found.

### 2026-10-21 — job 76: phase 2c's first DVD

Deployed as `2f7e7724`; the new pod logged all five `source-patches: applied`
lines at 19:48:35 UTC. *Pinky and the Brain* D2, ejected by job 75, was closed in
with `eject -t /dev/sr0` on vulcanus at Will's direction. Host boot still
2026-10-08 22:25, no `ata4` exception since 2026-10-20, SDF `0xA6` (re-downloaded,
`MSG:3338`, since the pod restart emptied `/root/.MakeMKV`).

| UTC | |
|---|---|
| 19:49:48 | job 76 starts; `crc_id` `7969bce42a920b30`, as job 75 |
| 19:59:57 | `info disc:9999`, ARM's drive lookup after a restart: `MSG:5010 Failed to open disc` |
| 20:00:03 | stamp written, `"complete": null`; `backup --decrypt … disc:0 …/Pinky-and-the-Brain_179261218992.iso` |
| 20:00:45 | `Using LibreDrive mode (v02.1 id=DFAA920B9011)`; `MSG:5072 Backing up disc into folder "file:///…/Pinky-and-the-Brain_179261218992.iso"` |
| 20:10:12 | `MSG:5070 Backup done`, `MSG:5081 Backup done.` |
| 20:10:13 | `Backup … checked complete`; stamp `"complete": true` |
| 20:10:13–20 | `info --noscan iso:…`: `Found 7 titles` |
| 20:10:21–11:49 | `mkv --noscan … iso:… all …/titles`: `MSG:5005 7 titles saved`; many `MSG:2008` (reads faster than the share writes) |
| 20:11:50 | HandBrake: `-i …/titles -t 0 --scan` |

- **The image.** At 2 min it was 671,088,640 bytes and growing, so MakeMKV writes
  it sequentially. Its ISO 9660 descriptor declared 3,028,667 sectors of 2048 =
  6,202,710,016 bytes, the size it finished at, and exactly the guest's
  `/sys/block/sr0/size` (12,114,668 × 512).
- **Drive time:** 10 min 10 s, from the backup's start to `Backup done`. Job 75's
  `info` and `mkv` of the same disc: 72 min 21 s. Neither ISO run logged
  `Using LibreDrive mode`; the drive's only `makemkvcon` runs were the lookup and
  the backup.
- **The titles** have job 75's names (`PINKY AND THE BRAIN VOL 3 DISC 2-B1_t00.mkv`
  …) and byte sizes, all seven. `D4_t06.mkv`'s file MD5s differ (`474a216e…`,
  `b7c06293…`); `ffmpeg -c copy -f streamhash -hash md5` gives the same four
  stream hashes for both, so only container metadata differs.
- **Scrambling.** `captures/phase2c-job76/css_check.py` reads the image's ISO 9660
  directory, walks every VOB extent and counts packs whose first video, audio or
  private-stream PES header has `PES_scrambling_control` set. Job 76's image:
  0 of 2,989,811 packs across `VTS_01_0`–`VTS_01_6` and `VTS_02_1`. The same
  script on the synthetic ISO with the bits set on 100 packs counts 100. Neither
  log shows whether the disc is CSS-protected (its `CINFO` says only `DVD disc`),
  so this establishes a clear image, not decryption.

### 2026-10-22 — the title map in the stamp

Asked by Will while assessing what reelbarrow keeps: the ISO, `titles/`, or the
transcodes. reelbarrow's design pairs transcode with raw by fingerprint
(`docs/design.md` step 3) and records it in its own `disc.json`, so it doesn't
depend on ARM for this. But once `titles/` is gone, nothing left links a
transcode to a title in the ISO:

- **Database:** job 76's HandBrake rows (1–7, 7671 s, 1299 s, …) and MakeMKV rows
  (0–6, 7649 s, 1295 s, …) have no link, and their lengths differ by 4–22 s.
- **The transcode:** `ffprobe` of `title_2.mkv` shows only `title=PINKY AND THE
  BRAIN VOL 3 DISC 2` and a creation time.
- **The job log** has it exactly (`batch: scanning …/titles/…-C1_t01.mkv` before
  `+ title 2:`), for 90 days.
- **MakeMKV's `info`** gives each title's DVD title number as TINFO 24: job 76's
  `t00`–`t06` are DVD titles 01, 04, 05, 06, 07, 08, 09.
- **HandBrake** numbers a folder by `qsort(files, …, compare_str)` over the full
  paths (`libhb/batch.c`, `hb_batch_init`, on `master`), so by file name.

Built into `_arm_backup_dvd`. In ARM's image with the synthetic DVD, MakeMKV named
the titles `B1_t00.mkv` (DVD title 1) and `A2_t01.mkv` (title 2). The stamp gave
them `handbrake_title` 2 and 1, and HandBrake's own scan of `titles/` listed
`+ title 1: …/A2_t01.mkv`, `+ title 2: …/B1_t00.mkv`.

### Job 76 stamp backfill (2026-10-22)

Job 76's stamp was written before `titles` existed in the schema. The title map
was computed from job 76's own log (TINFO lines from the ISO scan, code 24 for
disc title numbers, code 9 for duration, code 27 for filename), and the
`handbrake_title` numbers were confirmed against HandBrake's actual `-t N`
commands in the same log. The seven titles are disc titles 1, 4–9; MakeMKV
skipped titles 2 and 3 (below `MINLENGTH`). HandBrake titles 1–7 happen to
match MakeMKV's sequential `_tNN` order because the filenames share a prefix.
Written atomically via `tempfile` + `rename`.

### CSS verification: Pinky and the Brain D2 (2026-10-22, job 77's disc)

During job 77's manual-override wait, with the disc locked in the drive:

- `READ DVD STRUCTURE` format 1 (copyright info) returned `EINVAL` from both the
  kernel's `DVD_READ_STRUCT` ioctl and from libdvdcss. libdvdcss logged
  `could not check whether the disc was scrambled`. The drive (Pioneer BDR-212U)
  or the disc firmware refuses the copyright structure query.
- Direct sector reads tell the real story. 100 packs sampled across the physical
  disc at 27,500-sector intervals: **25 of 98 PES packs carry a non-zero
  scramble-control field** (CSS scrambled). The same 100 points in job 76's ISO:
  **0 of 100 scrambled**.
- The disc IS CSS-protected. MakeMKV's `backup` decrypted it. The copyright
  query's failure is not evidence of an unprotected disc; the PES headers are.

This closes the CSS verification item from phase 2c. The positive control (disc)
and negative control (ISO) both give the expected result.

### Job 77: first DVD with title map deployed (2026-10-22)

Pinky and the Brain D2 again (crc_id 7969bce42a920b30), same disc as jobs 75
and 76. This is the first job where `_arm_title_map` ran live rather than being
backfilled.

**Stamp written correctly at each stage:**
1. Before backup: `complete: null, titles: null`
2. After backup check: `complete: true, titles: null`
3. After title extraction: `complete: true, titles: [7 entries]`

**Title map matches job 76's backfill exactly** — same disc titles (1, 4–9),
same MKV filenames, same lengths in seconds, same HandBrake title numbers (1–7).
This confirms the backfill was correct.

**ISO identical in size** to job 76: 6,202,710,016 bytes. MKV sizes also match
byte-for-byte (B1_t00: 5,882,134,873; C1_t01: 996,370,850; etc.).

**CSS decryption confirmed:**
- Physical disc: 25 of 98 PES packs scrambled (sampled across the disc)
- Job 76 ISO: 0 of 100 scrambled
- Job 77 ISO: 0 of 100 scrambled
- `READ DVD STRUCTURE` format 1 returns EINVAL from both the kernel ioctl and
  libdvdcss; the drive/disc refuses the copyright query, but the PES scramble
  flags prove CSS protection.

**LibreDrive mode logged** (`MSG:1011`) on job 77's backup command. Job 76's
backup used `--noscan` and did not log it. The `--noscan` flag on the ISO runs
(info and mkv) keeps the drive out of the picture, as designed.

**MakeMKV license** works for real jobs — my earlier ad-hoc `makemkvcon info`
outside ARM's runtime failed with `MSG:5021` because it didn't load the
settings file ARM configures.

HandBrake is now transcoding title 1 of 7.

### Job 78: first Blu-ray with phase 2c (2026-10-23)

Downton Abbey: A New Era. Job 78, disctype bluray, status success.

**Blu-ray stamp coexistence confirmed.** MakeMKV wrote `BDMV/`, `CERTIFICATE/`
and `MAKEMKV/` into a folder already containing `arm-job.json`. No error, no
complaint. The risk that MakeMKV would refuse a non-empty output folder is
cleared.

**Final stamp:**
```json
{
  "schema": 1,
  "job_id": 78,
  "label": "DOWNTON_ABBEY_A_NEW_ERA",
  "disctype": "bluray",
  "crc_id": null,
  "stage": "179271627780",
  "start_time": "2026-10-23T00:44:37.859782+00:00",
  "complete": true,
  "problems": [],
  "titles": null
}
```

`crc_id: null` (Blu-rays have no pydvdid CRC), `titles: null` (title map is
DVD-only). `complete: true` with no problems — the `.clpi` completeness check
passed.

**Full pipeline:** backup → completeness check → MKV extraction → HandBrake
transcode (main feature + 2 extras) → move to `completed/movies/` → eject.

This completes the three-disc-type validation of phase 2c:
- Job 76: DVD, stamp + ISO + title map (backfilled)
- Job 77: DVD, stamp + ISO + title map (live)
- Job 78: Blu-ray, stamp + BDMV folder

### 2026-10-24 — titles that repeat other titles: surveyed, decided, built

Asked by Will: whether play-all titles only occur on TV discs, whether they
always duplicate the other titles, and how to recognise one. Job 80 (Downton
Abbey D1, the first Blu-ray series disc) was transcoding as this ran, and has
since started on its 207-minute play-all. Scripts and full outputs are in
`captures/play-all/`.

**Sources.** All 54 Blu-ray backups in `raw/` (playlists parsed from
`BDMV/PLAYLIST/*.mpls`: each play item's clip and in/out times, chapter marks);
the two phase-2c ISOs of *Pinky* D2 (`lsdvd`'s cell sector ranges); the
MakeMKV title tables in the ~45 job logs that still hold them, for the older
DVDs whose raw copy is only their long titles; durations of the older TV DVDs'
MKVs.

**Not TV alone.** TV discs with a play-all: *Sylvester and Tweety* D1/D2,
*Pinky* D1/D2, the animated *Mummy*, Downton D1. TV discs without: *Firefly*
D1–3, *Witch Hunter Robin* V1–6, *Sherlock Holmes in the 22nd Century*. Movie
discs carry them too: *Alien* Bonus D5 (four featurette play-alls), *Scott
Pilgrim* (two), the *Downton Abbey* film (a 16.9-min featurette play-all),
*Robin Hood*'s bonus disc, *X-Men: First Class*.

**Usually but not always a strict duplicate.** On *Pinky* D2, DVD title 1's 30
cells are exactly the six episodes' five content cells each; every episode
also plays one shared 73-sector closing cell the play-all omits, which is the
0.1 min the 2026-09-04 entry measured. *Scott Pilgrim* `00448` and *Alien*
`00246` hold pieces (a 175 s clip, ~7 min) that no title of 7 min or more
plays, so for those the play-all is the only transcoded copy.

**How to tell.** Length against the sum of the others works only on a simple
TV disc; it fails once a disc has extras or several play-alls. Position, chapter
counts and MakeMKV's segment map (TINFO 26, which restarts at 1 for every
title) don't work. libbluray's duplicate filters, which HandBrake uses, drop
only identical and clip-looping playlists. What works is containment of what
is played: a title that two or more others, each inside it and disjoint from
each other, cover.

**Near misses that shaped the rule.** *Dredd*'s film is four clips, three of
them long titles of their own, covering 93.5% of it: not a play-all. Alternate
cuts (*Cabin in the Woods*, *Howl's*, *Cowboy Bebop*) contain each other but
are one component, never two parts. Hence a 99% coverage threshold and the
two-disjoint-parts requirement.

**What the play-alls are**, checked rather than assumed:

- *Robin Hood*'s bonus disc (job 36, `crc_id` `d3c5ce89aef40f61`; job 35 was
  the film's disc): its 62.7-min title is `t01`+`t02`+`t03` back to back,
  90,696 of 90,698 video packets identical (`ffmpeg -f framemd5`), with chapters
  at the joins. Frames show interviews and an edit suite, a making-of in three
  parts. ARM filed it as `completed/movies/Robin-Hood (2010)_178999705941/Robin-Hood (2010).mkv`.
- *X-Men: First Class* `00500` is the film's 14 clips (`00800`, 131.7 min, the
  released 2h11m) in order with eight featurette clips from `00432` cut
  between them. It is the disc's "X Marks the Spot" mode, which "seamlessly
  branches away to behind-the-scenes videos… at appropriate moments"
  ([BigPictureBigSound](https://www.bigpicturebigsound.com/X-Men-First-Class-Blu-ray.shtml));
  frames of the inserts show a captioned interview, crew on set, a
  pre-visualisation split screen and a title-sequence motion test. The MPLS
  connection flags (5, seamless, on every join) can't tell this from an
  extended cut's branching.
- In our AV1 transcode of *Robin Hood*'s documentary, keyframes sit every
  ~6.7 s plus one exactly at each chapter (1086.185 s, 2854.390 s), so a kept
  whole can be cut into its parts without re-encoding.

**Decided** (Will, 2026-10-24), after asking whether TV alone would be safer:
every disc, since movie discs fare worse today (a play-all named as the film)
and the series label is OMDb's guess (job 63, the animated *Mummy*, came back
`movie`; job 69, *True Crime*, `series`). The in-film extras mode is not the
film: *"if it's like an interactive mode (e.g. not what the director would
have considered part of the movie), then preferable to leave it out"*, where an
extended edition would be the film. For a work in parts, *"very tempting to
keep the whole and skip the parts"*, without contradicting TV, where the
episodes are wanted. Hence: a series keeps the parts; a movie disc keeps a
whole whose parts are sequential with a chapter at each join; interleaved
parts are kept apart and the whole skipped, with a notification on a movie
disc. Plex documents no multi-part extras or sequential play
([Plex](https://support.plex.tv/articles/local-files-for-trailers-and-extras)),
so one file with chapters is the dependable way to play a work in parts
through.

**Built** as `arm-duplicate-titles.py`, appended to `handbrake.py`, with two
edits in `handbrake_all`. Run over every raw folder with a structure
(`captures/play-all/harness.py`, read-only, ARM's track list from a full
HandBrake scan as `get_track_info` makes it), the outcome:

| disc | type | skipped |
|---|---|---|
| Downton Abbey D1 | series | the 207-min play-all; main feature moved to an episode |
| *Pinky* D2, both ISOs | series | DVD title 1, 127.8 min |
| *Alien* Bonus D5 | movie | 32 featurettes, kept as three documentary wholes; `00246` and its parts transcoded |
| *Downton Abbey* film | movie | six intro variants, kept as the 16.9-min whole |
| *Scott Pilgrim* | movie | `00370`'s two halves, the whole kept; `00448` transcoded |
| *X-Men: First Class* | movie | `00500`, notified; main feature moved to `00800` |
| 47 others | | nothing |

*Pinky* D2 run as a movie keeps the 127.8-min whole and skips the six
episodes, with each episode's start recorded, which is the misfiled-series
case.

**Wrong turns in the session.**

- *X-Men*'s `00500` was first described as "the film with featurettes cut in",
  then grouped with *Robin Hood* as "extras filed as the film". Only *Robin
  Hood*'s is bonus-only.
- The first build placed each part by everything it plays. The *Downton Abbey*
  film's featurette variants share an intro clip, so both started at 0, read
  as interleaved, and sent a spurious "extended cut?" notification. Parts are
  now placed by what only they play.
- `lsdvd -c -d` was used to read a DVD's cells; it prints no title-set number
  (`vts`), only with `-v`, and the survey had used `-x`. The DVD path fell back
  to transcoding everything on both ISOs. The unit-test fixture had invented
  the key; the fake lsdvd now prints `vts` only with `-v`.
- The first edit's anchor spanned `d14-encode`'s line, marker included, so the
  patcher would have read D14 as applied wherever that text stood. Split into
  two edits that avoid it.

**Measured costs.** The comparison needs its own HandBrake scan of the
Blu-ray. A full scan of *Alien* (180 playlists) took 315 s; with
`--min-duration 420`, 22 s, the title numbering identical on all 52 Blu-rays
whose scan found titles (only HandBrake's own main-feature guess differs, on
*Alien* Bonus D5, which the code doesn't read). *Now You See Me*'s 213 long
playlists took 430 s even so, beside a transcode, and the decision 8.5 s. *Dune*
and *The Polar Express* yield no titles at all ("scan thread found 0 valid
title(s)"); both are D16's partial backups.

**Seen, not acted on.** *Now You See Me*'s 212 near-identical playlists are
alternate cuts to the rule and all transcoded; it is on the re-rip list. A
bonus disc's longest kept title is still named as the film.
