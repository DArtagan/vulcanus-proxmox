# Get ripped video out of `import/` and into the library

## Opening prompt

> A ripped DVD lands in `media/video/import/automatic-ripping-machine/completed/`
> and stops there. Nothing watches that directory, Plex cannot see it, and the
> one tool that could file it — FileBot — reports `Bad License` and refuses to
> rename. Read `todos/video-library-ingest.md`: the asymmetry with the audio
> path, what FileBot can and cannot do, and three routes with the naming traps
> each has to survive. The first decision is whether to renew the FileBot licence
> or write our own mover, and that is Will's.

Verified **2026-09-01**.

## The asymmetry

Audio is wired end to end and proven twice: ARM writes to `audio-rw/import`,
beets-flask watches it, the album reaches the library without anyone touching it.

Video dead-ends.

| | audio | video |
|---|---|---|
| ARM writes to | `audio-rw/import` | `media/video/import/automatic-ripping-machine/completed/` |
| watched by | beets-flask, 30s debounce | **nothing** |
| reaches library | automatically | never |

Plex mounts only `movies`, `shows`, `music` and `audiobooks` as read-only
subPaths (`kubernetes/apps/plex/deployment.yaml`), so it cannot see `import/` at
all. **filebot** and **media-toolkit-webtop** are the only workloads that mount
the whole video share and can see both sides; both are manual UIs.

Will's framing on 2026-08-24 was that downstream cataloguing is *"mostly outside
the scope of this project, unless there's opportunity for radical improvement"*.
The catch is that for video there is no downstream system to be outside the
scope — "processed later by other systems" does not happen. He confirmed on
2026-09-01 that this is the improvement worth having, once the phases produced a
file worth filing. One now exists.

## What is waiting

```
/video/import/automatic-ripping-machine/completed/movies/
  The-Hallelujah-Trail (1965)_178824223068/The-Hallelujah-Trail (1965).mkv   1.29 GB

/video/import/automatic-ripping-machine/completed/tv/
  The-Sylvester-and-Tweety-Mysteries (1995-2002)/               disc 1: title_0 … title_8
  The-Sylvester-and-Tweety-Mysteries (1995-2002)_178840975618/  disc 2: title_0 … title_5
```

Plus **8 empty directories** in that same tree, left by failed jobs — see the
naming traps below, they are not merely untidy.

### The TV tree is harder than the movie tree, and measurably so

Two discs of one season have now been ripped, and between them they rule out
every mapping that does not look at the video itself.

**Within a disc, `title_N` is not episode N.** Disc 1 holds nine titles, of which
`title_1` is a 168-minute "play all" compilation of the other eight. So the
numbering runs *episode, compilation, episode, episode…* — a mover assuming
position files seven of eight episodes wrongly and files a 2h48m compilation as a
21-minute one.

The compilation is at least *detectable* without content matching: its length
matches the sum of the other titles to within a tenth of a minute on both discs
(168.8 vs 168.9; 105.7 vs 105.8), and it is ~8× any single episode. That is a
reliable enough signal to drop it, and dropping it would also save around 40% of
each disc's transcode time. It does not, however, help identify what remains.

**Across discs, the same filename means different episodes.** Disc 2 did not join
disc 1's folder — `check_for_dupe_folder` gave it a sibling directory suffixed
with the job's stage number — and it numbers from `title_0` again. The season
therefore has two `title_0.mkv`, two `title_1.mkv` and so on, in directories
distinguished only by an opaque integer that records *when the job ran*, not which
disc it was. Re-ripping disc 1 tomorrow would sort it after disc 2.

The disc ordinal survives only in the label on the job row —
`SYLVESTER_TWEETY_MYSTERY_D1` and `..._D2` — which is a convention of one
publisher, not something to build on.

**Conclusion: content matching is the only thing that can file TV.** For a film,
ARM's own metadata is enough — it knows the title, the year and which file is the
main feature. For a series it knows the show and nothing else, and no amount of
filesystem or database inspection recovers the episode numbers. This is the
strongest argument for resolving the FileBot licence question below, because the
alternative is a person watching the opening of each file.

## FileBot cannot do this today

`rednoah/filebot:node`, FileBot **5.1.2**. A licence file exists at
`/data/license.txt` (673 bytes, dated 2024-02-04) and FileBot rejects it:

```
$ filebot -rename --action test -r "…/The-Hallelujah-Trail (1965)_178824223068" --db TheMovieDB --format "{plex}"
FileBot requires a valid license. Please run `filebot --license *.psm` to install your FileBot license.
* FileBot is running as [root] using [/data/license.txt]
Bad License (>_<)
```

FileBot has been commercial since 4.9, and 5.x refuses rename and move without a
valid licence. So the obvious tool is **unavailable until someone buys or renews
a licence**, and that is a purchase decision, not an engineering one. It also
explains why this has stayed manual: the tool nominally there for the job has
not worked, and nothing said so.

Its `history.xml` was last written 2024-08-05, which is consistent with it having
worked once and lapsed.

## Naming traps any route must survive

**The source directory name is wrong and the file name is right.** ARM's
`check_for_dupe_folder` appends the job's stage id when the tidy name is taken:

```
completed/movies/The-Hallelujah-Trail (1965)/                  <- empty, from a failed job
completed/movies/The-Hallelujah-Trail (1965)_178822432143/     <- empty, from a failed job
completed/movies/The-Hallelujah-Trail (1965)_178823907384/     <- empty, from a failed job
completed/movies/The-Hallelujah-Trail (1965)_178824223068/     <- the real one
        └── The-Hallelujah-Trail (1965).mkv
```

A mover keying on the **directory** name files this as
`The-Hallelujah-Trail (1965)_178824223068`. Keying on the **file** name is
correct here — but that only holds because ARM renames the main title to
`<Title> (<Year>).mkv`. It does not hold for extras, which stay `title_N.mkv`
under an `extras/` subdirectory.

**The existing library's convention**, which anything filed must match:

```
/video/movies/A Bug's Life (1998)/…          Title (Year)
/video/shows/Frontline {tmdb-4384}/…         Title {tmdb-id} where disambiguation is needed
```

Note `/video/movies/` also contains stragglers that do not follow it —
`2022 Laguna Seca`, a bare `GOPR3003.MP4` — so the convention is aspirational in
places and a mover should not assume every existing entry parses.

**TV is unsolved and worse, and the source says why.** For a movie,
`skip_transcode_movie` keeps the largest title and renames it
`<Title> (<Year>).mkv`. For a series, `move_files_post` (`arm_ripper.py:198`)
moves **every** track with `is_main_feature=False`, and `move_files`
(`utils.py:210`) flattens them into one directory because "for series there are
no extras". Nothing is the main feature, so nothing is renamed:

```
completed/tv/<Show Title>/title_0.mkv
completed/tv/<Show Title>/title_1.mkv   ← no episode identity anywhere
```

ARM knows the *show* and cannot know which title is which episode. So a mover
built on ARM's own metadata — which is sufficient for a film — **cannot file
TV**. Only content matching can, which is FileBot's whole purpose. That is the
strongest argument for route A, and it is why the recommendation defers rather
than dismisses the licence.

**Measured on a real disc, 2026-09-03**, and it is worse than "no episode
numbers". Job 21 produced nine files:

```
title_0.mkv   21m    239 MB   ← episode
title_1.mkv  168m   1.88 GB   ← "play all": every episode concatenated
title_2.mkv   21m    215 MB   ← episode
…            21m             ← episodes through title_8
```

So the numbering is *episode, compilation, episode, episode…*. **Positional
mapping is not fragile, it is wrong** — it would file seven of the eight
episodes under the wrong number and a 2h48m compilation as a 21-minute episode.
And a mover cannot detect the compilation by name or by size ratio alone without
essentially reimplementing content matching.

A heuristic does exist — a play-all's length is close to the sum of the others,
and ARM's `track` table holds every length — but it identifies *which file to
skip*, not *which episode each remaining file is*. That second half is the one
that matters, and only FileBot answers it.

Note also the destination differs: `convert_job_type` returns `"tv"`, while the
library is `/video/shows/`.

## Three routes

**A. Renew the FileBot licence and automate it.** FileBot's `amc` script is
purpose-built: it matches against TheMovieDB/TheTVDB, renames to a Plex-shaped
format, and moves. It reads the *file*, so the `_<stage>` directory suffix does
not mislead it, and it is the only option that has a credible answer for TV.
Costs a licence, and puts the pipeline's last mile behind a third-party paid
dependency that has already failed silently once.

**B. Write a mover, like the audio handoff.** `arm-audio-handoff.sh` is the
precedent and it works. For **movies only** the job is small: ARM has already
identified the film, so `job.title`, `job.year` and `job.imdb_id` are in the
database and the destination name can be built from them rather than re-derived
from the filesystem. No licence, no external lookup, no third party.

It does not solve TV, and it means ARM's identification is load-bearing — a
misidentified disc files a film under the wrong name, where FileBot would have
matched on content. Given `MANUAL_WAIT` exists precisely to correct
identification, that may be acceptable.

**C. Leave it manual.** Honest status quo. Reasonable while the volume is a
handful of discs, and it is what happens today.

**A hybrid is probably right:** B for movies, since ARM already knows what the
film is, and revisit A when TV discs are actually being ripped. That defers the
licence question until something needs it.

## Plex will not notice either way

ARM has `EMBY_REFRESH: false` and no Plex integration at all — no token, no
server URL. Whatever files the media, Plex needs a library scan afterwards or the
film sits on disk unlisted. Plex's API takes a token; that is a credential and
belongs in SOPS. `kubernetes/apps/homepage/secret.yaml` already holds a Plex
key for its widget, so the pattern and possibly the value exist.

## Verification

The film above is the fixture. Done means:

1. `ls "/video/movies/The Hallelujah Trail (1965)"` exists with the `.mkv` in it.
2. The `import/` copy is gone, or deliberately kept — **decide which**. `beets`
   keeps its inbox copy (`import.move: no`), and the raw `VIDEO_TS` backup is
   the archival copy regardless, so deleting the transcoded copy from `import/`
   loses nothing that matters.
3. Plex lists it without a manual scan.
4. The 8 empty directories are gone, and re-running does not recreate them.

## Related

- [disc-ripping.md](disc-ripping.md) — produces the files this consumes, and
  records why the directory suffix exists.
- [backups.md](backups.md) — `media/video` is covered by ZFS replication and the
  Proxmox backup, so moving files between directories on the same share does not
  change what is protected.
