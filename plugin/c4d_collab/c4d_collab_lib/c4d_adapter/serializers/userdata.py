"""User data: the elements of an object or tag that the user added (a rig control panel: skin choice, scale, tool
sliders, toggles).

The values are ordinary parameters and travel with the others (params.py, key "700:5:0/<id>:<type>:0"), but the
parameter only exists where the element exists: without these definitions a guest has no Skin / Scale / Tool slider
and the XPresso graphs that read them (xpresso.py) have nothing to connect to.

Data section "ud":
    [[element id, data type, creator, {container key: value}], ...]     in the order of the element list
The container is the description of the element (name, GUI, limits, default, cycle labels...) as written by
containers.py. Elements are only added and updated, never removed: another version may know more of them.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import c4d

from ...core.compat import is_fatal
from ...core.log import get_logger
from . import containers

LOG = get_logger("userdata")

MAX_ELEMENTS = 500


def _desc_id(element, dtype, creator):
    return c4d.DescID(c4d.DescLevel(c4d.ID_USERDATA, c4d.DTYPE_SUBCONTAINER, 0), c4d.DescLevel(element, dtype, creator))


def _elements(atom):
    getter = getattr(atom, "GetUserDataContainer", None)
    if getter is None:
        return []
    try:
        return list(getter())[:MAX_ELEMENTS]
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        return []


def dump(atom, uuid_of):
    """The user data elements of `atom` as data, or None when it has none."""
    items = []
    for desc_id, container in _elements(atom):
        if desc_id.GetDepth() != 2:
            continue   # the group headers of the list are no elements
        level = desc_id[1]
        items.append([level.id, level.dtype, level.creator, containers.encode_container(container, uuid_of)])
    return items or None


def load(atom, items, atom_of, uuid_of):
    """Create or update the elements in `items`. Returns the number written."""
    if not isinstance(items, list) or not hasattr(atom, "SetUserDataContainer"):
        return 0
    known = dict((entry[0], entry) for entry in dump(atom, uuid_of) or [])
    written = 0
    for entry in items[:MAX_ELEMENTS]:
        try:
            element, dtype, creator, data = entry
            if element in known and known[element][1:3] == [dtype, creator] and _same(known[element][3], data):
                continue
            container = containers.decode_container(data, atom_of, dtype)
            if atom.SetUserDataContainer(_desc_id(element, dtype, creator), container):
                written += 1
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            LOG.exception("Cannot write a user data element")
    return written


def _same(known, new):
    return containers.same(known, new)
