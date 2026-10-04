"""Tests for arm-title-charset.sh, which makes ARM's output paths writable.

Run with `python3 -m unittest discover tools/arm-title-charset`.

The fileserver runs Samba with `unix charset = ISO-8859-1`, so creating any file
whose name contains a character above U+00FF fails with EIO on every share.
Measured 2026-09-02; see todos/smb-charset-utf8.md for the real fix and why it is
a migration rather than a config change.

Until that lands, ARM cannot file a TV series: OMDb returns a series' year range
with an en dash, so job 20 died on
`/root/video/transcode/tv/The-Sylvester-and-Tweety-Mysteries (1995–2002)`.

ARM's own `clean_for_filename` does not help, for two reasons. It is applied to
*titles* only (`identify.py:141` and `:274`), while the en dash arrives through
`job.year`, which `fix_job_title` interpolates raw. And it would strip the dash
rather than replace it, turning `1995–2002` into `19952002`.

So this patches `fix_job_title` — the single funnel every output path goes
through — by appending a wrapper to `utils.py`. Appending rather than editing the
body, because every call site resolves the name at call time
(`utils.fix_job_title(...)` in arm_ripper.py, the bare name inside
`utils.move_files`), so rebinding the module global reaches all of them and does
not depend on the function's internals.

Latin-1 is the target rather than ASCII, deliberately: the fileserver accepts it
today, and `Mànran` already exists in the library under a Latin-1 name.
"""

import os
import subprocess
import tempfile
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
INIT_SCRIPTS = os.path.join(
    REPO, "kubernetes", "apps", "automatic-ripping-machine", "init-scripts.yaml"
)
UTILS = "/opt/arm/arm/ripper/utils.py"
PATCH_PY = "/usr/local/bin/arm-title-charset.py"
ARM_LOG = "/home/arm/logs/arm.log"

# Enough of ARM's utils.py to patch against.
STUB_UTILS = '''\
def fix_job_title(job):
    if job.year:
        return "%s (%s)" % (job.title, job.year)
    return job.title
'''


class Job:
    def __init__(self, title, year=None):
        self.title = title
        self.year = year
        self.title_manual = None


def load_configmap(key):
    with open(INIT_SCRIPTS) as handle:
        doc = yaml.safe_load(handle)
    try:
        return doc["data"][key]
    except KeyError:
        raise AssertionError(f"init-scripts.yaml has no {key} entry") from None


def sandbox(script, utils_path, patch_path, log):
    for original, replacement in (
        (UTILS, utils_path),
        (PATCH_PY, patch_path),
        (ARM_LOG, log),
    ):
        if original not in script:
            raise AssertionError(
                f"arm-title-charset.sh no longer contains {original!r}; "
                "update the constants at the top of this test."
            )
        script = script.replace(original, replacement)
    return script


class Harness:
    def __init__(self, tmpdir, utils_body=STUB_UTILS):
        self.tmpdir = tmpdir
        self.utils = os.path.join(tmpdir, "utils.py")
        self.log = os.path.join(tmpdir, "arm.log")
        with open(self.utils, "w") as fh:
            fh.write(utils_body)
        # The patch body ships as its own ConfigMap key, so the test uses the
        # real one rather than a copy.
        self.patch_py = os.path.join(tmpdir, "arm-title-charset.py")
        with open(self.patch_py, "w") as fh:
            fh.write(load_configmap("arm-title-charset.py"))
        self.script = os.path.join(tmpdir, "arm-title-charset.sh")
        with open(self.script, "w") as fh:
            fh.write(
                sandbox(
                    load_configmap("arm-title-charset.sh"),
                    self.utils,
                    self.patch_py,
                    self.log,
                )
            )
        os.chmod(self.script, 0o755)

    def run(self):
        return subprocess.run(
            ["bash", self.script], capture_output=True, text=True, timeout=60
        )

    def fix_job_title(self, job):
        """Import the patched module and call it, as ARM would."""
        ns = {}
        with open(self.utils) as fh:
            exec(compile(fh.read(), self.utils, "exec"), ns)
        return ns["fix_job_title"](job)

    def log_text(self):
        return open(self.log).read() if os.path.exists(self.log) else ""


class Sanitising(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.h = Harness(self.tmp.name)
        result = self.h.run()
        self.assertEqual(result.returncode, 0, result.stderr)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_case_that_broke_job_20(self):
        got = self.h.fix_job_title(
            Job("The-Sylvester-and-Tweety-Mysteries", "1995–2002")
        )
        self.assertEqual(got, "The-Sylvester-and-Tweety-Mysteries (1995-2002)")
        self.assertTrue(all(ord(c) < 0x100 for c in got))

    def test_dashes_become_hyphens_not_deleted(self):
        # ARM's own clean_for_filename would give "19952002", which is a
        # different year. Replacing beats stripping.
        for dash in "‐‑‒–—―":
            self.assertEqual(
                self.h.fix_job_title(Job("Show", f"1995{dash}2002")),
                "Show (1995-2002)",
            )

    def test_curly_quotes_and_ellipsis(self):
        self.assertEqual(
            self.h.fix_job_title(Job("Ocean’s Eleven", "2001")),
            "Ocean's Eleven (2001)",
        )
        self.assertEqual(
            self.h.fix_job_title(Job("And Then…", "1975")),
            "And Then... (1975)",
        )

    def test_latin1_is_preserved(self):
        # The share accepts it, and Mànran is already in the library this way.
        self.assertEqual(
            self.h.fix_job_title(Job("Mànran", "2013")), "Mànran (2013)"
        )

    def test_characters_with_no_latin1_form_are_dropped_not_left(self):
        got = self.h.fix_job_title(Job("Tokyo 中文", "2020"))
        self.assertTrue(
            all(ord(c) < 0x100 for c in got), f"{got!r} still has un-writable chars"
        )

    def test_plain_ascii_is_untouched(self):
        self.assertEqual(
            self.h.fix_job_title(Job("The-Hallelujah-Trail", "1965")),
            "The-Hallelujah-Trail (1965)",
        )

    def test_a_job_with_no_year_still_works(self):
        self.assertEqual(self.h.fix_job_title(Job("Some Disc")), "Some Disc")


class PatchSafety(unittest.TestCase):
    def test_running_twice_does_not_double_wrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp)
            h.run()
            first = open(h.utils).read()
            h.run()
            self.assertEqual(open(h.utils).read(), first, "patch is not idempotent")
            self.assertEqual(
                h.fix_job_title(Job("Show", "1995–2002")), "Show (1995-2002)"
            )

    def test_it_refuses_loudly_when_the_target_is_gone(self):
        # ARM now auto-updates, so a version that renames or moves
        # fix_job_title must not fail silently -- the symptom would be a TV rip
        # dying on EIO again with nothing pointing here.
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp, utils_body="def something_else(job):\n    return job\n")
            result = h.run()
            self.assertNotEqual(result.returncode, 0, "should exit non-zero")
            self.assertIn("fix_job_title", h.log_text())
            self.assertNotIn("_arm_charset_patch", open(h.utils).read())

    def test_the_original_function_still_does_its_own_job(self):
        # The wrapper must not replace ARM's logic, only clean its output.
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp)
            h.run()
            self.assertEqual(
                h.fix_job_title(Job("Le Mans", "1971")), "Le Mans (1971)"
            )


if __name__ == "__main__":
    unittest.main()
