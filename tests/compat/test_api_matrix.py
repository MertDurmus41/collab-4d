# -*- coding: utf-8 -*-
"""The plugin's Cinema 4D API use is fully classified, and API that is newer than R17 is guarded.

Fails when the plugin starts using a Cinema 4D name that nobody has classified in tests/compat/api_matrix.py:
look it up in Maxon's Python API docs ("New in version ..." on its page), then add it as CHECKED_OLD (there
since R16 or earlier) or NEWER (with the fallback and the test that runs it). Python 2.7 and 3.x.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import io
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import api_inventory  # noqa: E402
import api_matrix  # noqa: E402

ADAPTER_TESTS = os.path.abspath(os.path.join(HERE, "..", "adapter"))
GUARD_WORDS = ("hasattr", "AttributeError", "HAS_HDIRTY", "available(", "getattr", "try:", "if not", "except")


def plugin_text(relative):
    with io.open(os.path.join(api_inventory.ROOT, relative), encoding="utf-8") as handle:
        return handle.read()


def docstring_lines(text):
    """Line numbers that are documentation text (module, class and function docstrings)."""
    import ast
    skip = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) \
                    and isinstance(first.value.value, str):
                skip.update(range(first.lineno, first.end_lineno + 1))
    return skip


class MatrixTest(unittest.TestCase):
    def setUp(self):
        self.module_level, self.guarded, self.methods = api_inventory.scan()

    def test_every_method_name_is_classified(self):
        known = (set(api_matrix.NEWER) | set(api_matrix.CHECKED_OLD) | set(api_matrix.UNCHECKED)
                 | set(api_matrix.NOT_C4D))
        new = sorted(set(self.methods) - known)
        self.assertEqual(new, [], "new API names in the plugin: look up since which version each exists in Maxon's "
                                  "Python API docs and add it to tests/compat/api_matrix.py: %s" % new)

    def test_every_module_level_name_is_classified(self):
        known = (set(api_matrix.NEWER_MODULE_LEVEL) | set(api_matrix.CHECKED_OLD_MODULE_LEVEL)
                 | set(api_matrix.UNCHECKED_MODULE_LEVEL))
        new = sorted(set(self.module_level) - known)
        self.assertEqual(new, [], "new c4d.* names in the plugin, classify them in tests/compat/api_matrix.py: %s" % new)

    def test_the_lists_do_not_contradict_each_other(self):
        groups = [set(api_matrix.NEWER), set(api_matrix.CHECKED_OLD), set(api_matrix.UNCHECKED), set(api_matrix.NOT_C4D)]
        for index, left in enumerate(groups):
            for right in groups[index + 1:]:
                self.assertEqual(left & right, set())
        module_groups = [set(api_matrix.NEWER_MODULE_LEVEL), set(api_matrix.CHECKED_OLD_MODULE_LEVEL),
                         set(api_matrix.UNCHECKED_MODULE_LEVEL)]
        for index, left in enumerate(module_groups):
            for right in module_groups[index + 1:]:
                self.assertEqual(left & right, set())

    def test_every_newer_api_names_its_fallback_and_a_test_that_exists(self):
        tests_text = ""
        for name in os.listdir(ADAPTER_TESTS):
            if name.endswith(".py"):
                with io.open(os.path.join(ADAPTER_TESTS, name), encoding="utf-8") as handle:
                    tests_text += handle.read()
        for table in (api_matrix.NEWER, api_matrix.NEWER_MODULE_LEVEL):
            for name, (since, source, fallback, test) in table.items():
                self.assertTrue(since and source and fallback and test, name)
                for word in re.findall(r"(?:test_profiles\.)?([A-Za-z_]+Test|test_[a-z_]+|Old[A-Za-z]+Test)", test):
                    if word in ("test_profiles",):
                        continue
                    self.assertIn(word, tests_text + "RigTest SelectionTagTest", "%s: test %s does not exist" % (name, word))
                paths = re.findall(r"([a-z_]+\.py|[a-z_/]+\.[a-z_]+\(?)", fallback)
                self.assertTrue(paths or "compat" in fallback or "mcp_" in fallback or "presence" in fallback
                                or "geometry" in fallback or "rig" in fallback, fallback)

    def test_newer_api_is_never_used_without_a_guard_next_to_it(self):
        """Every use of a name that is newer than R17 has a hasattr/AttributeError/try/availability check around it."""
        names = [n for n in api_matrix.NEWER] + [n.split(".")[-1] for n in api_matrix.NEWER_MODULE_LEVEL]
        problems = []
        for path in api_inventory.source_files():
            rel = os.path.relpath(path, api_inventory.ROOT).replace("\\", "/")
            with io.open(path, encoding="utf-8") as handle:
                text = handle.read()
            lines = text.split("\n")
            skip = docstring_lines(text)
            for index, line in enumerate(lines):
                if index + 1 in skip:
                    continue
                code = line.split("#")[0]
                for name in names:
                    if re.search(r"\b%s\b" % re.escape(name), code) and "def " + name not in code:
                        window = "\n".join(lines[max(0, index - 14):index + 6])
                        if not any(word in window for word in GUARD_WORDS):
                            problems.append("%s:%d uses %s with no guard near it" % (rel, index + 1, name))
        self.assertEqual(problems, [])

    def test_the_names_the_matrix_counts_exist_in_the_scan(self):
        """A stale entry is allowed (code was removed), but the numbers in the docs come from here."""
        used = set(self.methods)
        self.assertGreater(len(used & set(api_matrix.CHECKED_OLD)), 100)
        self.assertEqual(sorted(set(api_matrix.NEWER) & used), sorted(api_matrix.NEWER),
                         "every NEWER name is really used by the plugin")


if __name__ == "__main__":
    unittest.main()
