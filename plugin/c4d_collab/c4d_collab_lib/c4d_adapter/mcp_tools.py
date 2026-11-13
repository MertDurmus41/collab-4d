"""The commands an AI assistant can run in Cinema 4D through the MCP page (see core/mcp_spec.py).

Every function takes the argument dict and returns plain data (JSON). They run on the main thread, called by
mcp_service. Reading tools never change the scene; run_script can, and only runs when it is allowed.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import io
import os
import sys
import traceback

import c4d

from ..core.compat import PY2, integer_types, is_fatal, string_types
from ..core.mcp_spec import AUDIT_FILE
from . import compat
from .serializers import params

MAX_NODES = 20000
MAX_OUTPUT = 200 * 1024
MAX_LOG_BYTES = 512 * 1024


class ToolError(Exception):
    """A request that cannot be served; the message goes back to the assistant."""


def _text(value):
    getter = getattr(value, "GetString", None)
    return compat.from_c4d_str(getter() if getter is not None else value)


def _integer(args, name, default, low, high):
    value = args.get(name, default)
    if isinstance(value, bool) or not isinstance(value, integer_types):
        raise ToolError("%s must be an integer" % name)
    return max(low, min(high, value))


def _boolean(args, name, default):
    value = args.get(name, default)
    if not isinstance(value, bool):
        raise ToolError("%s must be true or false" % name)
    return value


def _type_name(atom):
    try:
        return compat.from_c4d_str(atom.GetTypeName())
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        return ""


def _next(obj):
    """The object after `obj` in hierarchy order (children first)."""
    if obj.GetDown() is not None:
        return obj.GetDown()
    while obj is not None and obj.GetNext() is None:
        obj = obj.GetUp()
    return obj.GetNext() if obj is not None else None


def _walk(first, limit):
    obj, count = first, 0
    while obj is not None and count < limit:
        count += 1
        yield obj
        obj = _next(obj)


def _active_doc():
    doc = c4d.documents.GetActiveDocument()
    if doc is None:
        raise ToolError("there is no active document")
    return doc


# Tools ----------------------------------------------------------------------------------------

def ping(args, context):
    return {"pong": True, "c4d": c4d.GetC4DVersion(), "python": "%d.%d.%d" % sys.version_info[:3]}


def get_info(args, context):
    info = {"c4d": c4d.GetC4DVersion(), "python": "%d.%d.%d" % sys.version_info[:3],
            "python_execution_allowed": bool(context.allow_python()), "documents": []}
    doc = c4d.documents.GetFirstDocument()
    active = c4d.documents.GetActiveDocument()
    guard = 0
    while doc is not None and guard < 200:
        guard += 1
        entry = {"name": compat.from_c4d_str(doc.GetDocumentName()), "path": _text(doc.GetDocumentPath()),
                 "active": doc == active}
        info["documents"].append(entry)
        doc = doc.GetNext()
    if active is not None:
        count = sum(1 for _obj in _walk(active.GetFirstObject(), MAX_NODES))
        fps = active.GetFps()
        info["active"] = {
            "objects": count, "fps": fps,
            "frame": active.GetTime().GetFrame(fps),
            "frame_range": [active.GetMinTime().GetFrame(fps), active.GetMaxTime().GetFrame(fps)],
            "selected": [compat.from_c4d_str(o.GetName()) for o in
                         active.GetActiveObjects(compat.DESCFLAGS_GET_ACTIVE)][:50],
        }
    return info


def _node(obj, depth, max_depth, include_tags, counter):
    counter[0] += 1
    node = {"name": compat.from_c4d_str(obj.GetName()), "type": obj.GetType(), "type_name": _type_name(obj),
            "selected": bool(obj.GetBit(c4d.BIT_ACTIVE))}
    if include_tags:
        node["tags"] = [{"name": compat.from_c4d_str(t.GetName()), "type": t.GetType(), "type_name": _type_name(t)}
                        for t in _tags(obj)]
    child = obj.GetDown()
    if child is not None:
        if depth + 1 >= max_depth:
            node["children_cut"] = True
        else:
            node["children"] = []
            while child is not None and counter[0] < counter[1]:
                node["children"].append(_node(child, depth + 1, max_depth, include_tags, counter))
                child = child.GetNext()
            if child is not None:
                node["children_cut"] = True
    return node


def _tags(obj):
    tag = obj.GetFirstTag()
    found = []
    while tag is not None and len(found) < 200:
        found.append(tag)
        tag = tag.GetNext()
    return found


def get_hierarchy(args, context):
    doc = _active_doc()
    max_depth = _integer(args, "max_depth", 8, 1, 50)
    include_tags = _boolean(args, "include_tags", True)
    limit = _integer(args, "limit", 2000, 1, MAX_NODES)
    counter = [0, limit]
    roots = []
    obj = doc.GetFirstObject()
    while obj is not None and counter[0] < limit:
        roots.append(_node(obj, 0, max_depth, include_tags, counter))
        obj = obj.GetNext()
    return {"document": compat.from_c4d_str(doc.GetDocumentName()), "objects": roots,
            "listed": counter[0], "cut": obj is not None or counter[0] >= limit}


def _vector(value):
    return [round(value.x, 6), round(value.y, 6), round(value.z, 6)]


def _parameters(atom, limit):
    listed = []
    for bc, desc_id, _group in atom.GetDescription(compat.DESCFLAGS_DESC):
        if desc_id.GetDepth() < 1:
            continue
        dtype = desc_id[desc_id.GetDepth() - 1].dtype
        if dtype not in params.SUPPORTED_TYPES or desc_id[0].id in params.EXCLUDED_IDS:
            continue
        try:
            value = atom.GetParameter(desc_id, compat.DESCFLAGS_GET)
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            continue
        if dtype in params.LINK_TYPES:
            encoded = compat.from_c4d_str(value.GetName()) if value is not None else None
        else:
            ok, encoded = params.encode(value, dtype, lambda _atom: None)
            if not ok:
                continue
        try:
            label = compat.from_c4d_str(bc[c4d.DESC_NAME])
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            label = ""
        listed.append({"key": params.key_of(desc_id), "label": label, "value": encoded})
        if len(listed) >= limit:
            break
    return listed


def get_object(args, context):
    name = args.get("name")
    if not isinstance(name, string_types) or not name:
        raise ToolError("name is required")
    doc = _active_doc()
    obj = doc.SearchObject(compat.native_str(name))
    if obj is None:
        raise ToolError("no object called %r in the active document" % name)
    result = {"name": compat.from_c4d_str(obj.GetName()), "type": obj.GetType(), "type_name": _type_name(obj),
              "selected": bool(obj.GetBit(c4d.BIT_ACTIVE)),
              "parent": compat.from_c4d_str(obj.GetUp().GetName()) if obj.GetUp() is not None else None,
              "position": _vector(obj.GetRelPos()), "rotation": _vector(obj.GetRelRot()),
              "scale": _vector(obj.GetRelScale()),
              "tags": [{"name": compat.from_c4d_str(t.GetName()), "type": t.GetType(), "type_name": _type_name(t)}
                       for t in _tags(obj)]}
    if hasattr(obj, "GetPointCount"):
        result["points"] = obj.GetPointCount()
    if hasattr(obj, "GetPolygonCount"):
        result["polygons"] = obj.GetPolygonCount()
    if _boolean(args, "include_params", True):
        result["params"] = _parameters(obj, _integer(args, "limit", 400, 1, 5000))
    return result


def _tail(path, lines):
    if not os.path.isfile(path):
        return {"path": path, "exists": False, "lines": []}
    with io.open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - MAX_LOG_BYTES))
        raw = handle.read()
    text = raw.decode("utf-8", "replace").splitlines()
    return {"path": path, "exists": True, "lines": text[-lines:]}


def get_log(args, context):
    lines = _integer(args, "lines", 100, 1, 1000)
    which = args.get("log", "collab")
    if which == "audit":
        return _tail(os.path.join(compat.prefs_path(), AUDIT_FILE), lines)
    if which != "collab":
        raise ToolError("log must be \"collab\" or \"audit\"")
    return _tail(os.path.join(compat.prefs_path(), "c4d_collab.log"), lines)


class _Capture(object):
    """Collects what a script prints; takes bytes or text, whatever the Python version prints."""

    def __init__(self):
        self.parts = []
        self.size = 0

    def write(self, value):
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        if self.size < MAX_OUTPUT:
            self.parts.append(value)
            self.size += len(value)

    def flush(self):
        pass

    def text(self):
        text = "".join(self.parts)
        return text[:MAX_OUTPUT] + ("\n[output cut]" if self.size >= MAX_OUTPUT else "")


def run_script(args, context):
    if not context.allow_python():
        raise ToolError("Python execution is switched off. Turn on \"Allow Python Execution\" in "
                        "Edit > Preferences > MCP.")
    code, path = args.get("code"), args.get("file")
    if (code is None) == (path is None):
        raise ToolError("give either code or file")
    if code is not None:
        if not isinstance(code, string_types):
            raise ToolError("code must be text")
        source, filename = code, "<mcp>"
    else:
        if not isinstance(path, string_types) or not os.path.isfile(path):
            raise ToolError("file not found: %r" % (path,))
        with io.open(path, "rb") as handle:
            source, filename = handle.read(), path
    if PY2 and not isinstance(source, bytes):
        source = source.encode("utf-8")  # Python 2 refuses unicode source that has an encoding line
    namespace = {"__name__": "__c4d_mcp__", "c4d": c4d, "doc": c4d.documents.GetActiveDocument()}
    capture = _Capture()
    out, err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = capture
    outcome = {"ok": True}
    try:
        try:
            exec(compile(source, compat.native_str(filename), "exec"), namespace)
        except SystemExit as exc_:
            # sys.exit() / exit() in a script ends the script, never Cinema 4D
            code = exc_.code
            outcome = {"ok": code in (None, 0), "exit_code": code if isinstance(code, integer_types) else str(code)}
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            outcome = {"ok": False, "error": "".join(traceback.format_exception_only(type(exc_), exc_)).strip()}
            capture.write("\n" + traceback.format_exc())
    finally:
        sys.stdout, sys.stderr = out, err
    try:
        c4d.EventAdd()
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
    outcome["output"] = capture.text()
    if "result" in namespace:
        outcome["result"] = namespace["result"]
    return outcome


TOOLS = {
    "ping": ping, "get_info": get_info, "get_hierarchy": get_hierarchy, "get_object": get_object,
    "get_log": get_log, "run_script": run_script,
}


def call(name, args, context):
    """Run one tool. Raises ToolError for a bad request."""
    function = TOOLS.get(name)
    if function is None:
        raise ToolError("unknown command %r" % (name,))
    return function(args, context)
