# -*- coding: utf-8 -*-
"""What an XPresso graph writes is not an edit: the part of the tracker that leaves it out, and the data it uses.
Python 2.7 and 3.x."""
from __future__ import absolute_import, division, print_function, unicode_literals

import unittest

from test_sync import SyncTestCase


class DrivenTest(SyncTestCase):
    def setUp(self):
        SyncTestCase.setUp(self)
        import c4d
        from c4d_collab_lib.c4d_adapter import tracker
        from c4d_collab_lib.c4d_adapter.serializers import containers, xpresso
        self.c4d, self.tracker, self.containers, self.xpresso = c4d, tracker, containers, xpresso
        self._saved = dict((name, getattr(c4d, name, None)) for name in ("DTYPE_VECTOR", "DTYPE_COLOR", "VECTOR_X"))
        c4d.DTYPE_VECTOR, c4d.DTYPE_COLOR, c4d.VECTOR_X = 23, 24, 1000

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                if hasattr(self.c4d, name):
                    delattr(self.c4d, name)
            else:
                setattr(self.c4d, name, value)
        SyncTestCase.tearDown(self)

    def test_driven_parameters_are_named_by_their_key(self):
        data = {"dr": [["a", "903:23:5155/1001:19:0"], ["a", "903:23:5155/1002:19:0"], ["a", "700:5:0/34:15:0"],
                       ["b", "905:23:5155"], ["a", "700:5:0/37:19:0"]]}
        driven = self.xpresso.driven_of(data)
        self.assertEqual(driven["a"]["903:23:5155"], set([1, 2]))     # two components of a vector
        self.assertIsNone(driven["a"]["700:5:0/34:15:0"])             # a user data element: all of it
        self.assertIsNone(driven["b"]["905:23:5155"])
        self.assertEqual(sorted(driven["a"]), ["700:5:0/34:15:0", "700:5:0/37:19:0", "903:23:5155"])
        self.assertEqual(self.xpresso.driven_of(None), {})

    def test_driven_value_is_taken_from_what_the_session_has(self):
        old = {"p": {"903:23:5155": {"V": [1.0, 2.0, 3.0]}, "700:5:0/34:15:0": 0, "1:1:1": 5}}
        new = {"p": {"903:23:5155": {"V": [9.0, 8.0, 7.0]}, "700:5:0/34:15:0": 1, "1:1:1": 6}}
        modes = {"903:23:5155": set([1]), "700:5:0/34:15:0": None}
        masked = self.tracker._mask_driven(old, new, modes, False)
        self.assertEqual(masked["p"]["903:23:5155"], {"V": [9.0, 2.0, 7.0]})   # only the driven component stays
        self.assertEqual(masked["p"]["700:5:0/34:15:0"], 0)
        self.assertEqual(masked["p"]["1:1:1"], 6)                              # an edit of anything else stays
        self.assertEqual(new["p"]["700:5:0/34:15:0"], 1)                       # the input is not changed

    def test_nothing_driven_changes_nothing(self):
        new = {"p": {"a": 1}}
        self.assertIs(self.tracker._mask_driven({"p": {"a": 0}}, new, {}, False), new)

    def test_sliders_of_a_pose_morph_are_the_graph_result(self):
        old = {"pm": {"s": [0.0, 1.0], "n": ["a", "b"]}}
        new = {"pm": {"s": [0.5, 0.2], "n": ["a", "c"]}}
        masked = self.tracker._mask_driven(old, new, {"x": None}, True)
        self.assertEqual(masked["pm"]["s"], [0.0, 1.0])
        self.assertEqual(masked["pm"]["n"], ["a", "c"])    # a change of the shapes is an edit

    def test_graphs_are_compared_within_a_hair(self):
        a = {"n": [{"x": 1.0, "bc": {"1": 0.5}}], "c": [[0, 0, 1, 0]]}
        b = {"n": [{"x": 1.0 + 1e-9, "bc": {"1": 0.5}}], "c": [[0, 0, 1, 0]]}
        self.assertTrue(self.xpresso.same(a, b))
        b["c"] = [[0, 0, 1, 1]]
        self.assertFalse(self.xpresso.same(a, b))

    def test_camera_shake_is_not_an_edit_but_a_move_is(self):
        old = {"p": {"903:23:5155": {"V": [100.0, 300.0, 2000.0]}, "904:23:5155": {"V": [0.1, 0.0, 0.0]}}}
        shaken = {"p": {"903:23:5155": {"V": [100.3, 300.1, 2000.2]}, "904:23:5155": {"V": [0.11, 0.0, 0.0]}}}
        self.assertEqual(self.tracker._mask_shake(old, shaken)["p"], old["p"])
        moved = {"p": {"903:23:5155": {"V": [160.0, 300.0, 2000.0]}, "904:23:5155": {"V": [0.1, 0.0, 0.0]}}}
        self.assertEqual(self.tracker._mask_shake(old, moved)["p"]["903:23:5155"], {"V": [160.0, 300.0, 2000.0]})

    def test_a_large_shake_of_a_shaken_channel_is_not_an_edit_unless_the_object_is_selected(self):
        """Camera.1 of Fight_test11 shakes its scale with amplitude 2: 0.58 on the guest, 0.762 on the host, and the
        guest sent its scale on every join."""
        old = {"p": {"905:23:5155": {"V": [0.762, 0.762, 0.762]}, "903:23:5155": {"V": [100.0, 300.0, 2000.0]}}}
        shaken = {"p": {"905:23:5155": {"V": [0.58, 0.58, 0.58]}, "903:23:5155": {"V": [160.0, 300.0, 2000.0]}}}
        masked = self.tracker._mask_shake(old, shaken, shaken=set([905]), selected=False)["p"]
        self.assertEqual(masked["905:23:5155"], old["p"]["905:23:5155"])
        self.assertEqual(masked["903:23:5155"], {"V": [160.0, 300.0, 2000.0]}, "a channel the tag does not shake")
        picked = self.tracker._mask_shake(old, shaken, shaken=set([905]), selected=True)["p"]
        self.assertEqual(picked["905:23:5155"], {"V": [0.58, 0.58, 0.58]}, "a selected object is being edited")

    def test_what_another_version_reads_is_not_sent_but_a_change_of_it_is(self):
        """Live, R19 host and 2023 guest: on every join the guest sent the icon colour of every object, material and
        shader (parameters R19 has not) and the animated transforms of Plane.4, Plane.1, Camera and Mert as nulls."""
        from c4d_collab_lib.core.model import SceneState
        from c4d_collab_lib.c4d_adapter import ids
        tracker = self.tracker.Tracker(SceneState(), ids.Registry())
        icon = "1041671:3:110050"
        known = {"p": {"903:23:5155": {"V": [1.0, 2.0, 3.0]}, "1000:15:0": 1}}
        tracker._extras["u"] = {icon: {"V": [1.0, 0.9, 0.4]}}
        patch = {"sub": {"p": {"set": {icon: {"V": [1.0, 0.9, 0.4]}},
                               "sub": {"903:23:5155": {"set": {"V": [None, None, None]}}}}}}
        self.assertIsNone(tracker._drop_unsent("u", known, patch))
        changed = {"sub": {"p": {"set": {icon: {"V": [0.0, 0.0, 1.0]}}}}}
        self.assertEqual(tracker._drop_unsent("u", known, changed), {"sub": {"p": {"set": {icon: {"V": [0.0, 0.0, 1.0]}}}}})
        moved = {"sub": {"p": {"sub": {"903:23:5155": {"set": {"V": [None, 5.0, None]}}}}}}
        self.assertIsNotNone(tracker._drop_unsent("u", known, moved), "a real change of a component still goes out")
        added = {"sub": {"p": {"set": {"2000:15:0": 3}}}}   # a parameter nobody read when the data arrived (new user data)
        self.assertIsNotNone(tracker._drop_unsent("u", known, added))
        tracker._extras["u"] = {"1041670:400006001:110050": 0}   # read as a bool when the data arrived ...
        later = {"sub": {"p": {"set": {"1041670:15:110050": 0}}}}   # ... and as an integer once the object was drawn
        self.assertIsNone(tracker._drop_unsent("u", known, later))
        tracker.shadow.apply({"op": "create", "uuid": "o", "kind": "object", "ctype": 5140, "parent": None, "prev": None,
                              "name": "Auges", "data": {}})
        tracker.shadow.apply({"op": "create", "uuid": "vs", "kind": "tag", "ctype": 1026275, "parent": "o", "prev": None,
                              "name": "Visual Selector", "data": {"p": {}}})
        filled = {"sub": {"p": {"set": {"1003:19:1026275": 0.0, "1023:5:0/903:23:5155": {"V": [0.0, 0.0, 0.0]}}}}}
        self.assertIsNone(tracker._drop_unsent("vs", {"p": {}}, filled), "what a Visual Selector makes later")

    def test_code_nodes_are_not_standard_operators(self):
        safe = self.xpresso.SAFE_OPERATORS
        self.assertIn(400001000, safe)        # Object
        self.assertIn(400001136, safe)        # Range Mapper
        self.assertNotIn(400001154, safe)     # COFFEE
        self.assertNotIn(1022471, safe)       # Python
        self.assertNotIn(1000000, safe)       # anything a plugin brings


if __name__ == "__main__":
    unittest.main()
