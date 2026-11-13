# -*- coding: utf-8 -*-
"""Phase 7: the hidden overlay object, camera sharing and the actual drawing calls."""
from __future__ import absolute_import, division, print_function, unicode_literals

import time
import unittest

import fake_scene as fs
from test_sync import SyncTestCase


class RecordingDraw(object):
    """Stands in for a viewport (BaseDraw) and records what gets painted."""

    def __init__(self, behind=False):
        self.lines, self.lines2d, self.texts, self.pens = [], [], [], []
        self.behind = behind
        self.screen_mode = False

    def SetMatrix_Matrix(self, op, matrix):
        self.screen_mode = False

    def SetMatrix_Screen(self):
        self.screen_mode = True

    def SetPen(self, color):
        self.pens.append((color.x, color.y, color.z))

    def DrawLine(self, a, b, flags):
        self.lines.append((a, b))

    def DrawLine2D(self, a, b):
        assert self.screen_mode, "2D lines must be drawn in screen space"
        self.lines2d.append((a, b))

    def WS(self, point):
        return fs.Vector(300, 200, -1 if self.behind else 10)

    def DrawHUDText(self, x, y, text):
        self.texts.append((x, y, text))

    def GetSceneCamera(self, doc):
        return None

    def GetEditorCamera(self):
        return fs.FakeCamera(fs.Matrix(fs.Vector(0, 100, -500)))


class PresenceTestCase(SyncTestCase):
    def setUp(self):
        SyncTestCase.setUp(self)
        from c4d_collab_lib.c4d_adapter import compat, presence, tracker
        self.presence, self.compat, self.tracker_module = presence, compat, tracker
        self._interval = tracker.TRACK_SCAN_INTERVAL
        tracker.TRACK_SCAN_INTERVAL = 0.05
        self.a = self.host(self.build_scene())
        self.b = self.join()

    def tearDown(self):
        self.tracker_module.TRACK_SCAN_INTERVAL = self._interval
        SyncTestCase.tearDown(self)

    def overlay_for(self, instance, viewer=(0.0, 100.0, -500.0)):
        return self.presence.current_overlay(instance.doc, viewer, instance.session, instance.sync)

    def settle_slow(self):
        for _ in range(3):
            time.sleep(0.06)
            self.settle()


class OverlayObjectTest(PresenceTestCase):
    def test_each_document_gets_one_hidden_overlay_object(self):
        for instance in (self.a, self.b):
            holders = [o for o in instance.doc._all_objects() if o.GetType() == fs.PRESENCE_OBJECT]
            self.assertEqual(len(holders), 1)
            self.assertTrue(holders[0].GetNBit(self.c4d.NBIT_OHIDE), "hidden in the Object Manager")

    def test_overlay_object_is_never_synced(self):
        nodes = self.a.session.server.scene.nodes.values()
        self.assertFalse([n for n in nodes if n.ctype == fs.PRESENCE_OBJECT])
        self.assert_trees_equal(self.a, self.b)
        self.assert_no_echo()

    def test_overlay_object_is_not_left_selected(self):
        holder = self.presence.find(self.a.doc)
        self.assertFalse(holder.GetBit(fs.BIT_ACTIVE), "it appeared as the active object in the Attribute Manager")

    def test_ensure_is_idempotent_and_remove_cleans_up(self):
        again = self.presence.ensure(self.a.doc)
        self.assertIs(again, self.presence.find(self.a.doc))
        self.assertEqual(len([o for o in self.a.doc._all_objects() if o.GetType() == fs.PRESENCE_OBJECT]), 1)
        self.presence.remove(self.a.doc)
        self.assertIsNone(self.presence.find(self.a.doc))

    def test_leaving_removes_the_overlay_object(self):
        doc = self.b.doc
        self.b.session.leave()
        self.assertIsNone(self.presence.find(doc))
        self.assertIsNotNone(self.find(doc, "Cube"), "the scene itself stays")

    def test_missing_object_type_is_survivable(self):
        fs.CREATABLE = frozenset(t for t in fs.CREATABLE if t != fs.PRESENCE_OBJECT)
        try:
            self.assertIsNone(self.presence.ensure(fs.BaseDocument()))
        finally:
            fs.CREATABLE = frozenset(fs.SCHEMAS)


class CameraSharingTest(PresenceTestCase):
    def test_camera_reaches_the_other_user(self):
        self.a.doc.camera_matrix = fs.Matrix(fs.Vector(100, 200, -300), fs.Vector(0, 0, 1), fs.Vector(0, 1, 0),
                                             fs.Vector(-1, 0, 0))
        self.settle_slow()
        shared = self.b.session.mirror.presence[self.a.session.mirror.user_id]["cam"]
        self.assertEqual(shared, [100.0, 200.0, -300.0, 0.0, 0.0, 1.0, 0.0, 1.0, 0.0, -1.0, 0.0, 0.0])

    def test_moving_the_view_updates_it_but_a_still_view_sends_nothing(self):
        host_id = self.a.session.mirror.user_id
        self.a.doc.camera_matrix = fs.Matrix(fs.Vector(1, 2, 3))
        self.settle_slow()
        self.assertEqual(self.b.session.mirror.presence[host_id]["cam"][:3], [1.0, 2.0, 3.0])
        seq = self.b.session.client._incoming.qsize()
        self.settle_slow()
        self.settle_slow()
        self.assertEqual(self.b.session.client._incoming.qsize(), seq)
        self.a.doc.camera_matrix = fs.Matrix(fs.Vector(9, 9, 9))
        self.settle_slow()
        self.assertEqual(self.b.session.mirror.presence[host_id]["cam"][:3], [9.0, 9.0, 9.0])


class WhoIsWhoTest(PresenceTestCase):
    def select(self, instance, name):
        atom = self.find(instance.doc, name)
        self.edit(instance, lambda: atom.SetBit(fs.BIT_ACTIVE))

    def test_selection_shows_as_a_coloured_box_with_the_users_name(self):
        self.select(self.b, "Cube")
        content = self.overlay_for(self.a)
        guest = self.a.session.mirror.users[self.b.session.mirror.user_id]
        colour = tuple(guest["color"])
        boxes = [line for line in content.lines if line[2] == colour]
        self.assertEqual(len(boxes), 12)
        self.assertIn((guest["name"], colour), [(text, color) for _p, text, color in content.labels])
        self.assertEqual(guest["name"], "Guest")

    def test_users_have_different_colours_and_colour_names(self):
        users = self.a.session.mirror.users
        colours = [tuple(u["color"]) for u in users.values()]
        self.assertEqual(len(set(colours)), len(colours))
        self.assertEqual(sorted(u["color_name"] for u in users.values()), ["Blue", "Red"])

    def test_own_selection_is_not_drawn_for_yourself(self):
        self.select(self.a, "Cube")
        self.assertEqual(self.overlay_for(self.a).lines, [])
        self.assertTrue(self.overlay_for(self.b).lines, "...but the other user sees it")

    def test_release_removes_the_highlight(self):
        self.select(self.b, "Cube")
        self.assertTrue(self.overlay_for(self.a).lines)
        cube = self.find(self.b.doc, "Cube")
        self.edit(self.b, lambda: cube.DelBit(fs.BIT_ACTIVE))
        self.assertEqual(self.overlay_for(self.a).lines, [])

    def test_camera_and_selection_together(self):
        self.b.doc.camera_matrix = fs.Matrix(fs.Vector(300, 300, -300))
        self.select(self.b, "Cube")
        self.settle_slow()
        content = self.overlay_for(self.a)
        self.assertEqual(len(content.lines), 10 + 12)
        self.assertEqual(len(content.labels), 2)

    def test_nothing_to_draw_before_the_scene_is_synced(self):
        self.assertIsNone(self.presence.current_overlay(fs.BaseDocument(), None, self.a.session, self.a.sync),
                          "a different document is never drawn on")


class DrawingTest(PresenceTestCase):
    def test_painting_an_overlay(self):
        self.b.doc.camera_matrix = fs.Matrix(fs.Vector(300, 300, -300))
        cube_b = self.find(self.b.doc, "Cube")
        self.edit(self.b, lambda: cube_b.SetBit(fs.BIT_ACTIVE))
        self.settle_slow()
        bd = RecordingDraw()
        self.presence.draw_overlay(self.a.doc, bd, self.overlay_for(self.a))
        self.assertEqual(len(bd.lines), 22, "camera icon + selection box in world space")
        self.assertEqual([t for _x, _y, t in bd.texts], ["Guest", "Guest"])
        self.assertEqual(len(bd.lines2d), 8, "a coloured frame around each of the two name tags")
        self.assertEqual(set(bd.pens), {tuple(self.a.session.mirror.users[self.b.session.mirror.user_id]["color"])})

    def test_tags_behind_the_viewer_are_skipped(self):
        cube_b = self.find(self.b.doc, "Cube")
        self.edit(self.b, lambda: cube_b.SetBit(fs.BIT_ACTIVE))
        bd = RecordingDraw(behind=True)
        self.presence.draw_overlay(self.a.doc, bd, self.overlay_for(self.a))
        self.assertEqual(bd.texts, [])
        self.assertEqual(len(bd.lines), 12, "the 3D box is still drawn")

    def test_object_draw_returns_ok_and_survives_errors(self):
        from c4d_collab_lib.c4d_adapter import bridge
        obj = self.presence.find(self.a.doc)
        data = self.presence.PresenceObject()
        self.assertTrue(data.Init(obj))

        class ExplodingDraw(RecordingDraw):
            def SetMatrix_Matrix(self, op, matrix):
                raise RuntimeError("viewport exploded")
        # the plugin's own session is idle here, so Draw has nothing to paint; errors never escape
        self.assertEqual(data.Draw(obj, self.presence.DRAW_PASS, ExplodingDraw(), None), self.c4d.DRAWRESULT_OK)
        self.assertEqual(data.Draw(obj, self.presence.DRAW_PASS + 1, RecordingDraw(), None), self.c4d.DRAWRESULT_SKIP)
        self.assertEqual(self.presence.DRAW_PASS, self.c4d.DRAWPASS_OBJECT,
                         "the highlight pass paints white and R19 does not show it")
        self.assertEqual(data.Draw(obj, self.c4d.DRAWPASS_HIGHLIGHTS, RecordingDraw(), None), self.c4d.DRAWRESULT_SKIP)

    def test_overlay_object_is_inserted_last(self):
        doc = self.a.doc
        self.assertTrue(self.presence.is_last(self.presence.find(doc)))

    def test_move_last_puts_it_after_objects_added_later(self):
        doc = self.a.doc
        holder = self.presence.find(doc)
        late = fs.BaseObject(fs.Onull)
        doc.InsertObject(late, None, holder)  # no tick: sync would put the overlay back at the end
        self.assertFalse(self.presence.is_last(holder))
        self.presence.move_last(doc, holder)
        self.assertTrue(self.presence.is_last(holder))
        self.assertIs(holder, self.presence.find(doc))
        self.assertFalse(holder.GetBit(fs.BIT_ACTIVE))

    def test_sync_tick_keeps_the_overlay_last(self):
        doc = self.a.doc
        late = fs.BaseObject(fs.Onull)
        self.edit(self.a, lambda: doc.InsertObject(late, None, self.presence.find(doc)))
        self.a.activate()
        self.a.sync.tick()
        self.assertTrue(self.presence.is_last(self.presence.find(doc)))


class OutlineShapeTest(PresenceTestCase):
    def make_mesh(self, doc, polys, points=4):
        mesh = fs.PolygonObject()
        mesh.ResizeObject(points, len(polys))
        for index in range(points):
            mesh._points[index] = fs.Vector(index * 10.0, 0, 0)
        mesh._polys = list(polys)
        doc.InsertObject(mesh)
        return mesh

    def test_a_polygon_object_outlines_its_real_edges(self):
        mesh = self.make_mesh(self.a.doc, [fs.CPolygon(0, 1, 2), fs.CPolygon(1, 2, 3)])
        outline = self.presence.object_outline(mesh, "m")
        self.assertEqual(len(outline), 5, "two triangles share one edge")
        self.assertIn(((0.0, 0.0, 0.0), (10.0, 0.0, 0.0)), outline)

    def test_quads_give_four_edges(self):
        mesh = self.make_mesh(self.a.doc, [fs.CPolygon(0, 1, 2, 3)])
        self.assertEqual(len(self.presence.object_outline(mesh)), 4)

    def test_nulls_and_huge_meshes_fall_back_to_the_box(self):
        self.assertIsNone(self.presence.object_outline(fs.BaseObject(fs.Onull)))
        big = self.make_mesh(self.a.doc, [fs.CPolygon(0, 1, 2)] * (self.presence.MAX_SHAPE_POLYGONS + 1))
        self.assertIsNone(self.presence.object_outline(big))

    def test_the_outline_follows_the_object_and_is_cached_until_its_data_changes(self):
        mesh = self.make_mesh(self.a.doc, [fs.CPolygon(0, 1, 2)])
        first = self.presence.object_outline(mesh, "m")
        reads = []
        original = mesh.GetAllPolygons
        mesh.GetAllPolygons = lambda: reads.append(1) or original()
        self.presence.object_outline(mesh, "m")
        self.assertEqual(reads, [], "unchanged data: no second read of the mesh")
        mesh[fs.REL_POSITION] = fs.Vector(100, 0, 0)  # moved: the cached points are re-transformed
        moved = self.presence.object_outline(mesh, "m")
        self.assertEqual(moved[0][0][0], first[0][0][0] + 100.0)

    def test_no_cinema_4d_object_is_kept_between_redraws(self):
        """Cinema 4D frees its caches whenever it likes: a kept cache object is a dangling one (R19 crashed with an access
        violation inside the Python wrapper). Only plain points and edges are kept."""
        mesh = self.make_mesh(self.a.doc, [fs.CPolygon(0, 1, 2)])
        self.presence.object_outline(mesh, "m")
        def plain(value):
            if isinstance(value, (list, tuple)):
                return all(plain(item) for item in value)
            return value is None or isinstance(value, (int, float))
        self.assertTrue(plain(self.presence._SHAPES["m"]), self.presence._SHAPES["m"])

    def test_a_remote_selection_is_drawn_as_the_outline(self):
        mesh = self.make_mesh(self.a.doc, [fs.CPolygon(0, 1, 2)])
        info = self.presence._resolve_atom(mesh, "m")
        self.assertEqual(len(info), 5)
        self.assertEqual(len(info[4]), 3)


class PreviewTest(PresenceTestCase):
    """The panel's "Preview overlay" button: proves drawing works without a second user."""

    def tearDown(self):
        self.presence.PREVIEW["on"] = False
        PresenceTestCase.tearDown(self)

    def test_preview_draws_demo_camera_and_a_box_around_the_selection(self):
        cube = self.find(self.a.doc, "Cube")
        self.edit(self.a, lambda: cube.SetBit(fs.BIT_ACTIVE))
        bd = RecordingDraw()
        demo = self.presence.preview_overlay(self.a.doc, bd)
        self.assertEqual(len(demo.lines), 10 + 12)
        self.assertEqual(sorted(text for _p, text, _c in demo.labels), ["Preview", "Preview"])
        self.assertEqual(set(color for _s, _e, color in demo.lines), {(1.0, 0.6, 0.1)})

    def test_preview_without_a_selection_shows_just_the_camera(self):
        demo = self.presence.preview_overlay(self.a.doc, RecordingDraw())
        self.assertEqual((len(demo.lines), len(demo.labels)), (10, 1))

    def test_object_draw_paints_the_preview_and_logs_it_once(self):
        import logging
        records = []

        class Catch(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())
        handler = Catch()
        logging.getLogger("c4d_collab").addHandler(handler)
        logging.getLogger("c4d_collab").setLevel(logging.INFO)
        self.presence._announced.clear()
        try:
            cube = self.find(self.a.doc, "Cube")
            self.edit(self.a, lambda: cube.SetBit(fs.BIT_ACTIVE))
            self.presence.set_preview(self.a.doc, True)
            holder = self.presence.find(self.a.doc)
            data = self.presence.PresenceObject()
            bd = RecordingDraw()
            self.assertEqual(data.Draw(holder, self.presence.DRAW_PASS, bd, None), self.c4d.DRAWRESULT_OK)
            self.assertEqual(len(bd.lines), 22)
            self.assertEqual([t for _x, _y, t in bd.texts], ["Preview", "Preview"])
            data.Draw(holder, self.presence.DRAW_PASS, RecordingDraw(), None)
        finally:
            logging.getLogger("c4d_collab").removeHandler(handler)
        self.assertEqual(len([m for m in records if "Draw() is being called" in m]), 1)
        self.assertEqual(len([m for m in records if "painting 22 lines and 2 name tags" in m]), 1)

    def test_the_panel_has_no_preview_button(self):
        from c4d_collab_lib.c4d_adapter import ui_dialog
        self.assertFalse(hasattr(ui_dialog, "ID_PREVIEW"))
        dialog = ui_dialog.CollabDialog()
        dialog.CreateLayout()
        dialog.InitValues()
        self.assertFalse(self.presence.PREVIEW["on"])


if __name__ == "__main__":
    unittest.main()
