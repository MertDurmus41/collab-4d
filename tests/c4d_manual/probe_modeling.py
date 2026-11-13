# -*- coding: utf-8 -*-
"""Phase 6 probe: modeling API behaviour (run with c4dpy or Script Manager).

Answers: how are selection tags, UVW tags and splines read/written, what does
ResizeObject do to existing tags, which dirty counters move for modeling edits,
and how long does reading big meshes take.
Set C4D_COLLAB_PROBE_OUT to also write the report to a file.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import io
import os
import sys
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
    except Exception as exc:
        out("  FAIL", label, "->", "%s: %s" % (type(exc).__name__, exc))
        return None


def methods(cls, *names):
    return " ".join("%s=%s" % (n, hasattr(cls, n)) for n in names)


def make_quad_mesh(columns, rows, size=10.0):
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
    mesh.Message(c4d.MSG_UPDATE)
    return mesh


def main():
    out("C4D", c4d.GetC4DVersion(), "Python", sys.version.split()[0])
    doc = documents.BaseDocument()
    documents.InsertBaseDocument(doc)
    documents.SetActiveDocument(doc)

    section = lambda t: out("\n== " + t)

    section("API surface")
    out("PolygonObject:", methods(c4d.PolygonObject, "GetAllPoints", "SetAllPoints", "GetAllPolygons", "SetAllPolygons",
                                  "ResizeObject", "GetPointS", "GetPolygonS", "GetEdgeS", "GetPointCount",
                                  "GetPolygonCount", "GetSegmentCount", "GetPhongBreak", "GetNgonBase"))
    out("SplineObject:", methods(c4d.SplineObject, "GetAllPoints", "SetAllPoints", "ResizeObject", "GetSegmentCount",
                                 "GetSegment", "SetSegment", "GetTangent", "SetTangent", "GetAllSegments",
                                 "GetTangentCount", "GetAllTangents", "SetAllTangents", "GetPointS"))
    out("BaseSelect:", methods(c4d.BaseSelect, "GetAll", "SetAll", "DeselectAll", "Select", "Deselect", "IsSelected",
                               "GetCount", "GetSegment", "GetRange", "CopyTo", "FromArray", "ToggleAll", "SelectAll"))
    out("UVWTag:", methods(c4d.UVWTag, "GetDataCount", "GetSlow", "SetSlow", "GetDataAddressR", "GetDataAddressW",
                           "SetDataCount", "Resize", "CopyTo"))
    out("tag consts:", {n: getattr(c4d, n, None) for n in ("Tpolygonselection", "Tpointselection", "Tedgeselection",
                                                          "Tuvw", "Tphong", "Tnormal", "Tvertexmap")})
    out("obj consts:", {n: getattr(c4d, n, None) for n in ("Ospline", "Opolygon", "Osplinenside", "Ospline4side",
                                                          "SPLINETYPE_LINEAR", "SPLINETYPE_BEZIER", "SPLINEOBJECT_TYPE",
                                                          "SPLINEOBJECT_CLOSED")})

    section("Selection tags")
    mesh = make_quad_mesh(4, 4)
    doc.InsertObject(mesh)
    out("mesh points/polys", mesh.GetPointCount(), mesh.GetPolygonCount())
    poly_tag = attempt("MakeTag Tpolygonselection", lambda: mesh.MakeTag(c4d.Tpolygonselection))
    if poly_tag is not None:
        poly_tag.SetName("PolySel")
        select = poly_tag.GetBaseSelect()
        for index in (0, 1, 2, 5):
            select.Select(index)
        out("  select count", select.GetCount(), "segments", select.GetSegments() if hasattr(select, "GetSegments") else "n/a")
        attempt("GetAll(polygon count)", lambda: list(select.GetAll(mesh.GetPolygonCount())))
        attempt("GetRange(0, count)", lambda: select.GetRange(0, mesh.GetPolygonCount())
                if hasattr(select, "GetRange") else "no GetRange")
        attempt("tag data container name", lambda: poly_tag.GetName())
        fresh = c4d.BaseTag(c4d.Tpolygonselection)
        mesh.InsertTag(fresh)
        sel2 = fresh.GetBaseSelect()
        attempt("BaseTag(Tpolygonselection)+InsertTag, Select(3)", lambda: (sel2.Select(3), sel2.GetCount())[1])
        attempt("SetAll from list", lambda: (sel2.DeselectAll(), sel2.SetAll([1, 0, 1, 0] * 4), sel2.GetCount())[2]
                if hasattr(sel2, "SetAll") else "no SetAll")
        out("  selection after SetAll:", attempt("indices", lambda: [i for i in range(16) if sel2.IsSelected(i)]))
    point_tag = attempt("MakeTag Tpointselection", lambda: mesh.MakeTag(c4d.Tpointselection))
    if point_tag is not None:
        attempt("point select 0..3", lambda: [point_tag.GetBaseSelect().Select(i) for i in range(4)])
        attempt("point select count", lambda: point_tag.GetBaseSelect().GetCount())
    edge_tag = attempt("MakeTag Tedgeselection", lambda: mesh.MakeTag(c4d.Tedgeselection))
    if edge_tag is not None:
        attempt("edge select 0,5", lambda: [edge_tag.GetBaseSelect().Select(i) for i in (0, 5)])
        attempt("edge select count", lambda: edge_tag.GetBaseSelect().GetCount())
        attempt("edge count of mesh", lambda: mesh.GetPolygonCount() * 4)

    section("UVW tag")
    uvw = attempt("MakeTag Tuvw", lambda: mesh.MakeTag(c4d.Tuvw))
    if uvw is not None:
        out("  data count after MakeTag:", uvw.GetDataCount(), "polys", mesh.GetPolygonCount())
        attempt("GetSlow(0)", lambda: {k: (v.x, v.y) for k, v in uvw.GetSlow(0).items()})
        attempt("SetSlow(0)", lambda: uvw.SetSlow(0, c4d.Vector(0.1, 0.2, 0), c4d.Vector(0.3, 0.4, 0),
                                                 c4d.Vector(0.5, 0.6, 0), c4d.Vector(0.7, 0.8, 0)))
        attempt("GetSlow(0) after set", lambda: {k: (v.x, v.y) for k, v in uvw.GetSlow(0).items()})
        loose = c4d.BaseTag(c4d.Tuvw)
        out("  loose UVWTag count before insert:", loose.GetDataCount())
        other = make_quad_mesh(2, 2)
        doc.InsertObject(other)
        other.InsertTag(loose)
        out("  loose UVWTag count after InsertTag:", loose.GetDataCount(), "polys", other.GetPolygonCount())
        out("  Message MSG_POINTS_CHANGED ->", other.Message(c4d.MSG_POINTS_CHANGED))
        out("  count after MSG_POINTS_CHANGED:", loose.GetDataCount())
        other.Message(c4d.MSG_UPDATE)
        out("  count after MSG_UPDATE:", loose.GetDataCount())

    section("How to size a fresh UVW tag")
    fresh_mesh = make_quad_mesh(3, 2)
    doc.InsertObject(fresh_mesh)
    f_uvw = fresh_mesh.MakeTag(c4d.Tuvw)
    out("  after MakeTag: count", f_uvw.GetDataCount(), "polys", fresh_mesh.GetPolygonCount())
    attempt("ResizeObject(same, same)", lambda: fresh_mesh.ResizeObject(fresh_mesh.GetPointCount(), fresh_mesh.GetPolygonCount()))
    out("  after same-size ResizeObject: count", f_uvw.GetDataCount())
    attempt("SetPoint + Message(MSG_UPDATE)", lambda: (fresh_mesh.SetPoint(0, c4d.Vector(1, 2, 3)), fresh_mesh.Message(c4d.MSG_UPDATE)))
    out("  after SetPoint+MSG_UPDATE: count", f_uvw.GetDataCount())
    attempt("doc.ExecutePasses", lambda: doc.ExecutePasses(None, True, True, True, 0))
    out("  after ExecutePasses: count", f_uvw.GetDataCount())
    attempt("c4d.EventAdd + ExecutePasses", lambda: (c4d.EventAdd(), doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_NONE if hasattr(c4d, "BUILDFLAGS_NONE") else 0)))
    out("  count now", f_uvw.GetDataCount())
    attempt("MakeTag via SendModelingCommand MCOMMAND_GENERATEUVW?", lambda: hasattr(c4d, "MCOMMAND_GENERATEUVW"))
    g_uvw = None
    fm2 = make_quad_mesh(3, 2)
    doc.InsertObject(fm2)
    attempt("UVW tag created BEFORE insert into doc", lambda: fm2.InsertTag(c4d.BaseTag(c4d.Tuvw)))
    out("  count", fm2.GetFirstTag().GetDataCount())
    # copying a tag from another mesh
    fm3 = make_quad_mesh(3, 2)
    doc.InsertObject(fm3)
    fm3.ResizeObject(fm3.GetPointCount(), fm3.GetPolygonCount() + 1)
    fm3.ResizeObject(fm3.GetPointCount(), fm3.GetPolygonCount() - 1)
    out("  grow-then-shrink resize: tag-less mesh polys", fm3.GetPolygonCount())
    t3 = fm3.MakeTag(c4d.Tuvw)
    fm3.ResizeObject(fm3.GetPointCount(), fm3.GetPolygonCount() + 1)
    out("  MakeTag then grow by one polygon: count", t3.GetDataCount(), "polys", fm3.GetPolygonCount())
    fm3.ResizeObject(fm3.GetPointCount(), fm3.GetPolygonCount() - 1)
    out("  then shrink back: count", t3.GetDataCount(), "polys", fm3.GetPolygonCount())
    attempt("GetSlow(0) now", lambda: {k: (v.x, v.y) for k, v in t3.GetSlow(0).items()})

    section("ResizeObject with tags")
    victim = make_quad_mesh(2, 2)
    doc.InsertObject(victim)
    v_uvw = victim.MakeTag(c4d.Tuvw)
    v_sel = victim.MakeTag(c4d.Tpolygonselection)
    v_sel.GetBaseSelect().Select(1)
    out("  before: polys", victim.GetPolygonCount(), "uvw", v_uvw.GetDataCount(), "sel count", v_sel.GetBaseSelect().GetCount())
    attempt("ResizeObject(30, 25)", lambda: victim.ResizeObject(30, 25))
    out("  after resize: polys", victim.GetPolygonCount(), "points", victim.GetPointCount(), "uvw", v_uvw.GetDataCount())
    victim.Message(c4d.MSG_UPDATE)
    out("  after MSG_UPDATE: uvw", v_uvw.GetDataCount())
    out("  sel after resize: count", v_sel.GetBaseSelect().GetCount(), "polygon 1 selected",
        v_sel.GetBaseSelect().IsSelected(1))

    section("Extrude: what C4D does to tags")
    ex = make_quad_mesh(2, 2)
    doc.InsertObject(ex)
    ex_uvw = ex.MakeTag(c4d.Tuvw)
    ex_sel = ex.MakeTag(c4d.Tpolygonselection)
    out("  before: points", ex.GetPointCount(), "polys", ex.GetPolygonCount(), "uvw", ex_uvw.GetDataCount())
    ex.GetPolygonS().DeselectAll()
    ex.GetPolygonS().Select(0)
    settings = c4d.BaseContainer()
    settings[c4d.MDATA_EXTRUDE_OFFSET] = 20.0
    attempt("extrude", lambda: c4d.utils.SendModelingCommand(
        command=c4d.ID_MODELING_EXTRUDE_TOOL, list=[ex], mode=c4d.MODELINGCOMMANDMODE_POLYGONSELECTION,
        bc=settings, doc=doc))
    out("  after: points", ex.GetPointCount(), "polys", ex.GetPolygonCount(), "uvw", ex_uvw.GetDataCount())

    section("Dirty counters for modeling edits")
    names = ("DATA", "MATRIX", "CHILDREN", "CACHE", "SELECT", "DESCRIPTION")

    def dirty(atom):
        return tuple(atom.GetDirty(getattr(c4d, "DIRTYFLAGS_" + n)) for n in names)

    def hd(d):
        return {n: d.GetHDirty(getattr(c4d, "HDIRTYFLAGS_" + n)) for n in
                ("OBJECT", "OBJECT_MATRIX", "OBJECT_HIERARCHY", "TAG", "ANIMATION", "ALL")
                if hasattr(c4d, "HDIRTYFLAGS_" + n)}

    def measure(label, atoms, action):
        before = [dirty(a) for a in atoms]
        hb = hd(doc)
        action()
        after = [dirty(a) for a in atoms]
        ha = hd(doc)
        parts = []
        for atom, b, a in zip(atoms, before, after):
            parts.append("%s:%s" % (atom.GetName() or atom.GetTypeName(),
                                    ",".join(n for n, x, y in zip(names, b, a) if x != y) or "-"))
        out("  %-34s %s | doc:%s" % (label, " ".join(parts), ",".join(k for k in ha if ha[k] != hb[k]) or "-"))

    probe = make_quad_mesh(3, 3)
    probe.SetName("Probe")
    doc.InsertObject(probe)
    p_sel = probe.MakeTag(c4d.Tpolygonselection)
    p_sel.SetName("ProbeSel")
    p_uvw = probe.MakeTag(c4d.Tuvw)
    p_uvw.SetName("ProbeUVW")
    measure("SetPoint", [probe, p_sel, p_uvw], lambda: (probe.SetPoint(0, c4d.Vector(-5, -5, 0)), probe.Message(c4d.MSG_UPDATE)))
    measure("tag selection change", [probe, p_sel, p_uvw], lambda: p_sel.GetBaseSelect().Select(4))
    probe.ResizeObject(probe.GetPointCount(), probe.GetPolygonCount() + 1)  # sizes the tags (see above)
    probe.SetPolygon(probe.GetPolygonCount() - 1, c4d.CPolygon(0, 1, 2, 3))
    probe.ResizeObject(probe.GetPointCount(), probe.GetPolygonCount() - 1)
    out("  probe uvw count", p_uvw.GetDataCount(), "polys", probe.GetPolygonCount())
    if p_uvw.GetDataCount() > 1:
        measure("uvw SetSlow", [probe, p_sel, p_uvw], lambda: p_uvw.SetSlow(
            1, c4d.Vector(0, 0, 0), c4d.Vector(1, 0, 0), c4d.Vector(1, 1, 0), c4d.Vector(0, 1, 0)))
    measure("active polygon selection", [probe, p_sel, p_uvw], lambda: probe.GetPolygonS().Select(2))

    section("Spline")
    spline = attempt("SplineObject(5, SPLINETYPE_BEZIER)", lambda: c4d.SplineObject(5, c4d.SPLINETYPE_BEZIER))
    if spline is not None:
        doc.InsertObject(spline)
        spline.SetName("Spl")
        for i in range(5):
            spline.SetPoint(i, c4d.Vector(i * 10, (i % 2) * 10, 0))
        out("  segments", spline.GetSegmentCount(), "points", spline.GetPointCount())
        attempt("GetSegment(0)", lambda: spline.GetSegment(0))
        attempt("SetSegment(0, 5, True)", lambda: spline.SetSegment(0, 5, True))
        attempt("GetSegment(0) after", lambda: spline.GetSegment(0))
        attempt("GetTangent(1)", lambda: {k: (v.x, v.y) for k, v in spline.GetTangent(1).items()})
        attempt("SetTangent(1)", lambda: spline.SetTangent(1, c4d.Vector(-3, 0, 0), c4d.Vector(3, 0, 0)))
        attempt("GetAllTangents", lambda: len(spline.GetAllTangents()) if hasattr(spline, "GetAllTangents") else "n/a")
        attempt("ResizeObject(8, 2)", lambda: spline.ResizeObject(8, 2))
        out("  after resize: points", spline.GetPointCount(), "segments", spline.GetSegmentCount())
        attempt("SetSegment(0,4,False)+(1,4,True)", lambda: (spline.SetSegment(0, 4, False), spline.SetSegment(1, 4, True)))
        attempt("segments dump", lambda: [spline.GetSegment(i) for i in range(spline.GetSegmentCount())])
        measure("spline SetPoint", [spline], lambda: (spline.SetPoint(0, c4d.Vector(1, 1, 1)), spline.Message(c4d.MSG_UPDATE)))
        description_ids = []
        for bc, did, group in spline.GetDescription(getattr(c4d, "DESCFLAGS_DESC_NONE", 0)):
            if did.GetDepth() > 0:
                description_ids.append((did[0].id, did[did.GetDepth() - 1].dtype))
        out("  description ids (first 20):", description_ids[:20])
        attempt("spline type param", lambda: spline[c4d.SPLINEOBJECT_TYPE])
        attempt("spline closed param", lambda: spline[c4d.SPLINEOBJECT_CLOSED])

    section("Performance")
    clock = getattr(time, "perf_counter", None) or time.clock
    for columns in (100, 316):
        big = make_quad_mesh(columns, columns)
        count = big.GetPointCount()
        start = clock()
        points = big.GetAllPoints()
        flat = []
        for p in points:
            flat.extend((p.x, p.y, p.z))
        t_read = (clock() - start) * 1000
        start = clock()
        polys = big.GetAllPolygons()
        flat_p = []
        for q in polys:
            flat_p.extend((q.a, q.b, q.c, q.d))
        t_polys = (clock() - start) * 1000
        start = clock()
        big.SetAllPoints([c4d.Vector(flat[i], flat[i + 1], flat[i + 2]) for i in range(0, len(flat), 3)])
        t_write = (clock() - start) * 1000
        out("  %6d points: read+flatten %.1f ms, polygons %.1f ms, write %.1f ms" % (count, t_read, t_polys, t_write))

    path = os.environ.get("C4D_COLLAB_PROBE_OUT")
    if path:
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join("%s" % l for l in LINES))


try:
    main()
except Exception:
    out(traceback.format_exc())
    path = os.environ.get("C4D_COLLAB_PROBE_OUT")
    if path:
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join("%s" % l for l in LINES))
