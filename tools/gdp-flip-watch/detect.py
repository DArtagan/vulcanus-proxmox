"""Flip detection for generic-device-plugin.

A flip is a running process stepping from a sub-10ms gather to a degraded one
inside a single scrape interval. Thresholds come from 29 measured onsets on
piraeus-worker-1 between 2026-08-27 and 2026-09-16: the median step is 495x,
the smallest real one observed is 1.5ms -> 62.4ms, and the two minutes before a
flip sit at 6.4ms or below.
"""

STABLE_MS = 10.0
FLIP_MS = 50.0
MIN_RUN = 10
CONFIRM_RUN = 3


def is_flip(
    recent_ms,
    current_ms,
    prev_start,
    cur_start,
    stable_ms=STABLE_MS,
    flip_ms=FLIP_MS,
    min_run=MIN_RUN,
):
    """True when `current_ms` is the first degraded sample of an in-process flip.

    `prev_start` and `cur_start` are `process_start_time_seconds` either side of
    the transition. They must be equal: a fresh process that comes up already
    slow presents exactly like a running process stepping, and treating the two
    alike once yielded a monotonic counter apparently decreasing at onset.
    Unknown start times refuse, because the destructive capture this gates is
    not worth firing on a guess.
    """
    if prev_start is None or cur_start is None:
        return False
    if prev_start != cur_start:
        return False
    if current_ms <= flip_ms:
        return False
    if len(recent_ms) < min_run:
        return False
    return all(sample < stable_ms for sample in recent_ms[-min_run:])


def is_sustained(recent_ms, confirm_run=CONFIRM_RUN, flip_ms=FLIP_MS):
    """True when the last `confirm_run` samples are all degraded.

    An onset is not enough to justify the capture, which kills the process. Both
    excursions observed after Fix C recovered unaided — 775.6/138.1/5.5 ms on
    worker-1 and a lone 98.5 ms on worker-0 — so firing on an onset would have
    destroyed a healthy plugin and produced nothing.

    Three is calibrated against those two transients, the longest of which held
    for two samples. If a three-sample transient is ever recorded, raise it: the
    cost of waiting is a later capture, and the cost of firing early is a live
    process on worker-1 and the ARM admission window that comes with it.
    """
    if len(recent_ms) < confirm_run:
        return False
    tail = recent_ms[-confirm_run:]
    if not all(sample > flip_ms for sample in tail):
        return False
    # Exact repeats are one scrape read several times, not several scrapes.
    # `watch.py` already drops those by scrape timestamp; this is the backstop,
    # because the failure is silent and reads as a confirmed flip.
    return len(set(tail)) == len(tail)
