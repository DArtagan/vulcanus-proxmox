# bookdrop: deploying the web drop that files purchased ebooks into Stump

## Opening prompt

> Buying an ebook should end with it in Stump, from a phone as easily as from
> the desktop. **bookdrop** does it: upload the `.acsm` or DRM-free EPUB a store
> hands you, confirm the metadata it read, and it is filed into the books share
> and Stump rescans. It lives in its own repository
> (<https://github.com/DArtagan/bookdrop>, checked out at
> `~/repositories/bookdrop`), whose `docs/design.md` is the design and whose
> `todos/` holds the application work, one spec per step. This spec is the
> vulcanus-proxmox half: its manifests, secrets and the books mount. Read
> `todos/bookdrop.md`, then bookdrop's `todos/README.md`, whose first spec is
> the next step.

Verified **2026-11-03** unless marked otherwise.

---

## The steps

bookdrop's `todos/README.md` orders the work. These steps also need work here:

- **release-pipeline**: `kubernetes/apps/bookdrop/`, under *Deployment*.
- **adept**: the Adobe device key in `secrets.sops.yaml`.
- **filing**: the Stump API key, and the switch to live, under *Going live*.

## Decisions already made

By Will, 2026-11-03:

- **A web drop is preferred over an SMB share**, and building it, with Kobo
  DRM handling from the start, is the priority — "I understand that it flies in
  the face of MVP, and maybe changes the original spec". The book-import CLI
  spec this replaces, and its "No GUI" non-goal, are superseded.
- **DRM removal is in scope**, for personal backup and device transfer. Will
  cited a 2014 ruling by Judge Denise Cote as holding this non-infringing; the
  session could not place that ruling (her best-known ebook case is
  *United States v. Apple*), so the decision rests on Will's call, not that
  citation.
- **Adobe DRM, bought from Kobo**, not Readium LCP; bookdrop's design says why.
- **Its own repository, like reelbarrow**, with the same repository settings,
  and release-please without a component in branch or tag names.
- **A prefilled confirmation screen**, not auto-filing.
- **A dedicated Adobe ID** for bookdrop, separate from any other device.

## Deployment

`kubernetes/apps/bookdrop/`, added to `kubernetes/apps/kustomization.yaml`:

- A `Deployment` from `ghcr.io/dartagan/bookdrop`, with `books-pvc` mounted
  read-only at `/books` while dry run is on, and a volume at `/data` for uploads
  awaiting confirmation. `/data` needs a backup decision per
  [`docs/kubernetes.md`](../docs/kubernetes.md); it holds nothing that cannot be
  uploaded again, so likely none.
- A `Service`, and an `Ingress` on the internal class with
  `nginx.ingress.kubernetes.io/proxy-body-size` raised, as beets, photoprism and
  soft-serve set it to `"0"`. The phone reaches it off-LAN through the tailnet's
  internal ingress grant ([`docs/tailnet.md`](../docs/tailnet.md)).
- Image automation with the semver range `>=0.1.0 <1.0.0`, like reelbarrow's.
- `secrets.sops.yaml`: the Adobe device key (`device.xml`, `activation.xml`,
  `devicesalt`) mounted as files, and later the Stump API key.

Whether the internal network boundary is enough, as for the other internal
apps, or whether the page needs its own login, is still to decide.

### Going live

`BOOKDROP_DRY_RUN: "false"` and the books mount made writable, in one commit.

## What bears on it here

- **The books share** reaches pods through `books-pv`, an SMB CSI volume
  (`//192.168.0.105/books`, mount options `rw`). Stump mounts it at `/books`.
  Files on it show as root-owned from inside the Stump pod.
- **Filenames above U+00FF fail with `EIO`** on every share
  ([`smb-charset-utf8.md`](smb-charset-utf8.md)); bookdrop folds path
  components to Latin-1 until that lands.
- **Stump runs 0.1.11.** Its API takes user-scoped API keys, gated on the
  `ACCESS_API_KEYS` permission.
- **The desktop's Calibre** has DeDRM, Obok and KFX Input installed, with no
  Adobe keys and no DeACSM. It is a fallback for a single book, not part of
  bookdrop.

## Wrong turns

- **Stump's built-in upload** (`STUMP_ENABLE_UPLOAD`, off by default, 20 MB
  cap) was proposed first as the front end. It files an upload as-is, so it
  cannot fulfil an `.acsm`, strip DRM or fix metadata.
- **Bookshop.org** ebooks appeared to be read in its own app with no file to
  download (unverified, from memory); not a source.
