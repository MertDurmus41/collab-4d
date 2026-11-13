"""Object and tag serialization, plus the security/support policy for types.

Node data layout (all sections optional):
    object: {"p": {param key: value}, "g": {...}}   g = mesh or spline geometry
    object, tag: {"ud": [[id, type, creator, {...}], ...]}   ud = user data elements (userdata.py)
    tag:    {"p": {...}, "s": {...}}                s = selection tag content
            {"p": {...}, "xp": {...}}               xp = XPresso graph of standard nodes (xpresso.py)
            {"p": {...}, "hd": {...}}               hd = hidden settings of IK/IK-Spline/Constraint tags (hiddendata.py)
            {"p": {...}, "uv": {...}}               uv = UVW tag content
            {"p": {...}, "cw": {...}}               cw = skin weights (rig.py)
            {"p": {...}, "pm": {...}}               pm = pose morph shapes and strengths (morph.py)
    material: {"p": {...}, "f": {...}, "rl": [[flags, name], ...]}   rl = reflectance layers (layers.py)
See geometry.py for the chunked section formats.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import os

import c4d

from ...core.compat import is_fatal, string_types
from ...core.log import get_logger
from .. import compat, plugin_ids
from . import assets, geometry, hiddendata, layers, morph, params, rig, userdata, xpresso

LOG = get_logger("nodes")

# SECURITY: types that carry executable code are never sent nor created from
# network data (see ARCHITECTURE.md 6b). Two layers, because old versions do not
# define every constant (R19 has no c4d.Opython, measured in the Phase 3 test):
#   1. known numeric ids (verified on 2023) plus whatever constants exist here
#   2. the type name: anything called Python / COFFEE / XPresso is treated as code
CODE_OBJECTS = compat.type_ids("Opython", "Omgpython", "Fpython", "Omgcoffee") | frozenset([
    1023866,    # Python generator
    1025800,    # Python effector
    440000277,  # Python field
])
CODE_TAGS = compat.type_ids("Tpython", "Texpresso", "Tcoffeeexpression") | frozenset([
    1022749,    # Python tag
    1001149,    # XPresso tag (may contain Python/COFFEE nodes)
])
CODE_SHADERS = compat.type_ids("Xpython", "Xcoffee")
CODE_NAME_HINTS = ("python", "coffee", "xpresso")


def carries_code(atom, code_types):
    if atom.GetType() in code_types:
        return True
    try:
        name = compat.from_c4d_str(atom.GetTypeName()).lower()
    except Exception:
        return False
    return any(hint in name for hint in CODE_NAME_HINTS)


# Tags whose content lives outside the description and is not synced (yet).
# Selection and UVW tags were here until Phase 6, Pose Morph until Phase 8.
UNSUPPORTED_TAGS = compat.type_ids(
    "Tvertexmap", "Tnormal", "Tpoint", "Tpolygon", "Tsegment", "Tline", "Ttangent",
)

POLYGON_TYPES = geometry.POLYGON_TYPES
SPLINE_TYPES = geometry.SPLINE_TYPES
TAG_VISIBLE = getattr(c4d, "TAG_VISIBLE", 0)


LOCAL_OBJECTS = frozenset([plugin_ids.PRESENCE_OBJECT])  # our own overlay object: never synced


def object_supported(obj):
    return obj.GetType() not in LOCAL_OBJECTS and not carries_code(obj, CODE_OBJECTS)


def shader_supported(atom):
    """Materials and shaders: only the code-carrying ones (Python shaders...) stay local."""
    return not carries_code(atom, CODE_SHADERS)


def tag_supported(tag):
    kind = tag.GetType()
    if kind in UNSUPPORTED_TAGS:
        return False
    if xpresso.is_xpresso(tag):
        if not xpresso.is_safe(tag):
            return False   # a Python or COFFEE node (or one we do not know) in the graph: it stays local
    elif carries_code(tag, CODE_TAGS):
        return False
    if TAG_VISIBLE and not (tag.GetInfo() & TAG_VISIBLE):
        return False  # internal data tags
    return True


def creatable_object_type(ctype):
    return ctype not in CODE_OBJECTS


def creatable_tag_type(ctype):
    # An XPresso tag is created empty; load() builds only standard nodes into it (a graph with code in it is refused)
    return (ctype not in CODE_TAGS or ctype == xpresso.TAG_TYPE) and ctype not in UNSUPPORTED_TAGS


def is_selection_tag(tag):
    return tag.GetType() in geometry.SELECTION_TYPES


def is_weight_tag(tag):
    return rig.is_weight_tag(tag)


def is_uvw_tag(tag):
    return tag.GetType() in geometry.UVW_TYPES


def is_pose_morph_tag(tag):
    return morph.is_pose_morph(tag)


def is_xpresso_tag(tag):
    return xpresso.is_xpresso(tag)


# Dumping ---------------------------------------------------------------------------------

def dump_object(obj, uuid_of, matrix_only=False, exclude_ids=None, with_geometry=True):
    if matrix_only:
        return {"p": params.dump(obj, uuid_of, params.TRANSFORM_IDS, exclude_ids)}
    data = {"p": params.dump(obj, uuid_of, None, exclude_ids)}
    elements = userdata.dump(obj, uuid_of)
    if elements:
        data["ud"] = elements
    kind = obj.GetType()
    if not with_geometry:
        return data
    if kind in POLYGON_TYPES:
        data["g"] = geometry.dump_polygon(obj)
    elif kind in SPLINE_TYPES:
        data["g"] = geometry.dump_spline(obj)
    return data


def _path_text(value):
    """A path as text: Filename objects (R19) and plain strings (newer versions) both occur."""
    if value is None:
        return ""
    getter = getattr(value, "GetString", None)
    return compat.from_c4d_str(getter() if getter is not None else value)


def _texture_folders(atom):
    folders = []
    try:
        doc = atom.GetDocument()
        base = _path_text(doc.GetDocumentPath()) if doc is not None else ""
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        base = ""
    if base:
        folders.extend([base, os.path.join(base, "tex")])
    lookup = getattr(c4d, "GetGlobalTexturePath", None)
    for index in range(10 if lookup is not None else 0):
        try:
            path = _path_text(lookup(index))
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            break
        if path:
            folders.append(path)
    return folders


def _absolute_texture_path(atom, value):
    """A texture path as other computers could find it: a relative path is looked up next to the
    document, in its tex folder and in the global texture folders. Not found: unchanged."""
    if not value or os.path.isabs(value):
        return value
    name = os.path.basename(value.replace("\\", "/"))
    for folder in _texture_folders(atom):
        for candidate in (os.path.join(folder, value), os.path.join(folder, name)):
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)
    return value


def dump_shading(atom, uuid_of, exclude_ids=None, known=None):
    """Material or shader: its parameters. Texture file names are made absolute, and every file
    that exists is described in "f" so it can travel as an asset (see assets.py). `known` is what
    the session already has for this node: a parameter that was redirected to a cached copy
    reports the original path again, so the redirect never looks like an edit."""
    values = params.dump(atom, uuid_of, None, exclude_ids)
    files = {}
    for key, value in list(values.items()):
        if isinstance(value, string_types) and params.is_filename_key(key):
            value = assets.original_value(value, key, known)
            value = _absolute_texture_path(atom, value)
            values[key] = value
            info = assets.describe(value)
            if info is not None:
                files[key] = {"h": info[0], "n": info[1]}
    data = {"p": values}
    if files:
        data["f"] = files
    if layers.supported(atom):
        data["rl"] = layers.dump(atom)   # also [] : a material without layers must stay without
    return data


def dump_tag(tag, uuid_of, exclude_ids=None, ensure_of=None, morph_cache=None):
    if is_pose_morph_tag(tag):
        exclude_ids = dict(exclude_ids or {})
        exclude_ids.update(morph.LOCAL_PARAMS)  # edit/animate mode is each user's own
    data = {"p": params.dump(tag, uuid_of, None, exclude_ids)}
    elements = userdata.dump(tag, uuid_of)
    if elements:
        data["ud"] = elements
    if is_selection_tag(tag):
        selection = geometry.dump_selection(tag)
        if selection is not None:
            data["s"] = selection
    elif is_uvw_tag(tag):
        data["uv"] = geometry.dump_uvw(tag)
    elif is_weight_tag(tag):
        weights = rig.dump_weights(tag, ensure_of or uuid_of)
        if weights is not None:
            data["cw"] = weights
    elif is_pose_morph_tag(tag):
        shapes = morph.dump(tag, morph_cache if morph_cache is not None else {})
        if shapes is not None:
            data["pm"] = shapes
    elif is_xpresso_tag(tag):
        graph = xpresso.dump(tag, uuid_of)
        if graph is not None:
            data["xp"] = graph
    hidden = hiddendata.dump(tag)
    if hidden is not None:
        data["hd"] = hidden
    return data


def dump_uncounted(atom, ensure_of=None, morph_cache=None):
    """Content that may move no dirty counter, so it is compared periodically:
    selection tags and spline geometry (tangents, closed flags) on every version, UVW tags
    because R19 did not report UV edits (found by the Phase 6 test on R19), pose morph shapes
    (no counter moves for point edits, renames or new morphs, measured on 2023).
    None for other atoms."""
    if isinstance(atom, c4d.BaseTag):
        if is_selection_tag(atom):
            selection = geometry.dump_selection(atom)
            return {"s": selection} if selection is not None else None
        if is_uvw_tag(atom):
            return {"uv": geometry.dump_uvw(atom)}
        if is_weight_tag(atom):
            weights = rig.dump_weights(atom, ensure_of or compat.read_uid)
            return {"cw": weights} if weights is not None else None
        if is_pose_morph_tag(atom):
            # Cheap unless the morph list or the selected pose changed (see morph.dump).
            shapes = morph.dump(atom, morph_cache if morph_cache is not None else {})
            return {"pm": shapes} if shapes is not None else None
        return None
    if atom.GetType() in SPLINE_TYPES:
        return {"g": geometry.dump_spline(atom)}
    return None


# What a patch touches ------------------------------------------------------------------

def data_change(patch):
    """Describe what a node-data patch changes, so only that is written to the scene.

    {"params": set of parameter keys,
     "geo": None | "full" | {"pts"/"polys"/"tan": {chunk ids}},
     "sel": bool, "uv": None | "full" | {"uv": {chunk ids}}}
    """
    sets = patch.get("set", {})
    sub = patch.get("sub", {})
    keys = set()
    replaced = sets.get("p")
    if isinstance(replaced, dict):
        keys.update(replaced.keys())
    nested = sub.get("p") or {}
    keys.update(nested.get("set", {}).keys())
    keys.update(nested.get("sub", {}).keys())
    if "rl" in sets or "rl" in sub:
        keys = None   # layers were added/removed/moved: their parameters are written again
    return {
        "params": keys,
        "geo": geometry.change_of(sub.get("g"), "g" in sets),
        "sel": "s" in sets or "s" in sub,
        "uv": geometry.change_of(sub.get("uv"), "uv" in sets),
        "cw": "cw" in sets or "cw" in sub,
        "pm": "pm" in sets or "pm" in sub,
        "xp": "xp" in sets or "xp" in sub,
        "ud": "ud" in sets or "ud" in sub,
        "hd": "hd" in sets or "hd" in sub,
    }


FULL_CHANGE = {"params": None, "geo": geometry.FULL, "sel": True, "uv": geometry.FULL, "cw": True, "pm": True,
               "xp": True, "ud": True, "hd": True}


# Loading ----------------------------------------------------------------------------------

def load_node(atom, data, atom_of, change=None, morph_cache=None):
    """Write node data into an atom that is already inserted in the document
    (selection and UVW tags need their owner). `change` limits what is written."""
    change = change or FULL_CHANGE
    if change.get("ud") and data.get("ud"):
        userdata.load(atom, data["ud"], atom_of, compat.read_uid)   # before the parameters that live in it
    if change["params"] is None and "rl" in data and layers.supported(atom):
        layers.sync(atom, data["rl"])   # before the parameters: they live in the layers
    if change["params"] is None or change["params"]:
        params.load(atom, data.get("p") or {}, atom_of, change["params"])
        if "rl" in data and layers.supported(atom) and layers.has_extra(atom, data["rl"]):
            layers.sync(atom, data["rl"])   # R19 adds its default layer when the Reflection channel is switched on
    if change["geo"] and "g" in data:
        kind = atom.GetType()
        if kind in POLYGON_TYPES:
            morphed = bool(morph.points_tags(atom))
            # A Pose Morph tag keeps its own copy of the base and animates the points: write the whole
            # base (the other chunks may show an animated face) and let the tag take it as its base.
            geometry.load_polygon(atom, data["g"], geometry.FULL if morphed else change["geo"])
            if morphed:
                morph.store_base(atom)
        elif kind in SPLINE_TYPES:
            geometry.load_spline(atom, data["g"], change["geo"])
    if isinstance(atom, c4d.BaseTag):
        if change["sel"] and "s" in data and is_selection_tag(atom):
            geometry.load_selection(atom, data["s"])
        if change["uv"] and "uv" in data and is_uvw_tag(atom):
            geometry.load_uvw(atom, data["uv"], change["uv"])
        if change.get("cw") and "cw" in data and is_weight_tag(atom):
            rig.load_weights(atom, data["cw"], atom_of)
        if change.get("pm") and "pm" in data and is_pose_morph_tag(atom):
            morph.load(atom, data["pm"], morph_cache if morph_cache is not None else {})
        if change.get("hd") and "hd" in data:
            hiddendata.load(atom, data["hd"])
        if change.get("xp") and "xp" in data and is_xpresso_tag(atom):
            xpresso.load(atom, data["xp"], atom_of, compat.read_uid)
