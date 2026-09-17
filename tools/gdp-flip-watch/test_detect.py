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

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import detect  # noqa: E402


def stable_run(n=12, ms=2.0):
    return [ms] * n


class TestIsFlip(unittest.TestCase):
    def test_step_after_stable_run_is_a_flip(self):
        self.assertTrue(detect.is_flip(stable_run(), 250.0, 100.0, 100.0))

    def test_restart_is_not_a_flip(self):
        self.assertFalse(detect.is_flip(stable_run(), 250.0, 100.0, 900.0))

    def test_short_stable_run_is_not_enough(self):
        self.assertFalse(detect.is_flip(stable_run(n=3), 250.0, 100.0, 100.0))

    def test_unstable_lead_in_is_not_a_flip(self):
        self.assertFalse(detect.is_flip(stable_run(n=11) + [40.0], 250.0, 100.0, 100.0))

    def test_sample_below_threshold_is_not_a_flip(self):
        self.assertFalse(detect.is_flip(stable_run(), 20.0, 100.0, 100.0))

    def test_missing_start_times_refuse_rather_than_guess(self):
        self.assertFalse(detect.is_flip(stable_run(), 250.0, None, 100.0))
        self.assertFalse(detect.is_flip(stable_run(), 250.0, 100.0, None))

    def test_real_worker1_flip_09_06(self):
        # 09-06 12:00:30Z, 1.7ms -> 4338.3ms, process age 4.2 min.
        self.assertTrue(detect.is_flip([1.7] * 16, 4338.3, 1.0, 1.0))

    def test_real_worker1_flip_09_08_slow_step(self):
        # 09-08 13:48:45Z, 1.5ms -> 62.4ms. The smallest real step seen; the
        # 50ms threshold has to stay below it.
        self.assertTrue(detect.is_flip([1.5] * 16, 62.4, 1.0, 1.0))


if __name__ == "__main__":
    unittest.main()
