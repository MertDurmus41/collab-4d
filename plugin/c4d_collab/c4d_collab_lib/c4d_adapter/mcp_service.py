"""Runs the MCP server behind the Preferences page: start/stop, config file, audit log, main-thread pump.

All of this happens on the main thread except the socket thread inside core/rpc.py, which only moves
bytes and queues commands; `pump()` (called from the MessageData below) runs them.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import io
import json
import os
import time

import c4d

from ..core import mcp_clients
from ..core.compat import error_text, is_fatal
from ..core.log import get_logger
from ..core.mcp_spec import AUDIT_FILE, CONFIG_FILE, GROUP_NAMES, SERVER_NAME, TOOL_GROUPS, TOOL_NAMES, tools_of
from ..core.rpc import RpcServer
from . import compat, mcp_tools, plugin_ids
from .mcp_prefs import Settings

LOG = get_logger("mcp")

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
BRIDGE = os.path.join(PLUGIN_DIR, "mcp_stdio_bridge.py")
TIMER_MS = 50
MAX_AUDIT_BYTES = 2 * 1024 * 1024
ARG_PREVIEW = 300

_SERVER = None
_KEY = ""   # the socket thread must not touch the world container: it reads the key from here


class _Context(object):
    """What the tools may ask: today only whether Python may run (read when the command runs)."""

    @staticmethod
    def allow_python():
        return Settings.allow_python()


def wake():
    """Called from the socket thread when a command is waiting."""
    c4d.SpecialEventAdd(plugin_ids.MCP_MESSAGE)


def server():
    global _SERVER
    if _SERVER is None:
        _SERVER = RpcServer(key_of=lambda: _KEY, notify=wake, log=LOG.info, timeout_of=Settings.timeout)
    return _SERVER


def status_text():
    return server().status_text() if _SERVER is not None else "Off"


CLIENT_STATUS_TTL = 2.0
_CLIENT_STATUS = {}   # client -> (time, status): the page asks again on every redraw and the files can be big


def client_status(client):
    """"Set up", "Not set up", "Not installed" or "Set up by hand", cached for a moment."""
    now = time.time()
    cached = _CLIENT_STATUS.get(client)
    if cached is not None and now - cached[0] < CLIENT_STATUS_TTL:
        return cached[1]
    value = mcp_clients.status(client)
    _CLIENT_STATUS[client] = (now, value)
    return value


def forget_client_status():
    _CLIENT_STATUS.clear()


def client_file(client):
    """What the page shows as the selected client's config file."""
    if client == mcp_clients.OTHER:
        return "(use Copy Config)"
    if client == mcp_clients.CLAUDE_CODE:
        return "(set up with a terminal command)"
    return mcp_clients.config_file(client) or ""


def client_entry():
    """The entry an AI app needs to start the stdio bridge (python, bridge script, config file)."""
    return mcp_clients.server_entry(BRIDGE, _config_file())


def apply(force=False):
    """Make the server match the saved settings. Never raises."""
    global _KEY
    try:
        srv = server()
        if Settings.enabled():
            _KEY = Settings.token()
            port = Settings.port()
            if force or not srv.running or srv.port != port:
                if srv.running:
                    srv.stop()
                srv.start(port)
            write_config()
        else:
            if srv.running:
                srv.stop()
            srv.error = None
            _KEY = ""
            _remove_config()
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        LOG.exception("MCP: applying the settings failed")


def autostart():
    """At program start: start the server again if it was left switched on."""
    if Settings.enabled():
        apply()


def shutdown():
    global _KEY
    _KEY = ""
    if _SERVER is not None:
        _SERVER.stop()


# Config file ------------------------------------------------------------------------------------

def _config_file():
    return os.path.join(compat.prefs_path(), CONFIG_FILE)


def write_config():
    """The file the stdio bridge reads (port and key) and that tells the user how to add it to their AI
    app. Rewritten whenever the settings change, so a new key needs no change on the AI side."""
    if not Settings.enabled():
        return
    srv = server()
    path = _config_file()
    entry = client_entry()
    data = {
        "server": SERVER_NAME,
        "port": srv.port or Settings.port(),
        "key": Settings.token(),
        "timeout": Settings.timeout(),
        "python_execution_allowed": Settings.allow_python(),
        "enabled_tools": tools_of(Settings.groups(), Settings.allow_python()),
        "bridge": BRIDGE,
        "command": entry,
        "claude_code": mcp_clients.claude_code_command(entry),
        "mcp_json": {"mcpServers": {mcp_clients.SERVER_KEY: entry}},
    }
    text = json.dumps(data, indent=2, ensure_ascii=False)
    temp = path + ".tmp"
    try:
        with io.open(temp, "w", encoding="utf-8") as handle:
            handle.write(text if not isinstance(text, bytes) else text.decode("utf-8"))
        if os.path.exists(path):
            os.remove(path)
        os.rename(temp, path)
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        LOG.warning("MCP: could not write %s (%s)", path, error_text(exc_))


def _remove_config():
    try:
        if os.path.isfile(_config_file()):
            os.remove(_config_file())
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        LOG.warning("MCP: could not remove the config file (%s)", error_text(exc_))


# Commands ---------------------------------------------------------------------------------------

def _audit(line):
    """One line per command in c4d_collab_mcp_audit.log (time, command, arguments, outcome)."""
    if not Settings.log_calls():
        return
    path = os.path.join(compat.prefs_path(), AUDIT_FILE)
    try:
        if os.path.isfile(path) and os.path.getsize(path) > MAX_AUDIT_BYTES:
            old = path + ".old"
            if os.path.exists(old):
                os.remove(old)
            os.rename(path, old)
        with io.open(path, "a", encoding="utf-8") as handle:
            handle.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))
    except BaseException as exc_:
        if is_fatal(exc_):
            raise


def _preview(args):
    try:
        text = json.dumps(args, ensure_ascii=False)
    except (TypeError, ValueError):
        text = "?"
    text = text if not isinstance(text, bytes) else text.decode("utf-8", "replace")
    text = text.replace("\n", "\\n")
    return text if len(text) <= ARG_PREVIEW else text[:ARG_PREVIEW] + "..."


def _rejected(cmd, args, text):
    _audit("%s %s -> refused: %s" % (cmd, _preview(args), text))
    if Settings.console_rejections():
        print("C4D MCP: rejected %s: %s" % (cmd, text))
    return mcp_tools.ToolError(text)


def execute(cmd, args):
    """Run one command on the main thread (the RpcServer calls this from pump)."""
    if cmd not in TOOL_NAMES:
        raise _rejected(cmd, args, "unknown command %r" % cmd)
    group = TOOL_GROUPS.get(cmd)
    if group is not None and group not in Settings.groups():
        raise _rejected(cmd, args, "the %s toolset is switched off in Edit > Preferences > MCP > Toolset"
                        % GROUP_NAMES[group])
    started = time.time()
    try:
        result = mcp_tools.call(cmd, args, _Context)
    except mcp_tools.ToolError as exc:
        raise _rejected(cmd, args, error_text(exc))
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        LOG.exception("MCP: command %s failed", cmd)
        _audit("%s %s -> failed: %s" % (cmd, _preview(args), error_text(exc_)))
        raise mcp_tools.ToolError("%s failed: %s" % (cmd, error_text(exc_) or exc_.__class__.__name__))
    _audit("%s %s -> ok (%d ms)" % (cmd, _preview(args), (time.time() - started) * 1000))
    return result


def pump():
    """Run the waiting command, if any. Called on the main thread; never raises."""
    srv = _SERVER
    if srv is None or not srv.running:
        return
    try:
        srv.pump(execute, 1)
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        LOG.exception("MCP: pump failed")


class McpMessageData(c4d.plugins.MessageData):
    def GetTimer(self):
        return TIMER_MS

    def CoreMessage(self, id, bc):
        if id == plugin_ids.MCP_MESSAGE or id == c4d.MSG_TIMER:
            pump()
        return True


def register_message():
    return c4d.plugins.RegisterMessagePlugin(
        plugin_ids.MCP_MESSAGE, compat.native_str("Collab 4D MCP"), 0, McpMessageData())
