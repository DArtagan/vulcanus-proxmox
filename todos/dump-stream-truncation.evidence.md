# Evidence: dump-stream-truncation

Measured 2026-11-08, from the coverage check's pod logs in VictoriaLogs, K8up's
backup pod logs there, and `restic snapshots --json` read inside the
repository container.

## The coverage check's dump verdicts, 2026-10-25 to 2026-11-08

57 runs, 11 with a dump verdict (three more failed on volume shrinks, which
are not this fault). Sizes are from the verdict where it gives them; a SQL
dump's verdict is the missing completion marker. Each cut is a multiple of
4096 bytes.

| check run (UTC) | dump | held / header |
|---|---|---|
| 10-26 08:46 | `apps-sqlite.plex.sqlite` | 85,000,192 / 88,821,760 |
| 10-27 08:46 | `apps-database.salamander.sql` | no marker |
| 11-01 08:46 | `infrastructure-sqlite.grafana.sqlite` | 3,571,712 / 4,161,536 |
| 11-01 20:46 | `apps-database.photoprism.sql` | no marker |
| 11-02 14:46 | `apps-sqlite.plex.sqlite` | 88,276,992 / 89,034,752 |
| 11-02 14:46 | `infrastructure-sqlite.grafana.sqlite` | 3,342,336 / 4,161,536 |
| 11-02 20:45 | `apps-database.pinepods.sql` | no marker |
| 11-02 20:45 | `apps-sqlite.plex-blobs.sqlite` | 139,100,160 / 141,826,048 |
| 11-03 08:46 | `apps-sqlite.plex.sqlite` | 88,014,848 / 89,034,752 |
| 11-04 14:45 | `apps-database.pinepods.sql` | no marker (14,417,920 against ~18.9 MB whole) |
| 11-06 08:45 | `apps-database.photoprism.sql` | no marker (70,877,184) |
| 11-06 08:45 | `infrastructure-sqlite.grafana.sqlite` | 3,014,656 / 4,161,536 |
| 11-07 08:45 | `apps-database.pinepods.sql` | no marker (17,793,024) |
| 11-07 20:45 | `infrastructure-sqlite.grafana.sqlite` | 3,506,176 / 4,161,536 |

Across all logged runs from 2026-09-22: 19 dump verdicts, the first on
2026-10-08, none in the 68 runs before it.

## The Backups that produced them

Every K8up Backup read, from 2026-11-07 01:00 to 2026-11-08 01:30, ended
`Completed=True/Succeeded`. All 170 stdin backups logged from 2026-11-04 01:00
to 2026-11-08 01:30 report `"errors": 0`, the five short ones among them. For example, Grafana at 2026-11-07 19:30:45, K8up v2.16.0:

```
starting stdin backup  {"filename": "/infrastructure-sqlite", "extension": ".grafana.sqlite"}
backup finished        {"new files": 1, "changed files": 0, "errors": 0}
stats                  {"time": 6.553288396, "bytes added": 840284, "bytes processed": 3506176}
backups of annotated jobs have finished successfully
```

## restic's read time per dump, 2026-11-04 to 2026-11-08

From `stdinBackup.progress` `time`, 17 runs per producer. Short runs' times
fall inside the whole runs' range, so timing cannot pick them out.

| dump | whole: median / max s | short runs |
|---|---|---|
| `.grafana.sqlite` | 2.1 / 5.5 | 5.1 s, 6.6 s |
| `.photoprism.sql` | 6.2 / 28.2 | 5.6 s |
| `.pinepods.sql` | 5.2 / 10.9 | 5.6 s, 5.6 s |
| `.salamander.sql` | 24.2 / 28.3 | — |
| `.plex.sqlite` | 5.7 / 7.1 | — |
| `.plex-blobs.sqlite` | 5.9 / 8.1 | — |
| `.headscale.sqlite` | 5.5 / 8.2 | — |
| `.linkding.sqlite` | 4.3 / 6.1 | — |
| `.soft-serve.sqlite` | 4.2 / 5.6 | — |
| `.stump.sqlite` | 5.0 / 5.7 | — (its 1 MB runs of 11-04 and -05 predate Stump's rescan, not cuts) |

The apps dump runs took 87–124 s end to end (Job start to completion).

## The upstream issue, as read 2026-11-08

[kubernetes#142376](https://github.com/kubernetes/kubernetes/issues/142376),
opened 2026-09-24, `kind/bug`, `triage/accepted`, assigned. Its reproduction:
a pod writes 32 MiB and the client reads at 1 MiB/s. SPDY truncated 5/5
silently; `crictl exec` on the node truncated 5/5; a command sleeping 45 s
after its last write was never cut. A later comment ran 20 runs per cell on
four nodes, including containerd 2.4.0, and every control that slept after
writing (160) came back whole. The maintainers' plan is staged: client-go
treating a missing status as an error first, then the apiserver/kubelet
translator and wsstream, then the CRI streaming server behind a KEP.
