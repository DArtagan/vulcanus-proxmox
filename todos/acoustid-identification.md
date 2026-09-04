# Identify discs MusicBrainz has never seen

## Opening prompt

> A ripped CD that MusicBrainz does not have a disc ID for lands in the beets
> inbox as `Unknown Artist / Track 1…11` and waits for a person. Fingerprinting
> the audio would identify it without the disc — AcoustID matches on content,
> not on the table of contents. Read `todos/acoustid-identification.md`: the
> environment is surveyed, the three routes are costed, and the recommendation is
> the one that needs no container change. Decide the route with Will before
> building, because two of the three touch the beets-flask image.

Everything below verified **2026-08-29** against `metasauce/beets-flask:v2.0.0-rc5`
and ARM `2.23.2`.

## The case that prompted it

Job 14 ripped an 11-track CD cleanly and beets-flask stopped at
`PREVIEW_COMPLETED` rather than importing, because there was nothing to import
it *as*:

```
GET https://musicbrainz.org/ws/2/discid/me52FDJAZbLLImDQaZV1kydxfSI-
HTTP Error 404: Not Found
```

That is not a bug anywhere. The disc's TOC has simply never been submitted, so
`CDDBMETHOD=musicbrainz` has nothing to match and abcde falls back to
`Unknown Artist / Track N`. `getalbumart` fetches nothing for the same reason.
The rip itself is fine and the files are on disk — see
[disc-ripping.md](disc-ripping.md).

The disc is recoverable by hand this once, because someone is holding it. The
point of this work is the case where nobody is: a stack of discs fed in
unattended, one of which is not in MusicBrainz, and no one notices for a week.

## What is already recovered, and what is not

**The TOC survives in the job log** and does not need the disc. Reconstructed
from `Ripping from sector … to sector …` and **verified by recomputing the disc
ID from it** — it matches ARM's, so the TOC is right:

```
1 11 189049 150 17853 32849 47798 62923 81654 94529 114677 139357 156023 173288
```

11 tracks, 41:58. Attaching it to the right release, once identified, makes the
next rip of that disc identify itself:

```
https://musicbrainz.org/cdtoc/attach?toc=1+11+189049+150+17853+32849+47798+62923+81654+94529+114677+139357+156023+173288&tracks=11&id=me52FDJAZbLLImDQaZV1kydxfSI-
```

**Three disc-level identifiers are not available and re-inserting does not help.**
CD-TEXT, per-track ISRCs and the MCN barcode would each identify the release, and
none can be read today: abcde's CD-TEXT reader wants `icedax` or `cdda2wav`, and
ISRC/MCN want `cd-info` from libcdio-utils. The ARM container has none of
`cd-info`, `cd-drive`, `icedax`, `cdrdao` or `fpcalc`. So a re-insert yields
nothing that the log does not already have.

**AcoustID needs no disc at all.** It fingerprints the decoded audio, so it works
on the FLACs already sitting in the inbox, and the TOC being absent from
MusicBrainz is irrelevant to it. That is why it is the route worth taking.

## What the environment can and cannot take

| | |
|---|---|
| beets version in the image | 2.11.0 |
| `beetsplug/chroma.py` | **already present** — the plugin ships with beets |
| `chroma` in our `plugins:` list | absent (`kubernetes/apps/beets/config-map.yaml:157`) |
| `pyacoustid` (the `acoustid` module) | **absent** |
| `fpcalc` (chromaprint) | **absent** |
| base image | Debian 13 trixie, x86_64, `apt-get` present |
| `/venv` | writable, but **no `pip` and no `uv`** — `python -m pip` reports "No module named pip" |
| `chromaprint` in nixpkgs | yes, 1.6.0 |
| Dockerfile anywhere in this repo | none |

So the plugin code is in place and only its two dependencies are missing. The
absence of pip is the load-bearing fact: it rules out installing `pyacoustid`
into the running container without also shipping a package manager.

An AcoustID **API key** is required either way. Free, from
<https://acoustid.org/new-application>. It is a credential, so it goes in SOPS —
`kubernetes/apps/beets/` has no secret yet, so one is created.

## Three routes

**A. A local tool in `tools/`, nothing in the cluster changes. Recommended.**
`chromaprint` goes into `devenv.nix` (one line, it is in nixpkgs), and a script
takes a folder of audio, runs `fpcalc`, queries AcoustID, and prints candidate
releases with MusicBrainz IDs. Run it against anything sitting unidentified in
the inbox.

Cheapest by a distance, testable without a cluster, and it matches the `tools/`
precedent — `mb-seed`, `arm-disc-wrapper`, `arm-audio-handoff`, `review`. It
does **not** make identification automatic; it makes it one command instead of
guesswork. Given how rare an unlisted disc is, that may be the whole job.

**B. Hot-plug the dependencies into beets-flask.** An initContainer fetches a
static `fpcalc` and a `pyacoustid` wheel onto the `/config` PVC, exposed by
`PYTHONPATH` and the `FPCALC` environment variable; `chroma` is then added to
`plugins:`. This is the pattern Will already uses to inject `audiobook_genre.py`
via `config-seed`, and it keeps the upstream image and its ImagePolicy intact.

The costs are real: no pip in the container to do the install, so the wheel has
to be vendored or fetched at pod start; a network fetch on every start; and two
pinned versions to maintain. **Unverified:** whether the `pyacoustid` version
beets 2.11 wants honours the `FPCALC` environment variable, and whether `chroma`
behaves inside beets-flask's import flow rather than the beets CLI. Check both
before committing to this.

**C. A custom image.** `FROM metasauce/beets-flask`, `apt-get install
libchromaprint-tools`, `pip install pyacoustid`. Correct and durable, and new
ground: no Dockerfile exists in this repo, it needs somewhere to push to, and it
takes beets-flask off the ImagePolicy that currently tracks upstream — which
matters, because the fork's nine open bugs mean upstream releases are worth
following closely.

A and B are not exclusive: A answers "identify this disc now", B answers
"identify every disc automatically". A first is the cheaper order, because it
proves AcoustID actually matches these discs before anything is built around it.

## Verification

Whatever route: the test is a disc AcoustID identifies that MusicBrainz's disc ID
does not. The 2026-08-29 rip is exactly that case and is still in the inbox at
`/audio/import/Unknown Artist Unknown Album`, so it is the fixture.

Per-track durations for confirming a candidate by hand:

```
1  3:56   2  3:19   3  3:19   4  3:21   5  4:09   6  2:51
7  4:28   8  5:29   9  3:42  10  3:50  11  3:30      total 41:58
```

If AcoustID also fails, that is the answer: the disc is not in either database
and only the sleeve will identify it. Record that outcome — it decides whether
route B is worth building at all.

## Related

- [disc-ripping.md](disc-ripping.md) — where the disc came from, and why the rip
  succeeding while the import waits is correct behaviour.
- [audiobook-cover-art.md](audiobook-cover-art.md) — `getalbumart` fetching
  nothing here has the same root cause: no identification, nothing to look art
  up by.
