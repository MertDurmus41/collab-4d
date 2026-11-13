# -*- coding: utf-8 -*-
"""Phase 7: who is where and who selected what, in the viewport overlay.

Pure geometry tests (no Cinema 4D needed) plus two-instance tests with the fake C4D.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "plugin", "c4d_collab")))

import fake_scene as fs  # noqa: E402
from c4d_collab_lib.c4d_adapter import overlay  # noqa: E402

CAMERA = [10.0, 20.0, 30.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]  # pos, right, up, forward
USERS = {
    "u1": {"name": "Mert", "color": [0.95, 0.35, 0.30]},
    "u2": {"name": "Enjoy", "color": [0.25, 0.65, 0.95]},
}


def distance(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


class CameraParsingTest(unittest.TestCase):
    def test_valid_camera(self):
        pos, right, up, forward = overlay.parse_camera(CAMERA)
        self.assertEqual(pos, (10.0, 20.0, 30.0))
        self.assertEqual((right, up, forward), ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))

    def test_axes_are_normalised(self):
        camera = overlay.parse_camera([0, 0, 0, 5, 0, 0, 0, 3, 0, 0, 0, 2])
        self.assertAlmostEqual(distance(camera[1], (0, 0, 0)), 1.0)

    def test_untrusted_data_is_rejected(self):
        bad = [None, "cam", [], CAMERA[:11], CAMERA + [1.0], [float("nan")] + CAMERA[1:],
               [float("inf")] + CAMERA[1:], [1e12] + CAMERA[1:], ["x"] + CAMERA[1:], [True] + CAMERA[1:],
               CAMERA[:3] + [0, 0, 0] + CAMERA[6:],  # zero-length axis
               {"p": 1}]
        for data in bad:
            self.assertIsNone(overlay.parse_camera(data), repr(data))


class IconTest(unittest.TestCase):
    def test_size_scales_with_distance_and_is_clamped(self):
        near = overlay.icon_size((0, 0, 0), (0, 0, 100))
        far = overlay.icon_size((0, 0, 0), (0, 0, 10000))
        self.assertLess(near, far)
        self.assertEqual(overlay.icon_size((0, 0, 0), (0, 0, 0)), overlay.MIN_ICON)
        self.assertEqual(overlay.icon_size((0, 0, 0), (0, 0, 1e8)), overlay.MAX_ICON)
        self.assertEqual(overlay.icon_size(None, (0, 0, 0)), 40.0)

    def test_camera_icon_shape(self):
        camera = overlay.parse_camera(CAMERA)
        lines = overlay.camera_icon(camera, 10.0)
        self.assertEqual(len(lines), 10)
        pyramid = [line for line in lines if line[0] == camera[0]]
        self.assertEqual(len(pyramid), 4, "four edges from the eye to the frame")
        for _start, end in pyramid:
            self.assertAlmostEqual(end[2] - camera[0][2], 20.0, msg="the frame is 2x size ahead of the eye")


class BoxTest(unittest.TestCase):
    AXES = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))

    def test_twelve_distinct_edges_and_top_corner(self):
        edges, top = overlay.box_lines((100.0, 0.0, 0.0), self.AXES, (0, 0, 0), (10, 20, 30))
        self.assertEqual(len(edges), 12)
        self.assertEqual(len(set((tuple(a), tuple(b)) for a, b in edges)), 12)
        self.assertEqual(top[1], 20.0)
        xs = sorted(set(p[0] for edge in edges for p in edge))
        self.assertEqual(xs, [90.0, 110.0])

    def test_object_without_size_still_gets_a_visible_box(self):
        edges, _top = overlay.box_lines((0, 0, 0), self.AXES, (0, 0, 0), (0, 0, 0))
        extent = max(abs(c) for edge in edges for p in edge for c in p)
        self.assertEqual(extent, overlay.MIN_BOX)

    def test_follows_rotation_and_scale(self):
        rotated = ((0.0, 0.0, 2.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0))  # turned 90 degrees, X doubled
        edges, _top = overlay.box_lines((0, 0, 0), rotated, (0, 0, 0), (10, 10, 10))
        zs = sorted(set(p[2] for edge in edges for p in edge))
        self.assertEqual(zs, [-20.0, 20.0])


class OutlineTest(unittest.TestCase):
    def test_a_real_outline_replaces_the_box(self):
        outline = [((0, 0, 0), (1, 0, 0)), ((1, 0, 0), (0, 1, 0))]
        users = {"u": {"name": "Ufuk", "color": [0.1, 0.2, 0.3]}}
        content = overlay.build(users, {}, {"n": "u"}, "me", None,
                                lambda uuid: ((0, 0, 0), ((1, 0, 0), (0, 1, 0), (0, 0, 1)), (0, 0, 0), (50, 50, 50), outline))
        self.assertEqual(len(content.lines), 2, "just the outline, no box")
        self.assertEqual(content.lines[0][2], (0.1, 0.2, 0.3))
        self.assertEqual([text for _p, text, _c in content.labels], ["Ufuk"])

    def test_without_an_outline_the_box_is_used(self):
        users = {"u": {"name": "Ufuk", "color": [0.1, 0.2, 0.3]}}
        content = overlay.build(users, {}, {"n": "u"}, "me", None,
                                lambda uuid: ((0, 0, 0), ((1, 0, 0), (0, 1, 0), (0, 0, 1)), (0, 0, 0), (50, 50, 50)))
        self.assertEqual(len(content.lines), 12)


class BuildTest(unittest.TestCase):
    @staticmethod
    def resolve(uuid):
        return {"cube": ((0, 0, 0), BoxTest.AXES, (0, 0, 0), (10, 10, 10))}.get(uuid)

    def test_other_users_camera_and_selection_are_drawn_in_their_colour(self):
        content = overlay.build(USERS, {"u2": {"cam": CAMERA}}, {"cube": "u2"}, "u1", (0, 0, -500), self.resolve)
        colors = set(color for _s, _e, color in content.lines)
        self.assertEqual(colors, {(0.25, 0.65, 0.95)})
        self.assertEqual(len(content.lines), 10 + 12)
        self.assertEqual(sorted(text for _p, text, _c in content.labels), ["Enjoy", "Enjoy"])

    def test_own_camera_and_own_locks_are_not_drawn(self):
        content = overlay.build(USERS, {"u1": {"cam": CAMERA}}, {"cube": "u1"}, "u1", None, self.resolve)
        self.assertEqual((content.lines, content.labels), ([], []))

    def test_two_users_stay_distinguishable(self):
        users = dict(USERS, u3={"name": "Ayşe", "color": [0.4, 0.8, 0.35]})
        content = overlay.build(users, {"u2": {"cam": CAMERA}, "u3": {"cam": CAMERA[:3] + CAMERA[3:]}},
                                {"cube": "u3"}, "u1", None, self.resolve)
        by_name = dict((text, color) for _p, text, color in content.labels)
        self.assertEqual(by_name["Enjoy"], (0.25, 0.65, 0.95))
        self.assertEqual(by_name["Ayşe"], (0.4, 0.8, 0.35))

    def test_missing_or_hostile_data_never_raises(self):
        weird_users = {"u2": {"name": None, "color": "red"}, "u9": {}, "u4": None and {}}
        content = overlay.build(dict(USERS, **weird_users), {"u2": {"cam": "nope"}, "ghost": {"cam": CAMERA}},
                                {"missing": "u2", "cube": "unknown-user"}, "u1", None, self.resolve)
        self.assertEqual(content.lines, [])

    def test_locked_object_that_is_gone_is_skipped(self):
        content = overlay.build(USERS, {}, {"deleted": "u2"}, "u1", None, self.resolve)
        self.assertEqual(content.lines, [])


if __name__ == "__main__":
    unittest.main()
