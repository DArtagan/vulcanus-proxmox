# mb-seed

Hands the audiobook backlog to MusicBrainz's release editor one book at a time,
with every field pre-filled from the file's own tags.

```bash
mb-seed                          # the whole inbox
mb-seed Goroth Stain Jackson     # only books matching those substrings
```

## Why it exists

Almost none of these books are in MusicBrainz. Fifteen ASINs sampled against the
web service returned no hits at all, and where MusicBrainz does hold the book it
models it as Audible parts or a CD rip — 61 tracks, 99 tracks, 17 CDs — against
a single `.m4b`. beets-flask's own stored previews put 521 of 612 inbox folders
at a best-candidate distance of 0.50 or worse, with only two clearing the
auto-import threshold. The releases have to be created, not found.

What makes that affordable is the tags. The rips carry a tone/m4b-tool tag set —
`AUDIBLE_ASIN`, `SERIES`, `PART`, narrator in `©nrt`, publisher, language,
release date — which already holds everything the release editor asks for. So
the work per book is reading a filled form and pressing submit, not transcribing
a release by hand.

## How it works

1. `manifest.py` runs **inside the cluster**, where the audio share is mounted,
   and emits one JSON record per `.m4b`. `mb-seed` pipes it into the beets-flask
   pod over `kubectl exec`, so nothing has to be copied in.
2. `seed.py` serves a loopback-only review queue. Each book gets a page showing
   the parsed metadata and a link to MusicBrainz. Seeding is documented as POST
   only and a submit button cannot be middle-clicked, so the link points at a
   local page that POSTs on arrival — which leaves left-click, middle-click and
   ctrl-click all behaving the way the browser's own rules say they should.
3. MusicBrainz redirects back with the new `release_mbid`, which lands in
   `ledger.json`. If that redirect does not arrive, the page has a field to
   paste the MBID instead.
4. Books that have cover art get a second step before the queue moves on: the
   art is pulled out of the file, saved to `covers/`, and shown next to a link
   straight to that release's *add cover art* page.

## Cover art

462 of 484 books carry art embedded in the `.m4b`, 447 of them also as a sidecar
image; 22 have none. The Cover Art Archive has **no upload API and no form
seeding**, so the most that can be automated is knowing whether art exists,
having it ready as a real file the upload form's picker can point at, and
landing on the right page. Choose *Front* as the type.

The bytes are fetched from the pod on demand rather than carried in the
manifest: 463 covers at a median 553 KiB would be 250 MB, over the same
`kubectl exec` pipe that truncates silently. They cross as base64 and the length
is checked against what the manifest recorded.

This step is only about getting art *into MusicBrainz*. Getting it into the
beets library is a separate and larger question — `fetchart` has no embedded-art
source at all, so `artpath` is empty for every audiobook here. See
[`todos/audiobook-cover-art.md`](../../todos/audiobook-cover-art.md).

`manifest.json` and `ledger.json` are a catalogue of the library's contents and
its paths on the fileserver. This repository is public, so they are gitignored.

## The release model

**One track per release**, a Digital Media medium holding the whole book, which
is what the Audible download is. A chapter tracklist would be richer data, but
the files are one file per book and beets scores a 1-file album against a
61-track release badly enough that nothing would ever match — the same mismatch
that stalls the inbox today.

**Every narrator is credited**, in the phrasing this library's releases already
use across a whole series:

```
Neven Iliev narrated by Jeff Hays, Annie Ellicott & Justin Thomas James
```

The official audiobook guideline asks for "the author(s) of the book being read,
followed by a join phrase such as 'read by' or 'narrated by', followed by the
narrator(s)", and says to match the join phrase used on the release. It does not
prescribe a separator between multiple narrators; `", "` with `" & "` before the
last is what the existing releases here use.

**The credit is seeded as separate indexed names**, never as one joined string.
The `$author` inline field in the beets config resolves the author from
`albumartists[0]`, which only works if MusicBrainz stores an ordered list — a
single `"Eden Hudson narrated by Travis Baldree"` name would fall through to its
regex fallback instead. `test_seed.py` guards this.

Tag values that stand in for performers rather than naming one — `full cast` and
similar — are dropped and reported on the review page, because seeding them
would create junk artists. A studio like `Soundbooth Theater` is a real entity
and is left alone.

**The Audible source is memorialised as a URL relationship**,
`https://www.audible.com/pd/<ASIN>`, which is what the releases of this kind
already in MusicBrainz use and the only form of it anything can query. The ASIN
also goes in the annotation and the edit note, but that is free text.

The relationship's `link_type` is left unset. It is documented as optional —
"if left blank, can be selected in the release editor" — and the integer ID it
wants is not published anywhere reachable, so choosing *purchase for download*
in the editor beats guessing at a number that lands in a public database.

Never the `amazon asin` relationship. It carries Amazon's ASIN, which is a
different identifier from Audible's for the same book — `We Are Legion` is
`B01L082SCI` on Amazon and `B01L082HJ2` on Audible.

Because those two namespaces look alike, **provenance travels with the value**.
The `AUDIBLE_ASIN` atom names its vendor and the plain `asin` atom does not, so
the manifest records `asin_source` and only an identifier that said it was
Audible's gets an `audible.com/pd` link. One of unknown origin is recorded in
the annotation and flagged for review instead, because nothing in the file can
tell the two apart after the fact. Every book in the collection today reads
`audible`, and the two atoms agree wherever both are present — the distinction
exists for what arrives next, not for what is here.

Neither barcode nor catalogue number holds the ASIN: barcode is a GTIN/EAN/UPC,
which an Audible download does not have (it is seeded as `none`), and the
catalogue number belongs to the label. The publisher is seeded as the label
instead, matching `audible ORIGINAL` and `Soundbooth Theater` on the existing
releases.

## Tests

```bash
python3 -m unittest discover -s tools/mb-seed
```

Stdlib `unittest`, no dependencies — `mutagen` is stubbed, since it lives in the
beets-flask image rather than the devenv shell. Work on this tool is
test-driven: write the failing test first, then the code.
