# -*- coding: utf-8 -*-
"""Phase 4: selection locks, undoing forbidden local edits, reconciling refused ops.

Two fake Cinema 4D instances and a real server (Python 2.7 and 3.x).
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import time
import unittest

import fake_scene as fs
from test_sync import SyncTestCase


class LockTestCase(SyncTestCase):
    def setUp(self):
        SyncTestCase.setUp(self)
        self.a = self.host(self.build_scene())
        self.b = self.join()

    def select(self, instance, name, on=True):
        atom = self.find(instance.doc, name)
        self.edit(instance, lambda: atom.SetBit(fs.BIT_ACTIVE) if on else atom.DelBit(fs.BIT_ACTIVE))
        return atom

    def server_node(self, name):
        for node in self.a.session.server.scene.nodes.values():
            if node.name == name:
                return node
        return None

    def owner_of(self, name):
        return self.a.session.server.locks.owner(self.server_node(name).uuid)


class SelectionLockTest(LockTestCase):
    def test_selecting_locks_and_deselecting_releases(self):
        self.select(self.a, "Cube")
        self.assertEqual(self.owner_of("Cube"), self.a.session.mirror.user_id)
        self.assertFalse(self.b.session.mirror.editable(self.server_node("Cube").uuid))
        self.select(self.a, "Cube", on=False)
        self.assertIsNone(self.owner_of("Cube"))

    def test_other_user_cannot_select_a_locked_object(self):
        self.select(self.a, "Cube")
        cube_b = self.select(self.b, "Cube")
        self.assertFalse(cube_b.GetBit(fs.BIT_ACTIVE), "selection should have been taken away")
        self.assertTrue(any("being edited by Host" in t for t in self.c4d.status_texts))
        self.assertEqual(self.owner_of("Cube"), self.a.session.mirror.user_id)

    def test_disconnect_releases_locks(self):
        self.select(self.b, "Cube")
        self.assertEqual(self.owner_of("Cube"), self.b.session.mirror.user_id)
        self.b.session.leave()
        self.instances.remove(self.b)
        deadline = time.time() + 5
        while self.owner_of("Cube") is not None and time.time() < deadline:
            self.a.pump()
            time.sleep(0.01)
        self.assertIsNone(self.owner_of("Cube"))


class ForbiddenEditTest(LockTestCase):
    def setUp(self):
        LockTestCase.setUp(self)
        self.select(self.a, "Cube")
        self.seq = self.a.session.server.scene.seq

    def assert_nothing_sent_and_in_sync(self):
        self.assertEqual(self.a.session.server.scene.seq, self.seq, "a forbidden change reached the server")
        self.assert_trees_equal(self.a, self.b)

    def test_parameter_and_name_edit_is_undone(self):
        cube_b = self.find(self.b.doc, "Cube")

        def hack():
            cube_b[fs.PRIM_CUBE_SUBX] = 99
            cube_b.SetName(fs.native("Hacked"))
        self.edit(self.b, hack)
        cube_b = self.find(self.b.doc, "Cube")
        self.assertIsNotNone(cube_b, "name should be back")
        self.assertEqual(cube_b[fs.PRIM_CUBE_SUBX], 0)
        self.assertEqual(self.find(self.a.doc, "Cube")[fs.PRIM_CUBE_SUBX], 0)
        self.assertTrue(any("your change was undone" in text for text in self.c4d.status_texts), "said in the status bar")
        self.assertFalse(any("your change was undone" in line for line in self.b.session.log), "not in the Log panel")
        self.assert_nothing_sent_and_in_sync()

    def test_delete_is_undone_with_tags(self):
        self.edit(self.b, lambda: self.find(self.b.doc, "Cube").Remove())
        cube_b = self.find(self.b.doc, "Cube")
        self.assertIsNotNone(cube_b, "deleted locked object should come back")
        self.assertEqual(cube_b[fs.PRIM_CUBE_LEN], fs.Vector(300, 200, 100))
        self.assertEqual([t.GetName() for t in cube_b._tags], ["Phong"])
        self.assertEqual(cube_b.GetUp().GetName(), "Root")
        self.assert_nothing_sent_and_in_sync()

    def test_move_out_of_parent_is_undone(self):
        def move():
            cube = self.find(self.b.doc, "Cube")
            cube.Remove()
            self.b.doc.InsertObject(cube)
        self.edit(self.b, move)
        self.assertEqual(self.find(self.b.doc, "Cube").GetUp().GetName(), "Root")
        self.assert_nothing_sent_and_in_sync()

    def test_deleting_parent_of_locked_child_is_undone(self):
        self.edit(self.b, lambda: self.find(self.b.doc, "Root").Remove())
        self.assertIsNotNone(self.find(self.b.doc, "Root"))
        self.assertEqual(self.find(self.b.doc, "Cube").GetUp().GetName(), "Root")
        self.assert_nothing_sent_and_in_sync()

    def test_new_object_under_locked_parent_is_removed(self):
        def add_child():
            child = fs.BaseObject(fs.Onull)
            child.SetName(fs.native("Intruder"))
            self.b.doc.InsertObject(child, self.find(self.b.doc, "Cube"))
        self.edit(self.b, add_child)
        self.assertIsNone(self.find(self.b.doc, "Intruder"))
        self.assert_nothing_sent_and_in_sync()

    def test_tag_added_to_locked_object_is_removed(self):
        def add_tag():
            self.find(self.b.doc, "Cube").MakeTag(fs.Tphong).SetName(fs.native("ExtraTag"))
        self.edit(self.b, add_tag)
        self.assertEqual([t.GetName() for t in self.find(self.b.doc, "Cube")._tags], ["Phong"])
        self.assert_nothing_sent_and_in_sync()

    def test_unlocked_objects_still_sync(self):
        poly_b = self.find(self.b.doc, "Poly")
        self.edit(self.b, lambda: poly_b.SetPoint(1, fs.Vector(7, 7, 7)))
        self.assertEqual(self.find(self.a.doc, "Poly").GetAllPoints()[1], fs.Vector(7, 7, 7))

    def test_after_release_the_other_user_can_edit(self):
        self.select(self.a, "Cube", on=False)
        cube_b = self.find(self.b.doc, "Cube")
        self.edit(self.b, lambda: cube_b.__setitem__(fs.PRIM_CUBE_SUBX, 5))
        self.assertEqual(self.find(self.a.doc, "Cube")[fs.PRIM_CUBE_SUBX], 5)


class RaceTest(LockTestCase):
    def test_refused_changes_are_reconciled(self):
        """B edits before it has heard about A's lock: the server refuses, B rolls back."""
        root_a = self.find(self.a.doc, "Root")
        self.a.activate()
        root_a.SetBit(fs.BIT_ACTIVE)
        root_uuid = self.server_node("Root").uuid
        deadline = time.time() + 5
        while self.a.session.server.locks.owner(root_uuid) is None and time.time() < deadline:
            self.a.pump()  # only A talks; B's mirror still thinks Root is free
            time.sleep(0.01)
        self.assertIsNotNone(self.a.session.server.locks.owner(root_uuid))

        root_b = self.find(self.b.doc, "Root")
        self.b.activate()
        root_b[fs.REL_POSITION] = fs.Vector(9, 9, 9)
        child = fs.BaseObject(fs.Onull)
        child.SetName(fs.native("TooLate"))
        self.b.doc.InsertObject(child, root_b)
        self.assertGreater(self.b.sync.tick(), 0, "B should have sent its (doomed) changes")

        self.settle()
        self.assertEqual(self.find(self.b.doc, "Root")[fs.REL_POSITION], fs.Vector(0, 0, 0))
        self.assertIsNone(self.find(self.b.doc, "TooLate"))
        self.assertTrue(any("refused by the server were undone" in text for text in self.c4d.status_texts))
        self.assert_trees_equal(self.a, self.b)
        self.assert_no_echo()

    def test_both_select_same_object_converges(self):
        cube_a = self.find(self.a.doc, "Cube")
        cube_b = self.find(self.b.doc, "Cube")
        self.a.activate()
        cube_a.SetBit(fs.BIT_ACTIVE)
        self.b.activate()
        cube_b.SetBit(fs.BIT_ACTIVE)
        self.a.activate()   # each Cinema 4D has its own active document (they share one fake here)
        self.a.sync.tick()
        self.b.activate()
        self.b.sync.tick()
        self.settle()
        owner = self.owner_of("Cube")
        self.assertIsNotNone(owner)
        selected = [i for i in (self.a, self.b) if self.find(i.doc, "Cube").GetBit(fs.BIT_ACTIVE)]
        self.assertEqual(len(selected), 1, "exactly one user keeps the selection")
        self.assertEqual(selected[0].session.mirror.user_id, owner)


if __name__ == "__main__":
    unittest.main()
