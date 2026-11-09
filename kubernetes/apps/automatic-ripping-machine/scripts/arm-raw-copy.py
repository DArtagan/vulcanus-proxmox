# --- _arm_source_patch raw-copy-code, appended by arm-source-patches.py -------
"""A job's raw copy, recorded in ARM's database for reelbarrow's exporter.

ARM's database records a job's completed folder and never its raw one, nor
whether the copy is whole, nor which transcode each disc title became. The
patches record those here, one JSON document a job, in a table of their own,
and reelbarrow's exporter copies them out with the job. The document is in the
shape of the manifest's `raw` and `titles`, so the exporter only translates
ARM's paths and drops `handbrake_title`, the record's own key for a title:
HandBrake and ARM's moves know a title by nothing else. Appended to utils.py,
whose os, logging and db it uses as module globals, and whose move_files it
wraps so that each transcode's final path is recorded where ARM put it.

The table declares no foreign key: ARM's migrations rebuild `job` with
batch_alter_table, and leave a table they don't name alone. Writes go through
ARM's own session, as a second connection would wait on ARM's uncommitted
writes in this same thread until it timed out.
"""

import json as _arm_json

_ARM_RAW_COPY_SCHEMA = 1


def _arm_raw_copy_table() -> None:
    db.session.execute(
        db.text(
            "CREATE TABLE IF NOT EXISTS reelbarrow_raw_copy"
            " (job_id INTEGER PRIMARY KEY, document TEXT NOT NULL)"
        )
    )


def _arm_raw_copy(job: "Job") -> dict | None:
    """Return the job's document, or None if nothing is recorded for it."""
    _arm_raw_copy_table()
    document = db.session.execute(
        db.text("SELECT document FROM reelbarrow_raw_copy WHERE job_id = :job_id"),
        {"job_id": job.job_id},
    ).scalar()
    return None if document is None else _arm_json.loads(document)


def _arm_save_raw_copy(job: "Job", **fields: object) -> dict:
    """Set `fields` in the job's document, creating it, and commit."""
    document = _arm_raw_copy(job) or {"schema": _ARM_RAW_COPY_SCHEMA}
    document.update(fields)
    db.session.execute(
        db.text(
            "INSERT INTO reelbarrow_raw_copy (job_id, document)"
            " VALUES (:job_id, :document)"
            " ON CONFLICT (job_id) DO UPDATE SET document = excluded.document"
        ),
        {"job_id": job.job_id, "document": _arm_json.dumps(document)},
    )
    db.session.commit()
    return document


def _arm_record_transcode(job: "Job", filename: str, path: str) -> None:
    # On its title, found by HandBrake's number for it. A file no HandBrake
    # track names, such as a raw MKV ARM files untranscoded, is no transcode.
    track = job.tracks.filter_by(source="HandBrake", filename=filename).first()
    if track is None:
        return
    titles = (_arm_raw_copy(job) or {}).get("titles") or []
    number = int(track.track_number)
    title = next((t for t in titles if t.get("handbrake_title") == number), None)
    if title is None:
        logging.warning("Not recording %s: no title %s is recorded", path, number)
        return
    title |= {"transcode": path, "reason": None}
    _arm_save_raw_copy(job, titles=titles)


def _arm_recording_moves(move_files):  # noqa: ANN001, ANN202 - ARM's own
    def recording(base_path, filename, job, is_main_feature=False):  # noqa: ANN001, ANN202, FBT002
        result = move_files(base_path, filename, job, is_main_feature)
        if not filename:
            return result
        # Where ARM's move_files puts it: a series' files and, with
        # keep-main-feature-name, a movie's main feature under their own names
        # in the job's folder, a movie's others in its extras folder.
        if is_main_feature or job.video_type == "series":
            path = os.path.join(job.path, filename)
        else:
            path = os.path.join(job.path, job.config.EXTRAS_SUB, filename)
        # ARM logs a move that fails and carries on, and so does this: the file
        # has moved, and a path not recorded reads downstream as not recorded.
        if not os.path.isfile(path):
            logging.warning("Not recording %s as a transcode: it isn't there", path)
            return result
        try:
            _arm_record_transcode(job, filename, path)
        except Exception:
            db.session.rollback()
            logging.exception("Not recording %s as a transcode", path)
        return result

    return recording


# Rebound here, so ARM's callers, which look move_files up on the module, reach
# the wrapper. Without move_files the rest still loads, and records no paths.
if "move_files" in globals():
    move_files = _arm_recording_moves(move_files)
