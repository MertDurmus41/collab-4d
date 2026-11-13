"""Shared scene model: a tree of nodes identified by uuid, changed only through ops.

The server keeps the authoritative SceneState; every client keeps a mirror that
it updates with the same ops in the same (server-assigned) order.

Op shapes (see ARCHITECTURE.md section 3):
    {"op": "create", "uuid", "kind", "ctype", "parent", "prev", "name", "data"}
    {"op": "update", "uuid", "patch"?, "name"?}
    {"op": "move",   "uuid", "parent", "prev"}
    {"op": "delete", "uuid"}
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import copy

from . import diff as diffmod
from .compat import integer_types, string_types

KINDS = frozenset(["object", "tag", "track", "doc", "material", "shader", "asset"])
OP_TYPES = frozenset(["create", "update", "move", "delete"])
MAX_UUID_LENGTH = 64
MAX_NAME_LENGTH = 1024


class OpError(Exception):
    """An op cannot be applied (invalid shape or inconsistent with the current scene)."""


class Node(object):
    __slots__ = ("uuid", "kind", "ctype", "parent", "prev", "name", "data")

    def __init__(self, uuid, kind, ctype, parent=None, prev=None, name="", data=None):
        self.uuid = uuid
        self.kind = kind
        self.ctype = ctype
        self.parent = parent
        self.prev = prev
        self.name = name
        self.data = data if data is not None else {}

    def to_create_op(self):
        return {
            "op": "create", "uuid": self.uuid, "kind": self.kind, "ctype": self.ctype,
            "parent": self.parent, "prev": self.prev, "name": self.name,
            "data": copy.deepcopy(self.data),
        }


def _is_uuid(value):
    return isinstance(value, string_types) and 0 < len(value) <= MAX_UUID_LENGTH


def _is_optional_uuid(value):
    return value is None or _is_uuid(value)


def validate_op(op):
    """Shape check for an op coming from the network. Raises OpError."""
    if not isinstance(op, dict):
        raise OpError("op must be an object")
    kind = op.get("op")
    if kind not in OP_TYPES:
        raise OpError("unknown op %r" % (kind,))
    if not _is_uuid(op.get("uuid")):
        raise OpError("invalid uuid")
    if "name" in op and not (isinstance(op["name"], string_types) and len(op["name"]) <= MAX_NAME_LENGTH):
        raise OpError("invalid name")
    if kind == "create":
        if op.get("kind") not in KINDS:
            raise OpError("invalid kind")
        ctype = op.get("ctype")
        if not isinstance(ctype, integer_types + string_types) or isinstance(ctype, bool):
            raise OpError("invalid ctype")
        if not _is_optional_uuid(op.get("parent")) or not _is_optional_uuid(op.get("prev")):
            raise OpError("invalid parent/prev")
        if not isinstance(op.get("data", {}), dict):
            raise OpError("invalid data")
    elif kind == "update":
        if "patch" in op and not diffmod.is_valid_patch(op["patch"]):
            raise OpError("invalid patch")
    elif kind == "move":
        if not _is_optional_uuid(op.get("parent")) or not _is_optional_uuid(op.get("prev")):
            raise OpError("invalid parent/prev")


# assets (texture files) and materials come first: texture tags link to materials, materials need their
# files, so a snapshot must create them before the objects
KIND_ORDER = ("asset", "material", "shader", "object", "tag", "track", "doc")


class SceneState(object):
    """Nodes plus indexes. Siblings form one chain per (parent, kind): in Cinema 4D an
    object keeps its child objects and its tags in two separate lists.
    All mutations are O(1) apart from deleting a subtree (O(subtree))."""

    def __init__(self):
        self.seq = 0
        self._reset()

    def _reset(self):
        self.nodes = {}
        self._kids = {}   # parent uuid (None = root) -> set of child uuids (all kinds)
        self._next = {}   # uuid -> next sibling uuid
        self._first = {}  # (parent, kind) -> first sibling uuid

    # Queries -----------------------------------------------------------------

    def get(self, uuid):
        return self.nodes.get(uuid)

    def __contains__(self, uuid):
        return uuid in self.nodes

    def __len__(self):
        return len(self.nodes)

    def children(self, uuid, kind=None):
        """Direct children of `uuid` (None = root) in sibling order, grouped by kind."""
        ordered = []
        for each in ((kind,) if kind else KIND_ORDER):
            current = self._first.get((uuid, each))
            guard = 0
            while current is not None and guard <= len(self.nodes):
                ordered.append(self.nodes[current])
                current = self._next.get(current)
                guard += 1
        return ordered

    def next_sibling(self, uuid):
        return self._next.get(uuid)

    def descendants(self, uuid):
        result = []
        stack = [uuid]
        while stack:
            for child in self._kids.get(stack.pop(), ()):
                result.append(child)
                stack.append(child)
        return result

    def ancestors(self, uuid):
        result = []
        node = self.nodes.get(uuid)
        guard = 0
        while node is not None and node.parent is not None and guard <= len(self.nodes):
            result.append(node.parent)
            node = self.nodes.get(node.parent)
            guard += 1
        return result

    # Mutation ----------------------------------------------------------------

    def check(self, op):
        """Validate `op` against the current scene without applying it. Raises OpError."""
        validate_op(op)
        kind = op["op"]
        uuid = op["uuid"]
        if kind == "create":
            if uuid in self.nodes:
                raise OpError("uuid already exists")
            self._check_placement(uuid, op["kind"], op.get("parent"), op.get("prev"))
        else:
            node = self.nodes.get(uuid)
            if node is None:
                raise OpError("unknown uuid")
            if kind == "move":
                parent = op.get("parent")
                if parent == uuid or (parent is not None and uuid in self.ancestors(parent)):
                    raise OpError("cannot move a node under itself")
                self._check_placement(uuid, node.kind, parent, op.get("prev"))

    def _check_placement(self, uuid, kind, parent, prev):
        if parent is not None and parent not in self.nodes:
            raise OpError("unknown parent")
        if prev is not None:
            if prev == uuid:
                raise OpError("node cannot follow itself")
            prev_node = self.nodes.get(prev)
            if prev_node is None or prev_node.parent != parent or prev_node.kind != kind:
                raise OpError("prev is not a sibling")

    def apply(self, op):
        """Check and apply an op. Raises OpError; the scene is unchanged on error."""
        self.check(op)
        kind = op["op"]
        uuid = op["uuid"]
        if kind == "create":
            node = Node(uuid, op["kind"], op["ctype"], op.get("parent"), op.get("prev"),
                        op.get("name", ""), copy.deepcopy(op.get("data", {})))
            self.nodes[uuid] = node
            self._link(node)
        elif kind == "update":
            node = self.nodes[uuid]
            if "name" in op:
                node.name = op["name"]
            if op.get("patch"):
                diffmod.apply_patch(node.data, op["patch"])
        elif kind == "move":
            node = self.nodes[uuid]
            self._unlink(node)
            node.parent = op.get("parent")
            node.prev = op.get("prev")
            self._link(node)
        elif kind == "delete":
            node = self.nodes[uuid]
            for child in self.descendants(uuid):
                self._forget(child)
            self._unlink(node)
            self._forget(uuid)

    def _forget(self, uuid):
        """Drop a node that is deleted together with its whole subtree."""
        node = self.nodes.pop(uuid, None)
        self._next.pop(uuid, None)
        self._kids.pop(uuid, None)
        for each in KIND_ORDER:
            self._first.pop((uuid, each), None)
        return node

    def _link(self, node):
        """Insert node after node.prev (None = first) in its sibling chain."""
        key = (node.parent, node.kind)
        if node.prev is None:
            following = self._first.get(key)
            self._first[key] = node.uuid
        else:
            following = self._next.get(node.prev)
            self._next[node.prev] = node.uuid
        if following is not None:
            self._next[node.uuid] = following
            self.nodes[following].prev = node.uuid
        self._kids.setdefault(node.parent, set()).add(node.uuid)

    def _unlink(self, node):
        """Remove node from its chain: the next sibling now follows node.prev."""
        key = (node.parent, node.kind)
        following = self._next.pop(node.uuid, None)
        if node.prev is None:
            if following is not None:
                self._first[key] = following
            else:
                self._first.pop(key, None)
        else:
            if following is not None:
                self._next[node.prev] = following
            else:
                self._next.pop(node.prev, None)
        if following is not None:
            self.nodes[following].prev = node.prev
        siblings = self._kids.get(node.parent)
        if siblings is not None:
            siblings.discard(node.uuid)

    # Snapshot ----------------------------------------------------------------

    def snapshot_ops(self):
        """All nodes as create ops, parents before children and siblings in order."""
        ops = []
        queue = [None]
        index = 0
        while index < len(queue):
            parent = queue[index]
            index += 1
            for node in self.children(parent):
                ops.append(node.to_create_op())
                queue.append(node.uuid)
        return ops

    def load_snapshot(self, ops, seq=0):
        self._reset()
        for op in ops:
            self.apply(op)
        self.seq = seq
