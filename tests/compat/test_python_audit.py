# -*- coding: utf-8 -*-
"""The Python 2.7 / 3.7 audit finds what it should, and the plugin passes it. Python 3 (the audit reads source
with the ast module of the running interpreter; the checks are about the plugin's source, not about this one)."""
from __future__ import absolute_import, division, print_function, unicode_literals

import io
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import python_audit  # noqa: E402

HEADER = "from __future__ import absolute_import, division, print_function, unicode_literals\n"


class AuditCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="c4d_collab_audit_")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def problems(self, source, header=True):
        path = os.path.join(self.root, "sample.py")
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write((HEADER if header else "") + source)
        audit, _count = python_audit.run(self.root)
        return audit.problems


@unittest.skipIf(sys.version_info[0] == 2, "needs the ast of Python 3 (the f-string and walrus nodes)")
class AuditFindsTest(AuditCase):
    def test_syntax_that_old_pythons_cannot_read(self):
        cases = {
            "f-string": "name = 'x'\ntext = f'{name}'\n",
            "walrus": "if (n := 3):\n    pass\n",
            "positional-only": "def f(a, /, b):\n    pass\n",
            "keyword-only": "def f(a, *, b):\n    pass\n",
            "annotated assignment": "value: int = 3\n",
            "type annotation": "def f(a: int) -> int:\n    return a\n",
            "async": "async def f():\n    pass\n",
            "nonlocal": "def f():\n    x = 1\n    def g():\n        nonlocal x\n",
            "yield from": "def f():\n    yield from range(3)\n",
            "raise from": "def f():\n    raise ValueError() from None\n",
            "match": "match 1:\n    case 1:\n        pass\n",
        }
        for label, source in cases.items():
            self.assertTrue(self.problems(source), "missed: " + label)

    def test_stdlib_that_is_not_everywhere(self):
        cases = {
            "monotonic": "import time\nvalue = time.monotonic()\n",
            "which": "import shutil\nvalue = shutil.which('x')\n",
            "subprocess.run": "import subprocess\nsubprocess.run(['x'])\n",
            "pathlib": "import pathlib\n",
            "typing": "from typing import List\n",
            "removeprefix": "text = 'abc'.removeprefix('a')\n",
            "makedirs exist_ok": "import os\nos.makedirs('x', exist_ok=True)\n",
            "math.prod": "import math\nvalue = math.prod([1])\n",
            "subprocess timeout": "import subprocess\nprocess = None\nprocess.communicate(timeout=3)\n",
        }
        for label, source in cases.items():
            self.assertTrue(self.problems(source), "missed: " + label)

    def test_missing_header_and_coding_line(self):
        self.assertTrue(any("__future__" in p for p in self.problems("value = 1\n" * 40, header=False)))
        self.assertTrue(any("coding" in p for p in self.problems("name = 'Ayşe'\n")))
        self.assertFalse(self.problems("# -*- coding: utf-8 -*-\n" + HEADER + "name = 'Ayşe'\n", header=False))

    def test_guarded_use_is_accepted(self):
        guarded = (
            "import time\n"
            "try:\n    clock = time.monotonic\nexcept AttributeError:\n    clock = time.time\n"
            "import sys\n"
            "PY2 = sys.version_info[0] == 2\n"
            "if not PY2:\n    import pathlib\n"
            "def now():\n    monotonic = getattr(time, 'monotonic', None)\n    return monotonic() if monotonic else time.time()\n"
        )
        self.assertEqual(self.problems(guarded), [])

    def test_plain_python_2_compatible_code_is_clean(self):
        source = ("import os\nimport io\n\n\ndef read(path):\n    with io.open(path, encoding='utf-8') as handle:\n"
                  "        return handle.read()\n\n\nclass A(object):\n    def f(self, a, b=1, *args, **kwargs):\n"
                  "        return dict((k, v) for k, v in kwargs.items())\n")
        self.assertEqual(self.problems(source), [])


class PluginAuditTest(unittest.TestCase):
    def test_the_plugin_uses_only_what_python_2_7_and_3_7_have(self):
        audit, count = python_audit.run()
        self.assertGreater(count, 30, "the audit read the plugin's files")
        self.assertEqual(audit.problems, [])


if __name__ == "__main__":
    unittest.main()
