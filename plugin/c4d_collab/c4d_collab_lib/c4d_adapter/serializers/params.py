"""Generic parameter serializer driven by the object's description.

Every parameter with a simple data type is stored as  key -> value  where the
key encodes the full DescID ("id:dtype:creator/id:dtype:creator") so it can be
rebuilt exactly (user data needs the sub-container level).

Encoded values:
    float / int / bool / text            as is
    {"V": [x, y, z]}                     Vector / color (null = component driven by a
                                         track: not sent, the receiver keeps its own)
    {"T": [numerator, denominator]}      BaseTime
    {"L": uuid}                          link to another synced object/tag
    {"I": [[uuid, flags], ...]}          Include/Exclude list (members that are not shared are left out)
Parameters the receiver's version does not know are skipped there.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import c4d

from ...core.compat import integer_types, string_types
from .. import compat
from . import layers

TRANSFORM_IDS = frozenset(value for value in (
    getattr(c4d, "ID_BASEOBJECT_REL_POSITION", None),
    getattr(c4d, "ID_BASEOBJECT_REL_ROTATION", None),
    getattr(c4d, "ID_BASEOBJECT_REL_SCALE", None),
) if value is not None)

# Never synced as parameters: the name travels separately, the others are
# derived views of other values or point at data we do not sync (layers).
EXCLUDED_IDS = compat.type_ids(
    "ID_BASELIST_NAME",
    "ID_BASEOBJECT_ABS_POSITION", "ID_BASEOBJECT_ABS_ROTATION", "ID_BASEOBJECT_ABS_SCALE",
    "ID_BASEOBJECT_GLOBAL_POSITION", "ID_BASEOBJECT_GLOBAL_ROTATION", "ID_BASEOBJECT_GLOBAL_SCALE",
)

SIMPLE_TYPES = compat.type_ids("DTYPE_REAL", "DTYPE_LONG", "DTYPE_BOOL", "DTYPE_STRING", "DTYPE_FILENAME")
VECTOR_TYPES = compat.type_ids("DTYPE_VECTOR", "DTYPE_COLOR")
TIME_TYPES = compat.type_ids("DTYPE_TIME")
LINK_TYPES = compat.type_ids("DTYPE_BASELISTLINK")
INEX_TYPES = compat.type_ids("CUSTOMDATATYPE_INEXCLUDE_LIST")   # Include/Exclude lists: IK objects, cages, ...
SUPPORTED_TYPES = SIMPLE_TYPES | VECTOR_TYPES | TIME_TYPES | LINK_TYPES | INEX_TYPES


def key_of(desc_id):
    levels = []
    for index in range(desc_id.GetDepth()):
        level = desc_id[index]
        levels.append("%d:%d:%d" % (level.id, level.dtype, level.creator))
    return "/".join(levels)


def desc_id_of(key):
    levels = []
    for part in key.split("/"):
        ident, dtype, creator = [int(x) for x in part.split(":")]
        levels.append(c4d.DescLevel(ident, dtype, creator))
    return c4d.DescID(*levels)


FILENAME_TYPES = compat.type_ids("DTYPE_FILENAME")


def is_filename_key(key):
    """True when the last level of a parameter key is a file name parameter."""
    try:
        return int(key.split("/")[-1].split(":")[1]) in FILENAME_TYPES
    except (ValueError, IndexError):
        return False


def _last_dtype(desc_id):
    return desc_id[desc_id.GetDepth() - 1].dtype


def inex_members(value, doc):
    """[(object, flags)] of an Include/Exclude list."""
    members = []
    try:
        for index in range(value.GetObjectCount()):
            obj = value.ObjectFromIndex(doc, index)
            if obj is None:
                continue
            try:
                flags = int(value.GetFlags(index))
            except Exception:
                flags = 1
            members.append((obj, flags))
    except Exception:
        pass
    return members


def encode(value, dtype, uuid_of, masked=None, doc=None):
    """c4d value -> JSON value. Returns (ok, encoded). masked: vector components to leave out;
    doc: the document of the owner (Include/Exclude lists name their members through it)."""
    if value is None and dtype not in LINK_TYPES:
        return False, None
    if dtype in INEX_TYPES:
        items = []
        for obj, flags in inex_members(value, doc):
            uid = uuid_of(obj)
            if uid:
                items.append([uid, flags])
        return True, {"I": items}
    if dtype in VECTOR_TYPES:
        components = [value.x, value.y, value.z]
        for index in masked or ():
            components[index] = None
        return True, {"V": components}
    if dtype in TIME_TYPES:
        return True, {"T": [value.GetNumerator(), value.GetDenominator()]}
    if dtype in LINK_TYPES:
        if value is None:
            return True, {"L": None}
        target = uuid_of(value)
        return (True, {"L": target}) if target else (False, None)
    if isinstance(value, bool) or isinstance(value, integer_types) or isinstance(value, float):
        return True, value
    if isinstance(value, string_types) or isinstance(value, bytes):
        return True, compat.from_c4d_str(value)
    return False, None


def decode(value, atom_of, current=None):
    """JSON value -> c4d value. Returns (ok, decoded). `current` fills null vector components."""
    if isinstance(value, dict):
        if "V" in value:
            components = list(value["V"])
            if None in components:
                if current is None:
                    return False, None
                for index, own in enumerate((current.x, current.y, current.z)):
                    if components[index] is None:
                        components[index] = own
            x, y, z = components
            return True, c4d.Vector(float(x), float(y), float(z))
        if "T" in value:
            numerator, denominator = value["T"]
            return True, c4d.BaseTime(float(numerator), float(denominator))
        if "L" in value:
            if value["L"] is None:
                return True, None
            target = atom_of(value["L"])
            return (True, target) if target is not None else (False, None)
        if "I" in value:
            data = c4d.InExcludeData()
            for entry in value["I"] or ():
                target = atom_of(entry[0]) if entry and isinstance(entry[0], string_types) else None
                if target is not None:   # not there yet: relink() adds it once the whole batch exists
                    data.InsertObject(target, int(entry[1]))
            return True, data
        return False, None
    if isinstance(value, string_types):
        return True, compat.native_str(value)
    if isinstance(value, bool) or isinstance(value, integer_types) or isinstance(value, float):
        return True, value
    return False, None


_ID_KEYS = {}   # (object type, ids) -> [(first-level id, key, desc id, dtype)]: the description is walked once


def _keys_for(atom, only_ids):
    cache_key = (atom.GetType(), frozenset(only_ids))
    found = _ID_KEYS.get(cache_key)
    if found is None:
        found = []
        for _bc, desc_id, _group in atom.GetDescription(compat.DESCFLAGS_DESC):
            if desc_id.GetDepth() < 1 or desc_id[0].id not in only_ids or desc_id[0].id in EXCLUDED_IDS:
                continue
            dtype = _last_dtype(desc_id)
            if dtype in SUPPORTED_TYPES:
                found.append((desc_id[0].id, key_of(desc_id), desc_id, dtype))
        if len(_ID_KEYS) > 512:
            _ID_KEYS.clear()
        _ID_KEYS[cache_key] = found
    return found


def _dump_ids(atom, uuid_of, only_ids, exclude_ids):
    """dump() for a few first-level ids (the transform of a dragged object): no walk over the whole
    description, which dominated the cost of every matrix change on big rigs."""
    result = {}
    for first, key, desc_id, dtype in _keys_for(atom, only_ids):
        masked = None
        if exclude_ids and first in exclude_ids:
            masked = exclude_ids[first]
            if masked is None:
                continue
        try:
            value = atom.GetParameter(desc_id, compat.DESCFLAGS_GET)
        except Exception:
            continue
        ok, encoded = encode(value, dtype, uuid_of, masked if dtype in VECTOR_TYPES else None)
        if ok:
            result[key] = encoded
    return result


def dump(atom, uuid_of, only_ids=None, exclude_ids=None):
    """All simple parameters of atom as {key: value}. only_ids limits to first-level ids.
    exclude_ids: {first-level id: None (drop the parameter) or set of vector component
    indices to leave out} - used for values driven by animation tracks."""
    if only_ids is not None:
        return _dump_ids(atom, uuid_of, only_ids, exclude_ids)
    result = {}
    layer_ids = layers.local_ids(atom) if layers.supported(atom) else None   # keys by layer index
    doc = atom.GetDocument()
    description = atom.GetDescription(compat.DESCFLAGS_DESC)
    for _bc, desc_id, _group in description:
        if desc_id.GetDepth() < 1:
            continue
        first = desc_id[0].id
        if first in EXCLUDED_IDS or (only_ids is not None and first not in only_ids):
            continue
        masked = None
        if exclude_ids and first in exclude_ids:
            masked = exclude_ids[first]
            if masked is None:
                continue
        dtype = _last_dtype(desc_id)
        if dtype not in SUPPORTED_TYPES:
            continue
        try:
            value = atom.GetParameter(desc_id, compat.DESCFLAGS_GET)
        except Exception:
            continue
        ok, encoded = encode(value, dtype, uuid_of, masked if dtype in VECTOR_TYPES else None, doc)
        if ok:
            result[layers.to_canonical(key_of(desc_id), layer_ids)] = encoded
    return result


def relink(atom, values, atom_of):
    """Write the links whose target did not exist yet when the atom was loaded (a tag that points at an
    object deeper in the hierarchy, like an IK tag at its end joint: the snapshot builds level by level).
    Only links that are not already right are written. Returns the number written."""
    written = 0
    for key, value in values.items():
        if not isinstance(value, dict):
            continue
        try:
            if value.get("I"):
                written += _relink_list(atom, key, value, atom_of)
                continue
            if not value.get("L"):
                continue
            target = atom_of(value["L"])
            if target is None:
                continue
            desc_id = desc_id_of(key)
            current = atom.GetParameter(desc_id, compat.DESCFLAGS_GET)
            if current is not None and (current is target or current == target):
                continue
            if atom.SetParameter(desc_id, target, compat.DESCFLAGS_SET):
                written += 1
        except Exception:
            continue
    return written


def _relink_list(atom, key, value, atom_of):
    """An Include/Exclude list whose members did not all exist yet: write it again when it is not what
    the data says (as far as the members exist now)."""
    ok, wanted = decode(value, atom_of)
    if not ok:
        return 0
    doc = atom.GetDocument()
    desc_id = desc_id_of(key)
    want = inex_members(wanted, doc)
    have_value = atom.GetParameter(desc_id, compat.DESCFLAGS_GET)
    have = inex_members(have_value, doc) if have_value is not None else []
    if not want or (len(have) == len(want) and all(a[1] == b[1] and (a[0] is b[0] or a[0] == b[0])
                                                    for a, b in zip(have, want))):
        return 0
    return 1 if atom.SetParameter(desc_id, wanted, compat.DESCFLAGS_SET) else 0


def load(atom, values, atom_of, keys=None):
    """Write values (all, or only `keys`) into atom. Unknown/failed keys are skipped.
    Returns the number of parameters written."""
    written = 0
    layer_ids = layers.local_ids(atom) if layers.supported(atom) else None   # keys by layer index
    for key in (keys if keys is not None else values.keys()):
        if key not in values:
            continue
        value = values[key]
        local = layers.to_local(key, layer_ids)
        current = None
        if isinstance(value, dict) and None in (value.get("V") or ()):
            try:
                current = atom.GetParameter(desc_id_of(local), compat.DESCFLAGS_GET)
            except Exception:
                current = None
        ok, decoded = decode(value, atom_of, current)
        if not ok:
            continue
        try:
            if local.startswith("700:") and is_filename_key(local) and isinstance(decoded, string_types):
                # SetParameter stores the path of a user data element as a plain text (a Filename element then
                # fails every read of it: "CRITICAL: Stop [ge_container.h]" on each evaluation of an XPresso port
                # that reads it); item assignment stores it as a file name.
                atom[desc_id_of(local)] = decoded
                written += 1
            elif atom.SetParameter(desc_id_of(local), decoded, compat.DESCFLAGS_SET):
                written += 1
        except Exception:
            continue
    return written
