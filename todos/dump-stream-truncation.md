# Stop streamed database dumps losing their tail

## Opening prompt

> K8up streams every database dump out of its pod over `kubectl exec`, and
> since 2026-10-08 about one dump in forty has arrived short, saved by restic
> as a complete snapshot with no error anywhere. The coverage check catches
> each one and fails. The cause is Kubernetes'
> [kubernetes#142376](https://github.com/kubernetes/kubernetes/issues/142376):
> when the command exits, the streaming server closes the connection before a
> slow reader has drained the output. Read `todos/dump-stream-truncation.md`.
> The fix is decided: every backup command sleeps after its last write. Make
> that change across the ten producers, write the reason and the issue into
> `docs/backups.md`, then watch the coverage check for a week.

Verified **2026-11-08**. Measurements are in
[dump-stream-truncation.evidence.md](dump-stream-truncation.evidence.md).

## What is happening

- **Dumps arrive short, silently.** Fourteen in the fortnight to 2026-11-08,
  of about 570 checked, and nineteen since 2026-10-08. They came from all six
  producers streaming 4 MB or more (Plex's two databases, Grafana, PhotoPrism,
  salamander and PinePods) and none of the four smaller. Each time the Backup
  reported `Succeeded`, K8up logged nothing, restic recorded `errors: 0`, and
  every cut whose length was recorded fell on a 4096-byte boundary. The
  coverage check (`kubernetes/k8up/coverage/`) reads each dump back and failed
  on every one: 11 of its 57 runs in that fortnight. In its 68 runs before
  2026-10-08 it saw none.
- **Nothing is lost yet, but restores are exposed.** The previous whole dump
  stays in the repository, so a truncation costs one six-hour slot. But
  `docs/backups.md`'s restore steps load `latest` or a chosen snapshot without
  checking it is whole, and a short SQL dump loads partially without
  complaint.
- **The cause is upstream, in Kubernetes:**
  [kubernetes#142376](https://github.com/kubernetes/kubernetes/issues/142376),
  "Exec drops end of stdout when client reads slowly", open and
  `triage/accepted`. The streaming server writes the exit status and closes the
  connection as soon as the process exits, while output is still buffered for a
  reader that has not caught up. Reproduced there on 1.30 to 1.37 and with
  `crictl exec` alone, so it is the container runtime's streaming server as
  much as the apiserver. This cluster is on Kubernetes 1.35.0 under Talos
  1.12.4. The reader here is restic, which consumes stdin only as fast as it
  chunks and uploads to rest-server.
- **Why it began on 2026-10-08 was not found.** No Kubernetes, Talos or K8up
  change lines up with it. Probably restic read more slowly from then; not
  proven, and the fix does not depend on it.

## The fix, decided

**Each backup command sleeps 60 s after its `cat`**, so the process outlives its
output and the server has no exit to close the stream on until the reader has
drained it. In the issue's controls, a command that slept 45 s after its last
write was never cut, in 160 runs across both transports. The sleep has to
outlast the time restic takes to read the remainder after the last write,
which cannot exceed a whole read: the slowest here is salamander's, 28.3 s.
60 s is twice that.

The cost is a minute of wall time per dump. Ten dumps run one after another,
so the apps dump run grows from 87–124 s to about eleven minutes. No Backup
has a deadline, and the next consumer is the coverage check at :45.

The ten producers, each ending `test -s "$f"; cat "$f"'`:

| producer | file |
|---|---|
| PreBackupPods: Plex library and blobs | `kubernetes/apps/plex/backups.yaml` |
| PreBackupPods: headscale, linkding, soft-serve, stump | `kubernetes/apps/<app>/backups.yaml` |
| PreBackupPod: Grafana | `kubernetes/infrastructure/grafana-backups.yaml` |
| `k8up.io/backupcommand`: PhotoPrism, PinePods | `kubernetes/apps/photoprism/deployment.yaml`, `kubernetes/apps/pinepods/deployment.yaml` |
| `k8up.io/backupcommand`: salamander | `kubernetes/charts/photoprism/templates/deployment.yaml` |

**salamander's chart needs its version bumped** (`kubernetes/charts/photoprism/Chart.yaml`,
`1.16`): its HelmRelease sets no `reconcileStrategy`, so Flux re-renders the
chart only when the version changes, and a template edit alone would never
deploy. Check after deploying that salamander's Deployment carries the sleep.

Alongside it:

1. **`docs/backups.md`, *Each part closes off a failure*:** a bullet for the
   sleep, saying what it guards against, linking kubernetes#142376, and saying
   when it can go (below). That bullet is where the issue is tracked once this
   spec is deleted.
2. **`docs/backups.md`, the restore steps:** before loading a dump, confirm it
   is whole, by the same tests the coverage check applies: a SQL dump ends in
   its completion marker; a SQLite copy is as long as its header says. If it
   is short, restore the snapshot before it.
3. **`backup_coverage.py`'s module docstring** credits k8up#1109 for "a dump
   cut short". That K8up bug was fixed in v2.15.0, which this one survives.
   Point it at kubernetes#142376.

## Verification

The coverage check is the rule; nothing new is needed. **Against the control**:
11 of 57 runs failed on a dump in the fortnight before, one in five. If the
fix did nothing, a week of 28 runs would all be free of dump verdicts with
probability about 0.81²⁸, 1 in 400. Done when 28 consecutive runs show no dump verdict (`the header
says`, `no completion marker`). Other verdicts, such as a volume that shrank,
are not this fault and do not count against it.

A faster read is available per run: each `stdinBackup.progress` line in
VictoriaLogs carries `bytes processed`, which for a SQLite dump should equal
its header's length.

## When the sleep can go

The upstream fix spans client-go, the apiserver's WebSocket translator, the
kubelet and the runtime's streaming server (`k8s.io/cri-streaming`, vendored
by containerd), and is being staged behind a KEP. On Talos the kubelet and
containerd both arrive with a Talos release, so the sleep can go once the
Talos version running here ships a Kubernetes and a containerd that carry it.
Then remove it and watch the check for a week, as above.

## Not this spec

- **`apps/bookdrop-pvc` shrank from 1.3 MB to 1.5 KB** on 2026-11-06, failing
  the check for four runs. A false alarm: the 1.3 MB was a dry-run upload in
  bookdrop's inbox since 2026-11-02, which filing cleared, leaving a 1.5 KB
  `drop.json`. It recurs whenever an upload spans a nightly snapshot. Whether
  that inbox should be backed up at all belongs to bookdrop
  (`docs/bookdrop.md`).
- **Dumping to a file on a volume, and backing that up** instead of
  streaming. Robust against any transport bug, but a redesign of what
  `docs/backups.md` sets out, with a dump CronJob ahead of every Schedule. The
  fallback if the sleep does not hold.

## Decisions already made

- **The sleep, and the issue tracked in the docs** (Will, 2026-11-08): "Let's
  go with option #1, and include somewhere in our docs/specs a note referring
  to that GitHub issue for future tracking." Option 1 was the sleep; option 2
  was dumping to a file; option 3 was waiting for upstream.
- **Its own spec** (Will, 2026-11-08): "Yes please, write it as a spec."

## Wrong turns not to repeat

- **Not k8up#1109.** That K8up streaming bug was fixed in v2.15.0; this
  cluster runs v2.16.0 and is cut anyway. Nor
  [k8up#1286](https://github.com/k8up-io/k8up/issues/1286), where a reader
  stalled over 5 s kills the stream and K8up exits 1: here nothing errors.
- **Compare a dump with its own header, not with its series' largest
  snapshot.** Stump's eight 1 MB dumps of 2026-11-04 and -05 look short beside
  later ones, but the database grew when Stump rescanned on 2026-11-06.
- **Timing does not identify a cut.** restic's read time for a short dump sits
  inside the range of whole ones, so a slow restic start cannot be the test.
