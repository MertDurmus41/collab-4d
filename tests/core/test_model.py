from __future__ import absolute_import, division, print_function, unicode_literals

import unittest

import _path  # noqa: F401
from c4d_collab_lib.core import diff
from c4d_collab_lib.core.locks import LockTable
from c4d_collab_lib.core.model import OpError, SceneState


def create(uuid, parent=None, prev=None, kind="object", data=None):
    return {"op": "create", "uuid": uuid, "kind": kind, "ctype": 5100, "parent": parent,
            "prev": prev, "name": uuid, "data": data or {}}


def order(scene, parent=None):
    return [n.uuid for n in scene.children(parent)]


class DiffTest(unittest.TestCase):
    def test_roundtrip(self):
        old = {"a": 1, "b": {"x": 1.0, "y": [1, 2]}, "gone": True}
        new = {"a": 2, "b": {"x": 1.0, "y": [1, 3], "z": "new"}, "added": None}
        patch = diff.diff(old, new)
        self.assertTrue(diff.is_valid_patch(patch))
        self.assertEqual(diff.apply_patch(dict(old, b=dict(old["b"])), patch), new)

    def test_no_change_and_epsilon(self):
        self.assertIsNone(diff.diff({"a": 1.0, "b": {"c": 2}}, {"a": 1.0 + 1e-9, "b": {"c": 2}}))
        self.assertIsNotNone(diff.diff({"a": 1.0}, {"a": 1.01}))

    def test_bool_is_not_number(self):
        self.assertIsNotNone(diff.diff({"a": 1}, {"a": True}))

    def test_invalid_patch(self):
        self.assertFalse(diff.is_valid_patch({"set": []}))
        self.assertFalse(diff.is_valid_patch({"evil": 1}))
        self.assertFalse(diff.is_valid_patch({"sub": {"a": {"set": 1}}}))


class SceneTest(unittest.TestCase):
    def setUp(self):
        self.scene = SceneState()
        for op in [create("A"), create("B", prev="A"), create("C", prev="B"), create("A1", parent="A")]:
            self.scene.apply(op)

    def test_order(self):
        self.assertEqual(order(self.scene), ["A", "B", "C"])
        self.assertEqual(order(self.scene, "A"), ["A1"])

    def test_insert_in_middle(self):
        self.scene.apply(create("X", prev="A"))
        self.assertEqual(order(self.scene), ["A", "X", "B", "C"])
        self.scene.apply(create("F"))
        self.assertEqual(order(self.scene), ["F", "A", "X", "B", "C"])

    def test_move_and_delete(self):
        self.scene.apply({"op": "move", "uuid": "B", "parent": "A", "prev": "A1"})
        self.assertEqual(order(self.scene), ["A", "C"])
        self.assertEqual(order(self.scene, "A"), ["A1", "B"])
        self.scene.apply({"op": "delete", "uuid": "A"})
        self.assertEqual(order(self.scene), ["C"])
        self.assertEqual(len(self.scene), 1)

    def test_invalid_ops_do_not_change_scene(self):
        bad = [
            create("A"),                                            # duplicate
            create("Z", parent="missing"),                          # unknown parent
            create("Z", prev="A1"),                                 # prev not a sibling
            {"op": "move", "uuid": "A", "parent": "A1", "prev": None},  # under own child
            {"op": "update", "uuid": "missing"},
            {"op": "update", "uuid": "A", "patch": {"set": 3}},
            {"op": "explode", "uuid": "A"},
            {"op": "create", "uuid": "", "kind": "object", "ctype": 1},
            {"op": "create", "uuid": "Q", "kind": "alien", "ctype": 1},
            "not a dict",
        ]
        before = self.scene.snapshot_ops()
        for op in bad:
            with self.assertRaises(OpError):
                self.scene.apply(op)
        self.assertEqual(self.scene.snapshot_ops(), before)

    def test_update(self):
        self.scene.apply({"op": "update", "uuid": "A", "name": "Renamed",
                          "patch": diff.diff({}, {"pos": [1, 2, 3]})})
        self.assertEqual(self.scene.get("A").name, "Renamed")
        self.assertEqual(self.scene.get("A").data, {"pos": [1, 2, 3]})

    def test_tags_and_children_have_separate_chains(self):
        self.scene.apply(create("T1", parent="A", kind="tag"))
        self.scene.apply(create("T2", parent="A", prev="T1", kind="tag"))
        self.scene.apply(create("A2", parent="A", prev="A1"))
        self.assertEqual(order(self.scene, "A"), ["A1", "A2", "T1", "T2"])
        self.assertEqual([n.uuid for n in self.scene.children("A", "tag")], ["T1", "T2"])
        with self.assertRaises(OpError):  # a tag cannot follow an object
            self.scene.apply(create("T3", parent="A", prev="A2", kind="tag"))
        self.scene.apply({"op": "move", "uuid": "T2", "parent": "A", "prev": None})
        self.assertEqual([n.uuid for n in self.scene.children("A", "tag")], ["T2", "T1"])

    def test_move_within_same_parent_and_to_end(self):
        self.scene.apply({"op": "move", "uuid": "A", "parent": None, "prev": "C"})
        self.assertEqual(order(self.scene), ["B", "C", "A"])
        self.scene.apply({"op": "move", "uuid": "C", "parent": None, "prev": None})
        self.assertEqual(order(self.scene), ["C", "B", "A"])
        self.scene.apply({"op": "move", "uuid": "B", "parent": None, "prev": "C"})  # no-op move
        self.assertEqual(order(self.scene), ["C", "B", "A"])

    def test_delete_subtree_keeps_indexes_consistent(self):
        self.scene.apply(create("A1a", parent="A1"))
        self.scene.apply(create("TA", parent="A1", kind="tag"))
        self.scene.apply({"op": "delete", "uuid": "A1"})
        self.assertEqual(sorted(self.scene.nodes), ["A", "B", "C"])
        self.scene.apply(create("A1", parent="A"))  # uuid can be reused after delete
        self.assertEqual(order(self.scene, "A"), ["A1"])
        self.assertEqual(order(self.scene, "A1"), [])

    def test_large_scene_is_fast(self):
        import time
        scene = SceneState()
        start = time.time()
        prev = None
        for i in range(10000):
            uuid = "n%05d" % i
            scene.apply(create(uuid, prev=prev))
            prev = uuid
        ops = scene.snapshot_ops()
        copy = SceneState()
        copy.load_snapshot(ops)
        scene.apply({"op": "delete", "uuid": "n05000"})
        elapsed = time.time() - start
        self.assertEqual(len(copy), 10000)
        self.assertEqual(len(scene), 9999)
        self.assertLess(elapsed, 5.0, "10k-node scene took %.2fs" % elapsed)

    def test_snapshot_roundtrip(self):
        copy = SceneState()
        copy.load_snapshot(self.scene.snapshot_ops(), 7)
        self.assertEqual(copy.snapshot_ops(), self.scene.snapshot_ops())
        self.assertEqual(copy.seq, 7)


class LockTest(unittest.TestCase):
    def setUp(self):
        self.scene = SceneState()
        for op in [create("A"), create("A1", parent="A"), create("T", parent="A1", kind="tag"), create("B")]:
            self.scene.apply(op)
        self.locks = LockTable()

    def test_lock_covers_descendants(self):
        granted, denied = self.locks.try_lock(self.scene, "u1", ["A"])
        self.assertEqual((granted, denied), (["A"], []))
        self.assertFalse(self.locks.can_edit(self.scene, "u2", "T"))
        self.assertTrue(self.locks.can_edit(self.scene, "u1", "T"))
        self.assertTrue(self.locks.can_edit(self.scene, "u2", "B"))

    def test_conflicting_locks(self):
        self.locks.try_lock(self.scene, "u1", ["A1"])
        self.assertEqual(self.locks.try_lock(self.scene, "u2", ["A", "B"]), (["B"], ["A"]))
        self.assertFalse(self.locks.can_edit_subtree(self.scene, "u2", "A"))
        self.locks.release_all("u1")
        self.assertEqual(self.locks.try_lock(self.scene, "u2", ["A"]), (["A"], []))

    def test_release_only_own(self):
        self.locks.try_lock(self.scene, "u1", ["A"])
        self.assertEqual(self.locks.release("u2", ["A"]), [])
        self.assertEqual(self.locks.release("u1", ["A"]), ["A"])


if __name__ == "__main__":
    unittest.main()
