"""uuid <-> live Cinema 4D atom registry for the session document."""
from __future__ import absolute_import, division, print_function, unicode_literals

from . import compat


def is_alive(atom):
    if atom is None:
        return False
    check = getattr(atom, "IsAlive", None)
    if check is None:  # pragma: no cover - very old versions
        try:
            atom.GetName()
            return True
        except ReferenceError:
            return False
    try:
        return bool(check())
    except ReferenceError:  # pragma: no cover
        return False


class Registry(object):
    def __init__(self):
        self._atoms = {}

    def clear(self):
        self._atoms = {}

    def get(self, uuid):
        atom = self._atoms.get(uuid)
        if atom is not None and not is_alive(atom):
            del self._atoms[uuid]
            return None
        return atom

    def set(self, uuid, atom):
        self._atoms[uuid] = atom

    def uuids(self):
        return list(self._atoms)

    def discard(self, uuid):
        self._atoms.pop(uuid, None)

    def uuid_of(self, atom):
        return compat.read_uid(atom) if atom is not None else None

    def ensure(self, atom):
        """The atom's uuid, assigning one if it has none yet (a joint a weight tag names before the
        scan has reached the joint)."""
        if atom is None:
            return None
        uid = compat.read_uid(atom)
        if uid is None:
            uid = compat.new_uid()
            compat.write_uid(atom, uid)
        return uid

    def ensure_uid(self, atom, taken):
        """Return the atom's uuid, assigning a new one if it has none or a duplicate."""
        uid = compat.read_uid(atom)
        if uid is None or uid in taken:
            uid = compat.new_uid()
            compat.write_uid(atom, uid)
        return uid
