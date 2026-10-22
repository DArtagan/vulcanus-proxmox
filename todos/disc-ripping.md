# Disc ripping: from insert to catalogued media

Automatic Ripping Machine should rip whatever disc is put in the drive and leave it
in the right `import/` folder, with metadata clean enough for downstream cataloguing.
How it is built and run is in
[`docs/automatic-ripping-machine.md`](../docs/automatic-ripping-machine.md). This
spec holds what is left. [`disc-ripping.evidence.md`](disc-ripping.evidence.md)
holds the survey, every defect as first found, the finished phases, and the
progress log of the drive investigation.

Two priorities govern every decision here, stated by Will on 2026-08-24:

1. **Data integrity.** A rip that silently loses tracks, drops audio quality, or
   reports success having produced nothing is worse than a rip that fails loudly.
2. **Self-service by Will's brother.** The server and most of the optical media
   are at his house. Anything that needs Will to open a web UI or a shell is a
   step that will not happen.

Slug `disc-ripping`. Branch `disc-ripping`, worktree `.worktrees/disc-ripping`,
review base `review/disc-ripping-base`.

## Status

| Phase | What | State |
|---|---|---|
| 0 | Unwedge the drive, close the cross-cutting defects | done 2026-08-25 |
| 1 | Audio CD | done 2026-08-31; one loose end, below |
| 2 | DVD — movie | done 2026-09-01 |
| 2b | DVD — TV series | done 2026-09-04 |
| 2c | DVD — a whole-disc raw copy, each raw folder stamped with its job, every backup checked (D16) | deployed 2026-10-21; DVD ripped by job 76, Blu-ray untried |
| 3 | Blu-ray | done 2026-09-17 |
| 4 | 4K UHD Blu-ray | not started — feasibility unproven |
| — | Drive drops off its SATA link | **open, watching** — no event in jobs 47–72 |

CD, DVD and Blu-ray rip end to end, and the disc stays locked until its whole job
is done. Each phase's working is in the evidence. Since phase 2c, a DVD's raw copy
is the whole disc as a decrypted ISO, and DVDs ripped before it hold only their
long titles.

`disc-rerip-list.txt`, untracked at the root of the main checkout, is the list
handed to Will's brother: the seven Blu-rays D16 found missing part of their film,
to re-rip now, and every DVD ARM has ripped, to re-rip once phase 2c is deployed.

---

## The drive's SATA link losses: start here

Summary as of 2026-10-20. The evidence is the progress log's entries from
2026-09-22 through 2026-10-20, and each point below names its entry.

**What the failure is.** The drive stops answering one command, LibreDrive's
handshake read, `READ BUFFER` mode 2 buffer `0x77`. There are two forms: the
32-byte init read `@0`, which shows in the kernel as `dma 16416`, and a 4-byte
check read, `pio 16388`. It times out after 30 s with `status { DRDY }`. libata
resets the link, which sometimes recovers it. Otherwise the drive is disabled
or detached, and needs a cold power cycle of the host (*Recovery* in
`docs/automatic-ripping-machine.md`). Buffer `0x77` belongs to the extension
MakeMKV uploads into the drive's RAM. On a freshly powered drive it doesn't exist,
and the read is refused at once (2026-10-08 cold test). Every `ata4` event since
April has one of the two signatures (2026-10-05, 2026-10-08 job 45). **It isn't
new:** in 2024 the drive sat on `ata6`, and 33 of the 35 exceptions logged there
between 2024-01-14 and 2024-12-22 carry the same two signatures (2026-10-20). What
2024 ran — ARM, MakeMKV and SDF versions, and whether through this VM — isn't
known.

**Why it's thought to be the drive and LibreDrive, not the cable.** Hundreds of
GB of bulk reads have crossed the link with no CRC or handshake error. All four
PHY-level link-downs happened while that one command was in flight. A hung drive
ignores link resets until power is removed. And it hung with no disc and no
MakeMKV, on a lone host-sent read (2026-10-06 reproduction, 2026-10-09 job 47).
The cable isn't excluded. See [`vulcanus-onsite-checks.md`](vulcanus-onsite-checks.md).

**The drive.** It reports `BDR-212U 1.01`, and LibreDrive identifies stock
**BDR-212T firmware `1.01/ID83`**, "Original (unpatched)": a cross-flash,
bought from Billycar11 (2026-10-09 research). Firmware changes stay declined.
Updates can remove LibreDrive support, and Pioneer firmware can't be
downgraded.

**The current lead.** The D15 `HOME` fix also let MakeMKV download and keep
the latest LibreDrive definitions: SDF `0xA6`, against the built-in `0xA5`
that every earlier job ran. Jobs 47 through 72, 26 in a row, have run on `0xA6`
with no `ata4` event, all on the host boot of 2026-10-08 22:25 (checked
2026-10-20). Two of them were the discs with the worst record (*Around the
World* disc 2, *Sherlock*). From the 2026-09-21 restart until the fix, 1 of 9
jobs was clean. Against it: `0xA5` ran mostly clean in jobs 18–36; the drive's
`sdftool --info` entry is identical in both versions; and the host's lone read
hung after job 47 (2026-10-09 entries). Not established.

**Decided 2026-10-11** (Will): "Let's keep ripping normally, and when a new
problem presents then that will also be new data. I'll start a new session at
that time." No A/B test on `0xA5` for now, and no further drive testing until
something happens.

**When the next `ata4` event happens**, gather before anything resets the state:

1. The host kernel lines: `journalctl _TRANSPORT=kernel --since ... | grep ata4`
   on vulcanus, plus the snapshot in `/var/log/optical-drive-monitor/`.
2. The ARM job log (`/root/logs/<label>*.log` in the pod): which `makemkvcon`
   run hung and how far in, whether it logged `MSG:3338 Downloading latest
   SDF`, and the `Using LibreDrive mode (v… id=…)` line.
3. The SDF version that run used: `tar -tf /root/.MakeMKV/_private_data.tar |
   grep sdf_` in the pod (`sdf_000000a6.bin` today). If there's no archive, the
   built-in `0xA5` applies. **Above all: did it hang on `0xA6` or later?** If
   so, the SDF lead is dead.
4. Whether the host has been power-cycled since the previous `makemkvcon` run,
   and the boot time (`uptime -s`).
5. No SCSI trace runs between sessions, so the exact command won't be known
   unless one was started beforehand. The method is in the 2026-10-04 entry,
   *How the trace was taken, to repeat it*.

**Tests held in reserve**, each likely to cost power cycles:

- A/B: rip a problem disc on `0xA5`, with the download blocked, and on `0xA6`.
- The host reproducer (`sg_raw ... 3c 02 77 12 10 00 00 00 04 00`) works only
  after a MakeMKV run since power-on. It has hung 2 of 2 times then. It lacks
  the `MODE SELECT(10)` 0x740 MakeMKV always sends 12–25 ms before, so it may be
  a harsher trigger than ripping.
- freemkv-unlock, an open-source implementation of the same kind of unlock, as
  a diagnostic only. Its Pioneer support is self-described beta (2026-10-09
  research). Not a replacement ripper: MakeMKV derives AACS 1 keys itself and
  downloads UHD keys (`hkd_*.bin`) from its own server, while freemkv ships no
  AACS keys and needs a user-supplied `keydb.cfg`, and ARM is built around
  MakeMKV.
- The drive in another computer, the decisive drive-or-host test, at the next
  visit (`vulcanus-onsite-checks.md`).

**Unexplained, recorded only:** job 49's 4-byte checks read `0x122000` where
every earlier run read `0x121000`.

---

## Before touching anything

- **Check the container, not the ConfigMap.** ARM rewrites its own config file,
  the init container leaves an unresolved `${...}` verbatim, and **`init-scripts`
  is mounted with `subPath`, which Kubernetes never updates in place**, so the
  scripts in `/usr/local/bin/` are frozen at pod start: **every change to
  `init-scripts.yaml` needs a restart.** A secret read through `envFrom` on the
  init container needs one too. `kubectl rollout restart` is reverted by Flux
  within seconds but still leaves a fresh pod. Confirm in the running container,
  for instance `grep MANUAL_WAIT_TIME /etc/arm/config/arm.yaml`, and do not trust
  `flux get kustomizations`, which reports healthy either way.
- **After an ARM image bump**, `arm.log` should show five `source-patches:
  applied` lines (see `docs/automatic-ripping-machine.md`).
- **`sops -d` cannot run unattended here**: the age recipient is a
  passphrase-protected SSH key and sops has no tty. Editing the ARM secret is
  always Will's to do.
- **The drive's state is physical.** Ask before assuming a disc is in it. A disc
  loaded while ARM is idle starts no job until the tray is cycled, which from
  vulcanus is `eject /dev/sr0` then `eject -t /dev/sr0` — only at Will's
  direction, because of the enclosure door (see the decisions).

## Reading the evidence

Four sources have long memories. In descending order of usefulness:

| Source | Reaches back to | Trap |
|---|---|---|
| Proxmox host journal, `ssh root@vulcanus.forge.local` | 2023-10-22 | `journalctl -k` silently implies `-b`, so it shows only the current boot however far back `--since` reaches. Use `journalctl _TRANSPORT=kernel`. The host is `America/Denver`; ARM logs UTC. |
| ARM `notifications` table in `/root/db/arm.db` | job 1 | `Job: N was Abandoned!` prints the **PID**, not the job id, so N matches nothing in the job table. |
| File mtimes under `/root/video/{raw,transcode,completed}` | 2024 | A job's last transcode file is *partial* — it is what was being written when the process died, so its size means nothing. |
| ARM job logs in `/root/logs/` | `LOGLIFE: 90` days | Dominated by per-second `ServerUtil` lines; filter them out first. |

Prometheus retention is 30 days. The guest's kernel log is in no store at all:
Alloy collects pod logs only, so `talosctl -n 192.168.0.196 dmesg` holds worker-1's
current ring buffer and nothing older. Capture it while a failure is fresh.

## What this work touches

- `kubernetes/apps/automatic-ripping-machine/config-map.yaml` — `arm.yaml`,
  `abcde.conf` and `vulcanus-handbrake-preset.json`
- `kubernetes/apps/automatic-ripping-machine/deployment.yaml` — mounts, resources
- `kubernetes/apps/automatic-ripping-machine/init-scripts.yaml` — the udev rule,
  `arm-disc-wrapper.sh`, `arm-audio-handoff.sh`, `arm-title-charset.*`,
  `arm-source-patches.py` and the `arm-whole-disc.py` it appends
- `kubernetes/apps/automatic-ripping-machine/api-keys` — SOPS; MakeMKV, OMDb and
  Pushover
- `tools/arm-disc-wrapper/`, `tools/arm-audio-handoff/`, `tools/arm-title-charset/`,
  `tools/arm-source-patches/` — tests for those scripts, extracted from the
  ConfigMap
- `kubernetes/infrastructure/prometheus-rules.yaml` — the `disc-ripping` group
- `kubernetes/infrastructure/devices.yaml` — how the drive is advertised
- `ansible/proxmoxer.yaml` — the host's udev rule and `optical-drive-ata-monitor`
- `terraform/modules/proxmox_talos_vm/` — the passthrough and `max_sectors`
- `docs/automatic-ripping-machine.md` — the write-up
- reelbarrow's `todos/arm-manifest-hook.md` — the reader of D17's stamp

---

## Defects

Numbered as first found; the evidence has each one's full write-up.

| | Defect | State |
|---|---|---|
| D1 | An unidentified disc held the drive for a year | fixed — `MANUAL_WAIT_TIME: 600` |
| D2 | Every insert fired two jobs | fixed — the wrapper waits for `ID_CDROM_MEDIA_*` and holds a lock |
| D2b | A disc in the tray at pod start is never seen | **open** |
| D3 | A job killed mid-transcode records nothing | **mitigated** — cause found once |
| D4 | Success reported with no output | **open** |
| D5 | Nobody notified, disc never ejected | fixed — Pushover, `AUTO_EJECT` |
| D6 | Resource limits above the node's capacity | fixed — worker-1 at 16 GiB, ARM 4Gi/8Gi |
| D7 | `makemkvcon` believed unusable | fixed — it works; the docs say how |
| D8 | Disc ejected before the transcode | fixed — `eject-after-processing` patch |
| D9 | A node reboot leaves a `Failed` pod | documented — delete it; see the docs |
| D10 | Dead `RIPMETHOD_*` keys | removed here; ARM restores them — upstream |
| D11 | The docs no longer describe the system | fixed 2026-10-20 |
| D12 | An unlabelled disc is confidently misidentified | **open** |
| D13 | An unreadable region wedges the drive | **open** |
| D14 | Every DVD title transcoded twice | fixed — `d14-*` patches, verified jobs 48–49 |
| D15 | Jobs ran MakeMKV unregistered | fixed — the wrapper exports `HOME=/root` |
| D16 | A Blu-ray backup can be incomplete, and nothing notices | **built** with phase 2c; the 2024 cause unknown |
| D17 | A raw folder records nothing of the job that made it | **built** with phase 2c; backfill open |

**D2b.** ARM triggers only on a udev insert event, and the udev database the
container reads is empty at start; `udevadm trigger` fails on the read-only `/sys`,
and cycling the tray is ruled out by the enclosure door. What remains is to say so:
`CDROM_DRIVE_STATUS` still works, so a startup check can see a disc is loaded with no
job running for it and send a Pushover message telling whoever loaded it to cycle the
tray. Open a disc check with `O_NONBLOCK`: a blocking `open()` auto-closes an open
tray.

**D3.** `clean_old_jobs()` marks a job whose process vanished `fail`, with no error
and no stop time. Job 23's case was Talos' node-level OOM controller, fixed by
resizing worker-1; April's jobs 7–9 match its signature, unproven. `RipperRestarted`
catches the next one. What is left is upstream: record *that* a job was reaped.

**D4.** `handbrake_all()` and `rip_music()` report success without checking output;
`rip_music()` trusts abcde's exit code alone. For audio the data now exists:
`CDPARANOIAOPTS="-e"` writes cdparanoia's callbacks to the job log, and on a clean
disc only `read`, `verify`, `wrote`, `overlap` and `finished` appear (job 14's counts
are in the evidence). **Any other callback is worth failing on.** Nothing reads them
yet; acting on them needs the `BASH_SCRIPT` hook or a fork. The decision on audio
verification was happy path first, then a serious look at what moving to `whipper`
would cost. The happy path is done, so that look is next. whipper brings
AccurateRip, a stronger claim than cdparanoia's, but it is a move off ARM: the CD
path would run outside it, under a wrapper of our own, since whipper has no post-run
hook and won't get one (whipper#394).

**D12.** A Blu-ray with no `bdmt_eng.xml` falls back to its volume label, and
`identify_bluray` returns true regardless, so a placeholder like `LOGICAL_VOLUME_ID`
is fuzzy-matched on OMDb into a confident wrong title with `hasnicetitle: 1`. The
notification reads as a correct identification, so only the person holding the disc
can tell. DVDs have a CRC64 fallback that ignores labels; Blu-rays have none. Two
checks, neither subsuming the other:

- **A placeholder deny-list** before identification — `LOGICAL_VOLUME_ID`,
  `DVD_VIDEO`, bare `<digits>_VOLUME_ID` — forcing the unidentified path. Check both
  UDF fields: `blkid` reads the Logical Volume Identifier, the kernel the Volume
  Identifier, and they differ.
- **Compare the main title's runtime with the claimed film's.** A disc holding a
  100-minute feature is not a 77-minute film, whoever says otherwise — OMDb, a label,
  or a person reading a sleeve. It caught both the OMDb guess and a human's on *The
  Polar Express*.

Job 49 is a different shape of the same failure: the real label `Sherlock` matched
the BBC series, and nobody corrected it in the wait.

**D13.** Three faults from one marginal disc (*Field of Dreams*, jobs 30–31):

- **A blocked read has no timeout.** MakeMKV sat on one read for 5 h 42 min, holding
  the drive, and nothing crashed, so no alert can see it. The one visible sign
  was the output file no longer growing.
- **A failed secondary title discards a good primary one.** ARM fails the whole job,
  so the film that ripped perfectly never leaves `raw/`. The worst of the three,
  because it throws away work that succeeded.
- The third, a failure restarting itself, was never observed; see the correction in
  the evidence.

**D16.** An audit of all 51 Blu-ray backups in `raw/` on 2026-10-20 (evidence,
2026-10-20) checked every clip in `CLIPINF/` against its `.m2ts`. 40 are complete.
Four partial ones have a complete copy of the same disc beside them; *The Polar
Express*'s is job 26, killed 1½ minutes into its backup, and the complete copy is
job 25's, filed under `The-Adventures-of-Mary-Kate-and-Ashley--…`. **Seven have no
complete copy anywhere, and each lacks part of the film**, all from February–April
2024: *Alien3*, *Aliens*, *Despicable Me*, *Dune* (a UHD disc), *Edge of Tomorrow*,
*Mummy: Tomb of the Dragon Emperor* and *Now You See Me*. They are on the re-rip
list (Will, 2026-10-20).

Why those seven lost clips isn't established. Not hash-check failures: MakeMKV
keeps a file that fails its check. Not moved: no file of a missing clip's exact
size exists anywhere under `/video`. Drive hangs fit at most two. For five,
`STREAM/` changed between 25 seconds and 13 hours after the backup ended, which no
complete backup shows, so those clips were written and then removed. ARM's 2024
logs and database no longer exist. Two leads are untried: whether Will removed
`.m2ts` files by hand in early 2024, and the borg `video` repository's 2024
archives (see `backups.evidence.md`). If those cover `import/`, they could restore
the clips without a re-rip.

Whatever the 2024 cause, the gap was that a partial backup looks like any other.
**The fix, built with phase 2c:** when a backup ends, every clip's size is
checked against its `.clpi`, and a DVD's ISO against the volume size in its own
ISO 9660 descriptor. A missing or short file fails the job, and the result goes
into D17's stamp. ARM doesn't fail a job on MakeMKV's `Backup done but N files
failed hash check`: `makemkv_backup` discards every message, and that one has no
code ARM knows. The patch runs the backup itself to see it, and **a hash-check
failure fails the job too** (Will, 2026-10-21).

**D17.** ARM's database stores a job's completed path and never its raw one. The
raw folder is named from the title when the rip starts, and a title corrected
later renames only the completed folder. Only the job log, kept 90 days, holds the
pairing. Left like that, reelbarrow has to pair raw with transcode by content
fingerprint across partial copies, re-rips and misnamed folders.

**The fix, agreed 2026-10-20 and built with phase 2c:** the source patch writes
`arm-job.json` into the raw folder when it creates it, where the path is known:
job id, label, disc type, `crc_id`, `stage` and start time, with D16's
completeness result added when the backup ends. The fields and the meaning of
`complete` are in the docs, *Stage 1*. The file travels with the folder through
any rename, so reelbarrow joins on job id. Its reader is reelbarrow's
`todos/arm-manifest-hook.md`, which doesn't mention it yet.

**A DVD's stamp also maps its titles** (Will, 2026-10-22): for each title, the
DVD's own title number (MakeMKV's TINFO 24), its MKV, its length and HandBrake's
number for it, which names its transcode. Nothing else keeps that chain once
`titles/` is deleted: the database's MakeMKV and HandBrake rows aren't linked,
the transcode's tags name only the disc, and the job log goes after 90 days.
Job 76 shows why `_tNN` isn't the disc's number: `t01` is DVD title 4. Deployed
2026-10-22 (`35dfcd4e`); job 76's stamp predates it, and no DVD has run with it
yet.

**Backfill for existing jobs** is possible, from what ARM still holds:

- **A surviving job log** maps the job to its raw folder through its
  `makemkv.makemkv: Processing files to:` line. On 2026-10-20 that covered 50 of
  the 72 jobs, while 13 never reached a rip and 9 logs (jobs 1 and 3–10) are gone.
  The export is `captures/raw-pairing/job-raw-map.tsv`, which doesn't outlive this
  checkout. A logged path must be checked against the share: job 26 logged
  `Unidentified 2007 Bluray 44069507`, and its files are in `The-Polar-Express`.
- **A `_<stage>` suffix** on the folder name is the job's `stage` column, so it maps
  without a log. 23 folders carry one.
- **Neither** leaves the 2024 folders, which have no job at all, and unsuffixed
  folders whose log is gone. Those get no stamp; reelbarrow's fingerprints are all
  there is.

Logs age out 90 days after their job, so job 15's goes around 2026-11-30, and the
rest follow week by week. Run the backfill before then, or keep the mapping
somewhere durable.

## Other open items

- **Audio.** The preset's `AudioCopyMask` lists `copy:truehd` and `copy:dtshd`, but
  both `AudioList` entries use `AudioEncoder: opus`, so lossless audio is re-encoded
  to Opus. Nothing is lost — the raw backup keeps it — but the intent recorded was
  passthrough. Will's call which is wanted; the docs describe the re-encode, and
  change with the preset if passthrough is chosen.
- **Play-all titles are transcoded in full.** On a TV disc the compilation title
  passes `MINLENGTH` and costs hours of encoding for output that duplicates the
  episodes. It is recognisable: on both *Sylvester and Tweety* discs its length was
  within 0.1 min of the sum of the other titles. Needs a guard for one-episode discs
  and mixed discs before filtering on it. Not decided.
- **The transcode preset has never been reviewed** against a finished file: SVT-AV1
  10-bit, CRF 30, preset 4, Opus audio. Finished files now exist.
- **`LOGLEVEL: DEBUG`** was kept through phase 3 for detail when a rip failed, to be
  revisited once a Blu-ray completed. One has. Will's call.
- **Phase 1's loose end.** The album-name collision handling (`… (2)`) has sixteen
  unit tests and has never run on a real rip. Re-ripping one unidentifiable disc
  exercises it: expect `Unknown Artist Unknown Album (2)` beside the first, and a
  `handed off … to …(2)` line in `arm.log`. `refusing to overwrite` means the
  container runs a stale script.
- **Multi-disc movies are unproven.** Two discs holding halves of one film would each
  get a main feature picked, rather than being recognised as a pair.
- **Job 19, *An American Tail: Fievel Goes West*, failed to open** (`code: 11`) on a
  disc that mounted and identified. Job 62 ripped the same disc (`crc_id`
  `34518b467593a800`) cleanly on 2026-10-18, so the disc reads. Why job 19 failed
  is undiagnosed.
- **Job 39's `completed/` holds two titles** where seven passed `MINLENGTH`.
  Unexplained; its raw MKVs are intact.
- **Housekeeping**, after the work rather than during it: 76 empty directories in
  `/root/video/raw/`, four surplus copies of *The Rescuers*' BDMV (185 GB), D16's
  partial Blu-ray copies once their complete twins are confirmed, empty
  directories under `completed/` and `transcode/` that force the `_<stage>` suffix
  on later output, and *Sherlock* filed as `tv/Sherlock (2010-2017)/`.
- **Downstream.** Nothing watches `video/import/automatic-ripping-machine/`; that is
  [`video-library-ingest.md`](video-library-ingest.md), which also holds what the
  rips showed about TV numbering and the duplicate Blu-ray feature.

---

## Phase 2c — DVD: the whole disc, decrypted, as the raw copy

**The objective** (Will, 2026-10-20): a duplicate of the raw disc *"such that bonus
scenes, DVD games, menus, etc. could always be gone back to and re-processed"*,
preferably without the disc's DRM, *"making it more portable to work with"*.
Blu-ray meets it already. DVD doesn't: ARM backs up the whole disc only for a
Blu-ray (`makemkv.py:753` in 2.24.4, unchanged on upstream `main` on 2026-10-20),
and no `RIPMETHOD` changes that. A DVD gets `makemkvcon mkv … all
--minlength=420`, so its menus, DVD games, titles under seven minutes and the
disc's structure are never kept.

**Decided** (Will, 2026-10-20): *"decrypt ISO plus MakeMKV extraction"*, and
*"Definitely apply this going forward."* Re-ripping earlier DVDs is optional, and
they are on the re-rip list.

1. **One `makemkvcon backup --decrypt` of the disc writes a decrypted ISO.** It is
   the only read of the drive, so it adds no `makemkvcon` run against the drive
   while the SATA hang is open.
2. **MakeMKV extracts the per-title MKVs from the ISO** (`iso:` source), not the
   disc. Structure protection — decoy titles, broken IFOs — is part of the
   disc's layout, so it survives into the ISO. MakeMKV handles it there as it does on the disc; its 1.17.0 DVD engine was
   rewritten for exactly that. HandBrake reading the ISO directly wouldn't.
3. **HandBrake and everything after are unchanged.** The DVD path proven in phases
   2 and 2b, D14's patches included, runs on the input it was verified on.

The strict mirror of the Blu-ray path, HandBrake straight from the ISO, was
rejected for changing that proven path and for meeting decoy titles unguarded. The
cost of the chosen design is that the MKVs repeat what the ISO holds, about 4–8 GB
a disc.

**How it is built** (2026-10-21). `arm-whole-disc.py`, a ConfigMap key, is
appended to `makemkv.py` by `arm-source-patches.py`. One edit to `makemkv()`
calls it: after `setup_rawpath` it writes the stamp, sends a Blu-ray through the
checked backup and a DVD through `_arm_backup_dvd`. The DVD's layout is
`raw/<Title>/<Title>.iso`, `raw/<Title>/arm-job.json` and
`raw/<Title>/titles/*.mkv`, and `titles/` is what `makemkv()` returns as
HandBrake's input. HandBrake's folder scan counts every regular file at its top
level as a title and skips subfolders (`libhb/batch.c`, `S_ISREG`). MakeMKV's
track rows come from `info iso:`, not the drive, because D14's patches match on
them. Both ISO runs pass `--noscan`. The tests are
`tools/arm-source-patches/test_patches.py`.

**Established without the drive** (2026-10-21, ARM's 2.24.4 image under podman
locally, no devices, the month's beta key; scripts in
`captures/makemkv-local/` and `captures/e2e/`):

- **A DVD backup's destination is the image's file name.** On a folder, existing
  or empty, makemkvcon 1.17.x refuses with `Folder … already contains a backup`.
  Given `…/backup.iso` it writes there, reporting `Backing up disc into folder
  "file:///…/backup.iso"` then `Backup done`
  ([t=29674](https://forum.makemkv.com/forum/viewtopic.php?t=29674),
  [t=29072](https://forum.makemkv.com/forum/viewtopic.php?t=29072)). `backup`
  accepts only a `disc:` source (`Backup source must start with "disc:"`), so
  this couldn't be tried locally.
- **`info` and `mkv` work against an ISO with `--noscan`.** A synthetic DVD with
  titles of 430, 450 and 30 s gave `TCOUNT:2` and two MKVs, the short title
  skipped. A run that saves titles with none failing reports MSG 5005 (`2 titles
  saved`). ARM's zero-titles check reads only 5004, so the patch checks
  `titles/` itself.
- **End to end in ARM's image:** the patched `makemkv.py` imports, and
  `_arm_backup_dvd`, with the backup stubbed to copy the synthetic ISO, writes
  the stamp, extracts both titles and registers two MakeMKV track rows with
  `ripped=True`. With the copy truncated to 40 MiB it fails with `Synth-short.iso
  is 41943040 bytes of 92104704` and extracts nothing.
- All five edits apply once to unmodified 2.24.4 source, and a second run applies
  nothing.

**Proven on a disc** by job 76, *Pinky and the Brain* D2, the disc job 75 had
just ripped the old way (evidence, 2026-10-21):

- The ISO is written where it is told, growing as it goes rather than allocated
  in full. Its size equals its descriptor's volume size and the drive's reported
  capacity, 6,202,710,016 bytes. The stamp went from `null` to `true`.
- The drive was held 10 min for the backup, against job 75's 72 min for `info`
  and `mkv`. Neither ISO run logged `Using LibreDrive mode`.
- The seven MKVs extracted from the ISO match job 75's, extracted from the disc,
  in size. One pair checked hashes identically stream by stream; the files
  differ only in container metadata.
- No pack in any VOB of the ISO carries a PES scrambling flag (2,989,811
  packs). The counter finds 100 of 100 in a control. **Whether the disc is
  CSS-protected isn't established**, so this shows a clear image, not yet that
  scrambled sectors were decrypted. Will has approved reading the drive's
  copyright descriptor (`READ DVD STRUCTURE` format 1, 2026-10-22). Do it while
  the next DVD job holds its disc, since the tray is open between jobs and
  closing it starts one: the `DVD_READ_STRUCT` ioctl (`0x5390`, type 1) on
  `/dev/sr0` opened `O_NONBLOCK`, `cpst` at byte 2 (1 is CSS). Then run
  `css_check.py` (evidence, 2026-10-21) on that job's ISO.
- Job 76 finished `success` at 23:34:50 UTC: seven transcodes in `completed/`,
  the disc ejected at the end, and an empty `transcode/` folder left as before.

**Still unverified:**

- **A Blu-ray backup into a folder already holding `arm-job.json`.** ARM has only
  ever given MakeMKV a new, empty folder. If MakeMKV refuses one that isn't
  empty, the first Blu-ray after deploy fails at once. It would fail
  loudly, and the fix is to write the Blu-ray's stamp after its backup.
- How the backup copes with deliberately unreadable sectors.
- Whether pydvdid over the ISO reproduces the disc's `crc_id`. The 2009 ISO's
  didn't, since the image re-stamped every file, which is one reason D17 stamps
  the job rather than relying on the ISO to identify itself.

---

## Phase 4 — 4K UHD Blu-ray

**Feasibility is unproven and this may be the phase that cannot be finished with
this hardware.** Establish that before designing anything.

LibreDrive is enabled on this drive with BD raw data read and unrestricted read
speed (`sdftool --info`, 2026-10-09). What the MakeMKV forums report (checked
2026-08-25) is failure at the *key* stage instead: "The volume key is unknown for
this disc — video can't be decrypted" on newer AACS 2.0 titles, with mixed public
KeyDB coverage for Pioneer 212 drives. So the likely outcome is that some UHD discs
work and newer ones do not, for reasons no configuration here can fix.

**First action:** put a UHD disc in and see whether it decrypts. If only specific
discs fail on volume keys, the honest answer is a second drive — an LG
WH16NS40/WH16NS60 flashed to a LibreDrive firmware is the standard recommendation —
and that is a purchase decision for Will, not a task.

**Memory.** SVT-AV1's memory scales with targeted cores, resolution, bit depth and
lookahead; its published figure for 4K 10-bit is 14 GB at 8 vCPU. 1080p measured
over 3 GiB here, which extrapolates to roughly 12–16 GiB for 4K against worker-1's
13.1 GiB allocatable. Two levers cost no RAM, and the phase should choose rather than
inherit the 1080p preset: **cap `--lp`** through `VideoOptionExtra`, paid for in
speed; or **don't transcode UHD at all.** CRF 30 AV1 discards most of what makes a
UHD disc worth having, and HDR10+/Dolby Vision metadata often doesn't survive
HandBrake, where a remux keeps the stream intact.

UHD support tracks MakeMKV releases closely, and MakeMKV comes from the ARM image —
1.18.4 in every published image as of 2026-10-08, while 2.0.0 is out. That makes the
image a constraint on this phase in a way it is not on the others.

Sources:
[BDR-212UBK UHD thread](https://forum.makemkv.com/forum/viewtopic.php?t=21503) ·
[BDR-212U flashed, newest UHD](https://forum.makemkv.com/forum/viewtopic.php?t=40281) ·
[LibreDrive for Pioneer drives](https://forum.makemkv.com/forum/viewtopic.php?t=27378)

---

## Upstream

Will is willing to run ARM off a **forked image** where a fix can only be made in
code (2026-08-25), and wants what we find carried back to
`automatic-ripping-machine/automatic-ripping-machine` rather than kept as local
workarounds. **Nothing has been reported yet.**

The first block was found against **2.23.2**, the last against **2.24.x**. Re-check
each against `main` before filing — some may already be fixed.

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
| `identify_bluray` returns true when it has only guessed from the label | `ripper/identify.py:106` | D12. The caller cannot tell disc metadata from a label guess, so a placeholder label gets `hasnicetitle`. Upstream marks the spot `# Maybe call OMdb with label…`. On the success path, the year comes from `bdmt_eng.xml`'s mtime (`:130`), the authoring date, which is wrong for every re-issue. |
| One unreadable title fails the whole job | not yet located | D13. The titles that ripped are never transcoded. |
| The disc is ejected after the rip, not after processing | `ripper/makemkv.py:760` | D8. `makemkv()` calls `job.eject()` before the transcode starts, so the open tray stops meaning "done". |
| Every multi-title DVD is transcoded twice | `ripper/makemkv.py:1055`, `ripper/handbrake.py:307` | D14. MakeMKV's and HandBrake's tracks are both registered and both encoded, and MakeMKV's track 0 becomes HandBrake's "scan all titles" `-t 0`. |
| A job started by udev has no `HOME` | `scripts/docker/docker_arm_wrapper.sh` | D15. MakeMKV then runs unregistered, on its built-in drive definitions, and an expired MakeMKV refuses every disc. |

The first three are one report, not three: they compose into the wedge.

Two of ours are **not** upstream material and should stay local — the resource
limits (D6) and the alert rules, both of which are facts about this cluster.

## Decisions already made

- **Priorities**: data integrity first, brother's self-service second
  (Will, 2026-08-24).
- **Downstream cataloguing is out of scope** unless there is a radical
  improvement available (Will, 2026-08-24).
- **Manual identification is wanted, in principle** — `588af26` (2026-04-17)
  turned it on deliberately. **`MANUAL_WAIT_TIME` is 600 seconds** (Will,
  2026-08-25): keep the deliberate correction window, let the drive free itself. A
  title set any time before post-processing still renames the output, so the real
  correction window runs to the end of the transcode.
- **Auto-eject is on** (Will, 2026-08-25), reversing `a5d3b49`. The tray is the
  only done-signal that does not need a browser.
- **Audio CD verification: happy path first** (Will, 2026-08-25), then a serious
  look at `whipper`. Do not build integrity tooling on abcde before that.
- **Do not fork for the audio CD's dead wait** (Will, 2026-08-26): "That time cost
  is not a worry to us in the day-to-day." Something else would have to justify a
  fork.
- **ARM's write access stays at `import/`** (Will, 2026-08-26). abcde stages
  container-local rather than mounting the whole audio share, because upstream ARM
  has a history of surprising permission and directory behaviour.
- **Raw backups are never deleted** (`DELRAWFILES: false`). They are the archival
  copy and the integrity anchor. Treat as invariant. For a DVD that anchor is
  only the long titles until phase 2c lands, and for a Blu-ray only as good as
  D16's check.
- **The raw copy is the whole disc, decrypted** (Will, 2026-10-20): bonus scenes,
  DVD games and menus must stay re-processable. For DVD, *"decrypt ISO plus
  MakeMKV extraction"*, applied going forward; see phase 2c.
- **Audio CDs are out of scope for the whole-disc copy** (Will, 2026-10-20).
- **D16's check is built with phase 2c** (Will, 2026-10-21), so the stamp's
  `complete` is filled from the first job.
- **A hash-check failure fails the job** like a missing or short clip (Will,
  2026-10-21), though the clips are there at full size.
- **The manual test backup waits until job 75 is done** (Will, 2026-10-21), so a
  hang costs no transcode in progress.
- **D16 starts from exploration** (Will, 2026-10-20): *"Given the circumstances,
  I would have expected most of these (especially the Polar Express) to be
  complete. So we really need to dig into the nature of these missing streams."*
  The seven Blu-rays with no complete copy go on the re-rip list.
- **Raw is paired with its job by a stamp ARM writes** (Will, 2026-10-20), D17's
  `arm-job.json`, not by fingerprint alone.
- **No automatic tray movement, ever** (Will, 2026-08-25). The drive is inside an
  enclosure whose door is open only when someone is actively feeding it discs.
  An eject issued while nobody is there hits the door. `AUTO_EJECT` is only safe
  because a job can only exist if a human put a disc in — check that assumption
  still holds before adding any other eject.
- **A forked ARM image is acceptable** where a fix cannot be made in config
  (Will, 2026-08-25), with improvements carried back upstream.
- **The disc stays in the drive, locked, until its processing is fully
  complete** (Will, 2026-10-05): "The design intent is that it should be that
  the drive remains locked during transcode, and then ejects after processing of
  the disc is fully complete."
- **Declined for the drive's SATA link losses** (Will, before 2026-10-05; first
  written down 2026-10-06): turning off MakeMKV's LibreDrive, reducing
  `makemkvcon` runs per job, making VM 911 boot without the drive, and drive
  firmware changes. "No, I don't think any of these avenues are ones we'd like to
  pursue." Ask before proposing any of them again.
- **Registration is probably not the cause of the hangs** (Will, 2026-10-09): "As
  far as I know, there is no functional difference between running it with a
  'real' license vs a free 'beta' license. Also, there's the fact that the hang was
  triggered by a host-issued command." Don't build on registration without a
  reason that answers both points.
- **Keep ripping normally until the next event** (Will, 2026-10-11) — see *start
  here*.

## Wrong turns, recorded so they are not repeated

The full set is in the evidence, marked where it was corrected. These are the ones
the remaining work could repeat.

- **A limit sized against the VM, not allocatable.** ARM's 4Gi limit read as
  generous on an 8 GiB VM with 5.26 GiB allocatable and the rest resident, so the
  node died first. Size against `.status.allocatable` minus what is already there.
- **Deleting a key from the ConfigMap was taken to remove it.** ARM merges its
  template back and rewrites the file. Check the running container.
- **Reading the code instead of the log.** D8 was written from the source as "the
  disc stays locked through the transcode"; the running image ejected after the rip.
  Job 27 was recorded as "without incident" with 12 `ata4` timeouts in the host log.
  Check the job log and the host journal before calling a rip anything.
- **`Invalid field in cdb` was twice called CSS protection and normal.** It was 384
  KiB reads over the host's 128 KiB limit. A protection refusal is ASC `0x6F`.
- **"111 days without an event" was mostly idle.** The drive was used on 4 of them.
  Count runs, not days, and ask how likely the clean streak was by chance.
- **A probe without a control read as proof.** A single beets-flask probe "created
  no session" for an unrelated reason; the matched pair showed the dotted directory
  did not help.
- **A flag's meaning taken from `--help`.** cdparanoia's `-e` was described as the
  smiley progress bar; that is `-E`.
- **Job 32 was abandoned as a presumed retry** without checking its title. It was a
  different disc. Check a job's label before abandoning it.
- **Positional episode mapping.** `title_N` is not episode N: the play-all sits among
  the episodes, and every disc of a set restarts at `title_0`. Only content matching
  can file TV.
- **A DVD's raw copy was believed to be its whole `VIDEO_TS`**, from the setting's
  name, in the docs and in this spec's phase 2 notes, through four phases. Nobody
  listed a DVD's raw folder. Look at an artefact before calling it the archive.
- **The wrong HandBrake function was read.** `handbrake.py:236`, which feeds every
  file in a folder to HandBrake, is `handbrake_mkv`, reached only when `RIPMETHOD`
  is exactly `mkv`. Our DVDs go through `handbrake_all`, which scans the folder
  instead. Follow `start_transcode` to the branch actually taken.

---

## Prompt to open with

> Read `todos/disc-ripping.md` in full. ARM rips CD, DVD and Blu-ray end to end;
> what is left is proving phase 2c on a disc (a whole-disc raw copy for DVD, D17's
> job stamp and D16's completeness check, all built, see its *Unverified* list),
> D17's backfill, the drive's intermittent SATA link loss, the other open defects,
> and 4K.
> Work is on the `disc-ripping` branch in `.worktrees/disc-ripping`, with its review
> at `review/disc-ripping-base`. If a new `ata4` event is why you are here, start at
> **The drive's SATA link losses: start here** and gather its list before anything
> resets the drive. Rips are slow and the drive is physical, so expect this to span
> sessions. Open `todos/disc-ripping.evidence.md` only to re-examine a conclusion,
> and add your session's log entry at its end. `wt deploy` and the other `wt`
> aliases push and must be run by the user.
