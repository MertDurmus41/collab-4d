# -*- coding: utf-8 -*-
"""Which Cinema 4D API does the plugin use?   Run: py tests/compat/api_inventory.py [--json out.json]

Static (AST) scan of plugin/c4d_collab: no Cinema 4D needed. Lists
  * module level symbols            c4d.Foo, c4d.documents.Foo ...            (plain use: a missing one raises)
  * guarded names                   getattr(c4d, "FOO", default), compat.type_ids("A", "B"), _first_flag(...)
  * methods called on Cinema 4D objects (CamelCase names, e.g. AddUniqueID, GetHDirty, SetParameter)
and where each is used. tests/compat/api_matrix.py says since which version each one exists.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import ast
import collections
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "plugin", "c4d_collab"))
GUARD_FUNCTIONS = ("type_ids", "_first_flag", "getattr", "hasattr")


def source_files():
    for base, _dirs, files in os.walk(ROOT):
        for name in sorted(files):
            if name.endswith((".py", ".pyp")) and "mcp_stdio_bridge" not in name:
                yield os.path.join(base, name)


def is_api_method(name):
    """Cinema 4D's API style: CamelCase, starts with an upper case letter, not a Python/our own helper."""
    return name[:1].isupper() and not name.isupper()


def scan():
    module_level = collections.defaultdict(set)
    guarded = collections.defaultdict(set)
    methods = collections.defaultdict(set)
    for path in source_files():
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        with io.open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                chain, current = [], node
                while isinstance(current, ast.Attribute):
                    chain.append(current.attr)
                    current = current.value
                if isinstance(current, ast.Name) and current.id == "c4d":
                    module_level["c4d." + ".".join(reversed(chain))].add(rel)
                elif is_api_method(node.attr):
                    methods[node.attr].add(rel)
            elif isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name in GUARD_FUNCTIONS:
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value[:1].isalpha():
                            guarded[arg.value].add(rel)
    return module_level, guarded, methods


def main():
    module_level, guarded, methods = scan()
    direct = sorted(module_level)
    result = {
        "module_level": dict((k, sorted(v)) for k, v in module_level.items()),
        "guarded": dict((k, sorted(v)) for k, v in guarded.items()),
        "methods": dict((k, sorted(v)) for k, v in methods.items()),
    }
    print("module level symbols: %d | guarded names: %d | methods on objects: %d" % (len(direct), len(guarded), len(methods)))
    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]
        with io.open(out, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(result, indent=1, sort_keys=True, ensure_ascii=False))
        print("written", out)
    return result


if __name__ == "__main__":
    main()
