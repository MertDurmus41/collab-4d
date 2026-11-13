# -*- coding: utf-8 -*-
"""Host and guest on different frames, both sending what they change (real Cinema 4D).

A user scrubs the timeline on the guest (178 -> 190) while the host stays on 178; nobody touches the rig. Neither side
may end up in a pose that mixes the frames: the host must show its own frame, the guest its own, as a computer alone
with the scene shows them. Scrubbing the timeline alone must send nothing: before the fix each side sent what its rig
had written (Constraint and XPresso driven objects: head, head and hand attachments, tools, elbows: 16-19 updates per
frame step) as edits, and the other side took them; on a version whose rig does not evaluate fully (R19 before its
port fix) those values stayed where the sender's frame left them.

    c4dpy tests/c4d_real/frames_check.py          the last line says how many checks failed
"""
from __future__ import print_function

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pairlib import Pair, SCENE, at, c4d, path  # noqa: E402
from c4d_collab_lib.c4d_adapter.applier import Applier  # noqa: E402

HOST_FRAME, GUEST_FRAME = 178, 190
SKIP = ("Element_n", "Diamond Spark", "Bend", "Camera", "tongue")   # the tongue (IK-Spline) is a known 3 cm
FAILED = []


def check(name, ok, detail=""):
    print("  %s %s %s" % ("PASS" if ok else "FAIL", name, detail))
    if not ok:
        FAILED.append(name)


def snapshot(pair, doc):
    shot = {}
    for a, b in pair.pairs:
        obj = a if doc is pair.src else b
        shot[path(a)] = obj.GetMg()
    return shot


def off(pair, doc, shot):
    rows = []
    for a, b in pair.pairs:
        obj = a if doc is pair.src else b
        if any(s in path(a) for s in SKIP):
            continue
        d = (obj.GetMg().off - shot[path(a)].off).GetLength()
        if d > 1.0:
            rows.append((d, path(a)))
    return sorted(rows, reverse=True)


def main():
    pair = Pair(SCENE)
    back = Applier(pair.shadow, pair.registry, pair.tracker)   # applies the guest's edits to the host

    def exchange():
        ops = pair.tracker2.scan(pair.dst, force=True) or []
        back.apply(pair.src, json.loads(json.dumps(ops)))
        pair.push_edits()
        for _ in range(50):
            if not back.tweens.step(time.time() + 1000):
                break

    for label, doc, tracker, shadow in (("host", pair.src, pair.tracker, pair.shadow),
                                        ("guest", pair.dst, pair.tracker2, pair.shadow2)):
        at(doc, 170)
        tracker.scan(doc, force=True)
        tracker.scan(doc, force=True)
        at(doc, 190)
        sent = [shadow.get(op["uuid"]).name for op in tracker.scan(doc, force=True) or []
                if shadow.get(op["uuid"]) is not None and "Camera" not in shadow.get(op["uuid"]).name]
        check("%s: moving its own playhead sends nothing (%d updates %s)" % (label, len(sent), sorted(set(sent))[:6]),
              not sent)
    at(pair.src, GUEST_FRAME)
    reference_guest = snapshot(pair, pair.src)      # what the scene looks like on 190
    at(pair.src, HOST_FRAME)
    reference_host = snapshot(pair, pair.src)       # and on 178
    at(pair.dst, HOST_FRAME)
    pair.tracker.scan(pair.src, force=True)
    pair.tracker2.scan(pair.dst, force=True)
    for frame in range(HOST_FRAME + 1, GUEST_FRAME + 1):   # the guest plays on, the host stays
        at(pair.dst, frame)
        exchange()
        at(pair.src, HOST_FRAME)
    for _ in range(3):
        exchange()
        at(pair.src, HOST_FRAME)
        at(pair.dst, GUEST_FRAME)
    host_off = off(pair, pair.src, reference_host)
    guest_off = off(pair, pair.dst, reference_guest)
    check("host stays on its own frame %d (%d objects off, worst %s)" % (
        HOST_FRAME, len(host_off), host_off[:2]), not host_off)
    check("guest shows its own frame %d (%d objects off, worst %s)" % (
        GUEST_FRAME, len(guest_off), guest_off[:2]), not guest_off)
    print("\n%d failed" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
