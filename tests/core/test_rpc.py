# -*- coding: utf-8 -*-
"""The local command channel behind the MCP page (core/rpc.py) and the stdio bridge. Python 2.7 and 3.x."""
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.abspath(os.path.join(HERE, "..", "..", "plugin", "c4d_collab"))
sys.path.insert(0, PLUGIN)

from c4d_collab_lib.core import mcp_spec, protocol, rpc  # noqa: E402

KEY = "0123456789abcdef0123456789abcdef"


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class Harness(object):
    """A server whose commands run on THIS thread (standing in for Cinema 4D's main thread)."""

    def __init__(self, key=KEY, port=None, timeout=None):
        self.key = key
        self.thread_ids = []
        self.calls = []
        self.notified = threading.Event()
        self.server = rpc.RpcServer(key_of=lambda: self.key, notify=self.notified.set,
                                    timeout_of=(lambda: timeout) if timeout else None)
        self.port = port or free_port()
        assert self.server.start(self.port)

    def execute(self, cmd, args):
        self.thread_ids.append(threading.current_thread().ident)
        self.calls.append((cmd, args))
        if cmd == "boom":
            raise ValueError("it broke")
        if cmd == "big":
            return "x" * (rpc.MAX_RESULT + 10)
        return {"cmd": cmd, "args": args}

    def call(self, cmd, args=None, key=None, timeout=5.0, delay=0.0):
        """Run a client call in a thread and pump the server here (after `delay` seconds) until it answers."""
        outcome = {}

        def work():
            try:
                client = rpc.RpcClient(self.port, key or self.key, timeout=timeout)
                try:
                    outcome["result"] = client.call(cmd, args)
                finally:
                    client.close()
            except Exception as exc:
                outcome["error"] = exc
        thread = threading.Thread(target=work)
        thread.start()
        time.sleep(delay)
        deadline = time.time() + timeout + 1
        while thread.is_alive() and time.time() < deadline:
            self.server.pump(self.execute)
            time.sleep(0.005)
        thread.join(1)
        return outcome

    def close(self):
        self.server.stop()


class RpcServerTest(unittest.TestCase):
    def setUp(self):
        self.h = Harness()

    def tearDown(self):
        self.h.close()

    def test_a_command_round_trip_runs_on_the_pumping_thread(self):
        outcome = self.h.call("ping", {"a": 1})
        self.assertEqual(outcome.get("result"), {"cmd": "ping", "args": {"a": 1}})
        self.assertEqual(set(self.h.thread_ids), set([threading.current_thread().ident]),
                         "the network thread never runs a command")
        self.assertTrue(self.h.notified.is_set(), "the main thread is woken when a command waits")

    def test_nothing_runs_until_the_main_thread_pumps(self):
        outcome = {}
        client = rpc.RpcClient(self.h.port, KEY, timeout=1.0)
        thread = threading.Thread(target=lambda: outcome.update(error=self._catch(lambda: client.call("ping"))))
        thread.start()
        thread.join(1.5)
        self.assertEqual(self.h.calls, [], "no pump, no execution")
        client.close()

    @staticmethod
    def _catch(function):
        try:
            return function()
        except rpc.RpcError as exc:
            return exc

    def test_wrong_key_is_refused(self):
        outcome = self.h.call("ping", key="f" * 32)
        self.assertIsInstance(outcome.get("error"), rpc.RpcError)
        self.assertEqual(self.h.calls, [])

    def test_five_wrong_keys_lock_the_logins(self):
        for _ in range(rpc.LOGIN_FAILURES):
            self.h.call("ping", key="wrong")
        outcome = self.h.call("ping")   # even the right key waits now
        self.assertIn("error", outcome)
        self.assertEqual(self.h.calls, [])

    def test_the_key_is_read_at_every_login(self):
        self.assertIn("result", self.h.call("ping"))
        self.h.key = "a" * 32   # "Generate New Access Key"
        self.assertIn("error", self.h.call("ping", key=KEY))
        self.assertIn("result", self.h.call("ping", key="a" * 32))

    def test_a_command_before_login_is_refused(self):
        sock = socket.create_connection(("127.0.0.1", self.h.port), 2)
        decoder = rpc.Decoder(rpc.MAX_FRAME)
        challenge = decoder.feed(sock.recv(4096))[0]
        self.assertEqual(challenge["t"], "challenge")
        sock.sendall(rpc.encode({"t": "call", "id": 1, "cmd": "ping", "args": {}}))
        reply = decoder.feed(sock.recv(4096))[0]
        self.assertEqual(reply["t"], "error")
        self.assertEqual(self.h.calls, [])
        sock.close()

    def test_a_big_frame_before_login_closes_the_connection(self):
        sock = socket.create_connection(("127.0.0.1", self.h.port), 2)
        sock.recv(4096)
        sock.sendall(struct.pack(">I", rpc.MAX_PRE_AUTH_FRAME + 1))
        sock.settimeout(2)
        data = b""
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
        except socket.timeout:
            self.fail("the server kept the connection open")
        sock.close()

    def test_garbage_does_not_hurt_the_server(self):
        sock = socket.create_connection(("127.0.0.1", self.h.port), 2)
        sock.sendall(b"\xff\xff\xff\xff not a frame at all")
        sock.close()
        self.assertIn("result", self.h.call("ping"), "the server still answers")

    def test_a_failing_command_reports_its_error(self):
        outcome = self.h.call("boom")
        self.assertIsInstance(outcome["error"], rpc.RpcError)
        self.assertIn("it broke", str(outcome["error"]))
        self.assertIn("result", self.h.call("ping"), "and keeps serving")

    def test_results_that_json_cannot_write_become_text(self):
        original = self.h.execute
        self.h.execute = lambda cmd, args: {"v": object()}
        self.assertIn("object", self.h.call("ping")["result"]["v"])
        self.h.execute = original

    def test_an_oversized_result_is_refused(self):
        outcome = self.h.call("big")
        self.assertIn("too large", str(outcome["error"]))

    def test_one_command_at_a_time_per_client(self):
        client = rpc.RpcClient(self.h.port, KEY, timeout=3.0)
        client._send({"t": "call", "id": 1, "cmd": "ping", "args": {}})
        client._send({"t": "call", "id": 2, "cmd": "ping", "args": {}})
        second = client._read_message(2.0)   # the refusal comes at once, nothing was pumped yet
        self.assertEqual((second["id"], second["ok"]), (2, False))
        self.assertIn("busy", second["error"])
        client.close()

    def test_invalid_calls_are_refused_not_run(self):
        client = rpc.RpcClient(self.h.port, KEY, timeout=3.0)
        client._send({"t": "call", "id": 1, "cmd": 5, "args": {}})
        self.assertFalse(client._read_message(2.0)["ok"])
        client._send({"t": "call", "id": 2, "cmd": "ping", "args": [1]})
        self.assertFalse(client._read_message(2.0)["ok"])
        self.assertEqual(self.h.calls, [])
        client.close()

    def test_status_and_stop(self):
        self.assertEqual(self.h.server.status_text(), "Listening on 127.0.0.1:%d (no client yet)" % self.h.port)
        client = rpc.RpcClient(self.h.port, KEY, timeout=3.0)
        time.sleep(0.3)
        self.assertIn("1 client connected", self.h.server.status_text())
        self.h.server.stop()
        self.assertEqual(self.h.server.status_text(), "Off")
        with self.assertRaises(Exception):
            socket.create_connection(("127.0.0.1", self.h.port), 0.5)
        client.close()

    def test_a_busy_port_is_reported_not_raised(self):
        other = rpc.RpcServer(key_of=lambda: KEY)
        self.assertFalse(other.start(self.h.port))
        self.assertTrue(other.status_text().startswith("Error: port %d" % self.h.port))
        self.assertFalse(other.running)

    def test_restart_on_another_port(self):
        port = free_port()
        self.assertTrue(self.h.server.start(port))
        self.h.port = port
        self.assertIn("result", self.h.call("ping"))

    def test_it_listens_on_this_computer_only(self):
        self.assertEqual(self.h.server._listener.getsockname()[0], "127.0.0.1")


class RequestTimeoutTest(unittest.TestCase):
    def test_a_command_that_waited_too_long_is_dropped_not_run_late(self):
        harness = Harness(timeout=0.15)
        try:
            outcome = harness.call("ping", delay=0.5)
            self.assertIn("timed out", str(outcome["error"]))
            self.assertEqual(harness.calls, [])
            self.assertIn("result", harness.call("ping"), "a prompt main thread is fine")
        finally:
            harness.close()


class SpecTest(unittest.TestCase):
    def test_every_tool_has_a_schema_and_a_timeout(self):
        names = [t["name"] for t in mcp_spec.TOOLS]
        self.assertEqual(len(names), len(set(names)))
        for tool in mcp_spec.TOOLS:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertTrue(tool["description"])
            self.assertGreater(tool["timeout"], 0)
        self.assertIn("run_script", mcp_spec.PYTHON_TOOLS)


class BridgeTest(unittest.TestCase):
    """The stdio MCP server, started like an AI app starts it."""

    def setUp(self):
        port = free_port()
        self.config = os.path.join(os.environ.get("TEMP", "/tmp"), "c4d_collab_mcp_test_%d.json" % port)
        with open(self.config, "w") as handle:
            json.dump({"port": port, "key": KEY}, handle)
        # The bridge starts first: on Windows Python 2.7 a child inherits the parent's sockets, and an
        # inherited listener would keep the port open after the server closed it.
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(PLUGIN, "mcp_stdio_bridge.py"), "--config", self.config],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.h = Harness(port=port)
        self.stop = threading.Event()
        self.pumper = threading.Thread(target=self._pump)
        self.pumper.start()

    def tearDown(self):
        self.proc.stdin.close()
        self.proc.wait()
        self.proc.stdout.close()
        self.proc.stderr.close()
        self.stop.set()
        self.pumper.join(2)
        self.h.close()
        os.remove(self.config)

    def _pump(self):
        while not self.stop.is_set():
            self.h.server.pump(self.h.execute)
            time.sleep(0.005)

    def ask(self, method, params=None, ident=1):
        self.proc.stdin.write((json.dumps({"jsonrpc": "2.0", "id": ident, "method": method,
                                           "params": params or {}}) + "\n").encode("utf-8"))
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline().decode("utf-8"))

    def test_the_handshake_and_the_tool_list(self):
        reply = self.ask("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})
        self.assertEqual(reply["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(reply["result"]["serverInfo"]["name"], mcp_spec.SERVER_NAME)
        self.proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')   # no answer
        self.proc.stdin.flush()
        tools = self.ask("tools/list", ident=2)["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], [t["name"] for t in mcp_spec.TOOLS])

    def test_a_tool_call_reaches_cinema_and_comes_back(self):
        reply = self.ask("tools/call", {"name": "get_info", "arguments": {"x": 1}})["result"]
        self.assertFalse(reply["isError"])
        self.assertEqual(json.loads(reply["content"][0]["text"]), {"cmd": "get_info", "args": {"x": 1}})

    def test_errors_come_back_as_tool_errors(self):
        reply = self.ask("tools/call", {"name": "ping", "arguments": {}})["result"]
        self.assertFalse(reply["isError"])
        self.h.key = "b" * 32   # the key changed in Cinema 4D: the bridge's config is stale
        reply = self.ask("tools/call", {"name": "ping", "arguments": {}}, ident=2)["result"]
        self.assertTrue(reply["isError"])
        self.assertIn("access key", reply["content"][0]["text"])

    def write_config(self, **extra):
        data = {"port": self.h.port, "key": KEY}
        data.update(extra)
        with open(self.config, "w") as handle:
            json.dump(data, handle)

    def test_only_the_enabled_tools_are_listed(self):
        self.write_config(enabled_tools=["ping", "get_log"])
        self.ask("initialize", {})
        tools = self.ask("tools/list", ident=2)["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["ping", "get_log"])
        self.write_config(enabled_tools=[])
        self.assertEqual(self.ask("tools/list", ident=3)["result"]["tools"], [])
        self.write_config()   # an older file without the list: everything
        self.assertEqual(len(self.ask("tools/list", ident=4)["result"]["tools"]), len(mcp_spec.TOOLS))

    def test_all_tools_are_listed_while_cinema_is_not_running(self):
        os.remove(self.config)
        self.assertEqual(len(self.ask("tools/list")["result"]["tools"]), len(mcp_spec.TOOLS))
        with open(self.config, "w") as handle:   # so tearDown has a file to remove
            handle.write("{}")

    def test_unknown_tool_and_method(self):
        self.assertTrue(self.ask("tools/call", {"name": "format_disk", "arguments": {}})["result"]["isError"])
        self.assertEqual(self.ask("resources/list", ident=3)["error"]["code"], -32601)
        self.assertEqual(self.ask("ping", ident=4)["result"], {})

    def test_cinema_not_running_gives_a_helpful_message(self):
        self.h.close()
        reply = self.ask("tools/call", {"name": "ping", "arguments": {}})["result"]
        self.assertTrue(reply["isError"])
        self.assertIn("Cinema 4D is not reachable", reply["content"][0]["text"])
        self.h.server = rpc.RpcServer(key_of=lambda: KEY)   # so tearDown has something to stop

    def test_garbage_on_stdin_is_a_parse_error_not_a_crash(self):
        self.proc.stdin.write(b"this is not json\n")
        self.proc.stdin.flush()
        self.assertEqual(json.loads(self.proc.stdout.readline().decode("utf-8"))["error"]["code"], -32700)
        self.assertEqual(self.ask("ping", ident=5)["result"], {})


if __name__ == "__main__":
    unittest.main()
