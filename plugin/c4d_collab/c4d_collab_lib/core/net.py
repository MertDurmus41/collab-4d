"""Non-blocking framed socket connection shared by client and server."""
from __future__ import absolute_import, division, print_function, unicode_literals

import errno
import socket

from . import protocol
from .compat import error_text, monotonic

RECV_SIZE = 256 * 1024

_WOULD_BLOCK = set([
    errno.EAGAIN, errno.EWOULDBLOCK, getattr(errno, "WSAEWOULDBLOCK", errno.EWOULDBLOCK),
])


class ConnectionClosed(Exception):
    pass


class Connection(object):
    def __init__(self, sock, address=None):
        sock.setblocking(False)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except (OSError, socket.error):  # pragma: no cover
            pass
        self.sock = sock
        self.address = address
        self.decoder = protocol.FrameDecoder()
        self.outbuf = bytearray()
        self.closed = False
        self.last_seen = monotonic()

    def fileno(self):
        return self.sock.fileno()

    @property
    def wants_write(self):
        return bool(self.outbuf)

    def send(self, message):
        if self.closed:
            return
        self.outbuf.extend(protocol.encode(message))

    def read(self):
        """Read what is available. Returns decoded messages; raises ConnectionClosed/ProtocolError."""
        try:
            data = self.sock.recv(RECV_SIZE)
        except (OSError, socket.error) as exc:
            if exc.args and exc.args[0] in _WOULD_BLOCK:
                return []
            raise ConnectionClosed(error_text(exc))
        if not data:
            raise ConnectionClosed("peer closed the connection")
        self.last_seen = monotonic()
        return self.decoder.feed(data)

    def flush(self):
        """Write as much buffered output as the socket accepts."""
        while self.outbuf:
            try:
                sent = self.sock.send(bytes(self.outbuf[:RECV_SIZE]))
            except (OSError, socket.error) as exc:
                if exc.args and exc.args[0] in _WOULD_BLOCK:
                    return
                raise ConnectionClosed(error_text(exc))
            if not sent:
                return
            del self.outbuf[:sent]

    def flush_blocking(self, timeout=2.0):
        """Best-effort flush used right before closing (e.g. to deliver an error or bye)."""
        deadline = monotonic() + timeout
        while self.outbuf and monotonic() < deadline:
            try:
                self.flush()
            except ConnectionClosed:
                return

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except (OSError, socket.error):
            pass
        try:
            self.sock.close()
        except (OSError, socket.error):  # pragma: no cover
            pass
