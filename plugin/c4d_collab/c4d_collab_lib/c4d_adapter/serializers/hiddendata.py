"""Internal settings of solver tags that are not parameters of their description.

An IK-Spline tag keeps part of its state in its own container, under ids no description names (measured on a real rig:
10000 = 3, probably the number of spline controls). Those never travelled, so on the guest the tag did not move the
points of its spline: the spine curve stayed where the host had it at the frame of the join and the spine, hips and
legs hung on it were wrong at every other frame (up to 3 m; copying these values to the guest tag made the guest spline
follow the host again). Constraint tags keep state there as well (the number of constraints, their offsets).

Tag data section "hd": {container key: value}   int, float, Vector and Matrix values whose key is not a parameter
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import c4d

from ...core.compat import integer_types, is_fatal
from ...core.log import get_logger
from .. import compat
from . import containers

LOG = get_logger("hiddendata")

# IK (1019561), Constraint (1019364), IK-Spline (1019862)
TAG_TYPES = frozenset([1019561, 1019364, 1019862])
SIMPLE = (15, 19, 23, 25)   # DA_LONG, DA_REAL, DA_VECTOR, DA_MATRIX
MAX_KEY = 1000000


def applies(tag):
    return tag.GetType() in TAG_TYPES


def _parameter_ids(atom):
    ids = set()
    try:
        for _bc, desc_id, _group in atom.GetDescription(compat.DESCFLAGS_DESC):
            if desc_id.GetDepth() >= 1:
                ids.add(desc_id[0].id)
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
    return ids


def dump(tag):
    """{key: value} of the hidden settings of the tag, or None."""
    if not applies(tag):
        return None
    container = tag.GetDataInstance()
    named = _parameter_ids(tag)
    result = {}
    for key, value in container:
        if key in named or key > MAX_KEY or container.GetType(key) not in SIMPLE:
            continue
        ok, encoded = containers.encode_value(value, lambda atom: None)
        if ok:
            result[str(key)] = encoded
    return result or None


def load(tag, data):
    """Write the hidden settings into the tag where they are not already so. Returns True when something changed."""
    if not applies(tag) or not isinstance(data, dict):
        return False
    container = tag.GetData()
    current = dump(tag) or {}
    changed = False
    for key, value in data.items():
        if key in current and containers.same(current[key], value):
            continue
        try:
            if containers._store(container, int(key), value, lambda uuid: None):
                changed = True
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            LOG.warning("Cannot write hidden setting %s", key)
    if changed:
        tag.SetData(container)
        tag.SetDirty(c4d.DIRTYFLAGS_DATA)
    return changed
