# Evidence: nfs-cluster-volumes

Measured 2026-10-12 and 2026-10-13. The workloads:

- **walk:** `find` over `media/audio`, 39,927–39,930 entries in 2,346
  directories.
- **open+read:** `head -c 4096` over the first 3,000 files of that walk,
  sorted.

All mounts were read-only. Samba variants ran as isolated clones of `[media]`,
each on its own port with its own state directories, never production smbd.
Variants were interleaved, 3–6 runs each, and the figure given is the median.
Raw outputs were in `captures/smb-bench/` of the repo root checkout.

## Samba configuration and version (client: CIFS mount on the host)

| Samba | walk s | smbd CPU s | open+read s |
|---|---|---|---|
| 4.15.13 as configured | 3.88 | 3.27 | 6.37 |
| 4.15 + `ea support = no` | 2.99 | 2.40 | 6.30 |
| 4.15 + `log level = 0` | 3.92 | 3.30 | 5.89 |
| 4.15 + both | 2.95 | 2.37 | 5.33 |
| 4.19.5 (Ubuntu 24.04) as configured | 3.36 | 2.78 | 4.49 |
| 4.19 + both | 1.93 | 1.57 | 4.28 |
| 4.22.11 (Debian 13) as configured | 3.58 | 2.92 | 4.94 |
| 4.22 + both | 2.53 | 1.92 | 4.57 |

The 4.19 vs 4.22 gap is within run-to-run spread.

## OMV-thread settings on 4.15 (smbd CPU per operation, three runs)

Wall-clock MB/s swung 2–5x with host load, so CPU is the comparable figure.

| variant | 2 GiB read | 1 GiB write+fsync | walk | open+read |
|---|---|---|---|---|
| base | 1.85–2.02 | 0.64–0.82 | 2.19–2.33 | 3.22–3.39 |
| `use sendfile = yes` | 1.86–1.97 | 0.69–0.81 | 2.23–2.44 | 3.26–3.41 |
| `aio read/write size = 16384` | 1.85–1.99 | 0.67–0.83 | 2.24–2.37 | 3.35–3.36 |
| socket options with SO_*BUF | 2.01–2.15 | 0.75–0.90 | 2.19–2.39 | 3.25–3.43 |
| `min receivefile size = 16384` | 1.84–1.95 | **0.37–0.39** | 2.25–2.55 | 3.26–3.34 |
| all of the above | 1.91–2.05 | 0.48–0.52 | 2.21–2.35 | 3.24–3.42 |

Sendfile is never reached while async reads are on, which is the default. With
the file warm in ARC, `aio read size = 0` cut read CPU to 1.24–1.32 s, and
sendfile on top of that raised it to 1.36–1.46 s. Synchronous reads were not
pursued, because one smbd serves a whole node and a slow disk read would block
it.

## NFS (host's existing knfsd, `vers=4.2`)

| client | walk | open+read | server CPU per walk |
|---|---|---|---|
| host, fresh mount each run (5 runs) | 0.74–0.87 s | 0.76–0.88 s | 0.39–0.45 s (nfsd) |
| host, same mount, warm (5 runs) | 0.04–0.14 s | 0.32–0.42 s | ~0 |
| pod on worker-0, fresh mount (2 runs) | 1.51–1.72 s | 1.47 s | — |
| pod on worker-0, same mount (3 runs) | 0.55–2.77 s | 0.62–1.48 s | — |
| same pod over production SMB (5 runs) | 4.96–46.21 s | 7.54–17.4 s | — |

The 23.87 s and 46.21 s SMB walks came from fresh pods while worker-0's shared
smbd was busy. Over the 348,458 s it had been up, that process had used
49,992 CPU-seconds (14% of a core on average).

## smbd syscall profile of one walk (strace -c, production config)

The heaviest calls, from about 40k entries:

| syscall | count |
|---|---|
| newfstatat | 159,639 |
| fcntl | 64,108 |
| openat / close | 53,096 each |
| listxattr | 45,429 |
| getpid | 37,553 |
| getcwd | 26,102 |
| chdir | 14,844 |

`listxattr` was 24% of syscall time and disappears with `ea support = no`.

## Extended attributes on disk (2026-10-13)

| dataset | entries | xattrs found |
|---|---|---|
| `storage/media` | 1,962,280 | `user.DOSATTRIB` 203,505; `user.net.filebot.*` on 291 files (269 `video/shows`, 22 `video/movies`; newest ctime 2025-10) |
| `storage/filesync` | 57,566 | `user.DOSATTRIB` 57,356 |
| `storage/photos` | 62,252 | `user.DOSATTRIB` 50,388 |
| `storage/books` | 1,380 | none |
| `backups/borg` | 6,271 | `user.DOSATTRIB` 6,264 |

The shares also hold 139 AppleDouble `._` files (120 in `media`, 19 in
`filesync`).
