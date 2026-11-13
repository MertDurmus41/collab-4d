# -*- coding: utf-8 -*-
"""Setting up AI apps for the MCP server (core/mcp_clients.py) and the clipboard helper. Python 2.7 and 3.x."""
from __future__ import absolute_import, division, print_function, unicode_literals

import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "plugin", "c4d_collab")))

from c4d_collab_lib.core import clipboard, mcp_clients, mcp_spec  # noqa: E402

ENTRY = {"command": "C:/Python/python.exe", "args": ["C:/c4d/Ayşe plugin/mcp_stdio_bridge.py", "--config", "C:/p/c4d_collab_mcp.json"]}
ENV = ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "CODEX_HOME", "PATH")


class ClientCase(unittest.TestCase):
    def setUp(self):
        self.saved = dict((name, os.environ.get(name)) for name in ENV)
        self.root = tempfile.mkdtemp(prefix="c4d_mcp_clients_")
        self.home = os.path.join(self.root, "home")
        os.makedirs(self.home)
        os.environ["USERPROFILE"] = os.environ["HOME"] = self.home
        os.environ["APPDATA"] = os.path.join(self.home, "AppData", "Roaming")
        os.environ["LOCALAPPDATA"] = os.path.join(self.home, "AppData", "Local")
        os.environ.pop("CODEX_HOME", None)

    def tearDown(self):
        for name, value in self.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, path, text):
        folder = os.path.dirname(path)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        if isinstance(text, bytes):   # json.dumps returns str on Python 2
            text = text.decode("utf-8")
        with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def read(self, path):
        with io.open(path, encoding="utf-8") as handle:
            return handle.read()

    def backups(self, path):
        folder = os.path.dirname(path)
        return sorted(n for n in os.listdir(folder) if n.startswith(os.path.basename(path) + ".bak-"))


class JsonClientTest(ClientCase):
    def cursor_file(self):
        return mcp_clients.config_file(mcp_clients.CURSOR)

    def test_not_installed_until_the_app_folder_exists(self):
        self.assertEqual(mcp_clients.status(mcp_clients.CURSOR), mcp_clients.NOT_INSTALLED)
        ok, message, backup = mcp_clients.update(mcp_clients.CURSOR, ENTRY)
        self.assertFalse(ok)
        self.assertIn("does not seem to be installed", message)
        self.assertFalse(os.path.exists(self.cursor_file()), "nothing is created for an app that is not there")

    def test_a_new_config_is_created_in_an_installed_apps_folder(self):
        os.makedirs(os.path.dirname(self.cursor_file()))
        self.assertEqual(mcp_clients.status(mcp_clients.CURSOR), mcp_clients.NOT_SET_UP)
        ok, message, backup = mcp_clients.update(mcp_clients.CURSOR, ENTRY)
        self.assertTrue(ok, message)
        self.assertIsNone(backup, "nothing to back up")
        data = json.loads(self.read(self.cursor_file()))
        self.assertEqual(data, {"mcpServers": {"cinema4d": ENTRY}})
        self.assertEqual(mcp_clients.status(mcp_clients.CURSOR), mcp_clients.SET_UP)

    def test_other_servers_and_settings_are_kept_and_the_old_file_backed_up(self):
        original = {"theme": "dark", "mcpServers": {"github": {"command": "npx", "args": ["x"]},
                                                    "cinema4d": {"command": "old", "args": []}}}
        self.write(self.cursor_file(), json.dumps(original))
        ok, message, backup = mcp_clients.update(mcp_clients.CURSOR, ENTRY)
        self.assertTrue(ok, message)
        data = json.loads(self.read(self.cursor_file()))
        self.assertEqual(data["theme"], "dark")
        self.assertEqual(data["mcpServers"]["github"], original["mcpServers"]["github"])
        self.assertEqual(data["mcpServers"]["cinema4d"], ENTRY, "our old entry is replaced")
        self.assertEqual(json.loads(self.read(backup)), original, "the backup is the file as it was")
        self.assertEqual(len(self.backups(self.cursor_file())), 1)

    def test_a_file_that_is_not_json_is_never_overwritten(self):
        self.write(self.cursor_file(), "{ this is not json")
        ok, message, backup = mcp_clients.update(mcp_clients.CURSOR, ENTRY)
        self.assertFalse(ok)
        self.assertIn("not valid JSON", message)
        self.assertEqual(self.read(self.cursor_file()), "{ this is not json")
        self.assertEqual(self.backups(self.cursor_file()), [])

    def test_unexpected_shapes_are_refused(self):
        self.write(self.cursor_file(), "[1, 2]")
        self.assertFalse(mcp_clients.update(mcp_clients.CURSOR, ENTRY)[0])
        self.write(self.cursor_file(), '{"mcpServers": ["x"]}')
        ok, message, _ = mcp_clients.update(mcp_clients.CURSOR, ENTRY)
        self.assertFalse(ok)
        self.assertEqual(self.read(self.cursor_file()), '{"mcpServers": ["x"]}')

    def test_an_empty_file_and_a_bom_are_fine(self):
        self.write(self.cursor_file(), "")
        self.assertTrue(mcp_clients.update(mcp_clients.CURSOR, ENTRY)[0])
        self.write(self.cursor_file(), "\ufeff" + json.dumps({"a": 1}))
        self.assertTrue(mcp_clients.update(mcp_clients.CURSOR, ENTRY)[0])
        self.assertEqual(json.loads(self.read(self.cursor_file()))["a"], 1)

    def test_remove_takes_out_only_our_entry(self):
        self.write(self.cursor_file(), json.dumps({"mcpServers": {"github": {"command": "g"}, "cinema4d": ENTRY}}))
        self.assertEqual(mcp_clients.status(mcp_clients.CURSOR), mcp_clients.SET_UP)
        ok, message, backup = mcp_clients.remove(mcp_clients.CURSOR)
        self.assertTrue(ok, message)
        self.assertEqual(json.loads(self.read(self.cursor_file())), {"mcpServers": {"github": {"command": "g"}}})
        self.assertEqual(mcp_clients.status(mcp_clients.CURSOR), mcp_clients.NOT_SET_UP)
        self.assertFalse(mcp_clients.remove(mcp_clients.CURSOR)[0], "nothing left to remove")

    def test_non_ascii_text_survives(self):
        os.makedirs(os.path.dirname(self.cursor_file()))
        mcp_clients.update(mcp_clients.CURSOR, ENTRY)
        self.assertEqual(json.loads(self.read(self.cursor_file()))["mcpServers"]["cinema4d"]["args"][0],
                         "C:/c4d/Ayşe plugin/mcp_stdio_bridge.py")


class ClaudeDesktopTest(ClientCase):
    def test_the_microsoft_store_build_uses_the_package_folder(self):
        if sys.platform != "win32":
            self.skipTest("Windows only")
        package = os.path.join(os.environ["LOCALAPPDATA"], "Packages", "Claude_abc123")
        os.makedirs(package)
        target = mcp_clients.config_file(mcp_clients.CLAUDE_DESKTOP)
        self.assertTrue(target.startswith(package), target)
        self.assertEqual(mcp_clients.status(mcp_clients.CLAUDE_DESKTOP), mcp_clients.NOT_SET_UP)
        ok, message, _ = mcp_clients.update(mcp_clients.CLAUDE_DESKTOP, ENTRY)
        self.assertTrue(ok, message)
        self.assertTrue(os.path.isfile(os.path.join(package, "LocalCache", "Roaming", "Claude", "claude_desktop_config.json")))
        self.assertEqual(mcp_clients.status(mcp_clients.CLAUDE_DESKTOP), mcp_clients.SET_UP)

    def test_claude_4_keeps_its_config_in_claude_data_and_that_wins(self):
        if sys.platform != "win32":
            self.skipTest("Windows only")
        data = os.path.join(os.environ["LOCALAPPDATA"], "Claude-Data", "claude_abc123")
        package = os.path.join(os.environ["LOCALAPPDATA"], "Packages", "Claude_abc123", "LocalCache", "Roaming", "Claude")
        os.makedirs(data)
        os.makedirs(package)
        self.write(os.path.join(package, "claude_desktop_config.json"), "{}")   # the older location also exists
        self.write(os.path.join(data, "claude_desktop_config.json"), json.dumps({"preferences": {"x": 1}}))
        target = mcp_clients.config_file(mcp_clients.CLAUDE_DESKTOP)
        self.assertEqual(target, os.path.join(data, "claude_desktop_config.json"))
        ok, message, backup = mcp_clients.update(mcp_clients.CLAUDE_DESKTOP, ENTRY)
        self.assertTrue(ok, message)
        written = json.loads(self.read(target))
        self.assertEqual(written["preferences"], {"x": 1}, "the app's own settings stay")
        self.assertEqual(written["mcpServers"]["cinema4d"], ENTRY)
        self.assertEqual(self.read(os.path.join(package, "claude_desktop_config.json")), "{}", "the old file is not touched")
        self.assertEqual(mcp_clients.status(mcp_clients.CLAUDE_DESKTOP), mcp_clients.SET_UP)

    def test_the_normal_build_uses_the_roaming_folder(self):
        if sys.platform != "win32":
            self.skipTest("Windows only")
        folder = os.path.join(os.environ["APPDATA"], "Claude")
        os.makedirs(folder)
        self.assertEqual(mcp_clients.config_file(mcp_clients.CLAUDE_DESKTOP),
                         os.path.join(folder, "claude_desktop_config.json"))
        self.assertTrue(mcp_clients.update(mcp_clients.CLAUDE_DESKTOP, ENTRY)[0])

    def test_nothing_installed(self):
        self.assertEqual(mcp_clients.status(mcp_clients.CLAUDE_DESKTOP), mcp_clients.NOT_INSTALLED)
        self.assertFalse(mcp_clients.update(mcp_clients.CLAUDE_DESKTOP, ENTRY)[0])


class CodexTest(ClientCase):
    def setUp(self):
        ClientCase.setUp(self)
        self.path = os.path.join(self.home, ".codex", "config.toml")

    def test_a_marked_block_is_added_replaced_and_removed(self):
        existing = 'model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "x"\n'
        self.write(self.path, existing)
        self.assertEqual(mcp_clients.status(mcp_clients.CODEX), mcp_clients.NOT_SET_UP)
        ok, message, backup = mcp_clients.update(mcp_clients.CODEX, ENTRY)
        self.assertTrue(ok, message)
        text = self.read(self.path)
        self.assertTrue(text.startswith(existing.rstrip("\n")), "what was there stays untouched")
        self.assertIn("[mcp_servers.cinema4d]", text)
        self.assertIn('command = "C:/Python/python.exe"', text)
        self.assertIn('"C:/c4d/Ay\\u015fe plugin/mcp_stdio_bridge.py"', text)
        self.assertEqual(self.read(backup), existing)
        self.assertEqual(mcp_clients.status(mcp_clients.CODEX), mcp_clients.SET_UP)
        again = dict(ENTRY, command="D:/Python/python.exe")
        self.assertTrue(mcp_clients.update(mcp_clients.CODEX, again)[0])
        text = self.read(self.path)
        self.assertEqual(text.count("[mcp_servers.cinema4d]"), 1, "replaced, not added twice")
        self.assertIn("D:/Python/python.exe", text)
        self.assertTrue(mcp_clients.remove(mcp_clients.CODEX)[0])
        self.assertEqual(self.read(self.path).rstrip("\n"), existing.rstrip("\n"))
        self.assertEqual(mcp_clients.status(mcp_clients.CODEX), mcp_clients.NOT_SET_UP)

    def test_an_entry_made_by_hand_is_not_touched(self):
        hand = '[mcp_servers.cinema4d]\ncommand = "mine"\n'
        self.write(self.path, hand)
        ok, message, _ = mcp_clients.update(mcp_clients.CODEX, ENTRY)
        self.assertFalse(ok)
        self.assertIn("not made by this page", message)
        self.assertEqual(self.read(self.path), hand)

    def test_a_missing_file_in_an_installed_folder_is_created(self):
        os.makedirs(os.path.dirname(self.path))
        self.assertTrue(mcp_clients.update(mcp_clients.CODEX, ENTRY)[0])
        self.assertIn("[mcp_servers.cinema4d]", self.read(self.path))

    def test_codex_home_is_respected(self):
        os.environ["CODEX_HOME"] = os.path.join(self.root, "elsewhere")
        os.makedirs(os.environ["CODEX_HOME"])
        self.assertEqual(mcp_clients.config_file(mcp_clients.CODEX), os.path.join(os.environ["CODEX_HOME"], "config.toml"))


class OtherClientsTest(ClientCase):
    def test_claude_code_and_other_are_set_up_by_hand(self):
        self.assertEqual(mcp_clients.status(mcp_clients.OTHER), mcp_clients.MANUAL)
        self.assertFalse(mcp_clients.update(mcp_clients.OTHER, ENTRY)[0])
        self.assertFalse(mcp_clients.update(mcp_clients.CLAUDE_CODE, ENTRY)[0])
        self.assertFalse(mcp_clients.remove(mcp_clients.CLAUDE_CODE)[0])

    def test_claude_code_status_reads_its_user_config(self):
        path = os.path.join(self.home, ".claude.json")
        self.write(path, json.dumps({"mcpServers": {"cinema4d": ENTRY}, "x": 1}))
        self.assertEqual(mcp_clients.status(mcp_clients.CLAUDE_CODE), mcp_clients.SET_UP)
        self.write(path, "{ broken")
        self.assertEqual(mcp_clients.status(mcp_clients.CLAUDE_CODE), mcp_clients.NOT_SET_UP)

    def test_what_gets_copied(self):
        command = mcp_clients.copy_text(mcp_clients.CLAUDE_CODE, ENTRY)
        self.assertTrue(command.startswith("claude mcp add --scope user cinema4d -- "))
        self.assertIn('"C:/c4d/Ayşe plugin/mcp_stdio_bridge.py"', command, "paths with spaces are quoted")
        self.assertEqual(json.loads(mcp_clients.copy_text(mcp_clients.OTHER, ENTRY)), {"mcpServers": {"cinema4d": ENTRY}})
        self.assertEqual(json.loads(mcp_clients.copy_text(mcp_clients.CURSOR, ENTRY)), {"mcpServers": {"cinema4d": ENTRY}})
        self.assertIn("[mcp_servers.cinema4d]", mcp_clients.copy_text(mcp_clients.CODEX, ENTRY))


class PythonFinderTest(ClientCase):
    def test_a_real_python_by_absolute_path_beats_the_store_stub(self):
        if sys.platform != "win32":
            self.skipTest("Windows only")
        stub = os.path.join(self.home, "AppData", "Local", "Microsoft", "WindowsApps")
        real = os.path.join(self.home, "AppData", "Local", "Python", "pythoncore-3.14-64")
        os.makedirs(stub)
        os.makedirs(real)
        for folder in (stub, real):
            open(os.path.join(folder, "python.exe"), "w").close()
        os.environ["PATH"] = stub
        command, lead = mcp_clients.find_python()
        self.assertEqual((command, lead), (os.path.join(real, "python.exe"), []), "the Store stub would open the Store")

    def test_the_newest_installed_version_wins_and_the_launcher_is_last(self):
        if sys.platform != "win32":
            self.skipTest("Windows only")
        os.environ["PATH"] = ""
        base = os.path.join(self.home, "AppData", "Local", "Programs", "Python")
        for version in ("Python39", "Python312", "Python310"):
            os.makedirs(os.path.join(base, version))
            open(os.path.join(base, version, "python.exe"), "w").close()
        self.assertEqual(mcp_clients.find_python()[0], os.path.join(base, "Python312", "python.exe"))
        shutil.rmtree(base)
        launcher = os.path.join(self.home, "AppData", "Local", "Microsoft", "WindowsApps")
        os.makedirs(launcher)
        open(os.path.join(launcher, "py.exe"), "w").close()
        self.assertEqual(mcp_clients.find_python(), (os.path.join(launcher, "py.exe"), ["-3"]))

    def test_the_entry_runs_the_bridge_with_its_config(self):
        entry = mcp_clients.server_entry("B:/bridge.py", "B:/c.json")
        self.assertEqual(entry["args"][-3:], ["B:/bridge.py", "--config", "B:/c.json"])
        self.assertTrue(entry["command"])


class ToolSelectionTest(unittest.TestCase):
    def test_tools_follow_the_toolset_and_the_python_permission(self):
        every_group = mcp_spec.GROUPS
        self.assertEqual(mcp_spec.tools_of(every_group, False),
                         ["ping", "get_info", "get_hierarchy", "get_object", "get_log"])
        self.assertEqual(mcp_spec.tools_of(every_group, True)[-1], "run_script")
        self.assertEqual(mcp_spec.tools_of([mcp_spec.GROUP_BASICS], False), ["ping", "get_info"])
        self.assertEqual(mcp_spec.tools_of([], True), ["run_script"], "Python is decided by its own switch")
        self.assertEqual(mcp_spec.tools_of([], False), [])
        for tool in mcp_spec.TOOLS:
            self.assertTrue(tool.get("group") in mcp_spec.GROUPS or tool["name"] in mcp_spec.PYTHON_TOOLS, tool["name"])


@unittest.skipUnless(sys.platform == "win32", "uses the Windows clipboard")
class ClipboardTest(unittest.TestCase):
    def test_text_round_trips_through_the_windows_clipboard(self):
        import subprocess
        text = "token 0123 Ay\u015fe C:/Users/Ay\u015fe/x \"q\" \u00e7"
        self.assertTrue(clipboard.set_text(text))
        out = subprocess.check_output(["powershell", "-NoProfile", "-Command",
                                       "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw"])
        self.assertEqual(out.decode("utf-8").rstrip("\r\n"), text)


if __name__ == "__main__":
    unittest.main()
