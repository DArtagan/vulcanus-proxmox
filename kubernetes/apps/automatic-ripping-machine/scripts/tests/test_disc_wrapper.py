"""Tests for arm-disc-wrapper.sh, the udev gatekeeper in front of ARM.

Run with `python3 -m unittest discover -s .` from this directory.

The script runs with `python3`, `udevadm` and ARM's own entry point stubbed
onto PATH.

What the script decides matters more than it looks. `Job.parse_udev()` derives
`disctype` solely from the `ID_CDROM_MEDIA_*` udev properties, so invoking ARM
before those exist produces a job that dies with "Could not determine disc
type" and, because the drive record keeps it, blocks the next insert. The drive
reports CDS_DISC_OK before the TOC is readable, so the status ioctl alone is
not enough of a gate.

Note the two families of udev property are easy to confuse: `ID_CDROM_BD=1`
says the *drive* can read Blu-ray, `ID_CDROM_MEDIA_BD=1` says the *disc in the
tray* is one. Only the `_MEDIA_` ones answer the question.
"""

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent

# CDROM_DRIVE_STATUS values, from <linux/cdrom.h>.
CDS_NO_INFO = "0"
CDS_NO_DISC = "1"
CDS_TRAY_OPEN = "2"
CDS_DRIVE_NOT_READY = "3"
CDS_DISC_OK = "4"

# What `udevadm info` prints for a drive whose tray holds nothing identifiable:
# plenty of capability flags, no media flags. Taken from the real drive on
# 2026-08-25 with the tray open.
NO_MEDIA = """\
E: DEVNAME=/dev/sr0
E: ID_CDROM=1
E: ID_CDROM_BD=1
E: ID_CDROM_BD_RE=1
E: ID_CDROM_DVD=1
E: ID_CDROM_CD=1
E: ID_CDROM_RW_REMOVABLE=1
"""

AUDIO_CD = NO_MEDIA + "E: ID_CDROM_MEDIA=1\nE: ID_CDROM_MEDIA_TRACK_COUNT_AUDIO=12\n"
BLURAY = NO_MEDIA + "E: ID_CDROM_MEDIA=1\nE: ID_CDROM_MEDIA_BD=1\n"
DVD = NO_MEDIA + "E: ID_CDROM_MEDIA=1\nE: ID_CDROM_MEDIA_DVD=1\n"


# The two absolute paths the script writes to or hands off to. The test cannot
# shadow an absolute path with PATH, so it rewrites them to point inside the
# sandbox — and asserts each was present first, so a rename in the ConfigMap
# fails the tests rather than silently making them test nothing.
ARM_ENTRY_POINT = "/opt/arm/scripts/docker/docker_arm_wrapper.sh"
ARM_LOG = "/home/arm/logs/arm.log"
LOCK_DIR = "/run/lock"
# The real wait is 30 polls 2s apart, which is right for a Blu-ray reading its
# TOC and far too long to spend in a unit test. Shortened here rather than made
# configurable in the script, so the running system keeps exactly one value.
MEDIA_POLLS = "MEDIA_POLLS=30"
MEDIA_INTERVAL = "MEDIA_INTERVAL=2"


def load_wrapper() -> str:
    return (SCRIPTS / "arm-disc-wrapper.sh").read_text()


def sandbox_wrapper(script: str, entry_point: Path, log: Path, lock_dir: Path) -> str:
    for original, replacement in (
        (ARM_ENTRY_POINT, entry_point),
        (ARM_LOG, log),
        (LOCK_DIR, lock_dir),
        (MEDIA_POLLS, "MEDIA_POLLS=4"),
        (MEDIA_INTERVAL, "MEDIA_INTERVAL=0.05"),
    ):
        if original not in script:
            message = (
                f"arm-disc-wrapper.sh no longer contains {original!r}; "
                "update the constants at the top of this test to match."
            )
            raise AssertionError(message)
        script = script.replace(original, str(replacement))
    return script


class WrapperHarness:
    """Runs the wrapper with its three external dependencies stubbed.

    - `python3` stands in for the CDROM_DRIVE_STATUS ioctl and prints
      whatever status the test asked for.
    - `udevadm` prints a canned property block.
    - ARM's entry point records that it was called, and how many times.
    """

    def __init__(
        self,
        tmpdir: str,
        status: str,
        udev_output: str,
        flaky_until: int = 0,
        *,
        hold: bool = False,
    ) -> None:
        self.tmpdir = Path(tmpdir)
        self.bindir = self.tmpdir / "bin"
        self.armdir = self.tmpdir / "opt" / "arm" / "scripts" / "docker"
        self.calls = self.tmpdir / "arm-invocations"
        self.probes = self.tmpdir / "udev-probes"
        self.bindir.mkdir()
        self.armdir.mkdir(parents=True)
        (self.tmpdir / "home" / "arm" / "logs").mkdir(parents=True)

        self._write(self.bindir / "python3", f"#!/bin/sh\necho {status}\n")

        # `flaky_until` reproduces the real race: the first N probes see only
        # the drive's capability flags, later ones see the disc as well.
        empty = self.tmpdir / "udev-no-media"
        loaded = self.tmpdir / "udev-media"
        empty.write_text(NO_MEDIA)
        loaded.write_text(udev_output)
        self._write(
            self.bindir / "udevadm",
            "#!/bin/sh\n"
            f"echo probe >> {self.probes}\n"
            f"n=$(wc -l < {self.probes})\n"
            f'if [ "$n" -le {flaky_until} ]; then cat {empty}; else cat {loaded}; fi\n',
        )

        # ARM's entry point is exec'd, so the wrapper's lock file descriptor is
        # inherited by it and the lock is held for as long as it runs. With
        # `hold`, it stands in for a rip until the test calls `release`.
        self.entry_point = self.armdir / "docker_arm_wrapper.sh"
        self.released = self.tmpdir / "released"
        wait = f"until [ -e {self.released} ]; do sleep 0.01; done\n" if hold else ""
        self._write(
            self.entry_point,
            f'#!/bin/sh\necho "$@" >> {self.calls}\n{wait}',
        )

        self.log = self.tmpdir / "home" / "arm" / "logs" / "arm.log"
        self.script = self.tmpdir / "arm-disc-wrapper.sh"
        self.lock_dir = self.tmpdir / "lock"
        self.lock_dir.mkdir()
        self._write(
            self.script,
            sandbox_wrapper(load_wrapper(), self.entry_point, self.log, self.lock_dir),
        )

    @staticmethod
    def _write(path: Path, body: str) -> None:
        path.write_text(body)
        path.chmod(0o700)

    def run(
        self, devname: str = "sr0", timeout: int = 60
    ) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["PATH"] = f"{self.bindir}{os.pathsep}{env['PATH']}"
        return subprocess.run(
            ["bash", self.script, devname],
            env=env,
            cwd=self.tmpdir,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def start(self, devname: str = "sr0") -> subprocess.Popen[bytes]:
        """Run the wrapper in the background, as udev does."""
        env = dict(os.environ)
        env["PATH"] = f"{self.bindir}{os.pathsep}{env['PATH']}"
        return subprocess.Popen(
            ["bash", self.script, devname],
            env=env,
            cwd=self.tmpdir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def release(self) -> None:
        self.released.touch()

    def invocations(self) -> list[str]:
        if not self.calls.exists():
            return []
        return [
            line.strip() for line in self.calls.read_text().splitlines() if line.strip()
        ]


class DriveStatusGate(unittest.TestCase):
    """Only a closed tray with a disc in it reaches ARM."""

    def _run(
        self, status: str, udev: str = AUDIO_CD, flaky_until: int = 0
    ) -> list[str]:
        with tempfile.TemporaryDirectory() as tmpdir:
            harness = WrapperHarness(tmpdir, status, udev, flaky_until)
            harness.run()
            return harness.invocations()

    def test_tray_open_does_not_invoke_arm(self) -> None:
        self.assertEqual(self._run(CDS_TRAY_OPEN), [])

    def test_no_disc_does_not_invoke_arm(self) -> None:
        self.assertEqual(self._run(CDS_NO_DISC), [])

    def test_drive_not_ready_does_not_invoke_arm(self) -> None:
        self.assertEqual(self._run(CDS_DRIVE_NOT_READY), [])

    def test_no_info_does_not_invoke_arm(self) -> None:
        self.assertEqual(self._run(CDS_NO_INFO), [])

    def test_disc_ok_invokes_arm_once_with_the_device_name(self) -> None:
        self.assertEqual(self._run(CDS_DISC_OK), ["sr0"])


class MediaPropertyGate(unittest.TestCase):
    """ARM waits for udev's media properties, not only the drive's status.

    The case that produced job 11 on 2026-08-24: the drive reported
    CDS_DISC_OK while udev still had no media properties, so ARM started, could
    not determine the disc type, failed, and left the drive holding the job.
    """

    def _run(
        self, udev: str, flaky_until: int = 0
    ) -> tuple[list[str], subprocess.CompletedProcess[str]]:
        with tempfile.TemporaryDirectory() as tmpdir:
            harness = WrapperHarness(tmpdir, CDS_DISC_OK, udev, flaky_until)
            result = harness.run()
            return harness.invocations(), result

    def test_disc_ok_but_no_media_properties_does_not_invoke_arm(self) -> None:
        invocations, _ = self._run(NO_MEDIA)
        self.assertEqual(invocations, [])

    def test_capability_flags_alone_are_not_mistaken_for_media(self) -> None:
        # NO_MEDIA already carries ID_CDROM_BD and ID_CDROM_DVD. A gate that
        # matched ID_CDROM_ rather than ID_CDROM_MEDIA_ would pass this disc
        # straight through, which is the bug this test exists to prevent.
        invocations, _ = self._run(NO_MEDIA)
        self.assertEqual(invocations, [])

    def test_media_appearing_late_is_waited_for_rather_than_dropped(self) -> None:
        # The properties show up on the third probe. ARM must still be run:
        # dropping the event would mean a disc that is never seen at all.
        invocations, _ = self._run(BLURAY, flaky_until=2)
        self.assertEqual(invocations, ["sr0"])

    def test_audio_cd_reaches_arm(self) -> None:
        invocations, _ = self._run(AUDIO_CD)
        self.assertEqual(invocations, ["sr0"])

    def test_dvd_reaches_arm(self) -> None:
        invocations, _ = self._run(DVD)
        self.assertEqual(invocations, ["sr0"])


class ConcurrentEvents(unittest.TestCase):
    """Of several events for one disc, only one reaches ARM.

    One insert produces several udev change events, and an ATA link reset
    during a rip produces more. Before the lock, each one started its own ARM
    process; the losers died with "Job already running on /dev/sr0" after
    creating a notification, and on 2026-04-22 three did so in 90 seconds.
    """

    def test_an_event_while_a_job_holds_the_drive_doesnt_reach_arm(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            harness = WrapperHarness(tmpdir, CDS_DISC_OK, BLURAY, hold=True)
            first = harness.start()
            try:
                deadline = time.monotonic() + 10
                while not harness.invocations():
                    self.assertLess(time.monotonic(), deadline, "ARM never started")
                    time.sleep(0.01)
                # The first job is in ARM, holding the lock, when the next
                # event comes: it is turned away, not queued behind it.
                result = harness.run(timeout=10)
                self.assertEqual(result.returncode, 0)
                self.assertIn("a job already holds", harness.log.read_text())
            finally:
                harness.release()
                first.wait(timeout=10)
            self.assertEqual(harness.invocations(), ["sr0"])

    def test_the_lock_is_released_once_the_job_ends(self) -> None:
        # Otherwise the first disc of a session would be the only one ever
        # ripped without a pod restart.
        with tempfile.TemporaryDirectory() as tmpdir:
            harness = WrapperHarness(tmpdir, CDS_DISC_OK, BLURAY)
            harness.run()
            harness.run()
            self.assertEqual(harness.invocations(), ["sr0", "sr0"])


if __name__ == "__main__":
    unittest.main()
