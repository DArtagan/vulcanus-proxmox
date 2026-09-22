"""Tests for the backup coverage assertion.

Run with `python3 -m unittest discover kubernetes/k8up/coverage`.

As with ansible/files/pbs_freshness.py, the expensive mistakes are
misclassifications rather than crashes: an excluded claim that fails makes the
check an always-on warning, and an in-scope claim that only reports is a backup
failure nobody hears about. So the verdict table is tested directly, as a pure
function over parsed inputs. The parsers are tested against the shapes the
cluster API, restic and K8up's job logs were measured to produce on 2026-09-22.
"""

import json
import sys
import unittest
from pathlib import Path
from typing import ClassVar

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import backup_coverage  # noqa: E402

HOUR = 3600
NOW = 1_790_000_000
FRESH = NOW - HOUR
OLD = NOW - 40 * HOUR
# SQLite writes its largest page size, 65536, into the header as 1.
LARGEST_PAGE = 65536


def claim(
    name: str,
    namespace: str = "apps",
    *,
    excluded: bool = False,
    shared: bool = False,
    created: int = OLD,
) -> backup_coverage.Claim:
    return backup_coverage.Claim(
        namespace=namespace,
        name=name,
        excluded=excluded,
        shared=shared,
        created=created,
    )


def producer(
    path: str, namespace: str = "apps", *, created: int = OLD
) -> backup_coverage.Producer:
    return backup_coverage.Producer(
        namespace=namespace, path=path, source="pod test", created=created
    )


# One keyword per Series field, so each test names only the field it varies.
def series(  # noqa: PLR0913
    path: str,
    host: str = "apps",
    *,
    newest: int = FRESH,
    files: int | None = 10,
    size: int | None = 50_000_000,
    previous_files: int | None = 10,
    previous_size: int | None = 50_000_000,
    tags: frozenset[str] = frozenset(),
) -> backup_coverage.Series:
    return backup_coverage.Series(
        host=host,
        path=path,
        newest=newest,
        files=files,
        size=size,
        previous_files=previous_files,
        previous_size=previous_size,
        tags=tags,
    )


def verdicts(
    claims: list[backup_coverage.Claim],
    producers: list[backup_coverage.Producer],
    held: list[backup_coverage.Series],
    errors: dict[tuple[str, str], int] | None = None,
    contents: dict[tuple[str, str], tuple[str | None, str | None]] | None = None,
) -> tuple[list[str], list[str]]:
    return backup_coverage.classify(
        claims,
        producers,
        {(s.host, s.path): s for s in held},
        now=NOW,
        item_errors=errors or {},
        dump_contents=contents or {},
    )


def sqlite_header(
    page_size: int, pages: int, *, valid_for: int = 7, change_counter: int = 7
) -> bytes:
    """Build the 100-byte header SQLite writes, with the fields the check reads."""
    header = bytearray(100)
    header[0:16] = b"SQLite format 3\x00"
    header[16:18] = (1 if page_size == LARGEST_PAGE else page_size).to_bytes(2, "big")
    header[24:28] = change_counter.to_bytes(4, "big")
    header[28:32] = pages.to_bytes(4, "big")
    header[92:96] = valid_for.to_bytes(4, "big")
    return bytes(header)


class SnapshotParsing(unittest.TestCase):
    # Trimmed from `restic snapshots --json` against the repository, 2026-09-22.
    SNAPSHOTS = json.dumps(
        [
            {
                "time": "2026-09-21T20:56:08.561764618Z",
                "paths": ["/data/automatic-ripping-machine-pvc"],
                "hostname": "automatic-ripping-machine",
                "summary": {
                    "total_files_processed": 44,
                    "total_bytes_processed": 82176668,
                },
            },
            {
                "time": "2026-09-22T02:00:05.522581991Z",
                "paths": ["/data/automatic-ripping-machine-pvc"],
                "hostname": "automatic-ripping-machine",
                "summary": {
                    "total_files_processed": 45,
                    "total_bytes_processed": 82201226,
                },
            },
            {
                "time": "2026-09-21T11:45:02.123456789-06:00",
                "paths": [
                    "/srv/storage/books",
                    "/srv/storage/filesync",
                    "/srv/storage/photos",
                ],
                "hostname": "restic-repository",
                "tags": ["decommissioned"],
            },
        ]
    )

    def setUp(self) -> None:
        self.held = backup_coverage.parse_snapshots(self.SNAPSHOTS)

    def test_the_newest_and_the_one_before_it_are_kept(self) -> None:
        arm = self.held[
            "automatic-ripping-machine", "/data/automatic-ripping-machine-pvc"
        ]
        self.assertEqual(arm.files, 45)
        self.assertEqual(arm.previous_files, 44)
        self.assertEqual(arm.size, 82201226)
        self.assertEqual(arm.previous_size, 82176668)

    def test_nanosecond_times_are_read_to_the_second(self) -> None:
        arm = self.held[
            "automatic-ripping-machine", "/data/automatic-ripping-machine-pvc"
        ]
        self.assertEqual(arm.newest, 1_790_042_405)

    def test_an_offset_is_honoured(self) -> None:
        repo = self.held["restic-repository", "/srv/storage/books"]
        self.assertEqual(repo.newest, 1_790_012_702)

    def test_a_snapshot_with_no_summary_reads_as_unknown_size(self) -> None:
        repo = self.held["restic-repository", "/srv/storage/books"]
        self.assertIsNone(repo.files)
        self.assertIsNone(repo.size)
        self.assertIsNone(repo.previous_files)

    def test_tags_are_carried(self) -> None:
        repo = self.held["restic-repository", "/srv/storage/books"]
        self.assertIn("decommissioned", repo.tags)


class ClaimParsing(unittest.TestCase):
    PVCS = json.dumps(
        {
            "items": [
                {
                    "metadata": {
                        "namespace": "apps",
                        "name": "headscale-data-pvc",
                        "creationTimestamp": "2026-09-20T10:00:00Z",
                    },
                    "spec": {"accessModes": ["ReadWriteOnce"]},
                },
                {
                    "metadata": {
                        "namespace": "apps",
                        "name": "borg-backups-pvc",
                        "creationTimestamp": "2023-01-08T00:00:00Z",
                        "annotations": {"k8up.io/backup": "false"},
                    },
                    "spec": {"accessModes": ["ReadWriteMany"]},
                },
            ]
        }
    )

    def setUp(self) -> None:
        self.claims = {c.name: c for c in backup_coverage.parse_claims(self.PVCS)}

    def test_the_annotation_excludes(self) -> None:
        self.assertTrue(self.claims["borg-backups-pvc"].excluded)
        self.assertFalse(self.claims["headscale-data-pvc"].excluded)

    def test_read_write_many_marks_a_shared_volume(self) -> None:
        self.assertTrue(self.claims["borg-backups-pvc"].shared)
        self.assertFalse(self.claims["headscale-data-pvc"].shared)

    def test_creation_time_is_read(self) -> None:
        self.assertEqual(self.claims["headscale-data-pvc"].created, 1_789_898_400)


class ProducerParsing(unittest.TestCase):
    PODS = json.dumps(
        {
            "items": [
                {
                    "metadata": {
                        "namespace": "apps",
                        "name": "pinepods-68b7c5746f-zhwds",
                        "creationTimestamp": "2026-09-21T20:17:33Z",
                        "annotations": {
                            "k8up.io/backupcommand": "sh -c 'pg_dump ...'",
                            "k8up.io/backupcommand-container": "database",
                            "k8up.io/file-extension": ".pinepods.pgdump",
                        },
                    },
                    "spec": {
                        "containers": [{"name": "pinepods"}, {"name": "database"}]
                    },
                    "status": {"phase": "Running"},
                },
                {
                    "metadata": {
                        "namespace": "apps",
                        "name": "plain-annotated-abcde",
                        "creationTimestamp": "2026-09-21T20:17:33Z",
                        "annotations": {"k8up.io/backupcommand": "cat /x"},
                    },
                    "spec": {"containers": [{"name": "first"}, {"name": "second"}]},
                    "status": {"phase": "Running"},
                },
                {
                    "metadata": {
                        "namespace": "apps",
                        "name": "linkding-7c9f8b-q2w3e",
                        "creationTimestamp": "2026-09-21T20:17:33Z",
                    },
                    "spec": {"containers": [{"name": "linkding"}]},
                    "status": {"phase": "Running"},
                },
                {
                    "metadata": {
                        "namespace": "apps",
                        "name": "finished-annotated",
                        "creationTimestamp": "2026-09-21T20:17:33Z",
                        "annotations": {"k8up.io/backupcommand": "cat /x"},
                    },
                    "spec": {"containers": [{"name": "gone"}]},
                    "status": {"phase": "Succeeded"},
                },
            ]
        }
    )
    PREBACKUPPODS = json.dumps(
        {
            "items": [
                {
                    "metadata": {
                        "namespace": "apps",
                        "name": "plex-sqlite",
                        "creationTimestamp": "2026-09-21T20:17:00Z",
                    },
                    "spec": {
                        "fileExtension": ".plex.sqlite",
                        "pod": {"spec": {"containers": [{"name": "sqlite"}]}},
                    },
                },
            ]
        }
    )

    def setUp(self) -> None:
        self.paths = {
            p.path
            for p in backup_coverage.parse_producers(self.PODS, self.PREBACKUPPODS)
        }

    def test_an_annotated_pod_dumps_to_namespace_container_extension(self) -> None:
        self.assertIn("/apps-database.pinepods.pgdump", self.paths)

    def test_without_a_container_annotation_the_first_container_is_used(self) -> None:
        self.assertIn("/apps-first", self.paths)

    def test_a_prebackuppod_dumps_to_its_own_container_and_extension(self) -> None:
        self.assertIn("/apps-sqlite.plex.sqlite", self.paths)

    def test_pods_without_the_annotation_and_finished_pods_produce_nothing(
        self,
    ) -> None:
        self.assertEqual(len(self.paths), 3)

    def test_a_prebackuppod_and_the_pod_it_runs_as_are_one_producer(self) -> None:
        running = json.loads(self.PODS)
        running["items"].append(
            {
                "metadata": {
                    "namespace": "apps",
                    "name": "plex-sqlite-5d8f7-x1y2z",
                    "creationTimestamp": "2026-09-22T01:00:10Z",
                    "annotations": {
                        "k8up.io/backupcommand": "sh -c '...'",
                        "k8up.io/file-extension": ".plex.sqlite",
                    },
                },
                "spec": {"containers": [{"name": "sqlite"}]},
                "status": {"phase": "Running"},
            }
        )
        producers = backup_coverage.parse_producers(
            json.dumps(running), self.PREBACKUPPODS
        )
        plex = [p for p in producers if p.path == "/apps-sqlite.plex.sqlite"]
        self.assertEqual(len(plex), 1)


class JobLogParsing(unittest.TestCase):
    # The shape of K8up v2.16.0's restic log, as read from the first full run.
    LINES = (
        "2026-09-21T21:06:10Z\tINFO\tk8up.restic.restic.backup\tstarting backup",
        (
            "2026-09-21T21:06:10Z\tINFO\tk8up.restic.restic.backup\t"
            'starting backup for folder\t{"foldername": "headscale-data-pvc"}'
        ),
        (
            "2026-09-21T21:06:11Z\tINFO\tk8up.restic.restic.backup.progress\t"
            'backup finished\t{"new files": 4, "changed files": 0, "errors": 0}'
        ),
        (
            "2026-09-21T21:06:11Z\tINFO\tk8up.restic.restic.backup\t"
            'starting backup for folder\t{"foldername": "plex-config-pvc"}'
        ),
        (
            "2026-09-21T21:06:12Z\tERROR\tk8up.restic.restic.backup.progress\t"
            '/data/plex-config-pvc/x during scan\t{"error": "error occurred"}'
        ),
        (
            "2026-09-21T21:12:11Z\tINFO\tk8up.restic.restic.backup.progress\t"
            'backup finished\t{"new files": 24625, "changed files": 0, "errors": 2}'
        ),
        (
            "2026-09-21T21:06:00Z\tINFO\tk8up.restic.restic.stdinBackup\t"
            'starting stdin backup\t{"filename": "/apps-sqlite", '
            '"extension": ".plex.sqlite"}'
        ),
        (
            "2026-09-21T21:06:02Z\tINFO\tk8up.restic.restic.stdinBackup.progress\t"
            'backup finished\t{"new files": 1, "changed files": 0, "errors": 0}'
        ),
    )
    LOG = "\n".join(LINES)

    def test_each_item_gets_its_own_error_count(self) -> None:
        self.assertEqual(
            backup_coverage.parse_job_log(self.LOG),
            {
                "/data/headscale-data-pvc": 0,
                "/data/plex-config-pvc": 2,
                "/apps-sqlite.plex.sqlite": 0,
            },
        )

    def test_an_item_that_never_finished_is_absent_rather_than_clean(self) -> None:
        cut = self.LINES[:5]
        self.assertNotIn(
            "/data/plex-config-pvc", backup_coverage.parse_job_log("\n".join(cut))
        )


class VerdictTable(unittest.TestCase):
    def test_a_fresh_volume_is_silent(self) -> None:
        self.assertEqual(verdicts([claim("a")], [], [series("/data/a")]), ([], []))

    def test_a_stale_volume_fails(self) -> None:
        problems, _ = verdicts([claim("a")], [], [series("/data/a", newest=OLD)])
        self.assertEqual(len(problems), 1)
        self.assertIn("apps/a", problems[0])
        self.assertIn("40h", problems[0])

    def test_an_old_claim_never_backed_up_fails(self) -> None:
        problems, _ = verdicts([claim("a")], [], [])
        self.assertEqual(len(problems), 1)
        self.assertIn("never backed up", problems[0])

    def test_a_new_claim_awaiting_its_first_backup_only_reports(self) -> None:
        problems, reports = verdicts([claim("a", created=NOW - 2 * HOUR)], [], [])
        self.assertEqual(problems, [])
        self.assertIn("awaiting its first backup", reports[0])

    def test_an_excluded_claim_reports_and_never_fails(self) -> None:
        problems, reports = verdicts([claim("a", excluded=True, shared=True)], [], [])
        self.assertEqual(problems, [])
        self.assertIn("excluded", reports[0])

    def test_a_shared_volume_in_scope_fails_even_when_backed_up(self) -> None:
        problems, _ = verdicts([claim("a", shared=True)], [], [series("/data/a")])
        self.assertEqual(len(problems), 1)
        self.assertIn("ReadWriteMany", problems[0])

    def test_a_volume_that_went_empty_fails(self) -> None:
        problems, _ = verdicts([claim("a")], [], [series("/data/a", files=0, size=0)])
        self.assertEqual(len(problems), 1)
        self.assertIn("empty", problems[0])

    def test_a_volume_that_was_always_empty_only_reports(self) -> None:
        held = series("/data/a", files=0, size=0, previous_files=0, previous_size=0)
        problems, reports = verdicts([claim("a")], [], [held])
        self.assertEqual(problems, [])
        self.assertIn("holds nothing", reports[0])

    def test_a_volume_that_lost_more_than_half_its_size_fails(self) -> None:
        held = series("/data/a", size=20_000_000, previous_size=50_000_000)
        problems, _ = verdicts([claim("a")], [], [held])
        self.assertEqual(len(problems), 1)
        self.assertIn("shrank", problems[0])

    def test_a_volume_that_lost_less_than_half_is_silent(self) -> None:
        held = series("/data/a", size=30_000_000, previous_size=50_000_000)
        self.assertEqual(verdicts([claim("a")], [], [held]), ([], []))

    def test_halving_a_tiny_volume_is_not_a_signal(self) -> None:
        held = series("/data/a", size=40_000, previous_size=90_000)
        self.assertEqual(verdicts([claim("a")], [], [held]), ([], []))

    def test_a_first_snapshot_has_nothing_to_shrink_from(self) -> None:
        held = series("/data/a", previous_files=None, previous_size=None)
        self.assertEqual(verdicts([claim("a")], [], [held]), ([], []))

    def test_a_fresh_dump_is_silent(self) -> None:
        path = "/apps-database.pinepods.pgdump"
        self.assertEqual(verdicts([], [producer(path)], [series(path)]), ([], []))

    def test_a_dump_older_than_its_six_hourly_cycle_fails(self) -> None:
        path = "/apps-database.pinepods.pgdump"
        held = series(path, newest=NOW - 8 * HOUR)
        problems, _ = verdicts([], [producer(path)], [held])
        self.assertEqual(len(problems), 1)
        self.assertIn("8h", problems[0])

    def test_a_volume_eight_hours_old_is_still_fresh(self) -> None:
        held = series("/data/a", newest=NOW - 8 * HOUR)
        self.assertEqual(verdicts([claim("a")], [], [held]), ([], []))

    def test_a_dump_never_taken_fails(self) -> None:
        problems, _ = verdicts([], [producer("/apps-x.sql")], [])
        self.assertIn("never dumped", problems[0])

    def test_a_new_producer_awaiting_its_first_dump_only_reports(self) -> None:
        problems, reports = verdicts(
            [], [producer("/apps-x.sql", created=NOW - HOUR)], []
        )
        self.assertEqual(problems, [])
        self.assertIn("awaiting its first dump", reports[0])

    def test_an_empty_dump_fails(self) -> None:
        path = "/apps-x.sql"
        problems, _ = verdicts([], [producer(path)], [series(path, size=0)])
        self.assertIn("empty", problems[0])

    def test_a_dump_that_lost_more_than_half_its_size_fails(self) -> None:
        path = "/apps-x.sql"
        held = series(path, size=20_000_000, previous_size=72_000_000)
        problems, _ = verdicts([], [producer(path)], [held])
        self.assertIn("shrank", problems[0])

    def test_a_volume_whose_claim_is_gone_reports(self) -> None:
        problems, reports = verdicts([], [], [series("/data/gone", newest=OLD)])
        self.assertEqual(problems, [])
        self.assertIn("claim gone", reports[0])

    def test_a_decommissioned_volume_says_so(self) -> None:
        held = series("/data/gone", tags=frozenset({"decommissioned"}))
        _, reports = verdicts([], [], [held])
        self.assertIn("decommissioned", reports[0])

    def test_a_dump_whose_producer_is_gone_reports(self) -> None:
        problems, reports = verdicts([], [], [series("/apps-old.sql", newest=OLD)])
        self.assertEqual(problems, [])
        self.assertIn("producer gone", reports[0])

    def test_the_repository_hosts_own_snapshots_are_not_this_checks_business(
        self,
    ) -> None:
        held = series("/srv/storage/books", host="restic-repository", newest=OLD)
        self.assertEqual(verdicts([], [], [held]), ([], []))

    def test_unreadable_files_report_without_failing(self) -> None:
        problems, reports = verdicts(
            [claim("a")], [], [series("/data/a")], errors={("apps", "/data/a"): 3}
        )
        self.assertEqual(problems, [])
        self.assertIn("3 files", reports[0])

    def test_a_clean_run_reports_no_errors(self) -> None:
        self.assertEqual(
            verdicts(
                [claim("a")], [], [series("/data/a")], errors={("apps", "/data/a"): 0}
            ),
            ([], []),
        )

    def test_the_same_name_in_another_namespace_is_another_volume(self) -> None:
        problems, _ = verdicts(
            [claim("a"), claim("a", namespace="infrastructure")],
            [],
            [series("/data/a")],
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("infrastructure/a", problems[0])


class TheEstateAsMeasured(unittest.TestCase):
    """The cluster as it stood on 2026-09-22, after the first scheduled night.

    22 claims in scope and 18 excluded, seven dumps, and every in-scope claim
    and dump holding a snapshot from that night. The only report beyond the
    exclusions is pinepods-backups-pvc, which has never held anything.
    """

    IN_SCOPE: ClassVar[dict[str, list[str]]] = {
        "apps": [
            "beets-flask-config-pvc",
            "beets-library-pvc",
            "filebot-pvc",
            "headplane-data-pvc",
            "headscale-data-pvc",
            "linkding-data-pvc",
            "mumble-data-pvc",
            "photoprism-data-pvc",
            "photoprism-database-pvc",
            "pinepods-backups-pvc",
            "pinepods-database-pvc",
            "plex-config-pvc",
            "rclone-dropbox-config-pvc",
            "rustdesk-data-pvc",
            "salamander-data-pvc",
            "salamander-database-pvc",
            "speedtest-tracker-pvc",
            "stump-config-pvc",
            "syncthing-data-pvc",
            "youtube-dl-pvc",
        ],
        "infrastructure": ["kube-prometheus-grafana"],
        "automatic-ripping-machine": ["automatic-ripping-machine-pvc"],
    }
    EXCLUDED = 18
    DUMPS: ClassVar[dict[str, list[str]]] = {
        "apps": [
            "/apps-database.photoprism.sql",
            "/apps-database.pinepods.pgdump",
            "/apps-database.salamander.sql",
            "/apps-sqlite.headscale.sqlite",
            "/apps-sqlite.linkding.sqlite",
            "/apps-sqlite.plex.sqlite",
        ],
        "infrastructure": ["/infrastructure-sqlite.grafana.sqlite"],
    }

    def setUp(self) -> None:
        self.claims = [
            claim(name, namespace)
            for namespace, names in self.IN_SCOPE.items()
            for name in names
        ] + [
            claim(f"share-{n}", "apps", excluded=True, shared=True)
            for n in range(self.EXCLUDED)
        ]
        self.producers = [
            producer(path, namespace)
            for namespace, paths in self.DUMPS.items()
            for path in paths
        ]
        self.held = (
            [
                series(f"/data/{name}", namespace)
                for namespace, names in self.IN_SCOPE.items()
                for name in names
                if name != "pinepods-backups-pvc"
            ]
            + [
                series(
                    "/data/pinepods-backups-pvc",
                    files=0,
                    size=0,
                    previous_files=0,
                    previous_size=0,
                ),
                series(
                    "/srv/storage/books",
                    host="restic-repository",
                    files=None,
                    size=None,
                    previous_files=None,
                    previous_size=None,
                ),
            ]
            + [
                series(path, namespace)
                for namespace, paths in self.DUMPS.items()
                for path in paths
            ]
        )

    def test_the_baseline_is_nineteen_reports_and_no_failures(self) -> None:
        problems, reports = verdicts(self.claims, self.producers, self.held)
        self.assertEqual(problems, [])
        self.assertEqual(len(reports), self.EXCLUDED + 1)
        self.assertTrue(any("pinepods-backups-pvc" in r for r in reports))

    def test_a_missed_night_fails_every_volume_and_no_dump(self) -> None:
        missed = [
            s._replace(newest=NOW - 30 * HOUR) if s.path.startswith("/data/") else s
            for s in self.held
        ]
        problems, _ = verdicts(self.claims, self.producers, missed)
        self.assertEqual(len(problems), 22)

    def test_a_broken_prebackup_job_fails_every_dump_and_no_volume(self) -> None:
        stale = [
            s._replace(newest=NOW - 13 * HOUR) if not s.path.startswith("/data/") else s
            for s in self.held
        ]
        problems, _ = verdicts(self.claims, self.producers, stale)
        self.assertEqual(len(problems), 7)


if __name__ == "__main__":
    unittest.main()


class ContentChecks(unittest.TestCase):
    """Whether a dump is whole, read from the store rather than its size alone.

    k8up#1109 lost the last ~1% of dumps silently: 343 MB became 340 MB, far
    inside the size check's halving floor. Each format says for itself where
    its end is, and these read that.
    """

    PLEX_SIZE = 1024 * 86119  # the Plex copy measured on 2026-09-22

    def test_a_sqlite_copy_whose_header_matches_its_size_is_whole(self) -> None:
        verdict = backup_coverage.sqlite_header_verdict(
            sqlite_header(1024, 86119), self.PLEX_SIZE
        )
        self.assertEqual(verdict, (None, None))

    def test_a_sqlite_copy_shorter_than_its_header_says_fails(self) -> None:
        problem, _ = backup_coverage.sqlite_header_verdict(
            sqlite_header(1024, 86119), self.PLEX_SIZE - 3_000_000
        )
        self.assertIn(str(self.PLEX_SIZE), problem)

    def test_the_largest_page_size_is_written_as_one(self) -> None:
        verdict = backup_coverage.sqlite_header_verdict(
            sqlite_header(LARGEST_PAGE, 10), 10 * LARGEST_PAGE
        )
        self.assertEqual(verdict, (None, None))

    def test_something_that_is_not_sqlite_fails(self) -> None:
        problem, _ = backup_coverage.sqlite_header_verdict(b"-- a sql file" * 10, 130)
        self.assertIn("not a SQLite", problem)

    def test_a_header_cut_short_fails(self) -> None:
        problem, _ = backup_coverage.sqlite_header_verdict(
            sqlite_header(4096, 22)[:40], 90112
        )
        self.assertIn("header", problem)

    def test_a_header_whose_page_count_is_stale_reports_rather_than_guessing(
        self,
    ) -> None:
        verdict = backup_coverage.sqlite_header_verdict(
            sqlite_header(4096, 22, valid_for=6, change_counter=7), 90112
        )
        self.assertIsNone(verdict[0])
        self.assertIn("could not verify", verdict[1])

    def test_a_mariadb_dump_ending_in_its_trailer_is_whole(self) -> None:
        tail = b"UNLOCK TABLES;\n\n-- Dump completed on 2026-09-22  1:00:39\n"
        self.assertEqual(backup_coverage.sql_trailer_verdict(tail), (None, None))

    def test_a_postgres_dump_with_its_unrestrict_after_the_marker_is_whole(
        self,
    ) -> None:
        # pg_dump 18.6: the marker is followed by \unrestrict, 118 bytes from
        # the end of the real pinepods dump.
        tail = (
            b"\n\n--\n-- PostgreSQL database dump complete\n--\n\n"
            b"\\unrestrict AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdefghijklmnopq\n\n"
        )
        self.assertEqual(backup_coverage.sql_trailer_verdict(tail), (None, None))

    def test_a_sql_dump_cut_off_mid_row_fails(self) -> None:
        tail = b"INSERT INTO `photos` VALUES (1,'2019-04-01','IMG_0"
        problem, _ = backup_coverage.sql_trailer_verdict(tail)
        self.assertIn("completion marker", problem)

    def test_the_check_follows_the_extension(self) -> None:
        kind = backup_coverage.content_kind
        self.assertEqual(kind("/apps-sqlite.plex.sqlite"), "sqlite")
        self.assertEqual(kind("/apps-database.pinepods.sql"), "sql")
        self.assertEqual(kind("/apps-database.salamander.sql"), "sql")
        self.assertIsNone(kind("/apps-database.pinepods.pgdump"))
        self.assertIsNone(kind("/data/plex-config-pvc"))

    def test_a_current_dump_that_is_not_whole_fails(self) -> None:
        path = "/apps-x.sql"
        problems, _ = verdicts(
            [],
            [producer(path)],
            [series(path)],
            contents={("apps", path): ("no completion marker", None)},
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("no completion marker", problems[0])

    def test_a_stale_dump_fails_once_not_twice(self) -> None:
        path = "/apps-x.sql"
        problems, _ = verdicts(
            [],
            [producer(path)],
            [series(path, newest=NOW - 8 * HOUR)],
            contents={("apps", path): ("no completion marker", None)},
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("8h", problems[0])

    def test_a_content_report_rides_along(self) -> None:
        path = "/apps-x.sqlite"
        problems, reports = verdicts(
            [],
            [producer(path)],
            [series(path)],
            contents={("apps", path): (None, "could not verify the header")},
        )
        self.assertEqual(problems, [])
        self.assertIn("could not verify", reports[0])


class PingNeverRaises(unittest.TestCase):
    """A monitor that cannot be reached must not turn a verdict into a crash."""

    def test_a_placeholder_url_is_survived(self) -> None:
        backup_coverage.ping("REPLACE-WITH-THE-CHECK-PING-URL", "body", failed=True)

    def test_an_unreachable_host_is_survived(self) -> None:
        backup_coverage.ping("http://127.0.0.1:9/ping", "body", failed=False)
