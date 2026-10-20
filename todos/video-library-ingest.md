# Video library ingest: deploying reelbarrow, and ARM's half of the handoff

## Opening prompt

> Ripped discs stop in `media/video/import/automatic-ripping-machine/` and never
> reach the library Plex reads. The service that files them is **reelbarrow**,
> in its own repository (<https://github.com/DArtagan/reelbarrow>, checked out
> at `~/repositories/reelbarrow`). Its `docs/design.md` is the design, and its
> `todos/` holds the application work, one spec per step. This spec is the
> vulcanus-proxmox half: ARM's manifest hook, reelbarrow's deployment, its
> secrets and its alerts. Read `todos/video-library-ingest.md`, starting with
> **Status**, then the reelbarrow spec that the next row names.

The design was verified 2026-10-03 to 10-08 and moved to reelbarrow on
2026-10-18. The evidence behind every decision, including the experiments table,
is there now.

---

## Status

| step | repository | what | state |
|---|---|---|---|
| — | reelbarrow | design | **complete** 2026-10-05; now reelbarrow `docs/design.md` |
| — | reelbarrow | repository, devenv, CI, release-please, image publishing | **done** 2026-10-18; `0.1.0` published from tag `reelbarrow-v0.1.0`, pulls anonymously |
| 1 | both | first release running in the cluster | **done** 2026-10-19: dry run, share read-only; `0.1.1` rolled out with no hand edit (Flux Bot `1815ea86`) |
| 2 | reelbarrow | analysis | **in progress** 2026-10-20 |
| 3 | both | ARM manifest hook: the writer in reelbarrow (`arm-manifest-hook`), the dispatcher and wiring here | not started |
| 4–5 | reelbarrow | fixtures, dry-run planner | not started; the planner needs the TMDB key here |
| 6 | both | first real filing: reelbarrow `movie-filing`, and **the switch** below | not started |
| 7 | both | metrics there, alert rules here | not started |
| 8–12 | reelbarrow | review UI, editions, hard cases, episodes, case photo | not started; each adds a secret here |

The full order, and what each step needs, is in reelbarrow's `todos/README.md`.

---

## Decisions already made

The application's decisions are recorded verbatim in reelbarrow's design,
under *Decisions already made*. These bind this half:

- **Our own tool, in a new repository, as a separate service with a small web
  UI** (Will, 2026-10-04), named `reelbarrow` (2026-10-05).
- **The design lives in reelbarrow** (2026-10-18). Will chose to move it there,
  beside the code, over keeping it here, so this spec carries only the
  deployment half.
- **Public repository, MIT licence, semver releases** (2026-10-18).
  release-please cuts releases. The image is
  `ghcr.io/dartagan/reelbarrow:X.Y.Z`, built from the release tag, and the
  `ImagePolicy` here takes `>=0.1.0 <1.0.0`. A merge in reelbarrow deploys
  nothing until its release pull request is merged. This was chosen over the
  timestamp-per-push scheme trello-randomizer uses, because this tool moves and
  deletes media.
- **Dry run first** (2026-10-18): *"Initially we won't want to make actual
  changes, just do dry-runs and collect data - might be worth an flag."*
  `REELBARROW_DRY_RUN` defaults to true in the code. The deployment sets it
  explicitly and also mounts the share read-only, so a bug in the flag's handling
  meets `EROFS` rather than the library.

---

## reelbarrow's deployment

`kubernetes/apps/reelbarrow/`, in the `apps` namespace beside filebot and Plex:

- **The video share** through `video-pvc`, at `/video`, **read-only**. One
  mount covers both `import/` and the libraries, which filing needs, because
  filing is a `rename()` within one filesystem.
- **A 5 Gi `openebs-hostpath` claim** at `/data`, for SQLite (job state, plans,
  the ledger). It gets K8up's default volume backup. That is sufficient while
  it holds regenerable dry-run plans, but not once the ledger records real
  moves: reelbarrow's `movie-filing` decides the dump before filing starts.
- **The internal ingress** at `reelbarrow.immortalkeep.com`, LAN-only like
  `arm.immortalkeep.com`. The `*.immortalkeep.com` wildcard in CoreDNS already
  resolves it.
- A readiness probe on `/healthz`. No liveness probe, because analysis is long,
  synchronous work (see `docs/kubernetes.md`, *Probes*).

### Secrets, as each step needs them

One SOPS secret, `kubernetes/apps/reelbarrow/secrets.sops.yaml`, grown step by step:

| key | first needed by |
|---|---|
| TMDB API key (free personal key) | `dry-run-planner` |
| fanart.tv key, Plex token | `movie-filing` |
| Pushover app token | `review-ui`. ARM's pair is in `automatic-ripping-machine/api-keys.sops.yaml`; a separate token keeps the messages distinguishable |
| Anthropic API key, pay as you go, with a spend cap set in the Console | `episode-matching` |

### The switch to live filing

Done in reelbarrow's `movie-filing`, and only after its preconditions pass. In
one commit, set `REELBARROW_DRY_RUN` to `"false"` and drop `readOnly` from the
video mount. One precondition belongs here: `video-pv` mounts with options
`rw` and nothing else (verified 2026-10-05). It sets no `uid`, `gid`,
`file_mode` or `dir_mode`, so whether the image's uid 1000 may write to the
share is unknown until tested.

---

## ARM's manifest hook

The one interface between ARM and reelbarrow. **The script that writes the
manifests ships in reelbarrow** (decided 2026-10-19), so every user runs the
same one: reelbarrow's `todos/arm-manifest-hook.md` builds it, along with its
export mode for jobs 1–41. What the manifest carries is in reelbarrow's design,
*The ARM manifest*.

What stays here is wiring it into this ARM:

1. **A dispatcher as `BASH_SCRIPT`.** The setting is single-valued and holds
   `arm-audio-handoff.sh` today. The dispatcher calls that and reelbarrow's
   hook, both with ARM's `$1` (title) and `$2` (body). It runs them as children,
   so it must pass its own `$PPID`, the ripper's pid, to reelbarrow's hook in the
   environment variable that spec names. Otherwise the hook sees the dispatcher
   as its parent and selects no job.
2. **reelbarrow's script in ARM's container**, by whichever route that spec
   chooses (a copy pinned to a release, a release asset, or an init container).
   It writes to `/root/video/manifests/`, which is
   `import/automatic-ripping-machine/manifests/` on the share, where reelbarrow
   looks by default.
3. **The export for jobs 1–41**, run with reelbarrow's script against a copy of
   `/root/db/arm.db`, since ARM is live.

`init-scripts` is a `subPath` mount, so a changed script reaches the container
only after `kubectl rollout restart`, and must be checked in the container, not
in the ConfigMap ([config-change-rollouts.md](config-change-rollouts.md)).
`docs/automatic-ripping-machine.md` gets the hook once it is in place.

---

## Alerting

When reelbarrow's `metrics` step lands, the rules go in
`kubernetes/infrastructure/prometheus-rules.yaml`: a successful ARM job neither
filed nor awaiting review after 24 h, a review untouched for 7 days, and the
service's own liveness. That spec says what each rule must be shown to do before
it counts.

---

## Closing

This half spans most of reelbarrow's steps, so expect several review cycles
under this slug, closed at phase boundaries. See "A phased project spans several
reviews" in `CLAUDE.md`. The spec retires once the case photo works. What stays
true then goes into a `docs/reelbarrow.md`: how it is deployed, how it is turned
on and off, and where manifests are written. The hook goes into
`docs/automatic-ripping-machine.md`.

---

## Related

- [disc-ripping.md](disc-ripping.md): produces what reelbarrow consumes. It
  owns DVD-to-ISO, and the drift in `docs/automatic-ripping-machine.md` about
  `backup_dvd`.
- [smb-charset-utf8.md](smb-charset-utf8.md): the share rejects filenames above
  U+00FF with `EIO`, and TMDB names contain them. reelbarrow folds to Latin-1
  until that lands.
- [config-change-rollouts.md](config-change-rollouts.md): the hook is a `subPath`
  mount.
- [backups.md](backups.md): `media/video` is snapshotted and replicated, which
  is what makes deleting leftovers acceptable.
