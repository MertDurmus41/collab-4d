"""Developer-only self-test command (for versions without c4dpy, e.g. R17-R20).

Registered only when a file named `selftest.enabled` exists next to the .pyp
(never shipped); its content is the repository path. Clicking it runs tests/c4d_real/scenario.py in this Cinema 4D
with the role/port written by `py tests/c4d_real/run.py --manual-peer ...`
into tests/c4d_real/out/task.json. The UI is busy while the test runs.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import io
import json
import os

import c4d
from c4d import gui

from . import compat

SELFTEST_COMMAND = 1000004  # development id, like the others in plugin_ids


def enabled(plugin_dir):
    return os.path.exists(os.path.join(plugin_dir, "selftest.enabled"))


def repo_dir(plugin_dir):
    """Repository root. The plugin is usually linked into C4D's prefs folder, so
    "../.." from there is not the repo: selftest.enabled may contain the real path."""
    with io.open(os.path.join(plugin_dir, "selftest.enabled"), encoding="utf-8") as handle:
        written = handle.read().strip()
    if written and os.path.isdir(written):
        return written
    return os.path.abspath(os.path.join(plugin_dir, "..", ".."))


class SelfTestCommand(c4d.plugins.CommandData):
    def __init__(self, plugin_dir):
        self.repo = repo_dir(plugin_dir)

    def Execute(self, doc):
        task_path = os.path.join(self.repo, "tests", "c4d_real", "out", "task.json")
        scenario_path = os.path.join(self.repo, "tests", "c4d_real", "scenario.py")
        if not os.path.exists(task_path) or not os.path.exists(scenario_path):
            gui.MessageDialog(compat.native_str("No self-test task found:\n%s" % task_path))
            return True
        with io.open(task_path, encoding="utf-8") as handle:
            task = json.load(handle)
        if task.get("script"):
            return self._run_script(task)
        namespace = {"__name__": "__c4d_collab_selftest__", "__file__": scenario_path}
        with open(scenario_path, "rb") as handle:
            exec(compile(handle.read(), scenario_path, "exec"), namespace)
        scenario = namespace["run_scenario"](task["role"], task["port"], task["results"])
        failed = [c["name"] for c in scenario.checks if not c["ok"]]
        summary = "Collab 4D self-test (%s): %d passed, %d failed" % (
            task["role"], len(scenario.checks) - len(failed), len(failed))
        if failed:
            summary += "\n\n" + "\n".join(failed[:15])
        gui.MessageDialog(compat.native_str(summary))
        return True


    @staticmethod
    def _run_script(task):
        """A task that names its own script (tests/c4d_real/live_rig_check.py): its environment is set, its run_role()
        runs here, and the result file it writes is named in the message."""
        for key, value in (task.get("env") or {}).items():
            os.environ[compat.native_str(key)] = compat.native_str(value)
        namespace = {"__name__": "__c4d_collab_selftest__", "__file__": task["script"]}
        with open(task["script"], "rb") as handle:
            exec(compile(handle.read(), task["script"], "exec"), namespace)
        namespace["run_role"]()
        gui.MessageDialog(compat.native_str("Collab 4D self-test finished. Result: %s" % task["env"].get("C4D_COLLAB_RESULTS")))
        return True


def register(plugin_dir):
    return c4d.plugins.RegisterCommandPlugin(
        # A developer tool: out of the menu (run it from the Script Manager with c4d.CallCommand(1000004)).
        SELFTEST_COMMAND, compat.native_str("Collab 4D Self-Test (dev)"), getattr(c4d, "PLUGINFLAG_HIDEPLUGINMENU", 0), None,
        compat.native_str("Runs the automated sync scenario (developer tool)"), SelfTestCommand(plugin_dir))
