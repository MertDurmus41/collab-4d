# -*- coding: utf-8 -*-
"""Facial shapes: the Pose Morph tag (point morphs and strength sliders). Python 2.7 and 3.x."""
from __future__ import absolute_import, division, print_function, unicode_literals

import time
import unittest

import fake_scene as fs
from test_sync import SyncTestCase

SIZE = 20  # 21 x 21 = 441 points: four 128-point chunks


def grid(doc, name="Face"):
    mesh = fs.PolygonObject()
    mesh.SetName(fs.native(name))
    count = (SIZE + 1) * (SIZE + 1)
    mesh.ResizeObject(count, 1)
    mesh.SetAllPoints([fs.Vector(i % (SIZE + 1), i // (SIZE + 1), 0) for i in range(count)])
    mesh.SetPolygon(0, fs.CPolygon(0, 1, SIZE + 2, SIZE + 1))
    doc.InsertObject(mesh)
    return mesh


def add_morph(tag, name, offsets):
    """offsets: {point index: Vector}"""
    doc = tag.GetDocument()
    morph = tag.AddMorph()
    morph.Store(doc, tag, fs.CAMORPH_DATA_FLAGS_POINTS)
    morph.SetName(fs.native(name))
    morph.SetMode(doc, tag, fs.CAMORPH_MODE_FLAGS_ALL | fs.CAMORPH_MODE_FLAGS_EXPAND, fs.CAMORPH_MODE_REL)
    node = morph.GetFirst()
    for index, value in offsets.items():
        node.SetPoint(index, value)
    morph.SetMode(doc, tag, fs.CAMORPH_MODE_FLAGS_ALL | fs.CAMORPH_MODE_FLAGS_COLLAPSE, fs.CAMORPH_MODE_AUTO)
    return morph


class PoseMorphCase(SyncTestCase):
    def setUp(self):
        SyncTestCase.setUp(self)
        from c4d_collab_lib.c4d_adapter import tracker
        self._interval = tracker.TRACK_SCAN_INTERVAL
        tracker.TRACK_SCAN_INTERVAL = 0.03
        self.tracker_module = tracker

    def tearDown(self):
        self.tracker_module.TRACK_SCAN_INTERVAL = self._interval
        SyncTestCase.tearDown(self)

    def scene(self):
        doc = fs.BaseDocument()
        mesh = grid(doc)
        tag = mesh.MakeTag(fs.Tposemorph)
        tag.SetName(fs.native("Pose Morph"))
        tag[fs.ID_CA_POSE_POINTS] = True
        tag.AddMorph().Store(doc, tag, fs.CAMORPH_DATA_FLAGS_POINTS)  # base pose
        add_morph(tag, "smile", {5: fs.Vector(0, 0, 50), 300: fs.Vector(0, 2, 0)})
        add_morph(tag, "blink", {440: fs.Vector(1, 0, 0)})
        tag[fs.ID_CA_POSE_MODE] = fs.ID_CA_POSE_MODE_ANIMATE
        tag.SetParameter(tag.GetMorphID(1), 0.5, 0)
        return doc

    def tag_of(self, instance):
        return self.find(instance.doc, "Face").GetFirstTag()

    def wait_for(self, check, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline and not check():
            time.sleep(0.04)
            self.settle()
        return check()


class PoseMorphTest(PoseMorphCase):
    def test_shapes_and_strengths_arrive(self):
        a = self.host(self.scene())
        b = self.join()
        tag_b = self.tag_of(b)
        self.assertEqual(tag_b.GetType(), fs.Tposemorph)
        self.assertEqual([tag_b.GetMorph(i).GetName() for i in range(tag_b.GetMorphCount())],
                         ["Base Pose", "smile", "blink"])
        self.assertEqual(tag_b[fs.ID_CA_POSE_MODE], fs.ID_CA_POSE_MODE_ANIMATE)
        self.assertEqual(tag_b.deformed(), self.tag_of(a).deformed(), "the face looks the same")
        self.assertEqual(tag_b.deformed()[5], fs.Vector(5, 0, 25))
        self.assertEqual(self.find(b.doc, "Face").GetPoint(5), fs.Vector(5, 0, 25), "the guest's tag animates its mesh")
        self.assertEqual(tag_b._base()[5], fs.Vector(5, 0, 0), "and keeps the real base")
        self.assert_no_echo()

    def test_a_morph_moved_in_the_list_keeps_its_number(self):
        # Sliders, tracks and XPresso ports name a morph by its number, not its place in the list. Built in list
        # order on the guest, a leg's drivers moved the wrong shapes ("bend" got the value of "foot front").
        doc = self.scene()
        tag = self.find(doc, "Face").GetFirstTag()
        add_morph(tag, "wink", {7: fs.Vector(0, 3, 0)})
        tag.RemoveMorph(1)                                   # "smile" leaves number 1 free...
        add_morph(tag, "smile", {5: fs.Vector(0, 0, 50)})    # ...and takes it again, now at the end of the list
        numbers = [(tag.GetMorphID(i)[1].id - 1001) // 100 for i in range(tag.GetMorphCount())]
        self.assertEqual(numbers, [0, 2, 3, 1])
        smile_id = tag.GetMorphID(3)
        tag.SetParameter(smile_id, 0.8, 0)
        a = self.host(doc)
        b = self.join()
        tag_b = self.tag_of(b)
        self.assertEqual([tag_b.GetMorph(i).GetName() for i in range(tag_b.GetMorphCount())],
                         ["Base Pose", "blink", "wink", "smile"])
        self.assertEqual([(tag_b.GetMorphID(i)[1].id - 1001) // 100 for i in range(tag_b.GetMorphCount())], numbers)
        self.assertAlmostEqual(tag_b.GetParameter(smile_id, 0), 0.8, places=5)
        self.assertEqual(tag_b.deformed(), self.tag_of(a).deformed())
        self.assert_no_echo()

    def test_the_shared_mesh_is_the_base_not_the_animated_face(self):
        a = self.host(self.scene())
        self.assertEqual(self.find(a.doc, "Face").GetPoint(5), fs.Vector(5, 0, 25), "the host's mesh shows the smile")
        node = a.session.mirror.scene.get(a.sync.registry.uuid_of(self.find(a.doc, "Face")))
        from c4d_collab_lib.core import protocol
        self.assertEqual(protocol.unpack_floats(node.data["g"]["pts"]["0"])[15:18], [5.0, 0.0, 0.0])
        b = self.join()
        tag_a = self.tag_of(a)
        sent = []
        original = a.session.send_ops
        a.session.send_ops = lambda ops: (sent.extend(ops), original(ops))[1]
        for value in (0.1, 0.9, 0.4):  # XPresso moving a slider re-shapes the mesh every time
            self.edit(a, lambda: tag_a.SetParameter(tag_a.GetMorphID(1), value, 0))
        geometry_ops = [op for op in sent if "g" in (op.get("patch") or {}).get("sub", {})
                        or "g" in (op.get("patch") or {}).get("set", {})]
        self.assertTrue(sent, "the slider moves were sent")
        self.assertEqual(geometry_ops, [], "slider moves never send the face as geometry")
        self.assertTrue(self.wait_for(lambda: self.tag_of(b).GetParameter(self.tag_of(b).GetMorphID(1), 0) == 0.4))

    def test_a_base_edit_in_edit_mode_reaches_the_guest_tag(self):
        a = self.host(self.scene())
        b = self.join()
        tag_a, mesh_a = self.tag_of(a), self.find(a.doc, "Face")

        def sculpt_base():
            tag_a[fs.ID_CA_POSE_MODE] = fs.ID_CA_POSE_MODE_EDIT
            tag_a.SetActiveMorphIndex(0)
            mesh_a.SetPoint(5, fs.Vector(5, 0, -10))
        self.edit(a, sculpt_base)

        def leave():
            tag_a.SetActiveMorphIndex(-1)  # stores the base
            tag_a[fs.ID_CA_POSE_MODE] = fs.ID_CA_POSE_MODE_ANIMATE
        self.edit(a, leave)
        tag_b = self.tag_of(b)
        self.assertTrue(self.wait_for(lambda: tag_b._base()[5] == fs.Vector(5, 0, -10)))
        self.assertEqual(self.find(b.doc, "Face").GetPoint(5), fs.Vector(5, 0, 15), "new base + half a smile")
        self.assert_no_echo()

    def test_only_moved_chunks_travel(self):
        a = self.host(self.scene())
        self.join()
        node = a.session.mirror.scene.get(a.sync.registry.uuid_of(self.tag_of(a)))
        smile, blink = node.data["pm"]["m"][1], node.data["pm"]["m"][2]
        self.assertEqual(sorted(smile["d"]), ["0", "2"], "points 5 and 300; chunks 1 and 3 are all zero")
        self.assertEqual(sorted(blink["d"]), ["3"])
        self.assertNotIn("d", node.data["pm"]["m"][0], "the base pose carries no points")

    def test_slider_moves_travel_both_ways(self):
        a = self.host(self.scene())
        b = self.join()
        tag_a, tag_b = self.tag_of(a), self.tag_of(b)
        self.edit(a, lambda: tag_a.SetParameter(tag_a.GetMorphID(2), 1.0, 0))
        self.assertTrue(self.wait_for(lambda: tag_b.GetParameter(tag_b.GetMorphID(2), 0) == 1.0))
        self.edit(b, lambda: tag_b.SetParameter(tag_b.GetMorphID(1), 0.25, 0))
        self.assertTrue(self.wait_for(lambda: tag_a.GetParameter(tag_a.GetMorphID(1), 0) == 0.25))
        self.assertEqual(tag_a.deformed(), tag_b.deformed())
        self.assert_no_echo()

    def test_sculpting_a_pose_sends_the_pose_not_the_mesh(self):
        a = self.host(self.scene())
        b = self.join()
        tag_a, mesh_a = self.tag_of(a), self.find(a.doc, "Face")
        tag_b = self.tag_of(b)
        base_b = tag_b._base()

        def enter_and_sculpt():
            tag_a[fs.ID_CA_POSE_MODE] = fs.ID_CA_POSE_MODE_EDIT
            tag_a.SetActiveMorphIndex(1)  # the mesh now shows "smile"
            mesh_a.SetPoint(10, mesh_a.GetPoint(10) + fs.Vector(0, 0, 7))
        self.edit(a, enter_and_sculpt)
        self.assertEqual(tag_b._base(), base_b, "the sculpted pose did not travel as the base mesh")

        def leave():
            tag_a.SetActiveMorphIndex(0)  # stores the sculpt into "smile"
            tag_a[fs.ID_CA_POSE_MODE] = fs.ID_CA_POSE_MODE_ANIMATE
        self.edit(a, leave)
        self.assertTrue(self.wait_for(lambda: tag_b.GetMorph(1)._offsets[10] == fs.Vector(0, 0, 7)))
        self.assertEqual(tag_b.GetMorph(1)._offsets[5], fs.Vector(0, 0, 50), "the rest of the pose is kept")
        self.assertEqual(tag_b._base(), base_b)
        self.assertEqual(self.find(b.doc, "Face").GetAllPoints(), tag_b.deformed())
        self.assertEqual(tag_b[fs.ID_CA_POSE_MODE], fs.ID_CA_POSE_MODE_ANIMATE)
        self.assert_no_echo()

    def test_an_animated_slider_is_left_to_its_track(self):
        doc = self.scene()
        tag = doc.SearchObject("Face").GetFirstTag()
        track = fs.CTrack(tag, tag.GetMorphID(2))
        tag.InsertTrackSorted(track)
        a = self.host(doc)
        self.join()
        node = a.session.mirror.scene.get(a.sync.registry.uuid_of(self.tag_of(a)))
        self.assertEqual(node.data["pm"]["s"][1], 0.5)
        self.assertIsNone(node.data["pm"]["s"][2], "driven by a track: the receiver keeps its own value")

    def test_edit_mode_stays_local(self):
        a = self.host(self.scene())
        b = self.join()
        tag_a = self.tag_of(a)
        self.edit(a, lambda: tag_a.__setitem__(fs.ID_CA_POSE_MODE, fs.ID_CA_POSE_MODE_EDIT))
        self.settle()
        self.assertEqual(self.tag_of(b)[fs.ID_CA_POSE_MODE], fs.ID_CA_POSE_MODE_ANIMATE)

    def test_new_and_renamed_morphs_rebuild_the_tag(self):
        a = self.host(self.scene())
        b = self.join()
        tag_a = self.tag_of(a)

        def change():
            tag_a.GetMorph(2).SetName(fs.native("wink"))
            add_morph(tag_a, "frown", {0: fs.Vector(0, -3, 0)})
        self.edit(a, change)
        tag_b = self.tag_of(b)
        self.assertTrue(self.wait_for(lambda: tag_b.GetMorphCount() == 4))
        self.assertEqual([tag_b.GetMorph(i).GetName() for i in range(4)], ["Base Pose", "smile", "wink", "frown"])
        self.assertEqual(tag_b.GetMorph(3)._offsets[0], fs.Vector(0, -3, 0))
        self.assertEqual(tag_b.GetMorph(1)._offsets[5], fs.Vector(0, 0, 50), "rebuilt morphs keep their shapes")
        self.assertEqual(tag_b.GetParameter(tag_b.GetMorphID(1), 0), 0.5)
        self.assert_no_echo()

    def test_a_mesh_of_another_size_skips_points_without_echo(self):
        a = self.host(self.scene())
        b = self.join()
        mesh_b = self.find(b.doc, "Face")
        tag_b = self.tag_of(b)
        before = list(tag_b.GetMorph(1)._offsets)
        from c4d_collab_lib.core import diff, protocol
        uuid = b.sync.registry.uuid_of(tag_b)
        old = b.sync.shadow.get(uuid).data
        new = dict(old, pm=dict(old["pm"]))
        new["pm"]["n"] = mesh_b.GetPointCount() + 1  # sent from a mesh with another point count
        new["pm"]["m"] = [old["pm"]["m"][0], {"n": "smile", "d": {"0": protocol.pack_floats([9.0] * 384)}},
                          old["pm"]["m"][2]]
        b.activate()
        b.sync.applier.apply(b.doc, [{"op": "update", "uuid": uuid, "patch": diff.diff(old, new)}])
        self.assertEqual(tag_b.GetMorph(1)._offsets, before, "no points written")
        seq = a.session.server.scene.seq
        for _ in range(3):
            b.pump()
            time.sleep(0.05)
        self.assertEqual(a.session.server.scene.seq, seq, "the unwritten shapes are not reported back")


class LockedPoseMorphTest(PoseMorphCase):
    def test_rig_moving_sliders_of_a_locked_face_is_not_undone(self):
        a = self.host(self.scene())
        b = self.join()
        self.find(b.doc, "Face").SetBit(fs.BIT_ACTIVE)  # the guest holds the face
        self.settle()
        tag_a = self.tag_of(a)
        undone = []
        original = a.sync._undo_forbidden_changes
        a.sync._undo_forbidden_changes = lambda: (undone.append(set(a.sync.tracker.restore)), original())[1]
        for value in (0.1, 0.7, 0.3):  # XPresso drives the slider on every evaluation
            tag_a.SetParameter(tag_a.GetMorphID(1), value, 0)
            self.settle()
        self.assertFalse(any(undone), "a rig moving sliders is not an edit to undo")
        self.assertEqual(tag_a.GetParameter(tag_a.GetMorphID(1), 0), 0.3)

    def test_renaming_a_morph_of_a_locked_face_is_undone(self):
        a = self.host(self.scene())
        b = self.join()
        self.find(b.doc, "Face").SetBit(fs.BIT_ACTIVE)
        self.settle()
        tag_a = self.tag_of(a)

        def rename():
            tag_a.GetMorph(1).SetName(fs.native("hacked"))
            tag_a._touch("DATA")
        self.edit(a, rename)
        self.assertTrue(self.wait_for(lambda: tag_a.GetMorph(1).GetName() == "smile"), "put back")


class ReportTest(PoseMorphCase):
    def test_pose_morph_is_no_longer_reported_as_local(self):
        host = self.host(self.scene())
        self.assertNotIn("Pose Morph", host.sync.tracker.unsynced_report() or "")


if __name__ == "__main__":
    unittest.main()
