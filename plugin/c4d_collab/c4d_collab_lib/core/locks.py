"""Lock table: who may edit which part of the scene.

A lock on a node covers the node and all its descendants (child objects, tags,
tracks). A user may edit a node when no other user holds a lock on it or on
any of its ancestors.
"""
from __future__ import absolute_import, division, print_function, unicode_literals


class LockTable(object):
    def __init__(self):
        self._owners = {}  # uuid -> user_id (only explicitly locked nodes)

    def owner(self, uuid):
        return self._owners.get(uuid)

    def items(self):
        return dict(self._owners)

    def effective_owner(self, scene, uuid):
        """Owner of the closest locked node among uuid and its ancestors."""
        owner = self._owners.get(uuid)
        if owner is not None:
            return owner
        for ancestor in scene.ancestors(uuid):
            owner = self._owners.get(ancestor)
            if owner is not None:
                return owner
        return None

    def can_edit(self, scene, user_id, uuid):
        owner = self.effective_owner(scene, uuid)
        if owner is not None and owner != user_id:
            return False
        # A lock held by someone else on a descendant also blocks structural edits.
        return True

    def can_edit_subtree(self, scene, user_id, uuid):
        """Needed for delete/move: nobody else may hold the node, an ancestor or a descendant."""
        if not self.can_edit(scene, user_id, uuid):
            return False
        for child in scene.descendants(uuid):
            owner = self._owners.get(child)
            if owner is not None and owner != user_id:
                return False
        return True

    def try_lock(self, scene, user_id, uuids):
        """Lock what is free. Returns (granted, denied) lists of uuids."""
        granted = []
        denied = []
        for uuid in uuids:
            if uuid not in scene:
                denied.append(uuid)
                continue
            if not self.can_edit_subtree(scene, user_id, uuid):
                denied.append(uuid)
                continue
            if self._owners.get(uuid) != user_id:
                self._owners[uuid] = user_id
                granted.append(uuid)
        return granted, denied

    def release(self, user_id, uuids):
        released = []
        for uuid in uuids:
            if self._owners.get(uuid) == user_id:
                del self._owners[uuid]
                released.append(uuid)
        return released

    def release_all(self, user_id):
        return self.release(user_id, [u for u, owner in self._owners.items() if owner == user_id])

    def forget(self, uuids):
        """Drop locks of nodes that no longer exist. Returns the dropped uuids."""
        dropped = []
        for uuid in uuids:
            if self._owners.pop(uuid, None) is not None:
                dropped.append(uuid)
        return dropped
