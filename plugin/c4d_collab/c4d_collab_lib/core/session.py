"""Session controller: wires server (when hosting), client and mirror together.

DCC-agnostic and main-thread driven: the host application calls `pump()` from
its main thread (in C4D: on SpecialEventAdd and on a timer). Listeners receive
mirror events (see mirror.py) on that same thread, so they may touch the scene.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import collections
import errno
import time

from . import protocol
from .compat import error_text, is_fatal
from .client import CollabClient
from .log import get_logger
from .mirror import SessionMirror
from .server import SessionServer

LOG = get_logger("session")

MIN_PASSWORD_LENGTH = 4
MAX_MESSAGES_PER_PUMP = 2000
OPS_PER_MESSAGE = 5000
LOG_LINES = 300
# EADDRINUSE differs per OS; 10048 is WSAEADDRINUSE on Windows.
ADDRESS_IN_USE = set([errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", 10048), 10048])

IDLE = "idle"
STARTING = "starting"
CONNECTED = "connected"


class SessionError(Exception):
    """User-facing error (bad input, port in use...)."""


class CollabSession(object):
    def __init__(self, client_info=None, notify=None):
        self.client_info = client_info or {}
        self.notify = notify
        self.server = None
        self.client = None
        self.mirror = None
        self.is_host = False
        self.seeds_scene = False  # the server asked us to share our open scene (see SessionServer._claim_seed)
        self.log = collections.deque(maxlen=LOG_LINES)
        self._listeners = []
        self._address = None
        self._closed_dispatched = False
        self.last_kinds = set()  # kinds of the events the last pump() handled
        self._user_names = None  # {user id: name} at the last user list, None before the first

    # Listeners -----------------------------------------------------------------------

    def add_listener(self, callback):
        """callback(session, event) is called on the main thread for every mirror event."""
        self._listeners.append(callback)

    def remove_listener(self, callback):
        if callback in self._listeners:
            self._listeners.remove(callback)

    # State ---------------------------------------------------------------------------

    @property
    def state(self):
        if self.client is None:
            return IDLE
        if self.mirror is not None and self.mirror.synced and self.client.active:
            return CONNECTED
        return STARTING

    @property
    def active(self):
        return self.client is not None

    @property
    def leads(self):
        """True when our open scene is the session's scene: we host it, or we filled an empty server."""
        return self.is_host or self.seeds_scene

    @property
    def address(self):
        return self._address

    def users(self):
        if self.mirror is None:
            return []
        return sorted(self.mirror.users.values(), key=lambda u: u.get("id", ""))

    def note(self, text):
        """Add a line to the user-visible session log."""
        self._log(text)

    def set_presence(self, **fields):
        """Merge fields into our presence (selection, frame, ...) and publish it."""
        if self.client is None:
            return
        self._presence.update(fields)
        self.client.presence(dict(self._presence))

    def send_ops(self, ops):
        """Send local ops (chunked) and register them with the mirror for acks."""
        if self.client is None or self.mirror is None or not ops:
            return
        for chunk in protocol.chunk_ops(ops, OPS_PER_MESSAGE):
            cid = self.client.send_ops(chunk)
            self.mirror.submitted(cid, chunk)

    def _log(self, text):
        line = "%s  %s" % (time.strftime("%H:%M:%S"), text)
        self.log.append(line)
        LOG.info(text)

    # Actions -------------------------------------------------------------------------

    def host(self, name, port=protocol.DEFAULT_PORT, password="", bind="0.0.0.0", session_name=None):
        """Start a server in this process and join it."""
        self._ensure_idle()
        if len(password or "") < MIN_PASSWORD_LENGTH:
            raise SessionError("A password of at least %d characters is required to host." % MIN_PASSWORD_LENGTH)
        port = _check_port(port)
        server = SessionServer(host=bind, port=port, password=password,
                               session_name=session_name or ("%s's session" % name))
        try:
            port = server.start()
        except (OSError, IOError) as exc:
            code = exc.args[0] if exc.args else None
            if code in ADDRESS_IN_USE:
                raise SessionError(
                    "Port %d is already in use, maybe another Cinema 4D is already hosting.\n"
                    "Join that session instead, or choose another port." % port)
            raise SessionError("Cannot open port %d: %s" % (port, error_text(exc)))
        self.server = server
        self.is_host = True
        self._log("Hosting on port %d" % port)
        self._connect("127.0.0.1", port, name, password)

    def join(self, address, port, name, password=""):
        self._ensure_idle()
        address = (address or "").strip()
        if not address:
            raise SessionError("Enter the host address.")
        self.is_host = False
        self._connect(address, _check_port(port), name, password)

    def leave(self, reason="left the session"):
        if self.client is not None and not self._closed_dispatched:
            self._closed_dispatched = True
            self._dispatch(("closed", reason))
        if self.client is not None:
            self.client.close(reason)
        if self.server is not None:
            self.server.stop()
            self._log("Stopped hosting")
        if self.client is not None:
            self._log("Disconnected: %s" % reason)
        self.client = None
        self.server = None
        self.mirror = None
        self.is_host = False
        self.seeds_scene = False
        self._address = None

    def _ensure_idle(self):
        if self.client is not None:
            raise SessionError("Already in a session. Leave it first.")

    def _connect(self, address, port, name, password):
        self.mirror = SessionMirror()
        self._closed_dispatched = False
        self._user_names = None
        self._presence = {}
        self.seeds_scene = False
        self.client = CollabClient(address, port, name or "User", password=password,
                                   client_info=self.client_info, notify=self.notify)
        self._address = "%s:%d" % (address, port)
        self._log("Connecting to %s ..." % self._address)
        self.client.connect()

    # Main-thread pump ------------------------------------------------------------------

    def pump(self):
        """Process queued network messages. Returns the number of events dispatched."""
        if self.client is None:
            return 0
        messages = self.client.poll(MAX_MESSAGES_PER_PUMP)
        self.last_kinds = set()
        count = 0
        closed_reason = None
        for message in messages:
            for event in self.mirror.handle(message):
                count += 1
                self.last_kinds.add(event[0])
                if event[0] == "closed":
                    self._closed_dispatched = True
                    closed_reason = event[1]
                self._dispatch(event)
        if len(messages) == MAX_MESSAGES_PER_PUMP and self.notify is not None:
            self.notify()  # more waiting: ask for another pump instead of blocking the UI
        if closed_reason is not None:
            self.leave(closed_reason)
        return count

    def _dispatch(self, event):
        if event[0] == "welcome":
            self.seeds_scene = bool(event[1].get("seed")) and not self.is_host
        self._describe(event)
        for listener in list(self._listeners):
            try:
                listener(self, event)
            except BaseException as exc_:
                if is_fatal(exc_):
                    raise
                LOG.exception("Session listener failed on %s", event[0])

    def _describe(self, event):
        kind = event[0]
        if kind == "welcome":
            self._log("Joined as %s" % event[1].get("name"))
        elif kind == "snapshot":
            LOG.info("Received scene (%d items)", len(event[1]))   # the file log only: the panel is for people
        elif kind == "users":
            self._describe_users(event[1])
        elif kind == "rejected":
            for op, reason in event[1]:
                LOG.info("Change refused (%s %s): %s", op.get("op"), op.get("uuid"), reason)
        elif kind == "error":
            self._log("Server error: %s" % event[1].get("message"))


    def _describe_users(self, users):
        """Who joined and who left (the Log panel is not a place for anything that repeats)."""
        names = dict((u.get("id"), u.get("name", "?")) for u in users)
        known = self._user_names
        self._user_names = names
        if known is None:
            others = [name for user_id, name in names.items() if user_id != self.mirror.user_id]
            if others:
                self._log("In the session: %s" % ", ".join(sorted(others)))
            return
        for user_id in sorted(names, key=lambda key: str(key)):
            if user_id not in known:
                self._log("%s joined" % names[user_id])
        for user_id in sorted(known, key=lambda key: str(key)):
            if user_id not in names:
                self._log("%s left" % known[user_id])


def _check_port(port):
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise SessionError("Port must be a number.")
    if not 1024 <= port <= 65535:
        raise SessionError("Port must be between 1024 and 65535.")
    return port
