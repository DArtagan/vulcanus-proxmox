"""Tests for arm-audio-handoff.sh, which moves a finished CD rip into the inbox.

Run with `python3 -m unittest discover tools/arm-audio-handoff`.

Like the wrapper tests next door, the script under test lives in
`kubernetes/apps/automatic-ripping-machine/init-scripts.yaml`, because that
ConfigMap is what gets mounted into the pod. These tests extract it from there
so there is one copy, and rewrite its absolute paths into a sandbox.

Why the script exists at all: abcde encodes each track straight to its output
directory, and beets-flask's watchdog enqueues an album 30s after it stops
changing. Those gaps happen *between tracks*, so it always saw a partial album,
created a session against it, and never re-enqueued — the album stayed in the
inbox and out of the library. Verified 2026-08-26.

The property that matters is therefore not "the files arrive" but **"a
partially-copied album is never visible as an album"**. beets-flask offers two
independent ways to be invisible, and the script uses both at once:

- `watchdog/inbox.py` drops any event whose basename starts with "."
- `disk.py`'s `audio_regex` counts a file as audio only when its *name* ends in
  an audio extension

So tracks are copied as `.<name>.part` and renamed only once every one has
landed. A dotted *directory* is not enough on its own — that was measured on
2026-08-26 with a matched control, and beets-flask enqueued the dotted and
undotted probes identically.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
INIT_SCRIPTS = (
    REPO / "kubernetes" / "apps" / "automatic-ripping-machine" / "init-scripts.yaml"
)

STAGING = "/home/arm/arm-incoming"
INBOX = "/root/audio"
ARM_LOG = "/home/arm/logs/arm.log"

COMPLETE = "Music CD: Mànran The Test processing complete."
# Real messages ARM sends that must not trigger a handoff.
ENTRY = "Found music CD: None. Ripping all tracks."
VIDEO = "The Rescuers rip complete. Starting transcode. "
FATAL = "ARM encountered a fatal error processing None. Could not determine disc type"

# Names taken from the real rip: spaces, and non-ASCII that survived abcde's
# mungefilename.
TRACKS = [
    "01 - MSR.flac",
    "03 - Dhèanainn Sùgradh.flac",
    "10 - Overtime.flac",
    "cover.jpg",
]


def load_script() -> str:
    doc = yaml.safe_load(INIT_SCRIPTS.read_text())
    try:
        return doc["data"]["arm-audio-handoff.sh"]
    except KeyError:
        message = "init-scripts.yaml has no arm-audio-handoff.sh entry"
        raise AssertionError(message) from None


def sandbox(script: str, staging: Path, inbox: Path, log: Path) -> str:
    for original, replacement in ((STAGING, staging), (INBOX, inbox), (ARM_LOG, log)):
        if original not in script:
            message = (
                f"arm-audio-handoff.sh no longer contains {original!r}; "
                "update the constants at the top of this test."
            )
            raise AssertionError(message)
        script = script.replace(original, str(replacement))
    return script


class Harness:
    def __init__(
        self, tmpdir: str, album: str = "Mànran The Test", tracks: list[str] = TRACKS
    ) -> None:
        self.tmpdir = Path(tmpdir)
        self.staging = self.tmpdir / "staging"
        self.inbox = self.tmpdir / "inbox"
        self.log = self.tmpdir / "arm.log"
        self.bindir = self.tmpdir / "bin"
        self.copies = self.tmpdir / "copy-destinations"
        self.bindir.mkdir()
        self.inbox.mkdir()
        self.album_dir = self.staging / album
        self.album_dir.mkdir(parents=True)
        for t in tracks:
            (self.album_dir / t).write_text(f"contents of {t}\n")

        # Record every destination cp is asked to write, so the test can assert
        # on what the inbox looked like mid-copy without racing it. Resolve the
        # real cp rather than assuming /bin/cp — there is no /bin on NixOS.
        self.real_cp = shutil.which("cp")
        if self.real_cp is None:
            message = "cp not found on PATH"
            raise AssertionError(message)
        self.write_executable(
            self.bindir / "cp",
            f'#!/bin/sh\necho "$2" >> {self.copies}\nexec {self.real_cp} "$@"\n',
        )

        self.script = self.tmpdir / "arm-audio-handoff.sh"
        self.write_executable(
            self.script, sandbox(load_script(), self.staging, self.inbox, self.log)
        )

    @staticmethod
    def write_executable(path: Path, body: str) -> None:
        path.write_text(body)
        path.chmod(0o700)

    def run(
        self, title: str = "ARM notification", body: str = COMPLETE
    ) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["PATH"] = f"{self.bindir}{os.pathsep}{env['PATH']}"
        return subprocess.run(
            ["bash", self.script, title, body],
            env=env,
            cwd=self.tmpdir,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def copy_destinations(self) -> list[str]:
        if not self.copies.exists():
            return []
        return [
            line.strip()
            for line in self.copies.read_text().splitlines()
            if line.strip()
        ]

    def inbox_tree(self, album: str = "Mànran The Test") -> list[str]:
        return listing(self.inbox / album)


def listing(directory: Path) -> list[str]:
    """Name what a directory holds, sorted; nothing if it does not exist."""
    return (
        sorted(entry.name for entry in directory.iterdir())
        if directory.is_dir()
        else []
    )


class OnlyOnCompletion(unittest.TestCase):
    """Only ARM's completion message hands an album off.

    ARM calls this for every notification, so the message is the only signal
    that a rip finished. Acting on the wrong one would move a half-written
    album.
    """

    def _run_with(self, body: str) -> tuple[list[str], bool]:
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp)
            h.run(body=body)
            return h.inbox_tree(), h.album_dir.is_dir()

    def test_entry_notification_does_nothing(self) -> None:
        moved, staging_intact = self._run_with(ENTRY)
        self.assertEqual(moved, [])
        self.assertTrue(staging_intact)

    def test_video_notification_does_nothing(self) -> None:
        moved, _ = self._run_with(VIDEO)
        self.assertEqual(moved, [])

    def test_fatal_error_notification_does_nothing(self) -> None:
        moved, staging_intact = self._run_with(FATAL)
        self.assertEqual(moved, [])
        self.assertTrue(staging_intact, "a failed rip must not be handed off")

    def test_completion_moves_the_album(self) -> None:
        moved, staging_intact = self._run_with(COMPLETE)
        self.assertEqual(moved, sorted(TRACKS))
        self.assertFalse(staging_intact, "staging should be cleared after a handoff")


class NeverVisibleWhileIncomplete(unittest.TestCase):
    """No intermediate filename reads as audio to beets-flask.

    The whole point. If any did, the race this script exists to remove is
    still there.
    """

    def test_every_copy_lands_under_a_name_beets_flask_ignores(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp)
            h.run()
            destinations = h.copy_destinations()
            self.assertEqual(len(destinations), len(TRACKS))
            for d in destinations:
                base = Path(d).name
                self.assertTrue(
                    base.startswith("."),
                    f"{base} does not start with '.', so the watchdog would see it",
                )
                self.assertFalse(
                    base.endswith((".flac", ".mp3", ".m4a", ".ogg", ".wav")),
                    f"{base} ends in an audio extension, so it counts as a track",
                )

    def test_final_names_are_restored_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp)
            h.run()
            self.assertEqual(h.inbox_tree(), sorted(TRACKS))

    def test_contents_survive_the_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp)
            h.run()
            self.assertEqual(
                (h.inbox / "Mànran The Test" / "01 - MSR.flac").read_text(),
                "contents of 01 - MSR.flac\n",
            )


class Safety(unittest.TestCase):
    def test_nothing_staged_is_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp, tracks=[])
            h.album_dir.rmdir()
            result = h.run()
            self.assertEqual(result.returncode, 0)

    def test_existing_destination_is_not_clobbered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp)
            existing = h.inbox / "Mànran The Test"
            existing.mkdir()
            (existing / "keep.flac").write_text("previous rip\n")
            h.run()
            self.assertEqual(
                h.inbox_tree(), ["keep.flac"], "the first album must be untouched"
            )


class CollidingAlbumNames(unittest.TestCase):
    """An album whose directory is taken lands under a disambiguated name.

    Two discs can produce the same album directory, and it is not a corner
    case: every disc MusicBrainz cannot identify is named
    `Unknown Artist Unknown Album`. Two of those in a row collide.

    Refusing the handoff and keeping the album in staging looks safe and is not,
    for two reasons found on 2026-08-29. Staging is container-local and
    ephemeral, so a retained album dies at the next pod restart. And abcde writes
    the *next* rip into that same directory, so an 8-track disc landing on a
    retained 11-track one leaves tracks 1-8 from the new disc and 9-11 from the
    old — a plausible-looking album that is two discs. Silent corruption, which
    is worse than either losing it or refusing loudly.

    So the album always lands; the name is disambiguated instead.
    """

    def _rip(self, harness: Harness, tracks: list[str], contents: str) -> None:
        harness.album_dir.mkdir(parents=True, exist_ok=True)
        for t in tracks:
            (harness.album_dir / t).write_text(contents)
        harness.run()

    def test_second_album_lands_beside_the_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp, album="Unknown Artist Unknown Album", tracks=TRACKS)
            h.run()
            self._rip(h, TRACKS, "second disc\n")
            self.assertEqual(
                listing(h.inbox),
                ["Unknown Artist Unknown Album", "Unknown Artist Unknown Album (2)"],
            )
            self.assertEqual(
                (h.inbox / "Unknown Artist Unknown Album (2)" / TRACKS[0]).read_text(),
                "second disc\n",
            )

    def test_third_album_lands_too(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp, album="Unknown Artist Unknown Album", tracks=TRACKS)
            h.run()
            self._rip(h, TRACKS, "second\n")
            self._rip(h, TRACKS, "third\n")
            self.assertIn("Unknown Artist Unknown Album (3)", listing(h.inbox))

    def test_staging_is_always_cleared_so_the_next_rip_cannot_merge(self) -> None:
        # The corruption path: staging left populated, then abcde rips a
        # shorter disc into it and the leftover tracks masquerade as part of it.
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp, album="Unknown Artist Unknown Album", tracks=TRACKS)
            h.run()
            self.assertFalse(h.album_dir.is_dir())
            self._rip(h, TRACKS, "second\n")
            self.assertFalse(
                h.album_dir.is_dir(),
                "staging must be empty after a colliding handoff too",
            )

    def test_the_first_album_is_never_modified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp, album="Unknown Artist Unknown Album", tracks=TRACKS)
            h.run()
            first = h.inbox / "Unknown Artist Unknown Album" / TRACKS[0]
            before = first.read_text()
            self._rip(h, TRACKS, "second disc\n")
            self.assertEqual(first.read_text(), before)

    def test_a_colliding_handoff_is_still_invisible_while_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp, album="Unknown Artist Unknown Album", tracks=TRACKS)
            h.run()
            h.copies.unlink()
            self._rip(h, TRACKS, "second\n")
            for d in h.copy_destinations():
                base = Path(d).name
                self.assertTrue(base.startswith("."), base)
                self.assertFalse(base.endswith(".flac"), base)

    def test_a_failed_copy_moves_staging_aside_so_it_cannot_be_merged_into(
        self,
    ) -> None:
        # The abort path is now the only one that keeps staging, which makes it
        # the only remaining way abcde's next rip could write into a directory
        # that still holds an album. Moving it aside closes that.
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp)
            h.write_executable(
                h.bindir / "cp",
                f'#!/bin/sh\necho "$2" >> {h.copies}\n'
                f'[ "$(wc -l < {h.copies})" -gt 1 ] && exit 1\n'
                f'exec {h.real_cp} "$@"\n',
            )
            h.run()
            self.assertFalse(
                h.album_dir.is_dir(),
                "the album abcde would rip into next must not still hold a rip",
            )
            kept = [d for d in listing(h.staging) if d.startswith("Mànran The Test")]
            self.assertEqual(len(kept), 1, f"the rip must be kept somewhere: {kept}")
            self.assertNotEqual(kept[0], "Mànran The Test")
            self.assertEqual(listing(h.staging / kept[0]), sorted(TRACKS))

    def test_a_failed_copy_leaves_no_audio_in_the_inbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp)
            # cp succeeds once, then fails: a disk filling up mid-album.
            h.write_executable(
                h.bindir / "cp",
                f'#!/bin/sh\necho "$2" >> {h.copies}\n'
                f'[ "$(wc -l < {h.copies})" -gt 1 ] && exit 1\n'
                f'exec {h.real_cp} "$@"\n',
            )
            h.run()
            self.assertEqual(
                h.inbox_tree(), [], "a partial album must not be left behind"
            )
            self.assertTrue(
                any(d.startswith("Mànran The Test") for d in listing(h.staging)),
                "the rip must survive a failed handoff, under some name",
            )


if __name__ == "__main__":
    unittest.main()
