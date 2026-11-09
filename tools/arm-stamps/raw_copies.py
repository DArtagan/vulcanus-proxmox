"""Move each raw folder's arm-job.json into ARM's database, as its job's raw copy.

Run inside ARM's container, as a dry run first, which prints every document it
would write and writes nothing:

    kubectl exec -i -n automatic-ripping-machine deploy/automatic-ripping-machine \
        -c automatic-ripping-machine -- python3 - < tools/arm-stamps/raw_copies.py
    ... python3 - --write < tools/arm-stamps/raw_copies.py

The patch records a job's raw copy in `reelbarrow_raw_copy` as the job runs;
before it did, it wrote the same facts into the folder as `arm-job.json`. This
writes a row for each stamped job that has none, from its stamp and from where
ARM left its transcodes, and never replaces a row: a job's own record is the
better one. The stamp files are read, not changed. The container's Python is
3.10, older than the repository's target.
"""

import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

RAW = Path("/root/video/raw")
DB = Path("/root/db/arm.db")

# The first job whose stamp the patch wrote as it ran. Earlier stamps were
# written afterwards, when a backup's sizes could be checked again but not
# MakeMKV's hashes.
FIRST_LIVE_JOB = 76
# MakeMKV's report of a file failing the disc's hash check, the one problem a
# stamp held that isn't a size.
HASH_FAILED = "failed hash check"
# A transcode named for HandBrake's number for it, as ARM names them all but a
# feature it renamed for the film.
TRANSCODE = re.compile(r"title_\d+\.mkv")
# The patch's reasons that name the one title kept that plays the title
# skipped. Stamps from before it recorded `played_by` say so only here.
PLAYED_BY = re.compile(
    r"^(?:is a copy of title|is played whole by title|plays title) (\d+)\b"
)
# Names a stamp lost in earlier reshapes, where it held them: `titles`,
# `extracted` and a Blu-ray's `playlists` beside `title_map`, a `length` in its
# entries, and a bare `title` for HandBrake's number in `duplicates`. One still
# holding them predates the form this reads. `duplicates.orders` has a
# `titles` of its own, a count, and is current.
RETIRED_TOP = {"titles", "extracted", "playlists"}
RETIRED_ENTRY = {"length", "title"}

TABLE = (
    "CREATE TABLE IF NOT EXISTS reelbarrow_raw_copy"
    " (job_id INTEGER PRIMARY KEY, document TEXT NOT NULL)"
)

Document = dict[str, Any]
Job = dict[str, Any]


def retired(stamp: dict) -> bool:
    """Return whether the stamp holds a name from an earlier form."""
    if RETIRED_TOP & set(stamp):
        return True
    duplicates = stamp.get("duplicates") or {}
    entries = [
        *(stamp.get("title_map") or []),
        *duplicates.get("skipped", []),
        *duplicates.get("wholes", []),
        *(part for whole in duplicates.get("wholes", []) for part in whole["parts"]),
    ]
    return any(RETIRED_ENTRY & set(entry) for entry in entries)


def form(folder: Path) -> tuple[str, Path | None]:
    """Return what the folder holds, and its image if it is one."""
    if (folder / "BDMV").is_dir():
        return "bluray-backup", None
    images = sorted(folder.glob("*.iso"))
    if len(images) == 1:
        return "dvd-image", images[0]
    return "mkv-titles", None


def check(stamp: dict) -> dict:
    """Return the stamp's completeness as the result of each check."""
    if stamp["job_id"] < FIRST_LIVE_JOB:
        # Written afterwards, `complete` only when the sizes fell short: a
        # backup's hashes can't be checked again.
        sizes = "failed" if stamp.get("complete") is False else "passed"
        return {"sizes": sizes, "hashes": "not_run"}
    if stamp["complete"] is None:
        return {"sizes": "not_run", "hashes": "not_run"}
    problems = stamp["problems"]
    hashes = [p for p in problems if HASH_FAILED in p]
    return {
        "sizes": "failed" if len(hashes) < len(problems) else "passed",
        "hashes": "failed" if hashes else "passed",
    }


def transcodes(job: Job, numbers: list[int]) -> dict[int, str]:
    """Return each title's transcode where ARM left it, by HandBrake's number.

    ARM names a transcode title_N.mkv in the job's own folder: flat for a
    series and a movie's main feature, a movie's others in extras/. Before the
    patch that keeps the feature's name, ARM renamed the feature for the film.
    Which title that was is ARM's own record, the one HandBrake track it marked
    `main_feature`, and the file is the folder's one other .mkv above extras/.
    """
    if not job.get("path"):
        return {}
    folder = Path(job["path"])
    found = {}
    for number in numbers:
        name = f"title_{number}.mkv"
        paths = [p for p in (folder / name, folder / "extras" / name) if p.is_file()]
        if len(paths) == 1:
            found[number] = str(paths[0])
    renamed = sorted(
        path for path in folder.glob("*.mkv") if not TRANSCODE.fullmatch(path.name)
    )
    features = job.get("main_features", [])
    if len(features) == 1 and len(renamed) == 1:
        (feature,) = features
        if feature in numbers and feature not in found:
            found[feature] = str(renamed[0])
    return found


def key(entry: dict) -> dict:
    """Return the disc's own number for a title."""
    if entry.get("disc_title") is not None:
        return {"disc_title": entry["disc_title"]}
    return {"playlist": entry["playlist"]}


def played_by(skipped: dict) -> int | None:
    """Return the title kept that plays a skipped one, if the stamp names it.

    A stamp from before `played_by` names it only in its reason.
    """
    if "played_by" in skipped:
        return skipped["played_by"]
    match = PLAYED_BY.match(skipped["reason"])
    return int(match[1]) if match else None


def titles(stamp: dict, folder: Path, kind: str, job: Job) -> list[dict] | None:
    """Return the stamp's titles as the patch records them.

    What plays a title not transcoded is said once: by a whole's own parts, by
    the whole it is a part of, or by `played_by`.
    """
    title_map = stamp.get("title_map")
    if title_map is None:
        return None
    duplicates = stamp.get("duplicates") or {}
    skipped = {e["handbrake_title"]: e for e in duplicates.get("skipped", [])}
    wholes = {w["handbrake_title"]: w for w in duplicates.get("wholes", [])}
    part_of = {p["handbrake_title"]: n for n, w in wholes.items() for p in w["parts"]}
    keys = {e["handbrake_title"]: key(e) for e in title_map if e["handbrake_title"]}
    found = transcodes(job, [n for n in keys if n not in skipped])
    entries = []
    for mapped in title_map:
        number = mapped["handbrake_title"]
        entry = {"handbrake_title": number, **key(mapped)}
        if number is None:
            entry |= {"transcode": None, "reason": "not_extracted"}
        elif kind == "dvd-image" and mapped.get("mkv"):
            entry["extracted"] = str(folder / "titles" / mapped["mkv"])
        if number in skipped:
            entry |= {"transcode": None, "reason": "played_by_kept_titles"}
            kept = played_by(skipped[number])
            if kept is not None and part_of.get(number) != kept:
                entry["played_by"] = keys[kept]
        elif number in found:
            entry |= {"transcode": found[number], "reason": None}
        if number in wholes:
            whole = wholes[number]
            entry |= {
                "interleaved": whole["interleaved"],
                "parts": [{**key(p), "start": p["start"]} for p in whole["parts"]],
            }
        entries.append(entry)
    return entries


def orders(stamp: dict) -> list[dict]:
    """Return how each choice among shuffles was made, by the disc's numbers."""

    def plain(entry: dict) -> dict:
        return {k: v for k, v in entry.items() if k != "handbrake_title"}

    return [
        {**plain(order), "runner_up": order["runner_up"] and plain(order["runner_up"])}
        for order in stamp["duplicates"].get("orders", [])
    ]


def document(stamp: dict, folder: Path, job: Job) -> Document:
    """Return the record the patch would have written for the stamp's job."""
    kind, image = form(folder)
    result: Document = {
        "schema": 1,
        "folder": str(folder),
        "form": kind,
        "check": check(stamp),
        "problems": stamp.get("problems", []),
    }
    if image:
        result["image"] = str(image)
    entries = titles(stamp, folder, kind, job)
    if entries is not None:
        result["titles"] = entries
    if "duplicates" in stamp:
        result["titles_compared"] = True
        result["orders"] = orders(stamp)
    return result


def plan(
    jobs: list[Job], recorded: set[int], raw: Path = RAW
) -> tuple[dict[int, Document], list[tuple[object, str]]]:
    """Plan the documents to write by job, and the stamps passed over, with why."""
    by_id = {job["job_id"]: job for job in jobs}
    found: dict[int, list[tuple[Path, dict]]] = {}
    for path in sorted(raw.glob("*/arm-job.json")):
        stamp = json.loads(path.read_text())
        found.setdefault(stamp["job_id"], []).append((path.parent, stamp))
    documents, passed = {}, []
    for job_id, stamps in sorted(found.items()):
        if len(stamps) > 1:
            folders = [str(folder) for folder, _ in stamps]
            passed.append((job_id, f"stamped in more than one folder: {folders}"))
            continue
        folder, stamp = stamps[0]
        job = by_id.get(job_id)
        if job is None:
            passed.append((job_id, "no such job in ARM's database"))
        elif job["stage"] != stamp["stage"]:
            passed.append((job_id, f"{folder}'s stage isn't the job's"))
        elif retired(stamp):
            passed.append((job_id, f"{folder}'s stamp predates its current form"))
        elif job_id in recorded:
            passed.append((job_id, "already recorded"))
        else:
            documents[job_id] = document(stamp, folder, job)
    return documents, passed


def write(db: sqlite3.Connection, documents: dict[int, Document]) -> None:
    """All or nothing, keeping any row the patch wrote in the meantime."""
    with db:
        db.execute(TABLE)
        db.executemany(
            "INSERT INTO reelbarrow_raw_copy (job_id, document) VALUES (?, ?)"
            " ON CONFLICT (job_id) DO NOTHING",
            [(job_id, json.dumps(doc)) for job_id, doc in documents.items()],
        )


def main(argv: list[str]) -> None:
    """Show what would be written, or with --write write it."""
    apply = "--write" in argv
    mode = "rw" if apply else "ro"
    db = sqlite3.connect(f"file:{DB}?mode={mode}", uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    features: dict[int, list[int]] = {}
    for job_id, number in db.execute(
        "SELECT job_id, track_number FROM track"
        " WHERE source = 'HandBrake' AND main_feature"
    ):
        features.setdefault(job_id, []).append(int(number))
    jobs = [
        dict(row) | {"main_features": features.get(row["job_id"], [])}
        for row in db.execute("SELECT job_id, stage, path FROM job")
    ]
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master")}
    recorded = (
        {row[0] for row in db.execute("SELECT job_id FROM reelbarrow_raw_copy")}
        if "reelbarrow_raw_copy" in tables
        else set()
    )
    documents, passed = plan(jobs, recorded)
    for job_id, doc in documents.items():
        print(json.dumps({"job_id": job_id, "document": doc}))
    for who, why in passed:
        print(f"-- passed over {who}: {why}", file=sys.stderr)
    if apply:
        write(db, documents)
        print(f"wrote {len(documents)} raw copies", file=sys.stderr)
    else:
        count = len(documents)
        print(
            f"dry run: {count} raw copies would be written; --write to write them",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main(sys.argv[1:])
