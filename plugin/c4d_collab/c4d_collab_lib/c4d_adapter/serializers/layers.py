# -*- coding: utf-8 -*-
"""Reflectance layers of the standard material.

A material owns a list of layers (Default Specular, GGX, ...) and every layer owns a block of parameters
in the description: layer id + offset. Two things make the generic parameter walk not enough:

  * a new material is born with one default layer. A material without layers (the common case in scenes
    from older versions) would get that layer on the other side, a specular highlight nobody drew: the
    textures seemed to "reflect light" and change;
  * the layer ids are handed out by each Cinema 4D on its own (526336, 526848, ...) and differ after
    layers were added and removed, so the parameter keys of the same layer differ between two users.

So a material's data carries "rl" = [[flags, name], ...] in layer order, and its layer parameters are keyed
by the layer's INDEX (canonical id = LAYER_BASE + STEP * index) instead of the local id. On the way in
the layers are first made to match (count, flags, names), then canonical keys are translated back to the
local ids. Python 2.7 and 3.x."""
from __future__ import absolute_import, division, print_function, unicode_literals

import logging

import c4d

from .. import compat

LOG = logging.getLogger("c4d_collab.layers")

LAYER_BASE = 526336   # data id of the first layer of a new material
STEP = 512            # ids of one layer: [layer id, layer id + STEP)


def supported(atom):
    """True for the standard material in a Cinema 4D that has the Reflectance API (R16+)."""
    try:
        return (isinstance(atom, c4d.BaseMaterial) and atom.GetType() == c4d.Mmaterial
                and hasattr(atom, "GetReflectionLayerCount") and hasattr(atom, "RemoveReflectionAllLayers"))
    except Exception:
        return False


def local_ids(mat):
    """The data ids of the layers, top layer first."""
    try:
        return [mat.GetReflectionLayerIndex(i).GetDataID() for i in range(mat.GetReflectionLayerCount())]
    except Exception:
        return []


def _split(key):
    head, sep, tail = key.partition("/")
    ident, rest = head.split(":", 1)
    return int(ident), rest, sep + tail


def _translate(key, source_ids, target_base_of):
    """Move the first level of `key` from the layer it belongs to in source_ids to the corresponding one."""
    try:
        first, rest, tail = _split(key)
    except ValueError:
        return key
    for index, layer_id in enumerate(source_ids):
        if layer_id <= first < layer_id + STEP:
            return "%d:%s%s" % (target_base_of(index) + first - layer_id, rest, tail)
    return key


def to_canonical(key, ids):
    """Local key -> key by layer index (what travels)."""
    return _translate(key, ids, lambda index: LAYER_BASE + STEP * index) if ids else key


def to_local(key, ids):
    """Key by layer index -> this Cinema 4D's key."""
    if not ids:
        return key
    try:
        first, rest, tail = _split(key)
    except ValueError:
        return key
    index, offset = divmod(first - LAYER_BASE, STEP)
    if first >= LAYER_BASE and 0 <= index < len(ids):
        return "%d:%s%s" % (ids[index] + offset, rest, tail)
    return key


def dump(mat):
    """[[flags, name], ...] in layer order (empty: the material has no layers)."""
    result = []
    try:
        for index in range(mat.GetReflectionLayerCount()):
            layer = mat.GetReflectionLayerIndex(index)
            result.append([int(layer.GetFlags()), compat.from_c4d_str(layer.GetName())])
    except Exception:
        LOG.exception("Could not read the reflectance layers")
    return result


def has_extra(mat, wanted):
    """Does the material hold more layers than `wanted` (a layer nobody drew: a specular highlight)?"""
    try:
        return mat.GetReflectionLayerCount() > len(wanted)
    except Exception:
        return False


def sync(mat, wanted):
    """Make the layers of mat as many (and as flagged and named) as `wanted`. The parameters of the
    layers are loaded afterwards (params.load). Returns True when layers were added or removed."""
    changed = False
    try:
        have = mat.GetReflectionLayerCount()
        if not wanted:
            if have:
                mat.RemoveReflectionAllLayers()
                changed = True
        else:
            while have > len(wanted):
                mat.RemoveReflectionLayerIndex(have - 1)
                have -= 1
                changed = True
            while have < len(wanted):
                mat.AddReflectionLayer()
                have += 1
                changed = True
        for index, entry in enumerate(wanted):
            layer = mat.GetReflectionLayerIndex(index)
            flags, name = int(entry[0]), compat.native_str(entry[1])
            if layer.GetFlags() != flags:
                layer.SetFlags(flags)
            if compat.from_c4d_str(layer.GetName()) != entry[1]:
                layer.SetName(name)
    except Exception:
        LOG.exception("Could not match the reflectance layers")
    return changed
