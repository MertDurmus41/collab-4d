# -*- coding: utf-8 -*-
"""Tests for the main-thread session controller used by the C4D plugin."""
from __future__ import absolute_import, division, print_function, unicode_literals

import socket
import time
import unittest

import _path  # noqa: F401
from c4d_collab_lib.core.session import CONNECTED, IDLE, CollabSession, SessionError


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def pump_until(sessions, condition, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for session in sessions:
            session.pump()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached")


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.sessions = []

    def tearDown(self):
        for session in self.sessions:
            session.leave()

    def new(self):
        session = CollabSession(client_info={"app": "test"})
        self.sessions.append(session)
        return session

    def test_input_validation(self):
        session = self.new()
        with self.assertRaises(SessionError):
            session.host("A", free_port(), password="abc")  # too short
        with self.assertRaises(SessionError):
            session.host("A", "port", password="secret")
        with self.assertRaises(SessionError):
            session.host("A", 80, password="secret")
        with self.assertRaises(SessionError):
            session.join("", 47800, "B")
        self.assertEqual(session.state, IDLE)

    def test_host_join_leave(self):
        port = free_port()
        host, guest = self.new(), self.new()
        host.host("Host", port, password="secret", bind="127.0.0.1")
        guest.join("127.0.0.1", port, "Guest", password="secret")
        events = []
        guest.add_listener(lambda s, e: events.append(e[0]))
        pump_until([host, guest], lambda: host.state == CONNECTED and guest.state == CONNECTED
                   and len(guest.users()) == 2)
        self.assertTrue(host.is_host)
        self.assertFalse(guest.is_host)
        self.assertIn("welcome", events)
        with self.assertRaises(SessionError):
            guest.join("127.0.0.1", port, "Again")

        host.leave()
        pump_until([guest], lambda: guest.state == IDLE)
        self.assertTrue(any("Disconnected" in line for line in guest.log))

    def test_wrong_password_ends_session(self):
        port = free_port()
        host, guest = self.new(), self.new()
        host.host("Host", port, password="secret", bind="127.0.0.1")
        guest.join("127.0.0.1", port, "Guest", password="wrong")
        pump_until([host, guest], lambda: guest.state == IDLE)
        self.assertTrue(any("wrong password" in line for line in guest.log))

    def test_port_in_use(self):
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        try:
            with self.assertRaises(SessionError) as caught:
                self.new().host("Host", blocker.getsockname()[1], password="secret", bind="127.0.0.1")
            self.assertIn("already in use", caught.exception.args[0])
        finally:
            blocker.close()

    def test_unreachable_host(self):
        guest = self.new()
        guest.join("127.0.0.1", free_port(), "Guest")
        pump_until([guest], lambda: guest.state == IDLE)
        self.assertTrue(any("cannot connect" in line for line in guest.log))

    def test_first_to_join_a_server_without_cinema4d_shares_the_scene(self):
        """run_server.py / Docker: nobody hosts, so the first person to join fills the empty session."""
        from c4d_collab_lib.core import protocol
        from c4d_collab_lib.core.server import SessionServer
        server = SessionServer(host="127.0.0.1", port=free_port(), password="secret", first_joiner_seeds=True)
        port = server.start()
        self.addCleanup(server.stop)
        first, second = self.new(), self.new()
        first.join("127.0.0.1", port, "First", password="secret")
        pump_until([first], lambda: first.state == CONNECTED)
        second.join("127.0.0.1", port, "Second", password="secret")
        pump_until([first, second], lambda: second.state == CONNECTED)
        self.assertTrue(first.seeds_scene and first.leads)
        self.assertFalse(second.seeds_scene or second.leads)
        self.assertFalse(first.is_host)

        # The seeder leaves before sharing anything: the next person may seed instead.
        first.leave()
        third = self.new()
        third.join("127.0.0.1", port, "Third", password="secret")
        pump_until([second, third], lambda: third.state == CONNECTED)
        self.assertTrue(third.seeds_scene)
        self.assertFalse(first.seeds_scene)   # reset on leave

        # Once the scene has content, nobody seeds any more.
        third.send_ops([{"op": "create", "uuid": "n1", "kind": "object", "ctype": 5140, "parent": None,
                         "prev": None, "name": "Null", "data": {}}])
        pump_until([second, third], lambda: server.scene.seq > 0)
        third.leave()
        fourth = self.new()
        fourth.join("127.0.0.1", port, "Fourth", password="secret")
        pump_until([second, fourth], lambda: fourth.state == CONNECTED)
        self.assertFalse(fourth.seeds_scene)
        self.assertEqual(protocol.PROTOCOL_VERSION, 1)   # an extra welcome field, no protocol change

    def test_a_server_without_cinema4d_starts_over_when_everyone_has_left(self):
        from c4d_collab_lib.core.server import SessionServer
        server = SessionServer(host="127.0.0.1", port=free_port(), password="secret", first_joiner_seeds=True)
        port = server.start()
        self.addCleanup(server.stop)
        first, second = self.new(), self.new()
        first.join("127.0.0.1", port, "First", password="secret")
        pump_until([first], lambda: first.state == CONNECTED)
        first.send_ops([{"op": "create", "uuid": "n1", "kind": "object", "ctype": 5140, "parent": None,
                         "prev": None, "name": "Null", "data": {}}])
        pump_until([first], lambda: server.scene.seq > 0)
        second.join("127.0.0.1", port, "Second", password="secret")
        pump_until([first, second], lambda: second.state == CONNECTED)
        first.leave()
        time.sleep(0.2)
        self.assertGreater(server.scene.seq, 0)   # someone is still in: the scene stays
        second.leave()
        pump_until([], lambda: server.scene.seq == 0 and len(server.scene) == 0)
        third = self.new()
        third.join("127.0.0.1", port, "Third", password="secret")
        pump_until([third], lambda: third.state == CONNECTED)
        self.assertTrue(third.seeds_scene)

    def test_the_host_is_never_a_seeder_by_welcome(self):
        """Inside a hosting Cinema 4D only the host shares its scene, even when a guest's hello arrives before the
        host's own client (found by this test: both connect at once here)."""
        port = free_port()
        host, guest = self.new(), self.new()
        guest_first = self.new()
        host.host("Host", port, password="secret", bind="127.0.0.1")
        guest_first.join("127.0.0.1", port, "Early", password="secret")
        guest.join("127.0.0.1", port, "Guest", password="secret")
        pump_until([host, guest, guest_first], lambda: guest_first.state == CONNECTED)
        self.assertFalse(guest_first.leads)
        pump_until([host, guest], lambda: host.state == CONNECTED and guest.state == CONNECTED)
        self.assertTrue(host.leads)
        self.assertFalse(host.seeds_scene)    # leads as the host, not through the welcome flag
        self.assertFalse(guest.leads)

    def test_listener_errors_do_not_break_pump(self):
        port = free_port()
        host = self.new()
        host.add_listener(lambda s, e: 1 / 0)
        host.host("Host", port, password="secret", bind="127.0.0.1")
        pump_until([host], lambda: host.state == CONNECTED)


if __name__ == "__main__":
    unittest.main()
