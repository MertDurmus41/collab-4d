"""Network client. Runs its socket in a background thread.

The host application (Cinema 4D) never touches the socket: it calls `send_*`
methods from its main thread, and drains received messages with `poll()`
from its main thread. `notify` is called from the network thread whenever new
messages are queued; in Cinema 4D it calls c4d.SpecialEventAdd to wake the
main thread.

Local events pushed into the incoming queue use types starting with "_":
    {"t": "_closed", "reason": ...}
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import itertools
import select
import socket
import threading
import time

from . import protocol
from .compat import error_text, monotonic, queue
from .log import get_logger
from .net import Connection, ConnectionClosed

LOG = get_logger("client")

IDLE = "idle"
CONNECTING = "connecting"
AUTHENTICATING = "authenticating"
ACTIVE = "active"
CLOSED = "closed"

LOCAL_CLOSED = "_closed"


class CollabClient(object):
    def __init__(self, host, port, name, password="", client_info=None, notify=None,
                 ping_interval=3.0, timeout=15.0):
        self.host = host
        self.port = int(port)
        self.name = name
        self.password = password or ""
        self.client_info = client_info or {}
        self.notify = notify
        self.ping_interval = ping_interval
        self.timeout = timeout
        self.state = IDLE
        self.user_id = None
        self.latency_ms = None
        self.close_reason = None
        self._incoming = queue.Queue()
        self._outgoing = queue.Queue()
        self._stop = threading.Event()
        self._thread = None
        self._cid = itertools.count(1)

    # Public API (any thread) --------------------------------------------------------

    def connect(self, connect_timeout=5.0):
        if self._thread is not None:
            raise RuntimeError("client already started")
        self.state = CONNECTING
        self._thread = threading.Thread(target=self._run, args=(connect_timeout,), name="c4d-collab-client")
        self._thread.daemon = True
        self._thread.start()

    def close(self, reason="user left", timeout=2.0):
        if self._thread is None:
            return
        if self.state == ACTIVE:
            self.send(protocol.make(protocol.BYE, reason=reason))
        self._stop.set()
        self._thread.join(timeout)

    @property
    def active(self):
        return self.state == ACTIVE

    def send(self, message):
        self._outgoing.put(message)

    def send_ops(self, ops):
        """Queue a batch of ops. Returns the batch id (cid) echoed back in the ack."""
        cid = next(self._cid)
        self.send(protocol.make(protocol.OPS, cid=cid, ops=ops))
        return cid

    def lock(self, uuids):
        self.send(protocol.make(protocol.LOCK, uuids=list(uuids)))

    def release(self, uuids=None):
        if uuids is None:
            self.send(protocol.make(protocol.RELEASE, all=True))
        else:
            self.send(protocol.make(protocol.RELEASE, uuids=list(uuids)))

    def presence(self, data):
        self.send(protocol.make(protocol.PRESENCE, data=data))

    def poll(self, limit=None):
        """Return queued incoming messages (main thread)."""
        messages = []
        while limit is None or len(messages) < limit:
            try:
                messages.append(self._incoming.get_nowait())
            except queue.Empty:
                break
        return messages

    # Network thread ----------------------------------------------------------------

    def _push(self, message):
        self._incoming.put(message)
        if self.notify is not None:
            try:
                self.notify()
            except Exception:  # pragma: no cover - never let a UI callback kill the socket
                LOG.exception("notify callback failed")

    def _run(self, connect_timeout):
        conn = None
        reason = "closed"
        try:
            sock = socket.create_connection((self.host, self.port), connect_timeout)
            conn = Connection(sock, (self.host, self.port))
            self.state = AUTHENTICATING
            reason = self._loop(conn)
        except ConnectionClosed as exc:
            reason = "connection lost: %s" % error_text(exc)
        except protocol.ProtocolError as exc:
            reason = "protocol error: %s" % error_text(exc)
        except (OSError, socket.error) as exc:
            reason = "cannot connect to %s:%s (%s)" % (self.host, self.port, error_text(exc))
        except Exception as exc:  # pragma: no cover
            LOG.exception("Client loop crashed")
            reason = "internal error: %s" % error_text(exc)
        finally:
            if conn is not None:
                conn.flush_blocking(0.5)
                conn.close()
            self.state = CLOSED
            self.close_reason = reason
            LOG.info("Client closed: %s", reason)
            self._push({"t": LOCAL_CLOSED, "reason": reason})

    def _loop(self, conn):
        last_ping = monotonic()
        while True:
            if self._stop.is_set():
                self._drain_outgoing(conn)
                return "left the session"
            self._drain_outgoing(conn)
            readable, _, _ = select.select([conn], [conn] if conn.wants_write else [], [], 0.02)
            if readable:
                for message in conn.read():
                    stop = self._handle(conn, message)
                    if stop:
                        return stop
            conn.flush()
            now = monotonic()
            if self.state == ACTIVE and now - last_ping >= self.ping_interval:
                conn.send(protocol.make(protocol.PING, ts=time.time()))
                last_ping = now
            if now - conn.last_seen > self.timeout:
                return "server did not respond for %d seconds" % self.timeout

    def _drain_outgoing(self, conn):
        if self.state != ACTIVE:
            return
        while True:
            try:
                conn.send(self._outgoing.get_nowait())
            except queue.Empty:
                return

    def _handle(self, conn, message):
        """Handle protocol-level messages. Returns a reason string to stop, else None."""
        msg_type = message["t"]
        if msg_type == protocol.CHALLENGE:
            conn.send(protocol.make(
                protocol.HELLO, name=self.name, client=self.client_info,
                proof=protocol.auth_proof(self.password, message.get("nonce", ""))))
            return None
        if msg_type == protocol.WELCOME:
            self.user_id = message.get("user_id")
            self.state = ACTIVE
        elif msg_type == protocol.PING:
            conn.send(protocol.make(protocol.PONG, ts=message.get("ts", 0)))
            return None
        elif msg_type == protocol.PONG:
            sent = message.get("ts")
            if isinstance(sent, (int, float)):
                self.latency_ms = max(0.0, (time.time() - sent) * 1000.0)
            return None
        elif msg_type == protocol.ERROR:
            self._push(message)
            return "server error: %s" % message.get("message")
        elif msg_type == protocol.BYE:
            return "server closed the session: %s" % message.get("reason", "")
        self._push(message)
        return None
