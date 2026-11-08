# --- _arm_source_patch whole-disc-code, appended by arm-source-patches.py -----
# The raw copy of a disc is the whole disc, decrypted, in a folder stamped with
# the job that made it, and a backup that is short fails its job rather than
# passing for complete. Appended to makemkv.py, whose run(), progress_log(),
# OutputType, TInfo, TrackID, TrackInfoProcessor and utils it uses as module
# globals.
import datetime as _arm_datetime  # noqa: E402
import json as _arm_json  # noqa: E402
import struct as _arm_struct  # noqa: E402


# MakeMKV's TINFO attribute for the disc's own title number, ap_iaOriginalTitleId.
_ARM_DISC_TITLE = 24


def _arm_write_stamp(job, rawpath, problems=None, title_map=None):
    # ARM's database records a job's completed path and never its raw one, and
    # a title corrected later renames only the completed folder. The stamp
    # travels with the raw folder through any rename. `complete` stays null
    # until the backup has been checked, so a folder whose job died mid-backup
    # never claims to be whole.
    stamp = {
        "schema": 1,
        "job_id": job.job_id,
        "label": job.label,
        "disctype": job.disctype,
        "crc_id": job.crc_id,
        "stage": job.stage,
        "start_time": job.start_time.astimezone(_arm_datetime.timezone.utc).isoformat()
        if job.start_time
        else None,
        "complete": None if problems is None else not problems,
        "problems": problems or [],
        "title_map": title_map,
    }
    path = os.path.join(rawpath, "arm-job.json")
    with open(f"{path}.tmp", "w") as f:
        _arm_json.dump(stamp, f, indent=2)
        f.write("\n")
    os.replace(f"{path}.tmp", path)


def _arm_record_check(job, rawpath, problems):
    _arm_write_stamp(job, rawpath, problems)
    if problems:
        for problem in problems:
            logging.critical(f"Backup incomplete: {problem}")
        raise utils.RipperException(
            f"The backup in {rawpath} is incomplete: {'; '.join(problems)}"
        )
    logging.info(f"Backup in {rawpath} checked complete")


def _arm_backup(job, dest):
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
    logging.info(f"Backing up disc to {dest}")
    return [
        message.message
        for message in run(cmd, OutputType.MSG)
        if message is not None and "failed hash check" in message.message
    ]


def _arm_bluray_problems(rawpath):
    # Every clip's .clpi gives its source-packet count, a big-endian uint32 at
    # byte 56, and its .m2ts is 192 bytes a packet.
    clipinf = os.path.join(rawpath, "BDMV", "CLIPINF")
    stream = os.path.join(rawpath, "BDMV", "STREAM")
    if not os.path.isdir(clipinf):
        return ["BDMV/CLIPINF is missing"]
    clips = sorted(name for name in os.listdir(clipinf) if name.endswith(".clpi"))
    if not clips:
        return ["BDMV/CLIPINF holds no clips"]
    problems = []
    for clip in clips:
        with open(os.path.join(clipinf, clip), "rb") as f:
            header = f.read(60)
        m2ts = f"{clip[: -len('.clpi')]}.m2ts"
        if len(header) < 60:
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


def _arm_iso_problems(iso):
    # The image's own ISO 9660 volume descriptor, in sector 16, gives the
    # volume's size. MakeMKV writes it first, so a partial image still declares
    # the whole disc.
    name = os.path.basename(iso)
    if not os.path.isfile(iso):
        return [f"{name} is missing"]
    with open(iso, "rb") as f:
        f.seek(16 * 2048)
        descriptor = f.read(2048)
    if len(descriptor) < 2048 or descriptor[:6] != b"\x01CD001":
        return [f"{name} has no ISO 9660 volume descriptor"]
    blocks = _arm_struct.unpack_from("<I", descriptor, 80)[0]
    block_size = _arm_struct.unpack_from("<H", descriptor, 128)[0]
    size = os.path.getsize(iso)
    if size < blocks * block_size:
        return [f"{name} is {size} bytes of {blocks * block_size}"]
    return []


def _arm_backup_dvd(job, rawpath):
    # The ISO is the drive's only read. Titles are extracted from it by MakeMKV
    # rather than HandBrake, which would meet a structure-protected disc's
    # decoy titles unguarded, and into titles/ because HandBrake's folder scan
    # counts every file beside them as a title. --noscan keeps those two runs
    # off the drive. For a DVD the destination is the image's file name: given
    # a folder, MakeMKV refuses it as already holding a backup.
    iso = os.path.join(rawpath, f"{os.path.basename(rawpath)}.iso")
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
        tracks._process_message(message)
        if isinstance(message, TInfo):
            found.setdefault(message.tid, {})[message.id] = message.value
    tracks._add_track()

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
    logging.info(f"Extracting titles from {iso}")
    collections.deque(run(cmd, OutputType.MSG), maxlen=0)
    # ARM looks for no titles saved only in MSG 5004, which MakeMKV sends when a
    # title failed. A run with no failures reports 5005.
    if not any(name.endswith(".mkv") for name in os.listdir(titles)):
        raise utils.RipperException(f"MakeMKV extracted no titles from {iso}")
    _arm_write_stamp(job, rawpath, [], _arm_title_map(found, titles))
    return titles


def _arm_title_map(found, titles):
    # Each MKV against the disc's own title number, which is what finds it again
    # in the ISO once titles/ is gone: MakeMKV's _tNN counts only the titles it
    # kept. HandBrake numbers a folder's files from 1 in byte order of their
    # names (qsort in libhb/batch.c), and that number is ARM's title_N. It is
    # null for a title MakeMKV listed but did not save.
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
                "mkv": mkv,
                "handbrake_title": saved.index(mkv) + 1 if mkv in saved else None,
            }
        )
    return entries
