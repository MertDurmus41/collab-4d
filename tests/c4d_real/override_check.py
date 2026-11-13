# -*- coding: utf-8 -*-
"""A hand edit of a keyed (track-driven) parameter reaches the other user, whatever frame they are on.

    c4dpy tests/c4d_real/override_check.py [object name]        (default: controlWaist, the rig's waist control)

The host moves the control by hand on frame 185 without making a key; the guest sits on frame 120. Before the fix the host sent
0 ops and the guest did not move (the value of a keyed parameter was never synced); now one op goes out and the guest shows
the host's value on the components that were edited."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pairlib import *

NAME = sys.argv[1] if len(sys.argv) > 1 else "controlWaist"
p = Pair()
pairs = [(a, b) for a, b in p.pairs if a.GetName() == NAME and path(a).startswith("Mert")] or [(a, b) for a, b in p.pairs if a.GetName() == NAME]
host, guest = pairs[0]
at(p.src, 185)
p.push_edits()
at(p.dst, 120)
before_pos, before_rot = guest.GetRelPos(), guest.GetRelRot()
host.SetRelPos(host.GetRelPos() + c4d.Vector(0, 60, 0))
host.SetRelRot(host.GetRelRot() + c4d.Vector(0.4, 0, 0))
ops = p.push_edits()
moved = (guest.GetRelPos() - before_pos).GetLength(), (guest.GetRelRot() - before_rot).GetLength()
log("OVERRIDE ops sent:", len(ops), "| guest moved: position %.2f, rotation %.3f" % moved, "OK" if ops and moved[0] > 1 and moved[1] > 0.1 else "WRONG")
