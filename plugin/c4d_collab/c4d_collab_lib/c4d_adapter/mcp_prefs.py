"""The "MCP" page of Edit > Preferences, and the settings behind it.

A PreferenceData plugin (Cinema 4D R19 and newer) with a description resource (res/description/mcpprefs.*),
laid out like Maxon's own MCP page of Cinema 4D 2026: Server, Clients, Security and Toolset sections. The
values live in the world container under MCP_CONTAINER, so they persist with the other preferences. Buttons
arrive in `Message` (MSG_DESCRIPTION_COMMAND) and are handled by mcp_actions.

Every method here is wrapped: a mistake must never break the Preferences window or the plugin load.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import binascii
import os

import c4d

from ..core import mcp_clients, mcp_spec
from ..core.compat import is_fatal, string_types
from ..core.log import get_logger
from ..core.mcp_spec import DEFAULT_PORT, DEFAULT_TIMEOUT, MAX_TIMEOUT, MIN_TIMEOUT
from . import compat, plugin_ids

LOG = get_logger("mcp")

# Parameter ids: must equal res/description/mcpprefs.h (tests/adapter/test_mcp.py checks it).
# 1000-1002 and 1005 are the first version of the page and keep their meaning: they are stored in users'
# preferences. 1003, 1004 and 1006 were never stored values and stay unused.
MCP_ENABLE = 1000
MCP_PORT = 1001
MCP_ALLOW_PYTHON = 1002
MCP_TOKEN = 1005
MCP_HOST = 1100
MCP_SHOW_TOKEN = 1101
MCP_COPY_TOKEN = 1102
MCP_REGENERATE_TOKEN = 1103
MCP_TIMEOUT = 1104
MCP_CLIENT = 1105
MCP_CLIENT_STATUS = 1106
MCP_UPDATE_CLIENT = 1107
MCP_REMOVE_CLIENT = 1108
MCP_COPY_CONFIG = 1109
MCP_RESTART_HINT = 1110
MCP_LOG_CALLS = 1111
MCP_CONSOLE_REJECTIONS = 1112
MCP_TOOLSET_BASICS = 1113
MCP_TOOLSET_HIERARCHY = 1114
MCP_TOOLSET_ENTITIES = 1115
MCP_TOOLSET_SYSTEM = 1116
MCP_STATUS = 1117
MCP_CONFIG_FILE = 1118
MCP_TOOLSET_HINT = 1119

RESOURCE_NAME = "mcpprefs"
PAGE_NAME = "MCP"
# Where the page goes in the Preferences tree. 0/0 = a top-level page of its own; if it does not land
# right below "Scheme Colors" in a given version, these two numbers are the only thing to change.
PAGE_PARENT = 0
PAGE_SORT = 0

MIN_PORT, MAX_PORT = 1024, 65535
HOST = "127.0.0.1"

# Saved values: ident -> (c4d data type name, kind, default)
STORED = {
    MCP_ENABLE: ("DTYPE_BOOL", "bool", False),
    MCP_PORT: ("DTYPE_LONG", "int", DEFAULT_PORT),
    MCP_ALLOW_PYTHON: ("DTYPE_BOOL", "bool", False),
    MCP_TOKEN: ("DTYPE_STRING", "str", ""),
    MCP_SHOW_TOKEN: ("DTYPE_BOOL", "bool", False),
    MCP_TIMEOUT: ("DTYPE_REAL", "float", DEFAULT_TIMEOUT),
    MCP_CLIENT: ("DTYPE_LONG", "int", 0),
    MCP_LOG_CALLS: ("DTYPE_BOOL", "bool", True),
    MCP_CONSOLE_REJECTIONS: ("DTYPE_BOOL", "bool", False),
    MCP_TOOLSET_BASICS: ("DTYPE_BOOL", "bool", True),
    MCP_TOOLSET_HIERARCHY: ("DTYPE_BOOL", "bool", True),
    MCP_TOOLSET_ENTITIES: ("DTYPE_BOOL", "bool", True),
    MCP_TOOLSET_SYSTEM: ("DTYPE_BOOL", "bool", True),
}
COMPUTED = (MCP_HOST, MCP_STATUS, MCP_CLIENT_STATUS, MCP_CONFIG_FILE)   # shown, never saved
BUTTONS = (MCP_COPY_TOKEN, MCP_REGENERATE_TOKEN, MCP_UPDATE_CLIENT, MCP_REMOVE_CLIENT, MCP_COPY_CONFIG)
ALWAYS_GHOSTED = (MCP_HOST, MCP_STATUS, MCP_TOKEN, MCP_CLIENT_STATUS, MCP_CONFIG_FILE)   # information, not input
TOOLSET_IDS = {
    MCP_TOOLSET_BASICS: mcp_spec.GROUP_BASICS, MCP_TOOLSET_HIERARCHY: mcp_spec.GROUP_HIERARCHY,
    MCP_TOOLSET_ENTITIES: mcp_spec.GROUP_ENTITIES, MCP_TOOLSET_SYSTEM: mcp_spec.GROUP_SYSTEM,
}
CHANGES_CONFIG_FILE = (MCP_ALLOW_PYTHON, MCP_TIMEOUT, MCP_TOOLSET_BASICS, MCP_TOOLSET_HIERARCHY,
                       MCP_TOOLSET_ENTITIES, MCP_TOOLSET_SYSTEM)   # the AI side reads these from the file


def new_token():
    """A fresh random access token (32 hex characters)."""
    return binascii.hexlify(os.urandom(16)).decode("ascii")


def masked(token):
    """What the Auth Token field shows while "Show Token" is off: stars and the last four characters."""
    return ("*" * 24 + token[-4:]) if token else ""


def _present(values):
    """The ids stored in a container (BaseContainer has no cheap `in` on every version)."""
    try:
        return set(index for index, _value in values)
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        return set(STORED)


class Settings(object):
    """Reads and writes the saved values. Missing or damaged values fall back to the defaults."""

    @staticmethod
    def container(create=True):
        world = c4d.GetWorldContainerInstance()
        if world is None:
            return None
        values = world.GetContainerInstance(plugin_ids.MCP_CONTAINER)
        if values is None and create:
            world.SetContainer(plugin_ids.MCP_CONTAINER, c4d.BaseContainer())
            values = world.GetContainerInstance(plugin_ids.MCP_CONTAINER)
        return values

    @classmethod
    def get(cls, ident):
        _dtype, kind, default = STORED[ident]
        values = cls.container(False)
        if values is None or ident not in _present(values):
            return default
        try:
            if kind == "bool":
                return bool(values.GetBool(ident))
            if kind == "int":
                return int(values.GetInt32(ident))
            if kind == "float":
                return float(values.GetFloat(ident))
            return compat.from_c4d_str(values.GetString(ident))
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            return default

    @classmethod
    def set(cls, ident, value):
        _dtype, kind, _default = STORED[ident]
        values = cls.container(True)
        if values is None:
            return False
        if kind == "bool":
            values.SetBool(ident, bool(value))
        elif kind == "int":
            values.SetInt32(ident, int(value))
        elif kind == "float":
            values.SetFloat(ident, float(value))
        else:
            values.SetString(ident, compat.native_str(value))
        return True

    # Typed views of the values --------------------------------------------------------------

    @classmethod
    def enabled(cls):
        return cls.get(MCP_ENABLE)

    @classmethod
    def port(cls):
        port = cls.get(MCP_PORT)
        return port if MIN_PORT <= port <= MAX_PORT else DEFAULT_PORT

    @classmethod
    def allow_python(cls):
        return cls.get(MCP_ALLOW_PYTHON)

    @classmethod
    def timeout(cls):
        value = cls.get(MCP_TIMEOUT)
        return value if MIN_TIMEOUT <= value <= MAX_TIMEOUT else DEFAULT_TIMEOUT

    @classmethod
    def log_calls(cls):
        return cls.get(MCP_LOG_CALLS)

    @classmethod
    def console_rejections(cls):
        return cls.get(MCP_CONSOLE_REJECTIONS)

    @classmethod
    def show_token(cls):
        return cls.get(MCP_SHOW_TOKEN)

    @classmethod
    def client(cls):
        """The selected client's id (see mcp_clients.CLIENTS)."""
        index = cls.get(MCP_CLIENT)
        return mcp_clients.CLIENTS[index] if 0 <= index < len(mcp_clients.CLIENTS) else mcp_clients.CLIENTS[0]

    @classmethod
    def groups(cls):
        """The enabled toolset groups."""
        return [group for ident, group in sorted(TOOLSET_IDS.items()) if cls.get(ident)]

    @classmethod
    def token(cls):
        """The access token; created (and saved) the first time it is needed."""
        token = cls.get(MCP_TOKEN)
        if not token:
            token = new_token()
            cls.set(MCP_TOKEN, token)
        return token

    @classmethod
    def renew_token(cls):
        token = new_token()
        cls.set(MCP_TOKEN, token)
        return token

    @classmethod
    def set_port(cls, value):
        return cls.set(MCP_PORT, min(MAX_PORT, max(MIN_PORT, int(value))))

    @classmethod
    def set_timeout(cls, value):
        return cls.set(MCP_TIMEOUT, min(MAX_TIMEOUT, max(MIN_TIMEOUT, float(value))))


def _flag(name, default=0):
    return getattr(c4d, name, default)


_PreferenceData = getattr(getattr(c4d, "plugins", None), "PreferenceData", None)


def available():
    """Preference pages exist from R19 on."""
    return _PreferenceData is not None and hasattr(c4d.plugins, "RegisterPreferencePlugin")


if _PreferenceData is not None:
    class McpPreference(_PreferenceData):
        """The page. mcp_service does the work and mcp_actions the buttons; this class only moves values."""

        def Init(self, node, *args):
            try:
                for ident in STORED:
                    self._init_value(ident)
            except BaseException as exc_:
                if is_fatal(exc_):
                    raise
                LOG.exception("MCP page: Init failed")
            return True

        def _init_value(self, ident, description=None):
            """Tell Cinema 4D the type and default of one saved value (fills the world container)."""
            dtype_name, _kind, default = STORED[ident]
            dtype = getattr(c4d, dtype_name, None)
            if dtype is None:
                return
            desc_id = c4d.DescID(c4d.DescLevel(ident, dtype, 0))
            if isinstance(default, string_types):
                default = compat.native_str(default)
            try:
                self.InitPreferenceValue(ident, default, description, desc_id, Settings.container(True))
            except BaseException as exc_:
                if is_fatal(exc_):
                    raise
                LOG.warning("MCP page: InitPreferenceValue failed for %s (%s)", ident, exc_)

        def GetDDescription(self, node, description, flags):
            try:
                if not description.LoadDescription(compat.native_str(RESOURCE_NAME)):
                    return False
                if flags & _flag("DESCFLAGS_DESC_NEEDDEFAULTVALUE"):
                    for ident in STORED:
                        self._init_value(ident, description)
            except BaseException as exc_:
                if is_fatal(exc_):
                    raise
                LOG.exception("MCP page: loading the description failed")
                return False
            loaded = _flag("DESCFLAGS_DESC_LOADED")
            return (True, flags | loaded) if loaded else True

        def GetDParameter(self, node, id, flags):
            try:
                ident = id[0].id
                if ident not in STORED and ident not in COMPUTED:
                    return False
                from . import mcp_service
                if ident == MCP_HOST:
                    value = HOST
                elif ident == MCP_STATUS:
                    value = mcp_service.status_text()
                elif ident == MCP_CLIENT_STATUS:
                    value = mcp_service.client_status(Settings.client())
                elif ident == MCP_CONFIG_FILE:
                    value = mcp_service.client_file(Settings.client())
                elif ident == MCP_TOKEN:
                    token = Settings.token()
                    value = token if Settings.show_token() else masked(token)
                else:
                    value = Settings.get(ident)
                if isinstance(value, string_types):
                    value = compat.native_str(value)
                got = _flag("DESCFLAGS_GET_PARAM_GET")
                return (True, value, flags | got) if got else (True, value)
            except BaseException as exc_:
                if is_fatal(exc_):
                    raise
                LOG.exception("MCP page: reading a value failed")
                return False

        def SetDParameter(self, node, id, data, flags):
            try:
                ident = id[0].id
                if ident not in STORED:
                    if ident not in COMPUTED:
                        return False   # a button or something unknown
                    done = _flag("DESCFLAGS_SET_PARAM_SET")   # a shown value: what was typed is dropped
                    return (True, flags | done) if done else True
                if ident == MCP_TOKEN:
                    pass   # the field shows a masked copy: never store what was typed
                elif ident == MCP_ENABLE:
                    self._set_enabled(bool(data))
                elif ident == MCP_PORT:
                    Settings.set_port(data)
                    from . import mcp_service
                    mcp_service.apply()
                elif ident == MCP_TIMEOUT:
                    Settings.set_timeout(data)
                    self._refresh_config()
                else:
                    Settings.set(ident, data)
                    if ident in CHANGES_CONFIG_FILE:
                        self._refresh_config()
                done = _flag("DESCFLAGS_SET_PARAM_SET")
                return (True, flags | done) if done else True
            except BaseException as exc_:
                if is_fatal(exc_):
                    raise
                LOG.exception("MCP page: saving a value failed")
                return False

        @staticmethod
        def _refresh_config():
            from . import mcp_service
            mcp_service.write_config()

        @staticmethod
        def _set_enabled(want):
            from . import mcp_actions, mcp_service
            if want and not Settings.enabled() and not mcp_actions.confirm_enable():
                return   # the user said no: it stays off
            Settings.set(MCP_ENABLE, want)
            mcp_service.apply()

        def Message(self, node, mid, data):
            try:
                if mid == _flag("MSG_DESCRIPTION_COMMAND", 18):
                    from . import mcp_actions
                    mcp_actions.run(data["id"][0].id)
            except BaseException as exc_:
                if is_fatal(exc_):
                    raise
                LOG.exception("MCP page: a button failed")
            return True

        def GetDEnabling(self, node, id, t_data, flags, itemdesc):
            try:
                ident = id[0].id
                if ident == MCP_ENABLE:
                    return True
                if ident in ALWAYS_GHOSTED or not Settings.enabled():
                    return False
                if ident in (MCP_UPDATE_CLIENT, MCP_REMOVE_CLIENT):
                    from . import mcp_service
                    client = Settings.client()
                    if client == mcp_clients.OTHER:
                        return False
                    if ident == MCP_REMOVE_CLIENT:
                        return mcp_service.client_status(client) == mcp_clients.SET_UP
                    return mcp_service.client_status(client) != mcp_clients.NOT_INSTALLED
                return True
            except BaseException as exc_:
                if is_fatal(exc_):
                    raise
                return True


def register(register_function):
    """Register the page. `register_function` is c4d.plugins.RegisterPreferencePlugin called from the
    .pyp (the resource is looked up for the module that registers). Returns True when the page exists."""
    if not available():
        LOG.info("MCP page: this Cinema 4D has no preference pages")
        return False
    try:
        # Positional arguments: keyword names are not guaranteed on old versions.
        return bool(register_function(plugin_ids.MCP_PREFS, McpPreference, compat.native_str(PAGE_NAME),
                                      compat.native_str(RESOURCE_NAME), PAGE_PARENT, PAGE_SORT))
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        LOG.exception("MCP page: registration failed")
        return False
