"""Session server. Runs in a background thread inside the host's Cinema 4D,
or standalone via `server/run_server.py` / Docker.

The server is authoritative: it validates every op against the scene model and
the lock table, assigns a global sequence number and relays accepted ops to the
other participants. All state is touched only from the server thread.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import select
import unicodedata
import socket
import threading

from . import protocol
from .compat import error_text, integer_types, monotonic, string_types, to_bytes
from .locks import LockTable
from .log import get_logger
from .model import OpError, SceneState
from .net import Connection, ConnectionClosed

LOG = get_logger("server")

SNAPSHOT_CHUNK_SIZE = 500
MAX_OPS_PER_MESSAGE = 20000
MAX_UUIDS_PER_MESSAGE = 20000
MAX_PRESENCE_BYTES = 16 * 1024
MAX_NAME_LENGTH = 64
AUTH_TIMEOUT = 10.0

# Security limits ------------------------------------------------------------------
PRE_AUTH_MAX_FRAME = 64 * 1024            # unauthenticated peers cannot make us buffer more
MAX_PENDING_OUTPUT = 128 * 1024 * 1024    # a peer that does not read is dropped
RATE_LIMIT_PER_SECOND = 1000              # messages per second per peer (token bucket)
RATE_LIMIT_BURST = 3000
AUTH_FAIL_LIMIT = 5                       # failed logins per IP inside the window ...
AUTH_FAIL_WINDOW = 60.0
AUTH_BAN_SECONDS = 60.0                   # ... ban that IP for this long

USER_COLORS = [
    [0.95, 0.35, 0.30], [0.25, 0.65, 0.95], [0.40, 0.80, 0.35], [0.95, 0.75, 0.20],
    [0.75, 0.45, 0.95], [0.20, 0.85, 0.80], [0.95, 0.50, 0.75], [0.60, 0.60, 0.60],
]


def sanitize_name(name, default="User"):
    """Strip control/format characters so a name cannot break UIs or logs."""
    if not isinstance(name, string_types):
        return default
    cleaned = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    cleaned = " ".join(cleaned.split())[:MAX_NAME_LENGTH]
    return cleaned or default


USER_COLOR_NAMES = ["Red", "Blue", "Green", "Yellow", "Purple", "Cyan", "Pink", "Gray"]


class _Peer(object):
    def __init__(self, conn):
        self.conn = conn
        self.nonce = protocol.new_nonce()
        self.user_id = None
        self.name = None
        self.color = None
        self.client = {}
        self.presence = {}
        self.connected_at = monotonic()
        self.tokens = float(RATE_LIMIT_BURST)
        self.tokens_at = self.connected_at

    def take_token(self):
        now = monotonic()
        self.tokens = min(float(RATE_LIMIT_BURST), self.tokens + (now - self.tokens_at) * RATE_LIMIT_PER_SECOND)
        self.tokens_at = now
        if self.tokens < 1.0:
            return False
        self.tokens -= 1.0
        return True

    @property
    def authenticated(self):
        return self.user_id is not None

    def public(self):
        index = USER_COLORS.index(self.color) if self.color in USER_COLORS else 0
        return {"id": self.user_id, "name": self.name, "color": self.color,
                "color_name": USER_COLOR_NAMES[index], "client": self.client}


class SessionServer(object):
    def __init__(self, host="0.0.0.0", port=protocol.DEFAULT_PORT, password="",
                 session_name="C4D Collab", max_clients=16, idle_timeout=20.0, first_joiner_seeds=False):
        self.host = host
        self.port = port
        self.password = password or ""
        self.session_name = session_name
        self.max_clients = max_clients
        self.idle_timeout = idle_timeout
        # Only a server without Cinema 4D (run_server.py, Docker) lets the first person to join fill it. Inside a
        # hosting Cinema 4D the host's own client seeds; a guest whose hello happened to arrive first must not.
        self.first_joiner_seeds = first_joiner_seeds
        self.scene = SceneState()
        self.locks = LockTable()
        self._peers = {}
        self._listener = None
        self._thread = None
        self._stop = threading.Event()
        self._state_lock = threading.Lock()
        self._next_user = 1
        self._auth_failures = {}  # ip -> [timestamps]
        self._banned = {}         # ip -> unban time
        self._seeder = None       # user id of the one who shares their scene into an empty session

    # Lifecycle -------------------------------------------------------------------

    def start(self):
        """Bind and start serving in a daemon thread. Returns the bound port."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # On Windows SO_REUSEADDR would let two servers share a port; use exclusive bind instead.
            exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if exclusive is not None:
                listener.setsockopt(socket.SOL_SOCKET, exclusive, 1)
            else:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.host, self.port))
            listener.listen(32)
            listener.setblocking(False)
        except Exception:
            listener.close()
            raise
        self._listener = listener
        self.port = listener.getsockname()[1]
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="c4d-collab-server")
        self._thread.daemon = True
        self._thread.start()
        LOG.info("Session '%s' listening on %s:%d", self.session_name, self.host, self.port)
        return self.port

    def stop(self, timeout=3.0):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def users(self):
        """Thread-safe list of connected users."""
        with self._state_lock:
            return [p.public() for p in self._peers.values() if p.authenticated]

    # Main loop -------------------------------------------------------------------

    def _run(self):
        try:
            while not self._stop.is_set():
                self._tick()
        except Exception:  # pragma: no cover - last resort, keep the host's C4D alive
            LOG.exception("Server loop crashed")
        finally:
            for peer in list(self._peers.values()):
                peer.conn.send(protocol.make(protocol.BYE, reason="server stopped"))
                peer.conn.flush_blocking(0.5)
                peer.conn.close()
            self._peers.clear()
            try:
                self._listener.close()
            except (OSError, socket.error):  # pragma: no cover
                pass

    def _tick(self):
        conns = [p.conn for p in self._peers.values()]
        readable, writable, _ = select.select(
            [self._listener] + conns, [c for c in conns if c.wants_write], [], 0.05)
        with self._state_lock:
            for item in readable:
                if item is self._listener:
                    self._accept()
                else:
                    self._read(item)
            for conn in writable:
                peer = self._peers.get(conn) if not conn.closed else None
                if peer is None:
                    continue
                try:
                    conn.flush()
                except ConnectionClosed as exc:
                    self._drop(peer, error_text(exc))
            self._check_timeouts()
            # Flush right away what handlers queued, so latency does not depend on select.
            for peer in list(self._peers.values()):
                if len(peer.conn.outbuf) > MAX_PENDING_OUTPUT:
                    self._drop(peer, "client is not reading (%s)" % protocol.ERR_SLOW)
                    continue
                if peer.conn.wants_write:
                    try:
                        peer.conn.flush()
                    except ConnectionClosed as exc:
                        self._drop(peer, error_text(exc))

    def _accept(self):
        while True:
            try:
                sock, address = self._listener.accept()
            except (OSError, socket.error):
                return
            conn = Connection(sock, address)
            conn.decoder.max_size = PRE_AUTH_MAX_FRAME
            if self._is_banned(address[0]):
                conn.send(protocol.make(protocol.ERROR, code=protocol.ERR_BANNED,
                                        message="too many failed logins, try again later"))
                conn.flush_blocking(0.5)
                conn.close()
                continue
            if len(self._peers) >= self.max_clients:
                conn.send(protocol.make(protocol.ERROR, code=protocol.ERR_FULL, message="session is full"))
                conn.flush_blocking(0.5)
                conn.close()
                continue
            peer = _Peer(conn)
            self._peers[conn] = peer
            conn.send(protocol.make(protocol.CHALLENGE, nonce=peer.nonce, session=self.session_name,
                                    auth=bool(self.password)))
            LOG.info("Connection from %s", address)

    def _read(self, conn):
        peer = self._peers.get(conn)
        if peer is None or conn.closed:
            return
        try:
            messages = conn.read()
        except ConnectionClosed as exc:
            self._drop(peer, error_text(exc))
            return
        except protocol.ProtocolError as exc:
            self._fail(peer, protocol.ERR_PROTOCOL, error_text(exc))
            return
        for message in messages:
            if peer.conn.closed:
                return
            if not peer.take_token():
                self._fail(peer, protocol.ERR_RATE, "too many messages")
                return
            try:
                self._dispatch(peer, message)
            except protocol.ProtocolError as exc:
                self._fail(peer, protocol.ERR_PROTOCOL, error_text(exc))
                return

    def _check_timeouts(self):
        now = monotonic()
        for peer in list(self._peers.values()):
            if not peer.authenticated and now - peer.connected_at > AUTH_TIMEOUT:
                self._fail(peer, protocol.ERR_TIMEOUT, "authentication timed out")
            elif now - peer.conn.last_seen > self.idle_timeout:
                self._drop(peer, "idle timeout")

    # Brute-force protection ----------------------------------------------------------

    def _record_auth_failure(self, ip):
        if ip is None:
            return
        now = monotonic()
        recent = [t for t in self._auth_failures.get(ip, []) if now - t < AUTH_FAIL_WINDOW]
        recent.append(now)
        self._auth_failures[ip] = recent
        if len(recent) >= AUTH_FAIL_LIMIT:
            LOG.warning("Banning %s for %ds after %d failed logins", ip, AUTH_BAN_SECONDS, len(recent))
            self._banned[ip] = now + AUTH_BAN_SECONDS
            del self._auth_failures[ip]

    def _is_banned(self, ip):
        until = self._banned.get(ip)
        if until is None:
            return False
        if monotonic() >= until:
            del self._banned[ip]
            return False
        return True

    # Peer management -------------------------------------------------------------

    def _fail(self, peer, code, message):
        LOG.warning("Closing %s: %s (%s)", peer.name or peer.conn.address, message, code)
        peer.conn.send(protocol.make(protocol.ERROR, code=code, message=message))
        peer.conn.flush_blocking(0.5)
        self._drop(peer, message)

    def _drop(self, peer, reason):
        if self._peers.pop(peer.conn, None) is None and peer.conn.closed:
            return
        peer.conn.close()
        if peer.authenticated:
            LOG.info("User %s (%s) left: %s", peer.name, peer.user_id, reason)
            released = self.locks.release_all(peer.user_id)
            if released:
                self._broadcast(protocol.make(protocol.LOCKS, changes=dict((u, None) for u in released)))
            self._broadcast_users()
            self._reset_when_empty()

    def _reset_when_empty(self):
        """A server without Cinema 4D runs for days: once the last person has left, the next one to join brings their
        own scene again instead of finding yesterday's. Nothing is lost: everyone keeps their copy in their Cinema 4D."""
        if not self.first_joiner_seeds or any(p.authenticated for p in self._peers.values()):
            return
        if self.scene.seq == 0 and self._seeder is None:
            return
        LOG.info("Everyone left: the session is empty again, the next person to join shares their scene")
        self.scene = SceneState()
        self.locks = LockTable()
        self._seeder = None

    def _send(self, peer, message):
        try:
            peer.conn.send(message)
        except protocol.ProtocolError as exc:  # pragma: no cover - message too large
            LOG.error("Cannot send to %s: %s", peer.name, exc)

    def _broadcast(self, message, exclude=None):
        frame = None
        for peer in self._peers.values():
            if not peer.authenticated or peer is exclude or peer.conn.closed:
                continue
            if frame is None:
                frame = protocol.encode(message)
            peer.conn.outbuf.extend(frame)

    def _broadcast_users(self):
        self._broadcast(protocol.make(protocol.USERS, users=[
            p.public() for p in self._peers.values() if p.authenticated]))

    # Handlers ----------------------------------------------------------------------

    def _dispatch(self, peer, message):
        msg_type = message["t"]
        if not peer.authenticated:
            if msg_type != protocol.HELLO:
                raise protocol.ProtocolError("expected hello, got %s" % msg_type)
            self._on_hello(peer, message)
            return
        if msg_type not in protocol.CLIENT_MESSAGES:
            raise protocol.ProtocolError("unexpected message %s" % msg_type)
        handler = getattr(self, "_on_" + msg_type)
        handler(peer, message)

    def _on_hello(self, peer, message):
        if message["v"] != protocol.PROTOCOL_VERSION:
            self._fail(peer, protocol.ERR_VERSION, "protocol version %s is not supported (server: %s)"
                       % (message["v"], protocol.PROTOCOL_VERSION))
            return
        if self.password and not protocol.check_proof(self.password, peer.nonce, message.get("proof")):
            self._record_auth_failure(peer.conn.address[0] if peer.conn.address else None)
            self._fail(peer, protocol.ERR_AUTH, "wrong password")
            return
        name = sanitize_name(message.get("name"))
        peer.conn.decoder.max_size = protocol.MAX_MESSAGE_SIZE
        taken = set(p.name for p in self._peers.values() if p.authenticated)
        unique = name
        index = 2
        while unique in taken:
            unique = "%s (%d)" % (name, index)
            index += 1
        client = message.get("client")
        peer.client = client if isinstance(client, dict) and len(json.dumps(client)) < 4096 else {}
        peer.name = unique
        peer.user_id = "u%d" % self._next_user
        peer.color = USER_COLORS[(self._next_user - 1) % len(USER_COLORS)]
        self._next_user += 1
        seed = self._claim_seed(peer)
        LOG.info("User %s joined as %s%s", peer.name, peer.user_id, " (shares their scene)" if seed else "")

        self._send(peer, protocol.make(
            protocol.WELCOME, user_id=peer.user_id, name=peer.name, color=peer.color,
            session=self.session_name, seq=self.scene.seq, locks=self.locks.items(), seed=seed,
            users=[p.public() for p in self._peers.values() if p.authenticated]))
        snapshot = self.scene.snapshot_ops()
        self._send(peer, protocol.make(protocol.SNAPSHOT_BEGIN, count=len(snapshot), seq=self.scene.seq))
        for chunk in protocol.chunk_ops(snapshot, SNAPSHOT_CHUNK_SIZE):
            self._send(peer, protocol.make(protocol.SNAPSHOT_CHUNK, ops=chunk))
        self._send(peer, protocol.make(protocol.SNAPSHOT_END, seq=self.scene.seq))
        presence = [{"user": p.user_id, "data": p.presence}
                    for p in self._peers.values() if p.authenticated and p.presence and p is not peer]
        for item in presence:
            self._send(peer, protocol.make(protocol.PRESENCE, user=item["user"], data=item["data"]))
        self._broadcast_users()

    def _claim_seed(self, peer):
        """True for the one user who fills an empty session with their open scene.

        Only on a server without Cinema 4D (first_joiner_seeds: run_server.py, Docker); inside a hosting Cinema 4D
        the host seeds as the host and nobody gets this flag. It goes to the first person to join, as in Blender's
        Multi-User, where the
        first client to initialise a waiting dedicated server uploads its scene and later ones are refused. The
        claim passes on only while nothing was shared yet: a seeder who leaves before their first change lets the
        next person seed; once the scene has content everyone else joins it as a guest."""
        if not self.first_joiner_seeds or self.scene.seq != 0:
            return False
        current = self._seeder
        if current is not None and any(p.user_id == current for p in self._peers.values() if p is not peer):
            return False
        self._seeder = peer.user_id
        return True

    def _permission(self, peer, op):
        """Return None when allowed, otherwise a reason string."""
        kind = op["op"]
        uuid = op["uuid"]
        user = peer.user_id
        if kind == "create":
            parent = op.get("parent")
            if parent is not None and not self.locks.can_edit(self.scene, user, parent):
                return "parent is locked by another user"
            return None
        if kind == "update":
            return None if self.locks.can_edit(self.scene, user, uuid) else "locked by another user"
        if not self.locks.can_edit_subtree(self.scene, user, uuid):
            return "locked by another user"
        if kind == "move":
            parent = op.get("parent")
            if parent is not None and not self.locks.can_edit(self.scene, user, parent):
                return "target parent is locked by another user"
        return None

    def _on_ops(self, peer, message):
        ops = message.get("ops")
        cid = message.get("cid")
        if not isinstance(ops, list) or len(ops) > MAX_OPS_PER_MESSAGE:
            raise protocol.ProtocolError("invalid ops list")
        accepted = []
        accepted_index = []
        rejected = []
        dropped_locks = []
        for index, op in enumerate(ops):
            uuid = op.get("uuid") if isinstance(op, dict) else None
            try:
                self.scene.check(op)
            except OpError as exc:
                rejected.append({"i": index, "uuid": uuid, "reason": error_text(exc)})
                continue
            reason = self._permission(peer, op)
            if reason is not None:
                rejected.append({"i": index, "uuid": uuid, "reason": reason})
                continue
            removed = [uuid] + self.scene.descendants(uuid) if op["op"] == "delete" else []
            self.scene.apply(op)
            self.scene.seq += 1
            stamped = dict(op)
            stamped["seq"] = self.scene.seq
            accepted.append(stamped)
            accepted_index.append(index)
            dropped_locks.extend(self.locks.forget(removed))
        if accepted:
            self._broadcast(protocol.make(protocol.OPS, author=peer.user_id, ops=accepted), exclude=peer)
        if dropped_locks:
            self._broadcast(protocol.make(protocol.LOCKS, changes=dict((u, None) for u in dropped_locks)))
        self._send(peer, protocol.make(protocol.ACK, cid=cid, seq=self.scene.seq,
                                       accepted=accepted_index, rejected=rejected))

    def _uuid_list(self, message):
        uuids = message.get("uuids", [])
        if not isinstance(uuids, list) or len(uuids) > MAX_UUIDS_PER_MESSAGE:
            raise protocol.ProtocolError("invalid uuid list")
        return [u for u in uuids if isinstance(u, string_types)]

    def _on_lock(self, peer, message):
        granted, denied = self.locks.try_lock(self.scene, peer.user_id, self._uuid_list(message))
        if granted:
            self._broadcast(protocol.make(protocol.LOCKS, changes=dict((u, peer.user_id) for u in granted)))
        if denied:
            self._send(peer, protocol.make(protocol.LOCK_DENIED, uuids=denied))

    def _on_release(self, peer, message):
        if message.get("all") is True:
            released = self.locks.release_all(peer.user_id)
        else:
            released = self.locks.release(peer.user_id, self._uuid_list(message))
        if released:
            self._broadcast(protocol.make(protocol.LOCKS, changes=dict((u, None) for u in released)))

    def _on_presence(self, peer, message):
        data = message.get("data")
        if not isinstance(data, dict) or len(to_bytes(json.dumps(data))) > MAX_PRESENCE_BYTES:
            raise protocol.ProtocolError("invalid presence data")
        peer.presence = data
        self._broadcast(protocol.make(protocol.PRESENCE, user=peer.user_id, data=data), exclude=peer)

    def _on_ping(self, peer, message):
        ts = message.get("ts")
        self._send(peer, protocol.make(protocol.PONG, ts=ts if isinstance(ts, integer_types + (float,)) else 0))

    def _on_pong(self, peer, message):
        pass

    def _on_bye(self, peer, message):
        self._drop(peer, "said goodbye")
