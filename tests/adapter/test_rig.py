# -*- coding: utf-8 -*-
"""Character rigs: joints, skin weights (Weight tag) and the report of what stays local.
Python 2.7 and 3.x."""
from __future__ import absolute_import, division, print_function, unicode_literals

import time
import unittest

import fake_scene as fs
from test_sync import SyncTestCase


def set_weights(tag, index, values):
    """What a user of any version can do: the whole map (R21+) or one weight at a time (R17-R20)."""
    if hasattr(tag, "SetWeightMap"):
        tag.SetWeightMap(index, values)
    else:
        for point, value in enumerate(values):
            tag.SetWeight(index, point, value)


class RigTest(SyncTestCase):
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
        mesh = fs.PolygonObject()
        mesh.SetName(fs.native("Body"))
        mesh.ResizeObject(4, 1)
        mesh.SetAllPoints([fs.Vector(0, 0, 0), fs.Vector(100, 0, 0), fs.Vector(100, 100, 0), fs.Vector(0, 100, 0)])
        mesh.SetPolygon(0, fs.CPolygon(0, 1, 2, 3))
        doc.InsertObject(mesh)
        root = fs.BaseObject(fs.Ojoint)
        root.SetName(fs.native("Hips"))
        doc.InsertObject(root)
        child = fs.BaseObject(fs.Ojoint)  # one level deeper than the mesh's tag: created after it
        child.SetName(fs.native("Spine"))
        doc.InsertObject(child, root)
        tag = mesh.MakeTag(fs.Tweights)
        tag.SetName(fs.native("Weight"))
        tag.AddJoint(root)
        tag.AddJoint(child)
        tag.SetJointRestState(1, fs.Matrix(fs.Vector(0, 50, 0)), fs.Matrix(fs.Vector(0, -50, 0)), fs.Matrix(), fs.Matrix(), 42.0)
        set_weights(tag, 0, [1.0, 0.5, 0.25, 0.0])
        set_weights(tag, 1, [0.0, 0.5, 0.75, 1.0])
        return doc

    def weights_of(self, instance):
        return self.find(instance.doc, "Body").GetFirstTag()

    def test_skin_weights_arrive_with_their_joints(self):
        self.host(self.scene())
        guest = self.join()
        tag = self.weights_of(guest)
        self.assertEqual(tag.GetType(), fs.Tweights)
        self.assertEqual([j.GetName() for j in (tag.GetJoint(i, guest.doc) for i in range(tag.GetJointCount()))],
                         ["Hips", "Spine"])
        self.assertAlmostEqual(tag.GetWeight(0, 1), 0.5, places=4)
        self.assertAlmostEqual(tag.GetWeight(1, 2), 0.75, places=4)
        state = tag.GetJointRestState(1)
        self.assertEqual(state["m_bMg"].off, fs.Vector(0, 50, 0))
        self.assertEqual(state["m_Len"], 42.0)
        self.assert_no_echo()

    def test_the_binding_of_the_mesh_arrives(self):
        # Without it the guest binds the mesh where it stands and the Skin moves every point by the difference
        # (a real rig: 17 m, arms stretched across the scene).
        doc = self.scene()
        bound = fs.Matrix(fs.Vector(37.5, 112.5, 0))
        self.find(doc, "Body").GetFirstTag().SetGeomMg(bound)
        self.host(doc)
        guest = self.join()
        self.assertEqual(self.weights_of(guest).GetGeomMg().off, fs.Vector(37.5, 112.5, 0))
        self.assert_no_echo()

    def test_a_weight_edit_travels(self):
        a = self.host(self.scene())
        b = self.join()
        tag_a = self.weights_of(a)

        def paint():
            set_weights(tag_a, 0, [0.1, 0.2, 0.3, 0.4])
            tag_a._touch("DATA")
        self.edit(a, paint)
        deadline = time.time() + 3
        while time.time() < deadline and abs(self.weights_of(b).GetWeight(0, 0) - 0.1) > 1e-3:
            time.sleep(0.05)
            self.settle()
        self.assertAlmostEqual(self.weights_of(b).GetWeight(0, 3), 0.4, places=4)

    def test_unshared_joints_are_left_out_and_nothing_breaks(self):
        doc = self.scene()
        code_joint = fs.BaseObject(fs.Opython)  # a code-carrying object: never shared
        doc.InsertObject(code_joint)
        self.weights_of_doc(doc).AddJoint(code_joint)
        self.host(doc)
        guest = self.join()
        self.assertEqual(self.weights_of(guest).GetJointCount(), 2)

    @staticmethod
    def weights_of_doc(doc):
        return doc.SearchObject("Body").GetFirstTag()

    def test_what_stays_local_is_reported(self):
        doc = self.scene()
        generator = fs.BaseObject(fs.Opython)
        generator.SetName(fs.native("PyGen"))
        doc.InsertObject(generator)
        child = fs.BaseObject(fs.Onull)
        doc.InsertObject(child, generator)
        doc.SearchObject("Body").MakeTag(fs.Tpython)
        host = self.host(doc)
        report = host.sync.tracker.unsynced_report()
        self.assertIn("2 x Python Generator (object)", report, "the object and the child under it")
        self.assertIn("1 x Python (tag)", report)


class EmptyMeshTest(SyncTestCase):
    def test_an_empty_polygon_object_arrives_without_error(self):
        doc = fs.BaseDocument()
        empty = fs.PolygonObject()
        empty.SetName(fs.native("Empty"))
        doc.InsertObject(empty)
        self.host(doc)
        guest = self.join()
        self.assertEqual(self.find(guest.doc, "Empty").GetPointCount(), 0)


if __name__ == "__main__":
    unittest.main()


class LockedRigTest(RigTest):
    """A rig that is evaluated moves counters of nodes someone else has locked without changing them."""

    def test_counter_noise_on_a_locked_node_is_not_undone(self):
        a = self.host(self.scene())
        b = self.join()
        tag_b = self.find(b.doc, "Body").GetFirstTag()
        body_b = self.find(b.doc, "Body")
        body_b.SetBit(fs.BIT_ACTIVE)  # the guest selects the mesh: it is locked for the host
        self.settle()
        tag_a = self.find(a.doc, "Body").GetFirstTag()
        undone = []
        original = a.sync._undo_forbidden_changes
        a.sync._undo_forbidden_changes = lambda: (undone.append(set(a.sync.tracker.restore)), original())[1]
        tag_a._touch("DATA")  # counter noise only
        self.find(a.doc, "Body")._touch("DATA")
        self.settle()
        self.assertFalse(any(undone), "nothing differed, so nothing was undone")

    def test_a_real_edit_of_a_locked_node_is_still_undone(self):
        a = self.host(self.scene())
        b = self.join()
        self.find(b.doc, "Body").SetBit(fs.BIT_ACTIVE)
        self.settle()
        body_a = self.find(a.doc, "Body")
        self.edit(a, lambda: body_a.SetName(fs.native("Hacked")))
        self.assertEqual(body_a.GetName(), "Body", "the edit was put back")
