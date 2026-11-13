"""Short eased transitions for transforms that another user changes.

While someone drags an object their transform arrives in discrete updates (about 20-30 a second).
Writing each one straight into the scene looks steppy on the receiving side. Instead the object is
shown slightly in the past (about one update interval) and moves in a straight line from each received
pose to the next, so the speed stays continuous across updates. (An ease-in-out per update, as used
before, drops the speed to zero at every update and looked like stuttering.)

Echo safety: after every step the atom's dirty counters are stored (tracker.remember), so the
tracker never mistakes an intermediate pose for a local edit. The shadow already holds the final
value (the applier validated and stored the op before this runs).
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import math

import c4d

from ..core.compat import is_fatal
from ..core.log import get_logger
from . import compat
from .serializers import params

LOG = get_logger("tween")

DEFAULT_DELAY = 0.06      # seconds, for the first update after a pause
MIN_DELAY = 0.03
MAX_DELAY = 0.12          # never lag the real movement by more than this
DELAY_FACTOR = 1.25       # shown this many update intervals late: absorbs a little network jitter
RHYTHM_SMOOTHING = 0.3
MAX_BACKLOG = 0.25        # a queue of pending segments longer than this is collapsed
PAUSE = 0.4               # no update for this long: the next one starts a fresh glide

ROTATION_IDS = frozenset(value for value in (getattr(c4d, "ID_BASEOBJECT_REL_ROTATION", None),) if value is not None)


def glidable_keys(patch_params, data_params):
    """The parameter keys of an update that can glide, or None when the update has to be applied at
    once (anything other than plain, fully known transform vectors)."""
    keys = list(patch_params)
    if not keys:
        return None
    for key in keys:
        if "/" in key:
            return None
        try:
            first = int(key.split(":")[0])
        except ValueError:
            return None
        if first not in params.TRANSFORM_IDS:
            return None
        value = data_params.get(key)
        if not (isinstance(value, dict) and isinstance(value.get("V"), list) and None not in value["V"]):
            return None
    return keys


def _shortest(start, goal):
    """Rotation component that takes the short way round (angles in radians)."""
    delta = (goal - start + math.pi) % (2 * math.pi) - math.pi
    return start + delta


class TransformTweens(object):
    def __init__(self, tracker):
        self._tracker = tracker
        self._active = {}   # (uuid, key) -> state
        self._last_update = {}
        self._rhythm = {}   # (uuid, key) -> smoothed seconds between updates

    def __len__(self):
        return len(self._active)

    def cancel(self, uuid):
        for token in [t for t in self._active if t[0] == uuid]:
            del self._active[token]

    def clear(self):
        self._active = {}
        self._last_update = {}
        self._rhythm = {}

    def start(self, uuid, atom, keys, data_params, now):
        """Begin (or continue) gliding `atom` to the (already stored) values for `keys`."""
        for key in keys:
            desc_id = params.desc_id_of(key)
            components = data_params[key]["V"]
            goal = (float(components[0]), float(components[1]), float(components[2]))
            rotation = int(key.split(":")[0]) in ROTATION_IDS
            last = self._last_update.get((uuid, key))
            self._last_update[(uuid, key)] = now
            if last is None or now - last > PAUSE:
                self._rhythm[(uuid, key)] = DEFAULT_DELAY / DELAY_FACTOR
            else:  # smoothed time between updates: one late packet must not change the delay much
                previous = self._rhythm.get((uuid, key), now - last)
                self._rhythm[(uuid, key)] = previous + (now - last - previous) * RHYTHM_SMOOTHING
            delay = max(MIN_DELAY, min(MAX_DELAY, self._rhythm[(uuid, key)] * DELAY_FACTOR))
            state = self._active.get((uuid, key))
            segments = state["segments"] if state is not None else []
            if segments and segments[-1][1] - now <= MAX_BACKLOG:
                begin_at = max(segments[-1][1], now)  # continue where the previous segment ends
                begin = segments[-1][3]
            else:
                segments = []  # nothing pending (or too far behind): start from the pose shown now
                try:
                    current = atom.GetParameter(desc_id, compat.DESCFLAGS_GET)
                    begin = (current.x, current.y, current.z)
                except BaseException as exc_:
                    if is_fatal(exc_):
                        raise
                    begin = goal
                begin_at = now
            end_at = max(now + delay, begin_at + MIN_DELAY)
            if rotation:
                reach = tuple(_shortest(b, g) for b, g in zip(begin, goal))
            else:
                reach = goal
            segments.append([begin_at, end_at, begin, reach])
            self._active[(uuid, key)] = {"atom": atom, "desc": desc_id, "segments": segments, "goal": goal}

    def step(self, now):
        """Advance every glide. Returns True while any is still running (the caller keeps redrawing)."""
        if not self._active:
            return False
        for token in list(self._active):
            state = self._active[token]
            uuid = token[0]
            atom = state["atom"]
            segments = state["segments"]
            while len(segments) > 1 and now >= segments[0][1]:
                segments.pop(0)
            begin_at, end_at, begin, reach = segments[0]
            done = now >= end_at and len(segments) == 1
            try:
                if done:
                    values = state["goal"]
                elif now <= begin_at:
                    values = begin
                else:
                    fraction = min(1.0, (now - begin_at) / (end_at - begin_at))
                    values = tuple(b + (g - b) * fraction for b, g in zip(begin, reach))
                atom.SetParameter(state["desc"], c4d.Vector(*values), compat.DESCFLAGS_SET)
                self._tracker.remember(uuid, atom)
            except BaseException as exc_:
                if is_fatal(exc_):
                    raise
                done = True  # the object is gone or refuses the write: drop the glide
            if done:
                del self._active[token]
        return bool(self._active)
