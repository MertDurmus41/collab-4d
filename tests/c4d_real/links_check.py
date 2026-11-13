# -*- coding: utf-8 -*-
"""A tag that points at an object deeper in the hierarchy must keep that link on the guest (real Cinema 4D).

The guest's scene is built level by level from the server snapshot, so the deeper object does not exist yet when
the tag is built (an IK tag on a thigh naming the end joint). Run:  c4dpy tests/c4d_real/links_check.py
Exit code 1 on failure."""
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

failures = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (" " + str(extra) if extra else ""))
    if not ok:
        failures.append(name)


def uids(registry, doc):
    taken, stack = set(), [doc.GetFirstObject()]
    while stack:
        obj = stack.pop()
        while obj is not None:
            taken.add(registry.ensure_uid(obj, taken))
            tag = obj.GetFirstTag()
            while tag is not None:
                taken.add(registry.ensure_uid(tag, taken))
                tag = tag.GetNext()
            if obj.GetDown() is not None:
                stack.append(obj.GetDown())
            obj = obj.GetNext()


# host: Root (with a Target Expression tag) > A > B > Deep
src = documents.BaseDocument()
root = c4d.BaseObject(c4d.Onull)
root.SetName("Root")
src.InsertObject(root)
parent = root
for name in ("A", "B", "Deep"):
    child = c4d.BaseObject(c4d.Onull)
    child.SetName(name)
    src.InsertObject(child, parent)
    parent = child
tag = root.MakeTag(c4d.Ttargetexpression)
tag[c4d.TARGETEXPRESSIONTAG_LINK] = parent
check("host: the tag points at the deep object", tag[c4d.TARGETEXPRESSIONTAG_LINK] == parent)

shadow, registry = SceneState(), ids.Registry()
tracker = Tracker(shadow, registry, editable=lambda u: True, subtree_editable=lambda u: True)
uids(registry, src)
server = SceneState()
for op in tracker.scan(src, force=True):
    server.apply(op)
ops = json.loads(json.dumps(server.snapshot_ops()))      # what the guest receives: level by level

dst = documents.BaseDocument()
documents.InsertBaseDocument(dst)
shadow2, registry2 = SceneState(), ids.Registry()
tracker2 = Tracker(shadow2, registry2, editable=lambda u: True, subtree_editable=lambda u: True)
Applier(shadow2, registry2, tracker2).apply(dst, ops)

guest_root = dst.SearchObject("Root")
guest_deep = dst.SearchObject("Deep")
check("guest: the scene arrived", guest_root is not None and guest_deep is not None)
guest_tag = guest_root.GetFirstTag()
check("guest: the tag arrived", guest_tag is not None and guest_tag.GetType() == c4d.Ttargetexpression)
link = guest_tag[c4d.TARGETEXPRESSIONTAG_LINK]
check("guest: the tag points at the guest's own deep object", link is not None and link == guest_deep, link)


print("RESULT", "OK" if not failures else "FAILED: %s" % failures)
sys.exit(1 if failures else 0)
