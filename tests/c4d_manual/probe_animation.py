# -*- coding: utf-8 -*-
"""Phase 5 probe: how tracks, curves and keys behave (run with c4dpy or Script Manager).

Prints API availability, track types, key methods/parameters and which dirty
counters move for each animation edit. Writes the report next to the
results of tests/c4d_real when C4D_COLLAB_PROBE_OUT is set.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import io
import os
import sys

import c4d
from c4d import documents

LINES = []


def out(*parts):
    text = " ".join("%s" % (p,) for p in parts)
    LINES.append(text)
    print(text)


def flag(name):
    return getattr(c4d, name, None)


def dirty(atom):
    result = []
    for name in ("DATA", "MATRIX", "CHILDREN", "CACHE", "SELECT", "DESCRIPTION"):
        value = flag("DIRTYFLAGS_" + name)
        if value is not None:
            result.append(atom.GetDirty(value))
    return tuple(result)


def hdirty(doc):
    names = ("ANIMATION", "OBJECT", "OBJECT_MATRIX", "ALL")
    return dict((n, doc.GetHDirty(flag("HDIRTYFLAGS_" + n))) for n in names if flag("HDIRTYFLAGS_" + n) is not None)


def changed(before, after):
    return [i for i, (a, b) in enumerate(zip(before, after)) if a != b]


NAMES = ["DATA", "MATRIX", "CHILDREN", "CACHE", "SELECT", "DESCRIPTION"]


def measure(label, doc, atoms, action):
    before = [dirty(a) for a in atoms]
    doc_before = hdirty(doc)
    result = action()
    after = [dirty(a) for a in atoms]
    doc_after = hdirty(doc)
    parts = []
    for name, b, a in zip(("obj", "track", "curve"), before, after):
        parts.append("%s:%s" % (name, ",".join(NAMES[i] for i in changed(b, a)) or "-"))
    doc_changes = [k for k in doc_after if doc_after[k] != doc_before.get(k)]
    out("  %-30s %s | doc:%s" % (label, " ".join(parts), ",".join(doc_changes) or "-"))
    return result


def main():
    out("C4D", c4d.GetC4DVersion(), "Python", sys.version.split()[0])
    doc = documents.BaseDocument()
    documents.InsertBaseDocument(doc)
    documents.SetActiveDocument(doc)
    fps = doc.GetFps()
    out("fps", fps, "min", doc.GetMinTime().Get(), "max", doc.GetMaxTime().Get())
    for name in ("CTbase", "CTpla", "CTsound", "CTtime", "CTmorph", "CTdynamicspline",
                 "CINTERPOLATION_SPLINE", "CINTERPOLATION_LINEAR", "CINTERPOLATION_STEP",
                 "ID_CKEY_AUTO", "ID_CKEY_CLAMP", "ID_CKEY_ZERO_O", "ID_CKEY_ZERO_A",
                 "ID_CTRACK_BEFORE", "ID_CTRACK_AFTER", "DOCUMENT_FPS", "DOCUMENT_MINTIME",
                 "DOCUMENT_MAXTIME", "DOCUMENT_LOOPMINTIME", "DOCUMENT_LOOPMAXTIME"):
        out("  const", name, flag(name))
    out("CKey methods:", " ".join(sorted(n for n in dir(c4d.CKey) if not n.startswith("_"))))
    out("CCurve methods:", " ".join(sorted(n for n in dir(c4d.CCurve) if not n.startswith("_"))))
    out("CTrack methods:", " ".join(sorted(n for n in dir(c4d.CTrack) if not n.startswith("_"))))

    cube = c4d.BaseObject(c4d.Ocube)
    doc.InsertObject(cube)
    desc_id = c4d.DescID(c4d.DescLevel(c4d.ID_BASEOBJECT_REL_POSITION, c4d.DTYPE_VECTOR, 0),
                         c4d.DescLevel(c4d.VECTOR_X, c4d.DTYPE_REAL, 0))
    track = c4d.CTrack(cube, desc_id)
    cube.InsertTrackSorted(track)
    curve = track.GetCurve()
    out("track type", track.GetType(), "name", track.GetName(), "desc depth", track.GetDescriptionID().GetDepth())
    added = curve.AddKey(c4d.BaseTime(0, fps))
    out("AddKey returns", type(added).__name__, added.keys() if isinstance(added, dict) else "")
    key = added["key"] if isinstance(added, dict) else added
    key.SetValue(curve, 0.0)

    atoms = [cube, track, curve]
    measure("add key 2", doc, atoms, lambda: curve.AddKey(c4d.BaseTime(20, fps)))
    key2 = curve.GetKey(1)
    measure("key value", doc, atoms, lambda: key2.SetValue(curve, 100.0))
    measure("key time", doc, atoms, lambda: key2.SetTime(curve, c4d.BaseTime(30, fps)))
    measure("interpolation", doc, atoms, lambda: key2.SetInterpolation(curve, c4d.CINTERPOLATION_LINEAR))
    measure("tangent right", doc, atoms, lambda: key2.SetValueRight(curve, 5.0))
    if flag("ID_CTRACK_AFTER") is not None:
        measure("track after-loop param", doc, atoms, lambda: track.SetParameter(
            c4d.DescID(c4d.ID_CTRACK_AFTER), 1, getattr(c4d, "DESCFLAGS_SET_NONE", 0)))
    measure("delete key", doc, atoms, lambda: curve.DelKey(1))
    measure("set time 15 (scrub)", doc, atoms, lambda: doc.SetTime(c4d.BaseTime(15, fps)))
    measure("ExecutePasses at 15", doc, atoms, lambda: doc.ExecutePasses(None, True, True, True, 0))
    out("  cube pos x after eval:", cube.GetRelPos().x)

    out("key 0 fields: value %s time %s interp %s tl %s vl %s tr %s vr %s" % (
        key.GetValue(), key.GetTime().Get(), key.GetInterpolation(), key.GetTimeLeft().Get(),
        key.GetValueLeft(), key.GetTimeRight().Get(), key.GetValueRight()))
    out("key description params:")
    try:
        for bc, did, group in key.GetDescription(getattr(c4d, "DESCFLAGS_DESC_NONE", 0)):
            name = bc[c4d.DESC_NAME] if bc else ""
            out("   ", [did[i].id for i in range(did.GetDepth())], did[did.GetDepth() - 1].dtype, name)
    except Exception as exc:
        out("   description failed:", exc)
    out("track description params:")
    try:
        for bc, did, group in track.GetDescription(getattr(c4d, "DESCFLAGS_DESC_NONE", 0)):
            name = bc[c4d.DESC_NAME] if bc else ""
            out("   ", [did[i].id for i in range(did.GetDepth())], did[did.GetDepth() - 1].dtype, name)
    except Exception as exc:
        out("   description failed:", exc)

    # Bool and long parameter tracks
    sphere = c4d.BaseObject(c4d.Osphere)
    doc.InsertObject(sphere)
    for label, ident, dtype in (("long (segments)", c4d.PRIM_SPHERE_SUB, c4d.DTYPE_LONG),
                                ("bool (render perfect)", c4d.PRIM_SPHERE_PERFECT, c4d.DTYPE_BOOL)):
        t = c4d.CTrack(sphere, c4d.DescID(c4d.DescLevel(ident, dtype, 0)))
        sphere.InsertTrackSorted(t)
        c = t.GetCurve()
        a = c.AddKey(c4d.BaseTime(0, fps))
        k = a["key"] if isinstance(a, dict) else a
        try:
            k.SetValue(c, 3.0)
            value = k.GetValue()
        except Exception as exc:
            value = "SetValue failed: %s" % exc
        try:
            geo = k.GetGeData()
        except Exception as exc:
            geo = "GetGeData failed: %s" % exc
        out("  %s track type %s value %s gedata %r" % (label, t.GetType(), value, geo))

    path = os.environ.get("C4D_COLLAB_PROBE_OUT")
    if path:
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join("%s" % l for l in LINES))


main()
