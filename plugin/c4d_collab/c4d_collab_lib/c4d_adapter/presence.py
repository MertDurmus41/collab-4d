"""Viewport overlay: other users' cameras and selections, drawn in the 3D view.

Cinema 4D lets only objects draw into the viewport from Python (SceneHooks are C++
only). So during a session a small hidden object of our own type lives in the session
document; its Draw() paints the overlay. It is never synced (see nodes.object_supported)
and is removed when the session ends.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import time

import c4d

from ..core.compat import is_fatal
from ..core.log import get_logger
from . import compat, ids, overlay, plugin_ids

LOG = get_logger("presence")

TAG_HEIGHT = 18
CHAR_WIDTH = 8
# Drawing pass. The OBJECT pass keeps our pen colours and is shown on both R19 and 2023. The
# highlight pass was tried and rejected from live screenshots: Cinema 4D paints it in its own
# highlight colour (white, see BaseDraw.GetHighlightPassColor) and R19 does not show it at all.
#
# The object pass has one catch (first live run on R19: cubes lost their front faces and showed their
# insides): 2D drawing (name tags) switches depth testing off and R19's older pipeline does not turn it
# back on for the next object. Two measures: the overlay object always sits LAST in the document, so
# every scene object has been drawn before it, and Draw() gives the drawing state back explicitly.
DRAW_PASS = c4d.DRAWPASS_OBJECT
LINE_FLAGS = 0
STATE_PARAMS = ("DRAW_PARAMETER_USE_Z", "DRAW_PARAMETER_SETZ", "DRAW_PARAMETER_OGL_CULLING")
SMOOTHER = overlay.CameraSmoother()
_reported = set()
_announced = set()
PREVIEW = {"on": False}  # panel button: draw a demo overlay without needing a second user
PREVIEW_COLOR = [1.0, 0.6, 0.1]


def _report(where):
    """Log a drawing problem once; a bug here must never spam the console or break the viewport."""
    if where not in _reported:
        _reported.add(where)
        LOG.exception("Presence overlay failed in %s", where)


def _save_state(bd):
    """Drawing parameters we may disturb, read before drawing so they can be handed back."""
    saved = {}
    for name in STATE_PARAMS:
        param = getattr(c4d, name, None)
        if param is None:
            continue
        try:
            saved[param] = bd.GetDrawParam(param)
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
    return saved


def _restore_state(bd, op, saved):
    """Give the drawing state back: parameters, depth testing and the object matrix."""
    for param, value in saved.items():
        try:
            bd.SetDrawParam(param, value)
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
    for step in (lambda: bd.SetDepth(True), lambda: bd.SetMatrix_Matrix(op, op.GetMg())):
        try:
            step()
        except BaseException as exc_:
            if is_fatal(exc_):
                raise


def _announce(key, message, *args):
    """Log a milestone once (first Draw call, first non-empty overlay...) so the log file can
    prove that drawing really happens in a live window."""
    if key not in _announced:
        _announced.add(key)
        LOG.info(message, *args)


def preview_overlay(doc, bd):
    """A demo overlay: a camera icon in front of the viewer and a box around the active objects."""
    camera = bd.GetSceneCamera(doc)
    if camera is None:
        camera = bd.GetEditorCamera()
    mg = camera.GetMg()
    off, right, forward = mg.off, mg.v1, mg.v3
    ahead = (off.x + forward.x * 600, off.y + forward.y * 600, off.z + forward.z * 600)
    shared = [ahead[0], ahead[1], ahead[2], -right.x, -right.y, -right.z, mg.v2.x, mg.v2.y, mg.v2.z,
              -forward.x, -forward.y, -forward.z]  # looking back at the viewer
    atoms = list(doc.GetActiveObjects(compat.DESCFLAGS_GET_ACTIVE) or [])
    locks = dict(("preview-%d" % i, "preview") for i in range(len(atoms)))
    resolve_atoms = _resolve_atom
    return overlay.build({"preview": {"name": "Preview", "color": PREVIEW_COLOR}},
                         {"preview": {"cam": shared}}, locks, "me", (off.x, off.y, off.z),
                         lambda uuid: resolve_atoms(atoms[int(uuid.split("-")[1])]))


def viewer_camera(doc):
    """The camera of the active viewport (scene camera if the view uses one, else the editor camera)."""
    try:
        bd = doc.GetActiveBaseDraw()
        if bd is None:
            return None
        camera = bd.GetSceneCamera(doc)
        return camera if camera is not None else bd.GetEditorCamera()
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        _report("viewer_camera")
        return None


def camera_values(camera):
    """The camera as 12 numbers: position, then right/up/forward axes."""
    mg = camera.GetMg()
    values = [round(mg.off.x, 1), round(mg.off.y, 1), round(mg.off.z, 1)]
    for axis in (mg.v1, mg.v2, mg.v3):
        values.extend((round(axis.x, 3), round(axis.y, 3), round(axis.z, 3)))
    return values


MAX_SHAPE_POLYGONS = 400    # bigger meshes fall back to the bounding box: a thousand lines cost milliseconds
MAX_SHAPE_EDGES = 600       # per redraw (measured on R19: 850 lines) and say less than a box
MAX_SHAPE_PIECES = 64
_SHAPES = {}                # key -> (dirty, [([local points], [(i, j) edges])] or None): never Cinema 4D objects
_WORLD = {}                 # key -> (matrices, world segments): reused while the object stands still


def _collect_pieces(obj, out, siblings, depth=0):
    """The polygon objects that make up what is shown for `obj` (generators show their cache)."""
    while obj is not None and len(out) < MAX_SHAPE_PIECES and depth < 8:
        if isinstance(obj, c4d.PolygonObject):
            deformed = obj.GetDeformCache()
            out.append(deformed if isinstance(deformed, c4d.PolygonObject) else obj)
        else:
            sub = obj.GetDeformCache() or obj.GetCache()
            if sub is not None:
                _collect_pieces(sub, out, True, depth + 1)
        _collect_pieces(obj.GetDown(), out, True, depth + 1)
        if not siblings:
            break
        obj = obj.GetNext()


def _piece_edges(piece):
    polygons = piece.GetAllPolygons()
    if len(polygons) > MAX_SHAPE_POLYGONS:
        return None
    seen = set()
    for poly in polygons:
        corners = (poly.a, poly.b, poly.c) if poly.c == poly.d else (poly.a, poly.b, poly.c, poly.d)
        for index, start in enumerate(corners):
            end = corners[(index + 1) % len(corners)]
            seen.add((start, end) if start < end else (end, start))
    return sorted(seen)


def _live_pieces(atom):
    """The polygon objects shown for `atom` right now. Asked again on every redraw: they are Cinema 4D's caches, which it
    frees and rebuilds whenever it likes, so they are never kept (a kept one is a dangling object; reading it crashed R19
    with an access violation inside the Python wrapper, where 2023 only raises an error)."""
    found = []
    _collect_pieces(atom.GetDeformCache() or atom.GetCache() or atom, found, False)
    return found


def _shape_pieces(found):
    """[(local points, edges)] of the pieces `found`, or None when the shape is too big to draw."""
    shapes, total = [], 0
    for piece in found:
        edges = _piece_edges(piece)
        if edges is None:
            return None
        total += len(edges)
        if total > MAX_SHAPE_EDGES:
            return None
        shapes.append(([(p.x, p.y, p.z) for p in piece.GetAllPoints()], edges))
    return shapes or None


def _shape_dirty(atom):
    flags = getattr(c4d, "DIRTYFLAGS_DATA", 0) | getattr(c4d, "DIRTYFLAGS_CACHE", 0)
    return atom.GetDirty(flags)


def object_outline(atom, key=None):
    """The real outline of an object as world-space segments, or None (null objects, splines,
    huge meshes: the caller then shows the bounding box). The mesh is read again only when the
    object's data changed; moving it only re-transforms the cached points."""
    try:
        dirty = _shape_dirty(atom)
        found = _live_pieces(atom)
        cached = _SHAPES.get(key) if key is not None else None
        if cached is not None and cached[0] == dirty and cached[1] is not None and len(cached[1]) == len(found):
            shapes = cached[1]
        else:
            shapes = _shape_pieces(found)
            if key is not None:
                if len(_SHAPES) > 256:
                    _SHAPES.clear()
                _SHAPES[key] = (dirty, shapes)
        if not shapes or len(shapes) != len(found):
            return None
        matrices = []
        for piece in found:
            mg = piece.GetMg()
            matrices.append(((mg.off.x, mg.off.y, mg.off.z), (mg.v1.x, mg.v1.y, mg.v1.z),
                             (mg.v2.x, mg.v2.y, mg.v2.z), (mg.v3.x, mg.v3.y, mg.v3.z)))
        stamp = (dirty, tuple(matrices))
        if key is not None:
            reuse = _WORLD.get(key)
            if reuse is not None and reuse[0] == stamp:
                return reuse[1]  # nothing moved or changed since the last redraw
        segments = []
        for (points, edges), (o, a, b, c) in zip(shapes, matrices):
            world = [(o[0] + a[0] * x + b[0] * y + c[0] * z,
                      o[1] + a[1] * x + b[1] * y + c[1] * z,
                      o[2] + a[2] * x + b[2] * y + c[2] * z) for x, y, z in points]
            segments.extend((world[i], world[j]) for i, j in edges)
        if key is not None:
            if len(_WORLD) > 256:
                _WORLD.clear()
            _WORLD[key] = (stamp, segments)
        return segments
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        if key is not None:
            _SHAPES.pop(key, None)
            _WORLD.pop(key, None)
        _announce("outline", "Presence overlay: could not read the object's shape, using its box")
        return None


def _resolve_atom(atom, key=None):
    if isinstance(atom, c4d.BaseTag):
        atom = atom.GetObject()  # a locked tag is drawn as its owner
    if atom is None or not hasattr(atom, "GetRad"):
        return None
    mg, mp, rad = atom.GetMg(), atom.GetMp(), atom.GetRad()
    info = ((mg.off.x, mg.off.y, mg.off.z),
            ((mg.v1.x, mg.v1.y, mg.v1.z), (mg.v2.x, mg.v2.y, mg.v2.z), (mg.v3.x, mg.v3.y, mg.v3.z)),
            (mp.x, mp.y, mp.z), (rad.x, rad.y, rad.z))
    outline = object_outline(atom, key)
    return info + (outline,) if outline else info


def _resolve(registry):
    def resolve(uuid):
        atom = registry.get(uuid)
        return _resolve_atom(atom, uuid) if atom is not None else None
    return resolve


def current_overlay(doc, viewer_pos, session=None, sync=None):
    """Overlay for the running session, or None when there is nothing to draw.
    (session/sync default to the plugin's own; tests pass their instances.)"""
    if session is None:
        from . import bridge  # late: bridge imports sync, which imports this module
        session, sync = bridge.session(), bridge.sync()
    mirror = session.mirror
    if mirror is None or not mirror.synced or sync is None or not sync.attached:
        return None
    if sync.doc != doc:
        return None
    advance(session, time.time())  # keeps the eased poses current even if no timer is running
    shown = dict((user_id, {"cam": pose}) for user_id, pose in SMOOTHER.poses().items())
    return overlay.build(mirror.users, shown, mirror.locks, mirror.user_id,
                         viewer_pos, _resolve(sync.registry))


def advance(session, now):
    """Follow every remote camera; True while any is still easing (the caller keeps redrawing)."""
    mirror = session.mirror
    if mirror is None:
        SMOOTHER.reset()
        return False
    cams = dict((user_id, data.get("cam")) for user_id, data in mirror.presence.items()
                if user_id != mirror.user_id and isinstance(data, dict))
    SMOOTHER.retarget(cams)
    return SMOOTHER.advance(now)


def reset_smoothing():
    SMOOTHER.reset()


def draw_overlay(doc, bd, content):
    """Paint an Overlay into one viewport (world-space lines, then name tags)."""
    bd.SetMatrix_Matrix(None, c4d.Matrix())
    pen = None
    for start, end, color in content.lines:
        if color != pen:  # outlines have thousands of segments in one colour
            bd.SetPen(c4d.Vector(*color))
            pen = color
        bd.DrawLine(c4d.Vector(*start), c4d.Vector(*end), LINE_FLAGS)
    if not content.labels:
        return
    bd.SetMatrix_Screen()
    has_text = hasattr(bd, "DrawHUDText")   # new in R18: R17 draws the coloured frames only
    for position, text, color in content.labels:
        screen = bd.WS(c4d.Vector(*position))
        if screen.z <= 0:
            continue  # behind the viewer
        x, y = int(screen.x), int(screen.y)
        width = CHAR_WIDTH * len(text) + 10
        bd.SetPen(c4d.Vector(*color))
        # coloured name tag: frame + text, so who is who is clear even where text colour is fixed
        for (x1, y1, x2, y2) in ((x, y - TAG_HEIGHT, x + width, y - TAG_HEIGHT), (x + width, y - TAG_HEIGHT, x + width, y),
                                 (x + width, y, x, y), (x, y, x, y - TAG_HEIGHT)):
            bd.DrawLine2D(c4d.Vector(x1, y1, 0), c4d.Vector(x2, y2, 0))
        if has_text:
            bd.DrawHUDText(x + 5, y - TAG_HEIGHT + 2, compat.native_str(text))


class PresenceObject(c4d.plugins.ObjectData):
    def Init(self, node):
        return True

    def Draw(self, op, drawpass, bd, bh):
        if drawpass != DRAW_PASS:
            return c4d.DRAWRESULT_SKIP
        saved = _save_state(bd)
        try:
            _announce("draw", "Presence overlay: Draw() is being called by Cinema 4D")
            doc = op.GetDocument()
            viewer = viewer_pos_of(bd, doc)
            content = current_overlay(doc, viewer)
            if PREVIEW["on"]:
                demo = preview_overlay(doc, bd)
                if content is None:
                    content = demo
                else:
                    content.lines.extend(demo.lines)
                    content.labels.extend(demo.labels)
            if content is not None:
                if content.lines or content.labels:
                    # one log line per distinct overlay shape: 10 lines + 1 tag is a camera icon,
                    # 22 lines + 2 tags a camera plus one selected object (12 lines for its box)
                    _announce(("painted", len(content.lines), len(content.labels)),
                              "Presence overlay: painting %d lines and %d name tags",
                              len(content.lines), len(content.labels))
                draw_overlay(doc, bd, content)
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            _report("Draw")
        finally:
            _restore_state(bd, op, saved)
        return c4d.DRAWRESULT_OK


def viewer_pos_of(bd, doc):
    try:
        camera = bd.GetSceneCamera(doc)
        if camera is None:
            camera = bd.GetEditorCamera()
        off = camera.GetMg().off
        return (off.x, off.y, off.z)
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        return None


# Lifecycle -----------------------------------------------------------------------------

DESCRIPTION = "Opcollabpresence"  # res/description/Opcollabpresence.res


def _icon():
    """A small solid icon: object plugins may refuse to register without one."""
    try:
        bitmap = c4d.bitmaps.BaseBitmap()
        bitmap.Init(32, 32)
        for x in range(32):
            for y in range(32):
                bitmap.SetPixel(x, y, 240, 150, 30)
        return bitmap
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        return None


def register(registrar=None):
    """Register the overlay object type. Tries a few registration styles and logs each result
    (the log file is the only place to see why on a machine we cannot reach).
    Returns True when the type is registered.

    `registrar` must be a function defined in the plugin's .pyp file that calls
    c4d.plugins.RegisterObjectPlugin: Cinema 4D looks for the special variable `__res__` in the
    module making that call, and only the .pyp has it ("Could not find required '__res__'",
    found in the first live run)."""
    registrar = registrar or c4d.plugins.RegisterObjectPlugin
    name = compat.native_str("Collab 4D Presence")
    icon = _icon()
    # The object only draws the other users in the viewport and the plugin adds it by itself: nobody adds it by hand, so it
    # stays out of the Extensions/Plugins menu.
    hide = getattr(c4d, "PLUGINFLAG_HIDEPLUGINMENU", 0)
    variants = (
        ("icon, no description", compat.native_str(""), hide, icon),
        ("no icon, no description", compat.native_str(""), hide, None),
        ("icon + description resource", compat.native_str(DESCRIPTION), hide, icon),
    )
    registered = False
    for label, description, info, bitmap in variants:
        try:
            # Positional arguments: keyword names are not guaranteed on old versions.
            result = registrar(plugin_ids.PRESENCE_OBJECT, name, PresenceObject, description, info, bitmap)
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            LOG.warning("Presence object registration (%s) raised %s: %s", label, type(exc_).__name__, exc_)
            continue
        LOG.info("Presence object registration (%s) returned %r", label, result)
        if not result:
            continue
        registered = True
        probe = compat.allocate(c4d.BaseObject, plugin_ids.PRESENCE_OBJECT)
        if probe is not None:
            LOG.info("Presence object registered (%s) and can be created", label)
            return True
        LOG.warning("Presence object registered (%s) but cannot be created yet: %s", label,
                    compat.allocate.last_error)
    return registered


def find(doc):
    obj = doc.GetFirstObject()
    stack = []
    while obj is not None:
        if obj.GetType() == plugin_ids.PRESENCE_OBJECT:
            return obj
        if obj.GetDown() is not None:
            stack.append(obj.GetNext())
            obj = obj.GetDown()
        else:
            obj = obj.GetNext()
            while obj is None and stack:
                obj = stack.pop()
    return None


def set_preview(doc, on):
    """Turn the demo overlay on or off (the draw object must exist while it is on)."""
    PREVIEW["on"] = bool(on)
    if on:
        ensure(doc)
    c4d.EventAdd()


def ensure(doc):
    """Make sure the session document has its (hidden) overlay object. Returns it or None."""
    existing = find(doc)
    if existing is not None:
        return existing
    obj = compat.allocate(c4d.BaseObject, plugin_ids.PRESENCE_OBJECT)
    if obj is None:
        LOG.warning("Presence object type is not available (%s); the viewport overlay is off",
                    compat.allocate.last_error)
        return None
    obj.SetName(compat.native_str("Collab 4D Presence"))
    obj.ChangeNBit(c4d.NBIT_OHIDE, c4d.NBITCONTROL_SET)  # hidden in the Object Manager
    doc.InsertObject(obj, None, _last_root(doc))  # last: drawn after every scene object
    obj.DelBit(c4d.BIT_ACTIVE)  # it showed up as the selected object in the Attribute Manager
    return obj


def _last_root(doc, ignore=None):
    last = doc.GetFirstObject()
    while last is not None and (last.GetNext() is not None or last == ignore):
        if last.GetNext() is None:
            return None
        last = last.GetNext()
    return last


def is_last(obj):
    return obj.GetUp() is None and obj.GetNext() is None


def move_last(doc, obj):
    """Put the overlay object back at the end of the document (new objects may have been added
    after it). O(1) to check, rare to act."""
    if is_last(obj):
        return
    obj.Remove()
    doc.InsertObject(obj, None, _last_root(doc))
    obj.DelBit(c4d.BIT_ACTIVE)


def remove(doc):
    _SHAPES.clear()
    _WORLD.clear()
    if doc is None or not ids.is_alive(doc):
        return
    obj = find(doc)
    while obj is not None:
        obj.Remove()
        obj = find(doc)
