# -*- coding: utf-8 -*-
"""Imports every plugin module with the fake c4d. Run it with the Python of the oldest target, Cinema 4D R19's own
(its python.exe is under resource/modules/python/Python.win64.framework): a source file with a non-ASCII character and no
coding line is a SyntaxError there ("compile()" on text does not see it), and the plugin then silently fails to load.

    "C:/Program Files/MAXON/Cinema 4D R19/resource/modules/python/Python.win64.framework/python.exe" tests/compat/import_check.py
"""
from __future__ import print_function

import os
import sys
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tests", "adapter"))
sys.path.insert(0, os.path.join(ROOT, "plugin", "c4d_collab"))
import fake_c4d  # noqa: E402

fake_c4d.install(None)
failed = 0
for folder, _dirs, files in os.walk(os.path.join(ROOT, "plugin", "c4d_collab", "c4d_collab_lib")):
    for name in sorted(files):
        if not name.endswith(".py") or name == "__init__.py":
            continue
        rel = os.path.relpath(os.path.join(folder, name), os.path.join(ROOT, "plugin", "c4d_collab"))
        module = rel[:-3].replace(os.sep, ".")
        try:
            __import__(module)
        except Exception:
            failed += 1
            print("FAIL " + module)
            traceback.print_exc()
print("modules that failed to import: %d" % failed)
sys.exit(1 if failed else 0)
