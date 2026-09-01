# Importing audiobooks

## Opening prompt

> The audiobook backlog is mid-flight. `mb-seed` is built and the three-book
> pilot is submitted and waiting out MusicBrainz's voting period. Read
> `todos/audiobook-importing.md`, check whether the pilot survived review, and
> if it did, work Phase 2 — the 31 library albums that have no MusicBrainz
> release, which is the single gate on everything after it.

## Where things stand (verified 2026-08-21)

**The library** — 950 albums.

| | Albums |
|---|---|
| `genres:audiobook` | 107 |
| `albumtypes:audiobook` | 76 |
| `genres` but not `albumtypes` | **31** |
| `albumtypes` but not `genres` | 0 |
| Ours with no `mb_albumid` at all | **31** |

Those two 31s are the same 31 albums. Every album missing `albumtypes` is
exactly an album with no MusicBrainz release, which makes the gate on Phase 3
precise: create 31 releases, `mbsync`, and coverage goes to 107 of 107.

**The inbox** — `/audio/import`, 484 `.m4b`, 130 top-level folders, 392 GiB.

The 2026-08-13 figures in earlier versions of this spec counted differently and
were misleading. Corrected:

| Earlier claim | Actually |
|---|---|
| ~520 mixed album folders | 479 folders holding exactly one `.m4b` (475 of them; 4 hold 2–3) |
| "532 `.flac`/`.mp3` at depth ≤3" | a *file* count. It is 26 folders, ~8 of which are audiobooks too |
| duplicate copies worth enumerating | **zero duplicate ASINs** across all 444. Dawnshard and Arcanum have left the inbox |

**What the rips carry.** Not just the Audible marketing genres this spec used to
describe — a full tone/m4b-tool tag set, on 444 of 485 files:

```
aART  Eden Hudson                       ©nrt  Travis Baldree
©alb  Death Cultivator (Unabridged)     rldt  11-Oct-2020
----:com.pilabor.tone:AUDIBLE_ASIN / SERIES / PART / PUBLISHER / LANGUAGE
```

They are `com.pilabor.tone` atoms, not `com.apple.iTunes`, so `mediafile`'s
built-in `asin` field reads `None` against them. `albumartist` is the bare author
on 442 of 464 readable files: 8 lack `aART` entirely (the author is in `©ART`)
and 14 carry translator or illustrator noise, one listing the translator first.

**Why the releases must be created rather than found.** Fifteen ASINs sampled
against the MusicBrainz web service returned **0 hits by ASIN** and 4 by
title+artist. Where MusicBrainz holds the book it models it as Audible parts or a
CD rip — `We Are Legion` 61 tracks, `The Hunt for Red October` 99, `Academ's
Fury` 17 CDs — against our single file. beets-flask's own stored previews:

| best-candidate distance | tasks |
|---|---|
| ≥ 0.50 | **521** |
| 0.30–0.50 | 64 |
| 0.15–0.30 | 17 |
| no candidate | 8 |
| < 0.04 (auto) | **2** |

`tracks` is penalised on all 521. Searching harder cannot fix this.

## What is already built

**[`tools/mb-seed/`](../tools/mb-seed/)** — run as `mb-seed` from the devenv
shell; `mb-seed Cradle` narrows the queue by substring, which is how a batch is
picked. It regenerates a manifest inside the cluster, serves a loopback review
queue, hands each book to MusicBrainz's release editor with every field
pre-filled, captures the resulting MBID into `ledger.json`, and then offers the
book's cover art with a link to that release's upload page. 111 tests,
`python3 -m unittest discover -s tools/mb-seed`. Its README carries the design
rationale; read it before changing the seeding.

## Decisions already made

Recorded so they are not relitigated. The first three predate this phase.

- **One field decides placement.** The user: *"there should be a single source of
  truth."* Originally that meant keeping only `genres:audiobook`. It now means
  `albumtypes` alone — see the next entry.
- **MusicBrainz is where the metadata should live.** The user: *"I'll be directly
  contributing to Musicbrainz itself, not maintaining tags for these out of band
  (only in my library)."* Reaffirmed on 2026-08-21 after being shown the measured
  cost, so MusicBrainz stays on the critical path.
- **`genres` should eventually describe the book,** not merely mark it as one.
- **Route on `albumtypes` alone**, not on both fields during a transition — but
  only once every existing audiobook carries it, and by syncing it from
  MusicBrainz rather than setting it locally. The user: *"it would be even better
  if they were marked as such in musicbrainz and then we just synced against
  what's there."* That is why Phase 2 gates Phase 3.
- **Import through the beets-flask web UI**, keeping per-folder session state and
  `delete_imported_folders`. Chosen knowing beets-flask can only copy, so this
  writes 392 GiB of duplicate bytes over CIFS and the inbox drains by hand.
- **One track per release** for the single-file books. This is what closes the
  loop: beets matches the one `.m4b` cleanly and `mbsync` stays correct. The
  releases already in MusicBrainz for this library use one track.
- **Every narrator is credited**, as `author narrated by A, B & C`. Matches the
  official guideline and the nine releases already here.

## Phases

### Phase 1 — pilot ✅ submitted 2026-08-21, awaiting review

`Goroth`, `Stain` and `Jackson` (*Everybody Loves Large Chests* 7–9). Goroth is
imported and correct: `$author` resolved to `Neven Iliev` from `albumartists[0]`,
filed at `audiobooks/Neven Iliev/…`, `albumtypes: other; audiobook`.

**Check this first.** MusicBrainz edits sit in a seven-day voting period, so from
about **2026-08-28** it is knowable whether the one-track model and the seeded
metadata survived. If they did not, the model for all 479 changes and anything
already submitted needs revisiting.

```sh
# ledger.json holds path -> MBID for what has been submitted
https://musicbrainz.org/release/93d306d6-a23a-4bec-bcb3-3098f8f25ac7  # Goroth
https://musicbrainz.org/release/5478966d-d1c7-43cd-a8e8-3554dbc55d3f  # Stain
https://musicbrainz.org/release/a3f6281e-fb92-43ee-ba8b-8575304f4e3f  # Jackson
```

Note the pilot books are the *rarest* shape in the collection — full-cast
productions with 5, 9 and 9 narrators, and no ASIN. 423 of 485 books have exactly
one narrator. They stress-tested the credit logic but are not representative.

### Phase 2 — the 31 library albums, which gates Phase 3

```sh
beet ls -a 'genres:audiobook' '^mb_albumid::.'
```

They are *multi-track* — 782 tracks for `Judas Unchained`, 727 for `Pandora's
Star`, 493, 475, 107 — so they exercise a seeding path the single-file backlog
does not. Model them as CD mediums, as `Academ's Fury` already is.

`mb-seed` reads `/audio/import` and these are in the library, so it needs either
a second root or hand-seeding. Decide which before starting.

Then `beet mbsync` from `beets-shell`, and `genres:audiobook ^albumtypes:audiobook`
must reach empty.

**Risk:** a 782-track seed across ~40 mediums is a large POST and is untested.
Try the largest one early; fall back to a skeleton release plus a second edit.

### Phase 3 — switch routing to `albumtypes`

Only once Phase 2 leaves that query empty. In
`kubernetes/apps/beets/config-map.yaml`: replace the `genres:audiobook` path rule
with `albumtypes:audiobook` pointing at the same
`audiobooks/$author/$album%aunique{}/$track $title`, keep it above `default`, drop
`audiobook_genre` from the plugin list and delete its ConfigMap key, and drop the
matching `config-seed` line in `deployment.yaml`. Then from `beets-shell`:

```sh
beet move -a albumtypes:audiobook
beet move 'albumtypes:audiobook' singleton:true   # -a is blind to singletons
```

### Phase 4 — the backlog

Batches of ~25 folders. `mb-seed <substring>`, submit, import through the UI,
`delete_imported_folders`, confirm `albumtypes:audiobook` rose by the batch size.

Left to the end: the 41 `.m4b` with no ASIN; the 22 with a missing or noisy
`albumartist`, fixed in MusicBrainz rather than as a local `author` override so
the fix is not lost with `library.blb`; and the ~8 audiobooks among the 26
mp3/flac folders. The other ~18 folders plus 2 m4a folders are ordinary music.

## Costs discovered in the pilot

- **Narrator names need correcting per book.** The tags say `Will Watt` and
  `Justin James` where MusicBrainz has `Will M. Watt` and `Justin Thomas James`,
  and the release editor will happily create a duplicate artist rather than link
  the existing one. Watch the artist fields on every submission. If this proves
  common across the backlog, a narrator alias file — like the `authors.toml` in
  [`book-import-spec.md`](book-import-spec.md) — is the fix.
- **`mb-seed` must be restarted to pick up code changes**, and the manifest goes
  stale as books leave the inbox.

## Upstream bugs that will shape this work

All in beets-flask v2.0.0-rc5, none reported upstream yet. The first two will be
hit repeatedly.

- **A session that dies mid-import can only be repaired by hand.** A schema cycle
  (`task.chosen_candidate_id` ↔ `candidate.task_id`) means SQLAlchemy cannot
  order the deletes, so delete, retag and undo all fail with
  `CircularDependencyError`. Recovery is in [`docs/beets.md`](../docs/beets.md).
  Expect to do it at least once across 479 imports.
- **The rq job timeout is hardcoded at 600 s** in `backend/beets_flask/redis.py`.
  Not a factor while `replaygain.auto` is off, but it is why nothing slow may be
  added to the import path.
- **The documented `requirements.txt` plugin mechanism does not work.** Use
  `startup.sh` with an explicit `uv pip install`.
- **`docs/plugins.md` upstream still says the image is Alpine.** v2 is
  `python:3.12-slim`.

## Things already tried that did not work

- **`lastgenre.cleanup_existing: yes`** strips `audiobook` too, on every album,
  because that value is not in beets' canonical genre tree.
- **Splitting the Audible categories on commas** is not recoverable;
  `Movie, TV & Video Game Tie-Ins` is one category containing a comma.
- **A `hook` plugin** to derive the genre: `hook` can only run shell commands,
  would invoke `beet modify` re-entrantly against a library the importer holds
  open, and fires on `album_imported` — after files are placed.
- **Matching the backlog against MusicBrainz as it stands.** The 521-of-612
  distance measurement above is what settled it.
- **`urls._x_.link_type` in the seed.** The integer ID is not published anywhere
  reachable; the parameter is optional, so the editor's dropdown is used instead.
- **Carrying cover art in the manifest.** 463 covers at a median 553 KiB is
  250 MB over a `kubectl exec` pipe that truncates silently. Fetched on demand.

## Related

- [`audiobook-cover-art.md`](audiobook-cover-art.md) — `artpath` is empty on every
  audiobook; `fetchart` has no embedded-art source.
- [`config-change-rollouts.md`](config-change-rollouts.md) — Phase 3's ConfigMap
  change does not reach the running process on its own.
- [`backups.md`](backups.md) — `library.blb` holds every tagging decision since
  2022 and is not backed up. Snapshot before each phase; ad-hoc copies from
  2026-08-21 are in `~/backups/beets/`.
