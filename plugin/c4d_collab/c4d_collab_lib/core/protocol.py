"""Wire protocol: framing, message validation, authentication and array packing.

Frame layout: >I payload length, B flags (bit0 = zlib), then UTF-8 JSON payload.
No pickle, no eval: a peer can only ever send us plain JSON data.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import base64
import hashlib
import hmac
import json
import os
import struct
import zlib

from .compat import error_text, integer_types, string_types, to_bytes, to_text

PROTOCOL_VERSION = 1
DEFAULT_PORT = 47800
MAX_MESSAGE_SIZE = 64 * 1024 * 1024
COMPRESS_THRESHOLD = 64 * 1024

FLAG_ZLIB = 0x01
HEADER = struct.Struct(">IB")

# Message types --------------------------------------------------------------
CHALLENGE = "challenge"
HELLO = "hello"
WELCOME = "welcome"
SNAPSHOT_BEGIN = "snapshot_begin"
SNAPSHOT_CHUNK = "snapshot_chunk"
SNAPSHOT_END = "snapshot_end"
OPS = "ops"
ACK = "ack"
LOCK = "lock"
RELEASE = "release"
LOCKS = "locks"
LOCK_DENIED = "lock_denied"
PRESENCE = "presence"
USERS = "users"
PING = "ping"
PONG = "pong"
BYE = "bye"
ERROR = "error"

MESSAGE_TYPES = frozenset([
    CHALLENGE, HELLO, WELCOME, SNAPSHOT_BEGIN, SNAPSHOT_CHUNK, SNAPSHOT_END,
    OPS, ACK, LOCK, RELEASE, LOCKS, LOCK_DENIED, PRESENCE,
    USERS, PING, PONG, BYE, ERROR,
])

# Messages a client is allowed to send to the server after authentication.
CLIENT_MESSAGES = frozenset([OPS, LOCK, RELEASE, PRESENCE, PING, PONG, BYE])

# Error codes ------------------------------------------------------------------
ERR_VERSION = "version_mismatch"
ERR_AUTH = "auth_failed"
ERR_PROTOCOL = "protocol_error"
ERR_FULL = "session_full"
ERR_TIMEOUT = "timeout"
ERR_RATE = "rate_limited"
ERR_BANNED = "temporarily_banned"
ERR_SLOW = "too_slow"


class ProtocolError(Exception):
    """Raised when a peer sends something that does not follow the protocol."""


def make(msg_type, **fields):
    """Build a message dict with type and protocol version."""
    message = {"t": msg_type, "v": PROTOCOL_VERSION}
    message.update(fields)
    return message


def encode(message):
    """Encode a message dict to a framed byte string."""
    payload = to_bytes(json.dumps(message, separators=(",", ":"), ensure_ascii=False))
    flags = 0
    if len(payload) > COMPRESS_THRESHOLD:
        payload = zlib.compress(payload, 6)
        flags |= FLAG_ZLIB
    if len(payload) > MAX_MESSAGE_SIZE:
        raise ProtocolError("message too large (%d bytes)" % len(payload))
    return HEADER.pack(len(payload), flags) + payload


def decode_payload(payload, flags):
    if flags & FLAG_ZLIB:
        decompressor = zlib.decompressobj()
        payload = decompressor.decompress(payload, MAX_MESSAGE_SIZE)
        if decompressor.unconsumed_tail:
            raise ProtocolError("decompressed message too large")
    try:
        message = json.loads(to_text(payload))
    except ValueError as exc:
        raise ProtocolError("invalid JSON: %s" % error_text(exc))
    validate(message)
    return message


def validate(message):
    """Check the envelope of a decoded message. Field-level checks live with the handlers."""
    if not isinstance(message, dict):
        raise ProtocolError("message must be an object")
    msg_type = message.get("t")
    if msg_type not in MESSAGE_TYPES:
        raise ProtocolError("unknown message type: %r" % (msg_type,))
    version = message.get("v")
    if not isinstance(version, integer_types) or isinstance(version, bool):
        raise ProtocolError("missing protocol version")


class FrameDecoder(object):
    """Incremental decoder: feed raw socket bytes, get complete messages back."""

    def __init__(self, max_size=MAX_MESSAGE_SIZE):
        self._buffer = bytearray()
        self.max_size = max_size

    def feed(self, data):
        self._buffer.extend(data)
        messages = []
        while len(self._buffer) >= HEADER.size:
            length, flags = HEADER.unpack_from(bytes(self._buffer[:HEADER.size]))
            if length > self.max_size:
                raise ProtocolError("frame too large (%d bytes)" % length)
            end = HEADER.size + length
            if len(self._buffer) < end:
                break
            payload = bytes(self._buffer[HEADER.size:end])
            del self._buffer[:end]
            messages.append(decode_payload(payload, flags))
        return messages


# Authentication -------------------------------------------------------------

def new_nonce():
    return to_text(base64.b64encode(os.urandom(24)))


def auth_proof(password, nonce):
    """HMAC-SHA256(password, nonce) as hex. The password itself never travels."""
    return hmac.new(to_bytes(password or ""), to_bytes(nonce), hashlib.sha256).hexdigest()


def _constant_time_equal(left, right):
    # hmac.compare_digest only exists from Python 2.7.7; old Cinema 4D builds may ship older.
    compare = getattr(hmac, "compare_digest", None)
    if compare is not None:
        return compare(left, right)
    if len(left) != len(right):  # pragma: no cover
        return False
    result = 0
    for a, b in zip(bytearray(left), bytearray(right)):  # pragma: no cover
        result |= a ^ b
    return result == 0  # pragma: no cover


def check_proof(password, nonce, proof):
    if not isinstance(proof, string_types):
        return False
    return _constant_time_equal(to_bytes(auth_proof(password, nonce)), to_bytes(proof))


# Compact numeric arrays -------------------------------------------------------

def pack_floats(values):
    """Pack a flat list of floats (little-endian doubles) into a base64 string."""
    values = list(values)
    return to_text(base64.b64encode(struct.pack("<%dd" % len(values), *values)))


def unpack_floats(text):
    raw = base64.b64decode(to_bytes(text))
    if len(raw) % 8:
        raise ProtocolError("float array has invalid length")
    return list(struct.unpack("<%dd" % (len(raw) // 8), raw))


def pack_ints(values):
    """Pack a flat list of signed 32-bit ints into a base64 string."""
    values = list(values)
    return to_text(base64.b64encode(struct.pack("<%di" % len(values), *values)))


def unpack_ints(text):
    raw = base64.b64decode(to_bytes(text))
    if len(raw) % 4:
        raise ProtocolError("int array has invalid length")
    return list(struct.unpack("<%di" % (len(raw) // 4), raw))


MAX_CHUNK_BYTES = 8 * 1024 * 1024   # estimated size of one OPS / snapshot message (texture files are big)


def _op_weight(op):
    """Rough size of an op: only texture assets are big enough to matter, everything else counts 1 KB."""
    data = op.get("data") if isinstance(op, dict) else None
    packed = data.get("z") if isinstance(data, dict) else None
    return 1024 + (len(packed) if isinstance(packed, string_types) else 0)


def chunk_ops(ops, max_count, max_bytes=MAX_CHUNK_BYTES):
    """Split ops into messages of at most `max_count` ops and about `max_bytes` bytes. Order is kept;
    an op that is bigger than the limit travels alone."""
    chunk, weight = [], 0
    for op in ops:
        size = _op_weight(op)
        if chunk and (len(chunk) >= max_count or weight + size > max_bytes):
            yield chunk
            chunk, weight = [], 0
        chunk.append(op)
        weight += size
    if chunk:
        yield chunk
