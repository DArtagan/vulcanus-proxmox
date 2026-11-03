# bookdrop: a web drop that turns a purchased ebook into a Stump book

## Opening prompt

> Buying an ebook should end with it in Stump, from a phone as easily as from
> the desktop. **bookdrop** is the web page that does it: upload the `.acsm`
> (or a DRM-free EPUB) the store hands you, confirm the metadata it read, and
> it is filed into the books share and Stump rescans. It is a new application
> in its own repository, `~/repositories/bookdrop`, shaped like reelbarrow; this
> repository holds its deployment. Read `todos/bookdrop.md`. If the bookdrop
> repository does not exist yet, creating it is the first step and its
> `docs/design.md` takes over the *Application design* section below.

Verified **2026-11-03** unless marked otherwise.

---

## The flow

1. Buy on Kobo, choosing the Adobe DRM edition, on the phone or the desktop.
2. Download the `.acsm` the store offers.
3. Open `bookdrop.immortalkeep.com` (LAN, or the tailnet from anywhere) and
   upload it. On a phone this is the browser's file picker over Downloads;
   a Web Share Target would let the share sheet send it directly (phase 2).
4. bookdrop fulfils the `.acsm` into an EPUB, strips the Adobe DRM, reads the
   metadata, and shows it prefilled with the destination path.
5. Accept, or edit and accept. bookdrop files it and triggers a Stump scan.

## Decisions already made

By the user, 2026-11-03:

- **A web drop is preferred over an SMB share**, and building it, with Kobo
  DRM handling from the start, is the priority — "I understand that it flies in
  the face of MVP, and maybe changes the original spec". The old spec's CLI tool
  and its "No GUI" non-goal are superseded.
- **DRM removal is in scope**, for personal backup and device transfer. The user
  cited a 2014 ruling by Judge Denise Cote as holding this non-infringing; the
  session could not place that ruling (her best-known ebook case is
  *United States v. Apple*), so the decision rests on the user's call, not that
  citation.
- **Adobe DRM, bought from Kobo.** Not Readium LCP; see *Wrong turns*.
- **Own repository, like reelbarrow**: Python/uv, Dockerfile, release-please,
  image on `ghcr.io/dartagan/bookdrop`.
- **A prefilled confirmation screen**, not auto-filing. Kobo EPUB metadata is
  not trusted to carry series information (unverified; check against the first
  real purchase).
- **A dedicated Adobe ID** for bookdrop, separate from any other device.
- **Named bookdrop.**

## What was verified

- **libgourou does the Adobe half headlessly.** `adept_activate` registers a
  device against an Adobe ID, `acsmdownloader` fulfils an `.acsm` into the
  encrypted EPUB or PDF, and `adept_remove` strips the DRM. Upstream is
  <https://forge.soutade.fr/soutade/libgourou>, with commits in September 2026;
  the AUR carries 0.8.10, nixpkgs 0.8.9. Whether Debian packages it was not
  established, so the image may need to build it.
  [A worked example](https://brtkwr.com/posts/2026-08-18-sideloading-a-library-loan-onto-a-kobo-without-adobe-digital-editions/)
  from 2026-08 uses exactly this chain.
- **Stump's API:** user-scoped API keys, gated on the `ACCESS_API_KEYS`
  permission, authenticate the REST and GraphQL API
  (<https://www.stumpapp.dev/docs/developer/api>). Scans run per library or per
  series. The mutation that starts one was not looked up. Stump runs 0.1.11.
- **The books share** reaches pods through `books-pv`, an SMB CSI volume with
  only `rw` in its mount options, mounted at `/books` in Stump.
- **Filenames above U+00FF fail with `EIO`** on every share
  ([`smb-charset-utf8.md`](smb-charset-utf8.md)). Book titles carry curly
  apostrophes, en dashes and ellipses routinely.
- **Uploads need `nginx.ingress.kubernetes.io/proxy-body-size`** on the
  ingress; beets, photoprism and soft-serve set it to `"0"`.
- **The phone reaches it off-LAN:** the tailnet grants the internal ingress
  ([`docs/tailnet.md`](../docs/tailnet.md)).
- **The desktop's Calibre** has DeDRM, Obok and KFX Input installed, with no
  Adobe keys and no DeACSM. It is a fallback for a single book, not part of
  bookdrop.

## Application design

Moves to bookdrop's `docs/design.md` when that repository exists.

### Input

- `.acsm`: fulfil with `acsmdownloader`, then `adept_remove`. The result may be
  an EPUB or a PDF; Kobo sells some titles as Adobe PDF.
- A DRM-free `.epub`: straight to metadata.
- Anything else is refused with a clear message. Kindle formats and conversion
  wait until a purchase actually needs them.
- A file that is still encrypted after `adept_remove` is refused. Nothing
  DRM-locked enters the library.

### Metadata

Read from the EPUB's OPF. With an ISBN, fill gaps from Open Library
(`openlibrary.org/isbn/{isbn}.json`), then Google Books. Required on the
confirmation screen: title, author, and series with index where there is one.
Optional: publisher, ISBN, date, description, language, cover. An optional
author alias table maps variant spellings to one canonical name.

The confirmed values are written into the EPUB itself, since Stump reads them
from there, and into a `metadata.opf` beside it.

### Placement

```
Author/Series/Title/Title.epub     # in a series
Author/Title/Title.epub            # standalone
```

with `cover.jpg` and `metadata.opf` alongside, and the original PDF if that is
what the store sold. Path components are made Latin-1-safe (`’` to `'`, `–` to
`-`, `…` to `...`) until `smb-charset-utf8` lands; the true title stays in the
metadata. A target that already exists is shown on the confirmation screen
rather than overwritten.

This is the layout the library already has, measured from the Stump pod:
131 author directories, 230 EPUBs, `metadata.opf` and `cover.jpg` beside
nearly every book, and series one level deeper
(`Frank Herbert/Dune/Dune Messiah/`). A colon in a title is written ` - `
(`Chapterhouse - Dune`). Older books keep their original `azw3`/`mobi`
beside the EPUB. Filing is not perfectly consistent: *Children of Time* sits
standalone despite its series. How Stump's library is configured to group
series was not checked.

After placement, trigger a scan of the series or library through Stump's API.

### Adobe identity

`adept_activate` runs **once, on the desktop**, against the dedicated Adobe ID.
Its output (`device.xml`, `activation.xml`, `devicesalt`) is the device key and
goes into a SOPS secret mounted into the pod, so the Adobe password never enters
the cluster and restarts do not spend activations, which Adobe limits per
account.

### Rules carried from reelbarrow

A dry-run default enforced by a single writer, and real books never in the
repository: tests use synthetic EPUBs and `.acsm` fixtures with no account data.

## Deployment (this repository)

`kubernetes/apps/bookdrop/`: Deployment, Service, Ingress on the internal
class with `proxy-body-size`, image automation, the `books-pvc` mount, and
`secrets.sops.yaml` holding the Adobe device files and the Stump API key. Add it
to `kubernetes/apps/kustomization.yaml`. Decide whether the internal network
boundary is enough, as for the other internal apps, or whether the page needs
its own login.

## Phases

1. **The drop works end to end** for a Kobo `.acsm` and a DRM-free EPUB:
   upload, confirmation, placement, scan. Verified with a real purchase that
   then opens in Stump's reader and in KOReader.
2. **Phone ergonomics**: installable PWA with a Web Share Target, so "share"
   on the downloaded `.acsm` lands on the confirmation screen.
3. **On demand only**: duplicate detection against the library, Kindle and
   other sources, PDF to EPUB conversion.

## Wrong turns

- **Readium LCP** looks equivalent on ebooks.com but cannot be removed: the
  noDRM fork of DeDRM dropped LCP after Readium's 2022 DMCA takedown
  (<https://github.com/github/dmca/blob/master/2022/01/2022-01-04-readium.md>).
  LCP books are readable only in LCP apps, never in Stump.
- **Stump's built-in upload** (`STUMP_ENABLE_UPLOAD`, off by default, 20 MB
  cap) was proposed first as the front end. It files an upload as-is, so it
  cannot fulfil an `.acsm`, strip DRM or fix metadata.
- **Bookshop.org** ebooks appeared to be read in its own app with no file to
  download (unverified, from memory); not a source.
