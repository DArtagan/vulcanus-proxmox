"""Bring every raw folder's arm-job.json to the stamp's current form.

Run inside ARM's container, as a dry run first:

    kubectl exec -i -n automatic-ripping-machine deploy/automatic-ripping-machine \
        -c automatic-ripping-machine -- python3 - < tools/arm-stamps/stamps.py
    ... python3 - --write < tools/arm-stamps/stamps.py

It does two things. It rewrites stamps of an earlier shape into the current
one, filling a Blu-ray's title map from the job's own log where the stamp has
none. And it writes a stamp into a raw folder that has none, where the folder
holds the whole disc and is provably the job's. Nothing else on the share is
touched, and a stamp already in the current form is left as it is.

The container's Python is 3.10, older than the repository's target.
"""

import datetime
import difflib
import json
import re
import sqlite3
import struct
import sys
from pathlib import Path
from typing import Any

RAW = Path("/root/video/raw")
LOGS = Path("/root/logs")
DB = Path("/root/db/arm.db")

Stamp = dict[str, Any]
Job = dict[str, Any]

# A log is the job's only if it opens when the job started. ARM names a log
# after the disc's label, so a later job can take over an earlier one's name:
# job 2's database row names the log job 61 wrote.
LOG_OWNED_WITHIN = 600

ORDER = (
    "schema", "job_id", "label", "disctype", "crc_id", "stage", "start_time",
    "complete", "problems", "title_map", "duplicates",
)  # fmt: skip

CLPI_HEADER = 60
SOURCE_PACKET = 192
SECTOR = 2048


def ordered(stamp: Stamp) -> Stamp:
    """Order the stamp's fields as the patch writes them."""
    known = {key: stamp[key] for key in ORDER if key in stamp}
    return known | {key: value for key, value in stamp.items() if key not in known}


def conform(
    stamp: Stamp,
    log_map: dict[int, str] | None = None,
    playlist_names: list[str] | None = None,
) -> Stamp:
    """Return the stamp in the current form.

    Only the shape changes, and a Blu-ray's missing title map is filled from
    its log.
    """
    stamp = dict(stamp)
    # `titles` was `extracted` before 2026-10-26, and `extracted` `title_map`.
    old = stamp.pop("extracted", stamp.pop("titles", None))
    if "title_map" not in stamp:
        stamp["title_map"] = old
    if stamp["title_map"] is not None and stamp["disctype"] == "dvd":
        stamp["title_map"] = [
            {
                "disc_title": t.get("disc_title"),
                "mkv": t.get("mkv"),
                "handbrake_title": t.get("handbrake_title"),
            }
            for t in stamp["title_map"]
        ]
    playlists = stamp.pop("playlists", None)
    if stamp["disctype"] == "bluray":
        if playlists is not None:
            stamp["title_map"] = [
                {"playlist": t["playlist"], "handbrake_title": t["handbrake_title"]}
                for t in playlists
            ]
        elif stamp["title_map"] is None and log_map:
            stamp["title_map"] = bluray_title_map(
                log_map, stamp.get("duplicates"), playlist_names
            )
    if stamp.get("duplicates") is not None:
        stamp["duplicates"] = _named_duplicates(stamp["duplicates"])
    return ordered(stamp)


def _named_duplicates(duplicates: dict) -> dict:
    # `title` was `handbrake_title` before 2026-10-26.
    def entry(item: dict) -> dict:
        item = dict(item)
        if "title" in item:
            item = {"handbrake_title": item.pop("title")} | item
        return item

    return duplicates | {
        "skipped": [entry(item) for item in duplicates.get("skipped", [])],
        "wholes": [
            entry(whole) | {"parts": [entry(part) for part in whole.get("parts", [])]}
            for whole in duplicates.get("wholes", [])
        ],
    }


def bluray_title_map(
    log_map: dict[int, str],
    duplicates: dict | None = None,
    playlist_names: list[str] | None = None,
) -> list[dict]:
    """Each title HandBrake was given, against its playlist.

    The log names only the titles transcoded; a skipped one is in `duplicates`.
    Each playlist takes its name as it is on the disc, where the log has
    HandBrake's upper case.
    """
    names = {name.lower(): name for name in playlist_names or ()}
    pairs = {
        number: names.get(playlist.lower(), playlist.lower())
        for number, playlist in log_map.items()
    }
    for item in (duplicates or {}).get("skipped", []):
        if "playlist" in item:
            pairs.setdefault(item["handbrake_title"], item["playlist"])
    return [
        {"playlist": pairs[number], "handbrake_title": number}
        for number in sorted(pairs)
    ]


def log_owned(text: str, start_time: str | None) -> bool:
    """Whether the log opens when the job started, and so is the job's."""
    match = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", text)
    if not match or not start_time:
        return False
    opened = datetime.datetime.fromisoformat(match[1])
    started = datetime.datetime.fromisoformat(str(start_time)[:19])
    return abs((opened - started).total_seconds()) <= LOG_OWNED_WITHIN


def log_raw_path(text: str) -> Path | None:
    """Return the raw folder the job wrote to.

    The last logged, because ARM logs the name it tried before the `_<stage>`
    one it falls back to when the first is taken.
    """
    paths = re.findall(r"Processing files to: (/root/video/raw/[^\n]*)", text)
    return Path(paths[-1].rstrip()) if paths else None


def log_title_map(text: str) -> dict[int, str]:
    """HandBrake's number against the playlist it read, for each title transcoded."""
    pairs: dict[int, str] = {}
    current = None
    for line in text.splitlines():
        transcoding = re.search(r"Transcoding title (\d+) to ", line)
        if transcoding:
            current = int(transcoding[1])
            continue
        playlist = re.match(r"\s+\+ playlist: (\S+)", line)
        if playlist and current is not None and current not in pairs:
            pairs[current] = playlist[1]
    return pairs


def bluray_problems(rawpath: Path) -> list[str]:
    """As the patch's _arm_bluray_problems: each clip against its .clpi.

    A .clpi gives its clip's source-packet count, and the .m2ts is 192 bytes a
    packet.
    """
    clipinf = rawpath / "BDMV" / "CLIPINF"
    stream = rawpath / "BDMV" / "STREAM"
    if not clipinf.is_dir():
        return ["BDMV/CLIPINF is missing"]
    clips = sorted(
        path.name for path in clipinf.iterdir() if path.name.endswith(".clpi")
    )
    if not clips:
        return ["BDMV/CLIPINF holds no clips"]
    problems = []
    for clip in clips:
        with (clipinf / clip).open("rb") as f:
            header = f.read(CLPI_HEADER)
        m2ts = f"{clip[: -len('.clpi')]}.m2ts"
        if len(header) < CLPI_HEADER:
            problems.append(f"CLIPINF/{clip} is too short to read")
            continue
        expected = SOURCE_PACKET * struct.unpack_from(">I", header, 56)[0]
        try:
            size = (stream / m2ts).stat().st_size
        except FileNotFoundError:
            problems.append(f"STREAM/{m2ts} is missing")
            continue
        if size < expected:
            problems.append(f"STREAM/{m2ts} is {size} bytes of {expected}")
    return problems


def iso_problems(iso: Path) -> list[str]:
    """As the patch's _arm_iso_problems: the image against its own volume size."""
    if not iso.is_file():
        return [f"{iso.name} is missing"]
    with iso.open("rb") as f:
        f.seek(16 * SECTOR)
        descriptor = f.read(SECTOR)
    if len(descriptor) < SECTOR or descriptor[:6] != b"\x01CD001":
        return [f"{iso.name} has no ISO 9660 volume descriptor"]
    blocks = struct.unpack_from("<I", descriptor, 80)[0]
    block_size = struct.unpack_from("<H", descriptor, 128)[0]
    size = iso.stat().st_size
    if size < blocks * block_size:
        return [f"{iso.name} is {size} bytes of {blocks * block_size}"]
    return []


def whole_disc(folder: Path) -> str | None:
    """Return the raw copy's form if it holds the whole disc, else None."""
    if (folder / "BDMV" / "index.bdmv").is_file():
        return "bdmv"
    if len(list(folder.glob("*.iso"))) == 1:
        return "iso"
    return None


def utc(start_time: str | None) -> str | None:
    """As the patch writes it: ARM's naive local time, given in UTC."""
    if not start_time:
        return None
    local = datetime.datetime.fromisoformat(str(start_time))
    return local.astimezone(datetime.timezone.utc).isoformat()  # noqa: UP017 - the container's Python is 3.10


def backfill(job: Job, folder: Path, log_map: dict[int, str] | None = None) -> Stamp:
    """Return a stamp for a raw folder written before stamps were.

    `complete` is written only when the check finds the copy short: a backup's
    other test, MakeMKV's hash check, ran in a job that kept no record of it,
    so a copy that passes is shown not incomplete rather than complete. The job
    ran before titles were skipped, so there is no `duplicates`.
    """
    form = whole_disc(folder)
    problems = (
        bluray_problems(folder)
        if form == "bdmv"
        else iso_problems(next(folder.glob("*.iso")))
    )
    stamp: Stamp = {
        "schema": 1,
        "job_id": job["job_id"],
        "label": job["label"],
        "disctype": job["disctype"],
        "crc_id": job["crc_id"],
        "stage": job["stage"],
        "start_time": utc(job["start_time"]),
    }
    if problems:
        stamp |= {"complete": False, "problems": problems}
    if form == "bdmv" and log_map:
        names = [path.name for path in (folder / "BDMV" / "PLAYLIST").iterdir()]
        stamp["title_map"] = bluray_title_map(log_map, None, names)
    else:
        stamp["title_map"] = None
    return ordered(stamp)


def _own_log(job: Job, logs: Path) -> str | None:
    if not job["logfile"]:
        return None
    path = logs / job["logfile"]
    if not path.is_file():
        return None
    text = path.read_text(errors="replace")
    return text if log_owned(text, job["start_time"]) else None


def _playlist_names(folder: Path) -> list[str]:
    playlists = folder / "BDMV" / "PLAYLIST"
    return [path.name for path in playlists.iterdir()] if playlists.is_dir() else []


def plan(jobs: list[Job], raw: Path = RAW, logs: Path = LOGS) -> tuple[list, list]:
    """Plan what to write, as (path, before, after, why), and what to pass over."""
    actions: list = []
    passed: list = []
    folders = sorted(path for path in raw.iterdir() if path.is_dir())
    by_id = {job["job_id"]: job for job in jobs}

    stamped = set()
    for folder in folders:
        path = folder / "arm-job.json"
        if not path.is_file():
            continue
        before = json.loads(path.read_text())
        stamped.add(before["job_id"])
        job = by_id.get(before["job_id"])
        text = _own_log(job, logs) if job else None
        after = conform(
            before, log_title_map(text) if text else None, _playlist_names(folder)
        )
        if after != before:
            actions.append((path, before, after, "rewritten to the current form"))

    claimed: dict[Path, list] = {}
    for job in jobs:
        if job["job_id"] in stamped or job["disctype"] not in {"bluray", "dvd"}:
            continue
        folder = _proven_folder(job, folders, raw, logs, passed)
        if folder and whole_disc(folder):
            claimed.setdefault(folder, []).append(job)
    for folder, claims in claimed.items():
        if len(claims) > 1:
            passed.append(
                (
                    [job["job_id"] for job in claims],
                    f"{folder} is claimed by more than one job",
                )
            )
            continue
        if (folder / "arm-job.json").is_file():
            continue
        job = claims[0]
        text = _own_log(job, logs)
        stamp = backfill(job, folder, log_title_map(text) if text else None)
        actions.append((folder / "arm-job.json", None, stamp, "backfilled"))
    return actions, passed


def _proven_folder(
    job: Job, folders: list[Path], raw: Path, logs: Path, passed: list
) -> Path | None:
    # By the job's own log, or by a folder named for its stage; both, if both
    # name one, have to agree.
    text = _own_log(job, logs)
    by_log = log_raw_path(text) if text else None
    if by_log and by_log.is_relative_to(RAW):
        # The log has the container's path; the raw root here may be elsewhere.
        by_log = raw / by_log.relative_to(RAW)
    by_stage = {
        folder
        for folder in folders
        if job["stage"] and folder.name.endswith(f"_{job['stage']}")
    }
    candidates = by_stage | ({by_log} if by_log and by_log.is_dir() else set())
    if len(candidates) > 1:
        passed.append(
            (
                job["job_id"],
                f"its log and its stage name different folders: {sorted(candidates)}",
            )
        )
        return None
    return candidates.pop() if candidates else None


def write(path: Path, stamp: Stamp) -> None:
    """Write a stamp as the patch does, whole or not at all."""
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(stamp, indent=2) + "\n")
    temporary.replace(path)


def _render(stamp: Stamp | None) -> list[str]:
    return (
        []
        if stamp is None
        else (json.dumps(stamp, indent=2) + "\n").splitlines(keepends=True)
    )


def main(argv: list[str]) -> None:
    """Show what would be written, or with --write write it."""
    apply = "--write" in argv
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    query = (
        "SELECT job_id, label, disctype, crc_id, stage, start_time, logfile"
        " FROM job ORDER BY job_id"
    )
    jobs = [dict(row) for row in db.execute(query)]

    # The time format has to be the patch's: check it against the stamps ARM wrote.
    by_id = {job["job_id"]: job for job in jobs}
    for path in RAW.glob("*/arm-job.json"):
        stamp = json.loads(path.read_text())
        job = by_id.get(stamp["job_id"])
        if job and utc(job["start_time"]) != stamp["start_time"]:
            sys.exit(
                f"start_time {utc(job['start_time'])} would differ"
                f" from {path}'s {stamp['start_time']}"
            )

    actions, passed = plan(jobs)
    for path, before, after, why in actions:
        print(f"== {path}: {why}")
        sys.stdout.writelines(
            difflib.unified_diff(
                _render(before), _render(after), "before", "after", n=2
            )
        )
    for who, why in passed:
        print(f"-- passed over {who}: {why}")
    if apply:
        for path, _, after, _ in actions:
            write(path, after)
        print(f"wrote {len(actions)} stamps")
    else:
        print(f"dry run: {len(actions)} stamps would be written; --write to write them")


if __name__ == "__main__":
    main(sys.argv[1:])
