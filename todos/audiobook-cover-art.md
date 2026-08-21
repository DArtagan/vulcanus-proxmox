# A unified system for cover art

## Opening prompt

> The audiobook library has no cover art in beets at all — `artpath` is empty on
> every album — while 95% of the inbox carries art embedded in the file that
> nothing currently looks at. Read `todos/audiobook-cover-art.md` and design a
> system that sources art well, stores it where the other consumers expect it,
> pushes it to MusicBrainz where that is useful, and can be re-run later when a
> better source appears.

## What this has to satisfy

The user's four requirements, verbatim:

1. **Well sourced** — art should come from the best available place, and it
   should be knowable *which* place it came from.
2. **Standard-compliant locations** — where other software expects to find it.
3. **Uploaded to MusicBrainz when useful.**
4. **Local records can be updated in the future if higher quality sources become
   available.**

Requirement 4 is the one that shapes the design, and it is not hypothetical —
see the Teresa case below.

## Where things stand (verified 2026-08-21)

**The library has no art.** `artpath` is empty for all five *Everybody Loves
Large Chests* albums, which are the only audiobooks checked but are unlikely to
be special.

**The inbox has plenty**, across 484 books:

| | Books |
|---|---|
| Embedded art *and* a sidecar image | 447 |
| Embedded art only | 15 |
| Sidecar image only | 0 |
| No art anywhere | 22 |

Embedded art is a median 553 KiB, min 45 KiB, max 1.9 MiB; 461 JPEG, 2 PNG.
Sidecar images are named after the book, not `cover.jpg`
(`Patriot Games [B004KAPD4Y].jpg`).

**`fetchart` is enabled and entirely unconfigured**, so it runs on defaults:

```
auto         True
sources      ['filesystem', 'coverart', 'itunes', 'amazon', 'albumart', 'cover_art_url']
cover_names  ['cover', 'front', 'art', 'album', 'folder']
cautious     False
store_source False
```

**`fetchart` has no embedded-art source.** Its full source list is AlbumArt.org,
Amazon, Cover Art Archive, Cover Art URL, Filesystem, Google Images, Last.fm,
Spotify, Wikipedia, fanart.tv, iTunes Store. The art inside the `.m4b` is
invisible to it. That is the whole reason `artpath` is empty: `filesystem` found
no image in the folders that had none, the Cover Art Archive had nothing for
these releases, and the commercial sources do not carry LitRPG audiobooks.

**`embedart` is importable but not enabled.** It provides `beet extractart`,
which writes embedded art out to a file. ⚠️ **`embedart.auto` defaults to `yes`**,
which embeds art *into* files on import — rewriting `.m4b` files across 440 GiB
of CIFS. It must be set to `no` explicitly before the plugin is enabled.

**The Teresa case, which is requirement 4 in miniature.** `Teresa: Everybody
Loves Large Chests (Vol.5)` *does* have art in the Cover Art Archive today
(`coverartarchive.org/release/d1cee977-…` returns 307), and its `artpath` is
still empty — because `fetchart` ran at import time, before the art existed.
Nothing re-checks. Goroth returns 404 and has no CAA art at all yet.

**The Cover Art Archive has no upload API and its form takes no seeding.** The
website is the only route. `mb-seed` already does what can be done: extracts the
art, saves it to a real file, and links to
`musicbrainz.org/release/<mbid>/add-cover-art`.

## Design questions this needs to answer

- **What is the source order?** CAA is the most durable and shared, but is empty
  for nearly all of these releases until we upload it — and what we would upload
  *is* the embedded art. So the embedded art is the origin, and CAA becomes
  authoritative only after a round trip. Does the system prefer CAA once present,
  or keep the local original?
- **Where does the file go?** `cover.jpg` beside the audio is what Plex, Jellyfin
  and Kodi read, and beets' `art_filename` defaults to `cover`. Confirm against
  the two consumers that actually mount this tree: Plex
  (`kubernetes/apps/plex/deployment.yaml:35`) and podbook
  (`kubernetes/apps/podbook/deployment.yaml:40`).
- **`fetchart.store_source: yes` looks like the key to requirement 4.** It records
  which source art came from in an `art_source` flexible field, which is what
  makes "re-run only where the source was weak" expressible. It is off today.
  Note the field would live in `library.blb`, which is not backed up.
- **What counts as higher quality?** `minwidth` and `enforce_ratio` are the
  levers. A 45 KiB embedded cover is probably worse than anything CAA would hold;
  a 1.9 MiB one probably is not.
- **What re-runs it, and when?** `beet fetchart -f` forces a refetch, but
  something has to decide to run it. This is the same shape as the alerting
  problem in `CLAUDE.md`: a check that only pays off later needs to run without
  anyone remembering.
- **The 22 books with no art at all** need a source or an accepted gap.

## Constraints

- **Do not rewrite the audio files.** 440 GiB over CIFS, and the embedded art is
  already there; extracting is cheap, embedding is not.
- **A ConfigMap change does not reach the running process.** See
  [`todos/config-change-rollouts.md`](config-change-rollouts.md) and
  `docs/kubernetes.md` — verify at the pod, not at Flux.
- **Writing to `library.blb` needs beets-flask scaled to zero.** Use
  `beets-shell`, which does it automatically.
- **Whatever lands in `docs/beets.md`** should record why the embedded art needed
  a route of its own, so nobody re-derives that `fetchart` can read it.

## Related

`mb-seed` ([`tools/mb-seed/`](../tools/mb-seed/)) already handles the MusicBrainz
half of requirement 3. It records per book whether art is embedded, its mime type
and size, and any sidecar filenames — so the manifest it generates is a ready
inventory for whatever this becomes.
