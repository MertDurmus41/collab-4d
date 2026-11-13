# -*- coding: utf-8 -*-
"""Writes what the open document knows about the skinned meshes of the two characters to
%LOCALAPPDATA%\\Temp\\hp\\skin_<version>.txt: for each polygon object under "Auges" and "Mert" that has a Weight tag,
its Skin deformer, the joints of the tag (found or missing), the bind matrix (GetGeomMg), where the mesh's points are
before and after the Skin (centre of the points, in world space), and the world position of every joint.
Run it on both sides with Script Manager > open > Execute (R19 host and 2023 guest) while the guest's character is
broken, then compare the two files."""
import os
import traceback

import c4d

VERSION = c4d.GetC4DVersion()
OUT = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Temp", "hp", "skin_%s.txt" % VERSION)
ROOTS = ("Auges", "Mert")


def walk(doc):
    stack = [doc.GetFirstObject()]
    while stack:
        obj = stack.pop()
        while obj is not None:
            yield obj
            if obj.GetDown() is not None:
                stack.append(obj.GetDown())
            obj = obj.GetNext()


def root_of(obj):
    while obj.GetUp() is not None:
        obj = obj.GetUp()
    return obj.GetName()


def vec(v):
    return "(%.1f, %.1f, %.1f)" % (v.x, v.y, v.z)


def centre(obj, points):
    if not points:
        return "-"
    mg = obj.GetMg()
    total = c4d.Vector()
    for p in points:
        total += mg * p
    return vec(total * (1.0 / len(points)))


def main():
    doc = c4d.documents.GetActiveDocument()
    lines = ["version %s doc %r frame %s" % (VERSION, doc.GetDocumentName(),
                                              doc.GetTime().GetFrame(doc.GetFps()))]
    w = lines.append
    joints_seen = {}
    try:
        for obj in walk(doc):
            if root_of(obj) not in ROOTS or obj.GetType() != c4d.Opolygon:
                continue
            tag = obj.GetTag(c4d.Tweights)
            if tag is None:
                continue
            skin = None
            child = obj.GetDown()
            while child is not None:
                if child.GetType() == c4d.Oskin:
                    skin = child
                child = child.GetNext()
            cache = obj.GetDeformCache()
            w("MESH %s | %s | points %d | skin %s enabled=%s | rest centre %s | shown centre %s" % (
                root_of(obj), obj.GetName(), obj.GetPointCount(),
                skin.GetName() if skin else None, skin.GetDeformMode() if skin else None,
                centre(obj, obj.GetAllPoints()), centre(obj, cache.GetAllPoints()) if cache else "no deform cache"))
            try:
                w("   bind GetGeomMg off %s" % vec(tag.GetGeomMg().off))
            except Exception:
                w("   bind GetGeomMg: %s" % traceback.format_exc().splitlines()[-1])
            count = tag.GetJointCount()
            missing = 0
            names = []
            for index in range(count):
                joint = tag.GetJoint(index, doc)
                if joint is None:
                    missing += 1
                    continue
                names.append(joint.GetName())
                joints_seen[(root_of(joint), joint.GetName())] = joint
                state = tag.GetJointRestState(index)
                if index < 3:
                    w("   joint %d %s rest bind off %s len %.2f" % (index, joint.GetName(), vec(state["m_bMg"].off), state["m_Len"]))
            w("   joints %d (missing %d): %s" % (count, missing, ", ".join(names[:12])))
        w("-- every polygon object of the characters: visibility, layer, deformers under it, where its points are shown")
        for obj in walk(doc):
            if root_of(obj) not in ROOTS or obj.GetType() != c4d.Opolygon:
                continue
            deformers = []
            child = obj.GetDown()
            while child is not None:
                if child.GetInfo() & c4d.OBJECT_MODIFIER:
                    deformers.append("%s(type %d, on=%s)" % (child.GetName(), child.GetType(), child.GetDeformMode()))
                child = child.GetNext()
            layer = obj.GetLayerObject(doc)
            layer_text = "-"
            if layer is not None:
                data = layer.GetLayerData(doc) or {}
                layer_text = "%s view=%s" % (layer.GetName(), data.get("view"))
            cache = obj.GetDeformCache()
            w("POLY %s | %s | points %d | editor vis %s | layer %s | deformers %s | rest %s | shown %s" % (
                root_of(obj), obj.GetName(), obj.GetPointCount(), obj.GetEditorMode(), layer_text, deformers or "-",
                centre(obj, obj.GetAllPoints()), centre(obj, cache.GetAllPoints()) if cache else "no deform cache"))
        w("-- joints, world position")
        for (root, name), joint in sorted(joints_seen.items()):
            w("JOINT %s | %s | %s" % (root, name, vec(joint.GetMg().off)))
    except Exception:
        w(traceback.format_exc())
    folder = os.path.dirname(OUT)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    with open(OUT, "w") as handle:
        handle.write("\n".join(lines) + "\nDONE\n")
    print("dump_skin: wrote", OUT)


if __name__ == "__main__":
    main()
