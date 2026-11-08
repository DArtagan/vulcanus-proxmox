# --- _arm_source_patch duplicate-titles-code, appended by arm-source-patches.py -
# A title is not transcoded when the titles that are kept already play it: a
# TV disc's play-all, a bonus disc's play-all of its featurettes, a film with
# featurettes cut into it. Titles are compared by what they play from the
# disc, a Blu-ray playlist's clip times or a DVD title's cell sectors, never by
# length alone. Appended to handbrake.py, whose os, logging, subprocess, cfg,
# db and utils it uses as module globals.
import ast as _arm_ast  # noqa: E402
import json as _arm_json  # noqa: E402
import struct as _arm_struct  # noqa: E402


_ARM_BD_TICK = 45000
# A part is inside a whole when this much of it is. Parts of one whole overlap
# each other by less than _ARM_PARTS_OVERLAP: a DVD's episodes can share a
# closing cell, which its play-all leaves out.
_ARM_PART_INSIDE = 0.95
_ARM_PARTS_OVERLAP = 0.05
# Nothing is skipped unless the titles kept play this much of it. Below it sit
# wholes holding pieces too short to be titles of their own, and films built
# from clips the disc also offers singly.
_ARM_COVERED = 0.99
# Seconds between a part's start and the chapter marking the join.
_ARM_JOIN = 1.0
# What a second of a DVD's IFO plays for, by its frame rate. NTSC timecode
# counts 30 frames to its second while the video runs at 30000/1001, so
# lsdvd's lengths are 0.1% short, and unscaled a part's start lands seconds
# early against the transcode's chapters, which keep playback time.
_ARM_DVD_SECOND = {25.0: 1.0, 29.97: 1001 / 1000}
# The frames compared either side of a join: small and grey, as what tells a
# continuing shot from a jump is the picture's structure, not its detail.
_ARM_FRAME = (64, 36)
# Frames this alike are one picture. A piece of a clip whose frames all match
# its first is a still card, like the anti-ripping warning job 100's disc
# splices into a decoy.
_ARM_STILL = 0.01


def _arm_duplicate_titles(srcpath, job):
    # HandBrake's numbers for the titles not to transcode, each with the reason.
    if job.disctype not in ("bluray", "dvd"):
        return {}
    srcpath = os.path.normpath(srcpath)
    raw = srcpath if job.disctype == "bluray" else os.path.dirname(srcpath)
    minimum = int(cfg.arm_config["MINLENGTH"])
    maximum = int(cfg.arm_config["MAXLENGTH"])
    tracks = {
        int(track.track_number): track
        for track in job.tracks.filter_by(source="HandBrake")
        if minimum <= track.length <= maximum
    }
    series = job.video_type == "series"
    try:
        titles, skip, wholes, orders = {}, {}, [], []
        # A Blu-ray is scanned whatever its count, as the scan is its title map.
        if job.disctype == "bluray":
            titles = _arm_bluray_titles(srcpath, tracks)
        elif len(tracks) >= 3:
            titles = _arm_dvd_titles(raw, tracks)
        skip, orders = _arm_orders(
            srcpath if job.disctype == "bluray" else None, titles
        )
        remaining = {
            number: title for number, title in titles.items() if number not in skip
        }
        # A whole and two parts, or nothing to compare.
        if len(remaining) >= 3:
            repeats, wholes = _arm_duplicates(remaining, series)
            skip.update(repeats)
    except Exception as error:  # noqa: BLE001
        # Skipping is an economy. Whatever it trips on costs encoding time,
        # never the job.
        logging.warning(
            f"Transcoding every title, as the disc's structure cannot be read: {error!r}"
        )
        return {}

    for number, reason in sorted(skip.items()):
        logging.info(f"Title {number} is not transcoded: it {reason}")
    _arm_move_main_feature(tracks, skip)
    _arm_record_scan(raw, titles, skip, wholes, orders)
    for order in orders:
        playlist = titles[order["title"]]["source"]["playlist"]
        if order["mismatch"] is None:
            choice = (
                "Their joins could not be compared, so title "
                f"{order['title']} ({playlist}) is transcoded and is likely out of order."
            )
        else:
            runner_up = order["runner_up"]
            choice = (
                f"Title {order['title']} ({playlist}) is transcoded, as its joins match best: "
                f"{order['mismatch']:.2f}, against {runner_up['mismatch']:.2f} for title {runner_up['title']}."
            )
        utils.notify(
            job,
            utils.NOTIFY_TITLE,
            f"{job.title}: {order['titles']} titles play the same clips in different orders. {choice} "
            "If it plays out of order, transcode another by hand from the raw copy.",
        )
    for whole in wholes:
        # A series' play-all can open on a recap its episodes share, which
        # reads as interleaved and is no film.
        if whole["interleaved"] and whole["title"] in skip and not series:
            utils.notify(
                job,
                utils.NOTIFY_TITLE,
                f"{job.title}: title {whole['title']} is not transcoded, as it {skip[whole['title']]}. "
                "If it is an extended cut of the film, transcode it by hand from the raw copy.",
            )
    return skip


def _arm_orders(srcpath, titles):
    # One title of each group that plays the same clips: playlist obfuscation
    # offers an episode or a film as hundreds of playlists of its clips in
    # different orders, only one of them in order. Kept is the first, when they
    # all play their clips in one order, and on a Blu-ray otherwise the one
    # whose joins match best. A DVD's are left to _arm_duplicates, as reading
    # its frames would mean reading cells out of the ISO.
    skip, orders, cache = {}, [], {}
    still = (
        None if srcpath is None else (lambda piece: _arm_still(srcpath, piece, cache))
    )
    for group in _arm_same_content(titles, still):
        order = {
            number: tuple(segment[:3] for segment in titles[number]["segments"])
            for number in group
        }
        if len(set(order.values())) > 1 and srcpath is None:
            continue
        mismatch = None
        if len(set(order.values())) > 1:
            try:
                mismatch = {
                    number: _arm_mismatch(srcpath, titles[number]["segments"], cache)
                    for number in group
                }
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                logging.warning(
                    f"The joins of titles {group} cannot be compared: {error!r}"
                )
            ranked = (
                sorted(group, key=lambda number: (mismatch[number], number))
                if mismatch
                else group
            )
            kept = ranked[0]
            runner_up = next(
                number for number in ranked if order[number] != order[kept]
            )
            orders.append(
                {
                    "title": kept,
                    "mismatch": mismatch and mismatch[kept],
                    "titles": len(group),
                    "runner_up": mismatch
                    and {"title": runner_up, "mismatch": mismatch[runner_up]},
                }
            )
        else:
            kept = group[0]
        for number in group:
            if number == kept:
                continue
            extra = _arm_own(titles[number]["segments"], [titles[kept]["segments"]])
            if _arm_units(extra) > (1 - _ARM_COVERED) * _arm_units(
                titles[number]["segments"]
            ):
                skip[number] = f"plays title {kept}'s clips and a still picture"
            elif order[number] == order[kept]:
                skip[number] = f"is a copy of title {kept}"
            else:
                skip[number] = f"plays title {kept}'s clips out of order"
    return skip, orders


def _arm_same_content(titles, still=None):
    # Groups of two or more titles, each playing all but 1% of each other. With
    # `still`, what one title inside another plays beyond it counts as played
    # when every piece of it is a still picture.
    units = {number: _arm_units(title["segments"]) for number, title in titles.items()}

    def plays(number, other):
        segments = titles[number]["segments"]
        covered = _arm_covered(segments, [titles[other]["segments"]])
        if covered >= _ARM_COVERED * units[number]:
            return True
        if still is None or covered < _ARM_PART_INSIDE * units[number]:
            return False
        return all(
            still(piece) for piece in _arm_own(segments, [titles[other]["segments"]])
        )

    groups = []
    for number in sorted(number for number in titles if units[number]):
        for group in groups:
            if plays(group[0], number) and plays(number, group[0]):
                group.append(number)
                break
        else:
            groups.append([number])
    return [group for group in groups if len(group) > 1]


def _arm_mismatch(srcpath, segments, cache):
    # 1 - SSIM between the last frame before each join and the first after it,
    # summed. In order, a join cuts within a shot and scores near 0; out of
    # order nearly every join is a jump. A join on a cut between shots is a
    # jump either way, which is why the sum decides and no one join does.
    total = 0.0
    for (clip, _, end, _), (following, start, _, _) in zip(segments, segments[1:]):
        pair = ((clip, end, True), (following, start, False))
        if pair not in cache:
            cache[pair] = 1 - _arm_ssim(
                *(_arm_frame(srcpath, *frame, cache) for frame in pair)
            )
        total += cache[pair]
    return total


def _arm_frame(srcpath, clip, time, last, cache):
    # A clip's frame at `time`, in the ticks its play items count: the last
    # before it, or the first from it. ffmpeg is named, not taken from
    # FFMPEG_LOCAL, whose default "FFMPEG" is no binary.
    key = (clip, time, last)
    if key not in cache:
        stream = os.path.join(srcpath, "BDMV", "STREAM", f"{clip}.m2ts")
        at = (time - _arm_clip_start(srcpath, clip, cache)) / _ARM_BD_TICK
        window = (
            ["-ss", f"{max(at - 1, 0):.3f}", "-t", f"{min(at, 1):.3f}"]
            if last
            else ["-ss", f"{at:.3f}"]
        )
        width, height = _ARM_FRAME
        picture = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                *window,
                "-i",
                stream,
                "-map",
                "0:v:0",
                *([] if last else ["-frames:v", "1"]),
                "-vf",
                f"scale={width}:{height},format=gray",
                "-f",
                "rawvideo",
                "-",
            ],
            capture_output=True,
            check=True,
        ).stdout
        if len(picture) < width * height:
            raise ValueError(f"{stream} has no frame at {at:.3f}s")
        cache[key] = picture[-width * height :] if last else picture[: width * height]
    return cache[key]


def _arm_still(srcpath, piece, cache):
    # Whether a piece of a clip shows one picture throughout: its frames at its
    # quarters and its end all match its first. One that can't be read is not,
    # so the title holding it is kept.
    clip, start, end, _ = piece
    key = ("still", clip, start, end)
    if key not in cache:
        try:
            first = _arm_frame(srcpath, clip, start, False, cache)
            samples = [
                _arm_frame(srcpath, clip, start + (end - start) * k // 4, False, cache)
                for k in (1, 2, 3)
            ]
            samples.append(_arm_frame(srcpath, clip, end, True, cache))
            cache[key] = all(
                1 - _arm_ssim(first, sample) < _ARM_STILL for sample in samples
            )
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            logging.warning(
                f"Taking clip {clip} for moving pictures, as its frames cannot be read: {error!r}"
            )
            cache[key] = False
    return cache[key]


def _arm_clip_start(srcpath, clip, cache):
    # When a clip's first frame is presented, which its play items' times count
    # from: the presentation start of its CLIPINF's only STC sequence.
    if clip not in cache:
        path = os.path.join(srcpath, "BDMV", "CLIPINF", f"{clip}.clpi")
        with open(path, "rb") as f:
            data = f.read()
        if data[:4] != b"HDMV":
            raise ValueError(f"{path} is not clip information")
        sequences = _arm_struct.unpack_from(">I", data, 8)[0]
        if data[sequences + 5] != 1 or data[sequences + 10] != 1:
            raise ValueError(f"{path} has more than one time base")
        cache[clip] = _arm_struct.unpack_from(">I", data, sequences + 18)[0]
    return cache[clip]


def _arm_ssim(first, second):
    # Structural similarity of two frames: the mean over 8x8 windows half a
    # window apart, with SSIM's constants for 8-bit samples.
    width, height = _ARM_FRAME
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    scores = []
    for top in range(0, height - 7, 4):
        for left in range(0, width - 7, 4):
            cells = [
                row * width + column
                for row in range(top, top + 8)
                for column in range(left, left + 8)
            ]
            xs, ys = [first[cell] for cell in cells], [second[cell] for cell in cells]
            mx, my = sum(xs) / 64, sum(ys) / 64
            vx = sum((x - mx) ** 2 for x in xs) / 63
            vy = sum((y - my) ** 2 for y in ys) / 63
            cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / 63
            scores.append(
                ((2 * mx * my + c1) * (2 * cov + c2))
                / ((mx * mx + my * my + c1) * (vx + vy + c2))
            )
    return sum(scores) / len(scores)


def _arm_duplicates(titles, series):
    # A whole is a title that two or more others, each inside it and none
    # overlapping another, play between them. A series keeps the parts, since
    # each episode is an item of its own in the library. Otherwise the whole is
    # kept when its parts follow one another with a chapter at each join: the
    # encode has a keyframe at every chapter, so the parts can be cut from it
    # without re-encoding. Parts that interleave, as with featurettes cut into
    # a film, are kept apart.
    units = {number: _arm_units(title["segments"]) for number, title in titles.items()}
    titles = {number: title for number, title in titles.items() if units[number]}

    def inside(part, whole):
        return (
            _arm_covered(titles[part]["segments"], [titles[whole]["segments"]])
            / units[part]
        )

    wholes = []
    for whole in sorted(titles, key=lambda number: (-units[number], number)):
        parts = []
        for part in sorted(titles, key=lambda number: (-units[number], number)):
            # A part playing the whole is the whole again: taken as a part, it
            # would overlap every real one and leave the whole with none.
            if (
                part == whole
                or inside(part, whole) < _ARM_PART_INSIDE
                or inside(whole, part) >= _ARM_COVERED
            ):
                continue
            if all(
                _arm_covered(titles[part]["segments"], [titles[other]["segments"]])
                < _ARM_PARTS_OVERLAP * units[part]
                for other in parts
            ):
                parts.append(part)
        if len(parts) < 2:
            continue
        played = _arm_covered(
            titles[whole]["segments"], [titles[part]["segments"] for part in parts]
        )
        if played < _ARM_COVERED * units[whole]:
            continue

        def place(part, parts=parts, whole=whole):
            # By what only this part plays: featurettes sharing an intro, or
            # episodes a recap, would otherwise all start where it does and
            # read as interleaved.
            others = [titles[other]["segments"] for other in parts if other != part]
            own = _arm_own(titles[part]["segments"], others) or titles[part]["segments"]
            return _arm_span(own, titles[whole]["segments"])

        spans = sorted((place(part), part) for part in parts)
        interleaved = any(
            later[0][0] < earlier[0][1] - _ARM_JOIN
            for earlier, later in zip(spans, spans[1:])
        )
        joined = all(
            any(
                abs(chapter - start) <= _ARM_JOIN
                for chapter in titles[whole]["chapters"]
            )
            for (start, _), _ in spans[1:]
        )
        wholes.append(
            {
                "title": whole,
                "parts": [
                    {"title": part, "start": round(start, 3)}
                    for (start, _), part in spans
                ],
                "interleaved": interleaved,
                "keep_whole": not (series or interleaved or not joined),
            }
        )

    skip = {}
    for whole in wholes:
        number = whole["title"]
        if number in skip:
            continue
        if whole["keep_whole"]:
            for other in titles:
                if (
                    other != number
                    and other not in skip
                    and inside(other, number) >= _ARM_COVERED
                ):
                    skip[other] = f"is played whole by title {number}"
        elif whole["interleaved"]:
            parts = " and ".join(str(part["title"]) for part in whole["parts"])
            skip[number] = f"interleaves titles {parts}"
        else:
            parts = ", ".join(str(part["title"]) for part in whole["parts"])
            skip[number] = f"plays titles {parts} in turn"

    # Unskip until every skipped title is played by those kept.
    while True:
        kept = [
            title["segments"] for number, title in titles.items() if number not in skip
        ]
        short = [
            number
            for number in sorted(skip)
            if _arm_covered(titles[number]["segments"], kept)
            < _ARM_COVERED * units[number]
        ]
        if not short:
            break
        del skip[short[0]]

    for whole in wholes:
        del whole["keep_whole"]
        if whole["title"] in skip:
            whole["kept"] = "parts"
        elif all(part["title"] in skip for part in whole["parts"]):
            whole["kept"] = "whole"
        else:
            whole["kept"] = "both"
    return skip, wholes


def _arm_units(segments):
    return sum(end - start for _, start, end, _ in segments)


def _arm_covered(segments, others):
    # How much of `segments` at least one of `others` also plays, in the
    # segments' own units: ticks of a clip, or sectors of a title set.
    total = 0
    for source, start, end, _ in segments:
        spans = sorted(
            (max(start, other_start), min(end, other_end))
            for other in others
            for other_source, other_start, other_end, _ in other
            if other_source == source and min(end, other_end) > max(start, other_start)
        )
        reached = start
        for span_start, span_end in spans:
            span_start = max(span_start, reached)
            if span_end > span_start:
                total += span_end - span_start
                reached = span_end
    return total


def _arm_own(segments, others):
    # `segments` less whatever any of `others` also plays.
    own = []
    for source, start, end, seconds in segments:
        rate = seconds / (end - start) if end > start else 0.0
        spans = sorted(
            (other_start, other_end)
            for other in others
            for other_source, other_start, other_end, _ in other
            if other_source == source
        )
        reached = start
        for span_start, span_end in spans + [(end, end)]:
            if min(span_start, end) > reached:
                own.append(
                    (
                        source,
                        reached,
                        min(span_start, end),
                        (min(span_start, end) - reached) * rate,
                    )
                )
            reached = max(reached, span_end)
    return own


def _arm_span(part, whole):
    # The first and last second of `whole` that plays any of `part`.
    first = last = None
    elapsed = 0.0
    for source, start, end, seconds in whole:
        rate = seconds / (end - start) if end > start else 0.0
        for part_source, part_start, part_end, _ in part:
            low, high = max(start, part_start), min(end, part_end)
            if part_source == source and high > low:
                begins = elapsed + (low - start) * rate
                ends = elapsed + (high - start) * rate
                first = begins if first is None else min(first, begins)
                last = ends if last is None else max(last, ends)
        elapsed += seconds
    return first, last


def _arm_bluray_titles(srcpath, tracks):
    # HandBrake numbers only the playlists libbluray keeps, and its JSON scan
    # names each one's playlist. --min-duration leaves the numbering as ARM's
    # own scan has it and skips decoding previews of every short playlist,
    # which on a disc of 180 took five minutes.
    scan = subprocess.run(
        [
            cfg.arm_config["HANDBRAKE_LOCAL"],
            "--json",
            "-i",
            srcpath,
            "-t",
            "0",
            "--scan",
            "--min-duration",
            str(cfg.arm_config["MINLENGTH"]),
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    marker = "JSON Title Set:"
    title_set, _ = _arm_json.JSONDecoder().raw_decode(
        scan[scan.index(marker) + len(marker) :].lstrip()
    )
    folder = os.path.join(srcpath, "BDMV", "PLAYLIST")
    playlists = {name.lower(): name for name in os.listdir(folder)}
    titles = {}
    for title in title_set["TitleList"]:
        if title["Index"] not in tracks:
            continue
        name = playlists[f"{title['Playlist']:05d}.mpls"]
        segments, chapters = _arm_mpls(os.path.join(folder, name))
        titles[title["Index"]] = {
            "segments": segments,
            "chapters": chapters,
            "source": {"playlist": name},
        }
    return titles


def _arm_mpls(path):
    # A playlist's play items, each a clip between two times, and its chapter
    # marks in seconds from the playlist's start.
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"MPLS":
        raise ValueError(f"{path} is not a playlist")
    items_at, marks_at = _arm_struct.unpack_from(">II", data, 8)
    offset = items_at + 10
    segments, starts, elapsed = [], [], 0.0
    for _ in range(_arm_struct.unpack_from(">H", data, items_at + 6)[0]):
        length = _arm_struct.unpack_from(">H", data, offset)[0]
        clip = data[offset + 2 : offset + 7].decode("ascii")
        start, end = _arm_struct.unpack_from(">II", data, offset + 14)
        segments.append((clip, start, end, (end - start) / _ARM_BD_TICK))
        starts.append(elapsed)
        elapsed += (end - start) / _ARM_BD_TICK
        offset += 2 + length
    chapters = []
    for mark in range(_arm_struct.unpack_from(">H", data, marks_at + 4)[0]):
        kind, item, stamp = _arm_struct.unpack_from(
            ">xBHI", data, marks_at + 6 + 14 * mark
        )
        # Kind 1 is an entry mark, a chapter; kind 2 is a link point.
        if kind == 1 and item < len(segments):
            chapters.append(starts[item] + (stamp - segments[item][1]) / _ARM_BD_TICK)
    return segments, chapters


def _arm_dvd_titles(raw, tracks):
    # The stamp maps HandBrake's numbers to the DVD's own, and lsdvd reads each
    # of those titles' cells and chapters from the ISO. A cell's sectors count
    # within its title set, whose number, like the frame rate, only -v prints.
    with open(os.path.join(raw, "arm-job.json")) as f:
        mapped = _arm_json.load(f).get("title_map") or []
    images = [name for name in os.listdir(raw) if name.endswith(".iso")]
    if not mapped or len(images) != 1:
        raise ValueError(f"{raw} has no title map, or not exactly one ISO")
    listing = subprocess.run(
        ["lsdvd", "-c", "-d", "-v", "-Oy", os.path.join(raw, images[0])],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    disc = {
        title["ix"]: title
        for title in _arm_ast.literal_eval(listing.split("=", 1)[1])["track"]
    }
    titles = {}
    for entry in mapped:
        number, disc_title = entry["handbrake_title"], entry["disc_title"]
        if number not in tracks or disc_title not in disc:
            continue
        title = disc[disc_title]
        second = _ARM_DVD_SECOND[title["fps"]]
        lengths = [chapter["length"] * second for chapter in title["chapter"]]
        titles[number] = {
            "segments": [
                (
                    title["vts"],
                    cell["first_sector"],
                    cell["last_sector"] + 1,
                    cell["length"] * second,
                )
                for cell in title["cell"]
            ],
            "chapters": [sum(lengths[:index]) for index in range(len(lengths))],
            "source": {"disc_title": disc_title},
        }
    return titles


def _arm_move_main_feature(tracks, skip):
    # A movie's transcodes are named from the HandBrake track marked as its main
    # feature, and a skipped title has no transcode to name.
    if not any(tracks[number].main_feature for number in skip):
        return
    for number in skip:
        tracks[number].main_feature = False
    kept = [track for number, track in tracks.items() if number not in skip]
    max(kept, key=lambda track: track.length).main_feature = True
    db.session.commit()


def _arm_record_scan(raw, titles, skip, wholes, orders):
    # The map from each whole to its parts, with where each part starts, is what
    # cuts a kept whole into its parts later.
    path = os.path.join(raw, "arm-job.json")
    try:
        with open(path) as f:
            stamp = _arm_json.load(f)
    except (OSError, ValueError) as error:
        logging.warning(f"Not recording the skipped titles in {path}: {error}")
        return

    def entry(number):
        return {"handbrake_title": number, **titles[number]["source"]}

    stamp["duplicates"] = {
        "skipped": [
            {**entry(number), "reason": reason}
            for number, reason in sorted(skip.items())
        ],
        "wholes": [
            {
                **entry(whole["title"]),
                "kept": whole["kept"],
                "interleaved": whole["interleaved"],
                "parts": [
                    {**entry(part["title"]), "start": part["start"]}
                    for part in whole["parts"]
                ],
            }
            for whole in wholes
        ],
        "orders": [
            {
                **entry(order["title"]),
                "mismatch": order["mismatch"] and round(order["mismatch"], 3),
                "titles": order["titles"],
                "runner_up": order["runner_up"]
                and {
                    **entry(order["runner_up"]["title"]),
                    "mismatch": round(order["runner_up"]["mismatch"], 3),
                },
            }
            for order in orders
        ],
    }
    if any("playlist" in title["source"] for title in titles.values()):
        # A Blu-ray's title map, each playlist against its title_N, as a DVD's
        # maps the disc's titles through their MKVs. Length can't stand in for
        # it: featurette variants a second apart share all but a clip.
        stamp["title_map"] = [
            {**titles[number]["source"], "handbrake_title": number}
            for number in sorted(titles)
        ]
    with open(f"{path}.tmp", "w") as f:
        _arm_json.dump(stamp, f, indent=2)
        f.write("\n")
    os.replace(f"{path}.tmp", path)
