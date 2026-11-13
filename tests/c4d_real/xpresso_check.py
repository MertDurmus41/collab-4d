# -*- coding: utf-8 -*-
"""XPresso graphs through scanner -> server snapshot -> applier on a real rig (real Cinema 4D).

Reports how many graphs travelled, how many object ports could not be resolved to a parameter, and whether the
graph that was built on the guest dumps exactly like the host graph.   c4dpy tests/c4d_real/xpresso_check.py [scene.c4d]
"""
from __future__ import print_function
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugin", "c4d_collab"))
import c4d
from c4d import documents
from c4d_collab_lib.core.model import SceneState
from c4d_collab_lib.c4d_adapter import compat, ids
from c4d_collab_lib.c4d_adapter.applier import Applier
from c4d_collab_lib.c4d_adapter.serializers import xpresso
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


def first_difference(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                return "%s/%s only on one side" % (path, key)
            found = first_difference(a[key], b[key], path + "/" + key)
            if found:
                return found
        return None
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return "%s length %d vs %d" % (path, len(a), len(b))
        for index, (x, y) in enumerate(zip(a, b)):
            found = first_difference(x, y, "%s[%d]" % (path, index))
            if found:
                return found
        return None
    return None if xpresso.same(a, b) else "%s: %r vs %r" % (path, a, b)


src = documents.LoadDocument(SCENE, c4d.SCENEFILTER_OBJECTS | c4d.SCENEFILTER_MATERIALS)
src.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_0)
shadow, registry = SceneState(), ids.Registry()
tracker = Tracker(shadow, registry, editable=lambda u: True, subtree_editable=lambda u: True)
taken = set()
for obj in walk(src):
    taken.add(registry.ensure_uid(obj, taken))
    for tag in tags_of(obj):
        taken.add(registry.ensure_uid(tag, taken))
started = time.time()
server = SceneState()
for op in tracker.scan(src, force=True):
    server.apply(op)
print("scan of the host: %.2f s" % (time.time() - started))
ops = json.loads(json.dumps(server.snapshot_ops()))

host_tags = [t for obj in walk(src) for t in tags_of(obj) if xpresso.is_xpresso(t)]
print("XPresso tags on the host: %d, safe: %d" % (len(host_tags), len([t for t in host_tags if xpresso.is_safe(t)])))
shared = [n for n in server.nodes.values() if n.kind == "tag" and "xp" in n.data]
print("XPresso tags in the session: %d" % len(shared))
unresolved = 0
objects = 0
for node in shared:
    for spec in node.data["xp"]["n"]:
        if spec["i"] == xpresso.OBJECT_OPERATOR:
            objects += 1
            for port in spec["in"] + spec["out"]:
                if xpresso.PARAM_PORT_FIRST <= port["m"] < xpresso.SPECIAL_PORT_FIRST and "d" not in port:
                    unresolved += 1
print("Object nodes: %d, parameter ports without a parameter: %d" % (objects, unresolved))
print("driven parameters: %d" % sum(len(n.data["xp"]["dr"]) for n in shared))

dst = documents.BaseDocument()
documents.InsertBaseDocument(dst)
shadow2, registry2 = SceneState(), ids.Registry()
tracker2 = Tracker(shadow2, registry2, editable=lambda u: True, subtree_editable=lambda u: True)
started = time.time()
Applier(shadow2, registry2, tracker2).apply(dst, ops)
print("apply on the guest: %.2f s" % (time.time() - started))

bad = 0
for tag in host_tags:
    uuid = registry.uuid_of(tag)
    other = registry2.get(uuid)
    if not xpresso.is_safe(tag):
        continue
    if other is None:
        print("MISSING on the guest:", tag.GetObject().GetName())
        bad += 1
        continue
    mine, theirs = xpresso.dump(tag, registry.uuid_of), xpresso.dump(other, registry2.uuid_of)
    if theirs is None:
        print("NOT BUILT:", tag.GetObject().GetName())
        bad += 1
        continue
    mine["dr"] = [[registry2.uuid_of(registry.get(u)), k] for u, k in mine["dr"]]
    difference = first_difference(mine, theirs)
    if difference:
        bad += 1
        if bad <= 12:
            print("DIFFERENT %-22s %s" % (tag.GetObject().GetName()[:22], difference[:220]))
print("graphs that differ after the round trip: %d of %d" % (bad, len(host_tags)))
