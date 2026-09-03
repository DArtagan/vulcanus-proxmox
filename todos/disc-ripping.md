# Disc ripping: from insert to catalogued media

## Opening prompt

> Automatic Ripping Machine should rip whatever disc is put in the drive and
> leave it in the right `import/` folder with metadata clean enough for the
> downstream cataloguing to work. Read `todos/disc-ripping.md` — it has the
> verified state of the pipeline, the cross-cutting defects, and a phase per
> media type. Start with **Where things stand, before touching anything**, then
> the **Progress log** at the bottom for where the last session stopped, and
> continue from there. Rips are slow and the drive is physical, so expect this
> to span sessions.

Two priorities govern every decision here, stated by Will on 2026-08-24:

1. **Data integrity.** A rip that silently loses tracks, drops audio quality, or
   reports success having produced nothing is worse than a rip that fails loudly.
2. **Self-service by Will's brother.** The server and most of the optical media
   are at his house. Anything that needs Will to open a web UI or a shell is a
   step that will not happen.

Everything below was verified on **2026-08-24/25** against ARM `2.23.2`, pod
`automatic-ripping-machine-845c9b6987-bbrj5`, unless stated otherwise.

---

## Status

| Phase | What | State |
|---|---|---|
| 0 | Unwedge the drive, close the cross-cutting defects | **done** — reconciled and verified in the container, 2026-08-25 |
| 1 | Audio CD | **done** 2026-08-31 — one loose end, see below |
| 2 | DVD — movie | **done** 2026-09-01 — first video file ARM has ever produced |
| 2b | DVD — TV series | workaround shipped 2026-09-02, **needs the disc again** |
| 3 | Blu-ray | not started |
| 4 | 4K UHD Blu-ray | not started — feasibility unproven |

Phase 0 is a prerequisite for all of the others: until it is done, the drive
wedges on the first disc and stays wedged.

---

## Where things stand, before touching anything

Check these three before starting, because two of them will mislead you:

- **Check the container, not the ConfigMap** — this has now caught something on
  every single deploy. ARM rewrites its own config file (D10); the init container
  leaves an unresolved `${...}` verbatim; and **`init-scripts` is mounted with
  `subPath`, which Kubernetes never updates in place**, so the scripts in
  `/usr/local/bin/` are frozen at pod start no matter what Flux applies. That
  last one is worse than the stale-ConfigMap case in
  [config-change-rollouts.md](config-change-rollouts.md), because there is no
  interval after which it corrects itself: **every change to
  `init-scripts.yaml` requires `kubectl rollout restart`.** Confirm with
  `kubectl exec -n automatic-ripping-machine deploy/automatic-ripping-machine -- grep MANUAL_WAIT_TIME /etc/arm/config/arm.yaml`
  and do not trust `flux get kustomizations`, which reports healthy either way —
  see [config-change-rollouts.md](config-change-rollouts.md).
- **The drive is empty and the tray is open** as of 2026-08-25 02:00 UTC. The
  Mànran CD that produced job 12 was taken out; it has to go back in to start
  phase 1.
- **The MakeMKV key registration from D7 does not survive a pod restart.**
  `/root/.MakeMKV` is in the container's ephemeral layer, not on the PVC. Re-run
  `update_key.sh` before using `makemkvcon` for anything.

## Reading the evidence

Four sources have long memories, and the previous spec concluded the evidence was
gone without consulting three of them. In descending order of usefulness:

| Source | Reaches back to | Trap |
|---|---|---|
| Proxmox host journal, `ssh root@vulcanus.forge.local` | 2023-10-22 | `journalctl -k` silently implies `-b`, so it shows only the current boot however far back `--since` reaches. Grep for `kernel:` instead. The host is `America/Denver`; ARM logs UTC. |
| ARM `notifications` table in `/root/db/arm.db` | job 1 | `Job: N was Abandoned!` prints the **PID**, not the job id, so N matches nothing in the job table. |
| File mtimes under `/root/video/{raw,transcode,completed}` | 2024 | A job's last transcode file is *partial* — it is what was being written when the process died, so its size means nothing. |
| ARM job logs in `/root/logs/` | `LOGLIFE: 90` days | Dominated by per-second `ServerUtil` lines; filter them out first. |

Prometheus retention is 30 days, so it answers nothing about April 2026.

## What this work touches

- `kubernetes/apps/automatic-ripping-machine/config-map.yaml` — `arm.yaml`,
  `abcde.conf` and `vulcanus-handbrake-preset.json` all live in this one file
- `kubernetes/apps/automatic-ripping-machine/deployment.yaml` — mounts, resources
- `kubernetes/apps/automatic-ripping-machine/init-scripts.yaml` — the udev rule
  and `arm-disc-wrapper.sh`
- `kubernetes/apps/automatic-ripping-machine/api-keys.sops.yaml` — SOPS; MakeMKV,
  OMDb, and the Pushover pair once D5 is unblocked
- `kubernetes/infrastructure/prometheus-rules.yaml` — the `disc-ripping` group
- `kubernetes/infrastructure/devices.yaml` — how the drive is advertised
- `tools/arm-disc-wrapper/` — tests for the wrapper, extracted from the ConfigMap
- `docs/automatic-ripping-machine.md` — the write-up, currently drifted (D11)

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
carried forward as D9 and still couples to
[generic-device-plugin-hang.md](generic-device-plugin-hang.md).

One claim it repeated from `docs/` is now **disproven**: see D7.

---

## How a disc travels today

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

---

## Cross-cutting defects

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
  (`kubernetes/apps/borgmatic/pushover-secret.sops.yaml`,
  `kubernetes/infrastructure/notification-secrets.sops.yaml`), so the credential and
  the pattern exist.

  **`APPRISE` is not the route to use.** ARM has native Pushover:
  `utils.py:70` adds `pover://<PO_USER_KEY>@<PO_APP_KEY>` to its apprise object
  whenever `PO_USER_KEY` is non-empty, with no apprise.yaml involved. The
  commented `PUSHOVER_USER`/`PUSHOVER_TOKEN` at the bottom of `apprise.yaml` are
  a second, redundant path — leave them alone.

  `config-map.yaml` now reads `PO_USER_KEY: "${PUSHOVER_USER_KEY}"` and
  `PO_APP_KEY: "${PUSHOVER_APP_TOKEN}"`, so **the two must be added to
  `api-keys.sops.yaml` before this is pushed**. The `config-injector` init container
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
but it recurs on every reboot that loses the race, and it **couples to
[generic-device-plugin-hang.md](generic-device-plugin-hang.md)**: if a plugin
wedge overlaps an ARM pod recreation, admission fails exactly this way. Decide
the two together.

**Failing admission is not the only outcome, and the other one is quieter.** On
2026-08-25 a deliberate ARM restart sat `Pending` for ~2m30s with no container
statuses at all, then started normally. worker-1's plugin was serving zero bytes
of HTTP and throttled at 97.8% at that moment, against 0.3% on both other nodes.
`Allocate` still answered and `devic.es/cdrom` never left `allocatable: 1` — it
just answered slowly. Nothing alerts on that, correctly, and it reads like a slow
image pull or SMB mount. Budget for it when a restart seems to hang.

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

---

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
   `PUSHOVER_USER_KEY` / `PUSHOVER_APP_TOKEN` in `api-keys.sops.yaml`. Two things
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

Two things to settle here:

- **The duplicate main feature.** Two near-identical 77-minute titles is
  ambiguous for anything downstream. Decide whether ARM should pick one.
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

## Phase 4 — 4K UHD Blu-ray

**Feasibility is unproven and this may be the phase that cannot be finished with
this hardware.** Establish that before designing anything.

The drive is a **Pioneer BDR-212U, firmware 1.01, serial ALDL017235WL**. What the
MakeMKV forums say (checked 2026-08-25):

- The BDR-212U is **not "UHD friendly" out of the box** in the LG/ASUS
  cross-flash sense, and Pioneer firmware cannot be cross-flashed from another
  brand.
- LibreDrive support for Pioneer drives does exist, and users report the 212U
  reaching `Status: Enabled`, `Firmware type: Original (unpatched)`, `BD raw data
  read: Yes`, `Unrestricted read speed: Yes`.
- The reported failure is at the *key* stage, not the read stage: "The volume key
  is unknown for this disc — video can't be decrypted" on newer AACS 2.0 titles.
  Pioneer 212 drives have mixed coverage in the public KeyDB for UHD.

So the likely outcome is that some UHD discs work and newer ones do not, for
reasons no configuration change here can fix. **First action of this phase:** put
a UHD disc in and capture the LibreDrive block from `makemkvcon` (D7 makes this
possible now). If LibreDrive is enabled and only specific discs fail on volume
keys, the honest answer is a second drive — an LG WH16NS40/WH16NS60 flashed to a
LibreDrive firmware is the standard recommendation — and that is a purchase
decision for Will, not a task.

Also note the MakeMKV binary in the container is **1.18.3**, pinned by the ARM
image. UHD support tracks MakeMKV releases closely, so the ARM image version is a
constraint on this phase in a way it is not on the others.

Sources:
[BDR-212UBK UHD thread](https://forum.makemkv.com/forum/viewtopic.php?t=21503) ·
[BDR-212U flashed, newest UHD](https://forum.makemkv.com/forum/viewtopic.php?t=40281) ·
[LibreDrive for Pioneer drives](https://forum.makemkv.com/forum/viewtopic.php?t=27378)

---

## Where the media lands, and the asymmetry that matters

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

---

## Upstream

Will is willing to run ARM off a **forked image** where a fix can only be made in
code (2026-08-25), and wants what we find carried back to
`automatic-ripping-machine/automatic-ripping-machine` rather than kept as local
workarounds. Nothing has been reported yet.

Found so far, roughly by how much they cost here. All against **2.23.2**, and
each should be re-checked against `main` before filing — some may already be
fixed.

| What | Where | Why it matters |
|---|---|---|
| `check_for_wait()` runs for **every** disc type, including music and data, and its wait is unbounded by default | `ripper/main.py:117`, `ripper/utils.py:884` | A disc nobody identifies holds the drive for `MANUAL_WAIT_TIME`. This is D1, and it is the single most damaging behaviour we found. |
| A job whose title is never set holds the drive forever, and nothing but a UI abandon frees it | `models/system_drives.py:261`, `ui/settings/DriveUtils.py:312` | `processing` tests `job_current is not None` and ignores status, so marking a job failed does not release the drive; the code that *would* release it only runs on a settings page view. |
| `duplicate_run_check()` runs before `clean_old_jobs()` | `ripper/main.py:201` vs `:232` | The guard rejects the new job before the cleanup that would have cleared the stale one. The wedge cannot self-heal. |
| A job killed without SIGTERM is later marked `fail` with no error and no stop time | `ripper/utils.py:702` | D3. `clean_old_jobs()` cannot know the cause, but it could record *that* this is what happened rather than producing a failure indistinguishable from a real one. |
| HandBrake failing on every track still yields `status: success` | `ripper/handbrake.py`, `ripper/main.py` | D4, and the reason this repo believed it had working rips for four months. Completion should test for output, not for having reached the end. |
| `rip_music()` checks only abcde's exit code | `ripper/utils.py:466` | Carries upstream's own `# TODO check output and confirm all tracks ripped`. |
| `update_key.sh` has a bash syntax error | `scripts/update_key.sh:52` | `((: > 0 : syntax error (error token is "> 0 ")`. It still writes a valid `settings.conf` and exits 0, so it is cosmetic — but it is on the path everyone hits. |
| The abandon notification prints the PID as if it were the job id | `ui/json_api.py` | `Job: 366 was Abandoned!` where 366 is a PID. Makes the notification history unreadable against the job table. |
| One insert produces two jobs, and `duplicate_run_check` does not catch it | `ripper/utils.py:758` | D2. The docstring says this is exactly what it is for, but the first job finishes failing before the second starts, so there is no overlap to detect. Gating on `ID_CDROM_MEDIA_*` is what actually fixes it — our `arm-disc-wrapper.sh` does it outside ARM, and it belongs inside. |
| `parse_udev()` lets the last matching key win | `models/job.py:170` | A `for` loop of `elif`s with no `break`, so `disctype` depends on udev's iteration order rather than on precedence. |
| The music notification names `job.label`, which is always empty for an audio CD | `ripper/utils.py:115` | The video branch two lines up uses `job.title` and appends a link to the job. For music it uses `label`, so every Pushover message reads "Found music CD: None" — while `job.title` is sitting there correctly populated. One word. |
| `job.label` is never set for an audio CD | `ripper/identify.py:65-70` | `job.get_disc_type()` — the only thing that assigns `label` for music — is inside `if mounted:`, and an audio CD has no mountable filesystem. So the field the notification reads can never be filled for the one disc type that reads it. |
| The UI offers no way to identify a music job, so its manual wait can never end early | `ui/static/js/common.js:79` | `musicCheck()` renders Title Search, Custom Title and Edit Settings only when `video_type !== "Music"`. `check_for_wait()` exits early only when `title_manual` is set, and nothing can set it. The full `MANUAL_WAIT_TIME` therefore always elapses on an audio CD, doing nothing that could be acted on. |
| ARM rewrites the operator's `arm.yaml` in place | `config/config.py:33-38` | It merges its shipped template under the user's file and writes the result back, so a key removed by the operator is silently restored and the operator's comments are replaced by `comments.json`. Hostile to anything that manages the file declaratively — a ConfigMap, Ansible, a Nix module. The merge itself is reasonable; writing it back over the source is not. |

The first three are one report, not three: they compose into the wedge.

Two of ours are **not** upstream material and should stay local — the resource
limits (D6) and the alert rules, both of which are facts about this cluster.

## Decisions already made

- **Priorities**: data integrity first, brother's self-service second
  (Will, 2026-08-24).
- **Downstream cataloguing is out of scope** unless there is a radical
  improvement available (Will, 2026-08-24).
- **Manual identification is wanted, in principle** — `588af26` (2026-04-17)
  turned it on deliberately. D1 is about bounding it, not removing it.
  **`MANUAL_WAIT_TIME` is 600 seconds** (Will, 2026-08-25): keep the deliberate
  correction window, let the drive free itself.
- **Auto-eject is on** (Will, 2026-08-25), reversing `a5d3b49` (2026-04-10),
  which disabled it as a troubleshooting measure. That troubleshooting is over
  and the tray is the only done-signal that does not need a browser.
- **Audio CD verification: happy path first** (Will, 2026-08-25). Get one CD end
  to end into beets and collect what cdparanoia actually reports, then take a
  serious look at what moving to `whipper` would cost. Do not build integrity
  tooling on top of abcde before that data exists.
- **Raw backups are never deleted** (`DELRAWFILES: false`). Treat as invariant.
- **No automatic tray movement, ever** (Will, 2026-08-25). The drive is inside an
  enclosure whose door is open only when someone is actively feeding it discs.
  An eject issued while nobody is there hits the door. This rules out the
  otherwise-obvious fix for D2b, and it also means `AUTO_EJECT` is only safe
  because a job can only exist if a human put a disc in — check that assumption
  still holds before adding any other eject.
- **A forked ARM image is acceptable** where a fix cannot be made in config
  (Will, 2026-08-25), with improvements carried back upstream. See **Upstream**.

## What was not investigated

- **What actually killed jobs 7, 8 and 9** (D3). Three candidates listed, none
  confirmed; the nightly Proxmox backup is ruled out. Prometheus retention is
  30 d so April metrics are gone. The next occurrence has to be caught live,
  which is what the phase 0 alert is for.
- **Whether the transcode preset is what Will wants.** SVT-AV1 10-bit, CRF 30,
  preset 4, Opus-only audio (see phase 3). Never reviewed against a finished
  file, because there has never been a finished file.
- **The 2 GiB of hugepages on worker-1.** Reserved by the OpenEBS kubelet patch;
  this cluster uses `openebs-hostpath`, which does not need them. That is a
  quarter of the node's RAM and it is adjacent to D6 — reported, not bundled.
- **Whether `arm.immortalkeep.com` is the right self-service surface.** It is
  LAN-only (not in [`docs/tailnet.md`](../docs/tailnet.md)), with
  `DISABLE_LOGIN: true`. Fine for someone in the house; unusable for Will
  remotely except through the cluster API.

---

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

Two things learned in the doing, both recorded above: a Secret consumed through
`envFrom` on an init container has nothing watching it, so it needs a manual
rollout; and the device-plugin wedge can *delay* admission rather than fail it,
which is a quieter third outcome than D9 described. That wedge was live on
worker-1 during the restart and was deliberately left running, so it is
available for another goroutine dump — see
[generic-device-plugin-hang.md](generic-device-plugin-hang.md).

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
