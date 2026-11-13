# -*- coding: utf-8 -*-
"""Real rig through scanner -> server snapshot -> applier, then compare the copy with the original (real Cinema 4D).

Measures how far the guest's objects sit from the host's, and what the guest would send back right after joining
(its own rig evaluation must not look like an edit).   c4dpy tests/c4d_real/rig_check.py [--same-frame] [scene.c4d]
"""
from __future__ import print_function
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugin", "c4d_collab"))
import c4d
from c4d import documents
from c4d_collab_lib.core.model import SceneState
from c4d_collab_lib.c4d_adapter import ids
from c4d_collab_lib.c4d_adapter.applier import Applier
from c4d_collab_lib.c4d_adapter.tracker import Tracker

SCENE = next((a for a in sys.argv[1:] if not a.startswith("--")), None) or r"C:\Users\PC\Desktop\C4D Things\Fight_test11\Fight_test11.c4d"


def walk(doc):
    stack = [doc.GetFirstObject()]
    while stack:
        obj = stack.pop()
        while obj is not None:
            yield obj
            if obj.GetDown() is not None:
                stack.append(obj.GetDown())
            obj = obj.GetNext()


def tags_of(obj):
    tag = obj.GetFirstTag()
    while tag is not None:
        yield tag
        tag = tag.GetNext()


def uids(registry, doc):
    taken = set()
    for obj in walk(doc):
        taken.add(registry.ensure_uid(obj, taken))
        for tag in tags_of(obj):
            taken.add(registry.ensure_uid(tag, taken))


def evaluate(doc):
    doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_0)


src = documents.LoadDocument(SCENE, c4d.SCENEFILTER_OBJECTS | c4d.SCENEFILTER_MATERIALS)
evaluate(src)

shadow, registry = SceneState(), ids.Registry()
tracker = Tracker(shadow, registry, editable=lambda u: True, subtree_editable=lambda u: True)
uids(registry, src)
server = SceneState()
for op in tracker.scan(src, force=True):
    server.apply(op)
ops = json.loads(json.dumps(server.snapshot_ops()))

dst = documents.BaseDocument()
documents.InsertBaseDocument(dst)
shadow2, registry2 = SceneState(), ids.Registry()
tracker2 = Tracker(shadow2, registry2, editable=lambda u: True, subtree_editable=lambda u: True)
Applier(shadow2, registry2, tracker2).apply(dst, ops)
if "--same-frame" in sys.argv:
    dst.SetTime(src.GetTime())

pairs = []
for obj in walk(src):
    other = registry2.get(registry.uuid_of(obj))
    if other is not None:
        pairs.append((obj, other))
print("objects: host %d, guest %d, paired %d" % (len(list(walk(src))), len(list(walk(dst))), len(pairs)))


def compare(label):
    evaluate(dst)
    rows = []
    for obj, other in pairs:
        dist = (obj.GetMg().off - other.GetMg().off).GetLength()
        angle = max((obj.GetMg().v1 - other.GetMg().v1).GetLength(), (obj.GetMg().v2 - other.GetMg().v2).GetLength(),
                    (obj.GetMg().v3 - other.GetMg().v3).GetLength())
        rows.append((dist, angle, obj, other))
    bad = [r for r in rows if r[0] > 0.01 or r[1] > 0.001]
    print("== %s: %d of %d differ (position > 0.01 or axes > 0.001), worst position %.2f" %
          (label, len(bad), len(rows), max(r[0] for r in rows)))
    for dist, angle, obj, other in sorted(bad, key=lambda r: -r[0])[:12]:
        types = ",".join(sorted(set(t.GetTypeName() for t in tags_of(obj))))
        print("   %-28s pos %9.2f  axes %.3f  tags: %s" % (obj.GetName()[:28], dist, angle, types[:60]))
    return bad


compare("guest as built")


kinds = {}
for obj in walk(src):
    for tag in tags_of(obj):
        kinds[tag.GetTypeName()] = kinds.get(tag.GetTypeName(), 0) + 1
print("host tag kinds:", sorted(kinds.items(), key=lambda kv: -kv[1])[:25])
guest_kinds = {}
for obj in walk(dst):
    for tag in tags_of(obj):
        guest_kinds[tag.GetTypeName()] = guest_kinds.get(tag.GetTypeName(), 0) + 1
print("guest tag kinds:", sorted(guest_kinds.items(), key=lambda kv: -kv[1])[:25])


def local_report():
    """Objects whose own (local) matrix differs: the origin of a drift, not its consequences."""
    rows = []
    for obj, other in pairs:
        a, b = obj.GetMl(), other.GetMl()
        dist = (a.off - b.off).GetLength()
        axes = max((a.v1 - b.v1).GetLength(), (a.v2 - b.v2).GetLength(), (a.v3 - b.v3).GetLength())
        if dist > 0.01 or axes > 0.001:
            rows.append((dist, axes, obj, other))
    print("== local matrix differs on %d objects" % len(rows))
    for dist, axes, obj, other in sorted(rows, key=lambda r: -(r[0] + r[1] * 100))[:30]:
        parent = obj.GetUp().GetName() if obj.GetUp() is not None else "-"
        print("   %-26s type %-6s parent %-22s pos %9.2f axes %.3f | host pos %s | guest pos %s" % (
            obj.GetName()[:26], obj.GetType(), parent[:22], dist, axes,
            tuple(round(x, 1) for x in (obj.GetRelPos().x, obj.GetRelPos().y, obj.GetRelPos().z)),
            tuple(round(x, 1) for x in (other.GetRelPos().x, other.GetRelPos().y, other.GetRelPos().z))))
    return rows


local_report()


def probe(name):
    for obj, other in pairs:
        if obj.GetName() == name:
            print("-- %s" % name)
            print("   host time %s fps %s | guest time %s fps %s" % (src.GetTime().GetFrame(src.GetFps()), src.GetFps(),
                                                                    dst.GetTime().GetFrame(dst.GetFps()), dst.GetFps()))
            for label, o in (("host", obj), ("guest", other)):
                tracks = [t.GetName() for t in o.GetCTracks()] if hasattr(o, "GetCTracks") else []
                print("   %s: pos %s tracks %s tags %s" % (label, o.GetRelPos(), tracks, [t.GetTypeName() for t in tags_of(o)]))
            return


for n in ("R_Shoulder", "controlWaist", "L_Foot_Goal"):
    probe(n)


for name in ("l_knee", "r_heel", "l_elbow"):
    for obj, other in pairs:
        if obj.GetName() == name:
            print("ROT %-8s host rel %s ml-h %.3f | guest rel %s ml-h %.3f" % (
                name, tuple(round(x, 3) for x in (obj.GetRelRot().x, obj.GetRelRot().y, obj.GetRelRot().z)),
                c4d.utils.MatrixToHPB(obj.GetMl(), c4d.ROTATIONORDER_DEFAULT).x,
                tuple(round(x, 3) for x in (other.GetRelRot().x, other.GetRelRot().y, other.GetRelRot().z)),
                c4d.utils.MatrixToHPB(other.GetMl(), c4d.ROTATIONORDER_DEFAULT).x))
            break


def echo_report():
    """What the guest would send back right after joining: its own rig evaluation looks like an edit."""
    evaluate(dst)
    echo = tracker2.scan(dst, force=True)
    print("== ops the guest would send right after joining (should be 0): %d" % len(echo))
    for op in echo[:10]:
        node = shadow2.get(op.get("uuid"))
        print("   %s %-20s %s" % (op.get("op"), (node.name if node else "?")[:20], str(op.get("patch"))[:150]))
    return echo


ECHO = echo_report()


BASE = dict((op["uuid"], op) for op in ops if op.get("op") == "create")


def flat(patch, path=()):
    for key, value in (patch.get("set") or {}).items():
        yield path + (key,), value
    for key, sub in (patch.get("sub") or {}).items():
        for item in flat(sub, path + (key,)):
            yield item


def deltas(echo):
    """How far each echoed value is from what the host sent (the size of the evaluation noise)."""
    worst = {}
    for op in echo:
        base = BASE.get(op["uuid"])
        if base is None:
            continue
        for path, new in flat(op.get("patch") or {}):
            old = base["data"]
            for key in path:
                old = old.get(key) if isinstance(old, dict) else None
            if isinstance(new, dict) and "V" in new:
                new = new["V"]
                old = old.get("V") if isinstance(old, dict) else None
            if isinstance(new, list) and isinstance(old, list):
                size = max(abs(a - b) for a, b in zip(new, old))
            elif isinstance(new, (int, float)) and isinstance(old, (int, float)):
                size = abs(new - old)
            else:
                continue
            tag = (base["ctype"], path[-1])
            if size > worst.get(tag, (0, ""))[0]:
                worst[tag] = (size, base["name"])
    for (ctype, key), (size, name) in sorted(worst.items(), key=lambda kv: -kv[1][0])[:16]:
        print("   type %-8s %-26s biggest change %.5f (%s)" % (ctype, key, size, name[:20]))


deltas(ECHO)


for obj, other in pairs:
    extra = [t.GetTypeName() for t in tags_of(other) if t.GetTypeName() in ("Compositing", "Display")]
    mine = [t.GetTypeName() for t in tags_of(obj) if t.GetTypeName() in ("Compositing", "Display")]
    if len(extra) != len(mine):
        print("EXTRA", obj.GetName()[:24], "type", obj.GetType(), "host", mine, "guest", extra)


for obj, other in pairs:
    if obj.GetType() == c4d.Ospline:
        def info(o):
            try:
                tangents = o.GetTangentCount()
            except Exception as exc:
                tangents = "err %s" % exc
            return "type %s points %s tangents %s closed %s" % (o[c4d.SPLINEOBJECT_TYPE], o.GetPointCount(), tangents, o[c4d.SPLINEOBJECT_CLOSED])
        print("SPLINE", obj.GetName()[:20], "host:", info(obj), "| guest:", info(other))
