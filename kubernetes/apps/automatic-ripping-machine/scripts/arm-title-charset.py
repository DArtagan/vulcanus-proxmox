# --- _arm_source_patch title-charset-code, appended by arm-source-patches.py --
"""Job titles reduced to ASCII, which the fileserver writes in any charset.

The fileserver's Samba runs `unix charset = ISO-8859-1`, so a path holding any
character above U+00FF cannot be written and fails with EIO. Map the
punctuation metadata providers actually emit, spell out the letters with no
ASCII base, decompose the rest, then drop whatever still has no ASCII form.
ASCII rather than Latin-1, to match reelbarrow, which files these titles into
the library: an ASCII name is the same bytes in ISO-8859-1 and UTF-8, so the
filename-charset migration (todos/smb-charset-utf8.md) never has to convert
one, and media/video held none that wasn't ASCII when it was measured.

Wrapping rather than editing fix_job_title's body, because every call site
resolves the name at call time -- `utils.fix_job_title(...)` in arm_ripper.py
and the bare name inside `move_files` -- so rebinding the module global
reaches both without depending on the function's internals.
"""

import unicodedata as _arm_ud

_ARM_ASCII_END = 0x80

# Kept the same as reelbarrow's (reelbarrow.names), so both fold a title alike.
_arm_charset_map = {
    0x2010: "-",
    0x2011: "-",
    0x2012: "-",
    0x2013: "-",
    0x2014: "-",
    0x2015: "-",
    0x2018: "'",
    0x2019: "'",
    0x201A: "'",
    0x201B: "'",
    0x201C: '"',
    0x201D: '"',
    0x201E: '"',
    0x2026: "...",
    0x2032: "'",
    0x2033: '"',
    0x00A0: " ",
    0x00AB: '"',
    0x00BB: '"',
    0x00D7: "x",
    # Letters NFKD leaves whole, which would otherwise vanish.
    0x00C6: "AE",
    0x00E6: "ae",
    0x0152: "OE",
    0x0153: "oe",
    0x00D8: "O",
    0x00F8: "o",
    0x00DF: "ss",
    0x0141: "L",
    0x0142: "l",
    0x0110: "D",
    0x0111: "d",
    0x00D0: "D",
    0x00F0: "d",
    0x00DE: "Th",
    0x00FE: "th",
    0x0131: "i",
}


def _arm_charset_patch(text: object) -> str:
    text = str(text).translate(_arm_charset_map)
    out = []
    for ch in text:
        if ord(ch) < _ARM_ASCII_END:
            out.append(ch)
            continue
        # NFKD first, so an accented character keeps its base letter rather than
        # vanishing entirely; then drop what still has no ASCII form.
        decomposed = _arm_ud.normalize("NFKD", ch)
        out.append("".join(c for c in decomposed if ord(c) < _ARM_ASCII_END))
    return "".join(out)


_arm_charset_orig_fix_job_title = fix_job_title


def fix_job_title(job: "Job") -> str:
    """Wrap ARM's fix_job_title so its titles can be written to the share."""
    return _arm_charset_patch(_arm_charset_orig_fix_job_title(job))
