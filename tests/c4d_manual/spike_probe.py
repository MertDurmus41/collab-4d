# -*- coding: utf-8 -*-
"""C4D Collab - Phase 0 spike probe.

Run inside Cinema 4D: Extensions > Script Manager > File > Import this file > Execute.
Works on R17 (Python 2.7) up to 2026 (Python 3.11+).

It never touches your open scene: everything happens in a temporary document
that is closed at the end. The report is printed to the Python console and
written to your home folder as c4d_collab_spike_<version>.txt.

What it measures:
  1. Version / Python info and which API functions exist.
  2. Which dirty counters change for each kind of edit (transform, parameter,
     rename, keyframe, point move, topology change, hierarchy).
  3. How AddUniqueID behaves on clone, save and reload.
  4. How long a dirty scan over 1,000 and 10,000 objects takes.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import os
import sys
import tempfile
import time
import traceback
import uuid

import c4d
from c4d import documents

TEST_APP_ID = 1000001  # Development-only id. Replaced by a registered plugin id later.

LINES = []


def out(*parts):
    text = " ".join("%s" % (p,) for p in parts)
    LINES.append(text)
    print(text)


def section(title):
    out("")
    out("=" * 70)
    out(title)
    out("=" * 70)


def flag(name):
    return getattr(c4d, name, None)


DIRTY_FLAG_NAMES = [
    "DIRTYFLAGS_DATA", "DIRTYFLAGS_MATRIX", "DIRTYFLAGS_CHILDREN",
    "DIRTYFLAGS_CACHE", "DIRTYFLAGS_SELECT", "DIRTYFLAGS_DESCRIPTION",
]
HDIRTY_FLAG_NAMES = [
    "HDIRTYFLAGS_ANIMATION", "HDIRTYFLAGS_OBJECT", "HDIRTYFLAGS_OBJECT_MATRIX",
    "HDIRTYFLAGS_OBJECT_HIERARCHY", "HDIRTYFLAGS_MATERIAL", "HDIRTYFLAGS_TAG",
    "HDIRTYFLAGS_ALL",
]


def dirty_snapshot(atom):
    result = {}
    for name in DIRTY_FLAG_NAMES:
        value = flag(name)
        if value is None:
            continue
        try:
            result[name.replace("DIRTYFLAGS_", "")] = atom.GetDirty(value)
        except Exception as exc:  # noqa
            result[name.replace("DIRTYFLAGS_", "")] = "ERR %s" % exc
    return result


def hdirty_snapshot(doc):
    result = {}
    for name in HDIRTY_FLAG_NAMES:
        value = flag(name)
        if value is None:
            continue
        try:
            result[name.replace("HDIRTYFLAGS_", "")] = doc.GetHDirty(value)
        except Exception as exc:  # noqa
            result[name.replace("HDIRTYFLAGS_", "")] = "ERR %s" % exc
    return result


def changed_keys(before, after):
    return sorted(k for k in after if before.get(k) != after.get(k))


def measure(label, doc, atoms, action):
    """Run action() and report which dirty counters changed on each atom and on the document."""
    before_atoms = [dirty_snapshot(a) for a in atoms]
    before_doc = hdirty_snapshot(doc)
    try:
        action()
        c4d.EventAdd()
    except Exception:
        out("  [%s] FAILED:" % label)
        out(traceback.format_exc())
        return
    after_atoms = [dirty_snapshot(a) for a in atoms]
    after_doc = hdirty_snapshot(doc)
    parts = []
    for index, atom in enumerate(atoms):
        try:
            name = atom.GetName()
        except Exception:  # noqa
            name = "atom%d" % index
        parts.append("%s:%s" % (name, ",".join(changed_keys(before_atoms[index], after_atoms[index])) or "-"))
    out("  %-28s | %s | doc:%s" % (label, " ".join(parts), ",".join(changed_keys(before_doc, after_doc)) or "-"))


def probe_versions():
    section("1. Versions and API availability")
    out("C4D version:", c4d.GetC4DVersion())
    out("Python:", sys.version.replace("\n", " "))
    checks = [
        ("BaseList2D.AddUniqueID", hasattr(c4d.BaseList2D, "AddUniqueID")),
        ("BaseList2D.FindUniqueID", hasattr(c4d.BaseList2D, "FindUniqueID")),
        ("BaseList2D.GetMarker", hasattr(c4d.BaseList2D, "GetMarker")),
        ("BaseObject.GetGUID", hasattr(c4d.BaseObject, "GetGUID")),
        ("C4DAtom.GetDirty", hasattr(c4d.C4DAtom, "GetDirty")),
        ("C4DAtom.GetHDirty", hasattr(c4d.C4DAtom, "GetHDirty")),
        ("c4d.SpecialEventAdd", hasattr(c4d, "SpecialEventAdd")),
        ("c4d.CTrack", hasattr(c4d, "CTrack")),
        ("c4d.MAXON_CREATOR_ID", hasattr(c4d, "MAXON_CREATOR_ID")),
        ("c4d.EVMSG_ASYNCEDITORMOVE", hasattr(c4d, "EVMSG_ASYNCEDITORMOVE")),
        ("c4d.threading.C4DThread", hasattr(getattr(c4d, "threading", None), "C4DThread")),
        ("c4d.utils.SendModelingCommand", hasattr(c4d.utils, "SendModelingCommand")),
        ("c4d.ID_MODELING_EXTRUDE_TOOL", hasattr(c4d, "ID_MODELING_EXTRUDE_TOOL")),
        ("c4d.NBIT_OHIDE", hasattr(c4d, "NBIT_OHIDE")),
        ("module socket", _can_import("socket")),
        ("module select", _can_import("select")),
        ("module zlib", _can_import("zlib")),
        ("module hmac", _can_import("hmac")),
        ("module json", _can_import("json")),
    ]
    for name, ok in checks:
        out("  %-34s %s" % (name, "yes" if ok else "NO"))
    out("  dirty flags present:", ", ".join(n for n in DIRTY_FLAG_NAMES if flag(n) is not None))
    out("  hdirty flags present:", ", ".join(n for n in HDIRTY_FLAG_NAMES if flag(n) is not None))


def _can_import(name):
    try:
        __import__(name)
        return True
    except Exception:  # noqa
        return False


def add_position_key(obj, frame, value, fps):
    desc_id = c4d.DescID(
        c4d.DescLevel(c4d.ID_BASEOBJECT_REL_POSITION, c4d.DTYPE_VECTOR, 0),
        c4d.DescLevel(c4d.VECTOR_X, c4d.DTYPE_REAL, 0),
    )
    track = obj.FindCTrack(desc_id)
    if track is None:
        track = c4d.CTrack(obj, desc_id)
        obj.InsertTrackSorted(track)
    curve = track.GetCurve()
    added = curve.AddKey(c4d.BaseTime(frame, fps))
    key = added["key"] if isinstance(added, dict) else added
    key.SetValue(curve, value)
    return track


def make_poly_cube(size=100.0):
    s = size / 2.0
    points = [c4d.Vector(x, y, z) for x in (-s, s) for y in (-s, s) for z in (-s, s)]
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    poly = c4d.PolygonObject(len(points), len(quads))
    for index, point in enumerate(points):
        poly.SetPoint(index, point)
    for index, (a, b, c, d) in enumerate(quads):
        poly.SetPolygon(index, c4d.CPolygon(a, b, c, d))
    poly.SetName("Poly")
    poly.Message(c4d.MSG_UPDATE)
    return poly


def probe_dirty(doc):
    section("2. Dirty counters per edit (object:changed-flags | doc:changed-hflags)")
    fps = doc.GetFps()
    cube = c4d.BaseObject(c4d.Ocube)
    cube.SetName("Cube")
    null = c4d.BaseObject(c4d.Onull)
    null.SetName("Null")
    doc.InsertObject(null)
    doc.InsertObject(cube)
    c4d.EventAdd()

    atoms = [cube, null]
    measure("move (SetAbsPos)", doc, atoms, lambda: cube.SetAbsPos(c4d.Vector(10, 20, 30)))
    measure("rotate (SetAbsRot)", doc, atoms, lambda: cube.SetAbsRot(c4d.Vector(0.5, 0, 0)))
    measure("param size (PRIM_CUBE_LEN)", doc, atoms, lambda: cube.SetParameter(
        c4d.DescID(c4d.PRIM_CUBE_LEN), c4d.Vector(300, 200, 100), c4d.DESCFLAGS_SET_0
        if hasattr(c4d, "DESCFLAGS_SET_0") else c4d.DESCFLAGS_SET_NONE))
    measure("param via [] (segments)", doc, atoms, lambda: cube.__setitem__(c4d.PRIM_CUBE_SUBX, 4))
    measure("rename", doc, atoms, lambda: cube.SetName("CubeRenamed"))
    measure("select (SetBit)", doc, atoms, lambda: cube.SetBit(c4d.BIT_ACTIVE))
    measure("reparent under Null", doc, atoms, lambda: (cube.Remove(), cube.InsertUnder(null)))
    measure("add phong tag", doc, atoms, lambda: cube.MakeTag(c4d.Tphong))

    holder = {}
    measure("add first keyframe", doc, atoms, lambda: holder.__setitem__("track", add_position_key(cube, 0, 0.0, fps)))
    track = holder.get("track")
    if track is not None:
        curve = track.GetCurve()
        atoms_anim = [cube, track, curve]
        out("  (track/curve counters below include the CTrack and CCurve atoms)")
        measure("add second keyframe", doc, atoms_anim, lambda: add_position_key(cube, 20, 100.0, fps))
        measure("change key value", doc, atoms_anim, lambda: curve.GetKey(1).SetValue(curve, 250.0))
        measure("move key in time", doc, atoms_anim, lambda: curve.GetKey(1).SetTime(curve, c4d.BaseTime(30, fps)))
        measure("change frame (SetTime)", doc, atoms_anim, lambda: doc.SetTime(c4d.BaseTime(15, fps)))

    # Modeling: build a polygon cube by hand (MAKEEDITABLE behaves differently across versions).
    poly = make_poly_cube()
    doc.InsertObject(poly)
    c4d.EventAdd()
    if poly is not None:
        out("  Poly points/polys:", poly.GetPointCount(), poly.GetPolygonCount())

        def move_point():
            pt = poly.GetPoint(0)
            poly.SetPoint(0, pt + c4d.Vector(5, 0, 0))
            poly.Message(c4d.MSG_UPDATE)

        measure("move one point", doc, [poly], move_point)

        def extrude():
            sel = poly.GetPolygonS()
            sel.DeselectAll()
            sel.Select(0)
            settings = c4d.BaseContainer()
            settings[c4d.MDATA_EXTRUDE_OFFSET] = 20.0
            c4d.utils.SendModelingCommand(
                command=c4d.ID_MODELING_EXTRUDE_TOOL, list=[poly],
                mode=c4d.MODELINGCOMMANDMODE_POLYGONSELECTION, bc=settings, doc=doc)

        if hasattr(c4d, "ID_MODELING_EXTRUDE_TOOL"):
            measure("extrude polygon 0", doc, [poly], extrude)
            out("  Poly points/polys after extrude:", poly.GetPointCount(), poly.GetPolygonCount())

    out("")
    out("  NOTE: programmatic edits can differ from interactive mouse edits.")
    out("  Interactive behaviour will be measured with the plugin's debug panel in Phase 2.")


def read_uid(atom):
    try:
        found = atom.FindUniqueID(TEST_APP_ID)
    except Exception as exc:  # noqa
        return "ERR %s" % exc
    if found is None:
        return None
    try:
        return bytes(bytearray(found))
    except Exception:  # noqa
        try:
            return bytes(found)
        except Exception:  # noqa
            return "%r" % (found,)


def _byteseq_variants(value):
    """Different ways to hand bytes to AddUniqueID; old versions (R19) refuse a plain str."""
    yield "bytes", value
    yield "bytearray", bytearray(value)
    storage = getattr(c4d, "storage", None)
    byteseq = getattr(storage, "ByteSeq", None)
    if byteseq is not None:
        try:
            seq = byteseq(None, len(value))
            for index, char in enumerate(bytearray(value)):
                seq[index] = chr(char) if sys.version_info[0] == 2 else char
            yield "ByteSeq(None, n) + item assignment", seq
        except Exception as exc:  # noqa
            out("  ByteSeq(None, n) not usable:", exc)
        try:
            yield "ByteSeq(bytes, n)", byteseq(value, len(value))
        except Exception as exc:  # noqa
            out("  ByteSeq(bytes, n) not usable:", exc)


def write_uid(obj, value):
    """Try AddUniqueID with each byte container until one round-trips. Returns the method name."""
    for name, payload in _byteseq_variants(value):
        try:
            obj.AddUniqueID(TEST_APP_ID, payload)
        except Exception as exc:  # noqa
            out("  AddUniqueID with %s failed: %s" % (name, exc))
            continue
        ok = read_uid(obj) == value
        out("  AddUniqueID with %s works, reads back equal: %s" % (name, ok))
        if not ok:
            found = obj.FindUniqueID(TEST_APP_ID)
            out("    FindUniqueID returned type %s: %r" % (type(found).__name__, read_uid(obj)))
        if ok:
            return name
    return None


def probe_container_id(doc):
    """Fallback idea: keep the uuid as a string in the object's BaseContainer."""
    obj = c4d.BaseObject(c4d.Onull)
    obj.SetName("ContainerIdTest")
    doc.InsertObject(obj)
    value = uuid.uuid4().hex
    obj.GetDataInstance().SetString(TEST_APP_ID, value)
    clone = obj.GetClone()
    out("  container id readable:", obj.GetDataInstance().GetString(TEST_APP_ID) == value)
    out("  container id copied to clone:", clone.GetDataInstance().GetString(TEST_APP_ID) == value)


def probe_unique_id(doc):
    section("3. AddUniqueID behaviour (clone / save / reload)")
    try:
        probe_container_id(doc)
    except Exception:
        out("  container id test failed:")
        out(traceback.format_exc())
    if not hasattr(c4d.BaseList2D, "AddUniqueID"):
        out("  AddUniqueID NOT available in this version -> fallback needed")
        return
    obj = c4d.BaseObject(c4d.Onull)
    obj.SetName("UidTest")
    doc.InsertObject(obj)
    value = uuid.uuid4().bytes
    method = write_uid(obj, value)
    if method is None:
        out("  AddUniqueID: no working byte container found")
        return
    out("  original matches written bytes:", read_uid(obj) == value)

    clone = obj.GetClone()
    clone.SetName("UidTestClone")
    doc.InsertObject(clone)
    out("  clone has same id:", read_uid(clone) == value)
    out("  clone id value:", "none" if read_uid(clone) is None else "other id")
    out("  clone unique id count:", clone.GetUniqueIDCount())

    try:
        write_uid(obj, uuid.uuid4().bytes)
        out("  overwrite with new id works:", read_uid(obj) != value)
        out("  unique id count after overwrite:", obj.GetUniqueIDCount())
    except Exception as exc:  # noqa
        out("  overwrite failed:", exc)

    path = os.path.join(tempfile.gettempdir(), "c4d_collab_spike_uid.c4d")
    if sys.version_info[0] == 2:
        path = path.encode("utf-8")  # R17-R22 c4d API wants native str, not unicode
    expected = read_uid(obj)
    try:
        ok = documents.SaveDocument(doc, path, c4d.SAVEDOCUMENTFLAGS_DONTADDTORECENTLIST, c4d.FORMAT_C4DEXPORT)
        out("  saved:", ok, path)
        loaded = documents.LoadDocument(path, c4d.SCENEFILTER_OBJECTS)
        if loaded is None:
            out("  reload FAILED")
            return
        reloaded = loaded.SearchObject("UidTest")
        out("  id survives save+reload:", reloaded is not None and read_uid(reloaded) == expected)
        documents.KillDocument(loaded)
    except Exception:
        out("  save/reload failed:")
        out(traceback.format_exc())
    finally:
        try:
            os.remove(path)
        except Exception:  # noqa
            pass


def probe_performance(doc):
    section("4. Dirty scan performance")
    data_flag = flag("DIRTYFLAGS_DATA")
    matrix_flag = flag("DIRTYFLAGS_MATRIX")
    clock = getattr(time, "perf_counter", None) or getattr(time, "clock", time.time)
    for count in (1000, 10000):
        root = c4d.BaseObject(c4d.Onull)
        root.SetName("PerfRoot%d" % count)
        doc.InsertObject(root)
        children = []
        for _ in range(count):
            child = c4d.BaseObject(c4d.Onull)
            child.InsertUnder(root)
            children.append(child)
        start = clock()
        total = 0
        visited = 0
        obj = root.GetDown()
        while obj is not None:
            total += obj.GetDirty(data_flag) + obj.GetDirty(matrix_flag)
            visited += 1
            obj = obj.GetNext()
        elapsed = (clock() - start) * 1000.0
        out("  %6d objects (visited %d): %.2f ms per full scan" % (count, visited, elapsed))
        root.Remove()


def main():
    section("C4D Collab spike probe")
    original = documents.GetActiveDocument()
    doc = documents.BaseDocument()
    doc.SetDocumentName("c4d_collab_spike")
    documents.InsertBaseDocument(doc)
    documents.SetActiveDocument(doc)
    try:
        for step in (probe_versions, lambda: probe_dirty(doc), lambda: probe_unique_id(doc),
                     lambda: probe_performance(doc)):
            try:
                step()
            except Exception:
                out("STEP FAILED:")
                out(traceback.format_exc())
    finally:
        write_report()
        # C4D closes an empty, unmodified "Untitled" document when another one is inserted,
        # so the original document may no longer exist here.
        try:
            documents.KillDocument(doc)
            if original is not None and original.IsAlive():
                documents.SetActiveDocument(original)
        except Exception:
            traceback.print_exc()
        c4d.EventAdd()


def write_report():
    report = os.path.join(os.path.expanduser("~"), "c4d_collab_spike_%s.txt" % c4d.GetC4DVersion())
    try:
        with open(report, "wb") as handle:
            handle.write("\n".join(LINES).encode("utf-8"))
        print("\nReport written to:", report)
    except Exception:
        print("Could not write report file:")
        traceback.print_exc()


if __name__ == "__main__":
    main()
