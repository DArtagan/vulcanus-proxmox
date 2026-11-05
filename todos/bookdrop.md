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

bookdrop's `todos/README.md` orders the work. Its release-pipeline and adept
steps are done: 0.2.1 runs here in dry run, and on 2026-11-03 a Kobo `.acsm`
dropped from the phone came out a DRM-free EPUB. The one step left that needs
work here:

- **filing**: the switch to live, under *Going live*. Its Stump key is in place.

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
- **A dedicated Adobe ID** for bookdrop, separate from any other device. Adobe
  no longer runs ebook DRM sign-in, so this is a ByteBooks ID; see *The device
  key*.

## Deployment

`kubernetes/apps/bookdrop/`, added to `kubernetes/apps/kustomization.yaml`:

- A `Deployment` from `ghcr.io/dartagan/bookdrop`, with `books-pvc` mounted
  read-only at `/books` while dry run is on, and a volume at `/data` for uploads
  awaiting confirmation, backed up as a plain volume (see *Each application* in
  [`docs/backups.md`](../docs/backups.md)).
- A `Service`, and an `Ingress` on the internal class with
  `nginx.ingress.kubernetes.io/proxy-body-size` raised, as beets, photoprism and
  soft-serve set it to `"0"`, and a Let's Encrypt certificate like reelbarrow's,
  which a phone trusts. The phone reaches it off-LAN through the tailnet's
  internal ingress grant ([`docs/tailnet.md`](../docs/tailnet.md)).
- Image automation with the semver range `>=0.1.0 <1.0.0`, like reelbarrow's.
- `secrets.sops.yaml`: the `bookdrop-adept` Secret, mounted read-only at
  `/adept`, and `stump.sops.yaml`: the `bookdrop-stump` Secret, Stump's
  in-cluster URL, the Primary library's id and a key with only `ScanLibrary`,
  in the environment. A scan made through it from the pod on 2026-11-05 ran
  to completion in Stump.

### The device key

Activated on 2026-11-03 on the desktop with `adept_activate` (libgourou 0.8.9,
nixpkgs) against a dedicated **ByteBooks ID**. Adobe moved ebook DRM to Wipro's
ByteBooks in July 2025, and a freshly made Adobe ID fails activation with
`E_AUTH_FAILED … User does not exist`; ByteBooks accounts are made at
<https://dtsbytebooks.com/register>. The account's email and password are Will's
to keep; only the three device files are in the Secret. The desktop copy is in
`~/.config/bookdrop/adept`. The first Kobo `.acsm` was fulfilled against this
key on the desktop the same day, so that book belongs to this device.

Whether the internal network boundary is enough, as for the other internal
apps, or whether the page needs its own login, is still to decide.

### Going live

`BOOKDROP_DRY_RUN: "false"` and the books mount made writable, in one commit.

It waits on phase 1 of [nfs-cluster-volumes.md](nfs-cluster-volumes.md): no
write can land in the books share today, since the tree belongs to uid 1001 at
755 and `[books]` writes as 1100. That phase puts bookdrop on an NFS claim,
so "the books mount" here is that claim.

## What bears on it here

- **The books share** reaches pods through `books-pv`, an SMB CSI volume
  (`//192.168.0.105/books`, mount options `rw`). Stump mounts it at `/books`.
  Files on it show as root-owned from inside the Stump pod.
- **Filenames above U+00FF fail with `EIO`** on every share
  ([`smb-charset-utf8.md`](smb-charset-utf8.md)), and the books share's older
  accented names are UTF-8 on disk, read through it as mojibake: 96 paths such
  as `China MiÃ©ville`, seen from Stump's pod on 2026-11-05. bookdrop
  writes ASCII paths, which read the same before and after that migration.
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
