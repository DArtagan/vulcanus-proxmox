# bookdrop

bookdrop takes a purchased ebook from a phone or the desktop and files it into
the library Stump reads. Drop the `.acsm` or DRM-free EPUB a store hands you,
confirm the metadata it read, and it writes the book, its cover and a
`metadata.opf` into `Author/[Series/]Title/` on the books share, then asks Stump
to scan. It is an application of its own,
[DArtagan/bookdrop](https://github.com/DArtagan/bookdrop): its `docs/design.md`
is the design, and why it reads, writes and refuses what it does. This page is
its deployment here.

## Using it

1. Buy the book. On Kobo, take the **Adobe DRM** edition; a store offering
   Readium LCP instead sells a book bookdrop cannot read, as nothing maintained
   removes LCP.
2. Open <https://bookdrop.immortalkeep.com> and drop the download. A phone
   reaches it on the LAN, or from anywhere over the tailnet, whose internal
   ingress grant covers it ([tailnet.md](tailnet.md)).
3. Check the fields, each labelled with where its value came from, and the
   destination path, then **File it**. The page says where the book went and
   that Stump is scanning; it appears in Stump when the scan ends.

An `.acsm` is a one-time claim, not the book. It expires hours after purchase,
and **the first device to fulfil it owns the download**, so drop it on bookdrop
before opening it anywhere else. Kobo serves a fresh one from the account's
library once one has lapsed.

## How it is deployed

`kubernetes/apps/bookdrop/`, image `ghcr.io/dartagan/bookdrop`, which Flux
follows within 1.x. Releases are cut in its own repository by release-please;
its `docs/releasing.md` says how. Flux finds a new tag within 15 minutes. To
take one at once:

```bash
flux reconcile image repository bookdrop -n apps
flux reconcile image update image-update-automation -n apps
flux reconcile kustomization apps --with-source
```

- **The library** is `books-nfs-pvc`, mounted writable: the books share over
  NFS from the hypervisor, writing as the cluster's storage account, `piraeus`
  (1100:1200). Stump mounts the same claim. bookdrop is the only thing in the
  cluster that writes there, and its folder and file names are ASCII.
- **Its own volume**, `bookdrop-pvc` at `/data`, holds each drop's record and,
  until it is filed, its book. A filed drop keeps only its record.
- **The internal ingress is its only boundary.** It has no login of its own:
  anyone who reaches it can file books into the library. Will decided on
  2026-11-07 that the internal ingress is enough, "We'll add that scope if the
  circumstances necessitate it"; a change to who reaches it, such as another
  person on the tailnet, is that circumstance.
- **Uploads of any size** pass the ingress, which otherwise refuses bodies over
  1 MiB.

## Its two credentials

**The Adobe device key**, the `bookdrop-adept` Secret, is what every `.acsm` is
fulfilled to. It is a device activated against a dedicated **ByteBooks ID**,
used by nothing else: Adobe handed its ebook DRM platform to Wipro in 2025, and
a new device signs in with a ByteBooks ID (<https://dtsbytebooks.com/register>),
not an Adobe ID, which fails with `E_AUTH_FAILED … User does not exist`. The
account's email and password are Will's and are not in this repository; only
the three device files are. A copy is on the desktop in
`~/.config/bookdrop/adept`. Should the key be lost, `adept_activate` from
libgourou makes a new one against the same account, spending one of its limited
activations; books already filed carry no DRM and are unaffected.

**The Stump API key**, the `bookdrop-stump` Secret, beside Stump's in-cluster
URL and the library's id. It has only the *Scan Libraries* permission and no
expiry. To replace it, create a key in Stump's settings with *Explicit Permissions*,
*Scan Libraries* alone, and put it in `stump.sops.yaml` with `sops`.

## When something goes wrong

The page says what happened in each of these, and the pod's log records it.

- **"Open Library could not be asked"**: the lookup that fills gaps by ISBN
  failed, and it has been seen to time out from both the LAN and the cluster.
  The book can still be filed with what it says of itself; *Look up again*
  retries.
- **"Stump could not be asked to scan"**: the book is filed. Scan the library
  from Stump and it appears. Check the key if it keeps happening.
- **"Not taken"** at upload: the file is not an `.acsm`, EPUB or PDF, the
  device key could not fulfil or strip it, or the EPUB is broken. The reason is
  on the page.
- **"Not filed: A book is already at …"**: bookdrop never writes over a book.
  Edit the title or series to file it elsewhere, or deal with the existing one
  on the share.
- **The page hangs on upload or filing**: the books mount is NFS, mounted
  `hard`, so a stuck export makes pods wait rather than fail.
