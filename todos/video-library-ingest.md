# Video library ingest: from ARM's output to a filed, archival library

## Opening prompt

> Ripped discs stop in `media/video/import/automatic-ripping-machine/` and never
> reach the library Plex reads. Read `todos/video-library-ingest.md`: the
> decisions already made (our own tool, `reelbarrow`, in a new repository, as
> a separate service with a small web UI), the nine cases an ingest has to tell apart and
> the evidence for each, the identity and matching techniques that were measured
> to work, and the phased plan. Start with **Status** and **Decisions already made**.
> The fixtures table in **What is waiting** is the acceptance test for phases 0–3.

Design verified **2026-10-03 to 10-05** against ARM `2.24.4`, Plex `1.43.4`, and the
share as it stood on those dates, unless stated otherwise. Everything in this
spec was read-only: no file under `media/video` was moved or written.

---

## Status

| Phase | What | State |
|---|---|---|
| — | Design: decisions, cases, techniques, layout | **complete** 2026-10-05 — this document |
| 0 | `reelbarrow` repository; ARM job manifests; dry-run planner over current output | not started |
| 1 | Movies: automatic filing of the unambiguous ones, Plex refresh, tidy-up | not started |
| 2 | Review UI and notifications; the ambiguous movie cases | not started |
| 3 | TV: episode identification | not started |
| 4 | Case photo at insert time | not started |
| — | The `unidentified/` + `raw/` backlog | **next project**, see *Future work* |

---

## Goals, as Will stated them (2026-10-03)

The buckets:

> * `video/import`: a bit of a dumping ground, has collected many past video
>   import projects.
> * `video/import/youtube-dl`: videos grabbed from youtube, a bit of a mixed bag
>   between "real" tv shows/movies and random stuff. Most of which we would want
>   to end up in Plex eventually, under one category or another
> * `video/import/automatic-ripping-machine`: subdirectories contain the
>   pre-processed and final video files of movies and tv shows. This is the
>   primary target of this project. Ideally we make a solution that can be
>   extended to the prior categories, but this is the one we actually want to
>   solve today.

Archivist goals:

> 1. Videos (tv shows & movies) are watchable from Plex
> 2. Transcoded and "raw" source files for a given show/movie are stored
>    together (making it easy to re-transcode in the future, or do fun things like
>    play DVD games via the raw files)
> 3. Video files are accurately tagged (e.g. what was the show, but also what was
>    the episode of the show)
> 4. Files are metadata-complete (tagging, subtitles, audio channels, maybe movie
>    posters, etc.)

The two priorities from [disc-ripping.md](disc-ripping.md) govern here too:
**data integrity** first, **self-service by Will's brother** second. The server
and the discs are at his house; he inserts them and holds the cases.

## Decisions already made

Recorded verbatim where Will gave words, so they are not relitigated.

- **Our own tool, not FileBot** (2026-10-04): *"yes, let's build our own tool.
  It will go in a new repo."* Named **`reelbarrow`** (2026-10-05) — a
  wheelbarrow for reels. Asked first whether FileBot offers anything that
  cannot be attained independently — the answer, in *Why not FileBot*, was no.
- **A separate service with its own small web UI** (2026-10-04), fed by ARM,
  rather than a fork of ARM's UI or a chat bot. See *Architecture*.
- **Transcoding stays in ARM** (2026-10-03): *"I currently like having
  Transcode as part of ARM because it provides a way to surface to the user
  what's going on. The wasted time is currently not of concern, though maybe a
  project for the future - at which point we can build something different or
  contribute play-all identification upstream."* So the ingest consumes ARM's
  transcodes; it does not re-encode.
- **A raw copy that preserves menus is a requirement** (2026-10-03, *"Yes, this
  is a requirement."*), but DVD-to-ISO belongs to [disc-ripping.md](disc-ripping.md):
  *"Let's design for the present realities, but I'll take for the separate
  `disc-ripping` project the task of how to also rip DVDs to ISO."* The ingest
  files whatever raw form ARM produced and must not assume it.
- **Reviews are done by Will's brother where possible** (2026-10-03):
  *"Preferably my brother at his house - as he'll be the one inserting the discs
  and will have the cases on-hand for easy identification"*, on *"Phone, desktop
  or laptop"*. A photo of the case back is welcome *"as long as the process is
  as straight-forward as possible."*
- **Automation is binary, and a misfile is a bug** (2026-10-04): *"Either a
  thing is sufficiently trusted to be filed without human intervention or it is
  not. If, in the future, we find that something was incorrectly automatically
  filed, then that's to be treated as a bug in the system for us to patch. In
  our automatic filing, we're looking for "excellent" but not "perfect"
  certainty."* There is no trust ramp and no "confirm the first N discs" mode.
- **Choosing the best copy should be automatic, under strict rules** (2026-10-03):
  *"We need to be strict on those definitions though such that if a "best" can't
  be identified - then the fallback will be to ask."*
- **Tidy over hoarding** (2026-10-04): leftovers — duplicates, play-all
  transcodes, files not chosen, empty directories — are deleted once filing
  succeeds. *"We want to tend toward being tidy. I have a number of ZFS
  snapshots should the worst happen."* Verified: `rpool/storage/media` holds 96
  snapshots, hourly to 2026-10-03 22:00, monthlies back to 2021-01-10, and is
  replicated to mini-nas ([docs/backups.md](../docs/backups.md)).
- **Portability over Plex-only** (2026-10-03): *"I've been wanting to be as
  complete as possible to preserve portability of moving to other players in
  the future."* Applies to sidecars and to how editions are named.
- **Editions must be verified, then filed portably** (2026-10-04): *"We need a
  way to verify before we go forward. And in the case of editions, we still need
  to file both to the file-system library in a manner that is
  future-portable."* Will has Plex Pass, which editions require.
- **Unnamed extras are acceptable for now** (2026-10-04): *"moving them to an
  `extras/` folder is sufficient. As long as we're not losing opportunity to
  access information (e.g. while the original disc is in hand), then it's a
  problem that can be solved down the road."*
- **Third-party identification services are fine** (2026-10-03): *"I'm open
  using third party services to do media identification (Claude or others)."*
  Will has a Claude Pro subscription, not an API account.
- **The LLM is Claude, through a pay-as-you-go API key** (2026-10-05): *"LLM
  Provider: let's go with Claude."* then *"Claude: we can do a pay-as-you-go
  for now"*. Not `claude -p` on the Pro subscription, for the reasons in *The
  LLM*. The key goes in SOPS; set a spend cap in the Console.
- **A film already in the library is folded together with the new rip, keeping
  the best of each** (2026-10-05): *"In this case we're also seeking to keep the
  best version. The Shrek the Third case is tricky because the archive raw (iso)
  might be better, but the new transcode may be better (size does not
  immediately trump, when newer file formats are in play). We'd probably keep
  the newer transcode, as it follows our latest conventions. Maybe the two
  copies need to be folded together?"* See *Case 9: folding into an existing
  entry*.
- **Two films on one disc are split in the library; the disc's raw and extras
  stay with the first** (2026-10-05): *"Two films on one disc: should end up
  split up in the final library. The raw file(s) and extras can stay with the
  "first" film."* The second film's folder points at them with a plain text
  file — Will: *"A symlink could work, but maybe a simple text file is
  sufficient."* See *Target layout*.
- **Scope** (2026-10-03): ARM's current output is today's goal; the
  `unidentified/` tree is *"the immediate next for us to solve"*, so the design
  must have a rough awareness of it.

---

## What exists today

### The handoff that is missing

| | audio | video |
|---|---|---|
| ARM writes to | `audio-rw/import` | `media/video/import/automatic-ripping-machine/completed/` |
| picked up by | beets-flask, 30 s debounce | **nothing** |
| reaches library | automatically | never |

Plex mounts `video/movies` and `video/shows` only (read-only subPaths in
`kubernetes/apps/plex/deployment.yaml`). `filebot` and `media-toolkit-webtop`
mount the whole video share; both are manual UIs. `import/` and the library are
on the **same share and dataset**, so filing is a `rename()` — instant and atomic,
no copy.

### The library's conventions

`/video/movies` holds 105 folders and `/video/shows` 10. Three generations of
convention coexist:

| era | example | tool | shape |
|---|---|---|---|
| 2013–2017 | `Shrek the Third (2007)/` | tinyMediaManager 2.x | `.iso` + `.m4v` beside each other, Kodi sidecars, `movieset-*` art |
| 2017 | `Pacific Rim (2013)/Pacific Rim (2013)/BDMV` | by hand | raw BDMV nested under the film's own name |
| **2024** | `Dredd (2012) {tmdb-49049}/` | FileBot `amc` + hand-moved raw | film, `extras/`, **`raw/`**, `movie.nfo`, `poster.jpg`/`folder.jpg`, `fanart.jpg`, `clearart.png`, `logo.png`, `disc.png` |

The 2024 shape is the one to continue. `/video/movies/.plexignore` and
`/video/shows/.plexignore` both contain `*/raw/`, so Plex never indexes a raw
folder. TV in 2024 is `Show {tmdb-N}/Season NN/Show - SNNEMM - Title.ext`.

**What Plex actually consumes.** Both libraries use the modern agents
(`tv.plex.agents.movie`, `tv.plex.agents.series`). Of 96 movies, 91 are matched
online (`plex://` GUIDs) and 5 are local-only; 91 posters come from the agent and
one from a file on disk. Current Plex agents do not read NFO files. So the 2024
NFO and artwork sidecars exist for **portability**, not for Plex — which is
exactly why Will wants them.

### How FileBot was actually used in 2024

`/data` on the filebot PVC holds `history.xml` (363 renames, last 2024-08-05),
the `amc` task arguments and a shell history. Together they show:

- `amc` ran with `--def artwork=y clean=y ignore=raw`. Raw folders were moved
  into `<film>/raw` **by hand** afterwards (`mv Skyfall/ /video/movies/Skyfall\ (2012)\ \{tmdb-37724\}/raw`
  and a dozen like it).
- ARM's `unidentified/` folders were **renamed by hand** to `Title (Year)` before
  `amc` could match them.
- **Every TV episode from disc was renamed by hand before FileBot saw it.**
  Witch Hunter Robin arrives in `history.xml` as `s01e01.mkv … s01e26.mkv`.
  FileBot matches *names*; it cannot tell `title_3.mkv` is episode 4, and its own
  forum says so ("FileBot has no possibly way of identify the files",
  <https://www.filebot.net/forums/viewtopic.php?t=9689>).
- Its NOVA matches from `youtube-dl` names were wrong often enough that seven
  were renumbered by hand (`mv "NOVA - S30E12.mp4" "NOVA - S31E3.mp4"` …).

That is the hand-holding Will remembers, quantified.

### Why not FileBot

Asked on 2026-10-04 whether FileBot offers anything that cannot be built:

| FileBot provides | Equivalent here |
|---|---|
| Lookups (TMDB, TheTVDB, AniDB, OMDb) | TMDB API (free personal key) covers film and TV; the library already keys on TMDB IDs. FileBot's licence does bundle TheTVDB access, which is otherwise paid — not needed. |
| Naming formats | String formatting, plus `ffprobe` for technical fields |
| Artwork, NFO | TMDB + fanart.tv (free personal key), and writing XML |
| History / revert | The service's own ledger, which it needs anyway |
| Fuzzy matching of messy filenames | Its one real asset, and it matters only for the `youtube-dl` and legacy `import/` buckets. ARM supplies an IMDb ID, and TV needs content matching, which FileBot does not do. |

Licence at the time: US$8/year or US$80 lifetime. Not bought.

**TMDB's own data has gaps**, which shapes episode matching: of the 13 season-1
episodes of *The Sylvester & Tweety Mysteries* (TMDB `9867`), only 2 have an
English overview. Reference text has to come from several sources.

### What ARM knows, and where

ARM's SQLite database (`/root/db/arm.db`, on a node-local PVC on worker-1) is the
only place that knows, per job: `title`, `year`, `imdb_id`, `video_type`,
`label`, `disctype`, `crc_id`, `status`, `path` (the completed directory,
including any `_<stage>` suffix), and a `track` table holding **both** MakeMKV's
and HandBrake's view of every title. None of it is written next to the output.

- **`crc_id`** is pydvdid's CRC64 for DVDs. It is `None` for every Blu-ray job.
- **The track table is not a reliable raw↔transcode map.** For job 21,
  HandBrake's track *N* is MakeMKV's track *N−1*, and the final `title_K.mkv`
  names follow neither cleanly. Pairing has to be done by content.
- **There is no completion hook.** ARM calls `BASH_SCRIPT` with `$1=title
  $2=body` on every notification (`ripper/utils.py:88`), and
  `arm-audio-handoff.sh` already uses it by matching the message text. The
  video side can do the same. `BASH_SCRIPT` is single-valued, so a dispatcher
  has to call both handoffs. `job.pid` is `os.getpid()` of the ripper
  (`models/job.py:218`), and the hook runs as `/usr/bin/env bash <script>`, so
  the hook's `$PPID` should equal `job.pid` and select the row exactly. **Read
  from source, not exercised.**
- **Pushover is wired** (`PO_USER_KEY`/`PO_APP_KEY`), so the brother already
  receives ARM's messages on his phone.

### Raw is not what the docs say

`docs/automatic-ripping-machine.md` says `RIPMETHOD: backup_dvd` keeps a full
`VIDEO_TS`. In 2.24.4 it does not: `rip_with_mkv` (`makemkv.py:753`) backs up
the whole disc only for Blu-ray, and a DVD gets `makemkvcon mkv … all
--minlength=420`. **A DVD's raw copy is per-title MKVs of seven minutes or
more — no menus, no short titles.** Every DVD under `raw/` confirms it
(`raw/The-Hallelujah-Trail/B1_t00.mkv`). Blu-rays do get full `BDMV` backups.
The fix is Will's, in [disc-ripping.md](disc-ripping.md); the docs drift belongs
there too.

---

## What is waiting

### ARM's current output — the fixtures

These are the acceptance tests. Each row is what the ingest must conclude; the
evidence is in *The cases*. "Leftovers" means deleted once filing succeeds.

| ARM output (under `completed/`) | jobs | expected outcome |
|---|---|---|
| `movies/The-Hallelujah-Trail (1965)_178824223068` | 18 ok; 15–17 failed | **auto**: film → `The Hallelujah Trail (1965) {tmdb-…}`; raw `raw/The-Hallelujah-Trail/B1_t00.mkv`; leftovers: three empty sibling dirs and their `transcode/` twins |
| `movies/Those-Daring-Young-Men-…-Jalopies (1969)` | 34 | **auto**: film only |
| `movies/The-Great-Race (1965)` | 29 | **auto**: film + 1 extra |
| `movies/The-Polar-Express (2004)` | 25 ok; 24 the same disc misidentified | **auto**: film + 3 extras. Raw pairs by fingerprint: the complete backup is `raw/The-Adventures-of-Mary-Kate-and-Ashley--…` (14.3 GB); `raw/The-Polar-Express` is a 0.4 GB partial and is a leftover |
| `movies/Field-of-Dreams (1989)` | 30, 31 — **both failed**; file recovered by hand | **review**: no successful job vouches for the file. Two raw copies whose video hashes match (disc-ripping, 2026-09-20): keep one |
| `movies/Shrek-the-Third (2007)` | 28 | **fold** (case 9): ARM's AV1 transcode replaces the 2013 `.m4v`; the 2009 `.iso` is kept as raw and ARM's five per-title MKVs are leftovers, because the ISO is a decrypted superset of them (measured); the folder gains `{tmdb-N}` and fresh sidecars. Automatic only if every fold rule in *Confidence* holds |
| `movies/Robin-Hood (2010)` | 35 | **review** for labels: two cuts, verified as one film (case 5) |
| `movies/Robin-Hood (2010)_178999705941` | 36, different `crc_id` | **review**: a bonus disc whose 62.7-minute featurette ARM named `Robin-Hood (2010).mkv`; everything → `extras/` of Robin Hood |
| `movies/The Rescuers (1977)_178961349896` | 27; 5, 7, 8, 23 earlier | two films on one disc (case 6), split: the feature, the raw disc and the three extras → *The Rescuers (1977)*; `title_71` → *The Rescuers Down Under (1990)* with a pointer file. ARM never identified the second film, so it files automatically only if its identity meets the bar (transcript + runtime), else review. The four other raw backups are the same disc and are leftovers |
| `movies/Around-the-World-in-80-Days (1956)` | 39 ok; 40, 41 (disc 2) failed empty | **hold**: probably one film split across two discs (case 4); disc 2 not yet ripped |
| `tv/The-Sylvester-and-Tweety-Mysteries (1995-2002)` (+ two `_<stage>` siblings) | 21 (D1), 22 (D2), 33 (D2 again) | **auto** if the episode matcher meets the bar, else review: S01E01–E13; both play-alls dropped; job 33's six files are byte-identical duplicates of job 22's |
| empty: `An-American-Tail…`, `Le Mans (1971)*`, `Sherlock Holmes in the 22nd Century…`, the other `_<stage>` dirs | 2, 3, 19, 38, 40, 41… | leftovers; the matching `raw/` entry decides whether anything survives (e.g. `raw/Le Mans_177654944116/B1_t00.mkv` does) |
| `completed/Everything Everywhere All At Once {av1,h264}.mkv` | none — hand encodes, 2024-02 | not ARM output; leave for the backlog |

### The backlog, roughly

Will asked for rough awareness only (2026-10-03); it is the next project.

- `completed/unidentified/`: **180 entries, 154 holding no files at all** —
  skeletons of failed or already-filed jobs. 25 hold ~150 GB of 2024-era
  transcodes with labels as names (`DVD_VIDEO_170762049617`, `X2_DISC2`,
  `PRE0NNW1`, `MUMMY_ULTIMATE_BD_COLLECTION`, two Pirates of the Caribbean
  spellings…).
- `raw/`: **181 entries, 101 non-empty, 2.14 TB** — most of the backlog's
  substance. Many are Blu-ray backups whose transcode was filed by hand in 2024
  without its raw (Alien ×5, the Dark Knight trilogy and its bonus discs, Firefly
  D1–D3, Howl's Moving Castle…); some were never transcoded.
- `raw/` also proves how much re-ripping happened: `LOGICAL_VOLUME_ID` has 48
  suffixed siblings, `Puccini_-_Manon_Lescaut` 49, `Dune` 9 — almost all empty.

The design below is built so that the backlog is a *source*, not a special case:
the same analysis, identity and filing, with no ARM manifest and a human-supplied
title where ARM had none.

---

## The cases an ingest must tell apart

Will named three on 2026-10-03; the evidence turned up six more. Each is
something that has actually happened on this share.

| # | case | example | how it is recognised |
|---|---|---|---|
| 1 | **the same disc ripped again** | Tweety D2 (jobs 22, 33); Rescuers ×5; Hallelujah Trail ×4; Field of Dreams ×2 | same disc identity |
| 2 | **a film plus its bonus disc** | Robin Hood job 36; Dark Knight bonus discs in the backlog | same title, different disc identity, no title of the film's runtime |
| 3 | **a TV season across discs** | Tweety D1, D2 | same series, different disc identity |
| 4 | **one film split across discs** | Around the World disc 1 holds a 112.7-minute feature of a ~175-minute film | feature far shorter than the film's runtime, and a "disc 1" name — **unverified** until disc 2 rips |
| 5 | **two cuts of one film on one disc** | Robin Hood job 35: 155.9 min and 140.5 min | audio of the shorter is found, in order, inside the longer |
| 6 | **two films on one disc** | The Rescuers job 27: `title_71` is *The Rescuers Down Under* | two feature-length titles whose audio does not overlap at all |
| 7 | **a misidentified disc** | job 24 named The Polar Express "The Adventures of Mary-Kate and Ashley" (D12 in disc-ripping) | the runtime check; disc identity shared with a correctly named job |
| 8 | **a play-all title** | Tweety: 168.8 min on D1, 105.7 min on D2 | length ≈ sum of the others, *and* every episode's audio is found inside it |
| 9 | **already in the library** | Shrek the Third | the target folder exists |

Underneath all of them: **`title_N` carries no identity.** Re-ripping Tweety D2
renumbered every title (`title_4`→`title_5`, `title_1`→`title_0`,
`title_3`→`title_4`, `title_5`→`title_1`, `title_0`→`title_3`), and on D1 the
true order was E5, play-all, E1, E2, E3, E4, E7, E6, E8. Directory names carry an
`_<stage>` suffix that records when a job ran. Neither may be used as evidence of
what a file contains.

### Correction: the Rescuers "duplicate"

Earlier versions of this spec, and the first pass of this design, said a Blu-ray
"presents the feature through two playlists", that the 1,719 MB `title_71` was the
same film as the promoted 1,435 MB feature, and that *"at a fixed CRF a 20% larger
file means more retained detail, so … the better encode is the one filed as an
extra."* The 2026-10-03 session went further and, from mismatched frames at 0:45
and 73:20, called `title_71` a localisable version with credits moved to the end.

**Both were wrong.** Transcribing the same moments of each file
(2026-10-04) gives Miss Bianca at the Rescue Aid Society in the feature at 10:00,
and *"Okie dokie… grab on"* and *"a lovely lady's purse"* in `title_71`. Its
opening is an Outback landscape and its credits name Michael Kelly, editor of
*The Rescuers Down Under* (1990, 77 minutes — the title read off the physical
case during D12 in disc-ripping, then wrongly applied to the Polar Express disc
that had been put in it). The 35th Anniversary disc carries both films. An audio
fingerprint comparison finds 0.0% overlap, the same as two unrelated films.

The lesson that matters for the design: **two titles of near-identical length
told us nothing, and file size compares encodes only of identical content.**
Content comparison is the only safe test.

### Case 9: folding into an existing entry

Shrek the Third is the worked example. The library entry (2013–2017) holds a
7.2 GB `Shrek the Third (2007).iso`, a 1.07 GB `.m4v` (tinyMediaManager tags it
`h264-480p AC3-6ch`), a trailer, and two generations of sidecars. ARM job 28
produced an AV1 transcode, four extras, and five per-title raw MKVs.

Measured 2026-10-05:

- **The ISO is the same disc content as ARM's rip, and a superset of it.** It
  mounts as UDF with volume label `SHREK_THE_THIRD`, which is ARM's label for
  job 28. Its long titles match ARM's MakeMKV tracks one for one:

  | ISO title (lsdvd) | ARM track |
  |---|---|
  | 10:40.9, 2 ch | 640 s, 1 ch |
  | 9:54.6, 2 ch | 594 s, 1 ch |
  | 1:32:36.7, 19 ch | 5549 s, 18 ch |
  | 9:23.3, 2 ch | 562 s, 1 ch |
  | 18:24.6, 4 ch | 1102 s, 3 ch |

  MakeMKV drops the trailing stub chapter, hence one fewer each. The ISO also
  holds 34 shorter titles and the menus, which ARM's rip discarded.
- **The ISO is decrypted.** The 1 GB main-feature VOB decodes end to end with two
  warnings at its first GOP, and a frame at 5:00 is a clean picture.
- **pydvdid cannot confirm it.** The ISO's CRC64 is `413bb4d303e0a872` against
  ARM's `1e6b40d952fd6649`. pydvdid hashes file timestamps, and every file in
  the image is stamped 2009-12-29 19:38 — the night the image was made, not
  the disc's mastering date. **A re-authored image will never match a pressed
  disc's CRC**, so for images the identity check has to be structural, as above.

So the fold is: ARM's transcode becomes the film (Will's call: newer, current
conventions); the ISO becomes `raw/SHREK_THE_THIRD […]/`; ARM's five raw MKVs
and the old `.m4v` are leftovers; the trailer goes to `trailers/`; the old
sidecars are replaced by a fresh set; and the folder gains `{tmdb-N}`.

One thing to check before the first fold: **whether Plex keeps watched state
and ratings** when an item's folder is renamed and its file replaced. Every
legacy title re-ripped will go through this, so it is worth one deliberate test
on a throwaway item.

---

## Identity: what is this disc, and what is this title

### Disc identity

| disc | identity | measured |
|---|---|---|
| DVD | ARM's `crc_id` (pydvdid CRC64) | distinguishes Tweety D1 from D2, Robin Hood feature disc from bonus disc, Around the World disc 1 from disc 2; equal across re-rips |
| Blu-ray | SHA-1 over `BDMV/PLAYLIST/*`, `BDMV/index.bdmv`, `BDMV/MovieObject.bdmv` (path + bytes) | equal across all five Rescuers backups, **and across complete vs incomplete copies** (Scott Pilgrim 49.3 GB vs 1.0 GB; both X-Men DoFP pairs); joins Polar Express to the misnamed Mary-Kate folder |
| Blu-ray completeness | SHA-1 over relative path + size of every file under `BDMV/` | differs between complete and partial copies of the same disc |

**Do not use `CERTIFICATE/id.bdmv`.** Three different Dark Knight bonus discs
share one; several discs lack it.

For DVDs, `crc_id` lives only in ARM's database, and the raw MKVs cannot recompute
it — another reason the ARM manifest (below) matters. If disc-ripping moves DVDs
to ISO, pydvdid can be recomputed from the image.

### Title identity — three tools, each measured

**1. Exact duplicate: video-stream MD5.** `ffmpeg -map 0:v -c copy -f md5`. All
six Tweety D2 titles hash identically across jobs 22 and 33 despite different
numbering; Field of Dreams' two jobs did too. SVT-AV1 under ARM's preset is
deterministic for identical input, so equal hashes mean interchangeable files.

**2. Same work, different work, or an edit of it: audio fingerprint alignment.**
Chromaprint (`fpcalc -raw -length 0`) over each file's first audio track at
11,025 Hz mono, then map every 15-second window of the shorter onto the longer by
voting on frame offsets (`captures/video-library-ingest/robinhood/align.py`).

| pair | coverage of the shorter by the longer |
|---|---|
| Robin Hood 140.5 min cut vs 155.9 min cut | **94.9%**, offset rising monotonically 0 → +15.5 min across ~15 inserted passages, with a few scenes reordered |
| Rescuers feature vs `title_71` | 0.0% |
| control: Robin Hood vs Rescuers `title_71` | 0.0% |
| control: Rescuers feature vs Robin Hood | 0.0% |

The separation is total, which is what makes it usable as a gate. The unmatched
windows in the Robin Hood pair are the 15-second slices straddling an edit. It
works across codecs, so it also pairs a transcode with its raw source (AC3/DTS vs
Opus), and it proves a play-all contains each episode.

Note one fingerprint arrived short — the feature's audio stream through `kubectl
exec` was ~5 MB truncated (73:28 of 77:12). It did not change the result, but the
service reads files directly and must check that the decoded duration matches the
container's before trusting a fingerprint.

**3. Which episode is this: speech transcript matched against references.**
`whisper.cpp` with `ggml-base.en` on a 4-minute clip starting at 0:30 of each
Tweety episode — 8 clips in 32 s on 8 desktop CPU threads (no GPU). Every
transcript names its episode on sight (*"Pamplona periscope"*, *"Blarney
Stone"*, *"Copenhagen … Interpol"*): **13 of 13** identifiable by a reader.

As a deliberately crude baseline, TF-IDF cosine between transcript and the
Wikipedia synopsis:

| method | correct |
|---|---|
| top-1 per file | 11 of 13 |
| greedy one-to-one assignment | 13 of 13 |

Several margins were thin (1.0×–1.3×), so the one-to-one step got lucky in
places. The baseline proves the signal is there; it is not a sufficient gate on
its own. See *Confidence* for how the real matcher works.

Not measured: Whisper speed on worker-0's CPU; transcripts of a show whose
episodes lack distinctive proper nouns; any non-English audio.

---

## Architecture

```
           brother's phone / laptop
                │  ▲
     case photo │  │ Pushover: "review needed" / "filed", with a link
                ▼  │
  ┌──────────────────── reelbarrow (own repository) ─────────┐
  │ web UI ── job state + ledger (SQLite on a PVC)           │
  │ worker: intake → analyse → identify → plan → gate → file │
  └──┬──────────────▲───────────────────────┬────────────────┘
     │ reads/moves  │ manifests             │ partial scan
     ▼              │                       ▼
  video share   ARM hook (BASH_SCRIPT     Plex API
  (import/ and   dispatcher) writes
   library)      manifests/job-<id>.json
```

### The ARM side (this repository)

A dispatcher becomes `BASH_SCRIPT`, calling `arm-audio-handoff.sh` and a new
`arm-video-manifest.sh`. On each notification the video script looks up the job
by `$PPID` = `job.pid` and writes `manifests/job-<id>.json` under ARM's video
mount: the job row (title, year, IMDb ID, video type, label, disc type,
`crc_id`, status, paths) and its track rows. It writes on "Found disc" (so the
service can ask for a case photo while the disc is in the drive) and again on
completion or failure.

A file on the share rather than an HTTP call because it is durable: if the
service is down, nothing is lost, and the manifest outlives ARM's own job-log
retention. The service finds new manifests by scanning on an interval. A
notifying HTTP ping can be added for latency if the interval proves too slow.

For the existing jobs, phase 0 exports manifests once from ARM's database.

Remember [config-change-rollouts.md](config-change-rollouts.md):
`init-scripts` is a `subPath` mount, so a changed script needs `kubectl rollout
restart`, and must be checked in the container, not the ConfigMap.

### reelbarrow (its own repository)

Proposed shape, for review in the `reelbarrow` repository: Python (ffmpeg,
chromaprint and Whisper bindings all live there, as do ARM and beets), a server-rendered web UI
(FastAPI + HTMX or similar — phone-first, no SPA build), SQLite for job state and
the ledger, one worker process. The image is built by `reelbarrow`'s CI and
follows the usual image automation here — and per the CLAUDE.md lesson on
artifacts, check from which ref that workflow builds before trusting its tags.

The pipeline, per ARM job (and later per backlog folder):

1. **Intake.** Read the manifest; locate the completed directory (by the job's
   `path`, never by name-matching) and the raw candidates.
2. **Analyse.** For every output file: duration, streams, chapters, video MD5,
   audio fingerprint. For raw: Blu-ray nav and completeness hashes, per-title
   fingerprints. Verify decoded duration against the container before trusting a
   fingerprint.
3. **Classify titles**: feature, episode, play-all, extra, exact duplicate, other
   cut of the same work, other work. Pair each transcode with its raw source by
   fingerprint.
4. **Identify.** Film: ARM's IMDb ID → TMDB, checked against the feature's
   runtime. Series: episodes by transcript. Evidence from a case photo, if any.
5. **Plan.** A complete list of operations — renames, sidecar writes,
   deletions — each with its reasons.
6. **Gate.** Execute automatically only if every item in the plan meets the bar
   in *Confidence*; otherwise put the whole plan up for review. A plan is never
   half-executed.
7. **File.** Renames on the same filesystem, sidecars, then deletion of
   leftovers, then the ledger entry, in that order. Then a Plex partial scan of
   the touched folder (`/library/sections/{id}/refresh?path=…`). Plex's own
   change detection cannot be relied on over a CIFS mount.
8. **Notify.** Pushover to the brother: filed, or review needed, with a link.

### The review UI

One page per job, usable on a phone: what ARM thought, the proposed plan in plain
language ("Episode 5 – Something Fishy Around Here, from title_0"), and for each
uncertain item the evidence that made it uncertain — a frame grab, a transcript
excerpt, the runtime comparison. Actions: accept, correct (search TMDB, pick an
episode, label an edition), or "not sure — leave for Will". The page links into
ARM's job page for context. LAN-only, like `arm.immortalkeep.com`.

### The case photo

When a disc is found, the service sends a Pushover link: *"Disc found: Robin
Hood (2010). Tap to add a photo of the back of the case."* The page is a camera
button and nothing else. A vision model reads the photo for title, year, disc
number ("Disc 2 of 2", "Bonus Disc"), edition names ("Includes the Unrated
Director's Cut") and episode lists per disc. That is evidence for cases 2–6
captured at the one moment it is cheap, which is Will's concern about *"not
losing opportunity to access information (e.g. while the original disc is in
hand)"*. Optional: a job with no photo proceeds on the other evidence.

### The LLM

Used for two things: matching transcripts to episodes, and reading case photos.
Volume is small — a handful of calls per disc. Will has Claude Pro, not an API
account. The options, as of 2026-10-05:

| option | cost | fit |
|---|---|---|
| **Anthropic API key**, pay as you go | Haiku 4.5 is priced per million tokens; one disc's matching prompt is ~5k tokens in and ~1k out, so roughly a cent per disc. Pro does not include API credit | the documented route for a service; text and vision; a spend cap can be set in the Console |
| **`claude -p` under the Pro subscription**, via `claude setup-token` (`CLAUDE_CODE_OAUTH_TOKEN`) | nothing extra; shares Pro's usage limits with interactive use | it works — the measurements above ran this way from the workstation — but Anthropic's Claude Code legal page says OAuth is *"designed to support ordinary use of Claude Code and other native Anthropic applications"*, that *"developers building products or services … should use API key authentication"*, and reserves enforcement *"without prior notice"*. A cluster service calling it unattended is at best a grey area, and enforcement would break ingest silently. It also puts the Node-based CLI in the image |
| **Google Gemini API** | a free tier with rate limits; free-tier inputs may be used to improve Google's products | text and vision; a viable zero-cost option |
| **OpenAI API** | pay as you go, comparable | text and vision |
| **A local model on worker-0's CPU** (Ollama or llama.cpp) | free | no GPU in the cluster: a 7–8B model would prefill a 5k-token prompt in minutes and is markedly weaker. Not measured. The workstation's RTX 4090 is not always on |
| **No LLM**: local sentence embeddings + one-to-one assignment | free | a plausible replacement for TF-IDF as the *independent* signal whichever LLM is chosen; does not read photos |

Whatever is chosen sits behind one small interface, so it can be swapped.
Because the gate requires two independent signals, a weaker provider sends more
discs to review rather than misfiling them.

### Secrets

TMDB and fanart.tv keys, the LLM API key, Pushover keys (ARM's pair, or a new
app token), and a Plex token. All SOPS. Pushover and a Plex key may already exist
in `api-keys.sops.yaml` and the homepage secret respectively.

---

## Confidence: when a thing files itself

Will's bar is *"excellent" but not "perfect"*, and binary. Operationally, an item
files automatically only when **two independent signals agree and nothing
contradicts them**. Anything else goes to review. Each rule here is meant to be
read as a test case.

| decision | automatic when | otherwise |
|---|---|---|
| this disc is film X | ARM's identification (OMDb hit, CRC64 hit, or confirmed in the manual wait) **and** exactly one title within ±3% of TMDB's runtime for X | review — this is what catches case 7 |
| a title is a play-all | its length within 0.5% of the sum of ≥2 others **and** each of those others' audio is found inside it | treat as an ordinary title |
| a file is a duplicate | identical video MD5 to a file being kept | not a duplicate |
| best copy | rule 1: identical MD5 → keep the earliest successful job's. Rule 2: same disc identity, one copy incomplete (failed job, missing files, decoded length short of the track table) → keep the complete one | ask — never choose by size or bitrate |
| a title is an extra of film X | shorter than the feature, not another cut or another work (fingerprint), from a disc identified as X or as X's bonus disc | review |
| two cuts of one film | fingerprint coverage ≥ 90% with a monotonic offset | edition **labels** always need a source — the case photo, or TMDB/IMDb release data naming those exact runtimes |
| a bonus disc | no title near the film's runtime **and** the disc says so (label, embedded disc name, or the case photo) | review — otherwise indistinguishable from a misidentified disc |
| episode N | the LLM matcher assigns it with high confidence **and** an independent signal agrees (the TF-IDF baseline's assignment, the reference runtime, or the case photo's episode list) **and** the season assignment is one-to-one with no duplicates | review the disc's whole mapping |
| filing into an existing folder (case 9) | the existing entry is the same work by TMDB ID **and** every role below resolves by its own rule | review the whole fold |
| … which transcode is the film | same cut (fingerprint coverage ≥ 90%, lengths within 1%) **and** the newer is encoded from a source of equal or higher resolution → keep the newer, current-preset transcode (Will, 2026-10-05) | a different cut is an edition (case 5), not a replacement; a newer transcode from a *lower*-resolution source (a DVD rip beside a Blu-ray one) → review |
| … which raw survives | a decrypted full-disc image (ISO, `VIDEO_TS`, `BDMV`) whose long-title list matches the per-title MKVs (each length within 3 s, chapters within 1) is a superset of them → keep the image, drop the MKVs. Two raws of different discs (a DVD and a Blu-ray of one film) are both kept, each in its own `raw/<disc>/` | review |

**The episode matcher.** Whisper transcripts from two windows per episode (to
survive a cold open or recap), plus reference text from several sources, given
to an LLM that must return a one-to-one mapping with per-item confidence and
quoted evidence. References: Wikipedia episode lists (which had all 13 Tweety
synopses), TMDB (which had 2), IMDb plots, and OpenSubtitles text where
available (dialogue-to-dialogue is the strongest signal, but free accounts allow
5–20 downloads a day). The quoted evidence is what the review page shows.

Measured on 2026-10-05 with Claude Haiku 4.5 over the 13 Tweety transcripts
(through `claude -p` with a JSON schema):

| references given | correct | notes |
|---|---|---|
| episode titles only | **8 of 13** | the wrong ones were confident: the "Cajun Canary" episode went to *The Maltese Canary* at 0.75 |
| Wikipedia synopses | **13 of 13** | its one 0.6 was E08 by elimination — the same episode TF-IDF found hardest |

Two things follow. Reference text decides the outcome far more than the model
does, so gathering synopses is a first-class step. And a model's own confidence
is not calibrated — a wrong answer at 0.75 is exactly why the gate requires an
independent second signal rather than a confidence threshold.

A misfiled automatic decision is, per Will, a bug: the fix is a new row in the
table above plus a regression fixture, not a confirmation step.

---

## Target layout

### Movies

```
movies/
  Robin Hood (2010) {tmdb-N}/
    <feature or edition files — see Editions>
    movie.nfo · poster.jpg · fanart.jpg · clearlogo.png · disc.png · …
    extras/
      title_1.mkv …                     unnamed for now (Future work)
    raw/
      .ignore · .nomedia                plus the library-wide .plexignore
      ROBIN_HOOD [dvd-4391d7e10469999f]/   one folder per disc
        C1_t01.mkv  D2_t00.mkv
        disc.json                       identity, ARM job, and raw file → library file
      ROBIN_HOOD [dvd-d3c5ce89aef40f61]/   the bonus disc
```

`raw/` gets **one subfolder per disc**, named by label and identity, always —
even for a single-disc film — so a later bonus disc, re-rip or second disc of a
split film has an obvious home. `disc.json` records which raw title became which
library file, so a re-transcode or a menu-driven play of the raw copy (goal 2)
never needs this pipeline again. The 2024 entries keep their flat `raw/`; they
are not migrated.

**Two films on one disc** (case 6): the films are split into their own folders.
The disc's raw and its extras stay with the *first* film — the one ARM
identified, unless the review says otherwise. The second film's `raw/` holds one
plain text file named after the disc folder, so a listing of `raw/` reads the
same in both places:

```
The Rescuers Down Under (1990) {tmdb-N}/raw/RESCUERS_35TH_ANNIVERSARY [bd-b8de7cbf3e8d].txt

  The raw disc for this film is stored with The Rescuers (1977):
  ../../The Rescuers (1977) {tmdb-N}/raw/RESCUERS_35TH_ANNIVERSARY [bd-b8de7cbf3e8d]/
  This film is playlist title_71 on that disc (77:07). The disc's extras are
  filed with The Rescuers (1977).
```

Not a symlink. The cluster mounts the share over CIFS with no `mfsymlinks`
(`video-pv` mount options are just `rw`), so a symlink could only be made on the
fileserver itself, and it would not survive a copy to exFAT, a Windows client or
most backup tools. A text file reads the same everywhere, and the first film's
`disc.json` records both films for any program that needs it.

**A film split across discs** (case 4): concatenate the parts losslessly with
`mkvmerge` into one file, which every player handles, rather than relying on
`- part1/part2` stacking. Unverified: it needs Around the World disc 2.

### TV

```
shows/
  The Sylvester & Tweety Mysteries (1995) {tmdb-9867}/
    tvshow.nfo · poster.jpg · fanart.jpg · …
    Season 01/
      The Sylvester & Tweety Mysteries - S01E01 - The Cat Who Knew Too Much.mkv
      The Sylvester & Tweety Mysteries - S01E01 - The Cat Who Knew Too Much.nfo
      …
    extras/
    raw/
      SYLVESTER_TWEETY_MYSTERY_D1 [dvd-c27d6c87dc7dbea2]/
        THE SYLVESTER … DISC 1-B1_t00.mkv …    including the play-all
        disc.json
      SYLVESTER_TWEETY_MYSTERY_D2 [dvd-7da9a52fcee55723]/
```

The play-all's transcode is a leftover; its raw stays, because it is part of the
disc.

### Editions

Verified as one film (fingerprint), then named. The two conventions conflict:
Plex wants `{edition-Director's Cut}` at the end of the filename
(<https://support.plex.tv/articles/multiple-editions/>); Jellyfin wants each file
to begin with the folder name exactly, then a ` - Label`
(<https://jellyfin.org/docs/general/server/media/movies/>). The candidate is

```
Robin Hood (2010) {tmdb-N}/
  Robin Hood (2010) {tmdb-N} - Theatrical {edition-Theatrical}.mkv
  Robin Hood (2010) {tmdb-N} - Director's Cut {edition-Director's Cut}.mkv
```

plus a per-file NFO carrying `<edition>`. **This must be tested against Plex and
a throwaway Jellyfin before it is adopted** — it is phase 2's first task, and
Kodi's handling of versions should be checked at the same time.

### Sidecars and embedded metadata

For goal 4 and Will's portability requirement:

| what | why |
|---|---|
| NFO per movie, show and episode, Kodi schema, with TMDB and IMDb IDs | the one format Kodi, Jellyfin, Emby and tinyMediaManager all read; the durable record of the identification decision |
| artwork at Kodi-standard names (`poster`, `fanart`, `clearlogo`, `clearart`, `landscape`, `disc`, `season01-poster`) | read by all four; Plex can use it as a local asset |
| `{tmdb-N}` in folder names | Plex; it is the existing convention |
| inside each MKV: title and ID tags, track language/title/default/forced flags, chapter names | travels with the file through any rename |
| ignore markers in every `raw/`: `.plexignore` (exists), `.ignore`, `.nomedia` | keeps raw BDMV/VIDEO_TS out of every player — `.ignore` for Jellyfin and `.nomedia` for Kodi are believed, not verified |

---

## Phases

Each phase's done-condition is measured on the share or in Plex, not inferred
from the service's own report.

**0. Dry run.** Create the repository. Ship the ARM dispatcher and manifest
script; export manifests for jobs 1–41. The service runs analyse → plan over all
of `completed/` and **executes nothing**; its plans are compared row by row with
the fixtures table. Done when every row matches, or the table is corrected with
the evidence for why.

**1. Unambiguous movies.** Hallelujah Trail, Jaunty Jalopies, The Great Race,
Polar Express (including its raw pairing). Leftovers deleted. Done when each is
visible in Plex without a manual scan, `raw/` holds the right disc, and
`completed/movies/` holds nothing for those jobs.

**2. Review UI and the hard movie cases.** Notifications; the editions naming
test; Robin Hood (cases 2 and 5), The Rescuers (6), Field of Dreams (failed
jobs), Shrek the Third (9). Around the World waits for disc 2.

**3. TV.** The episode matcher; Tweety season 1 — 13 episodes, two play-alls, one
duplicate disc rip. Done when all 13 play in Plex as S01E01–E13 and a spot-check
of each opening matches its title.

**4. Case photo.** The insert-time link and vision extraction, used as evidence
by the phase 2–3 rules.

**Alerting**, in the spirit of "alert on the absence of recent success"
([docs/README.md](../docs/README.md)): a successful ARM job neither filed nor
awaiting review after 24 h; a review untouched for 7 days; and the service's own
liveness. The service exports metrics; the rules go in
`kubernetes/infrastructure/prometheus-rules.yaml`.

---

## Future work

Kept so it is not lost, each deliberately out of this project's scope:

- **The `unidentified/` + `raw/` backlog** — Will's stated next project. The same
  pipeline, with a human-supplied title where there is no manifest, and the 2024
  hand-filed films needing their raw reunited (e.g. the Alien and Dark Knight
  BDMVs).
- **Naming extras** (featurette, deleted scene, trailer…) into Plex's extras
  categories. The case photo already captures the disc's menu listing, so no
  information is lost by deferring.
- **Text subtitles by OCR** of the DVD (VobSub) and Blu-ray (PGS) image subtitles,
  kept alongside the originals. Image subtitles force many Plex clients to
  transcode video to burn them in; text subtitles also strengthen episode
  matching.
- **The `youtube-dl` and legacy `import/` buckets** as further sources. This is
  where name matching — FileBot's one strength — would matter; an LLM over names
  plus metadata may suffice.
- **Play-all filtering upstream in ARM**, using the sum-of-others rule — Will's
  suggestion for when transcode time matters.
- **DVD raw as ISO**, owned by [disc-ripping.md](disc-ripping.md).
- **Re-transcoding from raw**, which `disc.json` is designed to make possible.

---

## Open questions

None outstanding as of 2026-10-05. All were answered and moved to *Decisions*:
the tool's name (`reelbarrow`, chosen over `reeltoreel`, `dolly`, `filmferry`,
`charon` and `video-ingest` across four rounds — unclaimed on PyPI and GitHub
when checked), what to do when the film is already in the library (fold,
keeping the best of each), two films on one disc (split; raw and extras with
the first), how the second film points at the raw (a text file), and the LLM
(Claude, pay-as-you-go API key).

---

## Experiments

All read-only against the cluster and the share. Artefacts in
`captures/video-library-ingest/` (gitignored).

| # | date | what | result |
|---|---|---|---|
| E1 | 10-03 | inventory of `import/` from the filebot pod | backlog, duplicate rips, the size of `raw/` |
| E2 | 10-03 | read `makemkv.py`, `arm_ripper.py` | DVD raw is per-title MKV, not `VIDEO_TS` |
| E3 | 10-03 | survey of `/video/movies`, `/video/shows` | three conventions; 2024 `raw/` + `.plexignore` |
| E4 | 10-03 | FileBot `history.xml`, task args, shell history | TV renamed by hand before FileBot; raw moved by hand |
| E5 | 10-03 | `ffprobe` of Tweety and movie outputs | durations, chapters, embedded disc names |
| E6 | 10-03 | ARM `job` and `track` tables, read-only | `crc_id`, labels; the track table does not pair raw with transcode |
| E7 | 10-03 | Whisper `base.en`, 13 × 4-min Tweety clips | 13/13 identifiable; 32 s per 8 clips on CPU |
| E8 | 10-03 | TF-IDF vs Wikipedia synopses | 11/13 top-1; 13/13 one-to-one, thin margins |
| E9 | 10-03 | Plex library sections, read-only | modern agents only |
| E10 | 10-03 | research: FileBot forum, mkv-episode-matcher, TheDiscDB, FileBot pricing | FileBot cannot; TheDiscDB has no Tweety entry; OpenSubtitles limits |
| E11 | 10-03 | Blu-ray nav/completeness/`id.bdmv` hashes over 50 raw backups | nav hash is the identity; `id.bdmv` is not unique; Polar Express raw found misnamed |
| E12 | 10-03 | streams and chapters of the Rescuers pair | different length, crop, chapters, subtitle coverage |
| E13 | 10-03 | frames at 0:45 and 73:20 of the Rescuers pair | misread as a localised version — see E19 |
| E14 | 10-03 | survey of `unidentified/` and `raw/` | 154/180 empty; 101 non-empty raw, 2.14 TB |
| E15 | 10-03 | ARM notification and audio-handoff code | `BASH_SCRIPT` is the only hook; `$PPID` = `job.pid` from source |
| E16 | 10-03 | Plex GUIDs and poster sources | 91/96 online-matched, posters from the agent |
| E17 | 10-03 | Jellyfin naming docs; TMDB season page | `[tmdbid-…]` naming; TMDB missing 11/13 overviews |
| E18 | 10-04 | `zfs list` on vulcanus | `rpool/storage/media`: 96 snapshots, hourly current, back to 2021 |
| E19 | 10-04 | Chromaprint alignment: Robin Hood cuts, Rescuers pair, two controls | 94.9% vs 0.0% ×3; Whisper at 10:00/40:00 shows the Rescuers pair are two films |
| E20 | 10-04 | video-stream MD5 of Tweety D2, jobs 22 vs 33 | 6/6 identical, all renumbered |
| E21 | 10-04 | Plex editions and Jellyfin versions docs | the naming conflict in *Editions* |
| E22 | 10-04 | library vs ARM output | Shrek the Third already filed (case 9) |
| E23 | 10-05 | `video-pv` mount options; ARM track table for job 28 | CIFS without `mfsymlinks`; job 28's five long titles |
| E24 | 10-05 | Shrek ISO: label, pydvdid, `lsdvd`, decode test (copied locally, then deleted) | same disc content, decrypted superset of ARM's raw; CRC64 differs because the image re-stamped every file |
| E25 | 10-05 | streaming 7.2 GB through `kubectl exec` | 6 min 49 s, 17.6 MB/s — the service must read the share directly |
| E26 | 10-05 | Claude Code legal and authentication docs | OAuth is for ordinary use; services should use API keys; `claude setup-token` exists |
| E27 | 10-05 | Haiku 4.5 episode matching, titles only vs synopses | 8/13 (confidently wrong) vs 13/13 |

---

## Related

- [disc-ripping.md](disc-ripping.md) — produces what this consumes. Owns DVD-to-ISO,
  D12 (misidentification), the play-all observation, and the drift in
  `docs/automatic-ripping-machine.md` about `backup_dvd`.
- [smb-charset-utf8.md](smb-charset-utf8.md) — library names come from TMDB and
  will contain curly apostrophes and en dashes, which the share currently
  rejects with `EIO`. The service must apply the same Latin-1 folding as
  `arm-title-charset.sh` until that migration lands, and must not assume a
  failed write is anything but this.
- [config-change-rollouts.md](config-change-rollouts.md) — the ARM hook is a
  `subPath` mount.
- [backups.md](backups.md) — `media/video` is snapshotted and replicated, which is
  what makes deleting leftovers acceptable; deleted data still occupies space
  until the snapshots holding it expire (monthlies are kept two years).
