# -*- coding: utf-8 -*-
"""The MCP page's settings, buttons, server and tools against the REAL Cinema 4D API (run with c4dpy).

    c4dpy tests/c4d_real/mcp_check.py          (result: C4D_COLLAB_MCP_OUT, default %TEMP%/c4d_collab_mcp_check.json)

What this proves: the world container calls the settings use (Get/SetBool, Int32, Float, String), the page
object's value and button handlers, the real clipboard (written, then read back through c4d), the client
config writer, the server end to end (a real socket client, commands run by the pump), every tool on real
scene data, the toolset and the script runner. Dialogs are answered by the test. What it cannot prove (no
preferences window in c4dpy): that the page appears in Edit > Preferences and where, and that the buttons
reach `Message`; that is tests/c4d_manual/CHECKLIST.md, section 10.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "plugin", "c4d_collab")))

import c4d  # noqa: E402
from c4d import documents  # noqa: E402

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append({"name": name, "ok": bool(ok), "detail": "%s" % (detail,)})
    print("[mcp] %s %s %s" % ("PASS" if ok else "FAIL", name, detail))


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def read_clipboard():
    """The system clipboard as text. c4d.GetStringFromClipboard returns None without a user interface
    (c4dpy), so Windows is asked through PowerShell instead."""
    if sys.platform != "win32":
        return c4d.GetStringFromClipboard()
    import subprocess
    out = subprocess.check_output(["powershell", "-NoProfile", "-Command",
                                   "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw"])
    return out.decode("utf-8").rstrip("\r\n")


def main():
    from c4d_collab_lib.c4d_adapter import mcp_actions, mcp_prefs, mcp_service, plugin_ids
    from c4d_collab_lib.core import clipboard, mcp_clients
    from c4d_collab_lib.core.rpc import RpcClient
    settings = mcp_prefs.Settings

    check("the preference classes exist in this version", mcp_prefs.available())
    command, lead = mcp_clients.find_python()
    check("a real Python is found for the bridge (not the Store stub)", os.path.isfile(command)
          and "windowsapps" not in command.lower().replace("/", "\\"), (command, lead))

    # A private preferences folder and home: the real configs of AI apps are never touched
    folder = tempfile.mkdtemp(prefix="c4d_collab_mcp_real_")
    home = os.path.join(folder, "home")
    os.makedirs(os.path.join(home, ".cursor"))
    saved_env = dict((n, os.environ.get(n)) for n in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA"))
    os.environ["USERPROFILE"] = os.environ["HOME"] = home
    os.environ["APPDATA"] = os.path.join(home, "AppData", "Roaming")
    os.environ["LOCALAPPDATA"] = os.path.join(home, "AppData", "Local")
    real_path = c4d.storage.GeGetC4DPath
    c4d.storage.GeGetC4DPath = lambda which: folder if which == c4d.C4D_PATH_PREFS else real_path(which)
    asked, told = [], []
    answers = []
    mcp_actions.ask = lambda text: (asked.append(text), answers.pop(0) if answers else True)[1]
    mcp_actions.tell = lambda text: told.append(text)

    # Settings in the real world container ------------------------------------------------
    world = c4d.GetWorldContainerInstance()
    world.RemoveData(plugin_ids.MCP_CONTAINER) if hasattr(world, "RemoveData") else None
    check("defaults: off, safe", settings.enabled() is False and settings.allow_python() is False
          and settings.port() == 47921 and settings.timeout() == 30.0 and settings.log_calls() is True
          and settings.console_rejections() is False and len(settings.groups()) == 4,
          (settings.enabled(), settings.timeout(), settings.groups()))
    port = free_port()
    settings.set_port(port)
    settings.set(mcp_prefs.MCP_ENABLE, True)
    settings.set(mcp_prefs.MCP_ALLOW_PYTHON, True)
    settings.set_timeout(12.5)
    settings.set(mcp_prefs.MCP_LOG_CALLS, False)
    settings.set(mcp_prefs.MCP_TOOLSET_SYSTEM, False)
    settings.set(mcp_prefs.MCP_CLIENT, 3)
    token = settings.token()
    check("every kind of value reads back from the world container",
          settings.enabled() is True and settings.allow_python() is True and settings.port() == port
          and settings.timeout() == 12.5 and settings.log_calls() is False and settings.client() == "cursor"
          and "system" not in settings.groups() and len(token) == 32 and settings.token() == token,
          (settings.timeout(), settings.groups(), settings.client()))
    settings.set_port(80)
    settings.set_timeout(0.0)
    check("port and timeout are kept in range", settings.port() == 1024 and settings.timeout() == 0.1,
          (settings.port(), settings.timeout()))
    settings.set_port(port)
    settings.set_timeout(30.0)
    settings.set(mcp_prefs.MCP_LOG_CALLS, True)
    settings.set(mcp_prefs.MCP_TOOLSET_SYSTEM, True)
    renewed = settings.renew_token()
    check("a new token replaces the old one", renewed != token and settings.token() == renewed)

    # The page object (a Python PreferenceData; no C++ instance behind it here) -------------
    page = mcp_prefs.McpPreference()
    desc = lambda ident: c4d.DescID(c4d.DescLevel(ident))
    value = page.GetDParameter(None, desc(mcp_prefs.MCP_PORT), 0)
    check("the page reads a value", isinstance(value, tuple) and value[1] == port, value)
    masked = page.GetDParameter(None, desc(mcp_prefs.MCP_TOKEN), 0)[1]
    check("the token field is masked", masked.endswith(settings.token()[-4:]) and settings.token() not in masked, masked)
    settings.set(mcp_prefs.MCP_SHOW_TOKEN, True)
    check("Show Token shows it", page.GetDParameter(None, desc(mcp_prefs.MCP_TOKEN), 0)[1] == settings.token())
    settings.set(mcp_prefs.MCP_SHOW_TOKEN, False)
    done = page.SetDParameter(None, desc(mcp_prefs.MCP_ALLOW_PYTHON), False, 0)
    check("the page writes a value", done and settings.allow_python() is False, done)
    page.SetDParameter(None, desc(mcp_prefs.MCP_TOKEN), "typed over", 0)
    check("typing over the token changes nothing", settings.token() == renewed)
    settings.set(mcp_prefs.MCP_ENABLE, False)
    check("everything but the switch is ghosted while off", page.GetDEnabling(None, desc(mcp_prefs.MCP_ENABLE), None, 0, None)
          is True and page.GetDEnabling(None, desc(mcp_prefs.MCP_PORT), None, 0, None) is False)

    # Switching on goes through the page: the question first -------------------------------------
    answers[:] = [False]
    page.SetDParameter(None, desc(mcp_prefs.MCP_ENABLE), True, 0)
    check("saying no to the question leaves the server off", settings.enabled() is False
          and not mcp_service.server().running and len(asked) == 1, asked[-1][:40] if asked else "")
    answers[:] = [True]
    page.SetDParameter(None, desc(mcp_prefs.MCP_ENABLE), True, 0)
    server = mcp_service.server()
    check("saying yes starts it", settings.enabled() and server.running and server.port == port, server.status_text())
    settings.set(mcp_prefs.MCP_ALLOW_PYTHON, True)
    mcp_service.write_config()
    config_path = os.path.join(folder, "c4d_collab_mcp.json")
    with io.open(config_path, encoding="utf-8") as handle:
        config = json.load(handle)
    check("the config file names port, key, timeout and tools", config["port"] == port
          and config["key"] == settings.token() and config["timeout"] == 30.0
          and "run_script" in config["enabled_tools"] and "get_hierarchy" in config["enabled_tools"], config["enabled_tools"])
    check("the entry points at a real python and the bridge", os.path.isfile(config["command"]["command"])
          or config["command"]["command"] in ("py", "python", "python3"), config["command"]["command"])

    # Buttons: the real clipboard, written and read back -------------------------------------------
    page.Message(None, c4d.MSG_DESCRIPTION_COMMAND, {"id": desc(mcp_prefs.MCP_COPY_TOKEN)})
    clip = read_clipboard()
    check("Copy Token puts the token on the real clipboard", clip == settings.token(), clip)
    page.Message(None, c4d.MSG_DESCRIPTION_COMMAND, {"id": desc(mcp_prefs.MCP_REGENERATE_TOKEN)})
    with io.open(config_path, encoding="utf-8") as handle:
        config = json.load(handle)
    check("Regenerate Token changes it everywhere", config["key"] == settings.token() != token
          and server.running)
    settings.set(mcp_prefs.MCP_CLIENT, mcp_clients.CLIENTS.index(mcp_clients.OTHER))
    page.Message(None, c4d.MSG_DESCRIPTION_COMMAND, {"id": desc(mcp_prefs.MCP_COPY_CONFIG)})
    copied = json.loads(read_clipboard())
    check("Copy Config copies a usable mcpServers entry", "cinema4d" in copied["mcpServers"]
          and copied["mcpServers"]["cinema4d"]["args"][-2] == "--config", copied)
    settings.set(mcp_prefs.MCP_CLIENT, mcp_clients.CLIENTS.index(mcp_clients.CURSOR))
    mcp_service.forget_client_status()
    page.Message(None, c4d.MSG_DESCRIPTION_COMMAND, {"id": desc(mcp_prefs.MCP_UPDATE_CLIENT)})
    cursor_file = os.path.join(home, ".cursor", "mcp.json")
    with io.open(cursor_file, encoding="utf-8") as handle:
        written = json.load(handle)
    check("Update Selected Client writes the client's config", written["mcpServers"]["cinema4d"]["args"][-1] == config_path
          and mcp_service.client_status("cursor") == "Set up", written["mcpServers"]["cinema4d"]["args"])
    page.Message(None, c4d.MSG_DESCRIPTION_COMMAND, {"id": desc(mcp_prefs.MCP_REMOVE_CLIENT)})
    with io.open(cursor_file, encoding="utf-8") as handle:
        check("Remove from Client takes it out again", json.load(handle) == {"mcpServers": {}})

    # Server end to end -----------------------------------------------------------------------
    doc = documents.BaseDocument()
    documents.InsertBaseDocument(doc)
    documents.SetActiveDocument(doc)
    root = c4d.BaseObject(c4d.Onull)
    root.SetName("Root")
    doc.InsertObject(root)
    cube = c4d.BaseObject(c4d.Ocube)
    cube.SetName("Cube")
    doc.InsertObject(cube, root)
    cube.SetRelPos(c4d.Vector(10, 20, 30))
    cube.MakeTag(c4d.Tphong).SetName("Phong")
    cube.SetBit(c4d.BIT_ACTIVE)
    c4d.EventAdd()

    def call(cmd, args=None, timeout=20.0, delay=0.0):
        with io.open(config_path, encoding="utf-8") as handle:
            current = json.load(handle)
        outcome = {}

        def work():
            try:
                client = RpcClient(current["port"], current["key"], timeout=timeout)
                try:
                    outcome["result"] = client.call(cmd, args)
                finally:
                    client.close()
            except Exception as exc:
                outcome["error"] = "%s: %s" % (type(exc).__name__, exc)
        thread = threading.Thread(target=work)
        thread.start()
        time.sleep(delay)
        deadline = time.time() + timeout
        while thread.is_alive() and time.time() < deadline:
            mcp_service.pump()
            time.sleep(0.005)
        thread.join(1)
        return outcome

    ping = call("ping")
    check("ping answers with the real version", ping.get("result", {}).get("c4d") == c4d.GetC4DVersion(), ping)
    info = call("get_info")
    check("get_info lists documents and counts objects", info.get("result", {}).get("active", {}).get("objects") == 2
          and info["result"]["active"]["selected"] == ["Cube"], info)
    tree = call("get_hierarchy")
    top = tree.get("result", {}).get("objects", [{}])[0]
    check("get_hierarchy: nesting, tags, selection", top.get("name") == "Root"
          and top["children"][0]["name"] == "Cube" and top["children"][0]["selected"] is True
          and top["children"][0]["tags"][0]["name"] == "Phong", top)
    detail = call("get_object", {"name": "Cube"}).get("result", {})
    check("get_object: position and parameters", detail.get("position") == [10.0, 20.0, 30.0]
          and detail.get("parent") == "Root" and len(detail.get("params", [])) > 5
          and any(p.get("label") for p in detail["params"]), (detail.get("position"), len(detail.get("params", []))))
    with io.open(os.path.join(folder, "c4d_collab.log"), "w", encoding="utf-8") as handle:
        handle.write("one\ntwo\nthree\n")
    log = call("get_log", {"lines": 2}).get("result", {})
    check("get_log reads the end", log.get("lines") == ["two", "three"], log)
    script = call("run_script", {"code": "print('hello', doc.GetFps())\nresult = [o.GetName() for o in doc.GetObjects()]"})
    check("run_script returns output and result", script.get("result", {}).get("output") == "hello 30\n"
          and script["result"].get("result") == ["Root"], script)
    broken = call("run_script", {"code": "1/0"}).get("result", {})
    check("a failing script is reported, not fatal", broken.get("ok") is False and "ZeroDivisionError" in broken.get("error", ""))
    exited = call("run_script", {"code": "import sys\nsys.exit(2)"}).get("result", {})
    check("sys.exit in a script does not end Cinema 4D", exited.get("exit_code") == 2, exited)
    settings.set(mcp_prefs.MCP_TOOLSET_HIERARCHY, False)
    off = call("get_hierarchy")
    check("a switched-off toolset refuses at once", "Hierarchy toolset is switched off" in off.get("error", ""), off)
    settings.set(mcp_prefs.MCP_TOOLSET_HIERARCHY, True)
    settings.set_timeout(0.1)
    late = call("ping", delay=0.4)
    check("a command that waited too long for the main thread is dropped", "timed out" in late.get("error", ""), late)
    settings.set_timeout(30.0)
    settings.set(mcp_prefs.MCP_ALLOW_PYTHON, False)
    refused = call("run_script", {"code": "result = 1"})
    check("python is refused when switched off", "Allow Python Execution" in refused.get("error", ""), refused)
    audit = io.open(os.path.join(folder, "c4d_collab_mcp_audit.log"), encoding="utf-8").read()
    check("the audit log lists the calls and never the token", "get_object" in audit and "refused" in audit
          and settings.token() not in audit)

    mcp_service.shutdown()
    check("shutdown closes the port", not server.running)
    settings.set(mcp_prefs.MCP_ENABLE, False)
    mcp_service.apply()
    check("switching off removes the config file", not os.path.exists(config_path))

    c4d.storage.GeGetC4DPath = real_path
    for name, value in saved_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    shutil.rmtree(folder, ignore_errors=True)


def write(path):
    payload = {"c4d": c4d.GetC4DVersion(), "python": sys.version.split()[0],
               "passed": sum(1 for c in CHECKS if c["ok"]), "failed": sum(1 for c in CHECKS if not c["ok"]),
               "checks": CHECKS}
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    with io.open(path, "w", encoding="utf-8") as handle:
        handle.write(text if not isinstance(text, bytes) else text.decode("utf-8"))


if __name__ == "__main__":
    out = os.environ.get("C4D_COLLAB_MCP_OUT") or os.path.join(tempfile.gettempdir(), "c4d_collab_mcp_check.json")
    try:
        main()
    except BaseException:
        check("unexpected exception", False, traceback.format_exc())
    write(out)
    print("[mcp] %d passed, %d failed (%s)" % (sum(1 for c in CHECKS if c["ok"]), sum(1 for c in CHECKS if not c["ok"]), out))
