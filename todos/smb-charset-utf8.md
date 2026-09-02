# The fileserver cannot store a filename with a curly apostrophe

## Opening prompt

> Samba is configured `unix charset = ISO-8859-1`, so any filename containing a
> character above U+00FF fails to write with `EIO` on every share. It blocks ARM
> from filing a TV series whose year OMDb returns as `1995–2002`, and it will
> block any title with a curly apostrophe, en dash or ellipsis. Read
> `todos/smb-charset-utf8.md`: the boundary is measured, and the migration is
> the hard part — 150 existing filenames are stored as Latin-1 bytes and would
> become invalid UTF-8, while beets, Plex, photoprism and Stump all hold those
> byte paths in their databases.

Verified **2026-09-02**.

## The defect

`ansible/templates/smb.conf.j2:7`, confirmed live on the fileserver with
`testparm -s` against Samba 4.15.13:

```
   dos charset = cp850
   unix charset = ISO-8859-1
```

`unix charset` tells Samba what encoding the server's own filesystem uses.
Set to ISO-8859-1, any character with no Latin-1 representation cannot be
converted, and the failure reaches the CIFS client as `EIO`.

Measured from inside a pod, creating directories on two different shares:

| character | video share | audio share |
|---|---|---|
| ASCII | OK | OK |
| `à` U+00E0 | OK | OK |
| `é` U+00E9 | OK | OK |
| `–` en dash U+2013 | **EIO** | **EIO** |
| `—` em dash U+2014 | **EIO** | **EIO** |
| `’` right quote U+2019 | **EIO** | **EIO** |
| `…` ellipsis U+2026 | **EIO** | **EIO** |
| `中` U+4E2D | **EIO** | **EIO** |

The boundary is exactly Latin-1, and it is server-side — both shares behave
identically, and the CIFS mounts specify no `iocharset`.

**This is why `Mànran` worked and `1995–2002` does not.** `à` is Latin-1; an en
dash is not. The one non-ASCII name this project has handled successfully was
inside the range by luck.

## What it costs

**It blocks ARM now.** Job 20 (The Sylvester and Tweety Mysteries) failed with

```
OSError: [Errno 5] Input/output error:
  '/root/video/transcode/tv/The-Sylvester-and-Tweety-Mysteries (1995–2002)'
```

OMDb returns the year range for a series with an en dash, ARM builds the path
from it, and the write fails. Phase 2b of [disc-ripping.md](disc-ripping.md) is
blocked until this is fixed or worked around.

**It is latent everywhere else.** A curly apostrophe is ordinary in titles —
*Ocean's Eleven*, *Bridget Jones's Diary* — and any tool that takes a name from
a metadata provider rather than a keyboard will eventually produce one. beets,
Plex, photoprism and Stump all write names they did not choose.

## The migration is the hard part

Changing `unix charset` to `UTF-8` is one line. Doing it safely is not, because
**existing non-ASCII filenames are stored as Latin-1 bytes** and would be
reinterpreted as UTF-8 — where they are invalid.

Counted on the fileserver, 2026-09-02:

| tree | non-ASCII names | total |
|---|---|---|
| `/mnt/storage/media/audio` | 54 | 36,158 |
| `/mnt/storage/media/video` | 0 | 51,876 |
| `/mnt/storage/books` | 96 | 1,380 |
| `/mnt/storage/photos` | 0 | 62,026 |

150 names, which is small enough to handle carefully. An example, shown as
bytes: `/mnt/storage/media/audio/import/M\xE0nran - The Test` — `\xE0` is
Latin-1 `à`, and is not valid UTF-8 on its own.

`convmv -f iso-8859-1 -t utf-8` is the tool. The risk is not the rename.

**The risk is that databases hold the old byte paths.** At least four:

- **beets** — `library.blb` stores item paths as bytes. Renaming behind its back
  strands every affected track. `beet update` is the intended repair, and
  `todos/audiobook-importing.md` is already about path routing, so read it first.
- **Plex** — its library database references file paths.
- **photoprism** and **Stump** — same shape, though `photos` and most of `books`
  show 0 and 96 respectively.

So the order matters: quiesce the consumers, rename, repair each database, then
change Samba and restart it. That sequence has not been worked out and is the
substance of this task.

## Worth deciding first

**Is a workaround cheaper than the migration?** ARM's failure comes from a *year
range* containing an en dash — sanitising the characters ARM puts in a path
would unblock phase 2b without touching the fileserver, and ARM already has
`utils.clean_for_filename`. That is a narrow fix for a general problem, and it
leaves the landmine for the next tool. But it is hours rather than a
coordinated migration, and it keeps disc ripping moving.

The general fix is still worth doing. The question is whether it blocks phase 2b
or runs alongside it.

**Decided 2026-09-02: it runs alongside.** The workaround shipped as
`arm-title-charset.sh`, so ripping is unblocked and this migration is no longer
urgent — but the landmine is untouched. Everything except ARM still writes
provider-supplied names straight to the share, and `beets`, `photoprism` and
`Stump` have no equivalent guard. Note the workaround targets **Latin-1**, not
ASCII, precisely so it stays correct after this migration rather than needing
undoing.

**`dos charset = cp850` is a separate question** and matters far less: it applies
to legacy SMB1 clients negotiating a codepage, and `min protocol = SMB2` is
already set. Leave it or set it to UTF-8 with the same change; do not let it
complicate the decision.

## Verification

1. `testparm -s | grep 'unix charset'` reports UTF-8.
2. Creating `probe-’–…中` on each share succeeds — the same probe that measured
   the boundary above.
3. No filename anywhere under `/mnt/storage` fails `python3 -c "…decode('utf-8')"`.
4. beets can play a previously-affected track: its library path resolves.
5. ARM can create `tv/The-Sylvester-and-Tweety-Mysteries (1995–2002)`.

## Related

- [disc-ripping.md](disc-ripping.md) — blocked phase, and where the failure was
  found.
- [video-library-ingest.md](video-library-ingest.md) — anything filing media
  writes provider-supplied names, so it inherits this.
- [backups.md](backups.md) — renames touch 150 files on a replicated share;
  check what a snapshot restore would bring back before starting.
