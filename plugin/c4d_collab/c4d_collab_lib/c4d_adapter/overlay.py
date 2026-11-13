"""Who is where and who selected what: the geometry of the viewport overlay.

Pure Python (no c4d import) so it can be tested without Cinema 4D. Vectors are
3-tuples of floats in world space (centimetres).

For every *other* user the overlay contains
- their viewport camera as a camera icon (pyramid + frame + "up" triangle) with a name tag,
- a box around each object they have selected (= locked), with a name tag.

Everything is drawn in the user's own colour, so you can tell who is who at a glance.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import math

from ..core.compat import number_types

MAX_COORD = 1.0e9
MIN_BOX = 15.0          # objects without size (nulls) still get a visible box
MIN_ICON, MAX_ICON = 8.0, 2000.0
ICON_SCREEN_FRACTION = 0.045   # icon size relative to the distance from the viewer


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _mul(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def _length(a):
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def _unit(a):
    size = _length(a)
    return (a[0] / size, a[1] / size, a[2] / size) if size > 1e-9 else None


def _finite(values):
    for value in values:
        if isinstance(value, bool) or not isinstance(value, number_types):
            return False
        if value != value or abs(value) > MAX_COORD:  # NaN or absurd
            return False
    return True


def parse_camera(data):
    """Camera presence [px,py,pz, rx,ry,rz, ux,uy,uz, fx,fy,fz] -> (pos, right, up, forward) or None.
    Everything arriving from the network is untrusted: shape, finiteness and axes are checked."""
    if not isinstance(data, (list, tuple)) or len(data) != 12 or not _finite(data):
        return None
    pos = tuple(float(v) for v in data[0:3])
    axes = [_unit(tuple(float(v) for v in data[i:i + 3])) for i in (3, 6, 9)]
    if None in axes:
        return None
    return (pos, axes[0], axes[1], axes[2])


def icon_size(viewer_pos, camera_pos):
    """World size of a camera icon so it looks about the same size on screen at any distance."""
    if viewer_pos is None:
        return 40.0
    distance = _length(_sub(camera_pos, viewer_pos))
    return max(MIN_ICON, min(MAX_ICON, distance * ICON_SCREEN_FRACTION))


def camera_icon(camera, size):
    """Line segments of a camera icon: the eye at `pos`, a frame ahead, a triangle marking 'up'."""
    pos, right, up, forward = camera
    centre = _add(pos, _mul(forward, size * 2.0))
    half_w, half_h = size, size * 0.6

    def corner(sx, sy):
        return _add(centre, _add(_mul(right, sx * half_w), _mul(up, sy * half_h)))
    tl, tr, br, bl = corner(-1, 1), corner(1, 1), corner(1, -1), corner(-1, -1)
    apex = _add(centre, _mul(up, half_h * 1.7))
    left_base = _add(centre, _add(_mul(up, half_h), _mul(right, -half_w * 0.4)))
    right_base = _add(centre, _add(_mul(up, half_h), _mul(right, half_w * 0.4)))
    return [(pos, tl), (pos, tr), (pos, br), (pos, bl),
            (tl, tr), (tr, br), (br, bl), (bl, tl),
            (left_base, apex), (apex, right_base)]


def box_lines(origin, axes, mp, rad):
    """The 12 edges of an object's (oriented) bounding box, plus the highest corner.

    origin/axes are the object's global matrix (axes are scaled), mp/rad its local box centre/radius."""
    rad = tuple(max(abs(r), 0.0) for r in rad)
    if max(rad) < 1e-6:
        rad = (MIN_BOX, MIN_BOX, MIN_BOX)
    corners = {}
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                local = (mp[0] + sx * rad[0], mp[1] + sy * rad[1], mp[2] + sz * rad[2])
                world = _add(origin, _add(_mul(axes[0], local[0]), _add(_mul(axes[1], local[1]), _mul(axes[2], local[2]))))
                corners[(sx, sy, sz)] = world
    edges = []
    for (sx, sy, sz), start in corners.items():
        for flip in range(3):
            other = [sx, sy, sz]
            other[flip] = -other[flip]
            if (sx, sy, sz) < tuple(other):  # each edge once
                edges.append((start, corners[tuple(other)]))
    top = max(corners.values(), key=lambda corner: corner[1])
    return edges, top


class CameraSmoother(object):
    """Eases every remote camera toward its latest reported pose.

    Poses arrive about 20 times a second; the viewport can redraw faster. Drawing the raw poses looks
    steppy ("kasma"), so the shown pose follows the target with an exponential ease (time constant TAU)
    that depends on real elapsed time, which makes it independent of how often it is advanced.
    """
    TAU = 0.07        # seconds
    EPSILON = 0.02    # below this distance the pose snaps to its target and the animation stops
    MAX_STEP = 0.25   # a long pause (stalled UI) must not make the camera fly in one jump

    def __init__(self):
        self._shown = {}
        self._target = {}
        self._at = None

    def reset(self):
        self.__init__()

    def retarget(self, cams):
        """cams: {user id: 12 numbers or anything else}. Invalid poses are ignored."""
        valid = {}
        for user_id, values in cams.items():
            if parse_camera(values) is not None:
                valid[user_id] = [float(v) for v in values]
        for user_id in list(self._shown):
            if user_id not in valid:
                del self._shown[user_id]
        for user_id, values in valid.items():
            if user_id not in self._shown:
                self._shown[user_id] = list(values)  # first sighting: appear in place, do not fly in
        self._target = valid

    def advance(self, now):
        """Move every shown pose toward its target. Returns True while anything is still moving."""
        elapsed = 0.0 if self._at is None else min(max(now - self._at, 0.0), self.MAX_STEP)
        self._at = now
        alpha = 1.0 - math.exp(-elapsed / self.TAU) if elapsed > 0 else 0.0
        moving = False
        for user_id, target in self._target.items():
            shown = self._shown[user_id]
            if max(abs(goal - now_value) for goal, now_value in zip(target, shown)) <= self.EPSILON:
                shown[:] = target
                continue
            for index, goal in enumerate(target):
                shown[index] += (goal - shown[index]) * alpha
            moving = True
        return moving

    def poses(self):
        return dict((user_id, list(values)) for user_id, values in self._shown.items())


class Overlay(object):
    def __init__(self):
        self.lines = []   # (start, end, (r, g, b))
        self.labels = []  # (world position, text, (r, g, b))


def _color(user):
    value = (user or {}).get("color")
    if isinstance(value, (list, tuple)) and len(value) == 3 and _finite(value):
        return tuple(max(0.0, min(1.0, float(v))) for v in value)
    return (0.8, 0.8, 0.8)


def build(users, presence, locks, my_id, viewer_pos, resolve):
    """Everything to draw for the other users.

    users:    {user id: {"name": ..., "color": [r, g, b]}}
    presence: {user id: {"cam": [...], ...}}
    locks:    {node uuid: user id}
    resolve:  uuid -> None | (origin, (ax, ay, az), mp, rad[, edges]) in world space for an object;
              `edges` (optional) is the object's real outline as world-space segments: drawn instead of
              the bounding box, so a pyramid is outlined as a pyramid
    """
    overlay = Overlay()
    users = dict((uid, user) for uid, user in users.items() if isinstance(user, dict))  # untrusted
    presence = presence if isinstance(presence, dict) else {}
    for user_id, user in users.items():
        if user_id == my_id:
            continue
        color = _color(user)
        name = "%s" % (user.get("name") or user_id)
        shared = presence.get(user_id)
        camera = parse_camera(shared.get("cam")) if isinstance(shared, dict) else None
        if camera is not None:
            size = icon_size(viewer_pos, camera[0])
            for start, end in camera_icon(camera, size):
                overlay.lines.append((start, end, color))
            overlay.labels.append((_add(camera[0], _mul(camera[2], size * 1.4)), name, color))
    for uuid, owner in locks.items():
        if owner == my_id or owner not in users:
            continue
        info = resolve(uuid)
        if info is None:
            continue
        color = _color(users[owner])
        edges, top = box_lines(*info[:4])
        if len(info) > 4 and info[4]:
            edges = info[4]
        for start, end in edges:
            overlay.lines.append((start, end, color))
        overlay.labels.append((_add(top, (0.0, MIN_BOX, 0.0)), "%s" % (users[owner].get("name") or owner), color))
    return overlay
