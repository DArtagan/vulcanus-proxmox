# Beets

Music and audiobook tagging and library management. The library lives on the
fileserver's `audio` dataset; the catalogue is a single SQLite file on a
Kubernetes PVC.

Everything is in `kubernetes/apps/beets/`.

## What runs

| Object | State | Purpose |
|---|---|---|
| `beets-flask` Deployment | Running | Web UI at `beets.immortalkeep.com` (internal only). The primary interface. |
| `beets-import` CronJob | **Suspended** | The former nightly `beet import -q`. Retained as a known-working fallback. |

### Why the CronJob is suspended

It ran nightly for three years and stopped achieving anything long before it was
turned off. Its own log records 1,073 runs; the last time it took any action
other than `skip` was 23 February 2026. Each run walked the same ~520 album
folders, skipped every one, and exited after 75–80 minutes, leaving a 17 MB log
of 197,679 `skip` lines.

That was not a bug. `-q` refuses anything without a strong MusicBrainz match,
and the inbox is almost entirely audiobooks, which essentially never match. The
job could only ever have been a no-op for this collection. beets-flask exists to
make the interactive path — which was always where the real work happened — the
default one.

**Do not unsuspend it while beets-flask is running.** See the concurrency rule
below.

## Storage and concurrency

Three volumes matter:

| Mount | Claim | Notes |
|---|---|---|
| `/library` | `beets-library-pvc` | `library.blb`, the SQLite catalogue. RWO `openebs-hostpath`, PV pinned by `nodeAffinity` to `piraeus-worker-0`. |
| `/audio` | `audio-rw-beets-pvc` | The music tree and the import inbox, over SMB. |
| `/config` | `beets-flask-config-pvc` | beets-flask's own database and the seeded config. |

**`ReadWriteOnce` restricts to one node, not one pod.** Because the library PV
is pinned to `piraeus-worker-0`, anything that mounts it lands there, and
Kubernetes will happily run beets-flask, the CronJob and an ad-hoc pod against
it simultaneously. Nothing will stop you.

SQLite will. `library.blb` has no coordination between processes:

- Concurrent **reads** (`beet ls`, `beet stats`, `beet list -a`) are safe.
- Concurrent **writes** (`import`, `modify`, `update`, `mbsync`) hit
  `database is locked` and can leave an operation half-applied.

So: read freely; scale beets-flask to zero before any ad-hoc write. The
`beets-shell` devenv script does this automatically. Scaling works against Flux
because `deployment.yaml` sets no `replicas` field — server-side apply never
claims it, so the reconciler leaves manual scaling alone.

### The library database is not backed up

`beets-library-pvc` is `openebs-hostpath` with `reclaimPolicy: Delete`, and it
is *not* in borgmatic's mount list — unlike the audio share itself, which is
covered. Deleting the PVC destroys the catalogue. The audio files survive, but
every tagging decision made since 2022 does not.

Snapshot it before any significant change. Use SQLite's backup API rather than
`cp`, so the result is valid even if something is mid-write:

```bash
kubectl exec -n apps <pod> -- python3 -c \
  "import sqlite3; s=sqlite3.connect('/library/library.blb'); \
   d=sqlite3.connect('/tmp/library.blb'); s.backup(d); d.close(); s.close()"
kubectl cp apps/<pod>:/tmp/library.blb ~/backups/beets/library-$(date +%F).blb
```

## Imports are copies, not moves

beets-flask supports `copy` only — it types `move` as `Literal[False]`, so
`move: yes` is a hard config validation error rather than a setting it ignores.
The practical consequences:

- An import writes into `/audio/music/…` (or `audiobooks/`, `podcasts/`, …) and
  **leaves the original in `/audio/import`**. The inbox does not self-drain.
- Cleanup is a first-class inbox action, `delete_imported_folders` — "delete all
  folders from the inbox that have been imported" — backed by the
  `IMPORT_COMPLETED` session state beets-flask records per folder. There is also
  `delete` for a hand-picked selection and `undo` (with a `delete_files` option)
  to reverse an import.
- That action only knows about imports made *through beets-flask*. Anything the
  CronJob imported is not in its database — but the CronJob used `move`, so
  those folders already left the inbox.

## Configuration

One beets config, `config-beets.yaml` in `beets-config-map`, shared by both
consumers:

- **beets-flask** gets it seeded to `/config/beets/config.yaml` by the
  `config-seed` initContainer, which runs on *every* pod start. The ConfigMap in
  git is therefore authoritative: edits made in the web UI land on the PVC and
  are overwritten at the next restart.
- **The CronJob** projects it back to `/config/config.yaml` via `items:` on its
  ConfigMap volume, because beets expects that filename.

beets-flask's own settings — the inbox definition, terminal start path, worker
count — are a separate key, `beets-flask-gui.yaml`, because it reads them from a
separate file.

Settings that exist specifically because beets-flask is stricter than the CLI,
all harmless to the CronJob:

| Setting | Reason |
|---|---|
| `import.move: no`, `copy: yes` | beets-flask supports copy only. |
| `import.duplicate_action: ask` | beets-flask's schema defaults this to `remove`, which deletes duplicates unattended. |
| `replaygain.backend: ffmpeg` | The beets-flask image is `python:3.12-slim` with a static ffmpeg and no GStreamer at all — the `gstreamer` backend aborts beets at startup. `linuxserver/beets` ships both, so ffmpeg suits either. |
| no `permissions` plugin | Both consumers reach the library over CIFS, which fixes modes as mount options. Its chmod is a no-op at best, an EPERM per file at worst. |
| no `statefile` | CLI importer resume state. beets-flask keeps its own per-folder session state; sharing a pickle only lets the two confuse each other. |

The inbox runs `autotag: "auto"` with `auto_threshold: null`, which defers to
`match.strong_rec_thresh` (0.04, the beets default) — the same bar `beet import
-q` applied. The difference from the CronJob is what happens on a miss: instead
of a `skip` line in a log, the folder parks in the UI at
`WAITING_FOR_USER_SELECTION` with its candidates already fetched.

## beets-flask runs as uid 1000, and needs its own SMB mount

Every other workload that writes to an SMB share here runs as root, because the
shares mount with no `uid=` option and the CIFS client presents every file as
`root:root`. `pinepods` and `filebot` both set `PUID`/`PGID` to `"0"` for
exactly this reason.

beets-flask cannot. Its image entrypoint runs as root only long enough to fix
permissions, then unconditionally `su beetle`. There is no switch to keep it as
root, and `GROUP_ID=0` does not work either — the entrypoint runs
`groupmod -g 0 beetle`, which fails because gid 0 already belongs to `root`.

So there is a second PersistentVolume against the same share,
`audio-rw-beets-pv`, carrying `uid=1000,gid=1000` in its `mountOptions`. Those
options only change what the CIFS client reports locally and which uid it lets
past its own permission check; authorisation on the server still uses the same
`smb-credentials` identity as every other mount. `audio-rw-arm-pv` is the
precedent for a second PV against one share.

`volumeHandle` must be unique per PV for the SMB CSI driver — two PVs sharing a
handle are treated as the same volume, and the second's `mountOptions` are
silently dropped.

## It needs a polars workaround to start at all

The VMs are configured with `cpu type = "kvm64"`, which masks every instruction
set above SSE4.2 — the guests report `model name: Common KVM processor`.
beets-flask v2 depends on polars, whose default compiled runtime
(`polars-runtime-32`) is built for x86-64-v3 and raises `SIGILL` the instant it
is imported.

The failure is unusually quiet. The container stays up, the log prints
`Server running on http://0.0.0.0:5001`, redis and the rq workers all start
normally — and uvicorn's workers die and respawn about eleven times a second
without a traceback, so nothing ever binds the port. The readiness probe is the
only thing that distinguishes this from a healthy pod, which is why one is
defined despite no other app in this repo having them.

`beets-flask-startup.sh` in the ConfigMap is seeded to
`/config/beets-flask/startup.sh` and run by the image's entrypoint before the
app starts. It probes whether `import polars` succeeds and, if not, installs
`polars-runtime-compat` pinned to the installed polars version — derived at
runtime, because polars pins its runtime wheel to an exact version and a
hardcoded one would go stale the next time Flux bumps the image.

This costs a 54 MB download on every pod start and is a stopgap. The real fix is
[`todos/proxmox-cpu-type.md`](../todos/proxmox-cpu-type.md); the script is
written to become a no-op once that lands.

Do not switch this to the documented `requirements.txt` mechanism. In v2 the
entrypoint installs those with bare `pip`, which is `/usr/local/bin/pip` writing
to the system site-packages rather than the application's `/venv` — it reports
success and changes nothing.

## Image updates

`beets-flask` is the only pre-release-tracking ImagePolicy in the repo. v2.0.0
has only ever shipped as release candidates, so the range names one explicitly:

```yaml
range: '>=2.0.0-rc1 <3.0.0'
```

A plain `^2.0.0` would resolve to nothing at all. Masterminds/semver records
pre-release-ness per AND-group, so naming a pre-release in the lower bound also
lets `<3.0.0` match them. This will roll onto v2.0.0 final unattended when it
ships; tighten to `^2.0.0` at that point.

## Interactive CLI access

`beets-flask` has a built-in web terminal, which covers most needs. For
everything else there is a devenv script:

```bash
beets-shell
```

It scales beets-flask to zero, starts a `linuxserver/beets` pod with the same
config and mounts, drops you into a shell, and scales back up on exit.

Note that `kubectl debug --copy-to` is *not* a working substitute. The cluster
enforces PodSecurity `baseline`, and kubectl's default debug profile adds
`SYS_PTRACE`, which violates it. `--profile=baseline` avoids that if you need
the technique elsewhere.
