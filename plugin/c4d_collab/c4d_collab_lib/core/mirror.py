"""Client-side mirror of the authoritative session state.

The mirror holds what the server has *confirmed*: the snapshot, other users'
ops and our own ops once acknowledged. The adapter compares the live scene
against it to find local changes, and uses it to roll back rejected edits.

Usage (main thread):
    mirror = SessionMirror()
    cid = client.send_ops(ops); mirror.submitted(cid, ops)
    for message in client.poll():
        for event in mirror.handle(message):
            adapter.on_event(event)

Events are tuples:
    ("welcome", message)          ("snapshot", ops)
    ("remote_ops", author, ops)   ("rejected", [(op, reason), ...])
    ("locks", {uuid: user_id|None}) ("lock_denied", uuids)
    ("users", users)              ("presence", user_id, data)
    ("closed", reason)            ("error", message)
"""
from __future__ import absolute_import, division, print_function, unicode_literals

from .log import get_logger
from .model import OpError, SceneState

LOG = get_logger("mirror")


class SessionMirror(object):
    def __init__(self):
        self.scene = SceneState()
        self.locks = {}
        self.users = {}
        self.presence = {}
        self.user_id = None
        self.synced = False
        self._pending = {}
        self._snapshot = None

    def submitted(self, cid, ops):
        self._pending[cid] = list(ops)

    @property
    def pending_count(self):
        return sum(len(ops) for ops in self._pending.values())

    def owner(self, uuid):
        """Effective lock owner of uuid (checks ancestors too)."""
        owner = self.locks.get(uuid)
        if owner is not None:
            return owner
        for ancestor in self.scene.ancestors(uuid):
            owner = self.locks.get(ancestor)
            if owner is not None:
                return owner
        return None

    def editable(self, uuid):
        """May we change this node? (no other user locks it or an ancestor)"""
        if uuid is None:
            return True
        owner = self.owner(uuid)
        return owner is None or owner == self.user_id

    def subtree_editable(self, uuid):
        """May we delete/move this node? Also no other user may lock a descendant."""
        if not self.editable(uuid):
            return False
        for child in self.scene.descendants(uuid):
            owner = self.locks.get(child)
            if owner is not None and owner != self.user_id:
                return False
        return True

    def locked_by_others(self):
        """{uuid: user_id} of explicit locks held by other users."""
        return dict((u, o) for u, o in self.locks.items() if o != self.user_id)

    def user_name(self, user_id):
        user = self.users.get(user_id)
        return user.get("name", user_id) if user else user_id

    def handle(self, message):
        msg_type = message.get("t")
        handler = getattr(self, "_on_" + msg_type.lstrip("_"), None)
        if handler is None:
            return []
        return handler(message)

    def _apply_confirmed(self, op):
        try:
            self.scene.apply(op)
        except OpError as exc:
            # Should never happen: the server already validated the op in the same order.
            LOG.error("Mirror out of sync on %s %s: %s", op.get("op"), op.get("uuid"), exc)
            return False
        if "seq" in op:
            self.scene.seq = op["seq"]
        return True

    def _on_welcome(self, message):
        self.user_id = message.get("user_id")
        self.locks = dict(message.get("locks") or {})
        self.users = dict((u["id"], u) for u in message.get("users", []) if isinstance(u, dict))
        return [("welcome", message)]

    def _on_snapshot_begin(self, message):
        self._snapshot = []
        return []

    def _on_snapshot_chunk(self, message):
        if self._snapshot is not None:
            self._snapshot.extend(message.get("ops") or [])
        return []

    def _on_snapshot_end(self, message):
        ops = self._snapshot or []
        self._snapshot = None
        self.scene.load_snapshot(ops, message.get("seq", 0))
        self.synced = True
        return [("snapshot", ops)]

    def _on_ops(self, message):
        ops = [op for op in message.get("ops") or [] if self._apply_confirmed(op)]
        for op in ops:
            if op["op"] == "delete":
                self.locks.pop(op["uuid"], None)
        return [("remote_ops", message.get("author"), ops)] if ops else []

    def _on_ack(self, message):
        ops = self._pending.pop(message.get("cid"), [])
        accepted = set(message.get("accepted") or [])
        for index, op in enumerate(ops):
            if index in accepted:
                self._apply_confirmed(op)
        if "seq" in message:
            self.scene.seq = message["seq"]
        rejected = []
        for item in message.get("rejected") or []:
            index = item.get("i")
            if isinstance(index, int) and 0 <= index < len(ops):
                rejected.append((ops[index], item.get("reason", "")))
        return [("rejected", rejected)] if rejected else []

    def _on_locks(self, message):
        changes = message.get("changes") or {}
        for uuid, owner in changes.items():
            if owner is None:
                self.locks.pop(uuid, None)
            else:
                self.locks[uuid] = owner
        return [("locks", changes)]

    def _on_lock_denied(self, message):
        return [("lock_denied", message.get("uuids") or [])]

    def _on_users(self, message):
        self.users = dict((u["id"], u) for u in message.get("users", []) if isinstance(u, dict))
        for user_id in list(self.presence):
            if user_id not in self.users:
                del self.presence[user_id]
        return [("users", list(self.users.values()))]

    def _on_presence(self, message):
        user = message.get("user")
        self.presence[user] = message.get("data") or {}
        return [("presence", user, self.presence[user])]

    def _on_error(self, message):
        return [("error", message)]

    def _on_closed(self, message):
        self.synced = False
        return [("closed", message.get("reason", ""))]

