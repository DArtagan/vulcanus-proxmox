"""Edits to ARM's source, applied at container start.

Each edit replaces text that must occur exactly once. ARM auto-updates, so
an edit whose text has moved is skipped and logged rather than guessed at;
the symptom would otherwise be the old behaviour returning with nothing
pointing back here. Edits carry a marker, so a container restart, which keeps
the already-patched files, re-applies nothing.
"""

import datetime
import sys
from pathlib import Path

RIPPER = "/opt/arm/arm/ripper"
ARMLOG = "/home/arm/logs/arm.log"

# Whole functions, appended so that they resolve the module's own globals as
# ARM's code does. Applied before EDITS, which call them. The last field is text
# the module must hold for the code to load at all, or None: the title-charset
# wrapper binds fix_job_title as it is appended, so appended to a utils.py
# without it, it would stop the module importing and every job with it.
APPENDS = [
    (
        "whole-disc-code",
        f"{RIPPER}/makemkv.py",
        "/usr/local/bin/arm-whole-disc.py",
        None,
    ),
    (
        "duplicate-titles-code",
        f"{RIPPER}/handbrake.py",
        "/usr/local/bin/arm-duplicate-titles.py",
        None,
    ),
    (
        "title-charset-code",
        f"{RIPPER}/utils.py",
        "/usr/local/bin/arm-title-charset.py",
        "\ndef fix_job_title(",
    ),
]

EDITS = [
    (
        # The disc stays in the drive until the job is complete, transcode
        # included; main.py's finally: block ejects it. Without this edit the
        # tray opens as soon as the rip ends, which reads as "done" while the
        # transcode still holds the drive. See todos/disc-ripping.md, D8.
        "eject-after-processing",
        f"{RIPPER}/makemkv.py",
        (
            "    job.eject()\n"
            '    logging.info(f"Exiting MakeMKV processing with return value '
            'of: {rawpath}")\n'
        ),
        (
            "    # _arm_source_patch eject-after-processing: main.py ejects when "
            "the job ends\n"
            '    logging.info(f"Exiting MakeMKV processing with return value '
            'of: {rawpath}")\n'
        ),
    ),
    (
        # On the DVD path every title is registered twice: by MakeMKV numbered
        # from 0, and by HandBrake's scan numbered from 1. Only HandBrake's
        # numbers mean anything to `-t`. Encoding both sets runs `-t 0` (scan
        # all, writes nothing) and then encodes every title twice into the same
        # files. See todos/disc-ripping.md, D14.
        "d14-encode",
        f"{RIPPER}/handbrake.py",
        (
            "    for track in job.tracks:\n"
            "        # Don't raise error if we past max titles"
        ),
        (
            '    for track in job.tracks.filter_by(source="HandBrake"):  '
            "# _arm_source_patch d14-encode\n"
            "        # Don't raise error if we past max titles"
        ),
    ),
    (
        # The series branch moves each track by its filename. With d14-encode in
        # place MakeMKV's tracks keep their raw names, which find_matching_file
        # fuzzy-matches onto the transcodes (`C3_t01` scores as `title_1`) and
        # moves under the wrong name. Movies are left alone: their first
        # MakeMKV track routes them to skip_transcode_movie, which picks the
        # main feature by file size from the directory listing.
        "d14-move-series",
        f"{RIPPER}/arm_ripper.py",
        '    if job.video_type == "series":\n        for track in tracks:\n',
        (
            '    if job.video_type == "series":\n'
            "        # _arm_source_patch d14-move-series: only HandBrake's tracks "
            "name files it wrote\n"
            '        if tracks.filter_by(source="HandBrake").count():\n'
            '            tracks = tracks.filter_by(source="HandBrake")\n'
            "        for track in tracks:\n"
        ),
    ),
    (
        # ARM backs up the whole disc only for a Blu-ray, and a DVD's raw copy
        # is otherwise its long titles alone. This stamps the raw folder with
        # its job, backs a DVD up whole, and fails the job on a backup that is
        # short. See todos/disc-ripping.md, phase 2c, D16 and D17.
        "whole-disc-route",
        f"{RIPPER}/makemkv.py",
        (
            '    logging.info(f"Processing files to: {rawpath}")\n'
            "    # Rip BluRay\n"
            '    if (job.config.RIPMETHOD in ("backup", "backup_dvd")) and '
            'job.disctype == "bluray":\n'
            "        makemkv_backup(job, rawpath)\n"
            "    # Rip BluRay or DVD\n"
        ),
        (
            '    logging.info(f"Processing files to: {rawpath}")\n'
            "    # _arm_source_patch whole-disc-route: stamp the folder, back up a "
            "DVD whole, check the backup\n"
            "    _arm_write_stamp(job, rawpath)\n"
            "    # Rip BluRay\n"
            '    if (job.config.RIPMETHOD in ("backup", "backup_dvd")) and '
            'job.disctype == "bluray":\n'
            "        _arm_record_check(job, rawpath, _arm_backup(job, rawpath) + "
            "_arm_bluray_problems(rawpath))\n"
            '    elif job.config.RIPMETHOD == "backup_dvd" and job.disctype == "dvd":\n'
            "        rawpath = _arm_backup_dvd(job, rawpath)\n"
            "    # Rip BluRay or DVD\n"
        ),
    ),
    (
        # HandBrake otherwise encodes every title over MINLENGTH, a disc's
        # play-all included, repeating hours of encoding that the other titles
        # already hold. In handbrake_all, the only caller followed by this
        # debug line. Neither this edit nor the next spans d14-encode's line,
        # whose marker would read as applied wherever the text appeared.
        "duplicate-titles-scan",
        f"{RIPPER}/handbrake.py",
        (
            "    get_track_info(srcpath, job)\n"
            "\n"
            '    logging.debug(f"Total number of tracks is {job.no_of_titles}")\n'
        ),
        (
            "    get_track_info(srcpath, job)\n"
            "    # _arm_source_patch duplicate-titles-scan: titles the kept ones "
            "already play\n"
            "    duplicates = _arm_duplicate_titles(srcpath, job)\n"
            "\n"
            '    logging.debug(f"Total number of tracks is {job.no_of_titles}")\n'
        ),
    ),
    (
        # A movie's main feature keeps its title_N name instead of taking the
        # film's. The rename erased the one title number that ties the film's
        # transcode to the disc, and it named whatever ARM guessed: a bonus
        # disc's longest featurette, a film's in-film extras mode. Where it
        # lands, above extras/, still records the guess. Both routes that
        # pick a main feature, HandBrake's flag and skip_transcode_movie's
        # largest file, come through here.
        "keep-main-feature-name",
        f"{RIPPER}/utils.py",
        (
            "    if is_main_feature:\n"
            "        movie_file = os.path.join(movie_path, video_title + "
            '"." + job.config.DEST_EXT)\n'
        ),
        (
            "    if is_main_feature:\n"
            "        # _arm_source_patch keep-main-feature-name: the film is "
            "identified downstream\n"
            "        movie_file = os.path.join(movie_path, filename)\n"
        ),
    ),
    (
        "duplicate-titles-skip",
        f"{RIPPER}/handbrake.py",
        (
            "            continue\n"
            '        if track.length < int(cfg.arm_config["MINLENGTH"]):\n'
            "            # too short\n"
        ),
        (
            "            continue\n"
            "        # _arm_source_patch duplicate-titles-skip\n"
            "        if int(track.track_number) in duplicates:\n"
            '            logging.info(f"Track #{track.track_number} of '
            '{job.no_of_titles}. "\n'
            '                         f"It {duplicates[int(track.track_number)]}. '
            'Skipping...")\n'
            '        elif track.length < int(cfg.arm_config["MINLENGTH"]):\n'
            "            # too short\n"
        ),
    ),
]


def log(message: str) -> None:
    """Append a line to arm.log, stamped as ARM stamps its own."""
    now = datetime.datetime.now(datetime.timezone.utc)
    with Path(ARMLOG).open("a") as f:
        f.write(f"{now:%a %b %d %H:%M:%S UTC %Y} [ARM] source-patches: {message}\n")


failed = False
for name, path, code, requires in APPENDS:
    source = Path(path).read_text()
    if f"_arm_source_patch {name}" in source:
        continue
    if requires is not None and requires not in source:
        log(f"cannot apply {name}: {requires.strip()!r} not found in {path}; skipped")
        failed = True
        continue
    with Path(path).open("a") as f:
        f.write(f"\n\n{Path(code).read_text()}")
    log(f"applied {name} to {path}")

for name, path, old, new in EDITS:
    source = Path(path).read_text()
    if f"_arm_source_patch {name}" in source:
        continue
    count = source.count(old)
    if count != 1:
        log(
            f"cannot apply {name}: expected text found {count} times in {path}; skipped"
        )
        failed = True
        continue
    Path(path).write_text(source.replace(old, new))
    log(f"applied {name} to {path}")

sys.exit(1 if failed else 0)
