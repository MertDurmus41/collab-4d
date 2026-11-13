# -*- coding: utf-8 -*-
"""The plugin against the API of every Cinema 4D version from R17 to 2026, using version profiles of the fake
c4d module (tests/adapter/profiles.py). Python 2.7 and 3.x.

What this proves: with the API of that version (methods that did not exist yet raise AttributeError, flag
names differ) the plugin loads, syncs scenes, rigs, selections and shapes, and uses its fallbacks. What it
cannot prove: how a real R17...R22 behaves (dirty counters, drawing). tests/compat/api_matrix.py lists every
API fact and where it comes from; the real versions (R19, 2023) are tested in tests/c4d_real.

Every profile runs a set of existing suites (below) once with that profile; the profiles are:
R17, R18, R19, R20, R21 (also stands for R22) and R23 (also stands for 2024, 2025, 2026: nothing in the
API the plugin uses changed after R21; checked against Maxon's API docs for 2023 and the 2026 SDK examples).
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import os
import sys
import unittest

import fake_c4d
import fake_scene as fs
import profiles
import test_animation
import test_locks
import test_materials
import test_modeling
import test_posemorph
import test_presence
import test_rig
import test_sync
from test_adapter import PLUGIN_DIR, AdapterTestCase

PER_PROFILE = [
    test_sync.SeedAndJoinTest, test_sync.EditTest, test_rig.RigTest, test_modeling.SelectionTagTest,
    test_modeling.MeshTest, test_animation.KeyframeTest, test_locks.SelectionLockTest,
    test_posemorph.PoseMorphTest, test_materials.MaterialSyncTest,
]


def _derive(base, profile):
    name = str("%s_%s" % (base.__name__, profile))
    return type(name, (base,), {"profile": profile, "__module__": __name__})


for _profile in profiles.PROFILES:
    for _base in PER_PROFILE:
        _cls = _derive(_base, _profile)
        globals()[_cls.__name__] = _cls
del _profile, _base, _cls


def load_pyp(c4d):
    path = os.path.join(PLUGIN_DIR, "c4d_collab.pyp")
    namespace = {"__name__": "__main__", "__file__": path}
    with open(path, "rb") as handle:
        exec(compile(handle.read(), path, "exec"), namespace)
    return namespace


class ProfileFactsTest(unittest.TestCase):
    """What each profile means for the plugin, one profile at a time."""

    def setUp(self):
        fake_c4d.uninstall()

    def tearDown(self):
        fake_c4d.uninstall()

    def install(self, profile):
        self.c4d = fake_c4d.install(profile)
        from c4d_collab_lib.c4d_adapter import compat
        self.compat = compat
        return compat

    def test_every_profile_loads_the_plugin_without_a_word_of_complaint(self):
        for name in profiles.PROFILES:
            fake_c4d.uninstall()
            c4d = self.install(name).c4d
            from c4d_collab_lib.c4d_adapter import bridge
            namespace = load_pyp(c4d)
            kinds = sorted(kind for kind, _, _ in c4d.registered)
            has_page = name not in ("R17", "R18")
            dev = os.path.exists(os.path.join(PLUGIN_DIR, "selftest.enabled"))
            expected = ["command"] * (2 if dev else 1) + ["message"] * (2 if has_page else 1) + ["object"]
            if has_page:
                expected.append("preference")
            self.assertEqual(kinds, sorted(expected), name)
            self.assertEqual(c4d.GetC4DVersion(), profiles.PROFILES[name]["version"])
            namespace["PluginMessage"](c4d.C4DPL_ENDACTIVITY, None)
            bridge._SESSION = None

    def test_document_counters_per_version(self):
        expected = {"R17": "none", "R18": "none", "R19": "one document counter", "R20": "detailed",
                    "R21": "detailed", "R23": "detailed"}
        for name, text in expected.items():
            fake_c4d.uninstall()
            compat = self.install(name)
            self.assertIn(text, dict(compat.capabilities())["document counters"], name)

    def test_without_document_counters_every_tick_looks_changed(self):
        compat = self.install("R17")
        self.assertFalse(compat.HAS_HDIRTY)
        doc = fs.BaseDocument()
        self.assertNotEqual(compat.doc_dirty(doc), compat.doc_dirty(doc), "no counter: always 'something changed'")
        self.assertEqual(set(compat.doc_dirty(doc)), set(compat.HDIRTY_DETAILED), "same keys the tracker reads")

    def test_with_document_counters_nothing_changes_while_nothing_happens(self):
        for name in ("R19", "R20", "R23"):
            fake_c4d.uninstall()
            compat = self.install(name)
            doc = fs.BaseDocument()
            self.assertEqual(compat.doc_dirty(doc), compat.doc_dirty(doc), name)

    def test_the_capability_report_names_every_fallback(self):
        report = {}
        for name in profiles.PROFILES:
            fake_c4d.uninstall()
            report[name] = dict(self.install(name).capabilities())
        self.assertEqual([report[n]["preference page (MCP)"] for n in ("R17", "R18", "R19", "R23")],
                         [False, False, True, True])
        self.assertEqual(report["R17"]["selection ranges"], "by flags (before R19)")
        self.assertIs(report["R19"]["selection ranges"], True)
        self.assertEqual(report["R20"]["skin weights write"], "point by point (before R21)")
        self.assertEqual(report["R21"]["skin weights write"], "whole map")
        self.assertEqual(report["R17"]["viewport name labels"], "frames only (before R18)")
        self.assertIs(report["R18"]["viewport name labels"], True)
        self.assertTrue(all(r["node ids"] for r in report.values()), "AddUniqueID exists since R16.021")
        from c4d_collab_lib.c4d_adapter import compat
        self.assertIn("document counters:", compat.describe_capabilities())

    def test_the_capabilities_are_logged_when_the_plugin_starts(self):
        import logging
        import shutil
        import tempfile
        folder = tempfile.mkdtemp(prefix="c4d_collab_caps_")
        try:
            c4d = fake_c4d.install("R19")
            c4d.storage.GeGetC4DPath = lambda which: str(folder)
            from c4d_collab_lib.c4d_adapter import bridge
            bridge.setup_logging()
            bridge._LOG_HANDLER.flush()
            with open(os.path.join(folder, "c4d_collab.log"), "rb") as handle:
                text = handle.read().decode("utf-8")
            self.assertIn("Capabilities:", text)
            self.assertIn("cinema4d: 19024", text)
            self.assertIn("one document counter (R19)", text)
        finally:
            logging.getLogger("c4d_collab").handlers = []
            bridge._LOG_HANDLER = None
            shutil.rmtree(folder, ignore_errors=True)


class OldVersionsDrawTest(test_presence.PresenceTestCase):
    """R17 has no DrawHUDText: the overlay still draws, only the name text is left out."""

    def test_a_viewport_without_hud_text_gets_frames_and_no_error(self):
        class NoText(test_presence.RecordingDraw):
            DrawHUDText = profiles._Missing("DrawHUDText")

        self.b.doc.camera_matrix = fs.Matrix(fs.Vector(300, 300, -300))
        cube_b = self.find(self.b.doc, "Cube")
        self.edit(self.b, lambda: cube_b.SetBit(fs.BIT_ACTIVE))
        self.settle_slow()
        bd = NoText()
        self.assertFalse(hasattr(bd, "DrawHUDText"))
        self.presence.draw_overlay(self.a.doc, bd, self.overlay_for(self.a))
        self.assertEqual(len(bd.lines), 22, "camera icon and selection box are drawn")
        self.assertEqual(len(bd.lines2d), 8, "the coloured frames of the name tags are drawn")
        self.assertEqual(bd.texts, [])


class OldSelectionRangesTest(test_modeling.ModelingTestCase):
    """BaseSelect.GetRange is new in R19: older versions read selections from the flags (GetAll)."""

    profile = "R17"

    def test_the_fallback_reads_the_same_ranges(self):
        from c4d_collab_lib.c4d_adapter.serializers import geometry
        poly = fs.PolygonObject()
        poly.ResizeObject(8, 3)
        select = fs.BaseSelect()
        select.SetAll([0, 1, 1, 1, 0, 0, 1, 1])
        tag = fs.BaseTag(fs.Tpointselection)
        poly.InsertTag(tag)
        tag._select = select
        self.assertFalse(hasattr(select, "GetRange"))
        data = geometry.dump_selection(tag)
        self.assertEqual(geometry.protocol.unpack_ints(data["r"]), [1, 3, 6, 7])


if __name__ == "__main__":
    unittest.main()
