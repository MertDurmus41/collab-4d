# -*- coding: utf-8 -*-
"""R19 without Global Matrix ports (simulated on 2023): c4dpy tests/c4d_real/r19_mirror_check.py r19  -> 241 objects off before the plugin copies the matrices, 0 after; "normal" -> 0 both."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pairlib import *
from c4d_collab_lib.c4d_adapter.serializers import xpresso
MODE = sys.argv[1]
real = xpresso._add_port
if MODE != "normal":
    xpresso._add_port = lambda node, io, ident: None if ident in (xpresso.GLOBAL_IN, xpresso.GLOBAL_OUT) else real(node, io, ident)
p = Pair()
log(MODE, "mirrors registered:", len(xpresso._MIRRORS))
SKIP = ("Element_n", "Diamond Spark", "Camera", "Bend")
def rep(tag):
    rows = sorted(((a.GetMg().off - b.GetMg().off).GetLength(), path(a)) for a, b in p.pairs if (a.GetMg().off - b.GetMg().off).GetLength() > 1.0 and not any(s in path(a) for s in SKIP))
    head = [r for r in rows if "head" in r[1].lower() or "Attachments" in r[1]]
    log(MODE, tag, "objects off", len(rows), "head/attachments off", len(head), [(round(d, 1), q[-30:]) for d, q in head[-3:]])
for fr in (0, 60, 100, 185):
    at(p.src, fr); p.push_edits(); at(p.dst, fr)
    rep("frame %d before mirrors" % fr)
    if xpresso.apply_mirrors():
        at(p.dst, fr)
    rep("frame %d after mirrors" % fr)
