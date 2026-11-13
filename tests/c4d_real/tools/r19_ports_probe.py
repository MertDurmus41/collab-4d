# -*- coding: utf-8 -*-
"""Run inside a Cinema 4D GUI (Script Manager > open this file > Execute). Round 3: which way of making the Global Matrix
ports of an XPresso Object node works in R19 (the integer ports 30000001 / 40000001 answer None there)? The answer is written
to %LOCALAPPDATA%\Temp\hp\probe_<version>.txt, one file per Cinema 4D version."""
import os
import traceback

import c4d

VERSION = c4d.GetC4DVersion()
OUT = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Temp", "hp", "probe_%s.txt" % VERSION)
GLOBAL_IN, GLOBAL_OUT, LOCAL_IN, LOCAL_OUT = 30000001, 40000001, 30000000, 40000000
VISIBLE = c4d.GV_PORT_FLAG_IS_VISIBLE


def main():
    lines = []

    def w(*a):
        lines.append(" ".join(str(x) for x in a))

    def desc(r):
        return ("port %s type %s" % (r.GetMainID(), r.GetValueType())) if hasattr(r, "GetMainID") else repr(r)

    def attempt(label, fn):
        try:
            r = fn()
            w("  ", label, "->", desc(r))
            return r
        except Exception as e:
            w("  ", label, "raised", repr(e))

    try:
        doc = c4d.documents.BaseDocument()
        c4d.documents.InsertBaseDocument(doc)
        src = c4d.BaseObject(c4d.Onull)
        src.SetName("src")
        doc.InsertObject(src)
        dst = c4d.BaseObject(c4d.Onull)
        dst.SetName("dst")
        doc.InsertObject(dst)
        tag = dst.MakeTag(c4d.Texpresso)
        master = tag.GetNodeMaster()
        root = master.GetRoot()
        w("version", VERSION)

        def fresh(link=None, x=10):
            node = master.CreateNode(root, 400001000, None, x, 10)
            if link is not None:
                node[c4d.GV_OBJECT_OBJECT_ID] = link
            return node

        # 1. the integer ports with every flag / message combination, and the local ports for comparison
        for pid, io in ((GLOBAL_OUT, c4d.GV_PORT_OUTPUT), (LOCAL_OUT, c4d.GV_PORT_OUTPUT), (GLOBAL_IN, c4d.GV_PORT_INPUT), (LOCAL_IN, c4d.GV_PORT_INPUT)):
            for flags in (0, VISIBLE, 3):
                for message in (False, True):
                    node = fresh(src)
                    attempt("int %d io %d flags %d message %s" % (pid, io, flags, message), lambda: node.AddPort(io, pid, flags, message))
                    node.Remove()
        # 2. the same ports as DescIDs
        for pid, io in ((GLOBAL_OUT, c4d.GV_PORT_OUTPUT), (GLOBAL_IN, c4d.GV_PORT_INPUT)):
            for label, make in (("DescID(DescLevel(id))", lambda: c4d.DescID(c4d.DescLevel(pid))),
                                ("DescID(DescLevel(id, DTYPE_MATRIX, 0))", lambda: c4d.DescID(c4d.DescLevel(pid, c4d.DTYPE_MATRIX, 0))),
                                ("DescID(DescLevel(id, DTYPE_SUBCONTAINER, 0))", lambda: c4d.DescID(c4d.DescLevel(pid, c4d.DTYPE_SUBCONTAINER, 0)))):
                node = fresh(src)
                attempt("%s %d" % (label, pid), lambda: node.AddPort(io, make(), VISIBLE, True))
                node.Remove()
        # 3. after a parameter port exists, and with the ports of the other direction first
        node = fresh(src)
        attempt("rel position in (DescID)", lambda: node.AddPort(c4d.GV_PORT_INPUT, c4d.DescID(c4d.DescLevel(c4d.ID_BASEOBJECT_REL_POSITION, c4d.DTYPE_VECTOR, 0)), VISIBLE, True))
        attempt("  then global out int", lambda: node.AddPort(c4d.GV_PORT_OUTPUT, GLOBAL_OUT, VISIBLE, True))
        attempt("  then global in int", lambda: node.AddPort(c4d.GV_PORT_INPUT, GLOBAL_IN, VISIBLE, True))
        w("  ports:", [(p.GetMainID(), p.GetValueType()) for p in node.GetInPorts()], [(p.GetMainID(), p.GetValueType()) for p in node.GetOutPorts()])
        node.Remove()
        # 4. what the Object node of a graph made in the GUI looks like (ports of an existing node, to read the real ids)
        node = fresh(src)
        attempt("RemoveUnusedPorts", lambda: node.RemoveUnusedPorts())
        w("  ports after:", [(p.GetMainID(), p.GetValueType()) for p in node.GetInPorts()], [(p.GetMainID(), p.GetValueType()) for p in node.GetOutPorts()])
        # 5. every integer id that AddPortIsOK accepts near the special ranges
        ok = []
        for base in (30000000, 40000000):
            for offset in range(0, 12):
                for io in (c4d.GV_PORT_INPUT, c4d.GV_PORT_OUTPUT):
                    try:
                        if node.AddPortIsOK(io, base + offset):
                            ok.append((io, base + offset))
                    except Exception:
                        pass
        w("  AddPortIsOK true for", ok)
        c4d.documents.KillDocument(doc)
    except Exception:
        w(traceback.format_exc())
    folder = os.path.dirname(OUT)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    with open(OUT, "w") as handle:
        handle.write("\n".join(lines) + "\nDONE\n")
    print("r19_ports_probe: wrote", OUT)


if __name__ == "__main__":
    main()
