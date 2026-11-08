# --- _arm_source_patch title-charset-code, appended by arm-source-patches.py --
"""Job titles reduced to what the fileserver can write.

The fileserver's Samba runs `unix charset = ISO-8859-1`, so a path holding any
character above U+00FF cannot be written and fails with EIO. Map the
punctuation metadata providers actually emit, then drop whatever has no
Latin-1 form. Latin-1 rather than ASCII on purpose: the share accepts it, and
`Mànran` is already in the library under a Latin-1 name.

Wrapping rather than editing fix_job_title's body, because every call site
resolves the name at call time -- `utils.fix_job_title(...)` in arm_ripper.py
and the bare name inside `move_files` -- so rebinding the module global
reaches both without depending on the function's internals.
"""

import unicodedata as _arm_ud

_ARM_LATIN1_END = 0x100

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
}


def _arm_charset_patch(text: object) -> str:
    text = str(text).translate(_arm_charset_map)
    out = []
    for ch in text:
        if ord(ch) < _ARM_LATIN1_END:
            out.append(ch)
            continue
        # NFKD first, so an accented character keeps its base letter rather than
        # vanishing entirely; then drop what still has no Latin-1 form.
        decomposed = _arm_ud.normalize("NFKD", ch)
        out.append("".join(c for c in decomposed if ord(c) < _ARM_LATIN1_END))
    return "".join(out)


_arm_charset_orig_fix_job_title = fix_job_title


def fix_job_title(job: "Job") -> str:
    """Wrap ARM's fix_job_title so its titles can be written to the share."""
    return _arm_charset_patch(_arm_charset_orig_fix_job_title(job))
