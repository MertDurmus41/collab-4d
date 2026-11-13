# -*- coding: utf-8 -*-
"""Shared helpers of the real-rig probes: build a guest copy of a scene through scanner -> snapshot -> applier."""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugin", "c4d_collab"))
import c4d
from c4d import documents
from c4d_collab_lib.core.model import SceneState
from c4d_collab_lib.c4d_adapter import ids
from c4d_collab_lib.c4d_adapter.applier import Applier
from c4d_collab_lib.c4d_adapter.tracker import Tracker

SCENE = r"C:\Users\PC\Desktop\C4D Things\Fight_test11\Fight_test11.c4d"
T0 = time.time()


def log(*a):
    print("[%5.1fs]" % (time.time() - T0), *a)
    sys.stdout.flush()


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


def path(o):
    p = []
    while o:
        p.append(o.GetName())
        o = o.GetUp()
    return "/".join(reversed(p))


def at(doc, frame):
    doc.SetTime(c4d.BaseTime(frame, doc.GetFps()))
    doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_0)
    doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_0)


class Pair(object):
    def __init__(self, scene=SCENE, prepare=None):
        self.src = documents.LoadDocument(scene, c4d.SCENEFILTER_OBJECTS | c4d.SCENEFILTER_MATERIALS)
        documents.InsertBaseDocument(self.src)
        if prepare is not None:
            prepare(self.src)
        self.src.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_0)
        self.shadow, self.registry = SceneState(), ids.Registry()
        self.tracker = Tracker(self.shadow, self.registry, editable=lambda u: True, subtree_editable=lambda u: True)
        taken = set()
        for obj in walk(self.src):
            taken.add(self.registry.ensure_uid(obj, taken))
            for tag in tags_of(obj):
                taken.add(self.registry.ensure_uid(tag, taken))
        self.server = SceneState()
        for op in self.tracker.scan(self.src, force=True):
            self.server.apply(op)
        ops = json.loads(json.dumps(self.server.snapshot_ops()))
        self.dst = documents.BaseDocument()
        documents.InsertBaseDocument(self.dst)
        self.shadow2, self.registry2 = SceneState(), ids.Registry()
        self.tracker2 = Tracker(self.shadow2, self.registry2, editable=lambda u: True, subtree_editable=lambda u: True)
        self.applier = Applier(self.shadow2, self.registry2, self.tracker2)
        self.applier.apply(self.dst, ops)
        self.pairs = []
        for obj in walk(self.src):
            other = self.registry2.get(self.registry.uuid_of(obj))
            if other is not None:
                self.pairs.append((obj, other))
        self.by_path = dict((path(a), (a, b)) for a, b in self.pairs)

    def push_edits(self, drop=()):
        """What the host's tracker reports now, sent through the server to the guest (a live edit)."""
        ops = self.tracker.scan(self.src, force=False) or []
        if not ops:
            ops = self.tracker.scan(self.src, force=True) or []
        ops = [o for o in ops if not (self.shadow.get(o["uuid"]) is not None and self.shadow.get(o["uuid"]).name in drop)]
        for op in ops:
            self.server.apply(op)
        self.applier.apply(self.dst, json.loads(json.dumps(ops)))
        for _ in range(50):   # let the guest's glides arrive
            if not self.applier.tweens.step(time.time() + 1000):
                break
        return ops

    def other(self, obj):
        return self.registry2.get(self.registry.uuid_of(obj))
