#!/bin/bash
# Moves a finished CD rip from container-local staging into the beets-flask
# inbox, in a way that never shows a partial album.
#
# abcde encodes each track straight to its output directory, and
# beets-flask enqueues an album 30s after it stops changing. Those gaps fall
# *between tracks*, so pointing abcde at the inbox means it always sees a
# partial album: on 2026-08-26 it built a session against a one-track folder
# and never re-enqueued, leaving ten tracks on disk and nothing in the
# library.
#
# Staging is container-local rather than a second mount of the audio share,
# so ARM keeps write access to `import/` alone and nothing else.
#
# Invisibility during the copy uses two independent properties of
# beets-flask, because either alone is a single point of failure:
#   * its watchdog drops any event whose basename starts with "."
#   * a file counts as audio only when its *name* ends in an audio extension
# so tracks land as `.<name>.part` and are renamed only once all of them
# have arrived. A dotted *directory* is not sufficient — measured with a
# matched control on 2026-08-26, beets-flask enqueued dotted and undotted
# probes identically.
#
# ARM calls this for every notification (utils.bash_notify) with
# $1=title $2=body, because there is no completion hook: abcde's
# do_postprocess is commented out and ARM has no per-stage callback. Hence
# the message match.
set -u

BODY="${2:-}"

STAGING="/home/arm/arm-incoming"
INBOX="/root/audio"
ARMLOG="/home/arm/logs/arm.log"

log() {
    echo "$(date) [ARM] audio-handoff: $*" >> "${ARMLOG}"
}

# utils.py builds this as f"Music CD: {job.title} {PROCESS_COMPLETE}".
# Matching the failure messages too would hand off a half-ripped album.
case "${BODY}" in
    "Music CD: "*"processing complete."*) ;;
    *) exit 0 ;;
esac

[ -d "${STAGING}" ] || { log "nothing staged at ${STAGING}"; exit 0; }

shopt -s nullglob
for album in "${STAGING}"/*/; do
    album="${album%/}"
    name="$(basename "${album}")"
    dest="${INBOX}/${name}"

    # Two discs can land on the same name, and it is not a corner case:
    # every disc MusicBrainz cannot identify is called
    # "Unknown Artist Unknown Album". Disambiguate rather than refuse.
    #
    # Refusing and leaving the album in staging looks safer and is not.
    # Staging is container-local, so a retained album dies at the next pod
    # restart; and abcde writes the *next* rip into the same directory, so
    # an 8-track disc landing on a retained 11-track one leaves tracks 1-8
    # from the new disc and 9-11 from the old. That album looks complete and
    # is two discs.
    suffix=2
    while [ -e "${dest}" ]; do
        dest="${INBOX}/${name} (${suffix})"
        suffix=$(( suffix + 1 ))
    done

    mkdir -p "${dest}"
    failed=0
    for src in "${album}"/*; do
        [ -f "${src}" ] || continue
        base="$(basename "${src}")"
        if ! cp "${src}" "${dest}/.${base}.part"; then
            log "copy failed for ${base}"
            failed=1
            break
        fi
    done

    if [ "${failed}" -ne 0 ]; then
        # Leave nothing in the inbox that could be read as an album, and
        # keep the rip rather than discarding it. Move it aside under a name
        # abcde will not rip into: this is the only path that keeps staging,
        # so it is the only remaining way the next disc could merge into an
        # existing album.
        rm -f "${dest}"/.*.part
        rmdir "${dest}" 2>/dev/null
        kept="${album}.failed-$(date +%s)"
        mv "${album}" "${kept}"
        log "aborted ${name}; rip kept at ${kept}, and it will not survive a pod restart"
        continue
    fi

    # Reveal. Each rename is within one directory, so the album goes from
    # invisible to complete in a few milliseconds.
    for tmp in "${dest}"/.*.part; do
        base="$(basename "${tmp}")"
        base="${base#.}"
        base="${base%.part}"
        mv "${tmp}" "${dest}/${base}"
    done

    log "handed off ${name} to ${dest}"
    # Always, so the next rip cannot merge into what is left here.
    rm -rf "${album}"
done
