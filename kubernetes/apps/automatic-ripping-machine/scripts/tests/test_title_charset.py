"""Tests for arm-title-charset.py, which makes ARM's output paths writable.

Run with `python3 -m unittest discover -s .` from this directory.

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

ASCII is the target rather than Latin-1 (2026-11-10), to match reelbarrow, which
files these titles into the library: an ASCII name is the same bytes in
ISO-8859-1 and UTF-8, so the charset migration never has to convert one, and
media/video held no name that wasn't ASCII when it was measured.
"""  # noqa: RUF002 - quotes job 20's path, en dash and all

import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent

# Samba's `unix charset = ISO-8859-1` writes every code point below 0x100;
# the hook keeps to this, which every charset writes alike.
BEYOND_ASCII = 0x80

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
        self.assertTrue(all(ord(c) < BEYOND_ASCII for c in got))

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

    def test_an_accented_letter_keeps_its_base(self) -> None:
        self.assertEqual(fix_job_title(Job("Mànran", "2013")), "Manran (2013)")
        self.assertEqual(fix_job_title(Job("Amélie", "2001")), "Amelie (2001)")

    def test_a_letter_with_no_base_is_spelled_out_not_lost(self) -> None:
        self.assertEqual(fix_job_title(Job("Æon Flux", "2005")), "AEon Flux (2005)")
        self.assertEqual(fix_job_title(Job("Straße", "1990")), "Strasse (1990)")

    def test_characters_with_no_ascii_form_are_dropped_not_left(self) -> None:
        got = fix_job_title(Job("Tokyo 中文", "2020"))
        self.assertTrue(
            all(ord(c) < BEYOND_ASCII for c in got),
            f"{got!r} still has un-writable chars",
        )

    def test_the_original_function_still_does_its_own_job(self) -> None:
        # The wrapper must not replace ARM's logic, only clean its output.
        self.assertEqual(fix_job_title(Job("Le Mans", "1971")), "Le Mans (1971)")
        self.assertEqual(fix_job_title(Job("Some Disc")), "Some Disc")


if __name__ == "__main__":
    unittest.main()
