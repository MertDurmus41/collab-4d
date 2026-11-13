"""Main-thread bridge between Cinema 4D and the session.

- The network thread calls `wake()` -> c4d.SpecialEventAdd(MESSAGE_PLUGIN).
- Cinema 4D then calls CollabMessageData.CoreMessage on the main thread,
  which pumps the session. A timer does the same while a session is active,
  so periodic checks (selection, frame, change scan) also run.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import logging
import os
import time

import c4d

from .. import PLUGIN_VERSION
from ..core.compat import is_fatal
from ..core.log import get_logger, log_to_file
from ..core.session import CollabSession
from . import compat, plugin_ids, presence
from .serializers import xpresso
from .sync import SceneSync

LOG = get_logger("c4d")
TIMER_MS = 20

_SESSION = None
_SYNC = None
_LOG_HANDLER = None


# Events that only change what is drawn on top of the scene (cameras, who selected what, user list).
# They need the viewports repainted, not the whole scene re-evaluated: EventAdd() is far heavier.
PRESENCE_KINDS = frozenset(["presence", "locks", "users", "lock_denied"])
REPAINT_FLAGS = getattr(c4d, "DRAWFLAGS_NO_THREAD", 0) | getattr(c4d, "DRAWFLAGS_NO_ANIMATION", 0)


def repaint():
    """Redraw the viewports without re-evaluating the scene."""
    if hasattr(c4d, "DrawViews"):
        c4d.DrawViews(REPAINT_FLAGS)
    else:  # pragma: no cover - very old versions
        c4d.EventAdd()


def wake():
    """Safe to call from any thread."""
    c4d.SpecialEventAdd(plugin_ids.MESSAGE_PLUGIN)


def session():
    global _SESSION, _SYNC
    if _SESSION is None:
        _SESSION = CollabSession(client_info=compat.client_info(PLUGIN_VERSION), notify=wake)
        _SYNC = SceneSync(_SESSION)
    return _SESSION


def sync():
    session()
    return _SYNC


def setup_logging():
    global _LOG_HANDLER
    if _LOG_HANDLER is not None:
        return
    try:
        path = os.path.join(compat.prefs_path(), "c4d_collab.log")
        _LOG_HANDLER = log_to_file(compat.native_str(path), logging.INFO)
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        print("Collab 4D: could not open log file")
        return
    try:
        # which fallbacks this Cinema 4D needs: the first thing to read in a report from any version
        LOG.info("Capabilities: %s", compat.describe_capabilities())
    except BaseException as exc_:
        if is_fatal(exc_):
            raise


MIN_PUMP_GAP = 0.012     # two timers drive the pump (the panel's and Cinema 4D's own): never twice within this
MIN_EVAL_GAP = 0.04      # scene re-evaluations (EventAdd) at most 25 per second: a rig with hundreds of
                         # expressions is re-evaluated by each one
SLOW_PUMP = 0.04
_LAST_PUMP = 0.0
_LAST_EVAL = 0.0
_EVAL_PENDING = False
_NEXT_SLOW_LOG = 0.0
_STATS = {"pumps": 0, "evals": 0, "since": 0.0, "busy": 0.0, "quiet": 0}   # what the pump did in the last 30 s (see pump())


def timer_pump():
    """The pump as the timers call it."""
    global _LAST_PUMP
    now = time.time()
    if now - _LAST_PUMP < MIN_PUMP_GAP:
        return
    _LAST_PUMP = now
    pump()


def _evaluate():
    """c4d.EventAdd, but not more often than MIN_EVAL_GAP."""
    global _LAST_EVAL, _EVAL_PENDING
    now = time.time()
    if compat.is_playing():
        _EVAL_PENDING = False   # the timeline evaluates the scene on every frame already: no extra evaluation on top
        return True
    if now - _LAST_EVAL >= MIN_EVAL_GAP:
        _LAST_EVAL = now
        _EVAL_PENDING = False
        _STATS["evals"] += 1
        c4d.EventAdd()
        return True
    _EVAL_PENDING = True  # asked too soon: the next pump evaluates, so no change is left unshown
    return False


def mirror():
    """Rig copies of graphs this Cinema 4D cannot build (R19: Global Matrix ports): cheap, so it runs whenever the scene
    changed or the pump turns, and it asks for the evaluation that moves what hangs under the copies. Never raises."""
    if _SESSION is None or not _SESSION.active:
        return False   # no session: a left guest document is no one's to move
    try:
        if xpresso.apply_mirrors():
            global _LAST_EVAL
            _LAST_EVAL = 0.0   # the copies are what the viewport must show next: no waiting for the evaluation gap
            return True
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        LOG.exception("Rig copies failed")
    return False


def pump():
    """Process network messages on the main thread. Never raises."""
    global _NEXT_SLOW_LOG
    current = _SESSION
    if current is None or not current.active:
        return
    started = time.time()
    evaluate = False
    try:
        if current.pump():
            if current.last_kinds - PRESENCE_KINDS:
                evaluate = True
            else:
                repaint()
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        LOG.exception("Session pump failed")
    mid = time.time()
    evaluate = mirror() or evaluate   # before the scan: a scan that takes long must not hold the head back
    tick()
    ticked = time.time()
    animate(current, evaluate)
    done = time.time()
    _STATS["pumps"] += 1
    _STATS["busy"] += done - started
    if done - _STATS["since"] >= 30.0:
        share = 100.0 * _STATS["busy"] / (done - _STATS["since"])
        _STATS["quiet"] += 1
        if _STATS["since"] and (share >= 10.0 or _STATS["quiet"] >= 20):   # only a busy pump, or one line in ten minutes
            _STATS["quiet"] = 0
            LOG.info("Pump stats: %d pumps, %d scene evaluations asked, %.0f%% of the time in the pump (last %d s)%s",
                     _STATS["pumps"], _STATS["evals"], 100.0 * _STATS["busy"] / (done - _STATS["since"]), done - _STATS["since"],
                     ", timeline playing" if compat.is_playing() else "")
        _STATS.update(pumps=0, evals=0, busy=0.0, since=done)
    if done - started > SLOW_PUMP and done >= _NEXT_SLOW_LOG:
        _NEXT_SLOW_LOG = done + 10.0
        LOG.info("Slow pump: %d ms (network %d, scan %d, animation %d)", (done - started) * 1000,
                 (mid - started) * 1000, (ticked - mid) * 1000, (done - ticked) * 1000)


def animate(current, evaluate=False):
    """One animation step: other users' cameras ease toward their latest pose and their transforms
    glide. Keeps asking for repaints while anything is still moving."""
    try:
        now = time.time()
        gliding = _SYNC is not None and _SYNC.advance(now)
        easing = presence.advance(current, now)
        if gliding or evaluate or _EVAL_PENDING:
            if not _evaluate():
                return  # too soon after the last evaluation; the next pump asks again (gliding goes on)
        elif easing:
            repaint()
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        LOG.exception("Animation step failed")


_TICK_AFTER = 0.0
MAX_TICK_BREATHER = 0.25


def tick():
    """Scan and send local scene changes. Never raises. A tick that takes long is followed by a
    proportional pause, so a big scene cannot keep the main thread busy with scanning alone."""
    global _TICK_AFTER
    if _SYNC is None or _SESSION is None or not _SESSION.active:
        return
    started = time.time()
    if started < _TICK_AFTER:
        return
    try:
        _SYNC.tick()
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        LOG.exception("Scene scan failed")
    _TICK_AFTER = time.time() + min(MAX_TICK_BREATHER, (time.time() - started) * 2.0)


def shutdown():
    if _SESSION is not None and _SESSION.active:
        _SESSION.leave("Cinema 4D is closing")


class CollabMessageData(c4d.plugins.MessageData):
    def GetTimer(self):
        return TIMER_MS if _SESSION is not None and _SESSION.active else 0

    def CoreMessage(self, id, bc):
        if id == plugin_ids.MESSAGE_PLUGIN:
            pump()
        elif id == c4d.MSG_TIMER:
            timer_pump()
        elif id == c4d.EVMSG_CHANGE:
            if mirror():
                _evaluate()
            tick()
        return True


def register():
    # Positional arguments: keyword names are not guaranteed on old versions.
    return c4d.plugins.RegisterMessagePlugin(
        plugin_ids.MESSAGE_PLUGIN, compat.native_str("Collab 4D Bridge"), 0, CollabMessageData())
