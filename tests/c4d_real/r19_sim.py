# -*- coding: utf-8 -*-
"""Reproduces on 2023 what the R19 guest did before the fix: Description.GetParameter(id) raised TypeError there, so
no port of an XPresso Object node could be named and those graphs stayed half built.

    c4dpy tests/c4d_real/r19_sim.py normal      (host and guest agree: 0 objects off at every frame)
    c4dpy tests/c4d_real/r19_sim.py r19         (241 objects off by up to 284 cm: head, attachments, tongue; 68 meshes)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pairlib import *
from c4d_collab_lib.c4d_adapter.serializers import xpresso
MODE = sys.argv[1]
if MODE == "r19":   # what an R19 guest did before the fix: GetParameter(id) raises TypeError and no port can be named
    def broken(description, desc_id):
        raise TypeError("Required argument 'ar' (pos 2) not found")
    xpresso._level_name = broken
P = Pair()
SKIP = ("Element_n", "Diamond Spark", "Bend", "Camera")
print("MODE", MODE)
for fr in (0, 60, 100, 185):
    at(P.src, fr); at(P.dst, fr)
    rows = [((a.GetMg().off - b.GetMg().off).GetLength(), path(a)) for a, b in P.pairs if (a.GetMg().off - b.GetMg().off).GetLength() > 1.0 and not any(s in path(a) for s in SKIP)]
    head = [(round(d, 1), p[-40:]) for d, p in sorted(rows, reverse=True) if "head" in p.lower() or "Attachments" in p]
    mesh = 0
    for a, b in P.pairs:
        if a.GetType() == c4d.Opolygon and a.GetName() != "R_Local_Goal" and not a.GetName().startswith("Element"):
            pa, pb = (a.GetDeformCache() or a).GetAllPoints(), (b.GetDeformCache() or b).GetAllPoints()
            if pa and len(pa) == len(pb) and max(((a.GetMg() * x) - (b.GetMg() * y)).GetLength() for x, y in zip(pa, pb)) > 1.0: mesh += 1
    print("frame %3d: %3d objects off (head/attachments among them: %d) meshes off %d  worst %s" % (fr, len(rows), len(head), mesh, head[:3]))
