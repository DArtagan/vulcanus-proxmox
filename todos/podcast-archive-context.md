# Podcast archive — context and next steps

Working notes from the 2026-08-06 session that replaced Podgrab with Pinepods.
Intended as the starting context for a follow-up session.

## Why this exists

Two failure modes to guard against:

- **(a) Trimmed backlogs.** Shows that keep only the last N episodes live. This
  American Life serves 15 items; the archive holds 218. Episodes not captured
  while live are gone permanently.
- **(b) Dead feeds.** When a show goes offline the audio *and* the RSS disappear.
  Preserving the XML is what makes an archive re-hostable later.

These conflict: a feed that trims will eventually describe less than the archive
contains, so the RSS record has to be cumulative across snapshots.

## What was deployed

`kubernetes/apps/pinepods/` — Pinepods, Postgres 18, and Valkey as one pod,
following the `photoprism` sidecar pattern. Hand-rolled manifests, not the
upstream Helm chart (chart is pinned at `0.1.0` forever, ships `tag: latest`,
deploys three unwanted extra apps, and depends on archived `bitnamilegacy`
database images).

Plus an hourly CronJob (`podcast-feed-snapshot`) that preserves raw feed XML,
which Pinepods does not store.

### Layout on the `audio-rw` share

| Path | Purpose |
|---|---|
| `podcasts/` | Pinepods downloads — it owns this exclusively |
| `podcasts-legacy/` | The relocated 248.7 GB of pre-existing content |
| `podcasts-feeds/` | Raw RSS snapshots, one directory per feed |

`pg_dump` output goes to `pinepods-backups-pvc` on `openebs-hostpath`. It was
meant for the borg share so it would reach offsite storage, but that share is not
writable from Kubernetes — see `todos/backups.md`.

## Why Pinepods

The decisive property for an archive — that a feed refresh never deletes episodes
which have dropped off the remote feed — is undocumented, so it was verified by
reading the source:

- `refresh_rss_feed` → `apply_parsed_episodes` is insert/dedup only, no delete step
- `handlers/refresh.rs` contains no prune logic at all
- `FeedCutoffDays` is a **skip-on-insert filter**, default `0`, not a prune
- The only age-based deletion is `remove_old_youtube_videos`, called solely from
  `handlers/youtube.rs`
- Every `DELETE FROM Episodes` is `WHERE PodcastID = ?` — unsubscribe only

Also verified: per-podcast `username`/`password` auth via `fetch_feed_conditional`,
and a self-describing on-disk layout (per-show directories, `{date}_{title}_{user}_{id}.mp3`,
ID3v2.4 tags, JSON sidecars, cover art) — so the archive survives the tool.

Rejected: **Audiobookshelf** (`maxEpisodesToKeep` deletes by default), **Podgrab**
(discontinued, config only in a SQLite blob), **podcast-archiver** (solves strictly
less), **PodFetch** (no way to adopt an existing library), **feed-archiver**
(single maintainer). **FlexGet** is worth remembering — its `rss` plugin has the
best real basic-auth support of anything assessed, if exotic auth ever appears.

Empirically neutralised during research: RFC5005 feed pagination is worthless here —
zero of four representative feeds expose paging links.

## Current state

8 feeds subscribed, auto-download enabled on all, 855 episodes queued.
All 8 snapshot successfully.

| Podcast | ID | Episodes |
|---|---|---|
| Sawbones: A Marital Tour of Misguided Medicine | 7 | 590 |
| Old Gods of Appalachia | 8 | 140 |
| Dan Carlin Hardcore History Archives (private feed) | 9 | 58 |
| Norman Centuries | 5 | 20 |
| 12 Byzantine Rulers | 4 | 19 |
| This American Life | 2 | 15 |
| Dan Carlin's Hardcore History | 6 | 13 |
| The Properazzi Podcast | 3 | **0** |

The archival user's ID and API key are in the SOPS secret `pinepods`. The snapshot
CronJob reads the subscription list from `POST /api/data/backup_user` at runtime
rather than keeping a copy, so the two cannot drift and feed URLs never enter git.

Useful API endpoints, all under `/api/data` and needing an `Api-Key` header:
`backup_user` (OPML export), `import_opml`, `return_pods/{user_id}`,
`enable_auto_download`, `download_all_podcast`.

## Gotchas already hit — do not rediscover these

- **Feed credentials appear in the path, not just the query string.** One private
  feed embeds base64 `user:pass` as a path segment. Snapshot directory names are
  therefore `host + sha256[:12]`, never raw URL fragments, with the public show
  title in `title.txt` alongside. Any future code touching feed URLs must assume
  no part of the URL is safe to write down.
- **The snapshot job once reported success while doing nothing** — a renamed
  intermediate file meant it checked zero feeds and still exited 0. An absent feed
  list is now a hard error. Watch for this class of bug generally: for an archive,
  silent no-op is the worst failure mode.
- **OPML import silently drops unreachable feeds.** Pinepods returned success while
  discarding one. It was caught only by comparing the snapshot count against the
  expected number.
- **Relocate before deploying.** Pinepods runs a one-time recursive `chown` over
  its downloads directory, gated by a `.perms-migrated` marker. Against an empty
  directory it is instant; against 248 GB of CIFS it would not be.
- **`PUID`/`PGID` must be `0`** — the SMB shares present everything as root with no
  uid mapping. Matches `filebot`.
- **All six `DB_` variables are mandatory.** `validate_db.py` suggests defaults, but
  it is only a startup helper — `rust-api/src/config.rs` lists them in
  `db_required_vars` and `unwrap()`s each.

## Next steps, roughly in priority order

### 1. Legacy merge — the highest-value work

`podcasts-legacy/` holds 248.7 GB across ~three generations of tooling, and it
contains backlog the live feeds no longer offer (218 This American Life episodes
against a 15-item feed). Merging it is what makes the archive actually complete.

Complications:
- 34 show directories plus 267 loose `.mp3` files dumped at the tree root
- Several shows exist under two or three names (`This American Life` /
  `ThisAmericanLife`; `Sawbones` / `Sawbones- A Marital…`; `99pi` / `99% Invisible`;
  `HistoryOfByzantium` / `HistoryOfByzantiumAcast` / `…_backup`)
- Pinepods' local-media rescan is the intended mechanism but **has not been tested**.
  Try one show before turning it loose on the whole tree — the feature shipped in
  v0.9.0 (June 2026) and is the newest code in the stack.
- Expect duplication against `podcasts/`, since Pinepods re-downloaded whatever the
  live feeds still offer.

### 2. Four stray directories outside the archive

`Red Pilled America`, `The Properazzi Podcast`, `This American Life`, and
`Twenty Thousand Hertz` sit at the **root** of the `audio-rw` share, not inside
`podcasts-legacy/`. They are divergent copies of directories that also exist in the
legacy tree (Properazzi: 545 MB at root vs 525 MB in legacy). No Pinepods mount can
see them. That root namespace is shared with `music/` and `audiobooks/`, which Plex
mounts, so moving things there needs care.

### 3. Investigate The Properazzi Podcast

Subscribed with 0 episodes — its Spreaker feed resolves but returns nothing, while
525 MB of it sits in the legacy tree. Either the show was withdrawn or the feed URL
has changed.

### 4. Dan Carlin's Hardcore History Compilation is unrecoverable as a feed

`rss.dancarlin.com` no longer resolves in public DNS. Podgrab captured 5.1 GB across
59 episodes into the legacy tree, but the RSS was never snapshotted, so no
re-hostable feed can be reconstructed for it. A replacement feed on `dancarlin.com`
was subscribed separately (ID 9). This is requirement (b) failing in real time and
is worth remembering as motivation.

### 5. Cumulative feed generator

Build re-hostable per-show RSS from `podcasts-feeds/` snapshots plus Pinepods' JSON
sidecars, rewriting enclosure URLs to local paths. Partly obviated by Pinepods'
own per-podcast RSS serving, but still needed for shows whose feeds died before
capture.

### 6. Grow the feed list

The original goal was 20–100 feeds; only Podgrab's 8 have been migrated.
