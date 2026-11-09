"""Tests for raw_copies.py, which moves raw folders' stamps into ARM's database.

Run with `python3 -m unittest discover tools/arm-stamps`.

The stamps are shaped after those on the share: job 87's DVD, whose
documentary is kept whole and its parts skipped; job 84's Blu-ray, a series
disc whose play-all is skipped; and job 27's, a Blu-ray stamped afterwards.
"""

import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

import raw_copies

SCRIPTS = (
    Path(__file__).resolve().parents[2]
    / "kubernetes/apps/automatic-ripping-machine/scripts"
)

DVD = {
    "schema": 1,
    "job_id": 87,
    "label": "ROBIN_HOOD",
    "disctype": "dvd",
    "crc_id": "d3c5ce89aef40f61",
    "stage": "179323748073",
    "start_time": "2026-10-29T01:31:20.799631+00:00",
    "complete": True,
    "problems": [],
    "title_map": [
        {"disc_title": 1, "mkv": "ROBIN HOOD-E1_t00.mkv", "handbrake_title": 3},
        {"disc_title": 2, "mkv": "ROBIN HOOD-E2_t01.mkv", "handbrake_title": 4},
        {"disc_title": 9, "mkv": "ROBIN HOOD-B3_t04.mkv", "handbrake_title": None},
    ],
    "duplicates": {
        "skipped": [
            {
                "handbrake_title": 4,
                "disc_title": 2,
                "reason": "is played whole by title 3",
            }
        ],
        "wholes": [],
    },
}
BLURAY = {
    "schema": 1,
    "job_id": 84,
    "label": "DOWNTON_ABBEY_2_D1",
    "disctype": "bluray",
    "crc_id": None,
    "stage": "179306029568",
    "start_time": "2026-10-27T00:18:15.720212+00:00",
    "complete": True,
    "problems": [],
    "title_map": [
        {"playlist": "00001.mpls", "handbrake_title": 2},
        {"playlist": "00006.mpls", "handbrake_title": 6},
    ],
    "duplicates": {
        "skipped": [
            {
                "handbrake_title": 6,
                "playlist": "00006.mpls",
                "reason": "plays titles 2, 3, 4 in turn",
            }
        ]
    },
}
BACKFILLED = {
    "schema": 1,
    "job_id": 27,
    "label": "RESCUERS_35TH_ANNIVERSARY",
    "disctype": "bluray",
    "crc_id": None,
    "stage": "178961349896",
    "start_time": "2026-09-17T02:51:39.185765+00:00",
    "title_map": [{"playlist": "00251.mpls", "handbrake_title": 43}],
}


class Plan(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.raw = root / "raw"
        self.completed = root / "completed"
        self.jobs = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def titles(self) -> dict:
        (doc,) = self.plan()[0].values()
        return {t["handbrake_title"]: t for t in doc["titles"]}

    def stamped(self, name: str, stamp: dict, *, image: bool = False) -> Path:
        folder = self.raw / name
        folder.mkdir(parents=True)
        (folder / "arm-job.json").write_text(json.dumps(stamp))
        if image:
            (folder / f"{name}.iso").write_bytes(b"")
            (folder / "titles").mkdir()
        else:
            (folder / "BDMV").mkdir()
        path = self.completed / name
        self.jobs.append(
            {"job_id": stamp["job_id"], "stage": stamp["stage"], "path": str(path)}
        )
        return path

    def plan(self, recorded: set[int] = frozenset()) -> tuple[dict, list]:
        return raw_copies.plan(self.jobs, set(recorded), self.raw)

    def test_a_dvd_image_and_its_transcodes(self) -> None:
        completed = self.stamped("ROBIN_HOOD", DVD, image=True)
        (completed / "extras").mkdir(parents=True)
        (completed / "title_3.mkv").write_bytes(b"")
        folder = self.raw / "ROBIN_HOOD"
        documents, passed = self.plan()
        self.assertEqual(passed, [])
        self.assertEqual(
            documents[87],
            {
                "schema": 1,
                "folder": str(folder),
                "form": "dvd-image",
                "check": {"sizes": "passed", "hashes": "passed"},
                "problems": [],
                "image": str(folder / "ROBIN_HOOD.iso"),
                "titles": [
                    {
                        "handbrake_title": 3,
                        "disc_title": 1,
                        "extracted": str(folder / "titles/ROBIN HOOD-E1_t00.mkv"),
                        "transcode": str(completed / "title_3.mkv"),
                        "reason": None,
                    },
                    {
                        "handbrake_title": 4,
                        "disc_title": 2,
                        "extracted": str(folder / "titles/ROBIN HOOD-E2_t01.mkv"),
                        "transcode": None,
                        "reason": "played_by_kept_titles",
                        # Named only in its reason, and no whole says it.
                        "played_by": {"disc_title": 1},
                    },
                    {
                        "handbrake_title": None,
                        "disc_title": 9,
                        "transcode": None,
                        "reason": "not_extracted",
                    },
                ],
                "titles_compared": True,
                "orders": [],
            },
        )

    def test_a_whole_and_its_parts_say_what_plays_them_once(self) -> None:
        def title(n: int) -> dict:
            return {"handbrake_title": n, "playlist": f"0000{n}.mpls"}

        skipped = [{**title(n), "reason": "is played whole by title 6"} for n in (2, 3)]
        whole = {
            **title(6),
            "kept": "whole",
            "interleaved": False,
            "parts": [{**title(2), "start": 0.0}, {**title(3), "start": 9.5}],
        }
        stamp = {
            **BLURAY,
            "title_map": [title(n) for n in (2, 3, 6)],
            "duplicates": {"skipped": skipped, "wholes": [whole]},
        }
        self.stamped("Downton", stamp)
        titles = self.titles()
        self.assertEqual(
            {n: titles[n].get("reason") for n in titles},
            {2: "played_by_kept_titles", 3: "played_by_kept_titles", 6: None},
        )
        self.assertFalse(any("played_by" in t for t in titles.values()))
        self.assertEqual(
            titles[6]["parts"],
            [
                {"playlist": "00002.mpls", "start": 0.0},
                {"playlist": "00003.mpls", "start": 9.5},
            ],
        )
        self.assertIn("interleaved", titles[6])
        self.assertFalse(titles[6]["interleaved"])

    def test_a_transcode_is_found_in_extras_and_a_skipped_title_has_none(self) -> None:
        completed = self.stamped("Downton", BLURAY)
        (completed / "extras").mkdir(parents=True)
        (completed / "extras" / "title_2.mkv").write_bytes(b"")
        # A file named for a skipped title is no transcode of it.
        (completed / "title_6.mkv").write_bytes(b"")
        (doc,) = self.plan()[0].values()
        self.assertEqual(doc["form"], "bluray-backup")
        self.assertNotIn("image", doc)
        titles = self.titles()
        self.assertEqual(titles[2]["transcode"], str(completed / "extras/title_2.mkv"))
        self.assertEqual(titles[6]["transcode"], None)

    def renamed(self, features: list[int], files: list[str]) -> dict:
        completed = self.stamped("ROBIN_HOOD", DVD, image=True)
        completed.mkdir(parents=True)
        for name in files:
            (completed / name).write_bytes(b"")
        self.jobs[0]["main_features"] = features
        return self.titles()

    def test_a_feature_renamed_for_the_film_is_arms_main_feature(self) -> None:
        titles = self.renamed([3], ["Robin Hood (2010).mkv"])
        self.assertTrue(titles[3]["transcode"].endswith("/Robin Hood (2010).mkv"))

    def test_a_renamed_feature_is_recorded_only_when_unambiguous(self) -> None:
        cases = (
            ([], ["Robin Hood (2010).mkv"]),
            ([3, 4], ["Robin Hood (2010).mkv"]),
            ([3], ["Robin Hood (2010).mkv", "Robin Hood (2010) 2.mkv"]),
            # Its own title_N already found: no rename to account for.
            ([3], ["Robin Hood (2010).mkv", "title_3.mkv"]),
        )
        for features, files in cases:
            with self.subTest(features=features, files=files):
                titles = self.renamed(features, files)
                self.assertFalse(
                    any(
                        str(t.get("transcode")).endswith("(2010).mkv")
                        for t in titles.values()
                    )
                )
                self.tearDown()
                self.setUp()

    def test_a_title_in_both_places_is_not_recorded(self) -> None:
        completed = self.stamped("Downton", BLURAY)
        (completed / "extras").mkdir(parents=True)
        (completed / "title_2.mkv").write_bytes(b"")
        (completed / "extras" / "title_2.mkv").write_bytes(b"")
        self.assertNotIn("transcode", self.titles()[2])

    def test_each_check_by_its_own_result(self) -> None:
        hash_failed = "Backup done but 1 files failed hash check"
        missing = "STREAM/00800.m2ts is missing"
        cases = (
            ({**BLURAY, "complete": True}, ("passed", "passed")),
            (
                {**BLURAY, "complete": False, "problems": [missing]},
                ("failed", "passed"),
            ),
            (
                {**BLURAY, "complete": False, "problems": [hash_failed]},
                ("passed", "failed"),
            ),
            ({**BLURAY, "complete": None}, ("not_run", "not_run")),
            # Stamped afterwards: short, or not, and the hashes never rerun.
            (
                {**BACKFILLED, "complete": False, "problems": [missing]},
                ("failed", "not_run"),
            ),
            (BACKFILLED, ("passed", "not_run")),
        )
        for stamp, (sizes, hashes) in cases:
            self.assertEqual(
                raw_copies.check(stamp), {"sizes": sizes, "hashes": hashes}, stamp
            )

    def test_a_backfilled_stamp_has_no_problems_or_duplicates(self) -> None:
        self.stamped("Rescuers_178961349896", BACKFILLED)
        (doc,) = self.plan()[0].values()
        self.assertEqual(doc["problems"], [])
        self.assertNotIn("titles_compared", doc)
        self.assertNotIn("orders", doc)

    def test_what_is_passed_over(self) -> None:
        self.stamped("ROBIN_HOOD", DVD, image=True)
        self.stamped("Downton", BLURAY)
        self.stamped("Rescuers", {**BACKFILLED, "title_map": [{"title": 43}]})
        self.jobs[1]["stage"] = "1"
        documents, passed = self.plan(recorded={87})
        self.assertEqual(documents, {})
        self.assertEqual(
            passed,
            [
                (27, f"{self.raw / 'Rescuers'}'s stamp predates its current form"),
                (84, f"{self.raw / 'Downton'}'s stage isn't the job's"),
                (87, "already recorded"),
            ],
        )

    def test_a_current_stamp_with_shuffles_is_not_retired(self) -> None:
        # `orders` counts its group's `titles`, which an earlier form used for
        # the title map.
        order = {
            "handbrake_title": 2,
            "playlist": "00002.mpls",
            "mismatch": None,
            "titles": 4,
            "runner_up": None,
        }
        duplicates = {**BLURAY["duplicates"], "wholes": [], "orders": [order]}
        self.stamped("Downton", {**BLURAY, "duplicates": duplicates})
        documents, passed = self.plan()
        self.assertEqual((list(documents), passed), ([84], []))
        retired = {**BLURAY, "duplicates": {"skipped": [{"title": 6}], "wholes": []}}
        self.assertTrue(raw_copies.retired(retired))

    def test_a_job_stamped_twice_is_passed_over(self) -> None:
        self.stamped("Downton", BLURAY)
        self.stamped("Downton_2", BLURAY)
        documents, passed = self.plan()
        self.assertEqual(documents, {})
        self.assertEqual(passed[0][0], 84)

    def test_a_skipped_title_names_the_title_kept_for_it(self) -> None:
        reasons = {
            "is a copy of title 3": 3,
            "plays title 7's clips out of order": 7,
            "plays title 2's clips and a still picture": 2,
            "is played whole by title 6": 6,
            # A whole skipped for its parts: they say what plays it.
            "plays titles 2, 3, 4 in turn": None,
            "interleaves titles 2 and 3": None,
        }
        found = [raw_copies.played_by({"reason": reason}) for reason in reasons]
        self.assertEqual(found, list(reasons.values()))
        # A stamp written after the patch recorded it says so itself.
        self.assertEqual(raw_copies.played_by({"reason": "x", "played_by": 5}), 5)

    def test_writing_keeps_a_row_already_there(self) -> None:
        db = sqlite3.connect(":memory:")
        raw_copies.write(db, {84: {"form": "old"}})
        raw_copies.write(db, {84: {"form": "new"}, 87: {"form": "dvd-image"}})
        rows = db.execute("SELECT job_id, document FROM reelbarrow_raw_copy ORDER BY 1")
        self.assertEqual(
            [(n, json.loads(d)["form"]) for n, d in rows],
            [(84, "old"), (87, "dvd-image")],
        )

    def test_the_table_is_the_patchs(self) -> None:
        # Whichever writes first creates it.
        patch = (SCRIPTS / "arm-raw-copy.py").read_text()
        statement = re.search(
            r'"(CREATE TABLE IF NOT EXISTS [^"]+)"\s*"([^"]+)"', patch
        )
        self.assertEqual(raw_copies.TABLE, statement[1] + statement[2])


if __name__ == "__main__":
    unittest.main()
