# -*- coding: utf-8 -*-
"""Runs the overlay's real drawing code against Cinema 4D's real viewport API (BaseDraw).

Works in c4dpy (a real, off-screen BaseDraw exists there) and inside a Cinema 4D window (via the
self-test). It cannot prove that pixels appear, but it proves every drawing call is accepted with the
argument types we pass, the camera/selection geometry is read correctly and the demo overlay paints
what it should. `WS` (world to screen) returns zeros off-screen, so a thin wrapper supplies screen
positions while forwarding everything else to the real BaseDraw.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import io
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
PLUGIN_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "plugin", "c4d_collab"))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

import c4d  # noqa: E402
from c4d import documents  # noqa: E402

from c4d_collab_lib.c4d_adapter import compat, overlay, plugin_ids, presence  # noqa: E402


class CountingDraw(object):
    """Forwards every call to the real BaseDraw and counts them; WS gets a usable screen position."""

    def __init__(self, real):
        self._real = real
        self.calls = {}

    def _count(self, name):
        self.calls[name] = self.calls.get(name, 0) + 1

    def WS(self, point):
        self._count("WS")
        self._real.WS(point)  # still exercise the real call
        return c4d.Vector(400, 300, 10)

    def __getattr__(self, name):
        target = getattr(self._real, name)
        if not callable(target):
            return target

        def call(*args):
            self._count(name)
            return target(*args)
        return call


def run():
    checks = []

    def check(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": "%s" % (detail,)})
        print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))

    try:
        doc = documents.GetActiveDocument()
        cube = c4d.BaseObject(c4d.Ocube)
        cube.SetName(compat.native_str("DrawCheckCube"))
        doc.InsertObject(cube)
        cube.SetBit(c4d.BIT_ACTIVE)
        real = doc.GetActiveBaseDraw()
        check("a real viewport (BaseDraw) is available", real is not None)
        if real is None:
            return checks

        camera = presence.viewer_camera(doc)
        check("viewer camera is found", camera is not None)
        values = presence.camera_values(camera)
        check("camera values are 12 finite numbers", len(values) == 12 and overlay.parse_camera(values) is not None, values)

        info = presence._resolve_atom(cube)
        check("object box data is read (origin, axes, centre, radius)", info is not None and len(info) == 4, info)

        pyramid = c4d.BaseObject(c4d.Opyramid)
        doc.InsertObject(pyramid)
        doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_0)
        outline = presence.object_outline(pyramid, "pyramid")
        check("a pyramid is outlined by its 8 real edges, not a box", outline is not None and len(outline) == 8,
              None if outline is None else len(outline))
        check("a null object has no outline (box fallback)", presence.object_outline(c4d.BaseObject(c4d.Onull)) is None)

        users = {"u2": {"name": "Ufuk", "color": [0.25, 0.65, 0.95]}}
        content = overlay.build(users, {"u2": {"cam": values}}, {"cube": "u2"}, "u1", (0.0, 0.0, 0.0),
                                lambda uuid: presence._resolve_atom(cube))
        check("overlay has a camera icon and a selection box", len(content.lines) == 22 and len(content.labels) == 2,
              "%d lines, %d tags" % (len(content.lines), len(content.labels)))

        bd = CountingDraw(real)
        presence.draw_overlay(doc, bd, content)
        check("draw_overlay: every world-space line was drawn through the real API", bd.calls.get("DrawLine") == 22, bd.calls)
        check("draw_overlay: name tags (frame + HUD text) were drawn", bd.calls.get("DrawHUDText") == 2
              and bd.calls.get("DrawLine2D") == 8 and bd.calls.get("SetMatrix_Screen") == 1, bd.calls)

        presence.PREVIEW["on"] = True
        try:
            demo = presence.preview_overlay(doc, real)
            check("preview overlay: demo camera + box around the selection", len(demo.lines) == 22 and len(demo.labels) == 2,
                  "%d lines, %d tags" % (len(demo.lines), len(demo.labels)))
            holder = c4d.BaseObject(c4d.Onull)  # stands in for the hidden presence object
            doc.InsertObject(holder)
            bd2 = CountingDraw(real)
            data = presence.PresenceObject()
            result = data.Draw(holder, presence.DRAW_PASS, bd2, None)
            check("PresenceObject.Draw returns OK and paints the preview", result == c4d.DRAWRESULT_OK
                  and bd2.calls.get("DrawLine", 0) >= 22 and bd2.calls.get("DrawHUDText", 0) >= 2, bd2.calls)
            other_pass = data.Draw(holder, presence.DRAW_PASS + 1, CountingDraw(real), None)
            check("other draw passes are skipped", other_pass == c4d.DRAWRESULT_SKIP)
            hl_pass = data.Draw(holder, c4d.DRAWPASS_HIGHLIGHTS, CountingDraw(real), None)
            check("the highlight pass is skipped (it paints white; R19 does not show it)",
                  presence.DRAW_PASS == c4d.DRAWPASS_OBJECT and hl_pass == c4d.DRAWRESULT_SKIP)
            check("the drawing state is handed back (depth + matrix restored)",
                  bd2.calls.get("SetDepth", 0) >= 1 and bd2.calls.get("SetMatrix_Matrix", 0) >= 2, bd2.calls)
            presence.move_last(doc, holder)
            check("move_last puts an object at the end of the document", presence.is_last(holder))
        finally:
            presence.PREVIEW["on"] = False

        check("the plugin id of the overlay object is the one registered in the .pyp",
              plugin_ids.PRESENCE_OBJECT == 1000005)
    except BaseException as exc:  # noqa
        check("unexpected exception", False, traceback.format_exc())
    return checks


if __name__ == "__main__":
    results = run()
    out = os.environ.get("C4D_COLLAB_DRAW_OUT")
    if out:
        with io.open(out, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"checks": results, "c4d": c4d.GetC4DVersion()}, indent=2))
