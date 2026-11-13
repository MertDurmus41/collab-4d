"""Meshes, splines, selection tags and UVW tags as chunked, diff-friendly data.

Large arrays are cut into chunks of CHUNK items and every chunk is packed
separately (protocol.pack_floats / pack_ints). The generic dict diff then
carries only the chunks that changed: moving one point of a 100k-point mesh
sends one 128-point chunk (about 4 KB) instead of the whole mesh.

Polygon object "g":  {"n": [points, polygons],
                      "pts": {chunk: floats x3}, "polys": {chunk: ints x4}}
Spline object  "g":  {"n": [points, segments], "pts": {...}, "seg": ints (count, closed)*,
                      "tan": {chunk: floats x6}}            (tangents only when present)
Selection tag  "s":  {"n": element count, "r": ints (first, last)* inclusive ranges}
UVW tag        "uv": {"n": polygons, "uv": {chunk: floats x8}}   (u, v of corners a-d)

Measured in Phase 6 (ARCHITECTURE.md section 9):
- selection tag edits, spline tangents and segment flags move no dirty counter,
  so the tracker compares them periodically;
- a fresh UVW tag has 0 entries until its owner is resized (even to the same
  size), after which C4D keeps tag and polygon counts in step by itself;
- ResizeObject keeps existing points and UVs.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import c4d

from ...core import protocol
from ...core.compat import integer_types, string_types
from ...core.log import get_logger
from .. import compat

LOG = get_logger("geometry")

CHUNK = 128
MAX_ITEMS = 50 * 1000 * 1000

FULL = "full"

POLYGON_TYPES = compat.type_ids("Opolygon")
SPLINE_TYPES = compat.type_ids("Ospline")
POINT_SELECTION = compat.type_ids("Tpointselection")
POLYGON_SELECTION = compat.type_ids("Tpolygonselection")
EDGE_SELECTION = compat.type_ids("Tedgeselection")
SELECTION_TYPES = POINT_SELECTION | POLYGON_SELECTION | EDGE_SELECTION
UVW_TYPES = compat.type_ids("Tuvw")


# Chunk helpers -----------------------------------------------------------------------

def _chunks(flat, per_item, pack):
    size = CHUNK * per_item
    return dict((str(start // size), pack(flat[start:start + size])) for start in range(0, len(flat), size))


def _count(value):
    if isinstance(value, bool) or not isinstance(value, integer_types) or value < 0 or value > MAX_ITEMS:
        raise ValueError("invalid item count %r" % (value,))
    return value


def _chunk_values(chunks, index, count, per_item, unpack):
    data = chunks.get(str(index)) if isinstance(chunks, dict) else None
    if not isinstance(data, string_types):
        raise ValueError("missing chunk %s" % index)
    values = unpack(data)
    expected = min(CHUNK, count - index * CHUNK) * per_item
    if index < 0 or expected <= 0 or len(values) != expected:
        raise ValueError("chunk %s has the wrong size" % index)
    return values


def _gather(chunks, count, per_item, unpack):
    """Every value of a chunked section, validated against the declared item count."""
    flat = []
    for index in range((count + CHUNK - 1) // CHUNK):
        flat.extend(_chunk_values(chunks, index, count, per_item, unpack))
    return flat


def _each_chunk(chunks, ids, count, per_item, unpack):
    """(first item index, values) for the listed chunk ids."""
    for cid in sorted(ids, key=int):
        index = int(cid)
        yield index * CHUNK, _chunk_values(chunks, index, count, per_item, unpack)


def _vectors(flat, per_item=3):
    return [c4d.Vector(flat[i], flat[i + 1], flat[i + 2]) for i in range(0, len(flat), per_item)]


def _pair(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("expected [a, b]")
    return _count(value[0]), _count(value[1])


# What changed ------------------------------------------------------------------------

def change_of(sub_patch, set_keys):
    """Classify a patch of one data section ("g" or "uv" or "s").

    Returns None (untouched), FULL (rebuild everything) or {section: {chunk ids}}.
    `set_keys` = patch["set"] keys at the node-data level that mean "replaced as a whole".
    """
    if set_keys:
        return FULL
    if sub_patch is None:
        return None
    if sub_patch.get("set") or sub_patch.get("del"):
        return FULL  # counts changed, or a section appeared/vanished
    change = {}
    for section, nested in (sub_patch.get("sub") or {}).items():
        if nested.get("del") or nested.get("sub"):
            return FULL  # a chunk disappeared (fewer items now)
        change[section] = set((nested.get("set") or {}).keys())
    return change


# Polygon objects ------------------------------------------------------------------------

def dump_polygon(obj):
    flat = []
    points = obj.GetAllPoints()
    for p in points:
        flat.extend((p.x, p.y, p.z))
    flat_polys = []
    polys = obj.GetAllPolygons()
    for q in polys:
        flat_polys.extend((q.a, q.b, q.c, q.d))
    return {"n": [len(points), len(polys)],
            "pts": _chunks(flat, 3, protocol.pack_floats),
            "polys": _chunks(flat_polys, 4, protocol.pack_ints)}


def pack_points(points):
    """Point positions (c4d.Vector) as the chunked "pts" section."""
    flat = []
    for p in points:
        flat.extend((p.x, p.y, p.z))
    return _chunks(flat, 3, protocol.pack_floats)


def load_polygon(obj, g, change=FULL):
    n_points, n_polys = _pair(g.get("n"))
    partial = isinstance(change, dict) and obj.GetPointCount() == n_points and obj.GetPolygonCount() == n_polys
    if not partial:
        flat = _gather(g.get("pts"), n_points, 3, protocol.unpack_floats)
        flat_polys = _gather(g.get("polys"), n_polys, 4, protocol.unpack_ints)
        _check_indices(flat_polys, n_points)
        obj.ResizeObject(n_points, n_polys)
        if n_points:  # an empty mesh: SetAllPoints raises "Invalid object length" (seen on a real rig)
            obj.SetAllPoints(_vectors(flat))
        for index in range(n_polys):
            a, b, c, d = flat_polys[index * 4:index * 4 + 4]
            obj.SetPolygon(index, c4d.CPolygon(a, b, c, d))
    else:
        for start, values in _each_chunk(g.get("pts"), change.get("pts", ()), n_points, 3, protocol.unpack_floats):
            for k, vector in enumerate(_vectors(values)):
                obj.SetPoint(start + k, vector)
        for start, values in _each_chunk(g.get("polys"), change.get("polys", ()), n_polys, 4, protocol.unpack_ints):
            _check_indices(values, n_points)
            for k in range(len(values) // 4):
                a, b, c, d = values[k * 4:k * 4 + 4]
                obj.SetPolygon(start + k, c4d.CPolygon(a, b, c, d))
    obj.Message(c4d.MSG_UPDATE)
    return not partial


def _check_indices(flat_polys, n_points):
    for index in flat_polys:
        if index < 0 or index >= max(n_points, 1):
            raise ValueError("polygon index out of range")


# Splines --------------------------------------------------------------------------------

def dump_spline(obj):
    flat = []
    points = obj.GetAllPoints()
    for p in points:
        flat.extend((p.x, p.y, p.z))
    segments = []
    for index in range(obj.GetSegmentCount()):
        segment = obj.GetSegment(index)
        segments.extend((segment["cnt"], 1 if segment["closed"] else 0))
    data = {"n": [len(points), obj.GetSegmentCount()],
            "pts": _chunks(flat, 3, protocol.pack_floats),
            "seg": protocol.pack_ints(segments)}
    if len(points) and obj.GetTangentCount() == len(points):
        tangents = []
        for index in range(len(points)):
            tangent = obj.GetTangent(index)
            vl, vr = tangent["vl"], tangent["vr"]
            tangents.extend((vl.x, vl.y, vl.z, vr.x, vr.y, vr.z))
        data["tan"] = _chunks(tangents, 6, protocol.pack_floats)
    return data


def load_spline(obj, g, change=FULL):
    n_points, n_segments = _pair(g.get("n"))
    partial = isinstance(change, dict) and obj.GetPointCount() == n_points and obj.GetSegmentCount() == n_segments
    if not partial:
        flat = _gather(g.get("pts"), n_points, 3, protocol.unpack_floats)
        segments = protocol.unpack_ints(g.get("seg", ""))
        if len(segments) != n_segments * 2 or sum(segments[0::2]) > n_points:
            raise ValueError("invalid spline segments")
        obj.ResizeObject(n_points, n_segments)
        obj.SetAllPoints(_vectors(flat))
        for index in range(n_segments):
            obj.SetSegment(index, segments[index * 2], bool(segments[index * 2 + 1]))
        if "tan" in g and obj.GetTangentCount() == n_points:
            for start, values in _each_chunk(g["tan"], range(_chunk_total(n_points)), n_points, 6,
                                             protocol.unpack_floats):
                _set_tangents(obj, start, values)
    else:
        for start, values in _each_chunk(g.get("pts"), change.get("pts", ()), n_points, 3, protocol.unpack_floats):
            for k, vector in enumerate(_vectors(values)):
                obj.SetPoint(start + k, vector)
        if "tan" in g and obj.GetTangentCount() == n_points:
            for start, values in _each_chunk(g["tan"], change.get("tan", ()), n_points, 6, protocol.unpack_floats):
                _set_tangents(obj, start, values)
    obj.Message(c4d.MSG_UPDATE)
    return not partial


def _chunk_total(count):
    return (count + CHUNK - 1) // CHUNK


def _set_tangents(obj, start, values):
    for k in range(len(values) // 6):
        base = k * 6
        obj.SetTangent(start + k, c4d.Vector(values[base], values[base + 1], values[base + 2]),
                       c4d.Vector(values[base + 3], values[base + 4], values[base + 5]))


# Selection tags ----------------------------------------------------------------------------

def selection_size(tag):
    """Number of selectable elements the tag refers to (None when its owner has none)."""
    owner = tag.GetObject()
    if owner is None:
        return None
    kind = tag.GetType()
    try:
        if kind in POINT_SELECTION:
            return owner.GetPointCount()
        if kind in POLYGON_SELECTION:
            return owner.GetPolygonCount()
        if kind in EDGE_SELECTION:
            return owner.GetPolygonCount() * 4
    except AttributeError:
        return None
    return None


def dump_selection(tag):
    count = selection_size(tag)
    if count is None:
        return None
    select = tag.GetBaseSelect()
    ranges = []
    try:
        for index in range(select.GetSegments()):
            span = select.GetRange(index, count)
            if span:
                ranges.extend((span[0], span[1]))
    except AttributeError:  # very old versions: no segment API
        flags = select.GetAll(count)
        start = None
        for position in range(count):
            if flags[position] and start is None:
                start = position
            if start is not None and (position == count - 1 or not flags[position + 1]):
                ranges.extend((start, position))
                start = None
    return {"n": count, "r": protocol.pack_ints(ranges)}


def load_selection(tag, data):
    """Returns False when the tag does not (yet) match the owner's element count."""
    count = _count(data.get("n"))
    if selection_size(tag) != count:
        LOG.warning("Selection tag size %s does not match its owner (%s), skipped", count, selection_size(tag))
        return False
    pairs = protocol.unpack_ints(data.get("r", ""))
    if len(pairs) % 2:
        raise ValueError("invalid selection ranges")
    flags = bytearray(count)
    selected = 0
    for index in range(0, len(pairs), 2):
        first, last = pairs[index], pairs[index + 1]
        if first < 0 or last < first or last >= count:
            raise ValueError("selection range out of bounds")
        for position in range(first, last + 1):
            flags[position] = 1
        selected += last - first + 1
    select = tag.GetBaseSelect()
    select.DeselectAll()
    try:
        select.SetAll([int(f) for f in flags])
        ok = select.GetCount() == selected
    except Exception:
        ok = False
    if not ok:  # fall back to selecting element by element
        select.DeselectAll()
        for position in range(count):
            if flags[position]:
                select.Select(position)
    return True


# UVW tags ----------------------------------------------------------------------------------

def dump_uvw(tag):
    count = tag.GetDataCount()
    flat = []
    for index in range(count):
        corners = tag.GetSlow(index)
        for key in ("a", "b", "c", "d"):
            corner = corners[key]
            flat.extend((corner.x, corner.y))
    return {"n": count, "uv": _chunks(flat, 8, protocol.pack_floats)}


def load_uvw(tag, data, change=FULL):
    count = _count(data.get("n"))
    if count == 0:
        return True
    owner = tag.GetObject()
    if owner is None or owner.GetPolygonCount() != count:
        LOG.warning("UVW tag for %s polygons does not match its owner, skipped", count)
        return False
    if tag.GetDataCount() != count:
        # A fresh tag has no entries until its owner is resized; same size is enough.
        owner.ResizeObject(owner.GetPointCount(), owner.GetPolygonCount())
        if tag.GetDataCount() != count:
            raise ValueError("could not size the UVW tag")
    ids = range(_chunk_total(count)) if change == FULL or not isinstance(change, dict) else change.get("uv", ())
    for start, values in _each_chunk(data.get("uv"), ids, count, 8, protocol.unpack_floats):
        for k in range(len(values) // 8):
            v = values[k * 8:k * 8 + 8]
            tag.SetSlow(start + k, c4d.Vector(v[0], v[1], 0), c4d.Vector(v[2], v[3], 0),
                        c4d.Vector(v[4], v[5], 0), c4d.Vector(v[6], v[7], 0))
    return True
