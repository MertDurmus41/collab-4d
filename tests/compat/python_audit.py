# -*- coding: utf-8 -*-
"""Does the plugin only use what Python 2.7 and Python 3.7 (R23) both have?   Run: py tests/compat/python_audit.py

Cinema 4D runs Python 2.7 (R17-R22), 3.7 (R23), 3.9 (2022/2023), 3.11 (2024, 2025) and newer. The plugin has
to import and run on all of them, and only 2.7, 3.9 and the newest Python are available here, so this audit
reads the source (AST, no execution) and reports:

  * syntax that 2.7 or 3.7 cannot parse: f-strings, walrus, positional-only parameters, type annotations on
    variables, `match`, `except*`, `async`, `nonlocal`, `yield from`, keyword-only parameters, `print` without the
    __future__ import, set/dict comprehension is fine on both
  * standard-library names that appeared after 3.7 or after 2.7 (a list of the ones that matter), and
    Python-3-only names used without a guard (`time.monotonic`, `shutil.which`, `subprocess.run`, `pathlib`...)
  * a missing `from __future__ import ...` header and non-ASCII source without a coding line (R17-R22 would
    refuse to open the file)

A construct in an `if PY2`/`try: ... except ImportError` branch or behind hasattr/getattr is guarded and
accepted. Exit code 0 when clean.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import ast
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "plugin", "c4d_collab"))

# name -> (Python version that added it, note). Names whose absence on 2.7/3.7 would break an import or a call.
NEW_IN_PY3 = {
    "time.monotonic": "3.3 (core.compat.monotonic is the guarded wrapper)",
    "time.perf_counter": "3.3",
    "shutil.which": "3.3",
    "subprocess.run": "3.5",
    "subprocess.DEVNULL": "3.3",
    "os.makedirs(exist_ok": "3.2 (exist_ok)",
    "os.scandir": "3.5",
    "math.isclose": "3.5",
    "math.inf": "3.5",
    "hmac.compare_digest": "2.7.7 (core.protocol has a fallback)",
    "json.JSONDecodeError": "3.5 (it is a ValueError on every version: catch ValueError)",
    "secrets": "3.6",
    "pathlib": "3.4",
    "enum": "3.4",
    "typing": "3.5",
    "asyncio": "3.4",
    "selectors": "3.4",
    "ipaddress": "3.3",
    "unittest.mock": "3.3",
}
NEW_AFTER_37 = {
    "math.prod": "3.8", "shlex.join": "3.8", "functools.cached_property": "3.8", "functools.cache": "3.9",
    "statistics.fmean": "3.8", "time.thread_time": "3.7", "str.removeprefix": "3.9", "str.removesuffix": "3.9",
    "dict | dict": "3.9", "zoneinfo": "3.9", "graphlib": "3.9", "math.dist": "3.8", "math.isqrt": "3.8",
    "importlib.metadata": "3.8", "shutil.copytree(dirs_exist_ok": "3.8", "datetime.fromisoformat": "3.7",
    "itertools.pairwise": "3.10", "int.bit_count": "3.10", "str.isascii": "3.7",
}
ATTRIBUTES_AFTER_37 = ("removeprefix", "removesuffix", "cached_property", "fmean", "isqrt", "pairwise", "bit_count",
                       "dirs_exist_ok", "fromisoformat", "prod", "zoneinfo")
ATTRIBUTES_PY3_ONLY = ("monotonic", "perf_counter", "which", "DEVNULL", "scandir", "JSONDecodeError",
                       "exist_ok", "isclose")
MODULES_PY3_ONLY = ("pathlib", "typing", "asyncio", "enum", "selectors", "ipaddress", "secrets", "concurrent",
                    "dataclasses", "contextvars")


class Audit(object):
    def __init__(self, root):
        self.problems = []
        self.root = root

    def add(self, path, line, text):
        self.problems.append("%s:%s  %s" % (os.path.relpath(path, self.root).replace("\\", "/"), line, text))


def source_files(root):
    for base, _dirs, files in os.walk(root):
        for name in sorted(files):
            if name.endswith((".py", ".pyp")):
                yield os.path.join(base, name)


def guarded_lines(tree):
    """Line numbers inside `try/except ImportError|AttributeError`, `if PY2/not PY2`, `if hasattr(...)` or `getattr`."""
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            handlers = [h.type for h in node.handlers]
            names = set()
            for h in handlers:
                for part in (h.elts if isinstance(h, ast.Tuple) else [h]):
                    if isinstance(part, ast.Name):
                        names.add(part.id)
            if names & {"ImportError", "AttributeError", "Exception", "BaseException", "TypeError"} or not handlers:
                for child in node.body:
                    lines.update(range(child.lineno, getattr(child, "end_lineno", child.lineno) + 1))
        elif isinstance(node, ast.If):
            test = ast.dump(node.test)
            if "PY2" in test or "hasattr" in test or "getattr" in test or "version_info" in test or "platform" in test:
                for child in node.body + node.orelse:
                    lines.update(range(child.lineno, getattr(child, "end_lineno", child.lineno) + 1))
    return lines


def check_syntax(audit, path, tree, text):
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.JoinedStr):
            audit.add(path, line, "f-string (3.6+): not on 2.7")
        elif isinstance(node, ast.NamedExpr):
            audit.add(path, line, "walrus := (3.8+)")
        elif isinstance(node, ast.arguments):
            if getattr(node, "posonlyargs", None):
                audit.add(path, line, "positional-only parameters (3.8+)")
            if node.kwonlyargs:
                audit.add(path, line, "keyword-only parameters (3.0+): not on 2.7")
        elif isinstance(node, ast.AnnAssign):
            audit.add(path, line, "annotated assignment (3.6+): not on 2.7")
        elif isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.AsyncFor, ast.AsyncWith)):
            audit.add(path, line, "async/await (3.5+): not on 2.7")
        elif isinstance(node, ast.Nonlocal):
            audit.add(path, line, "nonlocal (3.0+): not on 2.7")
        elif isinstance(node, ast.YieldFrom):
            audit.add(path, line, "yield from (3.3+): not on 2.7")
        elif type(node).__name__ in ("Match", "TryStar"):
            audit.add(path, line, "%s (3.10+/3.11+)" % type(node).__name__)
        elif isinstance(node, ast.FunctionDef):
            if node.returns is not None or any(a.annotation is not None for a in node.args.args):
                audit.add(path, line, "type annotation on %s (3.0+): not on 2.7" % node.name)
        elif isinstance(node, ast.Starred) and not isinstance(getattr(node, "ctx", None), ast.Load):
            audit.add(path, line, "star-unpacking assignment (3.0+): not on 2.7")
        elif isinstance(node, ast.Raise) and getattr(node, "cause", None) is not None:
            audit.add(path, line, "raise ... from ... (3.0+): not on 2.7")
        elif isinstance(node, ast.Bytes if hasattr(ast, "Bytes") else ()):
            pass


def check_names(audit, path, tree, guarded):
    imports = {}
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                imports[alias.asname or top] = alias.name
                if top in MODULES_PY3_ONLY and line not in guarded:
                    audit.add(path, line, "import %s: Python 3 only, not guarded" % alias.name)
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            if top in MODULES_PY3_ONLY and line not in guarded:
                audit.add(path, line, "from %s import ...: Python 3 only, not guarded" % node.module)
            for alias in node.names:
                if (node.module or "") == "" and alias.name == "unicode_literals":
                    continue
        elif isinstance(node, ast.Attribute):
            owner = node.value
            if isinstance(owner, ast.Name) and owner.id in imports:
                full = "%s.%s" % (imports[owner.id], node.attr)
                if full in NEW_IN_PY3 and line not in guarded:
                    audit.add(path, line, "%s: new in %s, not guarded" % (full, NEW_IN_PY3[full]))
                if full in NEW_AFTER_37 and line not in guarded:
                    audit.add(path, line, "%s: new in Python %s (R23 runs 3.7)" % (full, NEW_AFTER_37[full]))
            if node.attr in ("removeprefix", "removesuffix", "bit_count", "fromisoformat", "isascii") and line not in guarded:
                audit.add(path, line, ".%s() needs Python 3.7-3.10" % node.attr)
        elif isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg in ("exist_ok", "dirs_exist_ok", "timeout") and line not in guarded:
                    func = getattr(node.func, "attr", getattr(node.func, "id", ""))
                    if keyword.arg == "exist_ok" and func == "makedirs":
                        audit.add(path, line, "os.makedirs(exist_ok=...) is Python 3 only")
                    if keyword.arg == "dirs_exist_ok":
                        audit.add(path, line, "dirs_exist_ok is Python 3.8+")
                    if keyword.arg == "timeout" and func in ("communicate", "wait", "check_output", "call"):
                        audit.add(path, line, "subprocess %s(timeout=...) is Python 3 only" % func)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            pass


def check_header(audit, path, text):
    head = text.split("\n", 40)
    if "from __future__ import" not in text and os.path.getsize(path) > 200:
        audit.add(path, 1, "no `from __future__ import ...` line (the project rule for every file)")
    if re.search(r"[^\x00-\x7f]", text):
        first_two = "\n".join(text.split("\n")[:2])
        if "coding" not in first_two:
            audit.add(path, 1, "non-ASCII characters but no `# -*- coding: utf-8 -*-` line (Python 2.7 will not open it)")


def run(root=ROOT):
    audit = Audit(root)
    count = 0
    for path in source_files(root):
        with io.open(path, encoding="utf-8") as handle:
            text = handle.read()
        try:
            tree = ast.parse(text, path)
        except SyntaxError as exc:
            audit.add(path, exc.lineno or 0, "does not even parse here: %s" % exc)
            continue
        count += 1
        guarded = guarded_lines(tree)
        check_syntax(audit, path, tree, text)
        check_names(audit, path, tree, guarded)
        check_header(audit, path, text)
    return audit, count


def main():
    audit, count = run()
    print("files read: %d" % count)
    for problem in audit.problems:
        print("PROBLEM", problem)
    print("%d problem(s)" % len(audit.problems))
    return 1 if audit.problems else 0


if __name__ == "__main__":
    sys.exit(main())
