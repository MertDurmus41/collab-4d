"""The Collab 4D panel: host / join / leave, users and log.

Built in code (no .res file) so one layout works from R17 to 2026.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import socket
import time

import c4d
from c4d import gui

from .. import PLUGIN_VERSION
from ..core import protocol
from ..core.compat import is_fatal
from ..core.log import get_logger
from ..core.session import CONNECTED, IDLE, SessionError
from . import bridge, compat, plugin_ids

LOG = get_logger("dialog")

# Widget ids
ID_NAME = 1000
ID_ADDRESS = 1001
ID_PORT = 1002
ID_PASSWORD = 1003
ID_HOST = 1010
ID_JOIN = 1011
ID_LEAVE = 1012
ID_STATUS = 1020
ID_SHARE = 1021
ID_USERS = 1030
ID_LOG = 1031
ID_LOCKS = 1032

REFRESH_MS = 500
PUMP_MS = 20   # while the panel is open it also drives the network pump, scene scan and animation (50 Hz)
ADDRESS_CACHE_SECONDS = 60.0
_address_cache = {"at": None, "value": [], "tailscale": None}
TAILSCALE_PROBE = "100.100.100.100"   # Tailscale's own DNS address: only a Tailscale network routes it
INTERNET_PROBE = "8.8.8.8"
IDLE_HINT = "Friends in other homes: everyone installs Tailscale (free), see README"
PASSWORD_FLAG = 0   # the password is shown as it is typed (the user asked for it)
READONLY_FLAG = getattr(c4d, "DR_MULTILINE_READONLY", 0)
ASYNC_EDITOR_MOVE = getattr(c4d, "EVMSG_ASYNCEDITORMOVE", -1)


def _route_source(target):
    """Our own address on the way to `target`. Connecting a UDP socket sends nothing: it only asks the system which
    interface it would use."""
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect((target, 53))
        return sock.getsockname()[0]
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except BaseException as exc_:
                if is_fatal(exc_):
                    raise


def in_tailscale_range(address):
    """100.64.0.0/10, the block Tailscale gives its machines."""
    parts = (address or "").split(".")
    if len(parts) != 4:
        return False
    try:
        first, second = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return first == 100 and 64 <= second <= 127


def _find_tailscale_address():
    """This machine's Tailscale address, or None. The route to Tailscale's own address leaves through the Tailscale
    interface only when Tailscale is running; without it that route is the normal one to the internet."""
    via = _route_source(TAILSCALE_PROBE)
    if via is None or not in_tailscale_range(via):
        return None
    if via == _route_source(INTERNET_PROBE):
        return None   # a provider that hands the computer a 100.64/10 address itself: not Tailscale
    return via


def _refresh_addresses():
    now = time.time()
    if _address_cache["at"] is not None and now - _address_cache["at"] < ADDRESS_CACHE_SECONDS:
        return
    found = []
    try:
        for address in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not address.startswith("127.") and address not in found:
                found.append(address)
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        pass
    tailscale = _find_tailscale_address()
    _address_cache["at"] = now
    _address_cache["value"] = [a for a in found if a != tailscale]
    _address_cache["tailscale"] = tailscale


def local_addresses():
    """IPv4 addresses people on the same network could use to reach this machine (best effort).

    gethostbyname_ex can take hundreds of milliseconds (DNS) and the panel refreshes twice a second:
    the answer is cached so it never stalls the interface."""
    _refresh_addresses()
    return list(_address_cache["value"])


def tailscale_address():
    """The address friends in other homes join with when everyone runs Tailscale (cached like local_addresses)."""
    _refresh_addresses()
    return _address_cache["tailscale"]


def share_text(port):
    """The line that tells the host what to send the others."""
    lan = local_addresses()
    tailscale = tailscale_address()
    parts = ["Others join with port %d at:" % port]
    if tailscale:
        parts.append("Tailscale %s (any home)" % tailscale)
    if lan:
        parts.append("%s (same network)" % " or ".join(lan))
    if not tailscale and not lan:
        parts.append("your IP address")
    text = "  ".join(parts)
    if not tailscale:
        text += "  -  other homes: Tailscale, see README"
    return text


def load_settings():
    data = c4d.plugins.GetWorldPluginData(plugin_ids.SETTINGS)
    if data is None:
        return {"name": "", "address": "", "port": protocol.DEFAULT_PORT}
    return {
        "name": compat.from_c4d_str(data.GetString(ID_NAME)),
        "address": compat.from_c4d_str(data.GetString(ID_ADDRESS)),
        "port": data.GetInt32(ID_PORT) or protocol.DEFAULT_PORT,
    }


def save_settings(name, address, port):
    """Never stores the password."""
    data = c4d.BaseContainer()
    data.SetString(ID_NAME, compat.native_str(name))
    data.SetString(ID_ADDRESS, compat.native_str(address))
    data.SetInt32(ID_PORT, int(port))
    c4d.plugins.SetWorldPluginData(plugin_ids.SETTINGS, data, False)


class CollabDialog(gui.GeDialog):
    def CreateLayout(self):
        self.SetTitle(compat.native_str("Collab 4D %s" % PLUGIN_VERSION))
        self.GroupBorderSpace(8, 8, 8, 8)

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 2, 0, compat.native_str(""), 0)
        self.GroupSpace(6, 4)
        self._label("Your name")
        self.AddEditText(ID_NAME, c4d.BFH_SCALEFIT, 200, 0)
        self._label("Host address")
        self.AddEditText(ID_ADDRESS, c4d.BFH_SCALEFIT, 200, 0)
        self._label("Port")
        self.AddEditNumberArrows(ID_PORT, c4d.BFH_LEFT, 80, 0)
        self._label("Password")
        self.AddEditText(ID_PASSWORD, c4d.BFH_SCALEFIT, 200, 0, PASSWORD_FLAG)
        self.GroupEnd()

        self.GroupBegin(0, c4d.BFH_SCALEFIT, 3, 0, compat.native_str(""), 0)
        self.AddButton(ID_HOST, c4d.BFH_SCALEFIT, 0, 0, compat.native_str("Host session"))
        self.AddButton(ID_JOIN, c4d.BFH_SCALEFIT, 0, 0, compat.native_str("Join"))
        self.AddButton(ID_LEAVE, c4d.BFH_SCALEFIT, 0, 0, compat.native_str("Leave"))
        self.GroupEnd()

        self.AddStaticText(ID_STATUS, c4d.BFH_SCALEFIT, 0, 0, compat.native_str(""), 0)
        self.AddStaticText(ID_SHARE, c4d.BFH_SCALEFIT, 0, 0, compat.native_str(""), 0)
        self._label("Users")
        self.AddMultiLineEditText(ID_USERS, c4d.BFH_SCALEFIT, 0, 60, READONLY_FLAG)
        self._label("Being edited by others")
        self.AddMultiLineEditText(ID_LOCKS, c4d.BFH_SCALEFIT, 0, 50, READONLY_FLAG)
        self._label("Log")
        self.AddMultiLineEditText(ID_LOG, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 0, 120, READONLY_FLAG)
        return True

    def _label(self, text):
        self.AddStaticText(0, c4d.BFH_LEFT, 0, 0, compat.native_str(text), 0)

    def _get(self, widget):
        return compat.from_c4d_str(self.GetString(widget)).strip()

    def _set(self, widget, text):
        self.SetString(widget, compat.native_str(text))

    def _show(self, widget, text):
        """Display-only text: written only when it changed (rewriting identical text every refresh
        repaints the widget and can make the panel flicker)."""
        shown = getattr(self, "_shown", None)
        if shown is None:
            shown = self._shown = {}
        if shown.get(widget) != text:
            shown[widget] = text
            self.SetString(widget, compat.native_str(text))

    def InitValues(self):
        settings = load_settings()
        self._set(ID_NAME, settings["name"])
        self._set(ID_ADDRESS, settings["address"])
        self.SetInt32(ID_PORT, settings["port"], 1024, 65535)
        self.SetTimer(PUMP_MS)
        try:
            self.refresh()
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            self._guard("InitValues")
        return True

    def _guard(self, where):
        """Log an error from a panel callback to the file and the panel's own log instead of the
        Python console (where nobody sees it). Never lets an exception escape into Cinema 4D."""
        LOG.exception("Panel error in %s", where)
        try:
            bridge.session().note("Internal error in %s (see c4d_collab.log)" % where)
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            pass

    def Timer(self, msg):
        try:
            bridge.timer_pump()  # backup for C4D's own message timer, which may not be running
            self._ticks = getattr(self, "_ticks", 0) + 1
            if self._ticks % (REFRESH_MS // PUMP_MS) == 0:
                self.refresh()
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            self._guard("Timer")

    def CoreMessage(self, id, msg):
        try:
            if id == plugin_ids.MESSAGE_PLUGIN:
                self.refresh()
            elif id == ASYNC_EDITOR_MOVE:
                bridge.tick()  # live transform while the user drags in the viewport
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            self._guard("CoreMessage")
        return gui.GeDialog.CoreMessage(self, id, msg)

    def Command(self, id, msg):
        try:
            return self._command(id)
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            self._guard("Command")
            gui.MessageDialog(compat.native_str("Collab 4D hit an internal error. Details: c4d_collab.log"))
            return True

    def _command(self, id):
        current = bridge.session()
        name = self._get(ID_NAME) or "User"
        address = self._get(ID_ADDRESS)
        port = self.GetInt32(ID_PORT)
        password = self._get(ID_PASSWORD)
        try:
            if id == ID_HOST:
                current.host(name, port, password)
                save_settings(name, address, port)
            elif id == ID_JOIN:
                current.join(address, port, name, password)
                save_settings(name, address, port)
            elif id == ID_LEAVE:
                current.leave()
            else:
                return True
        except SessionError as exc:
            gui.MessageDialog(compat.native_str(exc.args[0] if exc.args else "Error"))
        # Clear the password field once used, so it does not linger on screen.
        if id in (ID_HOST, ID_JOIN):
            self._set(ID_PASSWORD, "")
        bridge.wake()
        self.refresh()
        return True

    def refresh(self):
        current = bridge.session()
        state = current.state
        if state == IDLE:
            status = "Not connected"
        elif state == CONNECTED:
            role = "Hosting" if current.is_host else "Connected to %s%s" % (
                current.address, " (you shared your scene)" if current.seeds_scene else "")
            latency = current.client.latency_ms if current.client else None
            status = "%s - %d user(s)%s" % (role, len(current.users()),
                                           "" if latency is None else " - %d ms" % latency)
        else:
            status = "Connecting to %s ..." % current.address
        self._show(ID_STATUS, status)

        share = ""
        if current.is_host and current.server is not None:
            share = share_text(current.server.port)
        elif state == IDLE:
            share = IDLE_HINT
        self._show(ID_SHARE, share)

        lines = []
        presence = current.mirror.presence if current.mirror is not None else {}
        for user in current.users():
            client = user.get("client") or {}
            frame = (presence.get(user.get("id")) or {}).get("frame")
            lines.append("%s [%s]  (C4D %s, Python %s)%s" % (
                user.get("name"), user.get("color_name", "?"), client.get("app_version", "?"), client.get("python", "?"),
                "" if frame is None else "  -  frame %s" % frame))
        self._show(ID_USERS, "\n".join(lines))

        locks = []
        mirror = current.mirror
        if mirror is not None:
            for uuid, owner in sorted(mirror.locked_by_others().items()):
                node = mirror.scene.get(uuid)
                locks.append("%s  -  %s" % (node.name if node else uuid, mirror.user_name(owner)))
        self._show(ID_LOCKS, "\n".join(locks))
        self._show(ID_LOG, "\n".join(reversed(list(current.log))))

        self.Enable(ID_HOST, state == IDLE)
        self.Enable(ID_JOIN, state == IDLE)
        self.Enable(ID_LEAVE, state != IDLE)


class OpenDialogCommand(c4d.plugins.CommandData):
    dialog = None

    def _dialog(self):
        if self.dialog is None:
            self.dialog = CollabDialog()
        return self.dialog

    def Execute(self, doc):
        return self._dialog().Open(c4d.DLG_TYPE_ASYNC, plugin_ids.COMMAND_PLUGIN, -1, -1, 420, 520)

    def RestoreLayout(self, sec_ref):
        return self._dialog().Restore(plugin_ids.COMMAND_PLUGIN, sec_ref)


def register():
    return c4d.plugins.RegisterCommandPlugin(
        plugin_ids.COMMAND_PLUGIN, compat.native_str("Collab 4D"), 0, None,
        compat.native_str("Real-time collaboration: host or join a session"), OpenDialogCommand())
