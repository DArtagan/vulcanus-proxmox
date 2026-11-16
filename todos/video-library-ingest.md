# Video library ingest: deploying reelbarrow, and ARM's half of the handoff

## Opening prompt

> Ripped discs stop in `media/video/import/automatic-ripping-machine/` and never
> reach the library Plex reads. The service that files them is **reelbarrow**,
> in its own repository (<https://github.com/DArtagan/reelbarrow>, checked out
> at `~/repositories/reelbarrow`). Its `docs/design.md` is the design, and its
> `todos/` holds the application work, one spec per step. This spec is the
> vulcanus-proxmox half: ARM's manifest exporter, reelbarrow's deployment, its
> secrets and its alerts. Read `todos/video-library-ingest.md`, then
> reelbarrow's `todos/README.md`, whose first spec is the next step.

The design was verified 2026-10-03 to 10-08 and moved to reelbarrow on
2026-10-18. The evidence behind every decision, including the experiments table,
is there now.

---

## The steps

reelbarrow's `todos/README.md` orders the work, and a spec still in its
`todos/` is not done. Most steps are application work there alone. These also
need work here:

- **arm-manifest-export**: the exporter's sidecar in ARM's pod, under *ARM's
  manifest exporter*.
- **subtitle-flags**: ARM's HandBrake arguments, under *ARM's subtitle
  flags*.
- **first-filing**: the switch, under *The switch to live filing*.
- **metrics**: the alert rules, under *Alerting*.
- **Each step that calls an outside service** adds its key to reelbarrow's
  secret; *Secrets, as each step needs them* says which.

---

## Decisions already made

The application's decisions are recorded verbatim in reelbarrow's design,
under *Decisions already made*. These bind this half:

- **Our own tool, in a new repository, as a separate service with a small web
  UI** (Will, 2026-10-04), named `reelbarrow` (2026-10-05).
- **The design lives in reelbarrow** (2026-10-18). Will chose to move it there,
  beside the code, over keeping it here, so this spec carries only the
  deployment half.
- **Public repository, MIT licence, semver releases** (2026-10-18).
  release-please cuts releases. The image is
  `ghcr.io/dartagan/reelbarrow:X.Y.Z`, built from the release tag, and the
  `ImagePolicy` here takes `>=0.1.0 <1.0.0`. A merge in reelbarrow deploys
  nothing until its release pull request is merged. This was chosen over the
  timestamp-per-push scheme trello-randomizer uses, because this tool moves and
  deletes media.
- **Dry run first** (2026-10-18): *"Initially we won't want to make actual
  changes, just do dry-runs and collect data - might be worth an flag."*
  `REELBARROW_DRY_RUN` defaults to true in the code. The deployment sets it
  explicitly and also mounts the share read-only, so a bug in the flag's handling
  meets `EROFS` rather than the library.

---

## reelbarrow's deployment

`kubernetes/apps/reelbarrow/`, in the `apps` namespace beside filebot and Plex:

- **The video share** through `video-pvc`, at `/video`, **read-only**. One
  mount covers both `import/` and the libraries, which filing needs, because
  filing is a `rename()` within one filesystem.
- **A 5 Gi `openebs-hostpath` claim** at `/data`, for SQLite (job state and
  the inbox's plans). It gets K8up's default volume backup. The library's
  history isn't on it: each filing's record is written into the library,
  beside the disc's `disc.json`, on the snapshotted and replicated share, and
  reelbarrow keeps no ledger (Will, 2026-11-11, in reelbarrow's design), so
  `first-filing` needs no dump. Whether the inbox's database needs one, as
  `docs/kubernetes.md` asks of a database written continuously, is
  reelbarrow's `plan-inbox` to decide.
- **The internal ingress** at `reelbarrow.immortalkeep.com`, LAN-only like
  `arm.immortalkeep.com`. The `*.immortalkeep.com` wildcard in CoreDNS already
  resolves it.
- A readiness probe on `/healthz`. No liveness probe, because analysis is long,
  synchronous work (see `docs/kubernetes.md`, *Probes*).

### Secrets, as each step needs them

One SOPS secret, `kubernetes/apps/reelbarrow/secrets.sops.yaml`, grown step by
step. The Deployment takes it whole with `envFrom`, so each key is named as
reelbarrow reads it.

| key | first needed by |
|---|---|
| `REELBARROW_TMDB_TOKEN`, TMDB's API Read Access Token (free personal account) | `single-manifest-plan` |
| `REELBARROW_FANART_KEY`, fanart.tv's personal API key (free account). `reelbarrow file` refuses to run without it, dry run included | `executor` |
| `REELBARROW_PLEX_TOKEN`, with `REELBARROW_PLEX_URL` and `REELBARROW_PLEX_PATHS` set in the Deployment. Only a live run reads them; they were added with the fanart.tv key (Will, 2026-11-11, on reelbarrow's PR #22: *"let's add these plex keys to the deployment repo too"*) | `executor` |
| Pushover app token | `review-ui`. ARM's pair is in `automatic-ripping-machine/api-keys.sops.yaml`; a separate token keeps the messages distinguishable |
| Anthropic API key, pay as you go, with a spend cap set in the Console | `episode-matching` |

### The switch to live filing

Done in reelbarrow's `first-filing`, and only after its preconditions pass. In
one commit, set `REELBARROW_DRY_RUN` to `"false"` and drop `readOnly` from the
video mount. One precondition belongs here: `video-pv` mounts with options
`rw` and nothing else (verified 2026-10-05). It sets no `uid`, `gid`,
`file_mode` or `dir_mode`, so whether the image's uid 1000 may write to the
share is unknown until tested.

---

## ARM's manifest exporter

The one interface between ARM and reelbarrow. **What writes the manifests
ships in reelbarrow** (decided 2026-10-19), so every user runs the same one:
`reelbarrow arm-export-manifests`, which reads `arm.db` read-only every 30 s and
writes one manifest per job, plus `exporter.json` for liveness. reelbarrow's
`docs/exporter.md` is its use, *The manifest* in its design the contract, and
its `todos/arm-manifest-export.md` the checks that need it running. It
replaced a `BASH_SCRIPT` hook (2026-11-08) because ARM sets a job's final
status after its last notification, so no hook ever sees `success` or `fail`.
`BASH_SCRIPT` stays `arm-audio-handoff.sh`.

A manifest carries the job's raw copy whole, so the patches record it in ARM's
database rather than as `arm-job.json` in the raw folder (Will, 2026-11-08,
in reelbarrow's arm-manifest-export: the stamp and the manifest condensed into
one). Done, each with Will's go-ahead:

- **The patch** (`arm-raw-copy.py` and the changed whole-disc and
  duplicate-titles code) deployed 2026-11-09 08:21 UTC with the previous
  review: ARM's pod logged `applied raw-copy-code` with the other ten edits,
  and the patched modules compile under its Python 3.10.
- **The migration** (`tools/arm-stamps/raw_copies.py --write`) ran 2026-11-09
  with ARM idle: 32 rows, jobs 7, 8, 23, 25, 27 and 76–103, matching its dry
  run byte for byte. `PRAGMA integrity_check` read `ok` after, journal mode
  still `delete`, and a second dry run found nothing to write.
- **reelbarrow 0.3.0**, the first release with `arm-export-manifests`,
  released 2026-11-09.

- **The sidecar**, `manifest-exporter` in ARM's pod, deployed 2026-11-09
  (review #37) and described in `docs/automatic-ripping-machine.md`
  (*Manifests for reelbarrow*). The kubelet created the manifests folder. Its
  first deploy crash-looped, as reelbarrow's image has CMD and no ENTRYPOINT,
  so `args` alone replaced the command; it has `command: [reelbarrow]`. The
  image automation moved it and reelbarrow's service to 0.3.1 in one commit,
  as the shared `apps:reelbarrow` marker meant it to.

On this branch, the sidecar's second `--arm-path`, for
`/root/media`, where ARM mounted its folder of the share until 2026-04-20:
its first pass skipped jobs 1–3, 5 and 6, whose paths are recorded there.
reelbarrow 0.3.1 takes the option more than once; 0.3.0 kept only the last.
Merging it rolls ARM's pod: merge while ARM is idle.

What is left is checking it on the cluster and a real disc, in reelbarrow's
`todos/arm-manifest-export.md` (*Done when*).

---

## ARM's subtitle flags

reelbarrow's `subtitle-flags` spec. Every DVD transcode ARM has made flags
its full subtitle track **forced**, so a player that obeys the flag shows the
whole film's subtitles to a viewer who turned them off. MakeMKV flags the
first subtitle track default; HandBrake keeps that, and its Matroska muxer
flags every default subtitle forced as well (HandBrake `7e6a28f65`, 2015,
removed by its PR #8187 on 2026-10-20, in no release yet; ARM's image runs
1.11.2). reelbarrow clears the flag as it files, and *Subtitle flags* in its
design has the rule and the evidence (its E42 and E44). This stops ARM making
more.

**The change:** append `--subtitle-default=none` to `HB_ARGS_DVD` and
`HB_ARGS_BD` in `kubernetes/apps/automatic-ripping-machine/config-map.yaml`:

```yaml
HB_ARGS_DVD: "--preset-import-file /etc/arm/config/vulcanus-handbrake-preset.json --subtitle-default=none"
HB_ARGS_BD: "--preset-import-file /etc/arm/config/vulcanus-handbrake-preset.json --subtitle-default=none"
```

HandBrakeCLI then clears the default flag from every subtitle track the
preset selects, and with it the forced flag the muxer derives from it. The
foreign-audio search's track isn't one of those, and keeps default and
forced, as a track of only forced lines should. Transcodes have no default
subtitle track otherwise; Will chose that over waiting for a release with
#8187 (2026-11-16, *"--subtitle-default=none now"*). Once a release with
#8187 reaches ARM's image, drop the argument: MakeMKV's default then carries
through unforced, which is what reelbarrow leaves on a full track.

**Verified** 2026-11-16 off the cluster, with nixpkgs' HandBrake 1.11.2 and
this repository's preset, on an MKV made by ffmpeg with its subtitle track
default: out default and forced without the argument, neither with it.
**Not verified:** a real disc through ARM, which is the done-when below; and
the foreign-audio search's track on a disc that has forced subtitles.

**Made on this branch**, with a comment beside each argument saying when it
can go. ARM appends `HB_ARGS_DVD` as it stands to its HandBrakeCLI command
(`build_handbrake_command` in its `handbrake.py`), so the option reaches
HandBrake unchanged. `arm.yaml` isn't a generated ConfigMap, and an init
container copies it into an emptyDir as the pod starts, so a merged change
neither rolls ARM nor reaches it until it restarts: restart it while it is
idle, minding the double roll `docs/automatic-ripping-machine.md` describes.
The test disc is *Shrek the Third* again (Will, 2026-11-16): its transcode from
before the change is still in `completed/`, so one rip is a before and after
of the same disc. Its English track holds 1,092 pictures, 1 of them forced, so
the full track should come out with neither flag and the foreign-audio
search's one-line track default and forced. *The Hunchback of Notre Dame*
(1996), 1,294 pictures with 2 forced, is the same case on a disc not yet
ripped (reelbarrow's `captures/subtitle-flags/forced-all.txt`).

**Done when** a DVD ripped after the restart has no subtitle track forced but
a forced-only one, read with ffprobe on the share, and HandBrake's log in the
job lists no preset track as `Default`. Then the preset table's *Subtitles*
row in `docs/automatic-ripping-machine.md` says so.

---

## Alerting

When reelbarrow's `metrics` step lands, the rules go in
`kubernetes/infrastructure/prometheus-rules.yaml`: a successful ARM job neither
filed nor awaiting review after 24 h, a review untouched for 7 days, and the
service's own liveness. That spec says what each rule must be shown to do before
it counts.

---

## Closing

This half spans most of reelbarrow's steps, so expect several review cycles
under this slug, closed at phase boundaries. See "A phased project spans several
reviews" in `CLAUDE.md`. The spec retires once the case photo works. What stays
true then goes into a `docs/reelbarrow.md`: how it is deployed, how it is turned
on and off, and where manifests are written. The exporter's sidecar goes into
`docs/automatic-ripping-machine.md`.

---

## Related

- [disc-ripping.md](disc-ripping.md): produces what reelbarrow consumes. It
  owns DVD-to-ISO, and the drift in `docs/automatic-ripping-machine.md` about
  `backup_dvd`.
- [smb-charset-utf8.md](smb-charset-utf8.md): the share rejects filenames above
  U+00FF with `EIO`, and TMDB names contain them. reelbarrow folds library
  names to ASCII, as ARM's title hook does.
- [backups.md](backups.md): `media/video` is snapshotted and replicated, which
  is what makes deleting leftovers acceptable.
- **ARM's 2024 history**, which the running ARM's database lacks. That database
  begins at job 1 on 2026-04-17, so the exporter cannot see any earlier rip. The
  older `arm.db` survives as restic snapshot `7aced9c4` (host
  `automatic-ripping-machine`, tags `decommissioned` and `arm-2024-history`),
  recovered 2026-11-16 from a January snapshot of worker-0's OpenEBS disk:
  - It holds 109 jobs from 2024-01-15 to 2024-04-18: 80 succeeded and 29 failed;
    55 were Blu-ray, 50 DVD, and 4 music or unknown.
  - Their output paths are under `/home/root/media/completed/`.
  - Its schema is alembic `469d88477c13`; the running ARM's is `edf2272c0a9d`.
  - The job logs sit beside it in
    `/arm-2024-history/pvc-7ca1e9a9-79c4-4c39-8e59-e5c8b0135a6f/`.
  - A `README.txt` in the snapshot says what the other three directories are.

  It lives in the repository host, LXC 108. Run restic there as `restic`, never
  root: the repository is `restic`'s, and a lock root leaves behind is one
  `restic` cannot clear.

  ```bash
  ssh root@vulcanus.forge.local
  pct enter 108
  runuser -u restic -- env RESTIC_PASSWORD_FILE=/run/secrets/restic/password \
    restic -r /mnt/backups/restic restore 7aced9c4 --target /var/tmp/arm-2024
  ```

  `restic dump 7aced9c4 <path>` streams a single file instead.
