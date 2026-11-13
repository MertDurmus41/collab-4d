# -*- coding: utf-8 -*-
"""A guest on another Cinema 4D version reads some received values back differently (R19 host, 2023 guest: UVW,
Texture and Phong tags, joint transforms). Selecting such an object moved its counters, and the difference went out
as an edit, or, when the host had the object locked, was "undone" from the shadow every scan (the guest's Auges rig
left its body, parts went flat-coloured). Simulated here on 2023: the snapshot gives every Phong tag an angle limit
above the 180 degrees Cinema 4D allows, so the guest reads back 180.

    c4dpy tests/c4d_real/version_echo_check.py [old]     (old: without the read-back baseline, to see the bug)

The last line says how many checks failed (c4dpy's own exit status is not reliable)."""
from __future__ import print_function
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pairlib
from pairlib import c4d, documents, ids, path, walk, tags_of, log, SceneState, Tracker, Applier, SCENE

ANGLE = "%d:19:5612" % c4d.PHONGTAG_PHONG_ANGLE
FAILED = []
if "old" in sys.argv:
    Tracker._capture_applied = lambda self, uuid, atom: None


def check(name, ok, detail=""):
    print("  %s %s %s" % ("PASS" if ok else "FAIL", name, detail))
    if not ok:
        FAILED.append(name)


src = documents.LoadDocument(SCENE, c4d.SCENEFILTER_OBJECTS | c4d.SCENEFILTER_MATERIALS)
documents.InsertBaseDocument(src)
src.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_0)
shadow, registry = SceneState(), ids.Registry()
tracker = Tracker(shadow, registry)
server = SceneState()
for op in tracker.scan(src, force=True):
    server.apply(op)
ops = json.loads(json.dumps(server.snapshot_ops()))
changed = 0
for op in ops:
    params = (op.get("data") or {}).get("p")
    if op.get("op") == "create" and op.get("kind") == "tag" and isinstance(params, dict) and ANGLE in params:
        params[ANGLE] = 4.0   # 229 degrees: Cinema 4D keeps at most pi
        changed += 1
log("Phong tags given an angle this version cannot hold:", changed)

dst = documents.BaseDocument()
documents.InsertBaseDocument(dst)
shadow2, registry2 = SceneState(), ids.Registry()
locked = set()
tracker2 = Tracker(shadow2, registry2, editable=lambda u: u not in locked, subtree_editable=lambda u: u not in locked)
applier = Applier(shadow2, registry2, tracker2)
applier.apply(dst, ops)
dst.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_0)
tracker2.scan(dst, force=True)

phong_owners = [o for o in walk(dst) if any(t.GetType() == c4d.Tphong for t in tags_of(o))][:40]
log("objects with Phong tags to click:", len(phong_owners))


def click_all(label):
    sent, restored = 0, 0
    for obj in phong_owners:
        dst.SetActiveObject(obj, c4d.SELECTION_NEW)
        for tag in tags_of(obj):
            tag.SetDirty(c4d.DIRTYFLAGS_DATA)   # what the GUI does to a selected object's tags (live: "update Phong")
        obj.SetDirty(c4d.DIRTYFLAGS_DATA)
        dst.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_0)
        ops = tracker2.scan(dst, force=True) or []
        sent += sum(1 for o in ops if "patch" in o and ANGLE in json.dumps(o))
        restored += len(tracker2.restore)
        if tracker2.restore:
            applier.restore(dst, tracker2.restore)
        tracker2.restore = set()
    return sent, restored


locked |= set(registry2.uuid_of(o) for o in phong_owners) | set(registry2.uuid_of(t) for o in phong_owners for t in tags_of(o))
_, restored = click_all("locked")
check("objects the host has locked are not undone (restored %d)" % restored, restored == 0)
locked.clear()
sent, _ = click_all("free")
check("selecting objects sends no Phong edit (sent %d)" % sent, sent == 0)

# a real edit still goes out
tag = next(t for t in tags_of(phong_owners[0]) if t.GetType() == c4d.Tphong)
locked.clear()
tag[c4d.PHONGTAG_PHONG_ANGLE] = 0.5
dst.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_0)
ops = tracker2.scan(dst, force=True) or []
check("a real Phong edit is still sent", any(ANGLE in json.dumps(o) for o in ops), str(len(ops)))
print("%d check(s) failed" % len(FAILED))
