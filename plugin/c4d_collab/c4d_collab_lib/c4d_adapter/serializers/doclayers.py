"""Layers of the document: their settings travel with the document, and every object, tag and material names its layer
(the parameter ID_LAYER_LINK, see params.py).

Without them a guest shows what the host hides: a rig usually keeps its joints on a layer whose "View" is off, so the
guest drew every bone as rings and lines over the character. The layer flags also decide how things are evaluated
(Animation, Expressions, Deformers, Generators), so a layer that is not there changes the pose, not only the picture.

Document data section "ly": [{"u": uuid, "n": name, "p": uuid of the parent layer or None,
                              "d": {solo, view, render, manager, locked, generators, expressions, animation,
                                    deformers, xref: bool, color: [r, g, b]}}, ...]    parents come before children
Layers are only created and updated, never removed.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import c4d
from c4d import documents

from ...core.compat import is_fatal, string_types
from ...core.log import get_logger
from .. import compat

LOG = get_logger("doclayers")

MAX_LAYERS = 2000


def layers_of(doc):
    """[(layer, parent layer or None)] in tree order (a parent before its children)."""
    found = []
    getter = getattr(doc, "GetLayerObjectRoot", None)   # a stand-in document in the tests has no layers
    root = getter() if getter is not None else None
    stack = [(root.GetDown() if root is not None else None, None)]
    while stack and len(found) < MAX_LAYERS:
        layer, parent = stack.pop()
        while layer is not None and len(found) < MAX_LAYERS:
            found.append((layer, parent))
            if layer.GetDown() is not None:
                stack.append((layer.GetNext(), parent))
                stack.append((layer.GetDown(), layer))
                break
            layer = layer.GetNext()
    return found


def _ensure_uid(layer, taken):
    uid = compat.read_uid(layer)
    if uid is None or uid in taken:
        uid = compat.new_uid()
        compat.write_uid(layer, uid)
    taken.add(uid)
    return uid


# The settings every version has. A key only one version reads (2023 has more than R19) made the two sides differ for
# ever: each saw the other's document data as a change and sent it back every five seconds.
SETTINGS = frozenset(["solo", "view", "render", "manager", "locked", "generators", "expressions", "animation", "deformers", "color"])


def _flags_of(layer, doc):
    data = layer.GetLayerData(doc)
    result = {}
    for key, value in data.items():
        if key not in SETTINGS:
            continue
        if isinstance(value, c4d.Vector):
            result[key] = [value.x, value.y, value.z]
        elif isinstance(value, (bool, int)):   # Python 2.7 on R19 may hand back 0 and 1
            result[key] = bool(value)
    return result


def dump(doc):
    """The layers of `doc` as data (assigning an id to each that has none), or None when there are none."""
    taken = set()
    entries = []
    for layer, parent in layers_of(doc):
        uid = _ensure_uid(layer, taken)
        entries.append({"u": uid, "n": compat.from_c4d_str(layer.GetName()), "p": compat.read_uid(parent) if parent else None,
                        "d": _flags_of(layer, doc)})
    return entries or None


def _settings(entry):
    data = {}
    for key, value in (entry.get("d") or {}).items():
        if isinstance(value, list) and len(value) == 3:
            data[compat.native_str(key)] = c4d.Vector(float(value[0]), float(value[1]), float(value[2]))
        elif isinstance(value, bool):
            data[compat.native_str(key)] = value
    return data


def load(doc, entries):
    """Create the layers that are missing and write the settings of all. Returns the number of layers written."""
    if not isinstance(entries, list):
        return 0
    by_uid = dict((compat.read_uid(layer), layer) for layer, _parent in layers_of(doc))
    written = 0
    for entry in entries[:MAX_LAYERS]:
        try:
            uid = entry["u"]
            layer = by_uid.get(uid)
            if layer is None:
                layer = documents.LayerObject()
                compat.write_uid(layer, uid)
                parent = by_uid.get(entry.get("p"))
                if parent is not None:
                    layer.InsertUnderLast(parent)
                else:
                    layer.InsertUnderLast(doc.GetLayerObjectRoot())
                by_uid[uid] = layer
            name = entry.get("n")
            if isinstance(name, string_types) and compat.from_c4d_str(layer.GetName()) != name:
                layer.SetName(compat.native_str(name))
            wanted = _settings(entry)
            current = layer.GetLayerData(doc)
            if any(current.get(key) != value for key, value in wanted.items()):
                current.update(wanted)
                layer.SetLayerData(doc, current)
            written += 1
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            LOG.exception("Cannot write a layer")
    return written
