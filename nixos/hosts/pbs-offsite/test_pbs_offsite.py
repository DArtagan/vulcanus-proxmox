"""Tests for PBS #2's protection mirror and freshness check.

Run with `python3 -m unittest discover nixos/hosts/pbs-offsite`.

Both are comparisons of two snapshot lists, so the comparisons are tested as
pure functions, with every call to either store kept at the edges. The ages sit
at the limits the system really meets: vzdump at 10:00 or 11:00 UTC, the sync
at 12:00 and the check at 18:00, a snapshot a second either side of a limit.
"""

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pbs_offsite  # noqa: E402

HOUR = 3600
# 18:00 UTC, when the check runs.
NOW = 1_790_013_600
# This morning's vzdump, at 10:00 UTC, eight hours ago.
TODAY = NOW - 8 * HOUR
# Yesterday's, 32 hours ago: past the 26 h sync limit.
YESTERDAY = TODAY - 24 * HOUR
SYNC_CUTOFF = NOW - pbs_offsite.SYNC_LIMIT_HOURS * HOUR
VERIFY_CUTOFF = NOW - pbs_offsite.VERIFY_LIMIT_HOURS * HOUR


def snap(
    group: str,
    time: int,
    *,
    protected: bool = False,
    verified: str | None = "ok",
    arrived: int | None = None,
) -> pbs_offsite.Snapshot:
    return pbs_offsite.Snapshot(
        group=group,
        time=time,
        protected=protected,
        verified=verified,
        arrived=arrived,
    )


class ParseSnapshotsTest(unittest.TestCase):
    def test_reads_the_api_listing(self) -> None:
        items = [
            {
                "backup-type": "vm",
                "backup-id": "900",
                "backup-time": TODAY,
                "protected": True,
                "verification": {"state": "ok", "upid": "UPID:..."},
                "files": [],
            },
            # An unprotected, never-verified snapshot carries neither key.
            {"backup-type": "ct", "backup-id": "104", "backup-time": YESTERDAY},
        ]
        self.assertEqual(
            pbs_offsite.parse_snapshots(items),
            [
                snap("vm/900", TODAY, protected=True, verified="ok"),
                snap("ct/104", YESTERDAY, verified=None),
            ],
        )


class PlanMirrorTest(unittest.TestCase):
    def test_protects_what_the_primary_protects(self) -> None:
        local = snap("vm/900", YESTERDAY)
        changes, reports = pbs_offsite.plan_mirror(
            [snap("vm/900", YESTERDAY, protected=True)], [local]
        )
        self.assertEqual(changes, [(local, True)])
        self.assertEqual(reports, [])

    def test_clears_what_the_primary_clears(self) -> None:
        local = snap("vm/900", YESTERDAY, protected=True)
        changes, _ = pbs_offsite.plan_mirror([snap("vm/900", YESTERDAY)], [local])
        self.assertEqual(changes, [(local, False)])

    def test_leaves_matching_flags_alone(self) -> None:
        changes, reports = pbs_offsite.plan_mirror(
            [snap("vm/900", YESTERDAY, protected=True), snap("vm/900", TODAY)],
            [snap("vm/900", YESTERDAY, protected=True), snap("vm/900", TODAY)],
        )
        self.assertEqual((changes, reports), ([], []))

    def test_reports_a_protected_snapshot_the_primary_lost(self) -> None:
        changes, reports = pbs_offsite.plan_mirror(
            [], [snap("vm/100", YESTERDAY, protected=True)]
        )
        self.assertEqual(changes, [])
        self.assertEqual(len(reports), 1)
        self.assertIn("vm/100/", reports[0])

    def test_ignores_an_unprotected_snapshot_the_primary_lost(self) -> None:
        # The ordinary case: pruned on the primary, still within keep-last here.
        self.assertEqual(
            pbs_offsite.plan_mirror([], [snap("vm/900", YESTERDAY)]), ([], [])
        )

    def test_ignores_what_has_not_been_copied_yet(self) -> None:
        self.assertEqual(
            pbs_offsite.plan_mirror([snap("vm/900", TODAY, protected=True)], []),
            ([], []),
        )

    def test_matches_on_group_as_well_as_time(self) -> None:
        # vzdump's guests start within the same second often enough.
        local = snap("ct/104", YESTERDAY)
        changes, _ = pbs_offsite.plan_mirror(
            [snap("vm/900", YESTERDAY, protected=True), snap("ct/104", YESTERDAY)],
            [local],
        )
        self.assertEqual(changes, [])


class AssessSyncTest(unittest.TestCase):
    def assess(
        self, primary: list[pbs_offsite.Snapshot], local: list[pbs_offsite.Snapshot]
    ) -> tuple[list[str], list[str]]:
        return pbs_offsite.assess(primary, local, now=NOW)

    def test_passes_when_yesterdays_snapshot_is_here(self) -> None:
        primary = [snap("vm/900", YESTERDAY), snap("vm/900", TODAY)]
        self.assertEqual(self.assess(primary, [snap("vm/900", YESTERDAY)]), ([], []))

    def test_todays_snapshot_is_not_yet_due(self) -> None:
        # A late vzdump can miss the 12:00 sync; that is not a failure.
        primary = [snap("vm/900", YESTERDAY), snap("vm/900", TODAY)]
        problems, _ = self.assess(primary, [snap("vm/900", YESTERDAY)])
        self.assertEqual(problems, [])

    def test_fails_when_the_newest_due_snapshot_is_missing(self) -> None:
        # The sync stopped after the day before yesterday's run.
        primary = [
            snap("vm/900", YESTERDAY - 24 * HOUR),
            snap("vm/900", YESTERDAY),
            snap("vm/900", TODAY),
        ]
        problems, _ = self.assess(primary, [snap("vm/900", YESTERDAY - 24 * HOUR)])
        self.assertEqual(len(problems), 1)
        self.assertIn("vm/900/", problems[0])

    def test_a_snapshot_one_second_inside_the_limit_is_not_due(self) -> None:
        primary = [snap("vm/900", SYNC_CUTOFF + 1)]
        self.assertEqual(self.assess(primary, []), ([], []))

    def test_a_snapshot_exactly_at_the_limit_is_due(self) -> None:
        primary = [snap("vm/900", SYNC_CUTOFF)]
        problems, _ = self.assess(primary, [])
        self.assertEqual(len(problems), 1)

    def test_a_frozen_group_passes(self) -> None:
        # vm/100: one snapshot, months old, nothing new to copy.
        old = YESTERDAY - 30 * 24 * HOUR
        self.assertEqual(
            self.assess([snap("vm/100", old)], [snap("vm/100", old)]), ([], [])
        )

    def test_older_snapshots_pruned_here_do_not_matter(self) -> None:
        # Only the newest due snapshot is compared, so this store's own
        # retention can differ from the primary's.
        primary = [snap("vm/900", YESTERDAY - n * 24 * HOUR) for n in range(40)]
        self.assertEqual(self.assess(primary, [snap("vm/900", YESTERDAY)]), ([], []))

    def test_an_empty_group_is_nothing_to_copy(self) -> None:
        self.assertEqual(self.assess([], []), ([], []))

    def test_reports_a_group_the_primary_no_longer_holds(self) -> None:
        problems, reports = self.assess([], [snap("vm/101", YESTERDAY)])
        self.assertEqual(problems, [])
        self.assertEqual(len(reports), 1)
        self.assertIn("vm/101", reports[0])


class AssessVerifyTest(unittest.TestCase):
    def test_a_failed_verify_fails_at_once(self) -> None:
        local = [snap("vm/900", TODAY, verified="failed")]
        problems, _ = pbs_offsite.assess([], local, now=NOW)
        self.assertEqual(len(problems), 1)
        self.assertIn("failed", problems[0])

    def test_unverified_inside_the_limit_passes(self) -> None:
        local = [snap("vm/900", TODAY, verified=None, arrived=VERIFY_CUTOFF + 1)]
        self.assertEqual(pbs_offsite.assess([], local, now=NOW)[0], [])

    def test_unverified_at_the_limit_fails(self) -> None:
        local = [snap("vm/900", TODAY, verified=None, arrived=VERIFY_CUTOFF)]
        self.assertEqual(len(pbs_offsite.assess([], local, now=NOW)[0]), 1)

    def test_a_frozen_groups_snapshot_resynced_today_passes(self) -> None:
        # vm/100's one snapshot, taken 34 days ago, arrives again with every
        # day's pull at 12:00 and loses its verify state each time.
        frozen = snap(
            "vm/100", NOW - 34 * 24 * HOUR, verified=None, arrived=NOW - 6 * HOUR
        )
        self.assertEqual(pbs_offsite.assess([], [frozen], now=NOW)[0], [])

    def test_a_monday_resync_passes_through_sundays_verify(self) -> None:
        # Sunday's snapshot, verified that afternoon, arrives again with
        # Monday's 12:00 pull. The next verify starts Sunday at 14:00; one still
        # running at that evening's 18:00 check leaves it 6 d 6 h unverified.
        sunday_check = NOW
        monday_pull = sunday_check - (6 * 24 + 6) * HOUR
        local = [
            snap("vm/900", monday_pull - 26 * HOUR, verified=None, arrived=monday_pull)
        ]
        self.assertEqual(pbs_offsite.assess([], local, now=sunday_check)[0], [])


class ManifestPathTest(unittest.TestCase):
    def test_a_namespace_nests_under_ns(self) -> None:
        path = pbs_offsite.manifest_path(
            "/mnt/pbs", "vulcanus", snap("vm/100", 1_789_773_863)
        )
        self.assertEqual(
            path,
            Path("/mnt/pbs/ns/vulcanus/vm/100/2026-09-18T23:24:23Z/index.json.blob"),
        )

    def test_nested_namespaces_and_the_root(self) -> None:
        snapshot = snap("ct/104", 1_789_773_863)
        self.assertEqual(
            pbs_offsite.manifest_path("/d", "a/b", snapshot).parents[3],
            Path("/d/ns/a/ns/b"),
        )
        self.assertEqual(
            pbs_offsite.manifest_path("/d", "", snapshot).parents[3], Path("/d")
        )


class MainTest(unittest.TestCase):
    def test_refuses_a_url_file_without_an_https_url(self) -> None:
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        secret = tmp / "secret"
        url = tmp / "url"
        secret.write_text("s3cret\n")
        url.write_text("http://example.invalid\n")
        self.assertEqual(
            pbs_offsite.main(["pbs-offsite", "freshness", str(secret), str(url)]), 1
        )


if __name__ == "__main__":
    unittest.main()
