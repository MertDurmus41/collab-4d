# -*- coding: utf-8 -*-
"""Phase 6: meshes, selection tags, UVW tags and splines.

Two fake Cinema 4D instances and a real server (Python 2.7 and 3.x).
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import time
import unittest

import fake_scene as fs
from test_sync import SyncTestCase

COLUMNS, ROWS = 20, 20  # 441 points, 400 polygons: several chunks of 128


def make_grid(columns=COLUMNS, rows=ROWS, name="Mesh"):
    mesh = fs.BaseObject(fs.Opolygon)
    mesh.SetName(fs.native(name))
    mesh.ResizeObject((columns + 1) * (rows + 1), columns * rows)
    for y in range(rows + 1):
        for x in range(columns + 1):
            mesh.SetPoint(y * (columns + 1) + x, fs.Vector(x * 10, y * 10, 0))
    for y in range(rows):
        for x in range(columns):
            a = y * (columns + 1) + x
            mesh.SetPolygon(y * columns + x, fs.CPolygon(a, a + 1, a + columns + 2, a + columns + 1))
    return mesh


def add_uvw(mesh, name="UVs"):
    tag = mesh.MakeTag(fs.Tuvw)
    tag.SetName(fs.native(name))
    mesh.ResizeObject(mesh.GetPointCount(), mesh.GetPolygonCount())  # sizes the tag, as in real C4D
    for index in range(tag.GetDataCount()):
        u, v = (index % 20) / 20.0, (index // 20) / 20.0
        tag.SetSlow(index, fs.Vector(u, v, 0), fs.Vector(u + 0.05, v, 0), fs.Vector(u + 0.05, v + 0.05, 0),
                    fs.Vector(u, v + 0.05, 0))
    return tag


def tag_named(obj, name):
    for tag in obj._tags:
        if tag.GetName() == name:
            return tag
    return None


def selected(tag, count):
    return [i for i in range(count) if tag.GetBaseSelect().IsSelected(i)]


def uv_values(tag):
    return [[(c.x, c.y) for c in (d["a"], d["b"], d["c"], d["d"])]
            for d in (tag.GetSlow(i) for i in range(tag.GetDataCount()))]


def make_spline(points=6):
    spline = fs.BaseObject(fs.Ospline)
    spline.SetName(fs.native("Curve"))
    spline.ResizeObject(points, 1)
    for i in range(points):
        spline.SetPoint(i, fs.Vector(i * 10, (i % 2) * 20, 0))
        spline.SetTangent(i, fs.Vector(-3, i, 0), fs.Vector(3, -i, 0))
    spline.SetSegment(0, points, False)
    return spline


class ModelingTestCase(SyncTestCase):
    def setUp(self):
        SyncTestCase.setUp(self)
        from c4d_collab_lib.c4d_adapter import compat, tracker
        self.compat, self.tracker_module = compat, tracker
        self._interval = tracker.TRACK_SCAN_INTERVAL
        tracker.TRACK_SCAN_INTERVAL = 0.05  # quick periodic content scan for tests
        doc = fs.BaseDocument()
        self.mesh_a = make_grid()
        doc.InsertObject(self.mesh_a)
        sel = self.mesh_a.MakeTag(fs.Tpolygonselection)
        sel.SetName(fs.native("PolySel"))
        for index in (0, 1, 2, 3, 40, 41, 399):
            sel.GetBaseSelect().Select(index)
        self.uv_a = add_uvw(self.mesh_a)
        self.spline_a = make_spline()
        doc.InsertObject(self.spline_a, None, self.mesh_a)
        self.a = self.host(doc)
        self.b = self.join()
        self.sent = []
        original = self.a.session.send_ops

        def spy(ops):
            self.sent.extend(ops)
            original(ops)
        self.a.session.send_ops = spy

    def tearDown(self):
        self.tracker_module.TRACK_SCAN_INTERVAL = self._interval
        SyncTestCase.tearDown(self)

    def mesh(self, instance):
        return self.find(instance.doc, "Mesh")

    def spline(self, instance):
        return self.find(instance.doc, "Curve")

    def settle_slow(self):
        for _ in range(3):
            time.sleep(0.06)
            self.settle()

    def updates_for(self, obj):
        uid = self.compat.read_uid(obj)
        return [op for op in self.sent if op["op"] == "update" and op["uuid"] == uid]

    def assert_meshes_equal(self):
        a, b = self.mesh(self.a), self.mesh(self.b)
        self.assertEqual(a.GetPointCount(), b.GetPointCount())
        self.assertEqual([(p.x, p.y, p.z) for p in a.GetAllPoints()], [(p.x, p.y, p.z) for p in b.GetAllPoints()])
        self.assertEqual([(q.a, q.b, q.c, q.d) for q in a.GetAllPolygons()],
                         [(q.a, q.b, q.c, q.d) for q in b.GetAllPolygons()])


class MeshTest(ModelingTestCase):
    def test_join_receives_mesh_selection_uvs_and_spline(self):
        self.assert_meshes_equal()
        mesh_b = self.mesh(self.b)
        self.assertEqual(selected(tag_named(mesh_b, "PolySel"), 400), [0, 1, 2, 3, 40, 41, 399])
        uv_b = tag_named(mesh_b, "UVs")
        self.assertEqual(uv_b.GetDataCount(), 400)
        self.assertEqual(uv_values(uv_b), uv_values(self.uv_a))
        spline_b = self.spline(self.b)
        self.assertEqual(spline_b.GetPointCount(), 6)
        self.assertEqual(spline_b.GetSegment(0), {"cnt": 6, "closed": False})
        self.assertEqual(spline_b.GetTangent(3)["vl"], fs.Vector(-3, 3, 0))
        self.assertEqual(spline_b.GetTangent(3)["vr"], fs.Vector(3, -3, 0))
        self.assert_no_echo()

    def test_moving_one_point_sends_only_its_chunk(self):
        mesh = self.mesh(self.a)
        self.edit(self.a, lambda: mesh.SetPoint(300, fs.Vector(-5, -5, 7)))
        updates = self.updates_for(mesh)
        self.assertEqual(len(updates), 1)
        pts = updates[0]["patch"]["sub"]["g"]["sub"]["pts"]
        self.assertEqual(list(pts["set"].keys()), [str(300 // 128)])
        self.assertNotIn("polys", updates[0]["patch"]["sub"]["g"].get("sub", {}))
        self.assertLess(len(json.dumps(updates[0])), 8000)
        self.assertEqual(self.mesh(self.b).GetPoint(300), fs.Vector(-5, -5, 7))
        self.assert_meshes_equal()
        self.assert_no_echo()

    def test_topology_change_rebuilds_mesh_and_keeps_uvs(self):
        mesh = self.mesh(self.a)

        def grow():
            points, polys = mesh.GetPointCount(), mesh.GetPolygonCount()
            mesh.ResizeObject(points + 4, polys + 2)
            for k in range(4):
                mesh.SetPoint(points + k, fs.Vector(500 + k, 0, 0))
            mesh.SetPolygon(polys, fs.CPolygon(points, points + 1, points + 2, points + 3))
            mesh.SetPolygon(polys + 1, fs.CPolygon(points, points + 1, points + 2, points + 2))
        self.edit(self.a, grow)
        # The selection tag's size travels with the topology change, not a scan interval later.
        selection_uuid = self.compat.read_uid(tag_named(self.mesh(self.a), "PolySel"))
        self.assertEqual(self.a.session.server.scene.get(selection_uuid).data["s"]["n"], 402)
        self.settle_slow()
        self.assert_meshes_equal()
        self.assertEqual(self.mesh(self.b).GetPolygonCount(), 402)
        uv_b = tag_named(self.mesh(self.b), "UVs")
        self.assertEqual(uv_b.GetDataCount(), 402, "UVW tag follows the polygon count")
        self.assertEqual(uv_values(uv_b)[:400], uv_values(self.uv_a)[:400])
        self.assert_no_echo()

        def shrink():
            mesh.ResizeObject(mesh.GetPointCount(), 100)
        self.edit(self.a, shrink)
        self.settle_slow()
        self.assertEqual(self.mesh(self.b).GetPolygonCount(), 100)
        self.assertEqual(tag_named(self.mesh(self.b), "UVs").GetDataCount(), 100)
        self.assert_meshes_equal()

    def test_guest_edits_reach_host(self):
        mesh_b = self.mesh(self.b)
        self.edit(self.b, lambda: mesh_b.SetPoint(5, fs.Vector(1, 2, 3)))
        self.assertEqual(self.mesh(self.a).GetPoint(5), fs.Vector(1, 2, 3))
        self.assert_meshes_equal()


class SelectionTagTest(ModelingTestCase):
    def test_selection_edit_has_no_dirty_counter_but_is_found(self):
        tag_a = tag_named(self.mesh(self.a), "PolySel")
        self.a.activate()
        tag_a.GetBaseSelect().DeselectAll()
        for index in (10, 11, 12, 200):
            tag_a.GetBaseSelect().Select(index)
        self.settle_slow()
        self.assertEqual(selected(tag_named(self.mesh(self.b), "PolySel"), 400), [10, 11, 12, 200])
        self.assert_no_echo()

    def test_new_selection_tags_of_all_kinds(self):
        mesh_b = self.mesh(self.b)

        def add():
            for ctype, name, picks in ((fs.Tpointselection, "Pts", (0, 5, 6)), (fs.Tedgeselection, "Edges", (3, 99, 1599))):
                tag = mesh_b.MakeTag(ctype)
                tag.SetName(fs.native(name))
                for pick in picks:
                    tag.GetBaseSelect().Select(pick)
        self.edit(self.b, add)
        self.settle_slow()
        mesh_a = self.mesh(self.a)
        self.assertEqual(selected(tag_named(mesh_a, "Pts"), 441), [0, 5, 6])
        self.assertEqual(selected(tag_named(mesh_a, "Edges"), 1600), [3, 99, 1599])

    def test_empty_selection_and_clearing(self):
        tag_a = tag_named(self.mesh(self.a), "PolySel")
        self.a.activate()
        tag_a.GetBaseSelect().DeselectAll()
        self.settle_slow()
        self.assertEqual(selected(tag_named(self.mesh(self.b), "PolySel"), 400), [])


class UVWTagTest(ModelingTestCase):
    def test_uv_edit_sends_only_changed_chunk(self):
        uv_a = self.uv_a
        self.edit(self.a, lambda: uv_a.SetSlow(300, fs.Vector(0.9, 0.8, 0), fs.Vector(0.7, 0.6, 0),
                                               fs.Vector(0.5, 0.4, 0), fs.Vector(0.3, 0.1, 0)))
        updates = self.updates_for(uv_a)
        self.assertEqual(len(updates), 1)
        chunks = updates[0]["patch"]["sub"]["uv"]["sub"]["uv"]["set"]
        self.assertEqual(list(chunks.keys()), [str(300 // 128)])
        uv_b = tag_named(self.mesh(self.b), "UVs")
        self.assertEqual(uv_values(uv_b), uv_values(uv_a))
        self.assertNotEqual(uv_b.GetSlow(300)["a"].x, 0.9, "stored as float32 like C4D")
        self.assert_no_echo()

    def test_uv_edit_without_any_dirty_counter_is_found_periodically(self):
        """On R19 a UV edit moved no counter; the periodic content check still finds it."""
        uv_a = self.uv_a
        uv_a.counts_edits = False
        self.a.activate()
        uv_a.SetSlow(10, fs.Vector(0.11, 0.22, 0), fs.Vector(0.33, 0.44, 0), fs.Vector(0.55, 0.66, 0),
                     fs.Vector(0.77, 0.88, 0))
        self.settle_slow()
        uv_b = tag_named(self.mesh(self.b), "UVs")
        self.assertEqual(uv_values(uv_b), uv_values(uv_a))
        self.assertNotEqual(uv_values(uv_a)[10], uv_values(self.uv_a)[11])
        self.assert_no_echo()

    def test_fresh_unsized_uvw_tag(self):
        """MakeTag gives 0 entries; the owner's resize sizes the tag (measured on C4D 2023)."""
        mesh_b = self.mesh(self.b)
        holder = {}

        def make():
            holder["tag"] = mesh_b.MakeTag(fs.Tuvw)
            holder["tag"].SetName(fs.native("Fresh"))
        self.edit(self.b, make)
        fresh_a = tag_named(self.mesh(self.a), "Fresh")
        self.assertIsNotNone(fresh_a)
        self.assertEqual(fresh_a.GetDataCount(), 0)

        def fill():
            mesh_b.ResizeObject(mesh_b.GetPointCount(), mesh_b.GetPolygonCount())
            holder["tag"].SetSlow(7, fs.Vector(0.25, 0.5, 0), fs.Vector(0.75, 0.5, 0), fs.Vector(0.75, 1, 0),
                                  fs.Vector(0.25, 1, 0))
        self.edit(self.b, fill)
        self.assertEqual(fresh_a.GetDataCount(), 400)
        self.assertEqual(uv_values(fresh_a)[7], uv_values(holder["tag"])[7])


class SplineTest(ModelingTestCase):
    def test_point_tangent_and_segment_edits(self):
        spline = self.spline(self.a)
        self.edit(self.a, lambda: spline.SetPoint(2, fs.Vector(99, 99, 0)))
        self.assertEqual(self.spline(self.b).GetAllPoints()[2], fs.Vector(99, 99, 0))

        self.a.activate()
        spline.SetTangent(1, fs.Vector(-9, 9, 0), fs.Vector(9, -9, 0))  # moves no counter
        self.settle_slow()
        self.assertEqual(self.spline(self.b).GetTangent(1)["vr"], fs.Vector(9, -9, 0))

        self.a.activate()
        spline.SetSegment(0, 6, True)  # closed flag: moves no counter either
        self.settle_slow()
        self.assertEqual(self.spline(self.b).GetSegment(0), {"cnt": 6, "closed": True})
        self.assert_no_echo()

    def test_adding_points_and_segments(self):
        spline = self.spline(self.a)

        def grow():
            spline.ResizeObject(10, 2)
            spline.SetSegment(0, 6, False)
            spline.SetSegment(1, 4, True)
            for i in range(6, 10):
                spline.SetPoint(i, fs.Vector(i * 10, 5, 0))
        self.edit(self.a, grow)
        self.settle_slow()
        spline_b = self.spline(self.b)
        self.assertEqual(spline_b.GetPointCount(), 10)
        self.assertEqual([spline_b.GetSegment(i) for i in range(2)],
                         [{"cnt": 6, "closed": False}, {"cnt": 4, "closed": True}])
        self.assertEqual(spline_b.GetAllPoints()[9], fs.Vector(90, 5, 0))

    def test_linear_spline_has_no_tangents(self):
        spline = self.spline(self.a)
        self.edit(self.a, lambda: spline.__setitem__(fs.SPLINEOBJECT_TYPE, fs.SPLINETYPE_LINEAR))
        self.settle_slow()
        self.assertEqual(self.spline(self.b)[fs.SPLINEOBJECT_TYPE], fs.SPLINETYPE_LINEAR)
        self.assertEqual(self.spline(self.b).GetTangentCount(), 0)


class ModelingLockTest(ModelingTestCase):
    def test_point_edit_on_locked_mesh_is_undone(self):
        self.edit(self.a, lambda: self.mesh(self.a).SetBit(fs.BIT_ACTIVE))
        seq = self.a.session.server.scene.seq
        mesh_b = self.mesh(self.b)
        before = mesh_b.GetPoint(10)
        self.edit(self.b, lambda: mesh_b.SetPoint(10, fs.Vector(666, 666, 666)))
        self.settle_slow()
        self.assertEqual(self.mesh(self.b).GetPoint(10), before)
        self.assertEqual(self.a.session.server.scene.seq, seq)
        self.assert_meshes_equal()

    def test_selection_tag_edit_on_locked_mesh_is_undone(self):
        self.edit(self.a, lambda: self.mesh(self.a).SetBit(fs.BIT_ACTIVE))
        seq = self.a.session.server.scene.seq
        tag_b = tag_named(self.mesh(self.b), "PolySel")
        self.b.activate()
        tag_b.GetBaseSelect().Select(250)
        self.settle_slow()
        self.assertEqual(selected(tag_b, 400), [0, 1, 2, 3, 40, 41, 399])
        self.assertEqual(self.a.session.server.scene.seq, seq)

    def test_spline_edit_on_locked_spline_is_undone(self):
        self.edit(self.a, lambda: self.spline(self.a).SetBit(fs.BIT_ACTIVE))
        spline_b = self.spline(self.b)
        self.b.activate()
        spline_b.SetTangent(0, fs.Vector(55, 55, 0), fs.Vector(66, 66, 0))
        self.settle_slow()
        self.assertEqual(spline_b.GetTangent(0)["vl"], fs.Vector(-3, 0, 0))


class ModelingSecurityTest(ModelingTestCase):
    def test_hostile_geometry_and_tag_data_are_rejected(self):
        from c4d_collab_lib.core import protocol
        host = self.a
        parent_uuid = self.compat.read_uid(self.mesh(self.a))
        pack_f, pack_i = protocol.pack_floats, protocol.pack_ints

        def create(uuid, kind, ctype, parent, data, name):
            return {"op": "create", "uuid": uuid, "kind": kind, "ctype": ctype, "parent": parent, "prev": None,
                    "name": name, "data": data}
        bad = [
            create("1" * 32, "object", fs.Opolygon, None, {"p": {}, "g": {  # polygon index out of range
                "n": [3, 1], "pts": {"0": pack_f([0.0] * 9)}, "polys": {"0": pack_i([0, 1, 2, 99])}}}, "BadIndex"),
            create("2" * 32, "object", fs.Opolygon, None, {"p": {}, "g": {  # chunk has the wrong size
                "n": [200, 0], "pts": {"0": pack_f([0.0] * 9), "1": pack_f([0.0] * 9)}, "polys": {}}}, "BadChunk"),
            create("3" * 32, "object", fs.Opolygon, None, {"p": {}, "g": {  # absurd declared size
                "n": [10 ** 9, 0], "pts": {}, "polys": {}}}, "Huge"),
            create("4" * 32, "tag", fs.Tpolygonselection, parent_uuid, {"p": {}, "s": {  # range out of bounds
                "n": 400, "r": pack_i([390, 5000])}}, "BadSel"),
            create("5" * 32, "tag", fs.Tpolygonselection, parent_uuid, {"p": {}, "s": {  # size mismatch
                "n": 7, "r": pack_i([0, 3])}}, "WrongSizeSel"),
            create("6" * 32, "tag", fs.Tuvw, parent_uuid, {"p": {}, "uv": {  # size mismatch
                "n": 5, "uv": {"0": pack_f([0.0] * 40)}}}, "WrongSizeUV"),
            create("7" * 32, "object", fs.Ospline, None, {"p": {}, "g": {  # segments do not add up
                "n": [4, 1], "pts": {"0": pack_f([0.0] * 12)}, "seg": pack_i([9, 0])}}, "BadSpline"),
        ]
        host.session.send_ops(bad)
        self.settle()
        for name in ("BadIndex", "BadChunk", "Huge", "BadSpline"):
            created = self.find(self.b.doc, name)
            self.assertIsNotNone(created, name + " is created (the hierarchy stays intact)")
            self.assertEqual(created.GetPointCount(), 0, name + " has no (invalid) geometry")
        mesh_b = self.mesh(self.b)
        self.assertEqual(selected(tag_named(mesh_b, "PolySel"), 400), [0, 1, 2, 3, 40, 41, 399])
        self.assertEqual(selected(tag_named(mesh_b, "BadSel"), 400), [])
        self.assertEqual(tag_named(mesh_b, "WrongSizeUV").GetDataCount(), 0)
        self.assertTrue(self.a.session.server.running)


class PerformanceTest(unittest.TestCase):
    def test_100k_point_mesh_is_cheap_to_dump_and_diff(self):
        import fake_c4d
        fake_c4d.uninstall()
        fake_c4d.install()
        from c4d_collab_lib.c4d_adapter.serializers import geometry
        from c4d_collab_lib.core import diff
        mesh = make_grid(316, 316, "Big")
        self.assertGreater(mesh.GetPointCount(), 100000)
        start = time.time()
        first = geometry.dump_polygon(mesh)
        dump_time = time.time() - start
        mesh.SetPoint(70000, fs.Vector(1, 2, 3))
        start = time.time()
        second = geometry.dump_polygon(mesh)
        patch = diff.diff({"g": first}, {"g": second})
        edit_time = time.time() - start
        chunks = patch["sub"]["g"]["sub"]["pts"]["set"]
        self.assertEqual(len(chunks), 1)
        self.assertEqual(patch["sub"]["g"]["sub"].keys() if hasattr(patch["sub"]["g"]["sub"], "keys") else [], {"pts": 1}.keys())
        self.assertLess(len(json.dumps(patch)), 8000)
        self.assertLess(dump_time, 6.0, "first dump took %.2fs" % dump_time)
        self.assertLess(edit_time, 6.0, "dump+diff after a point edit took %.2fs" % edit_time)
        fake_c4d.uninstall()


if __name__ == "__main__":
    unittest.main()
