"""BaseContainer content as JSON: the settings of XPresso operators and of user data elements.

    int / float / bool / text            as is
    {"V": [x, y, z]}  {"M": [12 floats]}  {"T": [numerator, denominator]}     Vector, Matrix, BaseTime
    {"L": uuid or None}                  link to a synced object, tag or material (None: empty or not shared)
    {"C": {key: value}}                  nested container
    {"S": {"k": [knot, ...], "r": {...}}}   spline data (a Range Mapper curve)
    {"D": key}                           a description id (see params.key_of)
    {"F": text}                          a file name (a container tells it from a text by its type)
Keys are written as text.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import c4d

from ...core.compat import integer_types, is_fatal, string_types
from ...core.log import get_logger
from .. import compat
from . import params

LOG = get_logger("containers")

FILENAME_TYPE = getattr(c4d, "DA_FILENAME", 131)
SPLINE_KEYS = ("vPos", "lFlagsSettings", "bSelect", "vTangentLeft", "vTangentRight", "interpol")


def _encode_spline(spline):
    if hasattr(spline, "GetKnots"):
        knots = spline.GetKnots()
    else:
        knots = [spline.GetKnot(index) for index in range(spline.GetKnotCount())]
    encoded = []
    for knot in knots:
        item = {}
        for key in SPLINE_KEYS:
            value = knot.get(key)
            if isinstance(value, c4d.Vector):
                value = [value.x, value.y, value.z]
            item[key] = value
        encoded.append(item)
    result = {"k": encoded}
    try:
        result["r"] = dict(spline.GetRange())
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
    return {"S": result}


def encode_value(value, uuid_of):
    if value is None:
        return True, {"L": None}
    if isinstance(value, bool) or isinstance(value, integer_types) or isinstance(value, float):
        return True, value
    if isinstance(value, string_types) or isinstance(value, bytes):
        return True, compat.from_c4d_str(value)
    if isinstance(value, c4d.Vector):
        return True, {"V": [value.x, value.y, value.z]}
    if isinstance(value, c4d.Matrix):
        return True, {"M": [value.off.x, value.off.y, value.off.z, value.v1.x, value.v1.y, value.v1.z,
                            value.v2.x, value.v2.y, value.v2.z, value.v3.x, value.v3.y, value.v3.z]}
    if isinstance(value, c4d.DescID):
        return True, {"D": params.key_of(value)}
    if isinstance(value, c4d.BaseTime):
        return True, {"T": [value.GetNumerator(), value.GetDenominator()]}
    if isinstance(value, c4d.BaseContainer):
        return True, {"C": encode_container(value, uuid_of)}
    if isinstance(value, c4d.SplineData):
        return True, _encode_spline(value)
    if isinstance(value, c4d.C4DAtom) and hasattr(value, "GetType"):
        return True, {"L": uuid_of(value)}   # None when the atom is not shared: the link stays empty
    return False, None


def encode_container(container, uuid_of):
    result = {}
    for key, value in container:
        ok, encoded = encode_value(value, uuid_of)
        if ok and isinstance(encoded, string_types) and container.GetType(key) == FILENAME_TYPE:
            encoded = {"F": encoded}
        if ok:
            result[str(key)] = encoded
    return result


def _decode_spline(data):
    spline = c4d.SplineData()
    knots = data.get("k") or []
    spline.DeleteAllPoints()
    spline.MakePointBuffer(len(knots))
    for index, knot in enumerate(knots):
        vectors = [c4d.Vector(*knot[key]) for key in ("vPos", "vTangentLeft", "vTangentRight")]
        spline.SetKnot(index, vectors[0], knot.get("lFlagsSettings", 0), bool(knot.get("bSelect")), vectors[1],
                       vectors[2], knot.get("interpol", 0))
    wanted = data.get("r")
    if isinstance(wanted, dict):
        try:
            spline.SetRange(wanted["xmin"], wanted["xmax"], wanted["xsteps"], wanted["ymin"], wanted["ymax"],
                            wanted["ysteps"])
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
    return spline


def _store(container, key, value, atom_of):
    """Write one decoded value into a container. Returns False when the value cannot be written."""
    if isinstance(value, dict):
        if "L" in value:
            target = atom_of(value["L"]) if value["L"] else None
            try:
                container.SetLink(key, target)
            except TypeError:
                if target is not None:
                    raise
                container.RemoveData(key)   # R19's SetLink refuses None: an empty link is no entry there
            return True
        if "V" in value:
            container.SetVector(key, c4d.Vector(*value["V"]))
        elif "M" in value:
            v = value["M"]
            container.SetMatrix(key, c4d.Matrix(c4d.Vector(*v[0:3]), c4d.Vector(*v[3:6]), c4d.Vector(*v[6:9]),
                                                c4d.Vector(*v[9:12])))
        elif "T" in value:
            container.SetTime(key, c4d.BaseTime(value["T"][0], value["T"][1]))
        elif "C" in value:
            container.SetContainer(key, decode_container(value["C"], atom_of))
        elif "F" in value:
            path = compat.native_str(value["F"])
            container.SetFilename(key, c4d.Filename(path) if hasattr(c4d, "Filename") else path)   # R19: a class
        elif "D" in value:
            container.SetData(key, params.desc_id_of(value["D"]))
        elif "S" in value:
            container[key] = _decode_spline(value["S"])
        else:
            return False
        return True
    if isinstance(value, bool):
        container.SetBool(key, value)
    elif isinstance(value, integer_types):
        (getattr(container, "SetInt32", None) or container.SetLong)(key, int(value))   # SetLong: before R18
    elif isinstance(value, float):
        (getattr(container, "SetFloat", None) or container.SetReal)(key, value)   # SetReal: before R18
    elif isinstance(value, string_types):
        container.SetString(key, compat.native_str(value))
    else:
        return False
    return True


def decode_container(data, atom_of, container_id=None):
    """A BaseContainer from encoded data. `container_id` is the id of the container itself: the one of a user data
    element is its data type (a copy with another id is a different element)."""
    container = c4d.BaseContainer() if container_id is None else c4d.BaseContainer(container_id)
    for key, value in (data or {}).items():
        try:
            _store(container, int(key), value, atom_of)
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            LOG.warning("Cannot write XPresso setting %s", key)
    return container


def same(known, new, tolerance=1e-6):
    """Do two encoded values say the same (floats within a hair)?"""
    if isinstance(known, dict) and isinstance(new, dict):
        return set(known) == set(new) and all(same(known[key], new[key], tolerance) for key in known)
    if isinstance(known, list) and isinstance(new, list):
        return len(known) == len(new) and all(same(a, b, tolerance) for a, b in zip(known, new))
    if isinstance(known, bool) or isinstance(new, bool):
        return known == new
    if isinstance(known, (int, float)) and isinstance(new, (int, float)):
        return abs(known - new) <= tolerance * max(1.0, abs(known))
    return known == new
