"""Cinema 4D version differences (R17 -> 2026) in one place.

Measured in Phase 0 (see ARCHITECTURE.md, section 9):
- R17-R22 run Python 2 and their API wants native `str`, never `unicode`.
- R19 only has HDIRTYFLAGS_ALL; R20+ (verified on 2023) has the detailed flags.
- R19's AddUniqueID only accepts c4d.storage.ByteSeq.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import sys
import uuid as uuidlib

import c4d

from ..core.compat import PY2, is_fatal, text_type, to_text
from . import plugin_ids

VERSION = c4d.GetC4DVersion()
UUID_BYTES = 16


def native_str(value):
    """Text for c4d API calls: utf-8 `str` on Python 2, `str` on Python 3."""
    if value is None:
        value = ""
    if PY2 and isinstance(value, text_type):  # pragma: no cover - Python 2 only
        return value.encode("utf-8")
    if not PY2 and isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value if isinstance(value, str) else str(value)


def from_c4d_str(value):
    """Text coming from c4d (bytes on Python 2) as unicode."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return to_text(value) if _is_utf8(value) else value.decode("latin-1")
    return value


def _is_utf8(raw):
    try:
        raw.decode("utf-8")
        return True
    except UnicodeError:
        return False


# Flag names renamed in R20 (DESCFLAGS_*_0 -> DESCFLAGS_*_NONE) ------------------------

def _first_flag(*names):
    for name in names:
        value = getattr(c4d, name, None)
        if value is not None:
            return value
    return 0


DESCFLAGS_GET = _first_flag("DESCFLAGS_GET_NONE", "DESCFLAGS_GET_0")
DESCFLAGS_SET = _first_flag("DESCFLAGS_SET_NONE", "DESCFLAGS_SET_0")
DESCFLAGS_DESC = _first_flag("DESCFLAGS_DESC_NONE", "DESCFLAGS_DESC_0")
DESCFLAGS_GET_ACTIVE = _first_flag("GETACTIVEOBJECTFLAGS_NONE", "GETACTIVEOBJECTFLAGS_0")
BUILD_FLAGS = _first_flag("BUILDFLAGS_NONE", "BUILDFLAGS_0")
DIRTY_DATA = _first_flag("DIRTYFLAGS_DATA")
DIRTY_MATRIX = _first_flag("DIRTYFLAGS_MATRIX")


def type_ids(*names):
    """Resolve c4d type constants by name, skipping ones this version does not have."""
    return frozenset(value for value in (getattr(c4d, name, None) for name in names) if value is not None)


def allocate(factory, *args):
    """Create a Cinema 4D atom (BaseObject, BaseTag...). Returns None when its type is missing.

    A type that is not installed raises a bare BaseException ("the plugin ... is missing"),
    which `except Exception` does not catch (found by the Phase 7 real-C4D run).
    """
    allocate.last_error = None
    try:
        return factory(*args)
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException as exc:
        allocate.last_error = "%s: %s" % (type(exc).__name__, exc)
        return None


allocate.last_error = None


# Document dirty flags ------------------------------------------------------------------

def _flag(name):
    return getattr(c4d, name, None)


HDIRTY_ALL = _flag("HDIRTYFLAGS_ALL")
HDIRTY_DETAILED = dict(
    (key, _flag("HDIRTYFLAGS_" + key))
    for key in ("OBJECT", "OBJECT_MATRIX", "OBJECT_HIERARCHY", "ANIMATION", "TAG", "MATERIAL")
)
HAS_DETAILED_HDIRTY = all(value is not None for value in HDIRTY_DETAILED.values())
if HAS_DETAILED_HDIRTY and _flag("HDIRTYFLAGS_SHADER") is not None:
    HDIRTY_DETAILED["SHADER"] = _flag("HDIRTYFLAGS_SHADER")  # shader edits do not move the MATERIAL counter
# Counters that say "a material or shader changed". Where they are not available (R19) every change
# looks like one, so materials are looked at on every scan.
MATERIAL_COUNTERS = ("MATERIAL", "SHADER") if "SHADER" in HDIRTY_DETAILED else None
# Counters that only move when objects are moved or edited (not for hierarchy, tags, materials...).
OBJECT_ONLY_COUNTERS = frozenset(["OBJECT", "OBJECT_MATRIX"]) if HAS_DETAILED_HDIRTY else None


# BaseDocument.GetHDirty is new in R19 (Maxon's API docs): R17 and R18 have no document-level counters.
HAS_HDIRTY = hasattr(getattr(c4d.documents, "BaseDocument", None), "GetHDirty")
_NO_COUNTER_TICKS = [0]


def doc_dirty(doc):
    """Snapshot of the document dirty counters as {name: count}.

    On versions without detailed flags (R19) every name maps to the ALL counter,
    so any change looks like "everything may have changed" -> full scan.
    Without document counters at all (R17, R18) the snapshot changes on every call: every tick looks like
    "something may have changed", so the scene is scanned each tick (the tick's own rest period keeps that
    to a fraction of the main thread, see bridge.tick).
    """
    result = {}
    if not HAS_HDIRTY:
        _NO_COUNTER_TICKS[0] += 1
        for key in HDIRTY_DETAILED:
            result[key] = _NO_COUNTER_TICKS[0]
        return result
    if HAS_DETAILED_HDIRTY:
        for key, value in HDIRTY_DETAILED.items():
            result[key] = doc.GetHDirty(value)
    else:
        total = doc.GetHDirty(HDIRTY_ALL)
        for key in HDIRTY_DETAILED:
            result[key] = total
    return result


# Node uuids (AddUniqueID) -----------------------------------------------------------

def _to_byteseq(raw):
    """R19 wants c4d.storage.ByteSeq; measured working: allocate then assign per byte."""
    byteseq = getattr(c4d.storage, "ByteSeq", None)
    if byteseq is None:
        raise TypeError("this Cinema 4D has no ByteSeq")
    seq = byteseq(None, len(raw))
    for index, value in enumerate(bytearray(raw)):
        seq[index] = chr(value) if PY2 else value
    return seq


def read_uid(atom):
    """The node uuid stored on atom, as a 32-char hex string, or None."""
    try:
        found = atom.FindUniqueID(plugin_ids.UNIQUE_ID_APP)
    except Exception:
        return None
    if found is None:
        return None
    try:
        raw = bytes(bytearray(found))
    except Exception:
        try:
            raw = bytes(found)
        except Exception:
            return None
    if len(raw) != UUID_BYTES:
        return None
    return uuidlib.UUID(bytes=raw).hex


def write_uid(atom, uid_hex):
    """Store uid on atom. Only call once per atom: AddUniqueID appends, it does not replace."""
    raw = uuidlib.UUID(hex=uid_hex).bytes
    try:
        atom.AddUniqueID(plugin_ids.UNIQUE_ID_APP, raw)
    except TypeError:
        atom.AddUniqueID(plugin_ids.UNIQUE_ID_APP, _to_byteseq(raw))
    return read_uid(atom) == uid_hex


def new_uid():
    return uuidlib.uuid4().hex


# Misc ---------------------------------------------------------------------------

def _has(owner, name):
    return owner is not None and hasattr(owner, name)


def capabilities():
    """What this Cinema 4D can do for us, as [(name, value)]. Written to the log at start-up so a report from
    any version says which fallbacks are in use (see tests/compat/api_matrix.py for the reasons)."""
    character = getattr(getattr(c4d, "modules", None), "character", None)
    if HAS_DETAILED_HDIRTY:
        counters = "detailed document counters"
    elif HAS_HDIRTY:
        counters = "one document counter (R19)"
    else:
        counters = "none: the scene is scanned every tick (R17/R18)"
    return [
        ("cinema4d", VERSION),
        ("python", "%d.%d.%d" % sys.version_info[:3]),
        ("document counters", counters),
        ("preference page (MCP)", _has(getattr(c4d, "plugins", None), "PreferenceData")),
        ("selection ranges", _has(getattr(c4d, "BaseSelect", None), "GetRange") or "by flags (before R19)"),
        ("skin weights write", "whole map" if _has(getattr(character, "CAWeightTag", None), "SetWeightMap")
         else "point by point (before R21)"),
        ("viewport name labels", _has(getattr(c4d, "BaseDraw", None), "DrawHUDText") or "frames only (before R18)"),
        ("node ids", _has(getattr(c4d, "BaseList2D", None), "AddUniqueID")),
    ]


def describe_capabilities():
    return ", ".join("%s: %s" % item for item in capabilities())


def client_info(plugin_version):
    return {
        "app": "Cinema 4D",
        "app_version": VERSION,
        "python": "%d.%d.%d" % sys.version_info[:3],
        "plugin": plugin_version,
    }


def prefs_path():
    return from_c4d_str(c4d.storage.GeGetC4DPath(c4d.C4D_PATH_PREFS))


def is_playing():
    """Is the timeline playing right now?"""
    try:
        return bool(c4d.CheckIsRunning(c4d.CHECKISRUNNING_ANIMATIONRUNNING))
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        return False
