"""Make `c4d_collab_lib` importable from the tests (Python 2.7 and 3.x)."""
from __future__ import absolute_import

import os
import sys

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin", "c4d_collab"))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)
