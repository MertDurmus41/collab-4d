# -*- coding: utf-8 -*-
"""The "Mesh" deformer (Character > Deformers): it moves a mesh with a cage (a skinned mesh).

Its parameters travel like any other, but the bind data it computes from its Cages list (22 KB for an arm,
hidden, not a parameter) does not. Without it the deformer does nothing on the guest and the mesh it is
meant to follow stays where it was bound: parts of the character float around. The bind is the same on every
Cinema 4D (measured on 2023: same Cages -> same deformed points, at any frame), so the guest does not need
the data, it needs the deformer to be initialised again once its Cages list is in place. Python 2.7 and 3.x."""
from __future__ import absolute_import, division, print_function, unicode_literals

import c4d

from ...core.log import get_logger

LOG = get_logger("deformers")

MESH_DEFORMER = 1024543   # object type id (measured on 2023; the id of a plugin object does not change)
CAGES_ID = 1008           # its Include/Exclude list of cages
INITIALIZE_ID = 1001      # its "Initialize" button
# Tags Initialize puts on a cage on 2023 (Display: its own shading mode; Compositing: not seen by the camera), measured
# with c4dpy on Fight_test11.c4d. The host's cages do not have them.
INITIALIZE_TAGS = frozenset([getattr(c4d, "Tdisplay", 5613), getattr(c4d, "Tcompositing", 5637)])


def is_mesh_deformer(atom):
    try:
        return atom.GetType() == MESH_DEFORMER
    except Exception:
        return False


def touches_cages(keys):
    """True when the parameter keys of a patch include the Cages list (None = every parameter)."""
    return keys is None or any(key.startswith("%d:" % CAGES_ID) for key in keys)


def has_cages(values):
    """The data of the deformer names at least one cage."""
    for key, value in (values or {}).items():
        if key.startswith("%d:" % CAGES_ID) and isinstance(value, dict) and value.get("I"):
            return True
    return False


def cage_uuids(values):
    """The uuids of the cages the data names."""
    found = []
    for key, value in (values or {}).items():
        if key.startswith("%d:" % CAGES_ID) and isinstance(value, dict):
            found.extend(item[0] for item in value.get("I") or [] if item and isinstance(item[0], type(key)))
    return found


def initialize(atom):
    """Press the deformer's Initialize button. Returns True when Cinema 4D accepted it."""
    try:
        return bool(atom.Message(c4d.MSG_DESCRIPTION_COMMAND, {"id": c4d.DescID(c4d.DescLevel(INITIALIZE_ID))}))
    except Exception:
        LOG.exception("Could not initialise the Mesh deformer")
        return False
