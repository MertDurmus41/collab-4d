# -*- coding: utf-8 -*-
"""Phase 5: keyframes, tracks, document time settings and frame presence.

Two fake Cinema 4D instances and a real server (Python 2.7 and 3.x).
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import time
import unittest

import fake_scene as fs
from test_sync import SyncTestCase


def keys_of(track):
    curve = track.GetCurve()
    return [(round(k.GetTime().Get(), 4), k.GetValue()) for k in curve._keys]


def pos_x_track(obj):
    for track in obj._tracks:
        if track._desc.key() == (fs.REL_POSITION, fs.VECTOR_X):
            return track
    return None


class AnimationTestCase(SyncTestCase):
    def setUp(self):
        SyncTestCase.setUp(self)
        from c4d_collab_lib.c4d_adapter import tracker
        self.tracker_module = tracker
        self._interval = tracker.TRACK_SCAN_INTERVAL
        tracker.TRACK_SCAN_INTERVAL = 0.05  # make the periodic content scan quick for tests
        doc = self.build_scene()
        cube = doc.SearchObject("Cube")
        fs.animate(cube, fs.REL_POSITION, [(0, 0.0), (30, 100.0)], sub=fs.VECTOR_X)
        self.a = self.host(doc)
        self.b = self.join()

    def tearDown(self):
        self.tracker_module.TRACK_SCAN_INTERVAL = self._interval
        SyncTestCase.tearDown(self)

    def cube(self, instance):
        return self.find(instance.doc, "Cube")

    def settle_slow(self):
        """Give the periodic track scan a chance to run on every instance."""
        for _ in range(3):
            time.sleep(0.06)
            self.settle()


class KeyframeTest(AnimationTestCase):
    def test_animation_arrives_on_join(self):
        track = pos_x_track(self.cube(self.b))
        self.assertIsNotNone(track, "guest has the position X track")
        self.assertEqual(keys_of(track), [(0.0, 0.0), (1.0, 100.0)])
        # Y and Z are plain values. X is animated: its value comes from the track, except that this test set X by hand
        # (10.0, while the track gives 0.0 at the playhead), and a hand edit of a keyed value is sent (override_check).
        node = [n for n in self.a.session.server.scene.nodes.values() if n.name == "Cube"][0]
        position = [v for k, v in node.data["p"].items() if k.startswith("%d:" % fs.REL_POSITION)][0]
        self.assertEqual(position["V"][1:], [20.0, 30.0])
        self.assert_no_echo()

    def test_key_value_time_add_delete(self):
        curve = pos_x_track(self.cube(self.a)).GetCurve()
        self.edit(self.a, lambda: curve.GetKey(1).SetValue(curve, 250.0))
        self.assertEqual(keys_of(pos_x_track(self.cube(self.b)))[1], (1.0, 250.0))

        self.edit(self.a, lambda: curve.GetKey(1).SetTime(curve, fs.BaseTime(45, 30)))
        self.assertEqual(keys_of(pos_x_track(self.cube(self.b))), [(0.0, 0.0), (1.5, 250.0)])

        def add():
            curve.AddKey(fs.BaseTime(15, 30))["key"].SetValue(curve, 50.0)
        self.edit(self.a, add)
        self.assertEqual(len(keys_of(pos_x_track(self.cube(self.b)))), 3)

        self.edit(self.a, lambda: curve.DelKey(0))
        self.assertEqual(keys_of(pos_x_track(self.cube(self.b))), [(0.5, 50.0), (1.5, 250.0)])
        self.assert_no_echo()

    def test_interpolation_and_options(self):
        curve = pos_x_track(self.cube(self.a)).GetCurve()

        def change():
            key = curve.GetKey(0)
            key.SetInterpolation(curve, fs.CINTERPOLATION_STEP)
            key.options[919] = False  # auto tangents off
        self.edit(self.a, change)
        key_b = pos_x_track(self.cube(self.b)).GetCurve().GetKey(0)
        self.assertEqual(key_b.GetInterpolation(), fs.CINTERPOLATION_STEP)
        self.assertFalse(key_b.options[919])

    def test_tangent_edit_without_dirty_is_found_by_periodic_scan(self):
        curve = pos_x_track(self.cube(self.a)).GetCurve()
        self.a.activate()
        curve.GetKey(1).SetValueRight(curve, 42.0)  # moves no dirty counter, like real C4D
        self.settle_slow()
        self.assertEqual(pos_x_track(self.cube(self.b)).GetCurve().GetKey(1).GetValueRight(), 42.0)
        self.assert_no_echo()

    def test_track_settings(self):
        track = pos_x_track(self.cube(self.a))
        self.a.activate()
        track[fs.TRACK_AFTER] = 2  # e.g. "Repeat"
        self.settle_slow()
        self.assertEqual(pos_x_track(self.cube(self.b))[fs.TRACK_AFTER], 2)

    def test_guest_creates_and_deletes_a_track(self):
        cube_b = self.cube(self.b)
        self.edit(self.b, lambda: fs.animate(cube_b, fs.PRIM_CUBE_SUBX, [(0, 1.0), (10, 5.0)]))
        self.settle_slow()
        subx = [t for t in self.cube(self.a)._tracks if t._desc.key() == (fs.PRIM_CUBE_SUBX,)]
        self.assertEqual(len(subx), 1)
        self.assertEqual(keys_of(subx[0]), [(0.0, 1.0), (0.3333, 5.0)])

        self.edit(self.b, lambda: [t for t in cube_b._tracks if t._desc.key() == (fs.PRIM_CUBE_SUBX,)][0].Remove())
        self.settle_slow()
        self.assertEqual([t._desc.key() for t in self.cube(self.a)._tracks], [(fs.REL_POSITION, fs.VECTOR_X)])
        self.assert_no_echo()

    def test_static_components_of_an_animated_vector_still_sync(self):
        cube_a = self.cube(self.a)
        cube_b = self.cube(self.b)
        x_before = cube_b[fs.REL_POSITION].x
        self.edit(self.a, lambda: cube_a.__setitem__(fs.REL_POSITION, fs.Vector(cube_a[fs.REL_POSITION].x, -50, 7)))
        position = cube_b[fs.REL_POSITION]
        self.assertEqual((position.y, position.z), (-50.0, 7.0))
        self.assertEqual(position.x, x_before, "the animated X keeps the receiver's own value")

    def test_playback_values_are_not_sent(self):
        """Animation evaluation changes the animated parameter every frame; that must not be synced."""
        seq = self.a.session.server.scene.seq
        cube_a = self.cube(self.a)
        y, z = cube_a[fs.REL_POSITION].y, cube_a[fs.REL_POSITION].z
        for frame, value in ((1, 10.0), (2, 20.0), (3, 30.0)):  # only the animated X changes, and the playhead moves with it
            def play(frame=frame, value=value):
                self.a.doc.SetTime(fs.BaseTime(frame, 30))
                cube_a.__setitem__(fs.REL_POSITION, fs.Vector(value, y, z))
            self.edit(self.a, play)
        self.assertEqual(self.a.session.server.scene.seq, seq)

    def test_a_hand_edit_of_an_animated_value_is_sent(self):
        """Moving a keyed parameter by hand (no key, the playhead stays) is an edit: the other user sees it on any frame."""
        cube_a, cube_b = self.cube(self.a), self.cube(self.b)
        y, z = cube_a[fs.REL_POSITION].y, cube_a[fs.REL_POSITION].z
        self.edit(self.a, lambda: cube_a.__setitem__(fs.REL_POSITION, fs.Vector(55.0, y, z)))   # a first look: the baseline
        seq = self.a.session.server.scene.seq
        self.edit(self.a, lambda: cube_a.__setitem__(fs.REL_POSITION, fs.Vector(77.0, y, z)))
        self.assertGreater(self.a.session.server.scene.seq, seq)
        self.assertEqual(cube_b[fs.REL_POSITION].x, 77.0)


class DocumentAndPresenceTest(AnimationTestCase):
    def test_fps_and_time_range(self):
        def change():
            self.a.doc.SetFps(25)
            self.a.doc.SetMaxTime(fs.BaseTime(5.0))
            self.a.doc.SetLoopMaxTime(fs.BaseTime(4.0))
        self.edit(self.a, change)
        self.assertEqual(self.b.doc.GetFps(), 25)
        self.assertEqual(self.b.doc.GetMaxTime().Get(), 5.0)
        self.assertEqual(self.b.doc.GetLoopMaxTime().Get(), 4.0)
        self.assert_no_echo()

    def test_each_user_keeps_own_frame_and_sees_the_others(self):
        self.b.doc.SetTime(fs.BaseTime(12, 30))
        self.settle_slow()
        self.assertEqual(self.a.doc.GetTime().GetFrame(30), 0, "frames are not forced on others")
        guest_id = self.b.session.mirror.user_id
        self.assertEqual(self.a.session.mirror.presence[guest_id].get("frame"), 12)


class AnimationLockTest(AnimationTestCase):
    def test_editing_keys_of_a_locked_object_is_undone(self):
        cube_a = self.cube(self.a)
        self.edit(self.a, lambda: cube_a.SetBit(fs.BIT_ACTIVE))
        seq = self.a.session.server.scene.seq
        curve_b = pos_x_track(self.cube(self.b)).GetCurve()
        self.edit(self.b, lambda: curve_b.GetKey(1).SetValue(curve_b, -999.0))
        self.settle_slow()
        self.assertEqual(keys_of(pos_x_track(self.cube(self.b)))[1], (1.0, 100.0))
        self.assertEqual(keys_of(pos_x_track(cube_a))[1], (1.0, 100.0))
        self.assertEqual(self.a.session.server.scene.seq, seq)

    def test_new_track_on_locked_object_is_removed(self):
        cube_a = self.cube(self.a)
        self.edit(self.a, lambda: cube_a.SetBit(fs.BIT_ACTIVE))
        cube_b = self.cube(self.b)
        self.edit(self.b, lambda: fs.animate(cube_b, fs.PRIM_CUBE_SUBX, [(0, 2.0)]))
        self.settle_slow()
        self.assertEqual([t._desc.key() for t in self.cube(self.b)._tracks], [(fs.REL_POSITION, fs.VECTOR_X)])
        self.assertEqual(len(cube_a._tracks), 1)


if __name__ == "__main__":
    unittest.main()


class DocumentCreateOnGuestTest(SyncTestCase):
    def test_the_hosts_document_node_updates_the_one_the_guest_scanned_first(self):
        """The guest's own scan makes a document node before the scene arrives; the host's 'create document' then
        failed ('uuid already exists') and the layers (and so the hidden rig joints) never reached the guest."""
        from c4d_collab_lib.core.model import SceneState
        from c4d_collab_lib.c4d_adapter.applier import Applier
        from c4d_collab_lib.c4d_adapter.serializers import tracks
        shadow = SceneState()
        shadow.apply({"op": "create", "uuid": tracks.DOC_UUID, "kind": "doc", "ctype": 0, "parent": None,
                      "prev": None, "name": "", "data": {"fps": 30}})
        applier = Applier(shadow, None, None)
        op = applier._doc_create_as_update({"op": "create", "uuid": tracks.DOC_UUID, "kind": "doc", "ctype": 0,
                                            "parent": None, "prev": None, "name": "",
                                            "data": {"fps": 30, "ly": [{"u": "L1", "n": "JOINTS", "p": None,
                                                                        "d": {"view": False}}]}})
        self.assertEqual(op["op"], "update")
        shadow.apply(op)
        self.assertEqual(shadow.get(tracks.DOC_UUID).data["ly"][0]["n"], "JOINTS")
