# -*- coding: utf-8 -*-
"""Does real Cinema 4D read the MCP page's resource files (.res, .h, .str)?   Run: py tests/c4d_real/res_check.py

c4dpy has no Preferences window, but the same description syntax is parsed for any plugin. This builds a
throw-away object plugin in a temp folder that loads "mcpprefs" from a copy of plugin/c4d_collab/res, starts
c4dpy with that plugin folder (g_additionalModulePath) and reads the parsed description back: every group,
parameter, data type, name, the dropdown's entries and the number of buttons. A typo in a .res, .h or .str
file shows up here as a missing or unnamed parameter.

Prints one line per check; exit code 0 when all pass. Needs c4dpy (see tests/c4d_real/run.py for its path).
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.abspath(os.path.join(HERE, "..", "..", "plugin", "c4d_collab"))
CANDIDATES = [r"C:\Program Files\c4dpy.exe", r"C:\Program Files\Maxon Cinema 4D 2023\c4dpy.exe",
              r"C:\Program Files\Maxon Cinema 4D 2024\c4dpy.exe"]

PROBE_PLUGIN = '''
import c4d

class Probe(c4d.plugins.ObjectData):
    def Init(self, node, isCloneInit=False):
        return True

    def GetDDescription(self, node, description, flags):
        if not description.LoadDescription("mcpprefs"):
            return False
        return True, flags | c4d.DESCFLAGS_DESC_LOADED

if __name__ == "__main__":
    c4d.plugins.RegisterObjectPlugin(1000009, "ResProbe", Probe, "mcpprefs", c4d.OBJECT_GENERATOR, None)
'''

PROBE_SCRIPT = '''
import c4d, json, sys
out = {"rows": [], "cycle": {}}
obj = c4d.BaseObject(1000009)
if obj is not None:
    for bc, did, group in obj.GetDescription(c4d.DESCFLAGS_DESC_NONE):
        if did.GetDepth() < 1:
            continue
        ident = did[0].id
        out["rows"].append([ident, did[did.GetDepth() - 1].dtype, bc[c4d.DESC_NAME]])
        cycle = bc[c4d.DESC_CYCLE]
        if cycle is not None:
            out["cycle"][str(ident)] = [value for _key, value in cycle]
with open(sys.argv[1], "w") as handle:
    json.dump(out, handle)
'''


def main():
    c4dpy = next((p for p in CANDIDATES if os.path.exists(p)), None)
    if not c4dpy:
        print("c4dpy not found")
        return 2
    sys.path.insert(0, PLUGIN)
    from c4d_collab_lib.core import mcp_clients
    work = tempfile.mkdtemp(prefix="c4d_collab_res_check_")
    try:
        plug = os.path.join(work, "plug", "mcpdesc")
        os.makedirs(plug)
        shutil.copytree(os.path.join(PLUGIN, "res"), os.path.join(plug, "res"))
        for name, text in (("mcpdesc.pyp", PROBE_PLUGIN), ("run.py", PROBE_SCRIPT)):
            with io.open(os.path.join(work if name == "run.py" else plug, name), "w", encoding="utf-8") as handle:
                handle.write(text)
        result = os.path.join(work, "out.json")
        subprocess.check_output([c4dpy, "g_additionalModulePath=" + os.path.join(work, "plug"),
                                 os.path.join(work, "run.py"), result], stderr=subprocess.STDOUT, timeout=300)
        with io.open(result, encoding="utf-8") as handle:
            data = json.load(handle)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    rows = dict((row[0], row) for row in data["rows"])
    BOOL, LONG, REAL, STRING, BUTTON, STATIC, GROUP = 400006001, 15, 19, 130, 8, 12, 1
    expected = {
        990: (GROUP, "Server"), 991: (GROUP, "Clients"), 992: (GROUP, "Security"), 993: (GROUP, "Toolset"),
        1000: (BOOL, "Allow MCP Server"), 1001: (LONG, "Port"), 1002: (BOOL, "Allow Python Execution (run_script)"),
        1005: (STRING, "Auth Token"), 1100: (STRING, "Host"), 1101: (BOOL, "Show Token"),
        1102: (BUTTON, "Copy Token"), 1103: (BUTTON, "Regenerate Token"), 1104: (REAL, "Request Timeout (s)"),
        1105: (LONG, "MCP Client"), 1106: (STRING, "Client Status"), 1107: (BUTTON, "Update Selected Client"),
        1108: (BUTTON, "Remove from Client"), 1109: (BUTTON, "Copy Config"), 1110: (STATIC, None),
        1111: (BOOL, "Log Incoming Calls"), 1112: (BOOL, "Show Rejected Calls in Console"),
        1113: (BOOL, None), 1114: (BOOL, None), 1115: (BOOL, None), 1116: (BOOL, None),
        1117: (STRING, "Status"), 1118: (STRING, "Config File"), 1119: (STATIC, None),
    }
    failed = 0
    for ident, (dtype, name) in sorted(expected.items()):
        row = rows.get(ident)
        ok = row is not None and row[1] == dtype and bool(row[2]) and (name is None or row[2] == name)
        failed += 0 if ok else 1
        print("%s %d %s" % ("PASS" if ok else "FAIL", ident, row if row else "missing from the parsed description"))
    names = [mcp_clients.NAMES[c] for c in mcp_clients.CLIENTS]
    cycle = data["cycle"].get("1105")
    ok = cycle == names
    failed += 0 if ok else 1
    print("%s the MCP Client dropdown has the clients in order %s" % ("PASS" if ok else "FAIL", cycle))
    unexpected = sorted(i for i in rows if i not in expected and 500000000 > i)
    ok = not unexpected
    failed += 0 if ok else 1
    print("%s no parameter the code does not know about %s" % ("PASS" if ok else "FAIL", unexpected or ""))
    print("%d failed" % failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
