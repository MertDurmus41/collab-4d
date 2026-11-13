"""XPresso graphs: the logic of a rig travels as data and runs on every computer.

Until now an XPresso tag stayed local (it may hold Python or COFFEE nodes), so a guest got the controllers and
joints of the host but none of the rules that drive them (sliders limiting a control, values written over time,
facial shapes following a control): the guest rig stood in another pose than the host rig. A graph of standard
nodes (Object, Range Mapper, Condition, Compare, Math...) is only data: nodes, their settings and the connections
between their ports. That is sent; a graph with any other node (Python, COFFEE, third party) stays local as before.

Tag data section "xp":
    {"n":  [node, ...]                 nodes in document order (a parent comes before its children)
     "c":  [[from node, out port, to node, in port], ...]    connections, by position in the lists above
     "r":  {key: value}                settings of the graph itself (the container of the root group)
     "dr": [[uuid, parameter key], ...]  parameters the graph writes (connected input ports of Object nodes): both
                                         sides evaluate the graph, so the tracker does not send their values}
    node:  {"i": operator id, "nm": name, "pa": parent index or -1, "x": x, "y": y,
            "bc": {key: value}         settings of the operator; a link is {"L": uuid}
            "in": [port], "out": [port]}
    port:  {"m": main id, "t": value type, "d": parameter key (ports of an Object node that name a parameter),
            "v": 0 when hidden}
Object node ports are named by the description id of a parameter of the linked object; the port only tells its display
name ("Position . Y"), so the id is found by looking that name up in the description of the object.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import c4d

from ...core.compat import is_fatal
from ...core.log import get_logger
from .. import compat
from . import containers, morph, params

LOG = get_logger("xpresso")

TAG_TYPE = 1001149
OBJECT_OPERATOR = 400001000
GROUP_OPERATOR = 1001144
# The standard operators are data: 400001000..400001161 are the ones shipped with Cinema 4D, except the COFFEE node.
# The Python node (1022471) and everything else, third party nodes included, make the graph local.
SAFE_OPERATORS = frozenset((set(range(400001000, 400001162)) - set([400001154])) | set([GROUP_OPERATOR]))

# Keys of position, rotation and scale of an object in the "p" section (measured): what such a port drives
TRANSFORM_KEYS = ("903:23:5155", "904:23:5155", "905:23:5155")

PARAM_PORT_FIRST = 10000000     # ports of an Object node that name a parameter: 1000000n inputs, 2000000n outputs
SPECIAL_PORT_FIRST = 30000000   # its fixed ports (Local, Global, Object...)
MAX_NODES = 2000
TOLERANCE = 1e-6

IN, OUT = 1, 2


def is_xpresso(tag):
    return tag.GetType() == TAG_TYPE and hasattr(tag, "GetNodeMaster")


def _master(tag):
    try:
        return tag.GetNodeMaster()
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        return None


def _walk(node, parent_index, flat):
    """Nodes from `node` on in document order: (node, index of the parent in the same list)."""
    while node is not None and len(flat) < MAX_NODES:
        flat.append((node, parent_index))
        if node.GetDown() is not None:
            _walk(node.GetDown(), len(flat) - 1, flat)
        node = node.GetNext()


def graph_nodes(tag):
    master = _master(tag)
    flat = []
    if master is not None and master.GetRoot() is not None:
        _walk(master.GetRoot().GetDown(), -1, flat)
    return flat


_SAFE = {}   # tag uuid -> is_safe, for the length of one scan (a scan asks about the same tag from several places)


def begin_scan():
    _SAFE.clear()


def is_safe(tag):
    """True when every node of the graph of the tag is a standard one (the graph is data, not code)."""
    if not is_xpresso(tag):
        return False
    uuid = compat.read_uid(tag)
    if uuid is not None and uuid in _SAFE:
        return _SAFE[uuid]
    try:
        safe = all(node.GetOperatorID() in SAFE_OPERATORS for node, _parent in graph_nodes(tag))
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        safe = False
    if uuid is not None:
        _SAFE[uuid] = safe
    return safe


# Ports ---------------------------------------------------------------------------------------------

_NAME_TABLES = {}   # object type -> {port name: [description id]}


def _level_name(description, desc_id):
    try:
        container = description.GetParameter(desc_id)
    except TypeError:   # R19 wants the second argument (an AtomArray, None here); without it no port could be named
        container = description.GetParameter(desc_id, None)
    if container is None:
        return None
    try:
        return compat.from_c4d_str(container[c4d.DESC_NAME])
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        return None


def _cache_key(atom):
    """Objects of one type share a description apart from their user data; tags (Pose Morph strengths, Constraint
    targets) build theirs from their content: no sharing there."""
    if not isinstance(atom, c4d.BaseObject):
        return None
    try:
        user = tuple(compat.from_c4d_str(container[c4d.DESC_NAME] or "") for _id, container in atom.GetUserDataContainer())
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        return None
    return (atom.GetType(), user)


def _name_table(atom):
    """{display name of an Object node port: [DescID]} for the parameters of `atom`."""
    cache_key = _cache_key(atom)
    table = _NAME_TABLES.get(cache_key) if cache_key is not None else None
    if table is not None:
        return table
    table = {}
    description = atom.GetDescription(compat.DESCFLAGS_DESC)
    vector_types = compat.type_ids("DTYPE_VECTOR", "DTYPE_COLOR")
    for container, desc_id, _group in description:
        depth = desc_id.GetDepth()
        if depth < 1 or depth > 2:
            continue
        name = compat.from_c4d_str(container[c4d.DESC_NAME]) if container.GetType(c4d.DESC_NAME) else None
        if not name:
            continue
        table.setdefault(name, []).append(desc_id)   # user data and pose morph strengths: the bare name
        if depth == 1 and desc_id[0].dtype in vector_types:
            for component in (c4d.VECTOR_X, c4d.VECTOR_Y, c4d.VECTOR_Z):
                sub = c4d.DescID(desc_id[0], c4d.DescLevel(component, c4d.DTYPE_REAL, 0))
                sub_name = _level_name(description, sub)   # already complete: "Position . X"
                if sub_name:
                    table.setdefault(sub_name, []).append(sub)
    if cache_key is not None:
        if len(_NAME_TABLES) > 256:
            _NAME_TABLES.clear()
        _NAME_TABLES[cache_key] = table
    return table


# Data type of a port -> the parameter types it can belong to. Names are not unique ("Scale" is the scale of the object
# and a user data slider of a rig), the type tells them apart.
PORT_PARAMETER_TYPES = {
    400007003: compat.type_ids("DTYPE_REAL", "DTYPE_PERCENT", "DTYPE_METER", "DTYPE_DEGREE", "DTYPE_TIME"),   # real
    400007002: compat.type_ids("DTYPE_LONG"),                                                                  # integer
    400007001: compat.type_ids("DTYPE_BOOL"),
    400007004: compat.type_ids("DTYPE_VECTOR", "DTYPE_COLOR"),
    400007008: compat.type_ids("DTYPE_STRING"),
    400007006: compat.type_ids("DTYPE_MATRIX"),
    400007007: compat.type_ids("DTYPE_TIME"),
}


def _pick(candidates, value_type):
    """The parameter of `candidates` (same display name) that a port of `value_type` stands for."""
    wanted = PORT_PARAMETER_TYPES.get(value_type)
    if wanted:
        for desc_id in candidates:
            if desc_id[desc_id.GetDepth() - 1].dtype in wanted:
                return desc_id
    for desc_id in candidates:   # a link or a file name: the type of the port is the type of the parameter
        if desc_id[desc_id.GetDepth() - 1].dtype == value_type:
            return desc_id
    return candidates[0]


CONSTRAINT_TAG = 1019364
CONSTRAINT_MODES = 8        # Transform, Aim, Parent, Up, Clamp, Spring, Mirror, Collision (as in the tag's switches)
CONSTRAINT_SWITCH = 1000    # the switch of mode n is parameter 1000 + n
CONSTRAINT_STRENGTH_GROUP = 6000   # the group of mode n is 6000 + n, the group of its targets 7000 + n
CONSTRAINT_TARGET_GROUP = 7000
CONSTRAINT_DYNAMIC = 10000  # parameters of the targets (target: 10001, weight: 10002, ...) are made per target


def _constraint_parameters(tag):
    """(strength, weights) of the Constraint tag: the Strength parameter of its first active mode and the weight
    parameters of the targets of its active modes, as the description gives them.

    The description holds a "Strength" and a "Weight" for every mode (2203 and 8502 for Parent, 2116 for Transform...)
    that stand for nothing in a tag that does not use that mode; a graph that drives a Transform constraint writes the
    Strength 2116 and the dynamic per-target weights 10002, 10012... (measured: a rig's head world/local switch and its
    leg blends). Looking them up by name picks the first of those, which is not what the host's graph writes."""
    active = []
    for mode in range(CONSTRAINT_MODES):
        try:
            if tag[CONSTRAINT_SWITCH + mode]:
                active.append(mode)
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
    strengths, weights, target_mode = {}, {}, {}
    for container, desc_id, group in tag.GetDescription(compat.DESCFLAGS_DESC):
        if desc_id.GetDepth() != 1 or group.GetDepth() < 1 or not container.GetType(c4d.DESC_NAME):
            continue
        ident, name, parent = desc_id[0].id, compat.from_c4d_str(container[c4d.DESC_NAME]), group[group.GetDepth() - 1].id
        if name == "Strength" and CONSTRAINT_STRENGTH_GROUP <= parent < CONSTRAINT_STRENGTH_GROUP + CONSTRAINT_MODES:
            strengths[parent - CONSTRAINT_STRENGTH_GROUP] = desc_id
        elif ident >= CONSTRAINT_DYNAMIC and name == "Weight":
            weights[ident] = desc_id
        elif ident >= CONSTRAINT_DYNAMIC and name == "Target" and CONSTRAINT_TARGET_GROUP <= parent \
                < CONSTRAINT_TARGET_GROUP + CONSTRAINT_MODES:
            target_mode[ident] = parent - CONSTRAINT_TARGET_GROUP
    strength = next((strengths[mode] for mode in active if mode in strengths), None)
    wanted = [weights[ident] for ident in sorted(weights) if target_mode.get(ident - 1) in active]
    return strength, wanted


def _constraint_ports(node, ports, specs, tag):
    """The Strength and the per-target weight ports of a Constraint tag. XPresso names a weight port after its target
    object ("world", "local"), a name the description does not know, or "Weight". Weight ports are taken in the order
    of their numbers, the first to be used is the first target."""
    strength, weights = _constraint_parameters(tag)
    named = [index for index, port in enumerate(ports) if _is_param_port(port)]
    for index in named:
        if strength is not None and compat.from_c4d_str(ports[index].GetName(node)) == "Strength":
            specs[index]["d"] = params.key_of(strength)
    rest = [index for index in named if "d" not in specs[index] or compat.from_c4d_str(ports[index].GetName(node)) == "Weight"]
    rest = [index for index in rest if compat.from_c4d_str(ports[index].GetName(node)) != "Strength"]
    for rank, index in enumerate(sorted(rest, key=lambda i: ports[i].GetMainID())):
        if rank < len(weights):
            specs[index]["d"] = params.key_of(weights[rank])
        else:
            specs[index].pop("d", None)


def _same_names(node, ports, specs, atom):
    """Several parameters can have the same name (a rig that has a left and a right hand keeps "Tool", "Texture" and
    "Size" twice in its user data). Ports that show the same name are taken in the order of their numbers, the first
    to be used is the first parameter of that name; a port that is both input and output of one parameter has one
    number."""
    table = _name_table(atom)
    by_name = {}
    for index, port in enumerate(ports):
        if _is_param_port(port):
            by_name.setdefault(compat.from_c4d_str(port.GetName(node)), []).append(index)
    for name, indexes in by_name.items():
        candidates = [c for c in table.get(name) or [] if _fits(c, specs[indexes[0]].get("t"))]
        slots = sorted(set(ports[i].GetMainID() % PARAM_PORT_FIRST for i in indexes))
        if len(candidates) < 2 or len(slots) < 2:
            continue
        for index in indexes:
            rank = slots.index(ports[index].GetMainID() % PARAM_PORT_FIRST)
            if rank < len(candidates):
                specs[index]["d"] = params.key_of(candidates[rank])


def _fits(desc_id, value_type):
    wanted = PORT_PARAMETER_TYPES.get(value_type)
    return not wanted or desc_id[desc_id.GetDepth() - 1].dtype in wanted


def _is_param_port(port):
    return PARAM_PORT_FIRST <= port.GetMainID() < SPECIAL_PORT_FIRST


def _port_spec(port, node, atom):
    spec = {"m": port.GetMainID(), "t": port.GetValueType()}
    if atom is not None and _is_param_port(port):
        candidates = _name_table(atom).get(compat.from_c4d_str(port.GetName(node))) or []
        if candidates:
            spec["d"] = params.key_of(_pick(candidates, spec["t"]))
    try:
        if not port.GetVisible():
            spec["v"] = 0
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
    return spec


def _linked_atom(node, doc):
    try:
        return node.GetOperatorContainer().GetLink(c4d.GV_OBJECT_OBJECT_ID, doc)
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        return None


def _position(node):
    try:
        place = node.GetData().GetContainer(1001).GetContainer(1000)
        read = getattr(place, "GetFloat", None) or place.GetReal   # GetReal: before R18
        return read(100), read(101)
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        return 0.0, 0.0


def _set_position(node, x, y):
    try:
        data = node.GetData()
        outer = data.GetContainer(1001)
        place = outer.GetContainer(1000)
        write = getattr(place, "SetFloat", None) or place.SetReal
        write(100, x)
        write(101, y)
        outer.SetContainer(1000, place)
        data.SetContainer(1001, outer)
        node.SetData(data)
    except BaseException as exc_:
        if is_fatal(exc_):
            raise


def _path(node):
    """Where a node sits in the graph: the number of siblings before it, at every level up to the root. Python
    wrappers of one node are not equal to each other, so nodes are compared by position."""
    path = []
    while node is not None:
        before = 0
        sibling = node.GetPred()
        while sibling is not None:
            before += 1
            sibling = sibling.GetPred()
        path.append(before)
        node = node.GetUp()
    return tuple(path)


def dump(tag, uuid_of):
    """The graph of the tag as data, or None when it has none (or holds a node that is not a standard one)."""
    master = _master(tag)
    if master is None or not is_safe(tag):
        return None
    doc = tag.GetDocument()
    flat = graph_nodes(tag)
    listed = [node for node, _parent in flat]
    result_nodes, driven, links = [], [], []
    for node, parent in flat:
        atom = _linked_atom(node, doc) if node.GetOperatorID() == OBJECT_OPERATOR else None
        ins, outs = node.GetInPorts(), node.GetOutPorts()
        spec = {"i": node.GetOperatorID(), "nm": compat.from_c4d_str(node.GetName()), "pa": parent,
                "bc": containers.encode_container(node.GetOperatorContainer(), uuid_of),
                "in": [_port_spec(p, node, atom) for p in ins], "out": [_port_spec(p, node, atom) for p in outs]}
        if atom is not None and atom.GetType() == CONSTRAINT_TAG:
            _constraint_ports(node, ins, spec["in"], atom)
        elif atom is not None:
            _same_names(node, ins + outs, spec["in"] + spec["out"], atom)
        spec["x"], spec["y"] = _position(node)
        result_nodes.append(spec)
        target = uuid_of(atom) if atom is not None else None
        if target:
            for port, port_spec in zip(ins, spec["in"]):
                if "d" in port_spec and port.IsIncomingConnected():
                    driven.append([target, port_spec["d"]])
                elif port.GetMainID() == GLOBAL_IN and port.IsIncomingConnected():
                    driven.extend([target, key] for key in TRANSFORM_KEYS)   # the head and hand attachments of a rig
    place = dict((_path(node), index) for index, node in enumerate(listed))
    for index, node in enumerate(listed):
        for out_index, port in enumerate(node.GetOutPorts()):
            if not port.GetNrOfConnections():
                continue
            for dest in port.GetDestination() or []:
                other = dest.GetNode()
                target_index = place.get(_path(other), -1)
                sub_ids = [p.GetSubID() for p in other.GetInPorts()]
                port_index = sub_ids.index(dest.GetSubID()) if dest.GetSubID() in sub_ids else -1
                if target_index >= 0 and port_index >= 0:
                    links.append([index, out_index, target_index, port_index])
    return {"n": result_nodes, "c": links, "dr": sorted(driven),
            "r": containers.encode_container(master.GetRoot().GetOperatorContainer(), uuid_of)}


def driven_of(data):
    """{uuid: {parameter key as in the "p" section of that object: set of vector components, or None for all of it}}
    that the graph in `data` writes."""
    result = {}
    vectors = compat.type_ids("DTYPE_VECTOR", "DTYPE_COLOR")
    for uuid, key in (data or {}).get("dr") or []:
        levels = key.split("/")
        try:
            first = [int(part) for part in levels[0].split(":")]
            second = int(levels[1].split(":")[0]) if len(levels) == 2 else None
        except ValueError:
            continue
        slot = result.setdefault(uuid, {})
        if second is not None and first[1] in vectors and 0 <= second - c4d.VECTOR_X <= 2:
            if slot.get(levels[0], set()) is not None:
                slot.setdefault(levels[0], set()).add(second - c4d.VECTOR_X)
        else:
            slot[key] = None
    return result


# Comparing and building ------------------------------------------------------------------------------

FIRST_PORT_KEY = 10000000   # an Object node keeps the value of each parameter port in its settings from this id on


def _without_empty_port_values(data):
    """`data` without the empty settings an Object node keeps for ports that were deleted ({"L": None} at 10000000 and
    up, measured on the Mert graph of Fight_test11: the host's node 78 had two, the guest's none, and the graph was
    taken for a different one although every node and connection matched)."""
    if not isinstance(data, dict) or not isinstance(data.get("n"), list):
        return data
    cleaned = []
    for spec in data["n"]:
        bc = spec.get("bc") if isinstance(spec, dict) else None
        if isinstance(bc, dict):
            kept = dict((key, value) for key, value in bc.items()
                        if not (value == {"L": None} and str(key).isdigit() and int(key) >= FIRST_PORT_KEY))
            if len(kept) != len(bc):
                spec = dict(spec, bc=kept)
        cleaned.append(spec)
    return dict(data, n=cleaned)


def same(known, new):
    """Are two graph dumps the same graph (floats within a hair)?"""
    return containers.same(_without_empty_port_values(known), _without_empty_port_values(new), TOLERANCE)


def _clear(master):
    node = master.GetRoot().GetDown()
    while node is not None:
        following = node.GetNext()
        node.Remove()
        node = following


GLOBAL_IN, GLOBAL_OUT = 30000001, 40000001   # the Global Matrix ports of an Object node
_PORT_REPORTED = set()   # (operator, step) already logged this session
_DROPPED_LOGGED = [0]    # graphs with dropped connections logged in detail this session
MAX_DETAILED_DROPS = 3

# R19 cannot make the Global Matrix ports of an Object node from Python (measured: AddPort answers None for every id, flag
# and DescID, while parameter ports work), so a graph that copies the global matrix of one object onto another - the head
# of a rig following its control - cannot be built there. What the graph does is done here: {(tag, node, node): (from, to)}.
_MIRRORS = {}
DEGRADED = set()   # uuids of XPresso tags whose graph could not be built as the sender's (the tracker never reports those back)


# The same copy as a Python tag of ours on the target (a code tag: it never leaves this computer, see nodes.tag_supported):
# it runs inside the expression pass, so the head moves with its control on the same frame, also while the timeline plays.
COPY_TAG_NAME = "Collab rig copy"
COPY_CODE = ("import c4d\n\n\ndef main():\n    source = op[c4d.ID_USERDATA, 1]\n    target = op.GetObject()\n"
             "    if source is not None and target is not None:\n        target.SetMg(source.GetMg())\n")
_COPY_TAGS = {}   # mirror key -> the tag


def _copy_tag(source, target):
    """The Python tag on `target` that sets its global matrix to the one of `source` (made once, then only re-linked)."""
    tag = None
    candidate = target.GetFirstTag()
    while candidate is not None:
        if candidate.GetType() == c4d.Tpython and compat.from_c4d_str(candidate.GetName()) == COPY_TAG_NAME:
            tag = candidate
            break
        candidate = candidate.GetNext()
    if tag is None:
        tag = target.MakeTag(c4d.Tpython)
        tag.SetName(compat.native_str(COPY_TAG_NAME))
        tag[c4d.TPYTHON_CODE] = compat.native_str(COPY_CODE)
        data = c4d.GetCustomDataTypeDefault(c4d.DTYPE_BASELISTLINK)
        data[c4d.DESC_NAME] = compat.native_str("source")
        tag.AddUserData(data)
        try:   # after the rig's own expressions (Constraint tags, IK) of this frame
            priority = tag[c4d.EXPRESSION_PRIORITY]
            priority.SetPriorityValue(c4d.PRIORITYVALUE_MODE, c4d.CYCLE_EXPRESSION)
            priority.SetPriorityValue(c4d.PRIORITYVALUE_PRIORITY, 450)
            tag[c4d.EXPRESSION_PRIORITY] = priority
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
    tag[c4d.ID_USERDATA, 1] = source
    return tag


def _drop_copy_tag(key):
    tag = _COPY_TAGS.pop(key, None)
    if tag is not None:
        try:
            tag.Remove()
        except BaseException as exc_:
            if is_fatal(exc_):
                raise

def forget(uuid):
    """The tag `uuid` is gone: its rig copies and its marks go with it."""
    for key in [k for k in _MIRRORS if k[0] == uuid]:
        _MIRRORS.pop(key, None)
        _drop_copy_tag(key)
    DEGRADED.discard(uuid)


def clear_session():
    """A session ended or a new one starts: nothing of the old one stays (the copy tags are removed from their documents)."""
    for key in list(_COPY_TAGS):
        _drop_copy_tag(key)
    _MIRRORS.clear()
    DEGRADED.clear()
    _PORT_REPORTED.clear()
    _DROPPED_LOGGED[0] = 0


def _same_matrix(a, b, tolerance=1e-4):
    return all((x - y).GetLengthSquared() <= tolerance * tolerance for x, y in ((a.off, b.off), (a.v1, b.v1), (a.v2, b.v2), (a.v3, b.v3)))


def apply_mirrors():
    """Put every object of a graph that could not be built on the global matrix of the object it copies. Returns True
    when something moved (the caller evaluates the scene)."""
    moved_any = False
    for _pass in range(3):   # a copy of a copy settles in a few passes
        moved = False
        for key, (source, target) in list(_MIRRORS.items()):
            try:
                matrix = source.GetMg()
                if not _same_matrix(target.GetMg(), matrix):
                    target.SetMg(matrix)
                    moved = True
            except BaseException as exc_:
                if is_fatal(exc_):
                    raise
                _MIRRORS.pop(key, None)   # one of the objects is gone
        moved_any = moved_any or moved
        if not moved:
            break
    return moved_any


def _relink(node):
    """Set the linked object of an Object node again through its parameter (not its container), which makes the node
    read the object's description; returns the linked atom."""
    doc = node.GetDocument() if hasattr(node, "GetDocument") else None
    atom = node.GetOperatorContainer().GetLink(c4d.GV_OBJECT_OBJECT_ID, doc)
    if atom is not None:   # no link found (the object is not in the document yet): leave the node as it is
        node[c4d.GV_OBJECT_OBJECT_ID] = atom
    return atom


def _add_port(node, io, ident):
    """A new visible port, or None. On R19 every Object node port of a graph built here stayed missing (AddPort answered
    None, connections to them were dropped and the head of a rig no longer followed its control), so the port is asked
    again with the message that finishes it, then after the linked object is set through the node's parameter."""
    attempts = [lambda: node.AddPort(io, ident, c4d.GV_PORT_FLAG_IS_VISIBLE, False),
                lambda: node.AddPort(io, ident, c4d.GV_PORT_FLAG_IS_VISIBLE, True)]
    if node.GetOperatorID() == OBJECT_OPERATOR:
        attempts.append(lambda: (_relink(node), node.AddPort(io, ident, c4d.GV_PORT_FLAG_IS_VISIBLE, True))[1])
    if isinstance(ident, c4d.DescID):   # older releases match a parameter port without the creator of its levels
        levels = [ident[i] for i in range(ident.GetDepth())]
        attempts.append(lambda: node.AddPort(io, c4d.DescID(*[c4d.DescLevel(lv.id, lv.dtype, 0) for lv in levels]),
                                             c4d.GV_PORT_FLAG_IS_VISIBLE, True))
        attempts.append(lambda: node.AddPort(io, c4d.DescID(*[c4d.DescLevel(lv.id) for lv in levels]),
                                             c4d.GV_PORT_FLAG_IS_VISIBLE, True))
    for step, attempt in enumerate(attempts):
        try:
            port = attempt()
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            port = None
            if (node.GetOperatorID(), "x%d" % step) not in _PORT_REPORTED:
                _PORT_REPORTED.add((node.GetOperatorID(), "x%d" % step))
                LOG.warning("XPresso AddPort attempt %d raised %r", step, exc_)
        if port is not None:
            if step and (node.GetOperatorID(), step) not in _PORT_REPORTED:
                _PORT_REPORTED.add((node.GetOperatorID(), step))
                LOG.info("XPresso ports of node %s are made by attempt %d", node.GetOperatorID(), step)
            return port
    if isinstance(ident, int):   # R19 refuses a port that is there already (made from the node's settings): use it
        port = next((p for p in (node.GetInPorts() if io == IN else node.GetOutPorts()) if p.GetMainID() == ident), None)
        if port is not None:
            if (node.GetOperatorID(), "existing") not in _PORT_REPORTED:
                _PORT_REPORTED.add((node.GetOperatorID(), "existing"))
                LOG.info("XPresso node %s already had port %s: using it", node.GetOperatorID(), ident)
            return port
    kind = (node.GetOperatorID(), "none", isinstance(ident, int))
    if kind not in _PORT_REPORTED:
        _PORT_REPORTED.add(kind)
        try:
            ok = node.AddPortIsOK(io, ident)
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            ok = repr(exc_)
        try:   # a diagnostic must never stop the ports of the node
            doc = node.GetDocument() if hasattr(node, "GetDocument") else None
            LOG.warning("XPresso node %s refuses port %s (io %s): AddPortIsOK %s, linked %r, settings %s, ports in %s out %s",
                        node.GetOperatorID(), ident, io, ok, node.GetOperatorContainer().GetLink(c4d.GV_OBJECT_OBJECT_ID, doc),
                        sorted(key for key, _value in node.GetOperatorContainer())[:12],
                        [p.GetMainID() for p in node.GetInPorts()], [p.GetMainID() for p in node.GetOutPorts()])
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            LOG.warning("XPresso node %s refuses port %s (io %s): AddPortIsOK %s", node.GetOperatorID(), ident, io, ok)
    return None


def _fit_plain_ports(node, specs, io, made):
    """Give a node that is not an Object node the ports `specs` describe; made[i] is the port of spec i."""
    ports = node.GetInPorts() if io == IN else node.GetOutPorts()
    for index, spec in enumerate(specs):
        if index < len(ports) and ports[index].GetMainID() == spec["m"]:
            port = ports[index]
        else:
            port = _add_port(node, io, spec["m"])
        port = _finish_port(node, port, spec, io)
        made.append(port)
    for port in ports[len(specs):]:
        node.RemovePort(port)


def _finish_port(node, port, spec, io=None):
    """Give `port` the type and visibility of `spec`. Returns the port (a new one when it had to be made again)."""
    if port is not None and port.GetValueType() != spec["t"]:
        try:
            node.SetPortType(port, spec["t"])
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            LOG.warning("Cannot set the type of an XPresso port")
        if io is not None and port.GetValueType() != spec["t"]:
            # A Switch node refuses SetPortType (measured on 2023: False, the type stays). Its output exists from the
            # start as a real number and kept that type after the inputs became text, so the 10 links from the "tools"
            # graph's Switch nodes into the File ports of Object nodes were refused on the guest. A port made again
            # takes the node's current type (measured: text).
            removed = False
            try:
                node.RemovePort(port)
                removed = True
                port = _add_port(node, io, spec["m"])   # None when it cannot be made: its links are then dropped
            except BaseException as exc_:
                if is_fatal(exc_):
                    raise
                LOG.warning("Cannot make an XPresso port again")
                if removed:
                    port = None
    if port is not None and spec.get("v") == 0:
        port.SetVisible(False)
    return port


def _spare_parameters(atom, taken):
    """Description ids of `atom` that no wanted port uses (to hold the place of a port that was deleted)."""
    spare = []
    for candidates in _name_table(atom).values():
        key = params.key_of(candidates[0])
        if key not in taken and candidates[0].GetDepth() == 1:
            spare.append(candidates[0])
    return spare


def _slider(atom, desc_id):
    """The parameter of `atom` for a port the sender named by `desc_id`: the sliders of a Pose Morph tag are named by the
    sender's morph numbers, which this Cinema 4D may have numbered differently (morph.NUMBER_MAP)."""
    uuid = compat.read_uid(atom) if atom is not None and not isinstance(atom, c4d.BaseObject) else None
    return morph.local_slider(uuid, desc_id) if uuid else desc_id


def _fit_object_ports(node, ins, outs, atom, made_in, made_out):
    """The ports of an Object node are numbered by the order in which parameters were first used on the node
    (1000000n inputs, 2000000n outputs, one counter for both), and a deleted port leaves its number unused. The
    settings of the node are kept under these numbers, so the same numbers are made here: a number that no port
    uses is created on a spare parameter and removed again."""
    made_in.extend([None] * len(ins))
    made_out.extend([None] * len(outs))
    slots = {}
    fixed = []
    for io, specs, made in ((IN, ins, made_in), (OUT, outs, made_out)):
        for position, spec in enumerate(specs):
            if PARAM_PORT_FIRST <= spec["m"] < SPECIAL_PORT_FIRST:
                slots.setdefault(spec["m"] % PARAM_PORT_FIRST, []).append((io, position, spec, made))
            else:
                fixed.append((io, position, spec, made))
    taken = set(spec["d"] for entries in slots.values() for _io, _p, spec, _m in entries if "d" in spec)
    spare = _spare_parameters(atom, taken) if atom is not None and slots else []
    allocation = []
    for slot in range(max(slots) + 1 if slots else 0):   # 1st pass: number the parameters in their order of first use
        entries = [entry for entry in slots.get(slot, []) if "d" in entry[2]]
        if entries and atom is not None:
            allocation.append(_add_port(node, IN, _slider(atom, params.desc_id_of(entries[0][2]["d"]))))
        elif spare:
            allocation.append(_add_port(node, IN, spare.pop(0)))
    for port in allocation:
        if port is not None:
            node.RemovePort(port)   # the numbers stay taken
    for io, specs, made in ((IN, ins, made_in), (OUT, outs, made_out)):   # 2nd pass: the real ports, in their order
        for position, spec in enumerate(specs):
            if "d" in spec and atom is not None and PARAM_PORT_FIRST <= spec["m"] < SPECIAL_PORT_FIRST:
                port = _add_port(node, io, _slider(atom, params.desc_id_of(spec["d"])))
                _finish_port(node, port, spec)
                made[position] = port
    for io, position, spec, made in fixed:
        port = _add_port(node, io, spec["m"])
        _finish_port(node, port, spec)
        made[position] = port


def load(tag, data, atom_of, uuid_of):
    """Build the graph in `data` in the tag, unless it is that graph already. Returns True when it was rebuilt."""
    master = _master(tag)
    if master is None or not isinstance(data, dict):
        return False
    _SAFE.pop(compat.read_uid(tag), None)   # the graph is about to change
    # SECURITY: network data never makes a node that runs code (Python, COFFEE, anything not standard)
    if any(spec.get("i") not in SAFE_OPERATORS for spec in data.get("n") or []):
        LOG.warning("Refusing an XPresso graph with a node that is not a standard one")
        return False
    if is_safe(tag) and same(data, dump(tag, uuid_of)):
        return False
    doc = tag.GetDocument()
    _clear(master)
    root = master.GetRoot()
    root.SetOperatorContainer(containers.decode_container(data.get("r"), atom_of))
    built, last_child, ports = [], {}, []
    for spec in data.get("n") or []:
        parent_index = spec.get("pa", -1)
        parent = built[parent_index] if 0 <= parent_index < len(built) and built[parent_index] is not None else root
        node = master.CreateNode(parent, spec["i"], last_child.get(parent_index), spec.get("x", 0.0), spec.get("y", 0.0))
        if node is None:
            LOG.warning("Cannot create XPresso node %s", spec["i"])
            built.append(None)
            ports.append(([], []))
            continue
        last_child[parent_index] = node
        built.append(node)
        _set_position(node, spec.get("x", 0.0), spec.get("y", 0.0))
        if spec.get("nm"):
            node.SetName(compat.native_str(spec["nm"]))
        settings = containers.decode_container(spec.get("bc"), atom_of)
        node.SetOperatorContainer(settings)   # the linked object first: the ports are made on its parameters
        atom = _linked_atom(node, doc) if spec["i"] == OBJECT_OPERATOR else None
        ins, outs = [], []
        try:
            if spec["i"] == OBJECT_OPERATOR:
                _fit_object_ports(node, spec.get("in") or [], spec.get("out") or [], atom, ins, outs)
            else:
                _fit_plain_ports(node, spec.get("in") or [], IN, ins)
                _fit_plain_ports(node, spec.get("out") or [], OUT, outs)
            node.SetOperatorContainer(settings)   # again: making ports writes default values into it
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            LOG.exception("Cannot build the ports of XPresso node %s", spec["i"])
        ports.append((ins, outs))
    # The data type of a Condition or Math node comes from what is connected to it (SetPortType cannot set it), so a
    # connection to a port that has not taken its type yet is refused: retry what failed while others succeed.
    pending = list(data.get("c") or [])
    dropped = []   # connections that could not be made at all: the graph is not the host's
    for _pass in range(8):
        failed = []
        for link in pending:
            source, out_index, target, in_index = link
            try:
                out_port = ports[source][1][out_index]
                in_port = ports[target][0][in_index]
                if out_port is None or in_port is None:
                    dropped.append(link)
                    continue
                if not out_port.Connect(in_port):
                    failed.append(link)
            except (IndexError, TypeError):
                dropped.append(link)
                continue
            except BaseException as exc_:
                if is_fatal(exc_):
                    raise
                LOG.warning("Cannot connect two XPresso ports")
        if not failed or len(failed) == len(pending):
            dropped.extend(failed)
            break
        pending = failed
    for key in [k for k in _MIRRORS if k[0] == uuid_of(tag)]:
        del _MIRRORS[key]
        _drop_copy_tag(key)
    try:   # not the sender's graph (a connection or port that could not be made, a Pose Morph slider translated)
        degraded = bool(dropped) or not same(data, dump(tag, uuid_of))
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        degraded = True
    if degraded:
        DEGRADED.add(uuid_of(tag))
    else:
        DEGRADED.discard(uuid_of(tag))
    for source, out_index, target, in_index in dropped:
        try:
            out_spec, in_spec = data["n"][source]["out"][out_index], data["n"][target]["in"][in_index]
            if out_spec.get("m") == GLOBAL_OUT and in_spec.get("m") == GLOBAL_IN and built[source] is not None and built[target] is not None:
                from_atom, to_atom = _linked_atom(built[source], doc), _linked_atom(built[target], doc)
                if from_atom is not None and to_atom is not None:
                    key = (uuid_of(tag), source, target)
                    _MIRRORS[key] = (from_atom, to_atom)
                    try:
                        _COPY_TAGS[key] = _copy_tag(from_atom, to_atom)
                    except BaseException as exc_:
                        if is_fatal(exc_):
                            raise
                        LOG.warning("Cannot make the rig copy tag: %r (the copy runs from the pump instead)", exc_)
        except (KeyError, IndexError, TypeError):
            continue
    if dropped:
        owner = compat.from_c4d_str(tag.GetObject().GetName()) if tag.GetObject() else "?"
        _DROPPED_LOGGED[0] += 1
        if _DROPPED_LOGGED[0] > MAX_DETAILED_DROPS:   # the details of a few graphs tell the cause; the rest is one line each
            LOG.info("XPresso graph of %s: %d of %d connections were not made", owner, len(dropped), len(data.get("c") or []))
            return True
        LOG.warning("XPresso graph of %s: %d of %d connections were not made (first: %s)", owner, len(dropped), len(data.get("c") or []), dropped[:3])
        for source, out_index, target, in_index in dropped[:3]:
            try:
                out_port, in_port = ports[source][1][out_index], ports[target][0][in_index]
                LOG.warning("  link %s.out%s -> %s.in%s: out %s, in %s (wanted %s -> %s)", data["n"][source]["i"], out_index, data["n"][target]["i"], in_index,
                            "None" if out_port is None else "%s/type %s" % (out_port.GetMainID(), out_port.GetValueType()),
                            "None" if in_port is None else "%s/type %s" % (in_port.GetMainID(), in_port.GetValueType()),
                            data["n"][source]["out"][out_index].get("m"), data["n"][target]["in"][in_index].get("m"))
            except BaseException as exc_:
                if is_fatal(exc_):
                    raise
                LOG.warning("  link %s: cannot describe (%r)", (source, out_index, target, in_index), exc_)
    return True
