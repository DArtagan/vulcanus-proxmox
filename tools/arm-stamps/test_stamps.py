"""Tests for stamps.py, which brings every raw folder's stamp to the current form.

Run with `python3 -m unittest discover tools/arm-stamps`.

The stamps it reshapes are the forms the share has held: `titles` and a bare
`title` before 2026-10-26, then `extracted` with a `length` per title and, on
the disc-ripping branch only, a Blu-ray's `playlists`. The folders it backfills
are shaped after jobs 23, 25 and 27, whose logs are their own, and jobs 7 and 8,
which have no log and are proven by their folders' `_<stage>` suffix.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import stamps

sys.path.insert(0, str(Path(__file__).parent.parent / "arm-source-patches"))
from test_patches import FakeMakeMKV, whole_disc, write_clip, write_iso

DVD = {
    "schema": 1,
    "job_id": 76,
    "label": "PINKY_AND_THE_BRAIN_D2",
    "disctype": "dvd",
    "crc_id": "7969bce42a920b30",
    "stage": "179261218992",
    "start_time": "2026-10-21T19:49:49.973701+00:00",
    "complete": True,
    "problems": [],
}
BLURAY = {
    "schema": 1,
    "job_id": 83,
    "label": "DOWNTON_ABBEY_D2",
    "disctype": "bluray",
    "crc_id": None,
    "stage": "179298456935",
    "start_time": "2026-10-26T03:16:09.457630+00:00",
    "complete": True,
    "problems": [],
}
SKIPPED = {
    "handbrake_title": 7,
    "playlist": "00006.mpls",
    "reason": "plays titles 2, 3, 4 in turn",
}
DUPLICATES = {"skipped": [SKIPPED], "wholes": []}
# Job 83's log, shortened: the titles transcoded, each with HandBrake's scan of
# its playlist.
LOG = """2026-10-25 23:16:10 ARM: INFO: Starting
2026-10-25 23:20:00 ARM: INFO: Processing files to: /root/video/raw/Downton-Abbey
ARM: INFO: Processing files to: /root/video/raw/Downton-Abbey_179298456935
2026-10-26 00:10:00 ARM: INFO: Transcoding title 2 to '/x/title_2.mkv'
  + playlist: 00001.MPLS
  + playlist: 00009.MPLS
2026-10-26 01:10:00 ARM: INFO: Transcoding title 3 to '/x/title_3.mkv'
  + playlist: 00002.MPLS
"""


class Conform(unittest.TestCase):
    def test_a_dvd_map_loses_its_lengths_and_takes_its_name(self) -> None:
        stamp = {
            **DVD,
            "extracted": [
                {
                    "disc_title": 4,
                    "mkv": "C1_t01.mkv",
                    "length": 1295,
                    "handbrake_title": 2,
                }
            ],
        }
        self.assertEqual(
            stamps.conform(stamp)["title_map"],
            [{"disc_title": 4, "mkv": "C1_t01.mkv", "handbrake_title": 2}],
        )
        self.assertNotIn("extracted", stamps.conform(stamp))

    def test_the_form_before_2026_10_26(self) -> None:
        stamp = {
            **DVD,
            "titles": [
                {
                    "disc_title": 1,
                    "mkv": "B1_t00.mkv",
                    "length": 7649,
                    "handbrake_title": 1,
                }
            ],
            "duplicates": {
                "skipped": [{"title": 1, "disc_title": 1, "reason": "r"}],
                "wholes": [
                    {
                        "title": 1,
                        "disc_title": 1,
                        "kept": "parts",
                        "interleaved": False,
                        "parts": [{"title": 2, "disc_title": 4, "start": 0.0}],
                    }
                ],
            },
        }
        after = stamps.conform(stamp)
        self.assertEqual(
            after["title_map"],
            [{"disc_title": 1, "mkv": "B1_t00.mkv", "handbrake_title": 1}],
        )
        self.assertEqual(
            after["duplicates"]["skipped"],
            [{"handbrake_title": 1, "disc_title": 1, "reason": "r"}],
        )
        self.assertEqual(
            after["duplicates"]["wholes"][0]["parts"],
            [{"handbrake_title": 2, "disc_title": 4, "start": 0.0}],
        )
        self.assertEqual(after["duplicates"]["wholes"][0]["handbrake_title"], 1)

    def test_a_blu_rays_map_comes_from_its_log_and_its_skipped_titles(self) -> None:
        # The log names only what was transcoded, in HandBrake's upper case, and
        # the map takes each playlist's name as it is on the disc.
        stamp = {**BLURAY, "extracted": None, "duplicates": DUPLICATES}
        after = stamps.conform(
            stamp, stamps.log_title_map(LOG), ["00001.mpls", "00002.mpls", "00006.mpls"]
        )
        self.assertEqual(
            after["title_map"],
            [
                {"playlist": "00001.mpls", "handbrake_title": 2},
                {"playlist": "00002.mpls", "handbrake_title": 3},
                {"playlist": "00006.mpls", "handbrake_title": 7},
            ],
        )

    def test_a_blu_ray_without_its_log_keeps_no_map(self) -> None:
        self.assertIsNone(stamps.conform({**BLURAY, "extracted": None})["title_map"])

    def test_playlists_fold_into_the_map(self) -> None:
        stamp = {
            **BLURAY,
            "extracted": None,
            "playlists": [{"handbrake_title": 2, "playlist": "00001.mpls"}],
        }
        after = stamps.conform(stamp)
        self.assertEqual(
            after["title_map"], [{"playlist": "00001.mpls", "handbrake_title": 2}]
        )
        self.assertNotIn("playlists", after)

    def test_the_fields_keep_the_patchs_order(self) -> None:
        stamp = {**BLURAY, "duplicates": DUPLICATES, "extracted": None}
        self.assertEqual(
            list(stamps.conform(stamp)), [*BLURAY, "title_map", "duplicates"]
        )

    def test_a_current_stamp_is_left_as_it_is(self) -> None:
        current = stamps.conform(
            {
                **DVD,
                "extracted": [
                    {"disc_title": 4, "mkv": "m", "length": 1, "handbrake_title": 2}
                ],
            }
        )
        self.assertEqual(stamps.conform(current), current)
        blu_ray = stamps.conform(
            {**BLURAY, "extracted": None, "duplicates": DUPLICATES}, {2: "00001.MPLS"}
        )
        self.assertEqual(stamps.conform(blu_ray, {2: "00009.MPLS"}), blu_ray)


class Logs(unittest.TestCase):
    def test_a_log_is_the_jobs_only_if_it_opens_when_the_job_started(self) -> None:
        self.assertTrue(stamps.log_owned(LOG, "2026-10-25 23:16:09.457630"))
        # Job 2's row names the log job 61 wrote six months later.
        self.assertFalse(stamps.log_owned(LOG, "2026-04-17 21:17:46"))
        self.assertFalse(stamps.log_owned("no timestamp here", "2026-10-25 23:16:09"))

    def test_the_raw_folder_is_the_last_one_logged(self) -> None:
        self.assertEqual(
            stamps.log_raw_path(LOG), Path("/root/video/raw/Downton-Abbey_179298456935")
        )

    def test_each_transcode_takes_the_first_playlist_scanned_for_it(self) -> None:
        self.assertEqual(stamps.log_title_map(LOG), {2: "00001.MPLS", 3: "00002.MPLS"})


class Checks(unittest.TestCase):
    """The completeness checks, which have to be the patch's."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = Path(self.tmp.name)
        self.patch = whole_disc(FakeMakeMKV())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_blu_ray(self) -> None:
        write_clip(self.raw, "00000", 10, size=1920)
        write_clip(self.raw, "00001", 10, size=1919)
        write_clip(self.raw, "00002", 10, size=None)
        found = stamps.bluray_problems(self.raw)
        self.assertEqual(found, self.patch["_arm_bluray_problems"](str(self.raw)))
        self.assertEqual(len(found), 2)

    def test_image(self) -> None:
        for name, size in (("whole.iso", None), ("short.iso", 40 * 2048 - 1)):
            iso = str(self.raw / name)
            write_iso(iso, 40, size=size)
            self.assertEqual(
                stamps.iso_problems(Path(iso)), self.patch["_arm_iso_problems"](iso)
            )
        self.assertEqual(
            stamps.iso_problems(self.raw / "short.iso"),
            ["short.iso is 81919 bytes of 81920"],
        )


class Plan(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = Path(self.tmp.name, "raw")
        self.logs = Path(self.tmp.name, "logs")
        self.raw.mkdir()
        self.logs.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def bdmv(self, name: str, *, short: bool = False) -> Path:
        folder = self.raw / name
        (folder / "BDMV" / "PLAYLIST").mkdir(parents=True)
        (folder / "BDMV" / "index.bdmv").write_bytes(b"INDX0200")
        for playlist in ("00001.mpls", "00002.mpls"):
            (folder / "BDMV" / "PLAYLIST" / playlist).write_bytes(b"MPLS0200")
        write_clip(folder, "00001", 10, size=1919 if short else 1920)
        return folder

    def job(
        self,
        job_id: int,
        stage: str,
        logfile: str | None = None,
        start: str = "2026-10-25 23:16:09",
    ) -> dict:
        return {
            "job_id": job_id,
            "label": "RESCUERS_35TH_ANNIVERSARY",
            "disctype": "bluray",
            "crc_id": None,
            "stage": stage,
            "start_time": start,
            "logfile": logfile,
        }

    def plan(self, jobs: list[dict]) -> tuple[list, list]:
        return stamps.plan(jobs, raw=self.raw, logs=self.logs)

    def test_a_folder_named_for_its_stage_is_backfilled_without_a_log(self) -> None:
        folder = self.bdmv("The Rescuers_177674072574")
        ((path, before, after, why),) = self.plan([self.job(7, "177674072574")])[0]
        self.assertEqual(
            (path, before, why), (folder / "arm-job.json", None, "backfilled")
        )
        # The size check passed, but the backup's hash check can't be rerun.
        self.assertNotIn("complete", after)
        self.assertNotIn("problems", after)
        self.assertIsNone(after["title_map"])
        self.assertNotIn("duplicates", after)
        self.assertEqual(after["start_time"], stamps.utc("2026-10-25 23:16:09"))

    def test_a_short_copy_is_backfilled_incomplete(self) -> None:
        self.bdmv("The Rescuers_178848428496", short=True)
        ((_, _, after, _),) = self.plan([self.job(23, "178848428496")])[0]
        self.assertFalse(after["complete"])
        self.assertIsInstance(after["complete"], bool)
        self.assertEqual(after["problems"], ["STREAM/00001.m2ts is 1919 bytes of 1920"])

    def test_a_folder_named_for_its_title_is_proven_by_the_jobs_own_log(self) -> None:
        self.bdmv("Downton-Abbey_179298456935")
        (self.logs / "D2.log").write_text(LOG)
        # A stage that names no folder, so only the log can prove it.
        ((_, _, after, _),) = self.plan([self.job(83, "000000000000", "D2.log")])[0]
        self.assertEqual(
            after["title_map"],
            [
                {"playlist": "00001.mpls", "handbrake_title": 2},
                {"playlist": "00002.mpls", "handbrake_title": 3},
            ],
        )

    def test_another_jobs_log_proves_nothing(self) -> None:
        self.bdmv("Downton-Abbey_179298456935")
        (self.logs / "D2.log").write_text(LOG)
        self.assertEqual(
            self.plan([self.job(2, "177800000000", "D2.log", "2026-04-17 21:17:46")]),
            ([], []),
        )

    def test_a_folder_without_the_whole_disc_is_not_stamped(self) -> None:
        (self.raw / "Robin-Hood_178999705941").mkdir()
        (self.raw / "Robin-Hood_178999705941" / "B3_t04.mkv").write_bytes(b"")
        self.assertEqual(self.plan([self.job(36, "178999705941")]), ([], []))

    def test_a_folder_two_jobs_claim_is_passed_over(self) -> None:
        self.bdmv("Downton-Abbey_179298456935")
        (self.logs / "D2.log").write_text(LOG)
        (self.logs / "other.log").write_text(LOG)
        actions, passed = self.plan(
            [self.job(83, "179298456935", "D2.log"), self.job(84, "x", "other.log")]
        )
        self.assertEqual(actions, [])
        self.assertEqual(passed[0][0], [83, 84])

    def test_a_stamp_is_rewritten_in_place_and_never_backfilled_over(self) -> None:
        folder = self.bdmv("The Rescuers_177674072574")
        old = {
            **DVD,
            "job_id": 7,
            "extracted": [
                {"disc_title": 1, "mkv": "m", "length": 5, "handbrake_title": 1}
            ],
        }
        (folder / "arm-job.json").write_text(json.dumps(old))
        ((path, before, after, why),) = self.plan([self.job(7, "177674072574")])[0]
        self.assertEqual(
            (path, before, why),
            (folder / "arm-job.json", old, "rewritten to the current form"),
        )
        self.assertEqual(
            after["title_map"], [{"disc_title": 1, "mkv": "m", "handbrake_title": 1}]
        )

    def test_writing_is_as_the_patch_writes(self) -> None:
        path = self.raw / "arm-job.json"
        stamps.write(path, {"schema": 1})
        self.assertEqual(path.read_text(), '{\n  "schema": 1\n}\n')


if __name__ == "__main__":
    unittest.main()
