# -*- coding: utf-8 -*-
"""Live edits of a real rig on the host, followed on a guest: stretched limbs, joining while stretched, the head
world/local switch (real Cinema 4D; one c4dpy builds the host scene and the guest copy through the plugin's own
scanner, snapshot and applier).

    c4dpy tests/c4d_real/limb_check.py [scene.c4d]

Measured on Fight_test11.c4d (2023) before the fixes: a goal dragged beyond the reach of a leg (IK Stretch) left the
guest's thigh at scale 2.0, 2.5, ... while the host's stayed at 1.5 (solver results sent as edits were stretched a
second time by the guest's own solver); a guest that joined while the leg was stretched started at 2.0; the XPresso
ports "world"/"local" and "Strength" of Constraint tags drove parameters the tag does not use on the guest.
The last line says how many checks failed (c4dpy's own exit status is not reliable).
"""
from __future__ import print_function

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pairlib import Pair, SCENE, at, c4d, path, tags_of, walk  # noqa: E402

FRAME = 100
TOLERANCE = 1.0   # cm: both sides run the same solver on the same inputs
SCENE_FILE = next((a for a in sys.argv[1:] if a.lower().endswith(".c4d")), SCENE)
LEG_GOAL = "Mert/Rig Scale/goals/L_Foot_Goal"
ARM = "Mert/Rig Scale/joints/controlWaist/1/2/3/4/5/6/7/8/9/10/11/12/13/%s_Shoulder/%s_Local_Goal"
CHAIN = ("l_knee", "l_heel", "l_toe", "r_knee", "r_heel", "l_elbow", "r_elbow", "l_hand", "r_hand", "13", "head")
FAILED = []


def check(name, ok, detail=""):
    print("  %s %s %s" % ("PASS" if ok else "FAIL", name, detail))
    if not ok:
        FAILED.append(name)


def evaluate(pair):
    at(pair.src, FRAME)
    at(pair.dst, FRAME)


def worst_object(pair, names):
    worst = (0.0, "")
    for a, b in pair.pairs:
        if a.GetName() in names and path(a).startswith("Mert"):
            worst = max(worst, ((a.GetMg().off - b.GetMg().off).GetLength(), a.GetName()))
    return worst


def worst_mesh(pair):
    worst = (0.0, "")
    for a, b in pair.pairs:
        if a.GetType() != c4d.Opolygon or a.GetName() == "R_Local_Goal" or a.GetName().startswith("Element"):
            continue   # a control box the host never re-evaluated; an effect with random elements
        shown_a, shown_b = a.GetDeformCache() or a, b.GetDeformCache() or b
        points_a, points_b = shown_a.GetAllPoints(), shown_b.GetAllPoints()
        if points_a and len(points_a) == len(points_b):
            world_a, world_b = a.GetMg(), b.GetMg()
            worst = max(worst, (max(((world_a * x) - (world_b * y)).GetLength() for x, y in zip(points_a, points_b)),
                                a.GetName()))
    return worst


def drag(pair, goal_path, axis, label):
    """Drag a goal away from where it is, step by step and back, the way an animator does."""
    goal, _ = pair.by_path[goal_path]
    for track in list(goal.GetCTracks()):
        track.Remove()   # an animated goal is put back by its animation on every evaluation
    pair.src.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_0)
    pair.push_edits()
    evaluate(pair)
    base = goal.GetRelPos()
    worst = (0.0, "", 0)
    for step in (30, 60, 100, 150, 220, 300, 200, 100, 0):
        goal.SetRelPos(base + axis * step)
        pair.src.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_0)
        pair.push_edits()
        evaluate(pair)
        moved, name = worst_object(pair, CHAIN)
        bent, mesh = worst_mesh(pair)
        worst = max(worst, (max(moved, bent), "%s/%s" % (name, mesh), step))
    check("%s: guest follows the host (worst %.2f cm at +%d, %s)" % (label, worst[0], worst[2], worst[1]),
          worst[0] < TOLERANCE)


def stretched_leg_join():
    def prepare(doc):
        for obj in walk(doc):
            if path(obj) == LEG_GOAL:
                for track in list(obj.GetCTracks()):
                    track.Remove()
                obj.SetRelPos(obj.GetRelPos() + c4d.Vector(0, 0, 250))
        at(doc, FRAME)

    pair = Pair(SCENE_FILE, prepare=prepare)
    evaluate(pair)
    thigh_host, thigh_guest = pair.by_path["Mert/Rig Scale/joints/controlWaist/Hip Controller/Null.0/l_upper_leg"]
    check("joining while the leg is stretched: thigh scale host %.2f guest %.2f" % (
        thigh_host.GetRelScale().y, thigh_guest.GetRelScale().y),
        abs(thigh_host.GetRelScale().y - thigh_guest.GetRelScale().y) < 0.01)
    moved, name = worst_object(pair, CHAIN)
    check("joining while the leg is stretched: worst joint %.2f cm (%s)" % (moved, name), moved < TOLERANCE)


def head_switch(pair):
    root, _ = pair.by_path["Mert"]
    head_path = next(p for p in pair.by_path if p.startswith("Mert/") and p.endswith("/13/head"))
    head, head_copy = pair.by_path[head_path]
    host_tag = next(t for t in tags_of(head) if t.GetType() == 1019364)
    guest_tag = next(t for t in tags_of(head_copy) if t.GetType() == 1019364)

    def weights(tag):
        return [round(tag.GetParameter(c4d.DescID(c4d.DescLevel(n, c4d.DTYPE_REAL, 0)), c4d.DESCFLAGS_GET_0), 3)
                for n in (10002, 10012)]

    for did, container in root.GetUserDataContainer():
        if container[c4d.DESC_NAME] != "World Head":
            continue
        for value in (0, 1, 0, 1):
            root[did] = value
            pair.src.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_0)
            pair.push_edits()
            evaluate(pair)
            gap = (head.GetMg().off - head_copy.GetMg().off).GetLength()
            check("head world/local = %d: weights host %s guest %s, head %.2f cm apart" % (
                value, weights(host_tag), weights(guest_tag), gap),
                weights(host_tag) == weights(guest_tag) and gap < TOLERANCE)
        return
    check("the rig has a 'World Head' switch", False)


def graph_ports(pair):
    """The XPresso ports of a Constraint tag drive the parameters the tag really uses, on both sides: this rig has
    Transform constraints only, whose weights are 10002, 10012 and whose strength is 2116; the Parent group's 2203
    and the "Weight" template 8502 stand for nothing there (the old name lookup took them)."""
    from c4d_collab_lib.c4d_adapter.serializers import xpresso
    unused = set(["2203"] + [str(n) for n in range(8500, 8508)])
    used, wrong = {}, []
    for label, doc, uuid_of in (("host", pair.src, pair.registry.uuid_of), ("guest", pair.dst, pair.registry2.uuid_of)):
        for obj in walk(doc):
            if not path(obj).startswith("Mert"):
                continue
            for tag in tags_of(obj):
                if tag.GetType() != xpresso.TAG_TYPE:
                    continue
                graph = xpresso.dump(tag, uuid_of)
                for node, spec in zip([n for n, _ in xpresso.graph_nodes(tag)], (graph or {}).get("n", [])):
                    atom = xpresso._linked_atom(node, doc) if node.GetOperatorID() == xpresso.OBJECT_OPERATOR else None
                    if atom is None or atom.GetType() != xpresso.CONSTRAINT_TAG:
                        continue
                    for port in spec["in"]:
                        key = port.get("d", "").split(":")[0]
                        used.setdefault(label, set()).add(key)
                        if key in unused:
                            wrong.append("%s %s" % (label, key))
    check("Constraint ports drive 10002/10012/2116 on host and guest: host %s guest %s" % (
        sorted(used.get("host", [])), sorted(used.get("guest", []))),
        used.get("host") == used.get("guest") and "10002" in used.get("host", ()) and not wrong, str(wrong[:4]))


def main():
    pair = Pair(SCENE_FILE)
    evaluate(pair)
    pair.tracker.scan(pair.src, force=True)
    print("== legs")
    drag(pair, LEG_GOAL, c4d.Vector(0, 0, 1), "left leg goal along z")
    drag(pair, LEG_GOAL, c4d.Vector(0, 1, 0), "left leg goal along y")
    print("== arms")
    drag(pair, ARM % ("L", "L"), c4d.Vector(0, 0, 1), "left hand goal")
    drag(pair, ARM % ("R", "R"), c4d.Vector(1, 0, 0), "right hand goal")
    print("== head")
    head_switch(pair)
    graph_ports(pair)
    print("== join")
    stretched_leg_join()
    print("\n%d failed" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
