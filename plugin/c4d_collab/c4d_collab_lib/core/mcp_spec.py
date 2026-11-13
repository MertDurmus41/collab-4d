"""The tools the MCP page offers: names, descriptions and argument schemas.

One list shared by the stdio bridge (which tells the AI app about the tools, also while Cinema 4D is not
running) and by the Cinema 4D side (which checks the arguments). No c4d import, Python 2.7 and 3.x.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

SERVER_NAME = "cinema4d-collab-mcp"
DEFAULT_PORT = 47921
DEFAULT_TIMEOUT = 30.0     # seconds a command may wait for Cinema 4D's main thread
MIN_TIMEOUT, MAX_TIMEOUT = 0.1, 3600.0
CONFIG_FILE = "c4d_collab_mcp.json"
AUDIT_FILE = "c4d_collab_mcp_audit.log"

# The toolsets of the page (Preferences > MCP > Toolset): a group switched off hides its tools from the AI app
# and refuses their calls. run_script has no group: "Allow Python Execution" alone decides.
GROUP_BASICS, GROUP_HIERARCHY, GROUP_ENTITIES, GROUP_SYSTEM = "basics", "hierarchy", "entities", "system"
GROUPS = [GROUP_BASICS, GROUP_HIERARCHY, GROUP_ENTITIES, GROUP_SYSTEM]
GROUP_NAMES = {GROUP_BASICS: "Basics", GROUP_HIERARCHY: "Hierarchy", GROUP_ENTITIES: "Entities", GROUP_SYSTEM: "System"}

_OBJECT = "object"

TOOLS = [
    {
        "name": "ping",
        "description": "Check that Cinema 4D answers. Returns the Cinema 4D and Python versions.",
        "inputSchema": {"type": _OBJECT, "properties": {}},
        "timeout": 10,
        "group": GROUP_BASICS,
    },
    {
        "name": "get_info",
        "description": "Cinema 4D version, Python version, the active document (name, path, fps, frame range, "
                       "object count, selection), all open documents, and whether Python execution is allowed.",
        "inputSchema": {"type": _OBJECT, "properties": {}},
        "timeout": 20,
        "group": GROUP_BASICS,
    },
    {
        "name": "get_hierarchy",
        "description": "The object hierarchy of the active document: name, type, selected flag, tags and children. "
                       "Large scenes are cut at `limit` objects.",
        "inputSchema": {"type": _OBJECT, "properties": {
            "max_depth": {"type": "integer", "description": "How many levels to list (default 8)."},
            "include_tags": {"type": "boolean", "description": "List the tags of each object (default true)."},
            "limit": {"type": "integer", "description": "Maximum number of objects (default 2000)."},
        }},
        "timeout": 60,
        "group": GROUP_HIERARCHY,
    },
    {
        "name": "get_object",
        "description": "Details of one object (found by name) of the active document: type, matrix, tags and "
                       "its simple parameters with their names and values.",
        "inputSchema": {"type": _OBJECT, "properties": {
            "name": {"type": "string", "description": "The object's name (the first one with that name)."},
            "include_params": {"type": "boolean", "description": "Include parameters (default true)."},
            "limit": {"type": "integer", "description": "Maximum number of parameters (default 400)."},
        }, "required": ["name"]},
        "timeout": 30,
        "group": GROUP_ENTITIES,
    },
    {
        "name": "get_log",
        "description": "The last lines of the Collab 4D log (`log`: \"collab\") or of the MCP audit log "
                       "(`log`: \"audit\").",
        "inputSchema": {"type": _OBJECT, "properties": {
            "lines": {"type": "integer", "description": "How many lines (default 100, at most 1000)."},
            "log": {"type": "string", "enum": ["collab", "audit"]},
        }},
        "timeout": 20,
        "group": GROUP_SYSTEM,
    },
    {
        "name": "run_script",
        "description": "Run Python inside Cinema 4D on the main thread, with `c4d` and `doc` (the active document) "
                       "available. Returns what was printed and the value of a variable called `result`. Only works "
                       "when \"Allow Python Execution\" is on in Preferences > MCP. Cinema 4D cannot respond while "
                       "the script runs. Give either `code` or `file` (a path to a .py file).",
        "inputSchema": {"type": _OBJECT, "properties": {
            "code": {"type": "string", "description": "Python source."},
            "file": {"type": "string", "description": "Path of a Python file to run."},
        }},
        "timeout": 600,
    },
]

TOOL_NAMES = frozenset(tool["name"] for tool in TOOLS)
TOOL_TIMEOUTS = dict((tool["name"], tool["timeout"]) for tool in TOOLS)
TOOL_GROUPS = dict((tool["name"], tool.get("group")) for tool in TOOLS)   # None: no toolset (run_script)
PYTHON_TOOLS = frozenset(["run_script"])


def tools_of(groups, allow_python):
    """Names of the tools that are on, given the enabled toolset groups and the Python permission."""
    names = []
    for tool in TOOLS:
        group = tool.get("group")
        if tool["name"] in PYTHON_TOOLS:
            if allow_python:
                names.append(tool["name"])
        elif group in groups:
            names.append(tool["name"])
    return names
