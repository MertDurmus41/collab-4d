# -*- coding: utf-8 -*-
"""Writes what the foot rig looks like in the open document (R19 GUI: Script Manager > open > Execute; c4dpy: run it, it loads
the Fight_test11 scene) to %LOCALAPPDATA%\Temp\hp\foot_<version>.txt: the tags of the heel / toe / leg joints with every
parameter, and where the joints are. Run on the host (2023) and on the guest (R19), then compare the two files."""
import os
import traceback

import c4d

VERSION = c4d.GetC4DVersion()
OUT = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Temp", "hp", "foot_%s.txt" % VERSION)
SCENE = os.environ.get("C4D_SCENE", "")   # c4dpy only: the scene to load (set C4D_SCENE); the R19 GUI uses the open document
NAMES = ("l_heel", "l_toe", "l_end", "l_knee", "l_upper_leg", "L_Foot_Goal", "r_heel", "r_toe")


def walk(doc):
    stack = [doc.GetFirstObject()]
    while stack:
        obj = stack.pop()
        while obj is not None:
            yield obj
            if obj.GetDown() is not None:
                stack.append(obj.GetDown())
            obj = obj.GetNext()


def short(value):
    if isinstance(value, c4d.BaseList2D):
        return "<%s %s>" % (value.GetTypeName(), value.GetName())
    if isinstance(value, float):
        return "%.4f" % value
    if isinstance(value, c4d.Vector):
        return "(%.3f %.3f %.3f)" % (value.x, value.y, value.z)
    text = repr(value)
    return text if len(text) < 90 else text[:90] + "..."


def main():
    lines = []
    w = lines.append
    try:
        doc = c4d.documents.GetActiveDocument()
        w("version %s doc %s frame %s" % (VERSION, doc.GetDocumentName() if doc else None, None))
        if doc is None or "Mert" not in [o.GetName() for o in walk(doc)]:
            doc = c4d.documents.LoadDocument(SCENE, c4d.SCENEFILTER_OBJECTS)
            c4d.documents.InsertBaseDocument(doc)
        if os.environ.get("C4D_DUMP_FRAME"):   # c4dpy: look at the frame the other file was written on
            doc.SetTime(c4d.BaseTime(int(os.environ["C4D_DUMP_FRAME"]), doc.GetFps()))
            doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_0)
            doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_0)
        w("frame %d" % doc.GetTime().GetFrame(doc.GetFps()))
        for obj in walk(doc):
            if obj.GetName() not in NAMES or obj.GetTypeName() == "Polygon" and obj.GetName() != "L_Foot_Goal":
                continue
            parents = []
            up = obj.GetUp()
            while up is not None:
                parents.append(up.GetName())
                up = up.GetUp()
            if "Mert" not in parents:
                continue
            mg = obj.GetMg()
            w("== %s [%s] under %s" % (obj.GetName(), obj.GetTypeName(), "/".join(reversed(parents))[-60:]))
            w("   global pos %s  rot(deg) %s  scale %s" % (short(mg.off), short(c4d.utils.MatrixToHPB(mg) * 57.29578), short(c4d.Vector(mg.v1.GetLength(), mg.v2.GetLength(), mg.v3.GetLength()))))
            w("   local  rot(deg) %s" % short(obj.GetRelRot() * 57.29578))
            tag = obj.GetFirstTag()
            while tag is not None:
                if tag.GetType() in (1019364, 1019561):
                    w("   -- tag %s %s" % (tag.GetTypeName(), tag.GetName()))
                    try:
                        for container, desc_id, _group in tag.GetDescription(c4d.DESCFLAGS_DESC_0):
                            try:
                                value = tag[desc_id]
                            except Exception:
                                continue
                            label = container[c4d.DESC_NAME] if container.GetType(c4d.DESC_NAME) else ""
                            w("      %s %s = %s" % ([desc_id[i].id for i in range(desc_id.GetDepth())], label, short(value)))
                    except Exception:
                        w("      description failed: " + traceback.format_exc().splitlines()[-1])
                tag = tag.GetNext()
    except Exception:
        w(traceback.format_exc())
    folder = os.path.dirname(OUT)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    with open(OUT, "w") as handle:
        handle.write("\n".join(lines) + "\nDONE\n")
    print("dump_foot: wrote", OUT)


if __name__ == "__main__":
    main()
