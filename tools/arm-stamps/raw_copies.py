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
# Names a stamp lost in earlier reshapes. One still holding them predates the
# form this reads.
RETIRED = {"title", "titles", "extracted", "playlists", "length"}

TABLE = (
    "CREATE TABLE IF NOT EXISTS reelbarrow_raw_copy"
    " (job_id INTEGER PRIMARY KEY, document TEXT NOT NULL)"
)

Document = dict[str, Any]
Job = dict[str, Any]


def keys(value: object):  # noqa: ANN201
    """Yield every key in a stamp, at any depth."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from keys(inner)


def form(folder: Path) -> tuple[str, Path | None]:
    """Return what the folder holds, and its image if it is one."""
    if (folder / "BDMV").is_dir():
        return "bluray-backup", None
    images = sorted(folder.glob("*.iso"))
    if len(images) == 1:
        return "dvd-image", images[0]
    return "titles", None


def complete(stamp: dict) -> dict:
    """Return the stamp's completeness as the record's value and reason."""
    if stamp["job_id"] < FIRST_LIVE_JOB:
        # Written afterwards, and only when the sizes fell short.
        return {
            "value": False if stamp.get("complete") is False else None,
            "reason": "sizes",
        }
    if stamp["complete"] is None:
        return {"value": None, "reason": "not_checked"}
    return {"value": stamp["complete"], "reason": "sizes_and_hashes"}


def transcodes(job: Job, stamp: dict) -> dict[str, str]:
    """Each title's transcode where ARM left it, under its own title_N.mkv.

    ARM names a transcode for HandBrake's number for it in the job's own
    folder, flat for a series and for a movie's main feature, a movie's others
    in extras/. A feature ARM renamed for the film, before the patch that keeps
    its name, isn't found, and so isn't recorded.
    """
    if not job.get("path") or not stamp.get("title_map"):
        return {}
    folder = Path(job["path"])
    skipped = {
        entry["handbrake_title"]
        for entry in (stamp.get("duplicates") or {}).get("skipped", [])
    }
    found = {}
    for entry in stamp["title_map"]:
        number = entry["handbrake_title"]
        if number is None or number in skipped:
            continue
        name = f"title_{number}.mkv"
        paths = [p for p in (folder / name, folder / "extras" / name) if p.is_file()]
        if len(paths) == 1:
            found[str(number)] = str(paths[0])
    return found


def document(stamp: dict, folder: Path, job: Job) -> Document:
    """Return the record the patch would have written for the stamp's job."""
    kind, image = form(folder)
    title_map = stamp.get("title_map")
    if title_map is not None and kind == "dvd-image":
        title_map = [
            {**entry, "mkv": str(folder / "titles" / entry["mkv"])}
            for entry in title_map
        ]
    result: Document = {
        "schema": 1,
        "folder": str(folder),
        "form": kind,
        "complete": complete(stamp),
        "problems": stamp.get("problems", []),
        "title_map": title_map,
    }
    if image:
        result["image"] = str(image)
    if "duplicates" in stamp:
        result["duplicates"] = stamp["duplicates"]
    found = transcodes(job, stamp)
    if found:
        result["transcodes"] = found
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
        elif RETIRED & set(keys(stamp)):
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
    jobs = [dict(row) for row in db.execute("SELECT job_id, stage, path FROM job")]
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
