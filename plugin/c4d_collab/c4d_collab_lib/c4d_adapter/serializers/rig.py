"""Character rigs: skin weights (the Weight tag).

A Weight tag says how strongly each point of a mesh follows each joint. Without it a skinned character
stays in its bind pose or collapses (found in the first live run with a real rig). The joints
themselves, the Skin deformer and the constraint/IK tags are ordinary objects and tags and travel
through the generic parameter serializer.

Tag data section "cw":
    {"j": [joint uuid, ...]          joints in the tag's order
     "r": {joint uuid: [49 floats]}  rest state: bind matrix, its inverse, 2 more matrices (12 numbers
                                     each: offset + 3 axes) and the joint length
     "w": {joint uuid: packed ints}  weights per point, quantised to 0..65535 (what C4D stores itself)
     "gm": [12 floats]}              the matrix of the mesh when it was bound (GetGeomMg). Without it the guest binds
                                     the mesh where it stands now and the Skin moves every point by the difference
                                     (measured on a real rig: 17 m; the host had 37.5/112.5/0, the guest the current
                                     position of the mesh)
Joints are named by uuid, so the receiving side attaches its own joint objects.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import c4d

from ...core import protocol
from ...core.compat import is_fatal, string_types
from ...core.log import get_logger

LOG = get_logger("rig")

SCALE = 65535.0
MAX_JOINTS = 2000
MAX_POINTS = 5000000
REST_KEYS = ("m_bMg", "m_bMi", "m_oMg", "m_oMi")

WEIGHT_TYPES = frozenset(value for value in (getattr(c4d, "Tweights", None),) if value is not None)

# The Constraint tag works its offsets out from its targets when it is first evaluated (applier._settle_constraints)
CONSTRAINT_TYPES = frozenset([1019364])


def is_weight_tag(tag):
    return tag.GetType() in WEIGHT_TYPES and hasattr(tag, "GetJointCount")


def _matrix_values(matrix):
    return [matrix.off.x, matrix.off.y, matrix.off.z, matrix.v1.x, matrix.v1.y, matrix.v1.z,
            matrix.v2.x, matrix.v2.y, matrix.v2.z, matrix.v3.x, matrix.v3.y, matrix.v3.z]


def _matrix_of(values):
    off = c4d.Vector(*values[0:3])
    return c4d.Matrix(off, c4d.Vector(*values[3:6]), c4d.Vector(*values[6:9]), c4d.Vector(*values[9:12]))


def _weight_map(tag, index, point_count):
    """All weights of one joint, one per point. Read point by point: GetWeightMap returns a short,
    wrong list for sparse maps (measured on 2023: [0,0,0,0,1] came back as [0])."""
    return [tag.GetWeight(index, point) for point in range(point_count)]


def same_weights(known, new, tolerance=2, rest=1e-3):
    """Are two Weight tag dumps the same skin? Cinema 4D stores a weight as a 16-bit number and gives back one
    more or less than it was given (measured: 1 of 65535, on a third of the maps after loading), which is not
    a change by anybody."""
    if not isinstance(known, dict) or not isinstance(new, dict) or known.get("j") != new.get("j"):
        return False
    old_rest, new_rest = known.get("r") or {}, new.get("r") or {}
    if set(old_rest) != set(new_rest):
        return False
    for uuid, values in new_rest.items():
        if len(old_rest[uuid]) != len(values) or any(abs(a - b) > rest for a, b in zip(old_rest[uuid], values)):
            return False
    old_gm, new_gm = known.get("gm"), new.get("gm")
    if (old_gm is None) != (new_gm is None) or (
            new_gm is not None and (len(old_gm) != len(new_gm) or any(abs(a - b) > rest for a, b in zip(old_gm, new_gm)))):
        return False
    old_w, new_w = known.get("w") or {}, new.get("w") or {}
    if set(old_w) != set(new_w):
        return False
    for uuid, packed in new_w.items():
        if old_w[uuid] == packed:
            continue
        a, b = protocol.unpack_ints(old_w[uuid]), protocol.unpack_ints(packed)
        if len(a) != len(b) or any(abs(x - y) > tolerance for x, y in zip(a, b)):
            return False
    return True


def dump_weights(tag, uuid_of):
    """The tag's joints, rest states and weights, or None when it has no usable joint."""
    doc = tag.GetDocument()
    owner = tag.GetObject()
    point_count = owner.GetPointCount() if owner is not None and hasattr(owner, "GetPointCount") else 0
    order, rest, weights = [], {}, {}
    count = min(tag.GetJointCount(), MAX_JOINTS)
    for index in range(count):
        joint = tag.GetJoint(index, doc)
        uuid = uuid_of(joint) if joint is not None else None
        if not uuid:
            continue  # a joint that is not shared (code-carrying parent...): its weights stay local
        order.append(uuid)
        state = tag.GetJointRestState(index)
        values = []
        for key in REST_KEYS:
            values.extend(_matrix_values(state[key]))
        values.append(state["m_Len"])
        rest[uuid] = values
        values = _weight_map(tag, index, point_count)
        weights[uuid] = protocol.pack_ints([int(round(max(0.0, min(1.0, w)) * SCALE)) for w in values])
    if not order:
        return None
    data = {"j": order, "r": rest, "w": weights}
    if hasattr(tag, "GetGeomMg"):
        data["gm"] = _matrix_values(tag.GetGeomMg())
    return data


def load_weights(tag, data, atom_of):
    """Attach the joints named in `data` to the tag and set their rest states and weights.
    Joints that do not exist here are left out. Returns the number of joints attached."""
    doc = tag.GetDocument()
    if doc is None or not isinstance(data, dict):
        return 0
    wanted = []
    for uuid in data.get("j") or []:
        joint = atom_of(uuid) if isinstance(uuid, string_types) else None
        if joint is not None and not any(joint == other for _u, other in wanted):
            wanted.append((uuid, joint))
    if not wanted:
        return 0
    current = [tag.GetJoint(index, doc) for index in range(tag.GetJointCount())]
    if len(current) != len(wanted) or any(a != b for a, (_u, b) in zip(current, wanted)):
        for joint in current:
            if joint is not None:
                tag.RemoveJoint(joint)
        for _uuid, joint in wanted:
            tag.AddJoint(joint)
    owner = tag.GetObject()
    point_count = owner.GetPointCount() if owner is not None and hasattr(owner, "GetPointCount") else 0
    for uuid, joint in wanted:
        index = tag.FindJoint(joint)
        if index is None or index < 0:
            continue
        values = (data.get("r") or {}).get(uuid)
        if isinstance(values, list) and len(values) == 49:
            try:
                tag.SetJointRestState(index, *([_matrix_of(values[i * 12:i * 12 + 12]) for i in range(4)] + [values[48]]))
            except BaseException as exc_:
                if is_fatal(exc_):
                    raise
                LOG.exception("Could not set the rest state of a joint")
        packed = (data.get("w") or {}).get(uuid)
        if isinstance(packed, string_types):
            numbers = protocol.unpack_ints(packed)
            if len(numbers) > MAX_POINTS or (point_count and len(numbers) > point_count):
                raise ValueError("weight map longer than the mesh")
            values = [number / SCALE for number in numbers]
            if hasattr(tag, "SetWeightMap"):   # R21 and newer
                tag.SetWeightMap(index, values)
            else:   # R17-R20: only one weight at a time
                for point, value in enumerate(values):
                    tag.SetWeight(index, point, value)
    bind = data.get("gm")
    if isinstance(bind, list) and len(bind) == 12 and hasattr(tag, "SetGeomMg"):
        tag.SetGeomMg(_matrix_of(bind))
    # The Skin keeps what it computed from the old binding until something tells it otherwise.
    _touch(tag)
    if owner is not None:
        _touch(owner)
        child = owner.GetDown()
        while child is not None:
            _touch(child)
            child = child.GetNext()
    return len(wanted)


def _touch(atom):
    """Tell an atom that its data changed, so a Skin computes again from the new binding."""
    if hasattr(atom, "Message") and hasattr(c4d, "MSG_UPDATE"):
        atom.Message(c4d.MSG_UPDATE)
    if hasattr(atom, "SetDirty") and hasattr(c4d, "DIRTYFLAGS_DATA"):
        atom.SetDirty(c4d.DIRTYFLAGS_DATA)
