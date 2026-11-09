# --- _arm_source_patch whole-disc-code, appended by arm-source-patches.py -----
"""A disc's raw copy: the whole disc, decrypted, recorded and checked.

The raw copy of a disc is the whole disc, decrypted, in a folder recorded
against the job that made it, and a backup that is short fails its job rather
than passing for complete. Appended to makemkv.py, whose run(), progress_log(),
OutputType, TInfo, TrackID, TrackInfoProcessor and utils it uses as module
globals; the record is utils.py's, from raw-copy-code.
"""

import pathlib as _arm_pathlib
import struct as _arm_struct

# MakeMKV's TINFO attribute for the disc's own title number, ap_iaOriginalTitleId.
_ARM_DISC_TITLE = 24
# A clip's .clpi holds its source-packet count, a big-endian uint32, at byte 56.
_ARM_CLPI_HEADER = 60
_ARM_SECTOR = 2048


def _arm_record_raw_copy(job: "Job", rawpath: str) -> None:
    # Before anything is copied. `complete` stays unchecked until the backup
    # has been, so a folder whose job died mid-backup never claims to be whole.
    if job.disctype == "bluray" and job.config.RIPMETHOD in ("backup", "backup_dvd"):
        form = "bluray-backup"
    elif job.disctype == "dvd" and job.config.RIPMETHOD == "backup_dvd":
        form = "dvd-image"
    else:
        form = "titles"
    utils._arm_save_raw_copy(  # noqa: SLF001
        job,
        folder=rawpath,
        form=form,
        complete={"value": None, "reason": "not_checked"},
        problems=[],
        title_map=None,
    )


def _arm_record_check(job: "Job", rawpath: str, problems: list[str]) -> None:
    # Both checks ran: every size against the disc's own record of it, and
    # MakeMKV's hashes as it copied.
    utils._arm_save_raw_copy(  # noqa: SLF001
        job,
        complete={"value": not problems, "reason": "sizes_and_hashes"},
        problems=problems,
    )
    if problems:
        for problem in problems:
            logging.critical("Backup incomplete: %s", problem)
        message = f"The backup in {rawpath} is incomplete: {'; '.join(problems)}"
        raise utils.RipperException(message)
    logging.info("Backup in %s checked complete", rawpath)


def _arm_backup(job: "Job", dest: str) -> list[str]:
    # makemkv_backup's command, run here because makemkv_backup discards every
    # message, and with them MakeMKV's report of clips whose data failed the
    # disc's own hashes. The files are kept at full size, so no size check
    # sees them.
    cmd = ["backup", "--decrypt"]
    cmd += shlex.split(job.config.MKV_ARGS)
    cmd += [
        f"--minlength={job.config.MINLENGTH}",
        f"--progress={progress_log(job)}",
        f"disc:{job.drive.mdisc:d}",
        dest,
    ]
    logging.info("Backing up disc to %s", dest)
    return [
        message.message
        for message in run(cmd, OutputType.MSG)
        if message is not None and "failed hash check" in message.message
    ]


def _arm_bluray_problems(rawpath: str) -> list[str]:
    # Every clip's .m2ts is 192 bytes a packet.
    clipinf = os.path.join(rawpath, "BDMV", "CLIPINF")
    stream = os.path.join(rawpath, "BDMV", "STREAM")
    if not os.path.isdir(clipinf):
        return ["BDMV/CLIPINF is missing"]
    clips = sorted(name for name in os.listdir(clipinf) if name.endswith(".clpi"))
    if not clips:
        return ["BDMV/CLIPINF holds no clips"]
    problems = []
    for clip in clips:
        with _arm_pathlib.Path(clipinf, clip).open("rb") as f:
            header = f.read(_ARM_CLPI_HEADER)
        m2ts = f"{clip[: -len('.clpi')]}.m2ts"
        if len(header) < _ARM_CLPI_HEADER:
            problems.append(f"CLIPINF/{clip} is too short to read")
            continue
        expected = 192 * _arm_struct.unpack_from(">I", header, 56)[0]
        try:
            size = os.path.getsize(os.path.join(stream, m2ts))
        except FileNotFoundError:
            problems.append(f"STREAM/{m2ts} is missing")
            continue
        if size < expected:
            problems.append(f"STREAM/{m2ts} is {size} bytes of {expected}")
    return problems


def _arm_iso_problems(iso: str) -> list[str]:
    # The image's own ISO 9660 volume descriptor, in sector 16, gives the
    # volume's size. MakeMKV writes it first, so a partial image still declares
    # the whole disc.
    name = os.path.basename(iso)
    if not os.path.isfile(iso):
        return [f"{name} is missing"]
    with _arm_pathlib.Path(iso).open("rb") as f:
        f.seek(16 * _ARM_SECTOR)
        descriptor = f.read(_ARM_SECTOR)
    if len(descriptor) < _ARM_SECTOR or descriptor[:6] != b"\x01CD001":
        return [f"{name} has no ISO 9660 volume descriptor"]
    blocks = _arm_struct.unpack_from("<I", descriptor, 80)[0]
    block_size = _arm_struct.unpack_from("<H", descriptor, 128)[0]
    size = os.path.getsize(iso)
    if size < blocks * block_size:
        return [f"{name} is {size} bytes of {blocks * block_size}"]
    return []


def _arm_backup_dvd(job: "Job", rawpath: str) -> str:
    # The ISO is the drive's only read. Titles are extracted from it by MakeMKV
    # rather than HandBrake, which would meet a structure-protected disc's
    # decoy titles unguarded, and into titles/ because HandBrake's folder scan
    # counts every file beside them as a title. --noscan keeps those two runs
    # off the drive. For a DVD the destination is the image's file name: given
    # a folder, MakeMKV refuses it as already holding a backup.
    iso = os.path.join(rawpath, f"{os.path.basename(rawpath)}.iso")
    utils._arm_save_raw_copy(job, image=iso)  # noqa: SLF001
    _arm_record_check(job, rawpath, _arm_backup(job, iso) + _arm_iso_problems(iso))

    titles = os.path.join(rawpath, "titles")
    os.makedirs(titles)
    # D14's patches match on MakeMKV's track rows, which ARM otherwise reads
    # from the drive.
    tracks = TrackInfoProcessor(job, None)
    found = {}
    info = [
        "info",
        "--cache=1",
        "--noscan",
        f"iso:{iso}",
        f"--minlength={job.config.MINLENGTH}",
    ]
    for message in run(
        info, OutputType.CINFO | OutputType.SINFO | OutputType.TCOUNT | OutputType.TINFO
    ):
        # ARM offers no public way to feed it messages read elsewhere.
        tracks._process_message(message)  # noqa: SLF001
        if isinstance(message, TInfo):
            found.setdefault(message.tid, {})[message.id] = message.value
    tracks._add_track()  # noqa: SLF001

    cmd = ["mkv"]
    cmd += shlex.split(job.config.MKV_ARGS)
    cmd += [
        "--noscan",
        f"--progress={progress_log(job)}",
        f"iso:{iso}",
        "all",
        titles,
        f"--minlength={job.config.MINLENGTH}",
    ]
    logging.info("Extracting titles from %s", iso)
    collections.deque(run(cmd, OutputType.MSG), maxlen=0)
    # ARM looks for no titles saved only in MSG 5004, which MakeMKV sends when a
    # title failed. A run with no failures reports 5005.
    if not any(name.endswith(".mkv") for name in os.listdir(titles)):
        message = f"MakeMKV extracted no titles from {iso}"
        raise utils.RipperException(message)
    utils._arm_save_raw_copy(job, title_map=_arm_title_map(found, titles))  # noqa: SLF001
    return titles


def _arm_title_map(found: dict[int, dict[int, str]], titles: str) -> list[dict]:
    # Each MKV against the disc's own title number, which is what finds it again
    # in the ISO once titles/ is gone: MakeMKV's _tNN counts only the titles it
    # kept. HandBrake numbers a folder's files from 1 in byte order of their
    # names (qsort in libhb/batch.c), and that number is ARM's title_N. It is
    # null for a title MakeMKV listed but did not save. `mkv` is the file's
    # whole path, saved or not.
    saved = sorted(
        name
        for name in os.listdir(titles)
        if os.path.isfile(os.path.join(titles, name))
    )
    entries = []
    for _, attributes in sorted(found.items()):
        mkv = attributes.get(TrackID.FILENAME)
        disc_title = attributes.get(_ARM_DISC_TITLE)
        entries.append(
            {
                "disc_title": int(disc_title) if disc_title else None,
                "mkv": os.path.join(titles, mkv) if mkv else None,
                "handbrake_title": saved.index(mkv) + 1 if mkv in saved else None,
            }
        )
    return entries
