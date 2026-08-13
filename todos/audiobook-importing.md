# Importing audiobooks

## Opening prompt

> The audiobook side of the beets library is half-finished. Roughly 440 GiB of
> audiobooks sit unimported in `/audio/import`, most of them absent from
> MusicBrainz entirely; the ones already in the library are marked with a genre
> rather than a release type, which is a field beets' own genre machinery is
> hostile to. Read `todos/audiobook-importing.md` and work through whichever of
> its four strands is most useful now — they are deliberately not sequential,
> though 4 feeds 1 and 2, and 3 wants 2 mostly done first.

## Where things stand (verified 2026-08-13)

The beets-flask deployment is live at `beets.immortalkeep.com` and working; see
[`docs/beets.md`](../docs/beets.md) for how it is put together. This spec is only
about the audiobook-specific work left over.

**The library** — 914 albums, 14,092 items.

| | Albums |
|---|---|
| Tagged by us, `genres:audiobook` | 66 |
| Flagged by MusicBrainz, `albumtypes:audiobook` | 38 |
| Ours but not MusicBrainz's | 33 |
| MusicBrainz's but not ours | 5 |

Of those 33, **every one has no `mb_albumid` at all**. None are cases of a
release existing in MusicBrainz without the audiobook secondary type, so strand
1 is about *adding* releases, not correcting them.

**The inbox** — `/audio/import`, 3,755 files, 443.5 GiB (beets-flask's own inbox
stats). Around 520 album folders; the retired CronJob's last run skipped 522 and
beets-flask enqueued 553 on first scan, the difference being how nested folders
are counted. By file type: 445 `.m4b`, 532 `.flac`/`.mp3` at depth ≤3.

**What the Audible rips carry.** Of 200 sampled `.m4b` files, 195 have a genre
tag and 136 of those contain a comma. The values are Audible marketing
categories, not genres: `Epic`, `Epic, Action & Adventure`,
`Action & Adventure, Contemporary, Humorous, Movie, TV & Video Game Tie-Ins`.
None appear in the 1,549-entry whitelist. They cannot be reliably split, because
`Movie, TV & Video Game Tie-Ins` is a single category containing a comma. Six of
the 200 are already tagged `Audiobook` with a capital A.

## Decisions already made

Recorded so they are not relitigated:

- **One field decides placement.** The user: *"there should be a single source of
  truth, therefore keep only `genres:audiobook` and don't also include
  `albumtypes:audiobook`."* That is why `paths` has no albumtypes rule today.
- **MusicBrainz is where the metadata should live.** The user: *"I'll be directly
  contributing to Musicbrainz itself, not maintaining tags for these out of band
  (only in my library)."* This is what makes strand 3 worth doing at all.
- **`genres` should eventually describe the book,** not merely mark it as one —
  see strand 3.

## 1. Get the 33 into MusicBrainz

They have no `mb_albumid`, so they were imported as-is. Find them with:

```bash
beet ls -a 'genres:audiobook ^mb_albumid::.'
```

Each needs a MusicBrainz release created, then `beet mbsync` to pick up the
identifiers and `albumtypes`. Note that submission is GUI work — see strand 4 for
why that is the awkward part.

## 2. Give every audiobook `albumtypes: audiobook`

For anything in MusicBrainz this is automatic: `beet mbsync` refreshes
`albumtypes` from the release, so strand 1 delivers this as a side effect.

Do **not** hand-set `albumtypes` on releases that are in MusicBrainz — mbsync
will overwrite it from the source, and the field would then be lying about its
provenance. Setting it locally is only defensible for releases MusicBrainz does
not have, and even then it becomes wrong the moment one is added.

The reconciliation query, which uses MusicBrainz as a *detector* without letting
it route anything:

```bash
beet ls -a 'albumtypes:audiobook ^genres:audiobook'
```

## 3. Move path routing to `albumtypes`, then free up `genres`

Today `kubernetes/apps/beets/config-map.yaml` routes on `genres:audiobook`, and a
local plugin (`beets-flask-plugin-audiobook-genre.py`) derives that genre from
`albumtypes` at import time. Both exist because `albumtypes` covered too few
albums to route on.

Once strand 2 has good coverage that inverts, and the reasons to switch are
strong:

- **`audiobook` is not a genre, and beets knows it.** It is in the flat whitelist
  but absent from beets' canonical genre tree (684 branches; `rock` and
  `new wave` are in it). Every lastgenre feature is therefore a hazard to it —
  the tree rejects it, `title_case` mangles it, `cleanup_existing` deletes it.
- **It is the only option that survives `mbsync` by construction.** With
  `musicbrainz.genres: yes`, mbsync replaces `genres` wholesale
  (`- Indie Rock  + indietronica  + new wave`), and mbsync emits no plugin events
  at all, so nothing can re-derive the marker afterwards. `albumtypes` is
  MusicBrainz's own field and mbsync keeps it correct.
- **It restores the user's own principle.** Once MusicBrainz is authoritative for
  audiobook-ness, `genres:audiobook` is the duplicate, not `albumtypes`.

Suggested sequence: add an `albumtypes:audiobook` rule alongside the existing
`genres:audiobook` one (both pointing at `audiobooks/`), let them run together
while coverage fills in, then delete the `genres` rule and the plugin.

**Afterwards**, `genres` is free to describe the *content* of the book — fantasy,
history, biography. At that point the Audible categories stop being junk to
discard and become raw material worth mining, and `lastgenre.count: 3` plus
`musicbrainz.genres: yes` become safe to enable.

Remember `beet move -a genres:audiobook` (or the albumtypes equivalent) to
re-file anything already misplaced. Run it from `beets-shell`. It is cheaper
than it looks: source and destination sit on the same CIFS mount and
`beets.util.move` tries `os.replace` first, so anything already under `/audio/`
relocates as a server-side rename rather than a copy. Only files crossing in
from elsewhere move bytes.

Both rules must point at `audiobooks/$author/$album%aunique{}/$track $title` —
`$author` is an `inline` computed field, not `$albumartist`. See
[`docs/beets.md`](../docs/beets.md).

## Upstream bugs that will shape this work

Found while importing the first handful on 2026-08-13. All are in
beets-flask v2.0.0-rc5 and none are reported upstream yet — worth doing, since
the first two will be hit repeatedly while working through the backlog.

- **A session that dies mid-import can only be repaired by hand.** A schema cycle
  (`task.chosen_candidate_id` ↔ `candidate.task_id`) means SQLAlchemy cannot
  order the deletes, so the delete endpoint, retag and undo all fail with
  `CircularDependencyError`. Recovery procedure is in
  [`docs/beets.md`](../docs/beets.md). A failed undo can also leave a *second*
  session for the same folder, which is what makes the UI offer contradictory
  advice.
- **The rq job timeout is hardcoded at 600 s** in
  `backend/beets_flask/redis.py`, with no configuration for it. This is why
  ReplayGain had to move out of the import path; anything else slow enough will
  hit the same wall, and the failure looks like a stuck session rather than a
  timeout.
- **The documented `requirements.txt` plugin mechanism does not work.**
  `entrypoint_user_scripts.sh` installs with bare `pip`, which in v2 is
  `/usr/local/bin/pip` writing to the system site-packages rather than the
  application's `/venv`. It reports success and changes nothing. Use
  `startup.sh` with an explicit `uv pip install`, as the polars workaround does.
- **`docs/plugins.md` upstream still says the image is Alpine** and tells you to
  use `apk`. v2 is `python:3.12-slim`.

## Duplicate copies in the inbox

Two of the first albums imported turned out to have a second, unrelated copy
sitting elsewhere in `/audio/import`:

```
Dawnshard   Cosmere/Rosharan/Dawnshard, The Stormlight Archive # [B0B75NY8F2]   imported
            Brandon Sanderson/Dawnshard - Stormlight Archive                    untouched
Arcanum     Cosmere/Arcanum Unbounded [B01K5Q6VWO]                              imported
            Arcanum Unbounded - The Cosmere Collection                          untouched
```

Two out of the first handful suggests there are more. Worth enumerating them
before working through the backlog rather than discovering each one as a
duplicate prompt mid-import — `import.duplicate_action: ask` will stop and ask
every time.

## 4. A workflow for the inbox backlog

The hard part, and the one with no good tooling.

Around 445 `.m4b` files, most absent from MusicBrainz. Creating a release is
manual web work — MusicBrainz Picard or the release editor — and neither runs in
the cluster. `mbsubmit` is enabled and offers `p` (print tracks in a submittable
format) and `o` (open files in Picard) at the beets CLI prompt, plus a
`beet mbsubmit` command; only the first is useful headlessly.

So the loop needs designing, not just running. Things worth weighing:

- beets-flask's `import_terminal` action sends a literal `beet import -t <paths>`
  into its tmux pane, so `mbsubmit`'s prompt choices are available there.
- The `edit` plugin would allow setting fields mid-import from that same
  terminal. It is deliberately not enabled — it does nothing for the GUI import
  path, and was judged not worth it while `beet modify` covers the same ground
  afterwards. Revisit if this strand ends up living in the terminal.
- Batching matters. Upstream documents frontend lag past a few hundred inbox
  folders (issues #164, #175), and this inbox is at that scale.
- Imports are copies, so the inbox never drains itself. beets-flask's
  `delete_imported_folders` action clears what has landed.

## Things already tried that did not work

- **`lastgenre.cleanup_existing: yes`** looked like the clean way to strip the
  Audible category strings through the whitelist. It strips `audiobook` too, on
  every album — because that value is not in the canonical genre tree. With
  `prefer_specific: no` it survives but gets title-cased to `Audiobook`, and some
  albums still empty out entirely. Do not enable it while routing depends on the
  genre.
- **Splitting the Audible categories on commas** is not recoverable;
  `Movie, TV & Video Game Tie-Ins` is one category containing a comma.
- **Routing on `albumtypes` was rejected once already**, on single-source-of-truth
  grounds, when it covered 35 albums against 66. That reasoning was sound at the
  time and is recorded above; strand 3 exists because the premise changed, not
  because the reasoning was wrong.
- **A `hook` plugin** to derive the genre was considered and rejected in favour of
  the local plugin: `hook` can only run shell commands, would mean invoking
  `beet modify` re-entrantly against a library the importer holds open, and fires
  on `album_imported` — after files are placed, so it would force a second move.
