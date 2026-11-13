# -*- coding: utf-8 -*-
"""Reflectance layers on a real Cinema 4D: c4dpy tests/c4d_real/layers_check.py (exit code 1 on failure)."""
from __future__ import print_function
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugin", "c4d_collab"))
import c4d
from c4d_collab_lib.c4d_adapter.serializers import layers, nodes, params

failures = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (" " + str(extra) if extra else ""))
    if not ok:
        failures.append(name)


def strength_ids(mat):
    """(layer index, first-level ids) of a layer's parameters"""
    return [mat.GetReflectionLayerIndex(i).GetDataID() for i in range(mat.GetReflectionLayerCount())]


def rebuild(src, doc):
    data = nodes.dump_shading(src, lambda atom: None)
    dst = c4d.BaseMaterial(c4d.Mmaterial)   # a new material is born with one default layer
    doc.InsertMaterial(dst)
    nodes.load_node(dst, data, lambda uuid: None)
    return data, dst


def layer_view(mat):
    out = []
    for index in range(mat.GetReflectionLayerCount()):
        layer = mat.GetReflectionLayerIndex(index)
        base = layer.GetDataID()
        values = {}
        for _bc, did, _g in mat.GetDescription(c4d.DESCFLAGS_DESC_NONE):
            if did.GetDepth() >= 1 and base <= did[0].id < base + layers.STEP:
                try:
                    v = mat.GetParameter(did, c4d.DESCFLAGS_GET_NONE)
                except Exception:
                    continue
                if isinstance(v, c4d.Vector):
                    v = (round(v.x, 4), round(v.y, 4), round(v.z, 4))
                elif isinstance(v, float):
                    v = round(v, 4)
                elif not isinstance(v, (int, bool, str, type(None))):
                    continue
                values[did[0].id - base] = v
        out.append((layer.GetName(), layer.GetFlags(), values))
    return out


doc = c4d.documents.BaseDocument()

# 1. no layers
plain = c4d.BaseMaterial(c4d.Mmaterial)
doc.InsertMaterial(plain)
plain.RemoveReflectionAllLayers()
data, copy = rebuild(plain, doc)
check("0 layers: dump says []", data.get("rl") == [], data.get("rl"))
check("0 layers: the copy has none", copy.GetReflectionLayerCount() == 0, copy.GetReflectionLayerCount())

# 2. three layers with different ids and edited settings
rich = c4d.BaseMaterial(c4d.Mmaterial)
doc.InsertMaterial(rich)
rich.AddReflectionLayer()
rich.AddReflectionLayer()
rich.AddReflectionLayer()
rich.RemoveReflectionLayerIndex(1)            # ids are no longer a clean run
for index in range(rich.GetReflectionLayerCount()):
    layer = rich.GetReflectionLayerIndex(index)
    layer.SetName("L%d" % index)
    for _bc, did, _g in rich.GetDescription(c4d.DESCFLAGS_DESC_NONE):
        base = layer.GetDataID()
        if did.GetDepth() >= 1 and base <= did[0].id < base + layers.STEP and did[0].dtype == c4d.DTYPE_REAL:
            rich.SetParameter(did, 0.1 * (index + 1), c4d.DESCFLAGS_SET_NONE)
data, copy = rebuild(rich, doc)
check("3 layers: count", copy.GetReflectionLayerCount() == rich.GetReflectionLayerCount(), copy.GetReflectionLayerCount())
check("3 layers: ids differ between the two (so the mapping is really used)",
      strength_ids(copy) != strength_ids(rich), (strength_ids(rich), strength_ids(copy)))
check("3 layers: names, flags and every parameter identical", layer_view(copy) == layer_view(rich))
again = nodes.dump_shading(copy, lambda atom: None)
check("3 layers: dumping the copy gives the same data (no echo)", again == data)

# 3. layers added and removed afterwards
rich.AddReflectionLayer()
data2 = nodes.dump_shading(rich, lambda atom: None)
nodes.load_node(copy, data2, lambda uuid: None)
check("add: copy follows", copy.GetReflectionLayerCount() == 4 and layer_view(copy) == layer_view(rich))
rich.RemoveReflectionAllLayers()
nodes.load_node(copy, nodes.dump_shading(rich, lambda atom: None), lambda uuid: None)
check("remove all: copy follows", copy.GetReflectionLayerCount() == 0)

print("RESULT", "OK" if not failures else "FAILED: %s" % failures)
sys.exit(1 if failures else 0)
