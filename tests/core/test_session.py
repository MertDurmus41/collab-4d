# -*- coding: utf-8 -*-
"""End-to-end tests: a real server on localhost with several real clients."""
from __future__ import absolute_import, division, print_function, unicode_literals

import socket
import time
import unittest

import _path  # noqa: F401
from c4d_collab_lib.core import diff, protocol
from c4d_collab_lib.core.client import CollabClient
from c4d_collab_lib.core.mirror import SessionMirror
from c4d_collab_lib.core.server import SessionServer

TIMEOUT = 5.0


def create(uuid, parent=None, prev=None, data=None):
    return {"op": "create", "uuid": uuid, "kind": "object", "ctype": 5159, "parent": parent,
            "prev": prev, "name": uuid, "data": data or {}}


class Peer(object):
    """A client plus its mirror, pumped like the C4D main thread would."""

    def __init__(self, port, name, password=""):
        self.client = CollabClient("127.0.0.1", port, name, password=password,
                                   client_info={"app": "test"}, ping_interval=0.2)
        self.mirror = SessionMirror()
        self.events = []
        self.client.connect()

    def pump(self):
        for message in self.client.poll():
            self.events.extend(self.mirror.handle(message))

    def send(self, ops):
        cid = self.client.send_ops(ops)
        self.mirror.submitted(cid, ops)
        return cid

    def kinds(self):
        return [e[0] for e in self.events]


def wait_for(peers, condition, timeout=TIMEOUT):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for peer in peers:
            peer.pump()
        if condition():
            return True
        time.sleep(0.01)
    raise AssertionError("condition not reached in %.1fs" % timeout)


class SessionTest(unittest.TestCase):
    password = ""

    def setUp(self):
        self.server = SessionServer(host="127.0.0.1", port=0, password=self.password, idle_timeout=3.0)
        self.port = self.server.start()
        self.peers = []

    def tearDown(self):
        for peer in self.peers:
            peer.client.close()
        self.server.stop()

    def join(self, name, password=None):
        peer = Peer(self.port, name, self.password if password is None else password)
        self.peers.append(peer)
        return peer

    def join_synced(self, name):
        peer = self.join(name)
        wait_for([peer], lambda: peer.mirror.synced)
        return peer

    def test_host_seed_then_late_join_gets_snapshot(self):
        host = self.join_synced("Host")
        host.send([create("A"), create("B", prev="A"), create("A1", parent="A")])
        wait_for([host], lambda: host.mirror.pending_count == 0 and len(host.mirror.scene) == 3)
        late = self.join_synced("Late")
        self.assertEqual(late.mirror.scene.snapshot_ops(), host.mirror.scene.snapshot_ops())
        self.assertEqual(late.mirror.scene.seq, 3)

    def test_ops_are_relayed_in_order_to_three_clients(self):
        a, b, c = self.join_synced("A"), self.join_synced("B"), self.join_synced("C")
        a.send([create("X")])
        wait_for([a, b, c], lambda: all("X" in p.mirror.scene for p in (a, b, c)))
        # B and C edit different objects at the same time.
        b.send([create("Y", prev="X")])
        c.send([{"op": "update", "uuid": "X", "patch": diff.diff({}, {"pos": [1, 2, 3]})}])
        wait_for([a, b, c], lambda: all(p.mirror.pending_count == 0 for p in (a, b, c)) and
                 all(len(p.mirror.scene) == 2 and p.mirror.scene.get("X").data for p in (a, b, c)))
        expected = a.mirror.scene.snapshot_ops()
        self.assertEqual(b.mirror.scene.snapshot_ops(), expected)
        self.assertEqual(c.mirror.scene.snapshot_ops(), expected)
        self.assertEqual(a.mirror.scene.seq, self.server.scene.seq)

    def test_locks_block_other_users_and_are_released_on_disconnect(self):
        a, b = self.join_synced("A"), self.join_synced("B")
        a.send([create("X"), create("X1", parent="X")])
        wait_for([a, b], lambda: len(b.mirror.scene) == 2)
        a.client.lock(["X"])
        wait_for([a, b], lambda: b.mirror.owner("X1") == a.mirror.user_id)
        self.assertFalse(b.mirror.editable("X1"))

        b.send([{"op": "update", "uuid": "X1", "name": "hack"}, {"op": "delete", "uuid": "X"},
                create("child", parent="X1")])
        wait_for([b], lambda: "rejected" in b.kinds())
        rejected = [e for e in b.events if e[0] == "rejected"][0][1]
        self.assertEqual(len(rejected), 3)
        self.assertEqual(b.mirror.scene.get("X1").name, "X1")

        b.client.lock(["X1"])
        wait_for([b], lambda: "lock_denied" in b.kinds())

        a.client.close()
        wait_for([b], lambda: b.mirror.owner("X1") is None and len(b.mirror.users) == 1)
        b.send([{"op": "update", "uuid": "X1", "name": "mine now"}])
        wait_for([b], lambda: b.mirror.pending_count == 0)
        self.assertEqual(b.mirror.scene.get("X1").name, "mine now")

    def test_delete_drops_locks(self):
        a, b = self.join_synced("A"), self.join_synced("B")
        a.send([create("X")])
        wait_for([a, b], lambda: "X" in b.mirror.scene)
        a.client.lock(["X"])
        wait_for([b], lambda: b.mirror.owner("X") is not None)
        a.send([{"op": "delete", "uuid": "X"}])
        wait_for([b], lambda: "X" not in b.mirror.scene and b.mirror.owner("X") is None)
        self.assertEqual(self.server.locks.items(), {})

    def test_presence_and_users(self):
        a, b = self.join_synced("Ayşe"), self.join_synced("Ayşe")
        wait_for([a, b], lambda: len(a.mirror.users) == 2)
        names = sorted(u["name"] for u in a.mirror.users.values())
        self.assertEqual(names, ["Ayşe", "Ayşe (2)"])
        a.client.presence({"frame": 12, "selection": ["X"]})
        wait_for([b], lambda: b.mirror.presence.get(a.mirror.user_id, {}).get("frame") == 12)
        late = self.join_synced("Late")
        wait_for([late], lambda: a.mirror.user_id in late.mirror.presence)

    def test_ping_measures_latency(self):
        a = self.join_synced("A")
        wait_for([a], lambda: a.client.latency_ms is not None)

    def test_protocol_violation_disconnects_only_offender(self):
        good = self.join_synced("Good")
        raw = socket.create_connection(("127.0.0.1", self.port), 2)
        raw.sendall(protocol.HEADER.pack(5, 0) + b"junk!")
        raw.settimeout(2)
        data = b""
        try:
            while True:
                chunk = raw.recv(4096)
                if not chunk:
                    break
                data += chunk
        except socket.timeout:
            pass
        raw.close()
        messages = protocol.FrameDecoder().feed(data)
        self.assertEqual(messages[0]["t"], protocol.CHALLENGE)
        self.assertEqual(messages[-1]["code"], protocol.ERR_PROTOCOL)
        good.send([create("still-works")])
        wait_for([good], lambda: "still-works" in good.mirror.scene)

    def test_version_mismatch(self):
        raw = socket.create_connection(("127.0.0.1", self.port), 2)
        raw.settimeout(2)
        decoder = protocol.FrameDecoder()
        challenge = decoder.feed(raw.recv(4096))[0]
        hello = protocol.make(protocol.HELLO, name="old", proof=protocol.auth_proof("", challenge["nonce"]))
        hello["v"] = 999
        raw.sendall(protocol.encode(hello))
        reply = []
        while not reply:
            reply = decoder.feed(raw.recv(4096))
        raw.close()
        self.assertEqual(reply[0]["code"], protocol.ERR_VERSION)


class PasswordSessionTest(SessionTest):
    password = "gizli"

    def test_wrong_password(self):
        bad = self.join("Intruder", password="wrong")
        wait_for([bad], lambda: "closed" in bad.kinds())
        errors = [e for e in bad.events if e[0] == "error"]
        self.assertEqual(errors[0][1]["code"], protocol.ERR_AUTH)
        self.assertEqual(self.server.users(), [])


if __name__ == "__main__":
    unittest.main()
