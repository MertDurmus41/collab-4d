# -*- coding: utf-8 -*-
"""Collab 4D - real-time collaboration for Cinema 4D (R17 - 2026).

Entry point loaded by Cinema 4D. Keeps this file tiny: everything lives in
c4d_collab_lib (see ARCHITECTURE.md).
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import os
import sys

import c4d

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from c4d_collab_lib import PLUGIN_VERSION  # noqa: E402
from c4d_collab_lib.c4d_adapter import bridge, presence, ui_dialog  # noqa: E402


def _register_object(*args):
    """RegisterObjectPlugin must be called from this file: it needs this module's `__res__`."""
    return c4d.plugins.RegisterObjectPlugin(*args)


def _register_prefs(*args):
    """RegisterPreferencePlugin must be called from this file too: the page's description is looked up in
    this module's resource (`__res__`), like the presence object."""
    register = getattr(c4d.plugins, "RegisterPreferencePlugin", None)   # new in R19
    return register(*args) if register is not None else False


def _register_mcp():
    """The "MCP" page in Edit > Preferences and the message plugin that runs its commands. Everything in
    here is optional: whatever goes wrong, Collab 4D itself still loads."""
    try:
        from c4d_collab_lib.c4d_adapter import mcp_prefs, mcp_service
        if not mcp_prefs.available():
            return False   # R17/R18 have no preference pages: nothing could switch the server on
        page = mcp_prefs.register(_register_prefs)
        if not page:
            print("Collab 4D: the MCP preferences page could not be registered (details: c4d_collab.log)")
            return False   # without the page nothing switches the server on, so no timer is needed either
        return bool(mcp_service.register_message())
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        print("Collab 4D: MCP page unavailable (%s)" % exc)
        return False


def _mcp_service():
    try:
        from c4d_collab_lib.c4d_adapter import mcp_service
        return mcp_service
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return None


def _ensure_resource():
    """C4D normally provides `__res__` (the plugin's res folder); create it if it is missing."""
    if "__res__" not in globals():
        try:
            resource = c4d.plugins.GeResource()
            resource.Init(os.path.join(_HERE, "res"))
            globals()["__res__"] = resource
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            print("Collab 4D: could not load the resource folder (%s)" % exc)


def _run_selftest():
    """Developer hook for automated tests on versions without c4dpy (R17-R20).

    Only active when the C4D_COLLAB_SELFTEST environment variable points to a
    script, e.g. when tests/c4d_real/run.py starts Commandline.exe. Normal
    users never set it, so nothing happens in a regular Cinema 4D session.
    """
    script = os.environ.get("C4D_COLLAB_SELFTEST")
    if not script:
        return False
    namespace = {"__name__": "__c4d_collab_selftest__", "__file__": script}
    with open(script, "rb") as handle:
        exec(compile(handle.read(), script, "exec"), namespace)
    return True


def PluginMessage(id, data):
    if id == c4d.C4DPL_ENDACTIVITY:
        service = _mcp_service()
        if service is not None:
            service.shutdown()
        bridge.shutdown()
        return True
    if id == getattr(c4d, "C4DPL_PROGRAM_STARTED", None):
        service = _mcp_service()
        if service is not None:
            service.autostart()
        return False
    if id == c4d.C4DPL_COMMANDLINEARGS:
        return _run_selftest()
    return False


if __name__ == "__main__":
    bridge.setup_logging()
    ok = bridge.register() and ui_dialog.register()
    try:
        _ensure_resource()
        if not presence.register(_register_object):
            print("Collab 4D: viewport overlay could not be registered (details: c4d_collab.log)")
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        print("Collab 4D: viewport overlay unavailable (%s)" % exc)
    mcp_ok = _register_mcp()
    from c4d_collab_lib.c4d_adapter import selftest
    if selftest.enabled(_HERE):  # developer checkouts only, see selftest.py
        selftest.register(_HERE)
    print("Collab 4D %s %s%s" % (PLUGIN_VERSION, "loaded" if ok else "FAILED to register",
                                  "" if mcp_ok else " (without the MCP page: needs Cinema 4D R19 or newer)"))
