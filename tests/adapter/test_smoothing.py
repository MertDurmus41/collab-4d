# -*- coding: utf-8 -*-
"""Smoothing of remote cameras: pure tests (no Cinema 4D needed)."""
from __future__ import absolute_import, division, print_function, unicode_literals

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "plugin", "c4d_collab")))

from c4d_collab_lib.c4d_adapter.overlay import CameraSmoother  # noqa: E402

A = [0.0, 0.0, 0.0, 1, 0, 0, 0, 1, 0, 0, 0, 1]
B = [100.0, 50.0, -20.0, 1, 0, 0, 0, 1, 0, 0, 0, 1]


class CameraSmootherTest(unittest.TestCase):
    def test_first_sighting_appears_in_place(self):
        smoother = CameraSmoother()
        smoother.retarget({"u2": B})
        self.assertEqual(smoother.poses()["u2"], B)
        self.assertFalse(smoother.advance(0.0))

    def test_moves_toward_the_target_in_several_steps_and_then_stops(self):
        smoother = CameraSmoother()
        smoother.retarget({"u2": A})
        smoother.advance(0.0)
        smoother.retarget({"u2": B})
        positions = []
        now = 0.0
        moving = True
        while moving and now < 2.0:
            now += 1.0 / 30
            moving = smoother.advance(now)
            positions.append(smoother.poses()["u2"][0])
        self.assertGreater(len(positions), 5, "a jump of 100 cm is spread over many frames")
        self.assertTrue(all(b >= a for a, b in zip(positions, positions[1:])), "monotonic, no overshoot")
        self.assertTrue(all(p <= 100.0 for p in positions))
        self.assertEqual(smoother.poses()["u2"], B)
        self.assertFalse(smoother.advance(now + 0.1), "converged: nothing left to animate")

    def test_progress_depends_on_elapsed_time_not_on_call_count(self):
        coarse, fine = CameraSmoother(), CameraSmoother()
        for smoother in (coarse, fine):
            smoother.retarget({"u2": A})
            smoother.advance(0.0)
            smoother.retarget({"u2": B})
        coarse.advance(0.1)
        for step in range(1, 11):
            fine.advance(0.01 * step)
        self.assertAlmostEqual(coarse.poses()["u2"][0], fine.poses()["u2"][0], places=3)

    def test_a_stalled_ui_does_not_fling_the_camera(self):
        smoother = CameraSmoother()
        smoother.retarget({"u2": A})
        smoother.advance(0.0)
        smoother.retarget({"u2": B})
        smoother.advance(60.0)  # the interface froze for a minute
        self.assertLess(smoother.poses()["u2"][0], 100.0)
        self.assertGreater(smoother.poses()["u2"][0], 90.0)

    def test_users_that_left_and_garbage_are_dropped(self):
        smoother = CameraSmoother()
        smoother.retarget({"u2": A, "u3": B})
        smoother.retarget({"u3": B, "u4": "nonsense", "u5": [float("nan")] * 12})
        self.assertEqual(sorted(smoother.poses()), ["u3"])

    def test_reset(self):
        smoother = CameraSmoother()
        smoother.retarget({"u2": A})
        smoother.reset()
        self.assertEqual(smoother.poses(), {})


if __name__ == "__main__":
    unittest.main()
