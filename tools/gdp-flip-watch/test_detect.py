"""Tests for the generic-device-plugin flip detector.

Run with `python3 -m unittest discover tools/gdp-flip-watch`.

The detector exists to fire a destructive capture — it SIGQUITs the process it
is watching — so a false positive costs a restart of a live DaemonSet, and on
piraeus-worker-1 that briefly withdraws `devic.es/cdrom`. The invariant that
actually matters is the one that already produced a wrong finding once: a fresh
process that comes up slow is indistinguishable from a running process stepping
unless `process_start_time_seconds` is compared across the transition. See
todos/generic-device-plugin-hang.md.
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import detect  # noqa: E402


def stable_run(n: int = 12, ms: float = 2.0) -> list[float]:
    return [ms] * n


class TestIsFlip(unittest.TestCase):
    def test_step_after_stable_run_is_a_flip(self) -> None:
        self.assertTrue(detect.is_flip(stable_run(), 250.0, 100.0, 100.0))

    def test_restart_is_not_a_flip(self) -> None:
        self.assertFalse(detect.is_flip(stable_run(), 250.0, 100.0, 900.0))

    def test_short_stable_run_is_not_enough(self) -> None:
        self.assertFalse(detect.is_flip(stable_run(n=3), 250.0, 100.0, 100.0))

    def test_unstable_lead_in_is_not_a_flip(self) -> None:
        self.assertFalse(detect.is_flip([*stable_run(n=11), 40.0], 250.0, 100.0, 100.0))

    def test_sample_below_threshold_is_not_a_flip(self) -> None:
        self.assertFalse(detect.is_flip(stable_run(), 20.0, 100.0, 100.0))

    def test_missing_start_times_refuse_rather_than_guess(self) -> None:
        self.assertFalse(detect.is_flip(stable_run(), 250.0, None, 100.0))
        self.assertFalse(detect.is_flip(stable_run(), 250.0, 100.0, None))

    def test_real_worker1_flip_09_06(self) -> None:
        # 09-06 12:00:30Z, 1.7ms -> 4338.3ms, process age 4.2 min.
        self.assertTrue(detect.is_flip([1.7] * 16, 4338.3, 1.0, 1.0))

    def test_real_worker1_flip_09_08_slow_step(self) -> None:
        # 09-08 13:48:45Z, 1.5ms -> 62.4ms. The smallest real step seen; the
        # 50ms threshold has to stay below it.
        self.assertTrue(detect.is_flip([1.5] * 16, 62.4, 1.0, 1.0))


class TestIsSustained(unittest.TestCase):
    """An onset alone does not justify the capture, which destroys the process.

    Both excursions seen after Fix C recovered on their own, so firing on an
    onset would have SIGQUITed a healthy plugin on ARM's node for no data.
    """

    def test_three_consecutive_elevated_is_sustained(self) -> None:
        self.assertTrue(detect.is_sustained([200.0, 300.0, 400.0]))

    def test_observed_worker1_transient_is_not_sustained(self) -> None:
        # 2026-09-17 08:56:14Z: 775.6 -> 138.1 -> 5.5, recovered unaided.
        self.assertFalse(detect.is_sustained([775.6, 138.1, 5.5]))

    def test_observed_worker0_transient_is_not_sustained(self) -> None:
        # 2026-09-17 06:23:32Z: a single 98.5ms sample.
        self.assertFalse(detect.is_sustained([98.5, 1.9, 1.6]))

    def test_two_elevated_is_not_enough(self) -> None:
        self.assertFalse(detect.is_sustained([200.0, 300.0]))

    def test_only_the_tail_counts(self) -> None:
        # Recovery then a fresh climb must not borrow the earlier excursion.
        self.assertFalse(detect.is_sustained([900.0, 900.0, 2.0, 900.0]))

    def test_historical_onset_climb_is_sustained(self) -> None:
        # Post-onset medians from the age-vs-latency table: once a real flip
        # starts it stays up and climbs, so this must fire.
        self.assertTrue(detect.is_sustained([113.0, 190.0, 404.0]))


class TestDistinctScrapes(unittest.TestCase):
    """Polling faster than Prometheus scrapes defeats the persistence check.

    The watcher polls every 15s; after Fix C the PodMonitor scrapes every 60s,
    so an instant query returns the same value four times over. Three identical
    reads of one elevated scrape are not three elevated scrapes, and treating
    them as such produced four "confirmed flips" on worker-0 between 2026-09-19
    and 2026-09-20 whose samples were byte-identical floats.
    """

    def test_repeated_reads_of_one_scrape_are_not_sustained(self):
        # Verbatim from excursions.jsonl, 2026-09-19T17:05:04Z.
        self.assertFalse(detect.is_sustained([887.510338, 887.510338, 887.510338]))

    def test_distinct_elevated_scrapes_are_sustained(self):
        self.assertTrue(detect.is_sustained([887.5, 912.3, 1043.8]))

    def test_near_identical_but_distinct_values_still_count(self):
        # Real scrapes differ; only exact repeats are suspect.
        self.assertTrue(detect.is_sustained([200.0, 200.1, 200.2]))


if __name__ == "__main__":
    unittest.main()
