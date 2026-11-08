"""Tests for arm-title-charset.py, which makes ARM's output paths writable.

Run with `python3 -m unittest discover tools/arm-source-patches`.

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
through — by appending a wrapper to `utils.py`, which arm-source-patches.py does
and test_patches.py tests. Appending rather than editing the
body, because every call site resolves the name at call time
(`utils.fix_job_title(...)` in arm_ripper.py, the bare name inside
`utils.move_files`), so rebinding the module global reaches all of them and does
not depend on the function's internals.

Latin-1 is the target rather than ASCII, deliberately: the fileserver accepts it
today, and `Mànran` already exists in the library under a Latin-1 name.
"""  # noqa: RUF002 - quotes job 20's path, en dash and all

import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SCRIPTS = REPO / "kubernetes" / "apps" / "automatic-ripping-machine" / "scripts"

# Samba's `unix charset = ISO-8859-1` writes every code point below this.
BEYOND_LATIN1 = 0x100

# Enough of ARM's utils.py to wrap.
STUB_UTILS = """\
def fix_job_title(job):
    if job.year:
        return "%s (%s)" % (job.title, job.year)
    return job.title
"""


class Job:
    def __init__(self, title: str, year: str | None = None) -> None:
        self.title = title
        self.year = year
        self.title_manual = None


def fix_job_title(job: Job) -> str:
    """Load utils.py with the wrapper appended, as ARM would, and call it."""
    code = STUB_UTILS + "\n\n" + (SCRIPTS / "arm-title-charset.py").read_text()
    ns: dict = {}
    exec(compile(code, "utils.py", "exec"), ns)  # noqa: S102
    return ns["fix_job_title"](job)


class Sanitising(unittest.TestCase):
    def test_the_case_that_broke_job_20(self) -> None:
        got = fix_job_title(
            Job("The-Sylvester-and-Tweety-Mysteries", "1995\N{EN DASH}2002")
        )
        self.assertEqual(got, "The-Sylvester-and-Tweety-Mysteries (1995-2002)")
        self.assertTrue(all(ord(c) < BEYOND_LATIN1 for c in got))

    def test_dashes_become_hyphens_not_deleted(self) -> None:
        # ARM's own clean_for_filename would give "19952002", which is a
        # different year. Replacing beats stripping.
        for dash in (
            "\N{HYPHEN}",
            "\N{NON-BREAKING HYPHEN}",
            "\N{FIGURE DASH}",
            "\N{EN DASH}",
            "\N{EM DASH}",
            "\N{HORIZONTAL BAR}",
        ):
            self.assertEqual(
                fix_job_title(Job("Show", f"1995{dash}2002")), "Show (1995-2002)"
            )

    def test_curly_quotes_and_ellipsis(self) -> None:
        self.assertEqual(
            fix_job_title(Job("Ocean\N{RIGHT SINGLE QUOTATION MARK}s Eleven", "2001")),
            "Ocean's Eleven (2001)",
        )
        self.assertEqual(
            fix_job_title(Job("And Then\N{HORIZONTAL ELLIPSIS}", "1975")),
            "And Then... (1975)",
        )

    def test_latin1_is_preserved(self) -> None:
        # The share accepts it, and Mànran is already in the library this way.
        self.assertEqual(fix_job_title(Job("Mànran", "2013")), "Mànran (2013)")

    def test_characters_with_no_latin1_form_are_dropped_not_left(self) -> None:
        got = fix_job_title(Job("Tokyo 中文", "2020"))
        self.assertTrue(
            all(ord(c) < BEYOND_LATIN1 for c in got),
            f"{got!r} still has un-writable chars",
        )

    def test_the_original_function_still_does_its_own_job(self) -> None:
        # The wrapper must not replace ARM's logic, only clean its output.
        self.assertEqual(fix_job_title(Job("Le Mans", "1971")), "Le Mans (1971)")
        self.assertEqual(fix_job_title(Job("Some Disc")), "Some Disc")


if __name__ == "__main__":
    unittest.main()
