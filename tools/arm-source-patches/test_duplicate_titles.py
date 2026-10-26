"""Tests for arm-duplicate-titles.py, which is appended to ARM's handbrake.py.

Run with `python3 -m unittest discover tools/arm-source-patches`.

The decision is tested on titles built from segments, shaped after discs
ripped here: a TV play-all, a bonus disc's documentary in parts, a film with
featurettes cut into it, a film assembled from clips the disc also offers
singly. Every decision is checked for the property that matters, that no title
is skipped unless the titles kept play it, and each threshold is tested on
both sides of its boundary. Reading a disc is tested against a playlist file
built byte by byte, and against lsdvd's and HandBrake's output as they print
it.
"""

import json
import logging
import os
import struct
import subprocess
import tempfile
import types
import unittest
from pathlib import Path

from test_patches import load_configmap

TICK = 45000


class Tracks(list):
    def filter_by(self, **criteria: object) -> "Tracks":
        return Tracks(
            t for t in self if all(getattr(t, k) == v for k, v in criteria.items())
        )


class FakeSubprocess:
    """subprocess, answering HandBrake's and lsdvd's commands with fixed output.

    lsdvd's tracks are printed as lsdvd prints them: the title set's number,
    `vts`, only with -v.
    """

    SubprocessError = subprocess.SubprocessError
    CalledProcessError = subprocess.CalledProcessError

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.outputs: dict[str, str] = {}
        self.lsdvd_tracks: list[dict] = []

    def run(self, cmd: list[str], **_: object) -> types.SimpleNamespace:
        # As the real one does, before running anything.
        for arg in cmd:
            if not isinstance(arg, str | bytes | os.PathLike):
                kind = type(arg).__name__
                message = f"expected str, bytes or os.PathLike object, not {kind}"
                raise TypeError(message)
        self.commands.append(list(cmd))
        if cmd[0] == "lsdvd":
            tracks = [
                {k: v for k, v in t.items() if k != "vts" or "-v" in cmd}
                for t in self.lsdvd_tracks
            ]
            stdout = "lsdvd = " + repr({"track": tracks})
        else:
            stdout = self.outputs[cmd[0]]
        return types.SimpleNamespace(stdout=stdout, returncode=0)


def load(fake: FakeSubprocess | None = None) -> dict:
    ns = {
        "os": __import__("os"),
        "logging": logging,
        "subprocess": fake or FakeSubprocess(),
        "cfg": types.SimpleNamespace(
            # Typed as ARM's own config loads them from arm.yaml.
            arm_config={
                "MINLENGTH": 420,
                "MAXLENGTH": 99999,
                "HANDBRAKE_LOCAL": "HandBrakeCLI",
            }
        ),
        "db": types.SimpleNamespace(session=types.SimpleNamespace(commit=lambda: None)),
        "notices": [],
    }
    ns["utils"] = types.SimpleNamespace(
        NOTIFY_TITLE="ARM notification",
        notify=lambda _job, _title, body: ns["notices"].append(body),
    )
    code = load_configmap("arm-duplicate-titles.py")
    exec(compile(code, "arm-duplicate-titles.py", "exec"), ns)  # noqa: S102
    return ns


def title(*segments: tuple, chapters: list[float] | None = None) -> dict:
    """Build a title of segments (source, start, end), one unit a second."""
    segs = [(source, start, end, float(end - start)) for source, start, end in segments]
    if chapters is None:
        chapters, at = [], 0.0
        for _, start, end, _ in segs:
            chapters.append(at)
            at += end - start
    return {"segments": segs, "chapters": chapters, "source": {}}


def back_to_back(*parts: dict) -> dict:
    """Build a whole playing `parts` in turn, with a chapter at each join."""
    return title(*(seg[:3] for part in parts for seg in part["segments"]))


class Decision(unittest.TestCase):
    def setUp(self) -> None:
        self.ns = load()

    def decide(self, titles: dict, *, series: bool) -> tuple[dict, list]:
        skip, wholes = self.ns["_arm_duplicates"](titles, series)
        kept = [t["segments"] for n, t in titles.items() if n not in skip]
        units = self.ns["_arm_units"]
        for number in skip:
            played = self.ns["_arm_covered"](titles[number]["segments"], kept)
            self.assertGreaterEqual(
                played, 0.99 * units(titles[number]["segments"]), f"title {number} lost"
            )
        return skip, wholes

    def episodes(self) -> dict:
        eps = {n: title(("VTS1", n * 2000, n * 2000 + 1300)) for n in range(2, 6)}
        return {1: back_to_back(*eps.values()), **eps}

    def test_a_series_keeps_its_episodes_and_skips_the_play_all(self) -> None:
        skip, wholes = self.decide(self.episodes(), series=True)
        self.assertEqual(list(skip), [1])
        self.assertEqual(wholes[0]["kept"], "parts")
        self.assertEqual([p["title"] for p in wholes[0]["parts"]], [2, 3, 4, 5])

    def test_a_movie_keeps_the_whole_and_skips_its_parts(self) -> None:
        # Robin Hood's bonus disc: one documentary, and its three parts.
        skip, wholes = self.decide(self.episodes(), series=False)
        self.assertEqual(sorted(skip), [2, 3, 4, 5])
        self.assertEqual(wholes[0]["kept"], "whole")
        self.assertEqual(
            [p["start"] for p in wholes[0]["parts"]], [0, 1300, 2600, 3900]
        )

    def test_a_whole_missing_a_chapter_at_a_join_keeps_its_parts(self) -> None:
        titles = self.episodes()
        titles[1]["chapters"].remove(2600.0)
        skip, _ = self.decide(titles, series=False)
        self.assertEqual(list(skip), [1])

    def test_a_chapter_a_second_from_the_join_marks_it(self) -> None:
        titles = self.episodes()
        titles[1]["chapters"] = [0.0, 1301.0, 2599.0, 3900.0]
        skip, _ = self.decide(titles, series=False)
        self.assertEqual(sorted(skip), [2, 3, 4, 5])

    def test_a_chapter_just_over_a_second_away_does_not(self) -> None:
        titles = self.episodes()
        titles[1]["chapters"] = [0.0, 1301.01, 2600.0, 3900.0]
        skip, _ = self.decide(titles, series=False)
        self.assertEqual(list(skip), [1])

    def test_a_film_with_featurettes_cut_in_is_skipped_and_reported(self) -> None:
        # X-Men: First Class's "X Marks the Spot": the film's clips, each followed
        # by a featurette's.
        film = title(("A", 0, 1500), ("B", 0, 2400), ("C", 0, 3000))
        featurettes = title(("x", 0, 120), ("y", 0, 130), ("z", 0, 240))
        mode = title(
            ("A", 0, 1500),
            ("x", 0, 120),
            ("B", 0, 2400),
            ("y", 0, 130),
            ("C", 0, 3000),
            ("z", 0, 240),
        )
        for series in (False, True):
            skip, wholes = self.decide(
                {1: film, 2: featurettes, 3: mode}, series=series
            )
            self.assertEqual(list(skip), [3])
            self.assertIn("interleaves titles 1 and 2", skip[3])
            self.assertTrue(wholes[0]["interleaved"])

    def test_featurettes_sharing_an_intro_are_not_interleaved(self) -> None:
        # Downton Abbey's: each featurette opens on one intro, which their
        # play-all plays once.
        a = title(("intro", 0, 10), ("A", 0, 440))
        b = title(("intro", 0, 10), ("B", 0, 560))
        whole = title(
            ("intro", 0, 10), ("A", 0, 440), ("B", 0, 560), chapters=[0.0, 450.0]
        )
        skip, wholes = self.decide({1: whole, 2: a, 3: b}, series=False)
        self.assertFalse(wholes[0]["interleaved"])
        self.assertEqual(sorted(skip), [2, 3])
        self.assertEqual([p["start"] for p in wholes[0]["parts"]], [10, 450])

    def test_what_only_a_part_plays(self) -> None:
        own = self.ns["_arm_own"](
            [("A", 0, 100, 10.0), ("B", 0, 50, 5.0)],
            [[("A", 20, 30, 1.0), ("A", 25, 40, 1.5)], [("B", 0, 50, 5.0)]],
        )
        self.assertEqual(own, [("A", 0, 20, 2.0), ("A", 40, 100, 6.0)])

    def test_a_film_built_from_clips_offered_singly_is_kept(self) -> None:
        # Dredd: four clips make the film, three of them titles of their own.
        parts = {
            n: title((f"c{n}", 0, length))
            for n, length in ((2, 2420), (3, 483), (4, 2472))
        }
        film = title(
            ("c2", 0, 2420), ("c3", 0, 483), ("short", 0, 376), ("c4", 0, 2472)
        )
        skip, wholes = self.decide({1: film, **parts}, series=False)
        self.assertEqual(skip, {})
        self.assertEqual(wholes, [])

    def test_parts_playing_exactly_ninety_nine_percent_are_enough(self) -> None:
        a, b = title(("A", 0, 495)), title(("B", 0, 495))
        whole = title(("A", 0, 495), ("B", 0, 495), ("unique", 0, 10))
        skip, _ = self.decide({1: whole, 2: a, 3: b}, series=True)
        self.assertEqual(list(skip), [1])

    def test_parts_playing_less_leave_everything_transcoded(self) -> None:
        # Alien's bonus disc and Scott Pilgrim: a whole holding pieces too short
        # to be titles of their own.
        a, b = title(("A", 0, 495)), title(("B", 0, 494))
        whole = title(("A", 0, 495), ("B", 0, 494), ("unique", 0, 11))
        for series in (True, False):
            skip, _ = self.decide({1: whole, 2: a, 3: b}, series=series)
            self.assertEqual(skip, {})

    def test_a_part_ninety_five_percent_inside_counts(self) -> None:
        a = title(("A", 0, 950), ("own", 0, 50))
        b = title(("B", 0, 1000))
        whole = title(("A", 0, 950), ("B", 0, 1000))
        skip, wholes = self.decide({1: whole, 2: a, 3: b}, series=True)
        self.assertEqual([p["title"] for p in wholes[0]["parts"]], [2, 3])
        # The 5% of title 2 the whole lacks keeps title 2, and the whole is
        # played by the two of them.
        self.assertEqual(list(skip), [1])

    def test_a_part_less_than_ninety_five_percent_inside_does_not(self) -> None:
        a = title(("A", 0, 949), ("own", 0, 51))
        b = title(("B", 0, 1000))
        whole = title(("A", 0, 949), ("B", 0, 1000))
        skip, wholes = self.decide({1: whole, 2: a, 3: b}, series=True)
        self.assertEqual((skip, wholes), ({}, []))

    def test_alternate_cuts_are_not_a_whole_and_parts(self) -> None:
        # Robin Hood's two cuts, and Cowboy Bebop's near-identical playlists, each
        # inside the other.
        theatrical = title(("F", 0, 8000))
        extended = title(("F", 0, 8000), ("E", 0, 1300))
        same = title(("F", 0, 8000))
        skip, _ = self.decide({1: extended, 2: theatrical, 3: same}, series=False)
        self.assertEqual(skip, {})

    def test_episodes_sharing_a_closing_cell_are_still_parts(self) -> None:
        # Pinky and the Brain: each episode ends on one shared cell, which the
        # play-all leaves out.
        eps = {
            n: title(("V", n * 2000, n * 2000 + 1200), ("V", 0, 30))
            for n in range(2, 5)
        }
        play_all = title(*(("V", n * 2000, n * 2000 + 1200) for n in range(2, 5)))
        skip, _ = self.decide({1: play_all, **eps}, series=True)
        self.assertEqual(list(skip), [1])

    def test_a_play_all_within_a_play_all(self) -> None:
        a1, a2, b = title(("a1", 0, 600)), title(("a2", 0, 700)), title(("b", 0, 900))
        a = back_to_back(a1, a2)
        both = back_to_back(a1, a2, b)
        titles = {1: both, 2: a, 3: b, 4: a1, 5: a2}
        skip, _ = self.decide(titles, series=True)
        self.assertEqual(sorted(skip), [1, 2])
        skip, _ = self.decide(titles, series=False)
        self.assertEqual(sorted(skip), [2, 3, 4, 5])

    def test_one_title_inside_another_is_not_enough(self) -> None:
        skip, _ = self.decide(
            {1: title(("A", 0, 3000)), 2: title(("A", 0, 1500))}, series=False
        )
        self.assertEqual(skip, {})


def mpls(items: list[tuple[str, int, int]], marks: list[tuple[int, int, int]]) -> bytes:
    """Build a playlist of (clip, in, out) items and (kind, item, time) marks."""
    body = b""
    for clip, start, end in items:
        item = (
            clip.encode()
            + b"M2TS"
            + b"\x00\x01\x00"
            + struct.pack(">II", start, end)
            + bytes(12)
        )
        body += struct.pack(">H", len(item)) + item
    playlist = struct.pack(">IHHH", 0, 0, len(items), 0) + body
    playlist = struct.pack(">I", len(playlist) - 4) + playlist[4:]
    marks_at = 40 + len(playlist)
    marks_bytes = b"".join(
        struct.pack(">BBHIHI", 0, kind, item, stamp, 0xFFFF, 0)
        for kind, item, stamp in marks
    )
    header = b"MPLS0200" + struct.pack(">II", 40, marks_at) + bytes(24)
    return (
        header
        + playlist
        + struct.pack(">IH", len(marks_bytes) + 2, len(marks))
        + marks_bytes
    )


def handbrake_scan(titles: list[tuple[int, int]]) -> str:
    """Print HandBrake's JSON scan of (index, playlist) titles, the first main."""
    title_set = {
        "MainFeature": titles[0][0],
        "TitleList": [{"Index": i, "Playlist": p} for i, p in titles],
    }
    return (
        'Version: {\n    "Name": "HandBrake"\n}\n'
        'Progress: {"State": "SCANNING"}\n'
        f"JSON Title Set: {json.dumps(title_set, indent=4)}\n"
        "HandBrake has exited.\n"
    )


PLAY_ALL = 6


class Reading(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = Path(self.tmp.name)
        self.fake = FakeSubprocess()
        self.ns = load(self.fake)
        logging.disable(logging.CRITICAL)

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)
        self.tmp.cleanup()

    def test_a_playlists_items_and_chapters(self) -> None:
        path = self.raw / "00006.mpls"
        path.write_bytes(
            mpls(
                [("00000", 600 * TICK, 700 * TICK), ("00011", 600 * TICK, 606 * TICK)],
                [
                    (1, 0, 600 * TICK),
                    (2, 0, 650 * TICK),
                    (1, 0, 660 * TICK),
                    (1, 1, 600 * TICK),
                ],
            )
        )
        segments, chapters = self.ns["_arm_mpls"](str(path))
        self.assertEqual(
            segments,
            [
                ("00000", 600 * TICK, 700 * TICK, 100.0),
                ("00011", 600 * TICK, 606 * TICK, 6.0),
            ],
        )
        # The link point is not a chapter.
        self.assertEqual(chapters, [0.0, 60.0, 100.0])

    def test_a_file_that_is_no_playlist_is_refused(self) -> None:
        path = self.raw / "00001.mpls"
        path.write_bytes(b"NOPE" + bytes(60))
        with self.assertRaises(ValueError):
            self.ns["_arm_mpls"](str(path))

    def test_a_blu_rays_titles_are_found_by_their_playlists(self) -> None:
        folder = self.raw / "BDMV" / "PLAYLIST"
        folder.mkdir(parents=True)
        (folder / "00001.MPLS").write_bytes(
            mpls([("00000", 0, 3000 * TICK)], [(1, 0, 0)])
        )
        (folder / "00006.mpls").write_bytes(
            mpls([("00000", 0, 3000 * TICK)], [(1, 0, 0)])
        )
        self.fake.outputs["HandBrakeCLI"] = handbrake_scan([(2, 1), (6, 6), (7, 7)])
        titles = self.ns["_arm_bluray_titles"](str(self.raw), {2: None, 6: None})
        self.assertEqual(sorted(titles), [2, 6])
        self.assertEqual(titles[2]["source"], {"playlist": "00001.MPLS"})
        cmd = self.fake.commands[0]
        self.assertEqual(cmd[cmd.index("--min-duration") + 1], "420")

    def test_a_dvds_titles_are_found_through_the_stamp(self) -> None:
        (self.raw / "disc.iso").write_bytes(b"")
        (self.raw / "arm-job.json").write_text(
            json.dumps(
                {
                    "extracted": [
                        {"disc_title": 1, "handbrake_title": 1},
                        {"disc_title": 4, "handbrake_title": 2},
                        {"disc_title": 5, "handbrake_title": None},
                    ]
                }
            )
        )
        self.fake.lsdvd_tracks = (
            {
                "track": [
                    {
                        "ix": 1,
                        "vts": 1,
                        "chapter": [{"length": 66.4}, {"length": 454.3}],
                        "cell": [
                            {
                                "first_sector": 8483,
                                "last_sector": 36722,
                                "length": 66.4,
                            },
                            {
                                "first_sector": 36723,
                                "last_sector": 205854,
                                "length": 454.3,
                            },
                        ],
                    },
                    {
                        "ix": 4,
                        "vts": 1,
                        "chapter": [{"length": 66.4}],
                        "cell": [
                            {"first_sector": 8483, "last_sector": 36722, "length": 66.4}
                        ],
                    },
                ]
            }
        )["track"]
        titles = self.ns["_arm_dvd_titles"](str(self.raw), {1: None, 2: None})
        self.assertEqual(
            titles[1]["segments"], [(1, 8483, 36723, 66.4), (1, 36723, 205855, 454.3)]
        )
        self.assertEqual(titles[1]["chapters"], [0, 66.4])
        self.assertEqual(titles[2]["source"], {"disc_title": 4})

    def test_a_dvd_without_a_title_map_is_refused(self) -> None:
        (self.raw / "disc.iso").write_bytes(b"")
        (self.raw / "arm-job.json").write_text(json.dumps({"extracted": None}))
        with self.assertRaises(ValueError):
            self.ns["_arm_dvd_titles"](str(self.raw), {1: None})


class EndToEnd(unittest.TestCase):
    """Downton Abbey D1's shape: four episodes and a play-all."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = Path(self.tmp.name, "Downton-Abbey_179280640613")
        folder = self.raw / "BDMV" / "PLAYLIST"
        folder.mkdir(parents=True)
        lengths = {1: 3944, 2: 2858, 3: 2854, 4: 2767}
        for playlist, length in lengths.items():
            (folder / f"0000{playlist}.mpls").write_bytes(
                mpls([(f"0000{playlist - 1}", 0, length * TICK)], [(1, 0, 0)])
            )
        clips = [(f"0000{p - 1}", 0, n * TICK) for p, n in lengths.items()] + [
            ("00011", 0, 6 * TICK)
        ]
        (folder / "00006.mpls").write_bytes(mpls(clips, [(1, i, 0) for i in range(4)]))
        (self.raw / "arm-job.json").write_text(
            json.dumps({"job_id": 80, "extracted": None}) + "\n"
        )
        self.fake = FakeSubprocess()
        self.fake.outputs["HandBrakeCLI"] = handbrake_scan(
            [(PLAY_ALL, 6), (2, 1), (3, 2), (4, 3), (5, 4), (7, 7)]
        )
        self.ns = load(self.fake)
        lengths_by_title = {2: 3944, 3: 2858, 4: 2854, 5: 2767, PLAY_ALL: 12432, 7: 16}
        self.tracks = Tracks(
            types.SimpleNamespace(
                track_number=str(n),
                length=n_len,
                main_feature=n == PLAY_ALL,
                source="HandBrake",
            )
            for n, n_len in lengths_by_title.items()
        )
        logging.disable(logging.CRITICAL)

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)
        self.tmp.cleanup()

    def job(self, video_type: str = "series") -> types.SimpleNamespace:
        return types.SimpleNamespace(
            tracks=self.tracks,
            disctype="bluray",
            video_type=video_type,
            title="Downton-Abbey",
        )

    def stamp(self) -> dict:
        return json.loads((self.raw / "arm-job.json").read_text())

    def test_the_play_all_is_skipped_and_recorded(self) -> None:
        skip = self.ns["_arm_duplicate_titles"](str(self.raw), self.job())
        self.assertEqual(list(skip), [6])
        duplicates = self.stamp()["duplicates"]
        self.assertEqual(duplicates["skipped"][0]["handbrake_title"], 6)
        self.assertEqual(duplicates["skipped"][0]["playlist"], "00006.mpls")
        whole = duplicates["wholes"][0]
        self.assertEqual((whole["kept"], whole["interleaved"]), ("parts", False))
        self.assertEqual([p["start"] for p in whole["parts"]], [0, 3944, 6802, 9656])
        self.assertEqual(self.stamp()["job_id"], 80)

    def test_the_main_feature_moves_to_the_longest_title_kept(self) -> None:
        self.ns["_arm_duplicate_titles"](str(self.raw), self.job())
        self.assertEqual([t.track_number for t in self.tracks if t.main_feature], ["2"])

    def test_titles_under_minlength_are_not_compared(self) -> None:
        self.ns["_arm_duplicate_titles"](str(self.raw), self.job())
        self.assertNotIn("00007", "".join(map(str, self.fake.commands)))
        compared = {
            t["handbrake_title"]
            for t in self.stamp()["duplicates"]["wholes"][0]["parts"]
        }
        self.assertNotIn(7, compared)

    def test_an_unreadable_disc_has_every_title_transcoded(self) -> None:
        (self.raw / "BDMV" / "PLAYLIST" / "00006.mpls").write_bytes(b"garbage")
        self.assertEqual(
            self.ns["_arm_duplicate_titles"](str(self.raw), self.job()), {}
        )
        self.assertNotIn("duplicates", self.stamp())
        self.assertEqual([t.track_number for t in self.tracks if t.main_feature], ["6"])

    def test_too_few_titles_records_that_none_were_skipped(self) -> None:
        self.tracks[:] = self.tracks[:2]
        self.assertEqual(
            self.ns["_arm_duplicate_titles"](str(self.raw), self.job()), {}
        )
        self.assertEqual(self.fake.commands, [])
        self.assertEqual(self.stamp()["duplicates"], {"skipped": [], "wholes": []})

    def interleave(self) -> None:
        # Title 6 now plays title 3 cut into title 2.
        (self.raw / "BDMV" / "PLAYLIST" / "00006.mpls").write_bytes(
            mpls(
                [
                    ("00000", 0, 1000 * TICK),
                    ("00001", 0, 2858 * TICK),
                    ("00000", 1000 * TICK, 3944 * TICK),
                ],
                [(1, 0, 0)],
            )
        )

    def test_an_interleaved_title_on_a_series_disc_is_not_notified(self) -> None:
        # A play-all opening on a recap its episodes share reads as interleaved.
        self.interleave()
        skip = self.ns["_arm_duplicate_titles"](str(self.raw), self.job())
        self.assertEqual(list(skip), [6])
        self.assertEqual(self.ns["notices"], [])

    def test_an_interleaved_title_on_a_movie_disc_is_notified(self) -> None:
        self.interleave()
        skip = self.ns["_arm_duplicate_titles"](str(self.raw), self.job("movie"))
        self.assertEqual(list(skip), [6])
        self.assertEqual(len(self.ns["notices"]), 1)
        self.assertIn("title 6", self.ns["notices"][0])

    def test_a_disc_of_neither_kind_is_left_alone(self) -> None:
        job = self.job()
        job.disctype = "music"
        self.assertEqual(self.ns["_arm_duplicate_titles"](str(self.raw), job), {})
        self.assertEqual(self.fake.commands, [])


if __name__ == "__main__":
    unittest.main()
