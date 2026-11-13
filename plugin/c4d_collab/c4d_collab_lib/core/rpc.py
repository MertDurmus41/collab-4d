"""Local command channel for the MCP page: a tiny request/reply server and client.

An AI assistant (through mcp_stdio_bridge.py) sends commands to the running Cinema 4D; this module is
the transport only and knows nothing about Cinema 4D (see ARCHITECTURE.md, section "MCP").

Rules (all enforced here and tested in tests/core/test_rpc.py):
  * only connections from this computer (127.0.0.1 / ::1) are served, the socket is bound to 127.0.0.1
  * the access key never travels: the server sends a nonce, the client answers HMAC-SHA256(key, nonce)
  * before authentication a frame may be 4 KB at most and 5 s are allowed; after it 8 MB
  * five failed logins within a minute lock the logins for a minute
  * the network thread never runs a command: commands wait in a queue until the main thread calls
    `pump()`, so the scene is only ever touched from the main thread
  * only JSON, no pickle, no eval

Frames: >I payload length, then UTF-8 JSON. Messages:
  S->C {"t":"challenge","nonce":..}   C->S {"t":"auth","proof":..}   S->C {"t":"ready"}
  C->S {"t":"call","id":..,"cmd":..,"args":{..}}   S->C {"t":"result","id":..,"ok":bool,"result"|"error":..}
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import select
import socket
import struct
import threading
import time

from . import protocol
from .compat import error_text, integer_types, is_fatal, monotonic, queue, string_types, to_bytes, to_text

HEADER = struct.Struct(">I")
MAX_PRE_AUTH_FRAME = 4096
MAX_FRAME = 8 * 1024 * 1024
MAX_RESULT = 4 * 1024 * 1024
AUTH_TIMEOUT = 5.0
MAX_CLIENTS = 4
MAX_QUEUED = 8
LOGIN_FAILURES = 5
LOGIN_WINDOW = 60.0
LOGIN_LOCKOUT = 60.0
POLL_SECONDS = 0.05
LOOPBACK = frozenset(["127.0.0.1", "::1", "::ffff:127.0.0.1"])


def encode(message):
    payload = to_bytes(json.dumps(message, separators=(",", ":"), ensure_ascii=False, default=_fallback))
    return HEADER.pack(len(payload)) + payload


def _fallback(value):
    """Anything json cannot write (a c4d.Vector...) becomes its text instead of an exception."""
    try:
        return to_text(repr(value))
    except Exception:
        return "<unprintable>"


class FrameError(Exception):
    pass


class Decoder(object):
    def __init__(self, max_size):
        self.max_size = max_size
        self._buffer = bytearray()

    def feed(self, data):
        self._buffer.extend(data)
        messages = []
        while len(self._buffer) >= HEADER.size:
            length = HEADER.unpack_from(bytes(self._buffer[:HEADER.size]))[0]
            if length > self.max_size:
                raise FrameError("frame too large")
            end = HEADER.size + length
            if len(self._buffer) < end:
                break
            payload = bytes(self._buffer[HEADER.size:end])
            del self._buffer[:end]
            try:
                message = json.loads(to_text(payload))
            except ValueError:
                raise FrameError("invalid JSON")
            if not isinstance(message, dict):
                raise FrameError("message must be an object")
            messages.append(message)
        return messages


class _Peer(object):
    def __init__(self, sock, peer_id):
        sock.setblocking(False)
        self.sock = sock
        self.id = peer_id
        self.decoder = Decoder(MAX_PRE_AUTH_FRAME)
        self.outbuf = bytearray()
        self.authed = False
        self.nonce = protocol.new_nonce()
        self.since = monotonic()
        self.busy = False   # a call of this peer waits in the queue or runs

    def send(self, message):
        self.outbuf.extend(encode(message))

    def flush(self):
        while self.outbuf:
            try:
                sent = self.sock.send(bytes(self.outbuf[:65536]))
            except (OSError, socket.error) as exc:
                if _would_block(exc):
                    return
                raise
            if not sent:
                return
            del self.outbuf[:sent]

    def close(self):
        try:
            self.sock.close()
        except (OSError, socket.error):
            pass


def _would_block(exc):
    code = exc.args[0] if exc.args else None
    return code in (11, 35, 10035)   # EAGAIN, EAGAIN on BSD/macOS, WSAEWOULDBLOCK


class RpcServer(object):
    """Serves commands to local clients. `key_of()` returns the current access key (checked at every login)."""

    def __init__(self, key_of, notify=None, log=None, timeout_of=None):
        self._key_of = key_of
        self._timeout_of = timeout_of
        self._notify = notify
        self._log = log or (lambda text: None)
        self._jobs = queue.Queue()
        self._replies = queue.Queue()
        self._thread = None
        self._listener = None
        self._stop = threading.Event()
        self._peers = {}
        self._next_id = 1
        self._failures = []
        self._locked_until = 0.0
        self.port = None
        self.error = None
        self.clients = 0

    # State -----------------------------------------------------------------------------------

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def status_text(self):
        if self.error:
            return "Error: %s" % self.error
        if not self.running:
            return "Off"
        noun = "1 client connected" if self.clients == 1 else "%d clients connected" % self.clients
        return "Listening on 127.0.0.1:%d (%s)" % (self.port, noun if self.clients else "no client yet")

    # Start / stop ----------------------------------------------------------------------------

    def start(self, port):
        """Start listening on 127.0.0.1:port. Returns True, or False with `error` set (port in use...)."""
        if self.running:
            if self.port == port:
                return True
            self.stop()
        self.error = None
        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                listener.bind(("127.0.0.1", int(port)))
                listener.listen(4)
                listener.setblocking(False)
            except BaseException:
                listener.close()
                raise
        except BaseException as exc:
            if is_fatal(exc):
                raise
            self.error = "port %s: %s" % (port, error_text(exc))
            self._log("MCP server could not start: %s" % self.error)
            return False
        self._listener = listener
        self.port = int(port)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="c4d-collab-mcp")
        self._thread.daemon = True
        self._thread.start()
        self._log("MCP server listening on 127.0.0.1:%d" % self.port)
        return True

    def stop(self):
        thread, self._thread = self._thread, None
        self._stop.set()
        if thread is not None:
            thread.join(2.0)
        for peer in list(self._peers.values()):
            peer.close()
        self._peers = {}
        if self._listener is not None:
            try:
                self._listener.close()
            except (OSError, socket.error):
                pass
            self._listener = None
        self._drain(self._jobs)
        self._drain(self._replies)
        self.clients = 0
        self.port = None
        self._log("MCP server stopped")

    @staticmethod
    def _drain(box):
        try:
            while True:
                box.get_nowait()
        except queue.Empty:
            pass

    # Main thread -----------------------------------------------------------------------------

    def pump(self, execute, limit=1):
        """Run up to `limit` waiting commands with `execute(cmd, args)` -> result. Call from the main
        thread only. Returns the number of commands run."""
        done = 0
        while done < limit:
            try:
                peer_id, call_id, cmd, args, queued = self._jobs.get_nowait()
            except queue.Empty:
                break
            wait = self._timeout_of() if self._timeout_of is not None else None
            if wait and monotonic() - queued > wait:
                # Cinema 4D was busy (rendering, a long script...): the AI app has given up on this one
                self._replies.put((peer_id, {"t": "result", "id": call_id, "ok": False,
                                             "error": "timed out: Cinema 4D's main thread was busy for more than "
                                                      "%g s (Request Timeout in Preferences > MCP)" % wait}))
                done += 1
                continue
            try:
                reply = {"t": "result", "id": call_id, "ok": True, "result": execute(cmd, args)}
            except BaseException as exc:
                if is_fatal(exc):
                    raise
                reply = {"t": "result", "id": call_id, "ok": False, "error": error_text(exc) or exc.__class__.__name__}
            self._replies.put((peer_id, reply))
            done += 1
        return done

    # Network thread --------------------------------------------------------------------------

    def _run(self):
        while not self._stop.is_set():
            try:
                self._step()
            except BaseException as exc:
                if is_fatal(exc):
                    raise
                self._log("MCP server loop error: %s" % error_text(exc))
                time.sleep(0.2)

    def _step(self):
        listener = self._listener
        peers = list(self._peers.values())
        readable = [listener] + [p.sock for p in peers]
        writable = [p.sock for p in peers if p.outbuf]
        try:
            ready, can_write, _ = select.select(readable, writable, [], POLL_SECONDS)
        except (OSError, socket.error, ValueError):
            self._prune()
            time.sleep(0.02)
            return
        by_sock = dict((p.sock, p) for p in peers)
        for sock in ready:
            if sock is listener:
                self._accept()
            elif sock in by_sock:
                self._read(by_sock[sock])
        self._deliver()
        for peer in list(self._peers.values()):
            if peer.outbuf:
                try:
                    peer.flush()
                except (OSError, socket.error):
                    self._drop(peer)
        now = monotonic()
        for peer in list(self._peers.values()):
            if not peer.authed and now - peer.since > AUTH_TIMEOUT:
                self._drop(peer)
        self.clients = sum(1 for p in self._peers.values() if p.authed)

    def _accept(self):
        try:
            sock, address = self._listener.accept()
        except (OSError, socket.error):
            return
        host = address[0] if address else ""
        if host not in LOOPBACK or len(self._peers) >= MAX_CLIENTS or monotonic() < self._locked_until:
            try:
                sock.close()
            except (OSError, socket.error):
                pass
            return
        peer = _Peer(sock, self._next_id)
        self._next_id += 1
        self._peers[peer.id] = peer
        peer.send({"t": "challenge", "nonce": peer.nonce})

    def _read(self, peer):
        try:
            data = peer.sock.recv(65536)
        except (OSError, socket.error) as exc:
            if not _would_block(exc):
                self._drop(peer)
            return
        if not data:
            self._drop(peer)
            return
        try:
            for message in peer.decoder.feed(data):
                self._handle(peer, message)
                if peer.id not in self._peers:
                    return
        except FrameError as exc:
            self._reject(peer, "protocol_error", error_text(exc))

    def _handle(self, peer, message):
        kind = message.get("t")
        if not peer.authed:
            if kind != "auth":
                self._reject(peer, "protocol_error", "authenticate first")
                return
            key = self._key_of()
            if key and protocol.check_proof(key, peer.nonce, message.get("proof")):
                peer.authed = True
                peer.decoder.max_size = MAX_FRAME
                peer.send({"t": "ready"})
            else:
                self._note_failure()
                self._reject(peer, "auth_failed", "wrong access key")
            return
        if kind == "ping":
            peer.send({"t": "pong"})
            return
        if kind != "call":
            self._reject(peer, "protocol_error", "unknown message")
            return
        call_id, cmd, args = message.get("id"), message.get("cmd"), message.get("args")
        if args is None:
            args = {}
        valid_id = isinstance(call_id, string_types + integer_types) and not isinstance(call_id, bool)
        if not valid_id or not isinstance(cmd, string_types) or not isinstance(args, dict) or len(cmd) > 64:
            peer.send({"t": "result", "id": call_id if valid_id else None, "ok": False, "error": "invalid call"})
            return
        if peer.busy or self._jobs.qsize() >= MAX_QUEUED:
            peer.send({"t": "result", "id": call_id, "ok": False, "error": "busy: finish the previous command first"})
            return
        peer.busy = True
        self._jobs.put((peer.id, call_id, cmd, args, monotonic()))
        if self._notify is not None:
            try:
                self._notify()
            except BaseException as exc:
                if is_fatal(exc):
                    raise

    def _deliver(self):
        try:
            while True:
                peer_id, reply = self._replies.get_nowait()
                peer = self._peers.get(peer_id)
                if peer is None:
                    continue
                peer.busy = False
                frame = encode(reply)
                if len(frame) - HEADER.size > MAX_RESULT:
                    frame = encode({"t": "result", "id": reply.get("id"), "ok": False,
                                    "error": "result too large (%d bytes)" % (len(frame) - HEADER.size)})
                peer.outbuf.extend(frame)
        except queue.Empty:
            pass

    def _note_failure(self):
        now = monotonic()
        self._failures = [t for t in self._failures if now - t < LOGIN_WINDOW] + [now]
        if len(self._failures) >= LOGIN_FAILURES:
            self._locked_until = now + LOGIN_LOCKOUT
            self._failures = []
            self._log("MCP server: too many wrong access keys, logins locked for %d s" % LOGIN_LOCKOUT)

    def _reject(self, peer, code, text):
        peer.send({"t": "error", "code": code, "message": text})
        try:
            peer.flush()
        except (OSError, socket.error):
            pass
        self._drop(peer)

    def _drop(self, peer):
        self._peers.pop(peer.id, None)
        peer.close()

    def _prune(self):
        for peer in list(self._peers.values()):
            try:
                if peer.sock.fileno() < 0:
                    self._drop(peer)
            except (OSError, socket.error, ValueError):
                self._drop(peer)


class RpcError(Exception):
    pass


class RpcClient(object):
    """Blocking client used by the stdio bridge and the tests."""

    def __init__(self, port, key, timeout=10.0, host="127.0.0.1"):
        self.timeout = timeout
        self._next_id = 1
        self._sock = socket.create_connection((host, int(port)), timeout)
        self._decoder = Decoder(MAX_FRAME)
        self._pending = []
        try:
            challenge = self._read_message(timeout)
            if challenge.get("t") != "challenge":
                raise RpcError("unexpected reply from the server")
            self._send({"t": "auth", "proof": protocol.auth_proof(key, challenge.get("nonce"))})
            reply = self._read_message(timeout)
            if reply.get("t") == "error":
                raise RpcError(reply.get("message") or reply.get("code") or "login refused")
            if reply.get("t") != "ready":
                raise RpcError("unexpected reply from the server")
        except BaseException:
            self.close()
            raise

    def _send(self, message):
        self._sock.sendall(encode(message))

    def _read_message(self, timeout):
        deadline = monotonic() + timeout
        while not self._pending:
            left = deadline - monotonic()
            if left <= 0:
                raise RpcError("timed out waiting for Cinema 4D")
            self._sock.settimeout(left)
            try:
                data = self._sock.recv(65536)
            except socket.timeout:
                raise RpcError("timed out waiting for Cinema 4D")
            if not data:
                raise RpcError("Cinema 4D closed the connection")
            self._pending.extend(self._decoder.feed(data))
        return self._pending.pop(0)

    def call(self, cmd, args=None, timeout=None):
        """Run a command in Cinema 4D; returns its result or raises RpcError."""
        call_id = self._next_id
        self._next_id += 1
        self._send({"t": "call", "id": call_id, "cmd": cmd, "args": args or {}})
        reply = self._read_message(timeout if timeout is not None else self.timeout)
        while reply.get("t") == "result" and reply.get("id") != call_id:
            reply = self._read_message(timeout if timeout is not None else self.timeout)
        if reply.get("t") == "error":
            raise RpcError(reply.get("message") or reply.get("code"))
        if not reply.get("ok"):
            raise RpcError(reply.get("error") or "command failed")
        return reply.get("result")

    def close(self):
        try:
            self._sock.close()
        except (OSError, socket.error):
            pass
