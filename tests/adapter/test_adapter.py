# -*- coding: utf-8 -*-
"""Adapter tests with a fake c4d module (Python 2.7 and 3.x)."""
from __future__ import absolute_import, division, print_function, unicode_literals

import os
import socket
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "plugin", "c4d_collab"))
sys.path.insert(0, HERE)
sys.path.insert(0, PLUGIN_DIR)

import fake_c4d  # noqa: E402


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class AdapterTestCase(unittest.TestCase):
    def setUp(self):
        fake_c4d.uninstall()
        self.c4d = fake_c4d.install(getattr(self, "profile", None))
        from c4d_collab_lib.c4d_adapter import bridge, compat, plugin_ids, ui_dialog
        self.bridge, self.compat, self.ids, self.ui = bridge, compat, plugin_ids, ui_dialog

    def tearDown(self):
        self.bridge.shutdown()
        self.bridge._SESSION = None
        fake_c4d.uninstall()


class PluginLoadTest(AdapterTestCase):
    def test_pyp_registers_both_plugins(self):
        # Execute like Cinema 4D does: module globals stay alive afterwards
        # (runpy.run_path would wipe them on Python 2).
        path = os.path.join(PLUGIN_DIR, "c4d_collab.pyp")
        namespace = {"__name__": "__main__", "__file__": path}
        with open(path, "rb") as handle:
            exec(compile(handle.read(), path, "exec"), namespace)
        kinds = sorted(kind for kind, _, _ in self.c4d.registered)
        # The developer self-test command only exists in checkouts with selftest.enabled.
        dev = os.path.exists(os.path.join(PLUGIN_DIR, "selftest.enabled"))
        self.assertEqual(kinds, ["command"] * (2 if dev else 1) + ["message", "message", "object", "preference"])
        self.assertTrue(namespace["PluginMessage"](self.c4d.C4DPL_ENDACTIVITY, None))


class CompatTest(AdapterTestCase):
    def test_native_str(self):
        self.assertIsInstance(self.compat.native_str("Ayşe"), str)
        self.assertIsInstance(self.compat.native_str(None), str)
        self.assertEqual(self.compat.from_c4d_str(self.compat.native_str("Ayşe")), "Ayşe")

    def test_uid_roundtrip_modern_and_r19(self):
        for byteseq_only in (False, True):
            atom = fake_c4d.FakeAtom(byteseq_only=byteseq_only)
            self.assertIsNone(self.compat.read_uid(atom))
            uid = self.compat.new_uid()
            self.assertTrue(self.compat.write_uid(atom, uid))
            self.assertEqual(self.compat.read_uid(atom), uid)

    def test_doc_dirty_detailed_and_fallback(self):
        doc = fake_c4d.FakeDocument()
        doc.hdirty[self.c4d.HDIRTYFLAGS_ANIMATION] = 7
        self.assertTrue(self.compat.HAS_DETAILED_HDIRTY)
        self.assertEqual(self.compat.doc_dirty(doc)["ANIMATION"], 7)
        self.compat.HAS_DETAILED_HDIRTY = False  # behave like R19
        doc.hdirty[self.c4d.HDIRTYFLAGS_ALL] = 3
        self.assertEqual(set(self.compat.doc_dirty(doc).values()), {3})


class DialogFlowTest(AdapterTestCase):
    def pump_until(self, condition, others=(), timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.bridge.pump()
            for other in others:
                other.pump()
            if condition():
                return
            time.sleep(0.01)
        raise AssertionError("condition not reached")

    def make_dialog(self):
        dialog = self.ui.CollabDialog()
        self.assertTrue(dialog.CreateLayout())
        self.assertTrue(dialog.InitValues())
        return dialog

    def fill(self, dialog, name="Ayşe", address="", port=None, password="secret"):
        dialog.SetString(self.ui.ID_NAME, self.compat.native_str(name))
        dialog.SetString(self.ui.ID_ADDRESS, self.compat.native_str(address))
        dialog.SetInt32(self.ui.ID_PORT, port or free_port())
        dialog.SetString(self.ui.ID_PASSWORD, self.compat.native_str(password))

    def test_short_password_is_refused(self):
        dialog = self.make_dialog()
        self.fill(dialog, password="abc")
        dialog.Command(self.ui.ID_HOST, None)
        self.assertEqual(len(self.c4d.message_dialogs), 1)
        self.assertFalse(self.bridge.session().active)

    def test_host_then_guest_joins_then_leave(self):
        from c4d_collab_lib.core.session import CollabSession
        dialog = self.make_dialog()
        port = free_port()
        self.fill(dialog, port=port)
        message_data = self.bridge.CollabMessageData()
        self.assertEqual(message_data.GetTimer(), 0)

        dialog.Command(self.ui.ID_HOST, None)
        session = self.bridge.session()
        self.assertTrue(session.is_host)
        self.assertEqual(message_data.GetTimer(), self.bridge.TIMER_MS)
        # Password is wiped from the UI and never saved.
        self.assertEqual(dialog.GetString(self.ui.ID_PASSWORD), "")
        saved = self.c4d.world_data[self.ids.SETTINGS]
        self.assertNotIn(self.compat.native_str("secret"), saved._data.values())
        self.assertEqual(self.compat.from_c4d_str(saved.GetString(self.ui.ID_NAME)), "Ayşe")

        guest = CollabSession()
        guest.join("127.0.0.1", port, "Guest", password="secret")
        try:
            self.pump_until(lambda: len(session.users()) == 2, others=[guest])
            self.assertTrue(self.c4d.special_events)  # the network thread woke the main thread
            dialog.refresh()
            users_text = self.compat.from_c4d_str(dialog.GetString(self.ui.ID_USERS))
            self.assertIn("Guest", users_text)
            self.assertIn("Ayşe", users_text)
            self.assertIn(str(port), self.compat.from_c4d_str(dialog.GetString(self.ui.ID_SHARE)))
            self.assertFalse(dialog.enabled[self.ui.ID_HOST])
            self.assertTrue(dialog.enabled[self.ui.ID_LEAVE])

            dialog.Command(self.ui.ID_LEAVE, None)
            self.assertFalse(session.active)
            self.pump_until(lambda: not guest.active, others=[guest])
            self.assertTrue(dialog.enabled[self.ui.ID_HOST])
        finally:
            guest.leave()

    def test_join_wrong_password_shows_in_log(self):
        from c4d_collab_lib.core.session import CollabSession
        host = CollabSession()
        port = free_port()
        host.host("Host", port, password="secret", bind="127.0.0.1")
        try:
            dialog = self.make_dialog()
            self.fill(dialog, address="127.0.0.1", port=port, password="nope")
            dialog.Command(self.ui.ID_JOIN, None)
            self.pump_until(lambda: not self.bridge.session().active, others=[host])
            dialog.refresh()
            self.assertIn("wrong password", self.compat.from_c4d_str(dialog.GetString(self.ui.ID_LOG)))
        finally:
            host.leave()


if __name__ == "__main__":
    unittest.main()
