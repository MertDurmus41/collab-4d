# -*- coding: utf-8 -*-
"""Phase 8 probe: Pose Morph tag (CAPoseMorphTag) API behaviour (run with c4dpy or Script Manager).

Answers: which CAPoseMorphTag / CAMorph / CAMorphNode methods exist, how morph point data is read
and written (absolute vs relative mode, expand/collapse), where the strength sliders live in the
tag description, which dirty counters move for strength / point edits, whether a morph rebuilt in a
second tag deforms the mesh the same way, and how long reading big morphs takes.
The report also goes to C4D_COLLAB_PROBE_OUT or, if unset, %TEMP%/c4d_collab_probe_posemorph.txt.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import io
import os
import sys
import tempfile
import time
import traceback

import c4d
from c4d import documents

LINES = []


def out(*parts):
    text = " ".join("%s" % (p,) for p in parts)
    LINES.append(text)
    print(text)


def attempt(label, fn):
    try:
        result = fn()
        out("  OK  ", label, "->", repr(result)[:300])
        return result
    except BaseException as exc:  # c4d raises bare BaseException for some errors
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        out("  FAIL", label, "->", "%s: %s" % (type(exc).__name__, exc))
        return None


def flag(name):
    return getattr(c4d, name, None)


def methods(cls, *names):
    return " ".join("%s=%s" % (n, hasattr(cls, n)) for n in names)


DIRTY = ["DATA", "MATRIX", "CHILDREN", "CACHE", "SELECT", "DESCRIPTION"]


def dirty(atom):
    return tuple(atom.GetDirty(flag("DIRTYFLAGS_" + n)) if flag("DIRTYFLAGS_" + n) is not None else None
                 for n in DIRTY)


def hdirty(doc):
    names = ("OBJECT", "OBJECT_MATRIX", "TAG", "ANIMATION", "ALL")
    return dict((n, doc.GetHDirty(flag("HDIRTYFLAGS_" + n))) for n in names if flag("HDIRTYFLAGS_" + n) is not None)


def measure(label, doc, atoms, action):
    before = [dirty(a) for a in atoms]
    doc_before = hdirty(doc)
    result = action()
    after = [dirty(a) for a in atoms]
    doc_after = hdirty(doc)
    parts = []
    for name, b, a in zip(("obj", "tag"), before, after):
        parts.append("%s:%s" % (name, ",".join(DIRTY[i] for i, (x, y) in enumerate(zip(b, a)) if x != y) or "-"))
    doc_changes = [k for k in sorted(doc_after) if doc_after[k] != doc_before.get(k)]
    out("  %-38s %s | doc:%s" % (label, " ".join(parts), ",".join(doc_changes) or "-"))
    return result


def make_grid(doc, columns, rows, size=10.0, name="Face"):
    points = []
    for y in range(rows + 1):
        for x in range(columns + 1):
            points.append(c4d.Vector(x * size, y * size, 0))
    polys = []
    for y in range(rows):
        for x in range(columns):
            a = y * (columns + 1) + x
            polys.append(c4d.CPolygon(a, a + 1, a + columns + 2, a + columns + 1))
    mesh = c4d.PolygonObject(len(points), len(polys))
    mesh.SetAllPoints(points)
    for index, poly in enumerate(polys):
        mesh.SetPolygon(index, poly)
    mesh.SetName(name)
    mesh.Message(c4d.MSG_UPDATE)
    doc.InsertObject(mesh)
    return mesh


def evaluate(doc):
    doc.ExecutePasses(None, True, True, True, flag("BUILDFLAGS_NONE") or 0)


def deformed(obj):
    cache = obj.GetDeformCache()
    source = cache if cache is not None else obj
    return [(p.x, p.y, p.z) for p in source.GetAllPoints()], cache is not None


def node_info(node):
    info = {}
    for name in ("GetPointCount", "GetTangentCount", "GetVertexMapCount", "GetUVCount", "GetWeightMapCount",
                 "GetParamCount", "GetInfo"):
        if hasattr(node, name):
            try:
                info[name] = getattr(node, name)()
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                info[name] = "ERR %s" % (exc,)
    if hasattr(node, "GetLink"):
        try:
            link = node.GetLink(node.GetDocument() if hasattr(node, "GetDocument") else None)
            info["link"] = link.GetName() if link is not None else None
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            info["link"] = "ERR %s" % (exc,)
    return info


def morph_points(morph, doc, tag):
    """Points of the first node, with the morph expanded to absolute mode first."""
    node = morph.GetFirst()
    if node is None:
        return None
    count = node.GetPointCount()
    return [(lambda v: (v.x, v.y, v.z))(node.GetPoint(i)) for i in range(count)]


def build_tag(doc, mesh):
    """The documented way to create a pose morph: points mode, base + one morph, points written in ABS mode."""
    tag = c4d.BaseTag(c4d.Tposemorph)
    mesh.InsertTag(tag)
    attempt("tag[ID_CA_POSE_POINTS] = True", lambda: tag.__setitem__(c4d.ID_CA_POSE_POINTS, True))
    attempt("ExitEdit(doc, True)", lambda: tag.ExitEdit(doc, True))
    attempt("InitMorphs()", lambda: tag.InitMorphs())
    base = attempt("AddMorph() (base)", lambda: tag.AddMorph())
    attempt("base.Store(doc, tag, POINTS)", lambda: base.Store(doc, tag, c4d.CAMORPH_DATA_FLAGS_POINTS))
    morph = attempt("AddMorph() (smile)", lambda: tag.AddMorph())
    attempt("morph.SetName('smile')", lambda: morph.SetName("smile"))
    attempt("morph.Store(doc, tag, POINTS)", lambda: morph.Store(doc, tag, c4d.CAMORPH_DATA_FLAGS_POINTS))
    return tag, base, morph


def main():
    out("C4D", c4d.GetC4DVersion(), "Python", sys.version.split()[0])
    doc = documents.BaseDocument()
    documents.InsertBaseDocument(doc)
    documents.SetActiveDocument(doc)
    section = lambda t: out("\n== " + t)

    section("API surface")
    out("Tposemorph:", flag("Tposemorph"), "CAPoseMorphTag:", hasattr(c4d.modules.character, "CAPoseMorphTag")
        if hasattr(c4d, "modules") and hasattr(c4d.modules, "character") else "no c4d.modules.character")
    character = getattr(getattr(c4d, "modules", None), "character", None)
    tag_cls = getattr(character, "CAPoseMorphTag", None)
    morph_cls = getattr(character, "CAMorph", None)
    node_cls = getattr(character, "CAMorphNode", None)
    if tag_cls is not None:
        out("CAPoseMorphTag:", methods(tag_cls, "GetMorphCount", "GetMorph", "AddMorph", "RemoveMorph", "GetMorphID",
                                       "GetMorphIndex", "GetActiveMorph", "GetActiveMorphIndex", "SetActiveMorphIndex",
                                       "GetMorphBase", "GetMode", "SetMode", "ExitEdit", "InitMorphs", "UpdateMorphs",
                                       "GetPrecision", "SetPrecision", "GetMorphTarget"))
    if morph_cls is not None:
        out("CAMorph:", methods(morph_cls, "GetName", "SetName", "GetID", "Store", "Apply", "SetMode", "GetFirst",
                                "Find", "FindIndex", "GetNodeCount", "GetNode", "GetTarget", "SetTarget",
                                "GetStrength", "SetStrength", "CopyFrom"))
    if node_cls is not None:
        out("CAMorphNode:", methods(node_cls, "GetPointCount", "GetPoint", "SetPoint", "SetPointCount",
                                    "GetTangentCount", "GetVertexMapCount", "GetUVCount", "GetWeightMapCount",
                                    "GetParamCount", "GetInfo", "GetLink", "GetNext", "GetDown", "GetUp",
                                    "GetPSRMatrix", "SetPSRMatrix", "GetPosition", "SetPosition"))
    consts = ("CAMORPH_DATA_FLAGS_POINTS", "CAMORPH_DATA_FLAGS_ALL", "CAMORPH_MODE_ABS", "CAMORPH_MODE_REL",
              "CAMORPH_MODE_AUTO", "CAMORPH_MODE_CORRECTIONAL", "CAMORPH_MODE_FLAGS_ALL",
              "CAMORPH_MODE_FLAGS_EXPAND", "CAMORPH_MODE_FLAGS_COLLAPSE", "CAMORPH_COPY_FLAGS_NONE",
              "ID_CA_POSE_POINTS", "ID_CA_POSE_MODE", "ID_CA_POSE_MODE_EDIT", "ID_CA_POSE_MODE_ANIMATE",
              "ID_CA_POSE_MIXING", "ID_CA_POSE_MIXING_ABS", "ID_CA_POSE_MIXING_REL", "ID_CA_POSE_ANIMATE_DATA",
              "ID_CA_POSE_HIERARCHY", "ID_CA_POSE_STRENGTH", "ID_CA_POSE_TARGET", "ID_CA_POSE_ANIMATE_CNT",
              "ID_CA_POSE_ANIMATE_OFFSET", "ID_CA_POSE_ANIMATE_STRING")
    out("consts:", dict((n, flag(n)) for n in consts))
    if tag_cls is None or flag("Tposemorph") is None:
        out("No CAPoseMorphTag in this version, stopping.")
        return

    section("Build: base + one morph")
    mesh = make_grid(doc, 3, 3)
    tag, base, morph = build_tag(doc, mesh)
    out("  morph count", tag.GetMorphCount(), "mode param", attempt("tag[ID_CA_POSE_MODE]", lambda: tag[c4d.ID_CA_POSE_MODE]))
    out("  mixing", attempt("tag[ID_CA_POSE_MIXING]", lambda: tag[c4d.ID_CA_POSE_MIXING]))
    for index in range(tag.GetMorphCount()):
        m = tag.GetMorph(index)
        out("  morph", index, "name", attempt("GetName", m.GetName),
            "id", attempt("GetID", m.GetID) if hasattr(m, "GetID") else "-",
            "strength DescID", attempt("GetMorphID(%d)" % index, lambda: tag.GetMorphID(index)))
        node = attempt("GetFirst()", m.GetFirst)
        if node is not None:
            out("    first node:", node_info(node), "down:", node.GetDown() is not None, "next:", node.GetNext() is not None)

    section("Write points: ABS expand -> SetPoint -> collapse")
    attempt("morph.SetMode(ALL|EXPAND, ABS)", lambda: morph.SetMode(
        doc, tag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_EXPAND, c4d.CAMORPH_MODE_ABS))
    node = morph.GetFirst()
    out("  node after expand:", node_info(node) if node is not None else None)
    if node is not None:
        attempt("node.SetPoint(5, +50 z)", lambda: node.SetPoint(5, node.GetPoint(5) + c4d.Vector(0, 0, 50)))
        out("  ABS points[0:8]:", morph_points(morph, doc, tag)[:8])
    attempt("morph.SetMode(ALL|COLLAPSE, AUTO)", lambda: morph.SetMode(
        doc, tag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_COLLAPSE, c4d.CAMORPH_MODE_AUTO))
    out("  node after collapse:", node_info(morph.GetFirst()) if morph.GetFirst() is not None else None)
    attempt("tag.UpdateMorphs()", lambda: tag.UpdateMorphs())

    section("REL read (what is stored when collapsed)")
    attempt("morph.SetMode(ALL|EXPAND, REL)", lambda: morph.SetMode(
        doc, tag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_EXPAND, c4d.CAMORPH_MODE_REL))
    rel = morph_points(morph, doc, tag)
    out("  REL points (non zero):", [(i, p) for i, p in enumerate(rel or []) if any(abs(c) > 1e-6 for c in p)])
    attempt("morph.SetMode(ALL|COLLAPSE, AUTO)", lambda: morph.SetMode(
        doc, tag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_COLLAPSE, c4d.CAMORPH_MODE_AUTO))
    attempt("tag.UpdateMorphs()", lambda: tag.UpdateMorphs())

    section("Strength slider and deformation")
    attempt("tag[ID_CA_POSE_MODE] = ANIMATE", lambda: tag.__setitem__(c4d.ID_CA_POSE_MODE, c4d.ID_CA_POSE_MODE_ANIMATE))
    strength_id = tag.GetMorphID(1)
    attempt("tag[GetMorphID(1)] = 1.0", lambda: tag.__setitem__(strength_id, 1.0))
    evaluate(doc)
    points, has_cache = deformed(mesh)
    out("  deform cache:", has_cache, "point 5:", points[5])
    attempt("tag[GetMorphID(1)] = 0.5", lambda: tag.__setitem__(strength_id, 0.5))
    evaluate(doc)
    out("  strength 0.5 point 5:", deformed(mesh)[0][5])
    attempt("morph.GetStrength()", lambda: morph.GetStrength() if hasattr(morph, "GetStrength") else "n/a")

    section("Strength sliders in the description")
    found = []
    for bc, did, group in tag.GetDescription(flag("DESCFLAGS_DESC_NONE") or 0):
        if did.GetDepth() > 0:
            first = did[0].id
            if first >= 1000:
                found.append((first, did[did.GetDepth() - 1].dtype, bc[c4d.DESC_NAME]))
    out("  description ids >= 1000:", found[:40])
    out("  GetMorphID(0), (1):", attempt("ids", lambda: (str(tag.GetMorphID(0)), str(tag.GetMorphID(1)))))

    section("Reading tag parameters other ways")
    flags_get = flag("DESCFLAGS_GET_NONE") or 0
    for pid in (1001, 1009, 1017, 1027, 1003):
        attempt("GetParameter(%d)" % pid, lambda: tag.GetParameter(c4d.DescID(c4d.DescLevel(pid)), flags_get))
        attempt("GetDataInstance()[%d]" % pid, lambda: tag.GetDataInstance()[pid])
    attempt("GetParameter(strength 1)", lambda: tag.GetParameter(strength_id, flags_get))
    attempt("GetActiveMorphIndex()", lambda: tag.GetActiveMorphIndex())
    attempt("GetMode()", lambda: tag.GetMode())

    section("Collapsed morph read (no expand): cheap fingerprint?")
    first = morph.GetFirst()
    attempt("collapsed count", lambda: first.GetPointCount())
    attempt("collapsed points", lambda: [(lambda v: (v.x, v.y, v.z))(first.GetPoint(i)) for i in range(first.GetPointCount())])

    section("Edit mode: does the mesh itself hold the active pose?")
    probe_mesh = make_grid(doc, 3, 3, name="EditProbe")
    etag, ebase, emorph = build_tag(doc, probe_mesh)
    emorph.SetMode(doc, etag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_EXPAND, c4d.CAMORPH_MODE_REL)
    emorph.GetFirst().SetPoint(5, c4d.Vector(0, 0, 50))
    emorph.SetMode(doc, etag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_COLLAPSE, c4d.CAMORPH_MODE_AUTO)
    etag.UpdateMorphs()
    etag[c4d.ID_CA_POSE_MODE] = c4d.ID_CA_POSE_MODE_EDIT
    out("  GetMode() in edit:", attempt("GetMode", etag.GetMode))
    measure("SetActiveMorphIndex(1)", doc, [probe_mesh, etag], lambda: etag.SetActiveMorphIndex(1))
    evaluate(doc)
    out("  mesh point 5 (own points) with pose 1 active:", (lambda v: (v.x, v.y, v.z))(probe_mesh.GetPoint(5)))
    out("  deformed point 5:", deformed(probe_mesh)[0][5])
    measure("mesh.SetPoint(6) in edit, pose 1", doc, [probe_mesh, etag], lambda: (
        probe_mesh.SetPoint(6, probe_mesh.GetPoint(6) + c4d.Vector(0, 0, 9)), probe_mesh.Message(c4d.MSG_UPDATE)))
    evaluate(doc)
    measure("SetActiveMorphIndex(0)", doc, [probe_mesh, etag], lambda: etag.SetActiveMorphIndex(0))
    evaluate(doc)
    out("  mesh points 5,6 after switching to base:", [(lambda v: (v.x, v.y, v.z))(probe_mesh.GetPoint(i)) for i in (5, 6)])
    emorph.SetMode(doc, etag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_EXPAND, c4d.CAMORPH_MODE_REL)
    out("  pose 1 REL non zero after edit:", [(i, (lambda v: (v.x, v.y, v.z))(emorph.GetFirst().GetPoint(i)))
                                              for i in range(emorph.GetFirst().GetPointCount())
                                              if emorph.GetFirst().GetPoint(i).GetLength() > 1e-6])
    emorph.SetMode(doc, etag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_COLLAPSE, c4d.CAMORPH_MODE_AUTO)
    attempt("ExitEdit(doc, False)", lambda: etag.ExitEdit(doc, False))

    section("Animate mode writes the mesh's own points: where is the base?")
    amesh = make_grid(doc, 3, 3, name="AnimProbe")
    atag, abase, amorph = build_tag(doc, amesh)
    amorph.SetMode(doc, atag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_EXPAND, c4d.CAMORPH_MODE_REL)
    amorph.GetFirst().SetPoint(5, c4d.Vector(0, 0, 50))
    amorph.SetMode(doc, atag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_COLLAPSE, c4d.CAMORPH_MODE_AUTO)
    atag.UpdateMorphs()
    atag[c4d.ID_CA_POSE_MODE] = c4d.ID_CA_POSE_MODE_ANIMATE
    atag[atag.GetMorphID(1)] = 1.0
    evaluate(doc)
    out("  own point 5 after evaluation (strength 1):", (lambda v: (v.x, v.y, v.z))(amesh.GetPoint(5)),
        "deform cache:", amesh.GetDeformCache() is not None)
    attempt("base expand ABS", lambda: abase.SetMode(
        doc, atag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_EXPAND, c4d.CAMORPH_MODE_ABS))
    bnode = abase.GetFirst()
    out("  base ABS count, point 5, point 15:", bnode.GetPointCount(),
        (lambda v: (v.x, v.y, v.z))(bnode.GetPoint(5)), (lambda v: (v.x, v.y, v.z))(bnode.GetPoint(15)))
    abase.SetMode(doc, atag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_COLLAPSE, c4d.CAMORPH_MODE_AUTO)
    atag[atag.GetMorphID(1)] = 0.0
    evaluate(doc)
    out("  own point 5 at strength 0:", (lambda v: (v.x, v.y, v.z))(amesh.GetPoint(5)))
    # a new base arrives: write the mesh, Store the base pose again, then animate
    amesh.SetPoint(15, c4d.Vector(30, 30, 100))
    amesh.Message(c4d.MSG_UPDATE)
    attempt("base.Store after mesh edit", lambda: abase.Store(doc, atag, c4d.CAMORPH_DATA_FLAGS_POINTS))
    atag.UpdateMorphs()
    atag[atag.GetMorphID(1)] = 1.0
    evaluate(doc)
    out("  after new base + strength 1: point 5, point 15:", (lambda v: (v.x, v.y, v.z))(amesh.GetPoint(5)),
        (lambda v: (v.x, v.y, v.z))(amesh.GetPoint(15)))
    # and without Store: does the tag overwrite a new mesh point?
    amesh.SetPoint(14, c4d.Vector(20, 30, 77))
    amesh.Message(c4d.MSG_UPDATE)
    evaluate(doc)
    out("  mesh edit WITHOUT Store, after evaluation, point 14:", (lambda v: (v.x, v.y, v.z))(amesh.GetPoint(14)))

    section("Dirty counters")
    measure("strength 0.5 -> 0.8", doc, [mesh, tag], lambda: tag.__setitem__(strength_id, 0.8))

    def edit_point():
        morph.SetMode(doc, tag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_EXPAND, c4d.CAMORPH_MODE_ABS)
        n = morph.GetFirst()
        n.SetPoint(6, n.GetPoint(6) + c4d.Vector(0, 7, 0))
        morph.SetMode(doc, tag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_COLLAPSE, c4d.CAMORPH_MODE_AUTO)
        tag.UpdateMorphs()
    measure("morph point edit (expand/set/collapse)", doc, [mesh, tag], edit_point)
    measure("expand+collapse only (read)", doc, [mesh, tag], lambda: (
        morph.SetMode(doc, tag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_EXPAND, c4d.CAMORPH_MODE_REL),
        morph.SetMode(doc, tag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_COLLAPSE, c4d.CAMORPH_MODE_AUTO)))
    measure("morph.SetName('grin')", doc, [mesh, tag], lambda: morph.SetName("grin"))
    measure("AddMorph + Store", doc, [mesh, tag], lambda: tag.AddMorph().Store(doc, tag, c4d.CAMORPH_DATA_FLAGS_POINTS))
    measure("RemoveMorph(2)", doc, [mesh, tag], lambda: tag.RemoveMorph(2))
    measure("evaluate (ExecutePasses)", doc, [mesh, tag], lambda: evaluate(doc))

    section("Round trip: rebuild in a second tag on a copy of the mesh")
    morph.SetMode(doc, tag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_EXPAND, c4d.CAMORPH_MODE_REL)
    rel = morph_points(morph, doc, tag)
    morph.SetMode(doc, tag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_COLLAPSE, c4d.CAMORPH_MODE_AUTO)
    tag.UpdateMorphs()
    copy = make_grid(doc, 3, 3, name="Copy")
    tag2, base2, morph2 = build_tag(doc, copy)
    morph2.SetName(morph.GetName())
    attempt("morph2 expand REL", lambda: morph2.SetMode(
        doc, tag2, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_EXPAND, c4d.CAMORPH_MODE_REL))
    node2 = morph2.GetFirst()
    out("  node2:", node_info(node2) if node2 is not None else None)
    if node2 is not None and rel is not None:
        attempt("write REL points", lambda: [node2.SetPoint(i, c4d.Vector(*p)) for i, p in enumerate(rel)] and len(rel))
    attempt("morph2 collapse", lambda: morph2.SetMode(
        doc, tag2, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_COLLAPSE, c4d.CAMORPH_MODE_AUTO))
    attempt("tag2.UpdateMorphs()", lambda: tag2.UpdateMorphs())
    tag2[c4d.ID_CA_POSE_MODE] = c4d.ID_CA_POSE_MODE_ANIMATE
    tag2[tag2.GetMorphID(1)] = tag[strength_id]
    evaluate(doc)
    a, b = deformed(mesh)[0], deformed(copy)[0]
    diff = max(abs(x - y) for pa, pb in zip(a, b) for x, y in zip(pa, pb)) if a and b else None
    out("  max deformed point difference original vs rebuilt:", diff)

    section("Write REL into a collapsed morph directly (no expand)")
    morph3 = tag2.AddMorph()
    morph3.Store(doc, tag2, c4d.CAMORPH_DATA_FLAGS_POINTS)
    out("  collapsed node:", node_info(morph3.GetFirst()) if morph3.GetFirst() is not None else None)

    section("Performance (10k points, 20 morphs)")
    clock = getattr(time, "perf_counter", None) or time.clock
    big = make_grid(doc, 100, 100, name="Big")
    big_tag = c4d.BaseTag(c4d.Tposemorph)
    big.InsertTag(big_tag)
    big_tag[c4d.ID_CA_POSE_POINTS] = True
    big_tag.ExitEdit(doc, True)
    big_tag.InitMorphs()
    big_tag.AddMorph().Store(doc, big_tag, c4d.CAMORPH_DATA_FLAGS_POINTS)
    for i in range(20):
        big_tag.AddMorph().Store(doc, big_tag, c4d.CAMORPH_DATA_FLAGS_POINTS)
    start = clock()
    total = 0
    for i in range(1, big_tag.GetMorphCount()):
        m = big_tag.GetMorph(i)
        m.SetMode(doc, big_tag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_EXPAND, c4d.CAMORPH_MODE_REL)
        n = m.GetFirst()
        flat = []
        for k in range(n.GetPointCount()):
            v = n.GetPoint(k)
            flat.extend((v.x, v.y, v.z))
        total += len(flat)
        m.SetMode(doc, big_tag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_COLLAPSE, c4d.CAMORPH_MODE_AUTO)
    out("  read 20 morphs x %d points (expand/read/collapse): %.1f ms" % (total // 60, (clock() - start) * 1000))
    start = clock()
    for i in range(1, big_tag.GetMorphCount()):
        m = big_tag.GetMorph(i)
        m.SetMode(doc, big_tag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_EXPAND, c4d.CAMORPH_MODE_REL)
        n = m.GetFirst()
        for k in range(n.GetPointCount()):
            n.SetPoint(k, c4d.Vector(0, k % 3, 0))
        m.SetMode(doc, big_tag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_COLLAPSE, c4d.CAMORPH_MODE_AUTO)
    big_tag.UpdateMorphs()
    out("  write 20 morphs: %.1f ms" % ((clock() - start) * 1000))


def write_report():
    path = os.environ.get("C4D_COLLAB_PROBE_OUT") or os.path.join(tempfile.gettempdir(), "c4d_collab_probe_posemorph.txt")
    out("Report written to", path)
    with io.open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join("%s" % l for l in LINES))


try:
    main()
except BaseException:
    out(traceback.format_exc())
write_report()
