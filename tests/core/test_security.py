# -*- coding: utf-8 -*-
"""Security tests: brute force, oversized frames, flooding, slow readers, hostile names."""
from __future__ import absolute_import, division, print_function, unicode_literals

import socket
import time
import unittest

import _path  # noqa: F401
from c4d_collab_lib.core import protocol
from c4d_collab_lib.core import server as server_mod
from c4d_collab_lib.core.server import SessionServer, sanitize_name

PASSWORD = "correct horse"


class RawPeer(object):
    """Speaks the protocol by hand so tests can misbehave on purpose."""

    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), 2)
        self.sock.settimeout(3)
        self.decoder = protocol.FrameDecoder()
        self.pending = []

    def recv(self):
        while not self.pending:
            data = self.sock.recv(65536)
            if not data:
                raise EOFError("closed")
            self.pending.extend(self.decoder.feed(data))
        return self.pending.pop(0)

    def recv_until(self, msg_type):
        while True:
            message = self.recv()
            if message["t"] == msg_type:
                return message

    def send(self, message):
        self.sock.sendall(protocol.encode(message))

    def login(self, password=PASSWORD, name="raw"):
        challenge = self.recv()
        if challenge["t"] != protocol.CHALLENGE:
            return challenge
        self.send(protocol.make(protocol.HELLO, name=name,
                                proof=protocol.auth_proof(password, challenge["nonce"])))
        return self.recv()

    def close(self):
        self.sock.close()


class SecurityTest(unittest.TestCase):
    def setUp(self):
        self._saved = dict((k, getattr(server_mod, k)) for k in (
            "RATE_LIMIT_BURST", "RATE_LIMIT_PER_SECOND", "MAX_PENDING_OUTPUT"))
        self.server = SessionServer(host="127.0.0.1", port=0, password=PASSWORD, idle_timeout=5)
        self.port = self.server.start()
        self.peers = []

    def tearDown(self):
        for peer in self.peers:
            peer.close()
        self.server.stop()
        for key, value in self._saved.items():
            setattr(server_mod, key, value)

    def raw(self):
        peer = RawPeer(self.port)
        self.peers.append(peer)
        return peer

    def test_brute_force_gets_banned(self):
        for _ in range(server_mod.AUTH_FAIL_LIMIT):
            reply = self.raw().login(password="guess")
            self.assertEqual(reply["code"], protocol.ERR_AUTH)
        # Even the right password is refused while banned.
        reply = self.raw().login()
        self.assertEqual(reply["t"], protocol.ERROR)
        self.assertEqual(reply["code"], protocol.ERR_BANNED)

    def test_unauthenticated_peer_cannot_send_big_frames(self):
        peer = self.raw()
        peer.recv()  # challenge
        peer.sock.sendall(protocol.HEADER.pack(server_mod.PRE_AUTH_MAX_FRAME + 1, 0))
        reply = peer.recv()
        self.assertEqual(reply["code"], protocol.ERR_PROTOCOL)

    def test_must_authenticate_first(self):
        peer = self.raw()
        peer.recv()
        peer.send(protocol.make(protocol.OPS, cid=1, ops=[]))
        self.assertEqual(peer.recv()["code"], protocol.ERR_PROTOCOL)

    def test_flooding_is_cut_off(self):
        server_mod.RATE_LIMIT_BURST = 50
        server_mod.RATE_LIMIT_PER_SECOND = 10
        peer = self.raw()
        self.assertEqual(peer.login()["t"], protocol.WELCOME)
        frame = protocol.encode(protocol.make(protocol.PING, ts=0))
        try:
            peer.sock.sendall(frame * 500)
        except socket.error:
            pass
        error = peer.recv_until(protocol.ERROR)
        self.assertEqual(error["code"], protocol.ERR_RATE)

    def test_slow_reader_is_dropped(self):
        server_mod.MAX_PENDING_OUTPUT = 2000
        silent = self.raw()
        self.assertEqual(silent.login(name="silent")["t"], protocol.WELCOME)
        writer = self.raw()
        self.assertEqual(writer.login(name="writer")["t"], protocol.WELCOME)
        big = {"op": "create", "uuid": "big", "kind": "object", "ctype": 1, "parent": None,
               "prev": None, "name": "big", "data": {"blob": "x" * 5000}}
        writer.send(protocol.make(protocol.OPS, cid=1, ops=[big]))
        writer.recv_until(protocol.ACK)
        deadline = time.time() + 3
        while time.time() < deadline and len(self.server.users()) != 1:
            time.sleep(0.02)
        self.assertEqual([u["name"] for u in self.server.users()], ["writer"])

    def test_invalid_payloads_do_not_crash_server(self):
        peer = self.raw()
        self.assertEqual(peer.login()["t"], protocol.WELCOME)
        hostile = [
            protocol.make(protocol.OPS, cid=1, ops="nope"),
            protocol.make(protocol.LOCK, uuids={"a": 1}),
            protocol.make(protocol.PRESENCE, data="x" * 10),
            protocol.make(protocol.PRESENCE, data={"x": "y" * (server_mod.MAX_PRESENCE_BYTES + 1)}),
        ]
        for message in hostile:
            p = self.raw()
            self.assertEqual(p.login(name="h")["t"], protocol.WELCOME)
            p.send(message)
            self.assertEqual(p.recv_until(protocol.ERROR)["code"], protocol.ERR_PROTOCOL)
        # Malformed ops inside a valid list are rejected one by one, not fatal.
        peer.send(protocol.make(protocol.OPS, cid=7, ops=[None, 5, {"op": "create"}]))
        ack = peer.recv_until(protocol.ACK)
        self.assertEqual(len(ack["rejected"]), 3)
        self.assertTrue(self.server.running)


class SanitizeTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(sanitize_name("  Ayşe\n\t Yılmaz \x00‮ "), "Ayşe Yılmaz")
        self.assertEqual(sanitize_name(None), "User")
        self.assertEqual(sanitize_name("\x1b[31m"), "[31m")
        self.assertEqual(len(sanitize_name("a" * 500)), server_mod.MAX_NAME_LENGTH)


if __name__ == "__main__":
    unittest.main()
