# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import struct
import unittest

import _path  # noqa: F401
from c4d_collab_lib.core import protocol


class FramingTest(unittest.TestCase):
    def test_roundtrip_split_feed(self):
        messages = [protocol.make(protocol.PING, ts=1.5), protocol.make(protocol.USERS, users=[{"name": "Ayşe"}])]
        data = b"".join(protocol.encode(m) for m in messages)
        decoder = protocol.FrameDecoder()
        received = []
        for i in range(len(data)):  # one byte at a time
            received.extend(decoder.feed(data[i:i + 1]))
        self.assertEqual(received, messages)

    def test_large_message_is_compressed(self):
        message = protocol.make(protocol.SNAPSHOT_CHUNK, ops=[{"op": "x" * 100}] * 2000)
        frame = protocol.encode(message)
        _, flags = protocol.HEADER.unpack(frame[:protocol.HEADER.size])
        self.assertTrue(flags & protocol.FLAG_ZLIB)
        self.assertEqual(protocol.FrameDecoder().feed(frame), [message])

    def test_rejects_oversized_frame(self):
        decoder = protocol.FrameDecoder(max_size=10)
        with self.assertRaises(protocol.ProtocolError):
            decoder.feed(protocol.HEADER.pack(11, 0) + b"x" * 11)

    def test_rejects_garbage(self):
        bad = [b"not json", b"[1,2]", b'{"t":"nope","v":1}', b'{"t":"ping"}']
        for payload in bad:
            with self.assertRaises(protocol.ProtocolError):
                protocol.FrameDecoder().feed(struct.pack(">IB", len(payload), 0) + payload)

    def test_zip_bomb_is_rejected(self):
        import zlib
        payload = zlib.compress(b"{" + b" " * (protocol.MAX_MESSAGE_SIZE + 10) + b"}")
        with self.assertRaises(protocol.ProtocolError):
            protocol.FrameDecoder().feed(protocol.HEADER.pack(len(payload), protocol.FLAG_ZLIB) + payload)


class AuthTest(unittest.TestCase):
    def test_proof(self):
        nonce = protocol.new_nonce()
        proof = protocol.auth_proof("şifre", nonce)
        self.assertTrue(protocol.check_proof("şifre", nonce, proof))
        self.assertFalse(protocol.check_proof("wrong", nonce, proof))
        self.assertFalse(protocol.check_proof("şifre", protocol.new_nonce(), proof))
        self.assertFalse(protocol.check_proof("şifre", nonce, None))


class PackingTest(unittest.TestCase):
    def test_floats(self):
        values = [0.0, -1.5, 3.141592653589793, 1e300]
        self.assertEqual(protocol.unpack_floats(protocol.pack_floats(values)), values)

    def test_ints(self):
        values = [0, -1, 2 ** 31 - 1, -2 ** 31]
        self.assertEqual(protocol.unpack_ints(protocol.pack_ints(values)), values)

    def test_empty(self):
        self.assertEqual(protocol.unpack_floats(protocol.pack_floats([])), [])


if __name__ == "__main__":
    unittest.main()


class ChunkTest(unittest.TestCase):
    def op(self, size=0):
        return {"op": "create", "uuid": "u", "data": {"z": "x" * size} if size else {}}

    def test_ops_are_split_by_count_and_keep_their_order(self):
        ops = [dict(self.op(), n=index) for index in range(25)]
        chunks = list(protocol.chunk_ops(ops, 10))
        self.assertEqual([len(c) for c in chunks], [10, 10, 5])
        self.assertEqual([op["n"] for chunk in chunks for op in chunk], list(range(25)))

    def test_big_assets_are_split_by_size_and_a_huge_one_travels_alone(self):
        ops = [self.op(5 * 1024 * 1024), self.op(5 * 1024 * 1024), self.op(), self.op(20 * 1024 * 1024), self.op()]
        chunks = list(protocol.chunk_ops(ops, 1000))
        self.assertEqual([len(c) for c in chunks], [1, 2, 1, 1])
        self.assertEqual(list(protocol.chunk_ops([], 10)), [])
