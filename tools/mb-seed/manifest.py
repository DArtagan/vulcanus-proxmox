"""Read every .m4b under a root and emit one JSON record per book.

Runs inside the cluster, where the audio share is mounted, and writes the
manifest to stdout so nothing has to be copied in:

    kubectl exec -i -n apps <pod> -- /venv/bin/python - < manifest.py > manifest.json

The Audible rips carry a tone/m4b-tool tag set that already holds everything
MusicBrainz's release editor asks for. The atoms are `com.pilabor.tone`, not
`com.apple.iTunes`, which is why mediafile's own `asin` field reads None against
them and why this reads mutagen's raw tags instead.

Chapters are not read. The release model is one track per book, matching the
Audible download as delivered, so a chapter list would go unused; add a pass
with ffprobe if that model ever changes.
"""

import json
import os
import re
import sys

import mutagen

FREEFORM = "----:com.pilabor.tone:"

# Every file in this collection is English or untagged, so a full ISO 639-3
# table would be dead weight. An unmapped language is left None for review
# rather than guessed at.
LANGUAGES = {"English": "eng"}

MONTHS = {
    m: i
    for i, m in enumerate(
        "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), start=1
    )
}

# Audible appends this to almost every album title; MusicBrainz does not want it.
UNABRIDGED = re.compile(r"\s*\((?:un)?abridged\)\s*$", re.IGNORECASE)

# A credit that names anyone but the author is one the $author inline field
# cannot resolve, and one that must be fixed in MusicBrainz rather than with a
# local override. Flagged for review rather than parsed.
CREDIT_NOISE = re.compile(
    r"(translat|illustrat|read by|narrated by|performed by)", re.I
)


def text(tags, key):
    value = tags.get(key)
    return str(value[0]).strip() if value else None


def freeform(tags, key):
    value = tags.get(FREEFORM + key)
    if not value:
        return None
    raw = bytes(value[0])
    return raw.decode("utf-8", "replace").strip() or None


def parse_date(tags):
    """Prefer `rldt` ("11-Oct-2020"), which carries a full date, over `©day`."""
    released = text(tags, "rldt")
    if released:
        parts = released.split("-")
        if len(parts) == 3 and parts[1] in MONTHS:
            return {
                "year": int(parts[2]),
                "month": MONTHS[parts[1]],
                "day": int(parts[0]),
            }
    day = text(tags, "\xa9day")
    if day:
        match = re.match(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", day)
        if match:
            year, month, date = match.groups()
            out = {"year": int(year)}
            if month:
                out["month"] = int(month)
            if date:
                out["day"] = int(date)
            return out
    return None


# mutagen's MP4Cover image format codes.
COVER_MIMES = {13: "image/jpeg", 14: "image/png"}

SIDECAR_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif")


def cover_art(tags, directory):
    """Where this book's art is, not the art itself.

    The bytes are fetched on demand instead: 463 covers at a median 553 KiB
    would be 250 MB of manifest, and the manifest crosses `kubectl exec`, which
    truncates silently.
    """
    covers = tags.get("covr")
    embedded = bool(covers)
    image_format = getattr(covers[0], "imageformat", None) if embedded else None

    try:
        sidecars = sorted(
            name
            for name in os.listdir(directory)
            if name.lower().endswith(SIDECAR_SUFFIXES)
        )
    except OSError:
        sidecars = []

    return {
        "embedded": embedded,
        "mime": COVER_MIMES.get(image_format) if embedded else None,
        "bytes": len(bytes(covers[0])) if embedded else None,
        "sidecars": sidecars,
    }


def record(path, root):
    handle = mutagen.File(path)
    if handle is None or not handle.tags:
        return {"path": os.path.relpath(path, root), "warnings": ["unreadable tags"]}

    tags = handle.tags
    album = text(tags, "\xa9alb")
    author = text(tags, "aART") or text(tags, "\xa9ART")
    narrator = text(tags, "\xa9nrt") or text(tags, "\xa9wrt")
    language = freeform(tags, "LANGUAGE")

    # Which atom the identifier came from decides whether it can be linked. The
    # `AUDIBLE_ASIN` atom names its vendor; the plain `asin` atom does not, and
    # is where an Amazon-sourced file would put an identifier from a different
    # namespace. Nothing in the file can tell the two apart afterwards, so the
    # provenance travels with the value rather than being inferred later.
    audible_asin = freeform(tags, "AUDIBLE_ASIN")
    plain_asin = text(tags, "asin")
    asin = audible_asin or plain_asin
    asin_source = "audible" if audible_asin else ("unknown" if plain_asin else None)

    warnings = []
    if not author:
        warnings.append("no author credit")
    elif CREDIT_NOISE.search(author) or "/" in author:
        warnings.append("author credit names someone other than the author")
    if not narrator:
        warnings.append("no narrator")
    if not asin:
        warnings.append("no ASIN")
    elif asin_source == "unknown":
        warnings.append("ASIN of unknown provenance")
    if language and language not in LANGUAGES:
        warnings.append(f"unmapped language {language!r}")

    art = cover_art(tags, os.path.dirname(path))
    if not art["embedded"] and not art["sidecars"]:
        warnings.append("no cover art")

    return {
        "path": os.path.relpath(path, root),
        "folder": os.path.relpath(os.path.dirname(path), root),
        "asin": asin,
        "asin_source": asin_source,
        "title": UNABRIDGED.sub("", album) if album else None,
        "raw_album": album,
        "author": author,
        "narrator": narrator,
        "series": freeform(tags, "SERIES"),
        "part": freeform(tags, "PART"),
        "publisher": freeform(tags, "PUBLISHER") or text(tags, "\xa9pub"),
        "language": LANGUAGES.get(language),
        "date": parse_date(tags),
        # MusicBrainz wants the track length in milliseconds.
        "length_ms": int(handle.info.length * 1000) if handle.info else None,
        "art": art,
        "warnings": warnings,
    }


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "/audio/import"
    books = []
    for directory, _, filenames in os.walk(root):
        for filename in sorted(filenames):
            if filename.lower().endswith(".m4b"):
                path = os.path.join(directory, filename)
                try:
                    books.append(record(path, root))
                except Exception as exc:  # noqa: BLE001 - one bad file must not stop the walk
                    books.append(
                        {
                            "path": os.path.relpath(path, root),
                            "warnings": [f"failed to read: {exc}"],
                        }
                    )
    books.sort(key=lambda b: b["path"])
    json.dump({"root": root, "books": books}, sys.stdout, indent=1, ensure_ascii=False)
    sys.stdout.write("\n")
    print(f"{len(books)} books", file=sys.stderr)


if __name__ == "__main__":
    main()
