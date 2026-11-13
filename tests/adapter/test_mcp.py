# -*- coding: utf-8 -*-
"""The "MCP" page of Edit > Preferences, its server and its tools (fake c4d). Python 2.7 and 3.x."""
from __future__ import absolute_import, division, print_function, unicode_literals

import io
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest

import fake_scene as fs
from test_adapter import PLUGIN_DIR, AdapterTestCase, free_port

RES = os.path.join(PLUGIN_DIR, "res")
ENV = ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "CODEX_HOME")


def load_pyp(c4d):
    path = os.path.join(PLUGIN_DIR, "c4d_collab.pyp")
    namespace = {"__name__": "__main__", "__file__": path}
    with open(path, "rb") as handle:
        exec(compile(handle.read(), path, "exec"), namespace)
    return namespace


def text_of(value):
    return value.decode("utf-8") if isinstance(value, bytes) else value


class McpCase(AdapterTestCase):
    def setUp(self):
        AdapterTestCase.setUp(self)
        from c4d_collab_lib.c4d_adapter import mcp_actions, mcp_prefs, mcp_service, mcp_tools
        from c4d_collab_lib.core import clipboard, mcp_clients, rpc
        self.prefs, self.service, self.tools, self.rpc = mcp_prefs, mcp_service, mcp_tools, rpc
        self.actions, self.clients, self.clipboard = mcp_actions, mcp_clients, clipboard
        self.settings = mcp_prefs.Settings
        # a private preferences folder and home: nothing here may touch the real configs of AI apps
        self.dir = tempfile.mkdtemp(prefix="c4d_collab_mcp_test_")
        self.home = os.path.join(self.dir, "home")
        os.makedirs(self.home)
        self.saved_env = dict((name, os.environ.get(name)) for name in ENV)
        os.environ["USERPROFILE"] = os.environ["HOME"] = self.home
        os.environ["APPDATA"] = os.path.join(self.home, "AppData", "Roaming")
        os.environ["LOCALAPPDATA"] = os.path.join(self.home, "AppData", "Local")
        os.environ.pop("CODEX_HOME", None)
        self.prefs_dir = os.path.join(self.dir, "prefs")
        os.makedirs(self.prefs_dir)
        self.c4d.storage.GeGetC4DPath = lambda which: str(self.prefs_dir)
        self.copied = []
        self.clipboard_works = True
        self.original_set_text = clipboard.set_text
        clipboard.set_text = lambda text: (self.copied.append(text), self.clipboard_works)[1]
        mcp_service.forget_client_status()
        self.page = mcp_prefs.McpPreference()

    def tearDown(self):
        self.service.shutdown()
        self.clipboard.set_text = self.original_set_text
        for name, value in self.saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        AdapterTestCase.tearDown(self)
        logging.getLogger("c4d_collab").handlers = []   # the log file of this test is closed
        shutil.rmtree(self.dir, ignore_errors=True)

    # Driving the page like Cinema 4D does -----------------------------------------------------

    def desc_id(self, ident):
        return self.c4d.DescID(self.c4d.DescLevel(ident))

    def get(self, ident):
        result = self.page.GetDParameter(None, self.desc_id(ident), 0)
        return text_of(result[1] if isinstance(result, tuple) else result) if result is not False else False

    def set(self, ident, value):
        result = self.page.SetDParameter(None, self.desc_id(ident), value, 0)
        return result[0] if isinstance(result, tuple) else result

    def press(self, ident):
        return self.page.Message(None, self.c4d.MSG_DESCRIPTION_COMMAND, {"id": self.desc_id(ident)})

    def enabled(self, ident):
        return self.page.GetDEnabling(None, self.desc_id(ident), None, 0, None)

    def switch_on(self, port=None):
        self.page.Init(None)
        self.c4d.question_answers[:] = [True]
        self.set(self.prefs.MCP_PORT, port or free_port())
        self.set(self.prefs.MCP_ENABLE, True)
        return self.settings.port()

    def pump(self):
        self.service.McpMessageData().CoreMessage(self.ids.MCP_MESSAGE, None)

    def config(self):
        with io.open(os.path.join(self.prefs_dir, "c4d_collab_mcp.json"), encoding="utf-8") as handle:
            return json.load(handle)

    def call(self, cmd, args=None, timeout=5.0, delay=0.0, key=None):
        """A client in a thread; this thread plays the main thread and pumps (after `delay` seconds)."""
        config = self.config()
        outcome = {}

        def work():
            try:
                client = self.rpc.RpcClient(config["port"], key or config["key"], timeout=timeout)
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
            self.pump()
            time.sleep(0.005)
        thread.join(1)
        return outcome

    def scene(self):
        doc = fs.BaseDocument()
        doc.name = str("Scene.c4d")
        root = fs.BaseObject(fs.Onull)
        root.SetName(fs.native("Root"))
        doc.InsertObject(root)
        cube = fs.BaseObject(fs.Ocube)
        cube.SetName(fs.native("Cube"))
        doc.InsertObject(cube, root)
        cube[fs.REL_POSITION] = fs.Vector(1, 2, 3)
        cube.MakeTag(fs.Tphong).SetName(fs.native("Phong"))
        other = fs.BaseObject(fs.Onull)
        other.SetName(fs.native("Other"))
        doc.InsertObject(other)
        self.c4d.documents.InsertBaseDocument(doc)
        self.c4d.documents.SetActiveDocument(doc)
        cube.SetBit(fs.BIT_ACTIVE)
        return doc

    def install_cursor(self):
        os.makedirs(os.path.join(self.home, ".cursor"))
        return self.clients.config_file(self.clients.CURSOR)

    def choose(self, client):
        self.set(self.prefs.MCP_CLIENT, self.clients.CLIENTS.index(client))


class ResourceTest(McpCase):
    def header(self):
        with io.open(os.path.join(RES, "description", "mcpprefs.h"), encoding="utf-8") as handle:
            return dict((name, int(value)) for name, value in re.findall(r"COLLABMCP_(\w+)\s*=\s*(\d+)", handle.read()))

    def test_parameter_ids_match_the_resource_header(self):
        header = self.header()
        for name, value in header.items():
            if name.endswith("_GROUP") or name == "DUMMY" or name.startswith("CLIENT_") and name != "CLIENT_STATUS":
                continue
            self.assertEqual(getattr(self.prefs, "MCP_" + name), value, name)
        for index, client in enumerate(self.clients.CLIENTS):
            self.assertEqual(header["CLIENT_" + client.upper()], index, "the dropdown order is the client order")
        self.assertEqual(len(set(header.values())), len(header), "ids in the header are unique")

    def test_the_first_versions_ids_keep_their_meaning(self):
        # these were stored in users' preferences by the first version of the page
        self.assertEqual((self.prefs.MCP_ENABLE, self.prefs.MCP_PORT, self.prefs.MCP_ALLOW_PYTHON, self.prefs.MCP_TOKEN),
                         (1000, 1001, 1002, 1005))
        for ident in (1003, 1004, 1006):
            self.assertNotIn(ident, list(self.prefs.STORED) + list(self.prefs.COMPUTED))
        self.assertEqual(self.prefs.STORED[1005][1], "str")

    def test_every_symbol_of_the_description_has_a_string(self):
        with io.open(os.path.join(RES, "description", "mcpprefs.res"), encoding="utf-8") as handle:
            description = handle.read()
        with io.open(os.path.join(RES, "strings_us", "description", "mcpprefs.str"), encoding="utf-8") as handle:
            strings = handle.read()
        self.assertIn("CONTAINER mcpprefs", description)
        self.assertIn("STRINGTABLE mcpprefs", strings)
        self.assertTrue(re.search(r'\bmcpprefs\s+"MCP";', strings), "the page's name")
        header = self.header()
        used = set(re.findall(r"\b(COLLABMCP_[A-Z_]+)\b", description))
        for symbol in used:
            self.assertIn(symbol[len("COLLABMCP_"):], header, symbol + " is missing in the header")
            self.assertTrue(re.search(r'\b%s\s+"[^"]+";' % symbol, strings), symbol + " has no string")
        for ident in list(self.prefs.STORED) + list(self.prefs.COMPUTED) + list(self.prefs.BUTTONS):
            if ident != self.prefs.MCP_RESTART_HINT:
                self.assertIn(ident, header.values(), ident)

    def test_buttons_and_the_dropdown_are_what_the_code_expects(self):
        with io.open(os.path.join(RES, "description", "mcpprefs.res"), encoding="utf-8") as handle:
            description = handle.read()
        buttons = re.findall(r"BUTTON COLLABMCP_(\w+)", description)
        self.assertEqual(sorted(buttons), sorted(["COPY_TOKEN", "REGENERATE_TOKEN", "UPDATE_CLIENT", "REMOVE_CLIENT",
                                                  "COPY_CONFIG"]))
        header = self.header()
        self.assertEqual(sorted(header[b] for b in buttons), sorted(self.prefs.BUTTONS))
        cycle = re.search(r"CYCLE\s*\{([^}]*)\}", description).group(1)
        self.assertEqual(re.findall(r"COLLABMCP_CLIENT_(\w+);", cycle), [c.upper() for c in self.clients.CLIENTS])
        for name in ("SERVER", "CLIENTS", "SECURITY", "TOOLSET"):
            self.assertIn("GROUP COLLABMCP_%s_GROUP" % name, description)

    def test_plugin_ids_are_unique(self):
        values = [getattr(self.ids, n) for n in dir(self.ids) if n.isupper() and isinstance(getattr(self.ids, n), int)
                  and n != "SETTINGS"]   # SETTINGS is the command's id on purpose (see plugin_ids.py)
        self.assertEqual(len(values), len(set(values)))


class RegistrationTest(McpCase):
    def test_the_pyp_registers_the_page_and_the_message_plugin(self):
        load_pyp(self.c4d)
        kinds = [kind for kind, _, _ in self.c4d.registered]
        self.assertEqual(kinds.count("preference"), 1)
        self.assertEqual(self.c4d.preference_pages, [("MCP", "mcpprefs", 0, 0)])
        self.assertIn(self.ids.MCP_MESSAGE, [ident for kind, ident, _ in self.c4d.registered if kind == "message"])
        self.assertIn(self.ids.MCP_PREFS, [ident for kind, ident, _ in self.c4d.registered if kind == "preference"])

    def test_collab_still_loads_when_the_page_cannot_be_registered(self):
        def refuse(*args):
            raise RuntimeError("no page for you")
        self.c4d.plugins.RegisterPreferencePlugin = refuse
        load_pyp(self.c4d)
        kinds = [kind for kind, _, _ in self.c4d.registered]
        self.assertIn("command", kinds)
        self.assertIn("object", kinds)
        self.assertNotIn("preference", kinds)

    def test_old_versions_without_preference_pages_just_skip_the_page(self):
        del self.c4d.plugins.PreferenceData
        del self.c4d.plugins.RegisterPreferencePlugin
        for name in [n for n in sys.modules if n.startswith("c4d_collab_lib.c4d_adapter")]:
            del sys.modules[name]   # import the adapter again, now on a Cinema 4D without preference pages
        load_pyp(self.c4d)
        kinds = [kind for kind, _, _ in self.c4d.registered]
        self.assertNotIn("preference", kinds)
        self.assertIn("command", kinds)

    def test_the_page_name_and_resource_are_native_strings(self):
        load_pyp(self.c4d)   # the fake refuses unicode on Python 2 like R17-R22
        self.assertTrue(self.c4d.preference_pages)

    def test_closing_cinema_stops_the_server(self):
        namespace = load_pyp(self.c4d)
        self.switch_on()
        self.assertTrue(self.service.server().running)
        namespace["PluginMessage"](self.c4d.C4DPL_ENDACTIVITY, None)
        self.assertFalse(self.service.server().running)

    def test_a_server_left_on_starts_again_with_cinema_without_asking(self):
        namespace = load_pyp(self.c4d)
        self.switch_on()
        self.service.shutdown()
        self.assertFalse(self.service.server().running)
        asked = len(self.c4d.question_dialogs)
        namespace["PluginMessage"](self.c4d.C4DPL_PROGRAM_STARTED, None)
        self.assertTrue(self.service.server().running)
        self.assertEqual(len(self.c4d.question_dialogs), asked, "the question is for the user switching it on")


class PageTest(McpCase):
    def test_the_defaults_are_safe(self):
        self.page.Init(None)
        self.assertIs(self.settings.enabled(), False)
        self.assertIs(self.settings.allow_python(), False)
        self.assertEqual(self.settings.port(), 47921)
        self.assertEqual(self.settings.timeout(), 30.0)
        self.assertIs(self.settings.log_calls(), True)
        self.assertIs(self.settings.console_rejections(), False)
        self.assertEqual(self.settings.groups(), ["basics", "hierarchy", "entities", "system"])
        self.assertEqual(self.settings.client(), "claude_desktop")
        self.assertFalse(self.service.server().running, "off until the user switches it on")

    def test_the_defaults_hold_before_the_page_was_ever_initialised(self):
        self.assertIs(self.settings.log_calls(), True)
        self.assertEqual(len(self.settings.groups()), 4)
        self.assertEqual(self.settings.timeout(), 30.0)

    def test_the_description_is_loaded_by_name(self):
        description = self.c4d.Description()
        result = self.page.GetDDescription(None, description, 0)
        self.assertTrue(result[0] if isinstance(result, tuple) else result)
        self.assertEqual(description.loaded, ["mcpprefs"])

    def test_enabling_asks_first_and_no_leaves_it_off(self):
        self.page.Init(None)
        self.set(self.prefs.MCP_PORT, free_port())
        self.c4d.question_answers[:] = [False]
        self.set(self.prefs.MCP_ENABLE, True)
        self.assertEqual(len(self.c4d.question_dialogs), 1)
        self.assertIn("Enable the MCP server?", self.c4d.question_dialogs[0])
        self.assertIn("Python", self.c4d.question_dialogs[0])
        self.assertFalse(self.settings.enabled())
        self.assertFalse(self.service.server().running)
        self.c4d.question_answers[:] = [True]
        self.set(self.prefs.MCP_ENABLE, True)
        self.assertTrue(self.settings.enabled())
        self.assertTrue(self.service.server().running)

    def test_switching_off_does_not_ask(self):
        self.switch_on()
        asked = len(self.c4d.question_dialogs)
        self.set(self.prefs.MCP_ENABLE, False)
        self.assertEqual(len(self.c4d.question_dialogs), asked)
        self.assertFalse(self.service.server().running)
        self.assertEqual(self.get(self.prefs.MCP_STATUS), "Off")
        self.assertFalse(os.path.exists(os.path.join(self.prefs_dir, "c4d_collab_mcp.json")),
                         "the token is not left lying around while the server is off")

    def test_status_host_and_port(self):
        port = self.switch_on()
        self.assertIn("127.0.0.1:%d" % port, self.get(self.prefs.MCP_STATUS))
        self.assertEqual(self.get(self.prefs.MCP_HOST), "127.0.0.1")
        self.assertEqual(self.get(self.prefs.MCP_PORT), port)

    def test_the_port_is_kept_inside_the_allowed_range(self):
        self.page.Init(None)
        self.set(self.prefs.MCP_PORT, 80)
        self.assertEqual(self.settings.port(), 1024)
        self.set(self.prefs.MCP_PORT, 99999)
        self.assertEqual(self.settings.port(), 65535)

    def test_the_timeout_is_kept_inside_its_range(self):
        self.page.Init(None)
        self.set(self.prefs.MCP_TIMEOUT, 0.0)
        self.assertEqual(self.settings.timeout(), 0.1)
        self.set(self.prefs.MCP_TIMEOUT, 99999.0)
        self.assertEqual(self.settings.timeout(), 3600.0)
        self.set(self.prefs.MCP_TIMEOUT, 12.5)
        self.assertEqual(self.get(self.prefs.MCP_TIMEOUT), 12.5)

    def test_changing_the_port_restarts_on_the_new_one(self):
        self.switch_on()
        second = free_port()
        self.set(self.prefs.MCP_PORT, second)
        self.assertEqual(self.service.server().port, second)
        self.assertEqual(self.config()["port"], second)

    def test_a_busy_port_shows_an_error_and_nothing_breaks(self):
        port = self.switch_on()
        other = self.rpc.RpcServer(key_of=lambda: "k")
        self.set(self.prefs.MCP_ENABLE, False)
        self.assertTrue(other.start(port))
        try:
            self.c4d.question_answers[:] = [True]
            self.set(self.prefs.MCP_ENABLE, True)
            self.assertTrue(self.compat.from_c4d_str(self.get(self.prefs.MCP_STATUS)).startswith("Error: port %d" % port))
        finally:
            other.stop()

    def test_the_token_is_masked_until_show_token_is_on(self):
        self.switch_on()
        token = self.settings.token()
        self.assertEqual(len(token), 32)
        hidden = self.get(self.prefs.MCP_TOKEN)
        self.assertEqual(hidden, "*" * 24 + token[-4:])
        self.assertNotIn(token[:-4], hidden)
        self.set(self.prefs.MCP_SHOW_TOKEN, True)
        self.assertEqual(self.get(self.prefs.MCP_TOKEN), token)
        self.set(self.prefs.MCP_SHOW_TOKEN, False)
        self.assertEqual(self.get(self.prefs.MCP_TOKEN), hidden)

    def test_read_only_fields_ignore_what_is_typed(self):
        self.switch_on()
        token = self.settings.token()
        for ident in (self.prefs.MCP_TOKEN, self.prefs.MCP_STATUS, self.prefs.MCP_HOST, self.prefs.MCP_CONFIG_FILE,
                      self.prefs.MCP_CLIENT_STATUS):
            self.set(ident, "hacked")
        self.assertEqual(self.settings.token(), token)
        self.assertEqual(self.get(self.prefs.MCP_HOST), "127.0.0.1")
        self.assertNotEqual(self.get(self.prefs.MCP_STATUS), "hacked")

    def test_unknown_values_and_a_failing_service_never_raise(self):
        self.assertFalse(self.page.GetDParameter(None, self.desc_id(5), 0))
        self.assertFalse(self.page.SetDParameter(None, self.desc_id(5), 1, 0))
        self.assertFalse(self.page.GetDParameter(None, self.desc_id(self.prefs.MCP_COPY_TOKEN), 0), "a button has no value")
        self.service.status_text = lambda: 1 / 0
        self.assertFalse(self.page.GetDParameter(None, self.desc_id(self.prefs.MCP_STATUS), 0))

    def test_values_survive_in_the_world_container(self):
        self.switch_on()
        self.set(self.prefs.MCP_ALLOW_PYTHON, True)
        self.set(self.prefs.MCP_TIMEOUT, 7.5)
        self.set(self.prefs.MCP_TOOLSET_SYSTEM, False)
        container = self.c4d.GetWorldContainerInstance().GetContainerInstance(self.ids.MCP_CONTAINER)
        self.assertIsNotNone(container)
        self.assertTrue(container.GetBool(self.prefs.MCP_ENABLE))
        self.assertTrue(container.GetBool(self.prefs.MCP_ALLOW_PYTHON))
        self.assertEqual(container.GetFloat(self.prefs.MCP_TIMEOUT), 7.5)
        self.assertFalse(container.GetBool(self.prefs.MCP_TOOLSET_SYSTEM))


class GhostingTest(McpCase):
    def test_everything_but_the_switch_is_ghosted_while_the_server_is_off(self):
        self.page.Init(None)
        self.assertTrue(self.enabled(self.prefs.MCP_ENABLE))
        for ident in list(self.prefs.STORED) + list(self.prefs.COMPUTED) + list(self.prefs.BUTTONS):
            if ident != self.prefs.MCP_ENABLE:
                self.assertFalse(self.enabled(ident), ident)

    def test_when_on_inputs_are_live_and_information_stays_ghosted(self):
        self.switch_on()
        for ident in (self.prefs.MCP_PORT, self.prefs.MCP_SHOW_TOKEN, self.prefs.MCP_TIMEOUT, self.prefs.MCP_CLIENT,
                      self.prefs.MCP_COPY_TOKEN, self.prefs.MCP_REGENERATE_TOKEN, self.prefs.MCP_COPY_CONFIG,
                      self.prefs.MCP_ALLOW_PYTHON, self.prefs.MCP_LOG_CALLS, self.prefs.MCP_TOOLSET_BASICS):
            self.assertTrue(self.enabled(ident), ident)
        for ident in (self.prefs.MCP_HOST, self.prefs.MCP_STATUS, self.prefs.MCP_TOKEN, self.prefs.MCP_CLIENT_STATUS,
                      self.prefs.MCP_CONFIG_FILE):
            self.assertFalse(self.enabled(ident), ident)

    def test_update_and_remove_follow_the_selected_client(self):
        self.switch_on()
        self.choose(self.clients.OTHER)
        self.assertFalse(self.enabled(self.prefs.MCP_UPDATE_CLIENT))
        self.assertFalse(self.enabled(self.prefs.MCP_REMOVE_CLIENT))
        self.assertTrue(self.enabled(self.prefs.MCP_COPY_CONFIG), "any client can get its config copied")
        self.choose(self.clients.CURSOR)
        self.service.forget_client_status()
        self.assertFalse(self.enabled(self.prefs.MCP_UPDATE_CLIENT), "Cursor is not installed")
        path = self.install_cursor()
        self.service.forget_client_status()
        self.assertTrue(self.enabled(self.prefs.MCP_UPDATE_CLIENT))
        self.assertFalse(self.enabled(self.prefs.MCP_REMOVE_CLIENT), "nothing to remove yet")
        self.press(self.prefs.MCP_UPDATE_CLIENT)
        self.assertTrue(os.path.isfile(path))
        self.assertTrue(self.enabled(self.prefs.MCP_REMOVE_CLIENT))


class ButtonTest(McpCase):
    def test_buttons_do_nothing_while_the_server_is_off(self):
        self.page.Init(None)
        self.press(self.prefs.MCP_COPY_TOKEN)
        self.press(self.prefs.MCP_REGENERATE_TOKEN)
        self.assertEqual(self.copied, [])
        self.assertEqual(self.c4d.message_dialogs, [])

    def test_copy_token_puts_the_real_token_on_the_clipboard(self):
        self.switch_on()
        self.press(self.prefs.MCP_COPY_TOKEN)
        self.assertEqual(self.copied, [self.settings.token()])
        self.assertIn("copied", self.c4d.status_texts[-1])

    def test_copy_token_without_a_clipboard_says_what_to_do(self):
        self.switch_on()
        self.clipboard_works = False
        self.press(self.prefs.MCP_COPY_TOKEN)
        self.assertIn("Show Token", self.c4d.message_dialogs[-1])
        self.assertNotIn(self.settings.token(), self.c4d.message_dialogs[-1], "the token is not put in a dialog")

    def test_regenerate_token_takes_effect_everywhere(self):
        self.switch_on()
        old = self.settings.token()
        self.assertIn("result", self.call("ping"))
        self.press(self.prefs.MCP_REGENERATE_TOKEN)
        new = self.settings.token()
        self.assertNotEqual(new, old)
        self.assertEqual(self.config()["key"], new, "AI apps read the token from the file: nothing to set up again")
        self.assertIn("error", self.call("ping", key=old), "the old token no longer works")
        self.assertIn("result", self.call("ping"))
        self.assertIn("New access token", self.c4d.status_texts[-1])

    def test_copy_config_gives_each_client_its_own_format(self):
        self.switch_on()
        for client, expected in ((self.clients.CLAUDE_DESKTOP, '"mcpServers"'), (self.clients.OTHER, '"mcpServers"'),
                                 (self.clients.CURSOR, '"mcpServers"'), (self.clients.CODEX, "[mcp_servers.cinema4d]"),
                                 (self.clients.CLAUDE_CODE, "claude mcp add --scope user cinema4d")):
            self.choose(client)
            self.press(self.prefs.MCP_COPY_CONFIG)
            self.assertIn(expected, self.copied[-1], client)
        entry = self.service.client_entry()
        self.assertTrue(entry["args"][-3].endswith("mcp_stdio_bridge.py"))
        self.assertEqual(entry["args"][-2:], ["--config", os.path.join(self.prefs_dir, "c4d_collab_mcp.json")])

    def test_update_client_asks_writes_backs_up_and_explains_the_restart(self):
        self.switch_on()
        path = self.install_cursor()
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write('{"keep": true}')
        self.choose(self.clients.CURSOR)
        self.c4d.question_answers[:] = [False]
        self.press(self.prefs.MCP_UPDATE_CLIENT)
        self.assertIn(path, self.c4d.question_dialogs[-1])
        self.assertIn("backed up", self.c4d.question_dialogs[-1])
        with io.open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), '{"keep": true}', "no means no")
        self.c4d.question_answers[:] = [True]
        self.press(self.prefs.MCP_UPDATE_CLIENT)
        with io.open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["keep"], True)
        self.assertEqual(data["mcpServers"]["cinema4d"]["args"][-2], "--config")
        self.assertEqual(len([n for n in os.listdir(os.path.dirname(path)) if ".bak-" in n]), 1)
        self.assertIn("Restart Cursor", self.c4d.message_dialogs[-1])
        self.assertIn("quit the app completely", self.c4d.message_dialogs[-1])
        self.assertEqual(self.get(self.prefs.MCP_CLIENT_STATUS), "Set up")

    def test_remove_from_client(self):
        self.switch_on()
        path = self.install_cursor()
        self.choose(self.clients.CURSOR)
        self.press(self.prefs.MCP_UPDATE_CLIENT)
        self.assertEqual(self.get(self.prefs.MCP_CLIENT_STATUS), "Set up")
        self.c4d.question_answers[:] = [False]
        self.press(self.prefs.MCP_REMOVE_CLIENT)
        self.assertEqual(self.get(self.prefs.MCP_CLIENT_STATUS), "Set up", "no means no")
        self.c4d.question_answers[:] = [True]
        self.press(self.prefs.MCP_REMOVE_CLIENT)
        self.assertEqual(self.get(self.prefs.MCP_CLIENT_STATUS), "Not set up")
        with io.open(path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"mcpServers": {}})

    def test_a_config_that_cannot_be_parsed_is_reported_not_overwritten(self):
        self.switch_on()
        path = self.install_cursor()
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write("{ broken")
        self.choose(self.clients.CURSOR)
        self.press(self.prefs.MCP_UPDATE_CLIENT)
        self.assertIn("not valid JSON", self.c4d.message_dialogs[-1])
        with io.open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "{ broken")

    def test_claude_code_gets_a_command_to_paste(self):
        self.switch_on()
        self.choose(self.clients.CLAUDE_CODE)
        self.press(self.prefs.MCP_UPDATE_CLIENT)
        self.assertTrue(self.copied[-1].startswith("claude mcp add --scope user cinema4d -- "))
        self.assertIn(self.copied[-1], self.c4d.message_dialogs[-1])
        self.press(self.prefs.MCP_REMOVE_CLIENT)
        self.assertEqual(self.copied[-1], "claude mcp remove --scope user cinema4d")

    def test_other_clients_are_pointed_to_copy_config(self):
        self.switch_on()
        self.choose(self.clients.OTHER)
        self.press(self.prefs.MCP_UPDATE_CLIENT)
        self.assertIn("Copy Config", self.c4d.message_dialogs[-1])

    def test_a_button_that_fails_shows_why_and_logs(self):
        self.switch_on()
        original = self.actions.HANDLERS[self.prefs.MCP_COPY_TOKEN]
        self.actions.HANDLERS[self.prefs.MCP_COPY_TOKEN] = lambda: 1 / 0
        try:
            self.assertTrue(self.press(self.prefs.MCP_COPY_TOKEN))
        finally:
            self.actions.HANDLERS[self.prefs.MCP_COPY_TOKEN] = original
        self.assertIn("Something went wrong", self.c4d.message_dialogs[-1])

    def test_unknown_messages_and_ids_are_ignored(self):
        self.switch_on()
        self.assertTrue(self.page.Message(None, 5, {}))
        self.assertTrue(self.page.Message(None, self.c4d.MSG_DESCRIPTION_COMMAND, {"id": self.desc_id(77)}))
        self.assertTrue(self.page.Message(None, self.c4d.MSG_DESCRIPTION_COMMAND, None))


class ConfigFileTest(McpCase):
    def test_the_config_file_has_what_the_bridge_and_the_ai_apps_need(self):
        port = self.switch_on()
        config = self.config()
        self.assertEqual(config["port"], port)
        self.assertEqual(config["key"], self.settings.token())
        self.assertEqual(config["timeout"], 30.0)
        self.assertEqual(config["enabled_tools"], ["ping", "get_info", "get_hierarchy", "get_object", "get_log"])
        self.assertTrue(os.path.isfile(config["bridge"]))
        self.assertIn("claude mcp add", config["claude_code"])
        self.assertEqual(config["mcp_json"]["mcpServers"]["cinema4d"], config["command"])

    def test_switches_that_the_ai_side_reads_rewrite_the_file(self):
        self.switch_on()
        self.set(self.prefs.MCP_ALLOW_PYTHON, True)
        self.assertIn("run_script", self.config()["enabled_tools"])
        self.assertIs(self.config()["python_execution_allowed"], True)
        self.set(self.prefs.MCP_TOOLSET_HIERARCHY, False)
        self.assertNotIn("get_hierarchy", self.config()["enabled_tools"])
        self.set(self.prefs.MCP_TOOLSET_BASICS, False)
        self.assertEqual(self.config()["enabled_tools"], ["get_object", "get_log", "run_script"])
        self.set(self.prefs.MCP_TIMEOUT, 12.0)
        self.assertEqual(self.config()["timeout"], 12.0)


class ToolsTest(McpCase):
    def test_ping_over_the_wire_runs_on_the_main_thread(self):
        self.switch_on()
        seen = []
        original = self.tools.ping
        self.tools.TOOLS["ping"] = lambda args, ctx: (seen.append(threading.current_thread().ident), original(args, ctx))[1]
        outcome = self.call("ping")
        self.assertTrue(outcome["result"]["pong"])
        self.assertEqual(seen, [threading.current_thread().ident])

    def test_get_info_lists_the_documents(self):
        self.switch_on()
        self.scene()
        info = self.call("get_info")["result"]
        self.assertEqual(info["c4d"], 2023103)
        self.assertFalse(info["python_execution_allowed"])
        self.assertTrue(any(d["active"] and d["name"] == "Scene.c4d" for d in info["documents"]))
        self.assertEqual(info["active"]["objects"], 3)
        self.assertEqual(info["active"]["selected"], ["Cube"])

    def test_get_hierarchy(self):
        self.switch_on()
        self.scene()
        tree = self.call("get_hierarchy")["result"]
        self.assertEqual([o["name"] for o in tree["objects"]], ["Other", "Root"])   # a new object goes on top
        cube = tree["objects"][1]["children"][0]
        self.assertEqual((cube["name"], cube["selected"], cube["tags"][0]["name"]), ("Cube", True, "Phong"))
        self.assertFalse(tree["cut"])
        flat = self.call("get_hierarchy", {"max_depth": 1, "include_tags": False})["result"]
        self.assertTrue(flat["objects"][1]["children_cut"])
        self.assertNotIn("tags", flat["objects"][1])
        self.assertTrue(self.call("get_hierarchy", {"limit": 1})["result"]["cut"])

    def test_get_object(self):
        self.switch_on()
        self.scene()
        info = self.call("get_object", {"name": "Cube"})["result"]
        self.assertEqual((info["parent"], info["position"]), ("Root", [1.0, 2.0, 3.0]))
        self.assertEqual(info["tags"][0]["name"], "Phong")
        self.assertTrue(any(p["value"] == {"V": [1.0, 2.0, 3.0]} for p in info["params"]))
        self.assertNotIn("params", self.call("get_object", {"name": "Cube", "include_params": False})["result"])
        self.assertIn("no object called", str(self.call("get_object", {"name": "Nope"})["error"]))
        self.assertIn("name is required", str(self.call("get_object", {})["error"]))

    def test_bad_arguments_are_refused_with_a_reason(self):
        self.switch_on()
        self.scene()
        self.assertIn("integer", str(self.call("get_hierarchy", {"limit": "many"})["error"]))
        self.assertIn("true or false", str(self.call("get_hierarchy", {"include_tags": 1})["error"]))
        self.assertIn("unknown command", str(self.call("format_disk")["error"]))

    def test_get_log_reads_the_end_of_a_file(self):
        self.switch_on()
        with io.open(os.path.join(self.prefs_dir, "c4d_collab.log"), "w", encoding="utf-8") as handle:
            handle.write("\n".join("line %d" % i for i in range(300)))
        lines = self.call("get_log", {"lines": 3})["result"]["lines"]
        self.assertEqual(lines, ["line 297", "line 298", "line 299"])
        audit = self.call("get_log", {"log": "audit", "lines": 5})["result"]
        self.assertTrue(audit["exists"])
        self.assertIn("get_log", "".join(audit["lines"]))
        self.assertIn("must be", str(self.call("get_log", {"log": "../../secret"})["error"]))

    def test_every_command_is_audited(self):
        self.switch_on()
        self.call("ping")
        self.call("get_object", {"name": "Nope"})
        self.call("run_script", {"code": "result = 1"})
        with io.open(os.path.join(self.prefs_dir, "c4d_collab_mcp_audit.log"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("ping {} -> ok", text)
        self.assertIn("get_object", text)
        self.assertIn("refused", text)
        self.assertIn("run_script", text)

    def test_log_incoming_calls_can_be_switched_off(self):
        self.switch_on()
        self.set(self.prefs.MCP_LOG_CALLS, False)
        self.call("ping")
        self.assertFalse(os.path.exists(os.path.join(self.prefs_dir, "c4d_collab_mcp_audit.log")))
        self.set(self.prefs.MCP_LOG_CALLS, True)
        self.call("ping")
        self.assertTrue(os.path.exists(os.path.join(self.prefs_dir, "c4d_collab_mcp_audit.log")))

    def test_the_key_never_reaches_the_logs(self):
        self.switch_on()
        key = self.settings.token()
        self.call("ping")
        self.call("get_log", {"log": "audit"})
        for name in os.listdir(self.prefs_dir):
            if name.startswith("c4d_collab") and name != "c4d_collab_mcp.json":
                with io.open(os.path.join(self.prefs_dir, name), encoding="utf-8", errors="replace") as handle:
                    self.assertNotIn(key, handle.read(), name)


class ToolsetTest(McpCase):
    def test_a_switched_off_toolset_refuses_its_tools_at_once(self):
        self.switch_on()
        self.scene()
        self.assertIn("result", self.call("get_hierarchy"))
        self.set(self.prefs.MCP_TOOLSET_HIERARCHY, False)
        error = str(self.call("get_hierarchy")["error"])
        self.assertIn("Hierarchy toolset is switched off", error)
        self.assertIn("result", self.call("get_object", {"name": "Cube"}), "the others still work")
        self.set(self.prefs.MCP_TOOLSET_SYSTEM, False)
        self.assertIn("System toolset", str(self.call("get_log")["error"]))
        self.set(self.prefs.MCP_TOOLSET_BASICS, False)
        self.assertIn("Basics toolset", str(self.call("ping")["error"]))

    def test_rejected_calls_can_be_shown_in_the_console(self):
        self.switch_on()
        self.set(self.prefs.MCP_TOOLSET_BASICS, False)
        printed = []
        out = sys.stdout
        sys.stdout = type(str("W"), (object,), {"write": lambda s, t: printed.append(t), "flush": lambda s: None})()
        try:
            self.call("ping")
            self.assertEqual(printed, [], "quiet by default")
            self.set(self.prefs.MCP_CONSOLE_REJECTIONS, True)
            self.call("ping")
        finally:
            sys.stdout = out
        self.assertIn("rejected ping", "".join(printed))


class TimeoutTest(McpCase):
    def test_a_command_that_waited_too_long_for_the_main_thread_is_dropped(self):
        self.switch_on()
        self.set(self.prefs.MCP_TIMEOUT, 0.1)
        ran = []
        original = self.tools.ping
        self.tools.TOOLS["ping"] = lambda args, ctx: (ran.append(1), original(args, ctx))[1]
        outcome = self.call("ping", delay=0.4)   # Cinema 4D "busy" for 0.4 s
        self.assertIn("timed out", str(outcome["error"]))
        self.assertIn("Request Timeout", str(outcome["error"]))
        self.assertEqual(ran, [], "a late command is not run behind the user's back")
        self.set(self.prefs.MCP_TIMEOUT, 30.0)
        self.assertIn("result", self.call("ping", delay=0.4))


class PythonExecutionTest(McpCase):
    def test_it_is_off_by_default_and_says_how_to_turn_it_on(self):
        self.switch_on()
        error = str(self.call("run_script", {"code": "result = 1"})["error"])
        self.assertIn("Allow Python Execution", error)

    def test_it_runs_when_allowed(self):
        self.switch_on()
        self.scene()
        self.set(self.prefs.MCP_ALLOW_PYTHON, True)
        outcome = self.call("run_script", {"code": "print('hi', doc.GetFps())\nresult = {'objects': 3}"})["result"]
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["output"], "hi 30\n")
        self.assertEqual(outcome["result"], {"objects": 3})
        self.assertTrue(self.c4d.event_adds, "the scene is redrawn after a script")

    def test_the_switch_is_read_when_the_command_runs(self):
        self.switch_on()
        self.set(self.prefs.MCP_ALLOW_PYTHON, True)
        self.assertTrue(self.call("run_script", {"code": "result = 1"})["result"]["ok"])
        self.set(self.prefs.MCP_ALLOW_PYTHON, False)
        self.assertIn("error", self.call("run_script", {"code": "result = 1"}))

    def test_a_script_error_comes_back_with_its_output(self):
        self.switch_on()
        self.set(self.prefs.MCP_ALLOW_PYTHON, True)
        outcome = self.call("run_script", {"code": "print('before')\n1 / 0"})["result"]
        self.assertFalse(outcome["ok"])
        self.assertIn("ZeroDivisionError", outcome["error"])
        self.assertIn("before", outcome["output"])
        self.assertNotIn("result", outcome)
        self.assertTrue(self.call("ping")["result"]["pong"], "the server is still fine")

    def test_stdout_is_restored_and_exit_ends_only_the_script(self):
        self.switch_on()
        self.set(self.prefs.MCP_ALLOW_PYTHON, True)
        before = sys.stdout
        exited = self.call("run_script", {"code": "import sys\nprint('bye')\nsys.exit(3)"})["result"]
        self.assertEqual((exited["ok"], exited["exit_code"], exited["output"]), (False, 3, "bye\n"))
        self.assertTrue(self.call("run_script", {"code": "exit()"})["result"]["ok"])
        self.assertIs(sys.stdout, before)
        self.call("run_script", {"code": "print('x')"})
        self.assertIs(sys.stdout, before)

    def test_a_file_can_be_run_and_both_or_neither_is_refused(self):
        self.switch_on()
        self.set(self.prefs.MCP_ALLOW_PYTHON, True)
        path = os.path.join(self.prefs_dir, "mcp_test_script.py")
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write("# -*- coding: utf-8 -*-\nresult = 'Ayşe'\n")
        self.assertEqual(self.call("run_script", {"file": path})["result"]["result"], "Ayşe")
        self.assertIn("either code or file", str(self.call("run_script", {})["error"]))
        self.assertIn("either code or file", str(self.call("run_script", {"code": "1", "file": "x"})["error"]))
        self.assertIn("not found", str(self.call("run_script", {"file": "/no/such/file.py"})["error"]))

    def test_unicode_source_with_an_encoding_line_runs(self):
        self.switch_on()
        self.set(self.prefs.MCP_ALLOW_PYTHON, True)
        code = "# -*- coding: utf-8 -*-\nresult = 'Türkçe'"
        self.assertEqual(self.call("run_script", {"code": code})["result"]["result"], "Türkçe")


if __name__ == "__main__":
    unittest.main()
