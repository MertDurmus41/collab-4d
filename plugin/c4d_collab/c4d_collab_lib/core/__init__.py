"""DCC-agnostic core: protocol, scene model, diff, locks, client and server.

Rule: nothing in this package may import `c4d` (see ARCHITECTURE.md).
Everything must run on Python 2.7 and 3.x using only the standard library.
"""
from __future__ import absolute_import, division, print_function, unicode_literals
