"""Setting up AI apps (MCP clients) to use the Cinema 4D MCP server: where their config lives, what to write,
and how to do it safely. No c4d import, Python 2.7 and 3.x, standard library only.

Rules:
  * a file is only changed after a backup copy (`<name>.bak-<time>`) and written through a temp file
  * a config that cannot be parsed is never overwritten
  * only our own entry (named "cinema4d") is added, replaced or removed, everything else stays as it is
  * Codex's TOML is edited inside a marked block, so it can be replaced and removed exactly
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import glob
import io
import json
import os
import re
import sys
import time

from .compat import error_text, is_fatal, string_types, to_text

SERVER_KEY = "cinema4d"

CLAUDE_DESKTOP, CLAUDE_CODE, CODEX, CURSOR, OTHER = "claude_desktop", "claude_code", "codex", "cursor", "other"
CLIENTS = [CLAUDE_DESKTOP, CLAUDE_CODE, CODEX, CURSOR, OTHER]    # the order of the dropdown (ids 0..4)
NAMES = {CLAUDE_DESKTOP: "Claude Desktop", CLAUDE_CODE: "Claude Code", CODEX: "Codex", CURSOR: "Cursor", OTHER: "Other"}

SET_UP, NOT_SET_UP, NOT_INSTALLED, MANUAL = "Set up", "Not set up", "Not installed", "Set up by hand"

TOML_BEGIN = "# >>> C4D Collab MCP (managed by Cinema 4D, remove with Edit > Preferences > MCP) >>>"
TOML_END = "# <<< C4D Collab MCP <<<"


# Python ----------------------------------------------------------------------------------------

def _is_store_stub(path):
    """Windows ships python.exe / python3.exe stubs in WindowsApps that open the Microsoft Store."""
    return "windowsapps" in path.replace("/", "\\").lower()


def _path_search(names):
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        for name in names:
            candidate = os.path.join(folder.strip('"'), name)
            if folder and os.path.isfile(candidate) and not _is_store_stub(candidate):
                return candidate
    return None


def find_python():
    """(command, leading arguments) that start a normal Python for the stdio bridge.

    A real installation by absolute path comes first: AI apps are started from the desktop and often do not
    see the shell's PATH. The `py` launcher and plain `python3` are last resorts."""
    if sys.platform == "win32":
        found = _path_search(["python.exe", "python3.exe"])
        if found:
            return found, []
        patterns = [
            os.path.expandvars(r"%LOCALAPPDATA%\Python\pythoncore-*\python.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python3*\python.exe"),
            os.path.expandvars(r"%ProgramFiles%\Python3*\python.exe"),
            r"C:\Python3*\python.exe",
        ]
        installed = []
        for pattern in patterns:
            installed.extend(glob.glob(pattern))
        if installed:
            return sorted(installed, key=_version_key)[-1], []
        launcher = _path_search(["py.exe"]) or os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\py.exe")
        if launcher and os.path.isfile(launcher):
            return launcher, ["-3"]
        return "python", []
    found = _path_search(["python3", "python"])
    if found:
        return found, []
    for candidate in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"):
        if os.path.isfile(candidate):
            return candidate, []
    return "python3", []


def _version_key(path):
    numbers = re.findall(r"\d+", path)
    return [int(n) for n in numbers]


def server_entry(bridge, config_path):
    """What an AI app needs to start the stdio bridge."""
    command, lead = find_python()
    return {"command": command, "args": lead + [bridge, "--config", config_path]}


# Where each app keeps its config -----------------------------------------------------------------

def _home():
    return os.path.expanduser("~")


def _claude_desktop_files():
    """Candidate config files, most likely first.

    Claude Desktop 4.x keeps all of its data (settings, sign-in, this config) in %LOCALAPPDATA%\\Claude-Data\\<id>
    and does not read the older locations (found on a real machine: an entry written to the Store package's
    Roaming folder was never loaded). Older builds use the Store package's Roaming folder (LocalCache; they do
    not see %APPDATA%\\Claude) or %APPDATA%\\Claude."""
    files = []
    if sys.platform == "win32":
        for data in sorted(glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Claude-Data\claude_*"))):
            files.append(os.path.join(data, "claude_desktop_config.json"))
        for package in sorted(glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Packages\Claude_*"))):
            files.append(os.path.join(package, "LocalCache", "Roaming", "Claude", "claude_desktop_config.json"))
        files.append(os.path.expandvars(r"%APPDATA%\Claude\claude_desktop_config.json"))
    elif sys.platform == "darwin":
        files.append(os.path.join(_home(), "Library", "Application Support", "Claude", "claude_desktop_config.json"))
    else:
        files.append(os.path.join(_home(), ".config", "Claude", "claude_desktop_config.json"))
    return files


def _claude_desktop_installed(path):
    """The app is there when its config folder exists, or (Store build) its package folder does."""
    if os.path.isdir(os.path.dirname(path)):
        return True
    marker = os.sep + "LocalCache" + os.sep
    if marker in path:
        return os.path.isdir(path.split(marker)[0])
    return False


def config_file(client):
    """The file to edit for `client` (it may not exist yet), or None when there is none to edit."""
    if client == CLAUDE_DESKTOP:
        files = _claude_desktop_files()
        for path in files:
            if os.path.isfile(path):
                return path
        for path in files:
            if _claude_desktop_installed(path):
                return path
        return files[0] if files else None
    if client == CURSOR:
        return os.path.join(_home(), ".cursor", "mcp.json")
    if client == CODEX:
        return os.path.join(os.environ.get("CODEX_HOME") or os.path.join(_home(), ".codex"), "config.toml")
    if client == CLAUDE_CODE:
        return os.path.join(_home(), ".claude.json")
    return None


def is_installed(client):
    path = config_file(client)
    if client == OTHER:
        return True
    if path is None:
        return False
    if client == CLAUDE_DESKTOP:
        return _claude_desktop_installed(path) or os.path.isfile(path)
    if client == CLAUDE_CODE:
        return os.path.isfile(path) or os.path.isdir(os.path.join(_home(), ".claude"))
    return os.path.isdir(os.path.dirname(path))


# Reading and writing -------------------------------------------------------------------------------

def _read_text(path):
    with io.open(path, "r", encoding="utf-8-sig") as handle:
        return handle.read()


def _write_atomic(path, text):
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    temp = path + ".tmp"
    with io.open(temp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(to_text(text))
    if os.path.exists(path):
        os.remove(path)   # os.rename does not replace an existing file on Windows with Python 2
    os.rename(temp, path)


def _backup(path):
    """A copy next to the file. Returns its path, or None when there was nothing to copy."""
    if not os.path.isfile(path):
        return None
    backup = "%s.bak-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
    counter = 1
    while os.path.exists(backup):
        counter += 1
        backup = "%s.bak-%s-%d" % (path, time.strftime("%Y%m%d-%H%M%S"), counter)
    with io.open(path, "rb") as source, io.open(backup, "wb") as target:
        target.write(source.read())
    return backup


def _dumps(data):
    text = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False)
    return to_text(text) + "\n"


def _load_json(path):
    """(data, error): the parsed object (empty when the file is missing or empty), or an error text."""
    if not os.path.isfile(path):
        return {}, None
    try:
        text = _read_text(path)
    except (IOError, OSError, UnicodeError) as exc:
        return None, "Could not read %s (%s)." % (path, error_text(exc))
    if not text.strip():
        return {}, None
    try:
        data = json.loads(text)
    except ValueError as exc:
        return None, ("%s is not valid JSON (%s). It was not changed; fix or remove it and try again."
                      % (path, error_text(exc)))
    if not isinstance(data, dict):
        return None, "%s does not hold a JSON object. It was not changed." % path
    return data, None


def _servers(data, create=False):
    servers = data.get("mcpServers")
    if isinstance(servers, dict):
        return servers
    if create and servers is None:
        data["mcpServers"] = {}
        return data["mcpServers"]
    return None if servers is not None else {}


def _has_block(text):
    return TOML_BEGIN in text and TOML_END in text


def _toml_string(value):
    return to_text(json.dumps(value, ensure_ascii=True))   # JSON strings are valid TOML basic strings


def toml_block(entry):
    args = ", ".join(_toml_string(a) for a in entry["args"])
    return "\n".join([TOML_BEGIN, "[mcp_servers.%s]" % SERVER_KEY, "command = %s" % _toml_string(entry["command"]),
                      "args = [%s]" % args, TOML_END]) + "\n"


def _strip_block(text):
    start, end = text.find(TOML_BEGIN), text.find(TOML_END)
    if start < 0 or end < start:
        return text
    end += len(TOML_END)
    if text[end:end + 2] == "\r\n":
        end += 2
    elif text[end:end + 1] == "\n":
        end += 1
    return text[:start].rstrip("\r\n") + ("\n" if text[:start].strip() else "") + text[end:].lstrip("\r\n")


# Status, update, remove ------------------------------------------------------------------------------

def status(client):
    """One of SET_UP, NOT_SET_UP, NOT_INSTALLED, MANUAL (the app is set up by hand: Other)."""
    if client == OTHER:
        return MANUAL
    if not is_installed(client):
        return NOT_INSTALLED
    path = config_file(client)
    try:
        if client == CODEX:
            return SET_UP if os.path.isfile(path) and _has_block(_read_text(path)) else NOT_SET_UP
        data, error = _load_json(path)
        servers = (data or {}).get("mcpServers") if not error else None
        return SET_UP if isinstance(servers, dict) and SERVER_KEY in servers else NOT_SET_UP
    except BaseException as exc:
        if is_fatal(exc):
            raise
        return NOT_SET_UP


def update(client, entry):
    """Add or replace our entry in the app's config. Returns (ok, message, backup path or None)."""
    if client == OTHER:
        return False, "Use Copy Config for this client.", None
    if client == CLAUDE_CODE:
        return False, "Claude Code is set up from a terminal, see claude_code_command().", None
    if not is_installed(client):
        return False, "%s does not seem to be installed (no config folder found)." % NAMES[client], None
    path = config_file(client)
    try:
        if client == CODEX:
            text = _read_text(path) if os.path.isfile(path) else ""
            if _has_block(text):
                text = _strip_block(text)
            elif re.search(r"^\s*\[mcp_servers\.%s\]" % SERVER_KEY, text, re.M):
                return False, ("%s already has a [mcp_servers.%s] entry that was not made by this page. "
                               "It was not changed." % (path, SERVER_KEY)), None
            new = (text.rstrip("\r\n") + "\n\n" if text.strip() else "") + toml_block(entry)
            backup = _backup(path)
            _write_atomic(path, new)
            return True, "Codex configuration updated.", backup
        data, error = _load_json(path)
        if error:
            return False, error, None
        servers = _servers(data, create=True)
        if servers is None:
            return False, "%s has an \"mcpServers\" entry that is not an object. It was not changed." % path, None
        servers[SERVER_KEY] = entry
        backup = _backup(path)
        _write_atomic(path, _dumps(data))
        return True, "%s configuration updated." % NAMES[client], backup
    except BaseException as exc:
        if is_fatal(exc):
            raise
        return False, "Could not update %s (%s)." % (path, error_text(exc)), None


def remove(client):
    """Remove our entry. Returns (ok, message, backup path or None)."""
    if client in (OTHER, CLAUDE_CODE):
        return False, "Remove it from the client yourself (see the command that was copied).", None
    if not is_installed(client):
        return False, "%s does not seem to be installed." % NAMES[client], None
    path = config_file(client)
    try:
        if client == CODEX:
            if not os.path.isfile(path) or not _has_block(_read_text(path)):
                return False, "Codex has no Cinema 4D entry made by this page.", None
            backup = _backup(path)
            _write_atomic(path, _strip_block(_read_text(path)))
            return True, "Removed from Codex.", backup
        data, error = _load_json(path)
        if error:
            return False, error, None
        servers = _servers(data)
        if not servers or SERVER_KEY not in servers:
            return False, "%s has no Cinema 4D entry." % NAMES[client], None
        del servers[SERVER_KEY]
        backup = _backup(path)
        _write_atomic(path, _dumps(data))
        return True, "Removed from %s." % NAMES[client], backup
    except BaseException as exc:
        if is_fatal(exc):
            raise
        return False, "Could not change %s (%s)." % (path, error_text(exc)), None


# Text to copy ---------------------------------------------------------------------------------------------

def _quote(argument):
    return '"%s"' % argument.replace('"', '\\"') if (" " in argument or not argument) else argument


def claude_code_command(entry):
    return "claude mcp add --scope user %s -- %s" % (
        SERVER_KEY, " ".join(_quote(part) for part in [entry["command"]] + list(entry["args"])))


CLAUDE_CODE_REMOVE = "claude mcp remove --scope user %s" % SERVER_KEY


def json_snippet(entry):
    return _dumps({"mcpServers": {SERVER_KEY: entry}})


def copy_text(client, entry):
    """What "Copy Config" puts on the clipboard for `client`."""
    if client == CLAUDE_CODE:
        return claude_code_command(entry)
    if client == CODEX:
        return toml_block(entry)
    return json_snippet(entry)
