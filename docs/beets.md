# Beets

Music and audiobook tagging and library management. The library lives on the
fileserver's `audio` dataset; the catalogue is a single SQLite file on a
Kubernetes PVC.

Everything is in `kubernetes/apps/beets/`.

## What runs

| Object | State | Purpose |
|---|---|---|
| `beets-flask` Deployment | Running | Web UI at `beets.immortalkeep.com` (internal only). The primary interface. |
| `beets-import` CronJob | **Suspended** | The former nightly `beet import -q`. Retained as a known-working fallback. |
| `beets-replaygain` CronJob | 04:00 daily | ReplayGain analysis, which cannot run during import. See below. |

### Why the CronJob is suspended

It ran nightly for three years and stopped achieving anything long before it was
turned off. Its own log records 1,073 runs; the last time it took any action
other than `skip` was 23 February 2026. Each run walked the same ~520 album
folders, skipped every one, and exited after 75–80 minutes, leaving a 17 MB log
of 197,679 `skip` lines.

That was not a bug. `-q` refuses anything without a strong MusicBrainz match,
and the inbox is almost entirely audiobooks, which essentially never match. The
job could only ever have been a no-op for this collection. beets-flask exists to
make the interactive path — which was always where the real work happened — the
default one.

**Do not unsuspend it while beets-flask is running.** See the concurrency rule
below.

## Storage and concurrency

Three volumes matter:

| Mount | Claim | Notes |
|---|---|---|
| `/library` | `beets-library-pvc` | `library.blb`, the SQLite catalogue. RWO `openebs-hostpath`, PV pinned by `nodeAffinity` to `piraeus-worker-0`. |
| `/audio` | `audio-rw-beets-pvc` | The music tree and the import inbox, over SMB. |
| `/config` | `beets-flask-config-pvc` | beets-flask's own database and the seeded config. |

**`ReadWriteOnce` restricts to one node, not one pod.** Because the library PV
is pinned to `piraeus-worker-0`, anything that mounts it lands there, and
Kubernetes will happily run beets-flask, the CronJob and an ad-hoc pod against
it simultaneously. Nothing will stop you.

SQLite will. `library.blb` has no coordination between processes:

- Concurrent **reads** (`beet ls`, `beet stats`, `beet list -a`) are safe.
- Concurrent **writes** (`import`, `modify`, `update`, `mbsync`) hit
  `database is locked` and can leave an operation half-applied.

So: read freely; scale beets-flask to zero before any ad-hoc write. The
`beets-shell` devenv script does this automatically. Scaling works against Flux
because `deployment.yaml` sets no `replicas` field — server-side apply never
claims it, so the reconciler leaves manual scaling alone.

### Neither database is backed up

`beets-library-pvc` is `openebs-hostpath` with `reclaimPolicy: Delete`, and it
is *not* in borgmatic's mount list — unlike the audio share itself, which is
covered. Deleting the PVC destroys the catalogue. The audio files survive, but
every tagging decision made since 2022 does not.

`beets-flask-config-pvc` is exposed the same way and is easier to overlook,
because it looks like configuration. It also holds beets-flask's own database —
518 sessions as of 2026-08-13 — which is every fetched candidate for every
folder in the inbox. Losing it costs hours of MusicBrainz lookups rather than
anything irreplaceable, but it is not free.

Neither is a beets-specific problem: 33 PVCs use `openebs-hostpath` and exactly
one of them is in borgmatic. Only the coarse Proxmox VM blob backup covers the
rest. See [`todos/backups.md`](../todos/backups.md), which is the top-priority
item in that directory.

Ad-hoc snapshots taken during the 2026-08-13 migration live in
`~/backups/beets/` on the workstation — both databases plus a `beet stats`
reference. They are not managed by anything and will go stale.

Snapshot it before any significant change. Use SQLite's backup API rather than
`cp`, so the result is valid even if something is mid-write:

```bash
kubectl exec -n apps <pod> -- python3 -c \
  "import sqlite3; s=sqlite3.connect('/library/library.blb'); \
   d=sqlite3.connect('/tmp/library.blb'); s.backup(d); d.close(); s.close()"
kubectl cp apps/<pod>:/tmp/library.blb ~/backups/beets/library-$(date +%F).blb
```

## ReplayGain runs on a schedule, not on import

`replaygain.auto` is **off**, and the `beets-replaygain` CronJob does the
analysis nightly at 04:00 instead.

It cannot run during import. beets-flask executes imports as rq jobs with a
600-second timeout hardcoded in `backend/beets_flask/redis.py`, and ffmpeg
analyses at roughly 100× realtime — so an audiobook longer than about fifteen
hours exceeds it and takes the whole import down with it. Measured 2026-08-13:

| Audiobook | Length | Import |
|---|---|---|
| Dawnshard | 7.1 h | fine |
| Teresa: Everybody Loves Large Chests (Vol.5) | 14.9 h | fine |
| Arcanum Unbounded | 22.5 h | `JobTimeoutException` mid-import |

This never bit the old CronJob because a CronJob has no equivalent timeout. The
second reason is throughput: there is one import worker, and a long book would
occupy it for ten minutes or more.

The job runs without `-f`, so items that already carry `rg_track_gain` are
skipped and a nightly run is a cheap scan unless new content has landed. That
also makes it idempotent — which matters, because it writes `library.blb` and
so contends with beets-flask under the rule below. A collision surfaces as
`database is locked` and aborts the run; whatever was analysed up to that point
is already stored, and the next night continues. Nothing needs repairing.

To run it by hand — after a big import session, say, rather than waiting:

```bash
kubectl create job -n apps rg-now --from=cronjob/beets-replaygain
kubectl logs -n apps -f job/rg-now
```

## Recovering a stuck import session

An import that dies part-way — a worker restart, a timeout, an undo that cannot
apply — leaves its session in a state beets-flask cannot itself clear. **There is
no way out through the UI**, and the buttons that look like they should help
report contradictory things: undo says *"Cannot undo if never imported"* while
redo says *"Cannot redo imports. Try undo and/or retag!"*.

The cause is an upstream schema cycle. `task.chosen_candidate_id` references
`candidate.id` and `candidate.task_id` references `task.id`, so SQLAlchemy
cannot order the deletes and anything that needs to clear a session fails with:

```
sqlalchemy.exc.CircularDependencyError: Circular dependency detected.
  (DeleteState(<TaskStateInDb …>), DeleteState(<CandidateStateInDb …>))
```

That defeats `DELETE /api_v1/session/id/<id>`, re-enqueuing a preview (what the
retag button does — it is accepted, then the worker dies on it), and undo alike.

### The repair

Work on the session record directly, and **update rather than delete** — the
redundant rows are harmless, the unreachable *state* is the problem, and updates
avoid the cycle entirely. Set the session to whatever is actually true on disk
and in the beets library, and clear the stored exception.

`/config` is `ReadWriteOnce`, so beets-flask has to be scaled down to reach it:

```bash
kubectl scale -n apps deploy/beets-flask --replicas=0
kubectl wait -n apps --for=delete pod -l app=beets-flask --timeout=120s
# start a pod mounting beets-flask-config-pvc, then:
#   1. snapshot the DB with sqlite3's backup API and copy it out
#   2. update session.progress / task.progress, set session.exc = NULL
kubectl scale -n apps deploy/beets-flask --replicas=1
```

Back it up first. That database holds every fetched candidate — 518 sessions as
of 2026-08-13 — and re-fetching them means hours of MusicBrainz lookups.

Which state to choose:

| Situation | Set progress to |
|---|---|
| Files are in the library where they belong | `IMPORT_COMPLETED` |
| Never imported, or you want to import it afresh | `PREVIEW_COMPLETED` |

`PREVIEW_COMPLETED` is the safe landing point — `importer/progress.py` marks it a
dummy state with no stage behind it, and it is where the large majority of
healthy sessions sit. Candidates already fetched are preserved either way.

Two things not to be alarmed by. `pragma foreign_key_check` reports
`foreign key mismatch - "session" referencing "folder"` — that is pre-existing in
beets-flask's schema, present in an untouched backup, and not a sign of damage;
use `pragma integrity_check` instead. And more than one session per folder is
possible: a failed undo can create a second one, which is what produces the
contradictory buttons.

### The library keeps its own residue

The session is only half of what a failed import leaves behind. beets adds the
album to `library.blb` in the apply stage, *before* `manipulate_files` puts any
file where it belongs, so a failure during file manipulation leaves a complete
album — every item — whose paths still point inside `/audio/import`:

```sh
beet ls -a -f '$albumartist - $album'   # looks imported
beet ls -f '$path' 'album:<name>'       # paths are still under import/
```

Nothing surfaces this. Flux is healthy, the UI shows the folder as failed, and
`beet stats` counts the album as part of the collection. Check the paths, not the
presence of the album.

Repairing the session does not touch it, and the two have to agree. Setting a
session to `IMPORT_COMPLETED` while the library still points into the inbox
claims a file layout that does not exist; `PREVIEW_COMPLETED` plus `beet remove`
of the album — which drops the database rows and leaves the files alone — is the
combination that actually returns the folder to "not yet imported". `beet remove`
needs `beets-shell`, which scales the Deployment down for the write.

## Getting files into the inbox

The inbox is `/mnt/storage/media/audio/import/` on the fileserver — the ZFS path
behind the `audio-rw` share that beets-flask mounts as `/audio/import`. New
material goes there and is imported from the web UI. Nothing is written straight
into `music/` or `audiobooks/`: [`paths:`](#where-files-land) decides layout, and
the library has no record of a file it did not place itself.

From the LAN, mounting the share and writing through it needs no further thought
— Samba applies the identity below on its own. From the tailnet it is not an
option, since only port 22 is granted ([`tailnet.md`](tailnet.md)), so the route
is rsync over SSH, which has to set ownership itself:

```sh
rsync -avh --info=progress2 --chown=rancher --chmod=D755,F644 \
  "$HOME/music/Artist - Album/" \
  root@192.168.0.105:"/mnt/storage/media/audio/import/Artist - Album/"
```

`--chown` is the point of the command. `[audio-rw]` sets no `force user`, so
Samba authorises as the real Unix user `rancher`, and a transfer that keeps its
own uid lands a folder beets can read and cannot write — the failure under [the
mount hides every permission the server
enforces](#the-mount-hides-every-permission-the-server-enforces), which surfaces
much later, as `delete_imported_folders` failing to clear a folder that imported
cleanly. Setting the owner is sufficient and the group is left alone; the mode
bits only match what a write through the share would have produced anyway.
`--chown` needs super-user on the receiving side, hence `root@`.

Confirm on the server, the only vantage point that reports real ownership:

```sh
ssh root@192.168.0.105 \
  'stat -c "%a %U:%G %n" "/mnt/storage/media/audio/import/Artist - Album"'
```

### Source filenames have to be Latin-1

Samba runs `unix charset = ISO-8859-1`, so a filename holding any character above
U+00FF cannot be written to any share here and fails with `EIO`. A curly
apostrophe, en dash or ellipsis in a track title is enough, which makes this an
ordinary hazard for music rather than an exotic one. Screen a batch before
sending it:

```sh
find "$src" -mindepth 1 -printf '%P\n' | while IFS= read -r n; do
  printf '%s' "$n" | iconv -f UTF-8 -t ISO-8859-1 >/dev/null 2>&1 \
    || echo "UNSTORABLE: $n"
done
```

Renaming the offender is the whole fix, and it costs nothing downstream: the
import destination is computed from tags rather than from the source filename,
and `asciify_paths` transliterates it to ASCII regardless. The limit applies to
what is copied *into* the inbox, not to what beets writes out of it.
[`todos/smb-charset-utf8.md`](../todos/smb-charset-utf8.md) holds the measured
boundary and what moving the fileserver to UTF-8 would cost.

## Imports are copies, not moves

beets-flask supports `copy` only — it types `move` as `Literal[False]`, so
`move: yes` is a hard config validation error rather than a setting it ignores.
The practical consequences:

- An import writes into `/audio/music/…` (or `audiobooks/`, `podcasts/`, …) and
  **leaves the original in `/audio/import`**. The inbox does not self-drain.
- Cleanup is a first-class inbox action, `delete_imported_folders` — "delete all
  folders from the inbox that have been imported" — backed by the
  `IMPORT_COMPLETED` session state beets-flask records per folder. There is also
  `delete` for a hand-picked selection and `undo` (with a `delete_files` option)
  to reverse an import.
- That action only knows about imports made *through beets-flask*. Anything the
  CronJob imported is not in its database — but the CronJob used `move`, so
  those folders already left the inbox.

## Where files land

`directory` is `/audio/`, and `paths:` sorts everything below it by genre:

| Query | Destination |
|---|---|
| `genres:audiobook` | `audiobooks/$author/$album%aunique{}/$track $title` |
| `genres:podcast` | `podcasts/$albumartist/…` |
| `genres:christmas` | `christmas/$albumartist/…` |
| `comp` | `music/Various Artists/…` |
| `singleton` | `music/$artist/singles/$title` |
| `default` | `music/$albumartist/…` |

beets takes the **first** query that matches, and `default` matches everything,
so it has to come last. It was listed first until 2026-08-13, which made every
rule below it unreachable.

The queries say `genres`, not `genre`. Only the plural is in `Album.item_keys`,
and these queries are evaluated against items — so `beet modify -a … genre=…`
sets an album field that never reaches an item and routes nothing.

Genre is the single field that decides placement. The local `audiobook_genre`
plugin keeps it that way without hand-tagging everything MusicBrainz already
knows: on `import_task_apply` — which fires before `manipulate_files`, so the
destination is computed with the genre already set — it prepends `audiobook` to
`genres` whenever `albumtypes` contains it.

Any existing spelling of `audiobook` is dropped first, case-folded, so the marker
appears exactly once and always in lower case. Audible tags a number of these
rips `Audiobook` outright, and matching only the exact marker left that beside it
as a near-duplicate differing in case alone. Genres that describe the book are
untouched.

There is deliberately no
`albumtypes:audiobook` path rule; two fields deciding one thing is worse than
one field doing it well. To find audiobooks MusicBrainz knows about that we have
not tagged:

```sh
beet ls -a 'albumtypes:audiobook ^genres:audiobook'
```

### Audiobooks file under the author, not the artist credit

For an audiobook, `albumartist` is the whole MusicBrainz artist credit — author
*and* narrator — so one author scatters across a folder per narration. Brandon
Sanderson had seven, and 88 albums occupied 50 top-level folders. `$author` is
an `inline` computed field that collapses them to 35, leaving the tags alone so
`mbsync` keeps working and Plex still shows the full credit.

It resolves in this order:

1. An explicit `author` flexible field, the manual override:
   `beet modify 'album:Tanya' author='Carlo Zen'`. Note it lives only in
   `library.blb`, which is not backed up, and is never written to file tags.
2. `albumartists[0]`. MusicBrainz splits the credit into an ordered list with
   the author first.
3. `albumartist` split on ` read by ` / ` narrated by ` / ` performed by ` / `/`,
   for albums tagged from their filenames, where `albumartists` is empty.
4. The credit string unchanged.

Two things it gets wrong, both fixable with the override: a credit that lists
someone other than the author first (an illustrator, as on the Tanya the Evil
light novels) is taken at its word, and a co-authored book files under its first
author only.

Three overrides are in force as of 2026-08-13. They are listed here because they
live only in `library.blb`, which [is not backed up](#neither-database-is-backed-up)
— losing it loses them silently, and this is the only record:

```sh
beet modify 'album:The Saga of Tanya the Evil, Vol. 1' author='Carlo Zen'
beet modify 'albumartist::추공'                        author='Chugong'
beet modify 'album:Abundance'                          author='Ezra Klein & Derek Thompson'
```

Respectively: an illustrator credited ahead of the author; a Korean pen name
that `asciify_paths` renders `cugong`; and a genuinely co-authored book that
should keep both names.

Three mechanics are worth knowing before changing it:

- It must be an **item** field. Only `item_fields` reach `Item._getters()`,
  which is what `Item.destination()` formats a path against. An `album_fields`
  entry evaluates fine in `beet ls -a` and routes nothing — the same trap as
  `genre` vs `genres`.
- The corollary, which looks like a bug the first time: `beet ls -a -f '$author'`
  prints the literal string `$author`, because `-a` formats against the Album and
  `Album._getters()` has no such field. Inspect it with `beet ls`, no `-a`.
- `%aunique{}` stays on its default `albumartist album` keys. `aunique` builds a
  SQL query through `Album.duplicates_query`, which cannot see computed fields,
  so `%aunique{author album}` would silently disambiguate nothing. The gap that
  leaves is two different narrations of the same title by the same author, which
  now share a directory.

Re-filing the existing tree after a `paths:` change is `beet move -a
genres:audiobook` from `beets-shell`, plus `beet move 'genres:audiobook'
singleton:true` — `-a` matches albums, so singletons are invisible to it and
stay wherever they were. That is how four Bartimaeus books sat in
`music/Jonathan Stroud/singles/` until 2026-08-13.

Source and destination are on the same CIFS mount and `beets.util.move` tries
`os.replace` first, so it runs as server-side renames rather than copies —
minutes for the whole library, not hours. `Item.move` prunes the vacated
directories on the way, but only the one an item just left: a directory emptied
by some earlier rename is not on that path and survives. Sweep with
`find /audio/audiobooks -mindepth 1 -type d -empty` afterwards.

An album whose library paths do not match what is on disk is skipped silently —
`beet move` finds no file to move and says nothing. Check for those before
concluding a re-file worked:

```sh
beet ls -f '$path' 'genres:audiobook' | while read -r p; do
  [ -e "$p" ] || echo "MISSING: $p"
done
```

## Configuration

One beets config, `config-beets.yaml` in `beets-config-map`, shared by both
consumers:

- **beets-flask** gets it seeded to `/config/beets/config.yaml` by the
  `config-seed` initContainer, which runs on *every* pod start. The ConfigMap in
  git is therefore authoritative: edits made in the web UI land on the PVC and
  are overwritten at the next restart.
- **The CronJob** projects it back to `/config/config.yaml` via `items:` on its
  ConfigMap volume, because beets expects that filename.

beets-flask's own settings — the inbox definition, terminal start path, worker
count — are a separate key, `beets-flask-gui.yaml`, because it reads them from a
separate file.

Settings that exist specifically because beets-flask is stricter than the CLI,
all harmless to the CronJob:

| Setting | Reason |
|---|---|
| `import.move: no`, `copy: yes` | beets-flask supports copy only. |
| `import.duplicate_action: ask` | beets-flask's schema defaults this to `remove`, which deletes duplicates unattended. |
| `replaygain.backend: ffmpeg` | The beets-flask image is `python:3.12-slim` with a static ffmpeg and no GStreamer at all — the `gstreamer` backend aborts beets at startup. `linuxserver/beets` ships both, so ffmpeg suits either. |
| no `permissions` plugin | Both consumers reach the library over CIFS, which fixes modes as mount options. Its chmod is a no-op at best, an EPERM per file at worst. |
| no `statefile` | CLI importer resume state. beets-flask keeps its own per-folder session state; sharing a pickle only lets the two confuse each other. |

The inbox runs `autotag: "auto"` with `auto_threshold: null`, which defers to
`match.strong_rec_thresh` (0.04, the beets default) — the same bar `beet import
-q` applied. The difference from the CronJob is what happens on a miss: instead
of a `skip` line in a log, the folder parks in the UI at
`WAITING_FOR_USER_SELECTION` with its candidates already fetched.

## beets-flask runs as uid 1000, and needs its own SMB mount

Every other workload that writes to an SMB share here runs as root, because the
shares mount with no `uid=` option and the CIFS client presents every file as
`root:root`. `pinepods` and `filebot` both set `PUID`/`PGID` to `"0"` for
exactly this reason.

beets-flask cannot. Its image entrypoint runs as root only long enough to fix
permissions, then unconditionally `su beetle`. There is no switch to keep it as
root, and `GROUP_ID=0` does not work either — the entrypoint runs
`groupmod -g 0 beetle`, which fails because gid 0 already belongs to `root`.

So there is a second PersistentVolume against the same share,
`audio-rw-beets-pv`, carrying `uid=1000,gid=1000` in its `mountOptions`. Those
options only change what the CIFS client reports locally and which uid it lets
past its own permission check; authorisation on the server still uses the same
`smb-credentials` identity as every other mount. `audio-rw-arm-pv` is the
precedent for a second PV against one share.

`volumeHandle` must be unique per PV for the SMB CSI driver — two PVs sharing a
handle are treated as the same volume, and the second's `mountOptions` are
silently dropped.

### The mount hides every permission the server enforces

`uid=`, `gid=`, `file_mode=` and `dir_mode=` describe what the CIFS client
*reports*. Nothing under `/audio` carries real ownership into the pod, so every
path renders as `drwxr-xr-x beetle beetle` regardless of what the fileserver
holds, and `ls` from inside the cluster cannot show a permission problem — it
prints the mount options back at you. Only two checks mean anything:

```sh
# Does the operation the app needs actually succeed?
kubectl exec -n apps <pod> -- su beetle -c 'touch "/audio/import/<folder>/.probe"'
# What does the server itself think?
ssh root@192.168.0.105 'stat -c "%a %U:%G %n" "/mnt/storage/media/audio/import/<folder>"'
```

There is a standing reason to reach for them. `[audio-rw]` sets no `force user`,
so Samba authorises as the real Unix user `rancher` — unlike `[media]`, which
forces `nobody` and is immune. A folder that reaches the inbox by a route that
preserves some other uid — rsync or scp as root, rather than a write through the
share — lands owned by that uid at mode 0755, leaving rancher `r-x`. beets can
read the audio and can create nothing beside it.

That asymmetry is what makes the failure confusing rather than merely annoying:
the destination is writable, the source is not, and the CIFS view says both are
`beetle`. A copy out of such a folder succeeds; anything needing to write or
unlink *within* it fails with `Permission denied` against a directory whose
permissions look correct from every vantage point inside the cluster.

## Image updates

`beets-flask` is the only pre-release-tracking ImagePolicy in the repo. v2.0.0
has only ever shipped as release candidates, so the range names one explicitly:

```yaml
range: '>=2.0.0-rc1 <3.0.0'
```

A plain `^2.0.0` would resolve to nothing at all. Masterminds/semver records
pre-release-ness per AND-group, so naming a pre-release in the lower bound also
lets `<3.0.0` match them. This will roll onto v2.0.0 final unattended when it
ships; tighten to `^2.0.0` at that point.

## Interactive CLI access

`beets-flask` has a built-in web terminal, which covers most needs. For
everything else there is a devenv script:

```bash
beets-shell
```

It scales beets-flask to zero, starts a `linuxserver/beets` pod with the same
config and mounts, drops you into a shell, and scales back up on exit.

Note that `kubectl debug --copy-to` is *not* a working substitute. The cluster
enforces PodSecurity `baseline`, and kubectl's default debug profile adds
`SYS_PTRACE`, which violates it. `--profile=baseline` avoids that if you need
the technique elsewhere.
