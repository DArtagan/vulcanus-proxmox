"""Tests for arm-source-patches.py and the code it appends to ARM's makemkv.py.

Run with `python3 -m unittest discover tools/arm-source-patches`.

The patcher is run in a sandbox against stub modules holding the text each edit
replaces. Whether that text still matches ARM's real source is a property of
the image, not of this repository, and shows at container start as one
`source-patches: applied` line per edit in arm.log.

The appended code is exercised against a fake MakeMKV: `run` answers each
command the way makemkvcon would, and a backup writes a disc image or a BDMV
tree whose size the test chooses. Sizes sit at the boundary that matters, a
file of exactly the declared size against one a byte short.
"""

import datetime
import json
import logging
import os
import shlex
import struct
import subprocess
import tempfile
import types
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
INIT_SCRIPTS = (
    REPO / "kubernetes" / "apps" / "automatic-ripping-machine" / "init-scripts.yaml"
)
RIPPER = "/opt/arm/arm/ripper"
ARM_LOG = "/home/arm/logs/arm.log"
WHOLE_DISC_PY = "/usr/local/bin/arm-whole-disc.py"

SECTOR = 2048
PACKET = 192
APPLIED_EDITS = 5


def load_configmap(key: str) -> str:
    doc = yaml.safe_load(INIT_SCRIPTS.read_text())
    try:
        return doc["data"][key]
    except KeyError:
        message = f"init-scripts.yaml has no {key} entry"
        raise AssertionError(message) from None


def patcher_namespace() -> dict:
    """Read the patcher's EDITS and APPENDS without running it."""
    source = load_configmap("arm-source-patches.py")
    head = source.split("\ndef log(", 1)[0]
    ns: dict = {}
    exec(compile(head, "arm-source-patches.py", "exec"), ns)  # noqa: S102
    return ns


# --- the patcher ------------------------------------------------------------


class Patcher:
    """arm-source-patches.py in a temporary directory, against stub modules."""

    def __init__(self, tmpdir: str) -> None:
        self.root = Path(tmpdir)
        self.ripper = self.root / "ripper"
        self.ripper.mkdir()
        self.log = self.root / "arm.log"
        self.whole_disc = self.root / "arm-whole-disc.py"
        self.whole_disc.write_text(load_configmap("arm-whole-disc.py"))
        ns = patcher_namespace()
        self.edits = [
            (name, Path(path.replace(RIPPER, str(self.ripper))), old)
            for name, path, old, _ in ns["EDITS"]
        ]
        # Each stub holds the text its edits replace, between lines of its own.
        for path in {path for _, path, _ in self.edits}:
            olds = [old for _, p, old in self.edits if p == path]
            path.write_text("# head\n" + "# between\n".join(olds) + "# tail\n")
        script = load_configmap("arm-source-patches.py")
        for original, replacement in (
            (RIPPER, self.ripper),
            (ARM_LOG, self.log),
            (WHOLE_DISC_PY, self.whole_disc),
        ):
            if original not in script:
                message = (
                    f"arm-source-patches.py no longer contains {original!r}; "
                    "update the constants at the top of this test."
                )
                raise AssertionError(message)
            script = script.replace(original, str(replacement))
        self.script = self.root / "arm-source-patches.py"
        self.script.write_text(script)

    def run(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", self.script],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def log_lines(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []


class Patching(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Patcher(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_every_edit_applies_once(self) -> None:
        result = self.p.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        applied = [line for line in self.p.log_lines() if "applied" in line]
        self.assertEqual(len(applied), APPLIED_EDITS, applied)
        for name, path, old in self.p.edits:
            text = path.read_text()
            self.assertIn(f"_arm_source_patch {name}", text)
            self.assertNotIn(old, text, f"{name} left its original text")

    def test_the_code_is_appended_before_the_edit_that_calls_it(self) -> None:
        self.p.run()
        order = [line.split("applied ", 1)[1].split()[0] for line in self.p.log_lines()]
        self.assertLess(order.index("whole-disc-code"), order.index("whole-disc-route"))
        makemkv = (self.p.ripper / "makemkv.py").read_text()
        self.assertIn("def _arm_backup_dvd(job, rawpath):", makemkv)

    def test_a_restart_applies_nothing_again(self) -> None:
        # The container keeps its patched files across a restart.
        self.p.run()
        before = {path: path.read_text() for _, path, _ in self.p.edits}
        result = self.p.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({path: path.read_text() for path in before}, before)
        applied = [line for line in self.p.log_lines() if "applied" in line]
        self.assertEqual(len(applied), APPLIED_EDITS)

    def test_moved_text_is_skipped_loudly_and_the_rest_still_apply(self) -> None:
        # An ARM update that rewrites makemkv()'s routing must not take the D14
        # edits down with it, and must say so in arm.log.
        makemkv = self.p.ripper / "makemkv.py"
        makemkv.write_text(
            makemkv.read_text().replace("# Rip BluRay\n", "# Rip a Blu-ray\n")
        )
        result = self.p.run()
        self.assertNotEqual(result.returncode, 0)
        log = "\n".join(self.p.log_lines())
        self.assertIn("cannot apply whole-disc-route", log)
        self.assertIn("applied d14-encode", log)
        self.assertNotIn("_arm_source_patch whole-disc-route", makemkv.read_text())

    def test_patch_names_are_not_prefixes_of_each_other(self) -> None:
        # The marker check is a substring test, so a name that prefixes another
        # would read as applied whenever the longer one is.
        ns = patcher_namespace()
        names = [entry[0] for entry in ns["APPENDS"]] + [e[0] for e in ns["EDITS"]]
        for a in names:
            for b in names:
                if a != b:
                    self.assertFalse(b.startswith(a), f"{a} prefixes {b}")


# --- the appended code ------------------------------------------------------


class Message:
    def __init__(self, text: str) -> None:
        self.message = text


class FakeMakeMKV:
    """Answers makemkvcon commands as run() would yield them."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.backup = lambda _dest: None
        self.backup_messages: list[str] = ["Backup done"]
        self.titles_saved = ["B1_t00.mkv", "A2_t01.mkv"]

    def run(self, cmd: list[str], _select: object):  # noqa: ANN201 - a generator, as ARM's is
        self.commands.append(list(cmd))
        verb = cmd[0]
        if verb == "backup":
            self.backup(cmd[-1])
            # ARM's output checker returns None for some messages.
            yield None
            yield from (Message(text) for text in self.backup_messages)
        elif verb == "info":
            yield ("TINFO", 0)
            yield ("TINFO", 1)
        elif verb == "mkv":
            dest = cmd[cmd.index("all") + 1]
            for name in self.titles_saved:
                Path(dest, name).write_bytes(b"mkv")
            yield Message(f"{len(self.titles_saved)} titles saved")


class TrackInfoProcessor:
    seen: list = []  # noqa: RUF012 - reset per test

    def __init__(self, job: object, index: object) -> None:
        self.job = job
        self.index = index

    def _process_message(self, message: object) -> None:
        TrackInfoProcessor.seen.append(message)

    def _add_track(self) -> None:
        TrackInfoProcessor.seen.append("added")


class RipperError(Exception):
    pass


def whole_disc(fake: FakeMakeMKV) -> dict:
    """Load the appended code into a namespace standing in for makemkv.py's."""
    ns = {
        "os": os,
        "logging": logging,
        "shlex": shlex,
        "collections": __import__("collections"),
        "run": fake.run,
        "progress_log": lambda job: f"/root/logs/progress/{job.job_id}.log",
        "OutputType": types.SimpleNamespace(
            CINFO=1, SINFO=2, TCOUNT=4, TINFO=8, MSG=16
        ),
        "TrackInfoProcessor": TrackInfoProcessor,
        "utils": types.SimpleNamespace(RipperException=RipperError),
    }
    exec(compile(load_configmap("arm-whole-disc.py"), "arm-whole-disc.py", "exec"), ns)  # noqa: S102
    return ns


def make_job(**overrides: object) -> types.SimpleNamespace:
    job = types.SimpleNamespace(
        job_id=76,
        label="PINKY_AND_THE_BRAIN_D2",
        disctype="dvd",
        crc_id="7969bce42a920b30",
        stage="179258877540",
        start_time=datetime.datetime(2026, 10, 21, 13, 19, 30, tzinfo=datetime.UTC),
        drive=types.SimpleNamespace(mdisc=0),
        config=types.SimpleNamespace(MINLENGTH="420", MKV_ARGS=""),
    )
    for key, value in overrides.items():
        setattr(job, key, value)
    return job


def write_iso(path: str, blocks: int, *, size: int | None = None) -> None:
    """Write a disc image whose volume descriptor declares `blocks` sectors."""
    descriptor = bytearray(SECTOR)
    descriptor[:6] = b"\x01CD001"
    struct.pack_into("<I", descriptor, 80, blocks)
    struct.pack_into(">I", descriptor, 84, blocks)
    struct.pack_into("<H", descriptor, 128, SECTOR)
    with Path(path).open("wb") as f:
        f.seek(16 * SECTOR)
        f.write(descriptor)
        f.truncate(blocks * SECTOR if size is None else size)


def write_clip(raw: Path, clip: str, packets: int, *, size: int | None) -> None:
    """Write a CLIPINF entry declaring `packets`, and its stream at `size` bytes."""
    clipinf = raw / "BDMV" / "CLIPINF"
    stream = raw / "BDMV" / "STREAM"
    clipinf.mkdir(parents=True, exist_ok=True)
    stream.mkdir(parents=True, exist_ok=True)
    header = bytearray(64)
    header[:8] = b"HDMV0200"
    struct.pack_into(">I", header, 56, packets)
    (clipinf / f"{clip}.clpi").write_bytes(bytes(header))
    if size is not None:
        with (stream / f"{clip}.m2ts").open("wb") as f:
            f.truncate(size)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = Path(self.tmp.name, "Pinky-and-the-Brain_179258877540")
        self.raw.mkdir()
        self.fake = FakeMakeMKV()
        self.ns = whole_disc(self.fake)
        TrackInfoProcessor.seen = []
        logging.disable(logging.CRITICAL)

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)
        self.tmp.cleanup()

    def stamp(self) -> dict:
        return json.loads((self.raw / "arm-job.json").read_text())

    def complete(self) -> str:
        """Return the stamp's `complete` as JSON, where null and false differ."""
        return json.dumps(self.stamp()["complete"])


class Stamp(Base):
    def test_written_at_the_start_with_completeness_unknown(self) -> None:
        self.ns["_arm_write_stamp"](make_job(), str(self.raw))
        self.assertEqual(
            self.stamp(),
            {
                "schema": 1,
                "job_id": 76,
                "label": "PINKY_AND_THE_BRAIN_D2",
                "disctype": "dvd",
                "crc_id": "7969bce42a920b30",
                "stage": "179258877540",
                "start_time": "2026-10-21T13:19:30+00:00",
                "complete": None,
                "problems": [],
            },
        )

    def test_start_time_is_written_in_utc(self) -> None:
        mountain = datetime.timezone(datetime.timedelta(hours=-6))
        start = datetime.datetime(2026, 10, 21, 7, 19, 30, tzinfo=mountain)
        self.ns["_arm_write_stamp"](make_job(start_time=start), str(self.raw))
        self.assertEqual(self.stamp()["start_time"], "2026-10-21T13:19:30+00:00")

    def test_a_blu_ray_has_no_crc(self) -> None:
        job = make_job(disctype="bluray", crc_id=None)
        self.ns["_arm_write_stamp"](job, str(self.raw))
        self.assertIsNone(self.stamp()["crc_id"])

    def test_rewritten_in_place_and_leaves_no_temporary(self) -> None:
        job = make_job()
        self.ns["_arm_write_stamp"](job, str(self.raw))
        self.ns["_arm_write_stamp"](job, str(self.raw), ["x is missing"])
        self.assertEqual([path.name for path in self.raw.iterdir()], ["arm-job.json"])
        self.assertEqual(self.complete(), "false")
        self.assertEqual(self.stamp()["problems"], ["x is missing"])

    def test_a_failed_check_fails_the_job_after_recording_it(self) -> None:
        with self.assertRaises(RipperError):
            self.ns["_arm_record_check"](make_job(), str(self.raw), ["x is missing"])
        self.assertEqual(self.complete(), "false")

    def test_a_clean_check_records_complete(self) -> None:
        self.ns["_arm_record_check"](make_job(), str(self.raw), [])
        self.assertEqual(self.complete(), "true")


class Backup(Base):
    def test_the_command_is_makemkv_backups_own(self) -> None:
        # The Blu-ray path is proven on this command; only the messages differ.
        self.ns["_arm_backup"](make_job(), "/dest")
        self.assertEqual(
            self.fake.commands,
            [
                [
                    "backup",
                    "--decrypt",
                    "--minlength=420",
                    "--progress=/root/logs/progress/76.log",
                    "disc:0",
                    "/dest",
                ]
            ],
        )

    def test_mkv_args_are_passed_through(self) -> None:
        job = make_job()
        job.config.MKV_ARGS = "--debug=-stdout --cache=16"
        self.ns["_arm_backup"](job, "/dest")
        self.assertEqual(self.fake.commands[0][2:4], ["--debug=-stdout", "--cache=16"])

    def test_hash_check_failures_are_reported(self) -> None:
        self.fake.backup_messages = [
            "Backup done but 3 files failed hash check",
            "Backup done.",
        ]
        self.assertEqual(
            self.ns["_arm_backup"](make_job(), "/dest"),
            ["Backup done but 3 files failed hash check"],
        )

    def test_a_clean_backup_reports_nothing(self) -> None:
        self.assertEqual(self.ns["_arm_backup"](make_job(), "/dest"), [])


class BluRay(Base):
    def problems(self) -> list[str]:
        return self.ns["_arm_bluray_problems"](str(self.raw))

    def test_every_clip_at_its_declared_size_is_complete(self) -> None:
        write_clip(self.raw, "00000", 1000, size=1000 * PACKET)
        write_clip(self.raw, "00001", 1, size=PACKET)
        self.assertEqual(self.problems(), [])

    def test_a_stream_one_byte_short_is_incomplete(self) -> None:
        write_clip(self.raw, "00000", 1000, size=1000 * PACKET)
        write_clip(self.raw, "00001", 1000, size=1000 * PACKET - 1)
        self.assertEqual(
            self.problems(),
            [f"STREAM/00001.m2ts is {1000 * PACKET - 1} bytes of {1000 * PACKET}"],
        )

    def test_a_missing_stream_is_incomplete(self) -> None:
        # How the seven 2024 backups D16 found lack their films.
        write_clip(self.raw, "00000", 1000, size=1000 * PACKET)
        write_clip(self.raw, "00800", 50_000_000, size=None)
        self.assertEqual(self.problems(), ["STREAM/00800.m2ts is missing"])

    def test_no_clip_info_is_incomplete(self) -> None:
        self.assertEqual(self.problems(), ["BDMV/CLIPINF is missing"])
        (self.raw / "BDMV" / "CLIPINF").mkdir(parents=True)
        self.assertEqual(self.problems(), ["BDMV/CLIPINF holds no clips"])

    def test_a_truncated_clip_info_is_incomplete(self) -> None:
        write_clip(self.raw, "00000", 1, size=PACKET)
        (self.raw / "BDMV" / "CLIPINF" / "00000.clpi").write_bytes(b"HDMV0200")
        self.assertEqual(self.problems(), ["CLIPINF/00000.clpi is too short to read"])


class Iso(Base):
    def iso(self) -> str:
        return str(self.raw / "disc.iso")

    def test_exactly_the_declared_size_is_complete(self) -> None:
        write_iso(self.iso(), 2_000_000)
        self.assertEqual(self.ns["_arm_iso_problems"](self.iso()), [])

    def test_one_byte_short_is_incomplete(self) -> None:
        write_iso(self.iso(), 2_000_000, size=2_000_000 * SECTOR - 1)
        self.assertEqual(
            self.ns["_arm_iso_problems"](self.iso()),
            [f"disc.iso is {2_000_000 * SECTOR - 1} bytes of {2_000_000 * SECTOR}"],
        )

    def test_missing_is_incomplete(self) -> None:
        self.assertEqual(
            self.ns["_arm_iso_problems"](self.iso()), ["disc.iso is missing"]
        )

    def test_an_image_without_a_volume_descriptor_is_incomplete(self) -> None:
        # Cut off before sector 16, or not a disc image at all.
        Path(self.iso()).write_bytes(b"\0" * (16 * SECTOR))
        self.assertEqual(
            self.ns["_arm_iso_problems"](self.iso()),
            ["disc.iso has no ISO 9660 volume descriptor"],
        )


class Dvd(Base):
    BLOCKS = 3_000_000

    def setUp(self) -> None:
        super().setUp()
        self.fake.backup = lambda dest: write_iso(dest, self.BLOCKS)

    def backup_dvd(self) -> str:
        return self.ns["_arm_backup_dvd"](make_job(), str(self.raw))

    def test_the_image_is_named_after_its_folder(self) -> None:
        # Given a folder, MakeMKV refuses a DVD backup; it takes a file name.
        self.backup_dvd()
        iso = str(self.raw / "Pinky-and-the-Brain_179258877540.iso")
        self.assertEqual(self.fake.commands[0][0], "backup")
        self.assertEqual(self.fake.commands[0][-1], iso)
        self.assertTrue(Path(iso).is_file())

    def test_the_drive_is_read_once(self) -> None:
        # The backup is the one command that names the disc; the rest read the
        # image, with --noscan so MakeMKV does not probe the drive either.
        self.backup_dvd()
        verbs = [cmd[0] for cmd in self.fake.commands]
        self.assertEqual(verbs, ["backup", "info", "mkv"])
        iso = f"iso:{self.raw / 'Pinky-and-the-Brain_179258877540.iso'}"
        for cmd in self.fake.commands[1:]:
            self.assertIn("--noscan", cmd)
            self.assertIn(iso, cmd)
            self.assertFalse(
                [arg for arg in cmd if arg.startswith(("disc:", "dev:"))], cmd
            )

    def test_titles_go_to_their_own_folder_which_is_returned(self) -> None:
        # HandBrake scans the folder it is given and counts every file in it,
        # so the image and the stamp stay one level up.
        titles = self.backup_dvd()
        self.assertEqual(titles, str(self.raw / "titles"))
        names = sorted(path.name for path in Path(titles).iterdir())
        self.assertEqual(names, ["A2_t01.mkv", "B1_t00.mkv"])
        mkv = self.fake.commands[2]
        self.assertEqual(mkv[mkv.index("all") + 1], titles)
        self.assertIn("--minlength=420", mkv)

    def test_makemkvs_track_rows_come_from_the_image(self) -> None:
        self.backup_dvd()
        self.assertEqual(TrackInfoProcessor.seen, [("TINFO", 0), ("TINFO", 1), "added"])
        self.assertIn("--minlength=420", self.fake.commands[1])

    def test_a_complete_image_is_stamped_complete(self) -> None:
        self.backup_dvd()
        self.assertEqual(self.complete(), "true")

    def test_a_short_image_fails_before_anything_is_extracted(self) -> None:
        self.fake.backup = lambda dest: write_iso(
            dest, self.BLOCKS, size=self.BLOCKS * SECTOR - 1
        )
        with self.assertRaises(RipperError):
            self.backup_dvd()
        self.assertEqual([cmd[0] for cmd in self.fake.commands], ["backup"])
        self.assertEqual(self.complete(), "false")
        self.assertFalse((self.raw / "titles").exists())

    def test_hash_check_failures_fail_the_job(self) -> None:
        self.fake.backup_messages = ["Backup done but 1 files failed hash check"]
        with self.assertRaises(RipperError):
            self.backup_dvd()
        self.assertEqual(
            self.stamp()["problems"], ["Backup done but 1 files failed hash check"]
        )

    def test_no_titles_extracted_fails_the_job(self) -> None:
        # The image is the archive and stays complete; the job still fails,
        # because nothing will be transcoded.
        self.fake.titles_saved = []
        with self.assertRaises(RipperError):
            self.backup_dvd()
        self.assertEqual(self.complete(), "true")


if __name__ == "__main__":
    unittest.main()
