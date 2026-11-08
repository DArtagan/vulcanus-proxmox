#!/bin/bash
# Makes ARM's output paths writable on this fileserver.
#
# Samba runs `unix charset = ISO-8859-1`, so creating any name containing a
# character above U+00FF fails with EIO on every share. Job 20 died on
# `.../tv/The-Sylvester-and-Tweety-Mysteries (1995-2002)` -- with an en dash --
# because OMDb returns a series' year range that way. The real fix is a charset
# migration; see todos/smb-charset-utf8.md, which explains why it is a migration
# and not a one-line config change. This is the workaround that keeps ripping.
#
# ARM's own clean_for_filename does not cover it. It is applied to titles only
# (identify.py:141, :274) while the en dash arrives through job.year, which
# fix_job_title interpolates raw -- and it strips rather than replaces, turning
# a year range into a single meaningless number.
#
# The patch body lives in arm-title-charset.py because a heredoc of top-level
# Python cannot be indented inside a YAML block scalar without breaking one or
# the other.
set -u

UTILS="/opt/arm/arm/ripper/utils.py"
PATCH="/usr/local/bin/arm-title-charset.py"
ARMLOG="/home/arm/logs/arm.log"
MARKER="_arm_charset_patch"

log() {
    echo "$(date) [ARM] title-charset: $*" >> "${ARMLOG}"
}

if grep -q "${MARKER}" "${UTILS}" 2>/dev/null; then
    exit 0
fi

# ARM auto-updates now, so a release that renames or moves this function must
# not fail quietly: the symptom would be a TV rip dying on EIO again with
# nothing pointing back here.
if ! grep -q "^def fix_job_title" "${UTILS}" 2>/dev/null; then
    log "cannot patch ${UTILS}: no 'def fix_job_title' found."
    log "  TV discs with a non-Latin-1 year will fail with EIO until this is fixed."
    exit 1
fi

cat "${PATCH}" >> "${UTILS}"
log "patched fix_job_title in ${UTILS} for the ISO-8859-1 fileserver"
