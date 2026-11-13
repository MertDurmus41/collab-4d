"""Facial and corrective shapes: the Pose Morph tag (point morphs).

Tag data section "pm":
    {"n": point count of the owner,
     "m": [{"n": name, "i": id, "d": {chunk: packed floats}} ...]   morphs in the tag's order; index 0 is the base
                                                           pose and carries no points. "d" holds the
                                                           offsets from the base (relative mode), 128
                                                           points per chunk; chunks that are all zero
                                                           are left out (a smile moves a few hundred of
                                                           thousands of points)
     "s": [strength or null, ...]}                         slider per morph; null = driven by a track,
                                                           the receiver keeps its own
"i" is the number of the morph inside the tag: its slider is the parameter (4000, 1001 + 100 * i), and that, not the
position in the list, is what XPresso ports, animation tracks and the parameters name. A morph that was moved in the
list keeps its number, so the guest builds the same numbers (see _rebuild); built in list order the numbers moved and
every driver and track of a leg moved the wrong shape (measured on a real rig: "bend" got the value of "foot front").
Only point morphs travel (the "Points" option). Position/rotation/scale, UV, parameter, map and
hierarchy morphs stay local.

Measured on 2023 (tests/c4d_manual/probe_posemorph.py):
  * morph points are read and written by expanding a morph (CAMorph.SetMode EXPAND, relative), then
    GetFirst().GetPoint / SetPoint, then collapsing again. A collapsed morph holds 1 meaningless point.
  * editing morph points, renaming, adding a morph and switching the active pose move NO dirty counter.
    Strength changes move the tag's DATA counter, RemoveMorph too.
  * reading is ~3 ms per morph of 10,000 points; writing ~17 ms. A face rig evaluated by XPresso
    changes strengths on every evaluation, so the points are read again only when the tag's shape
    signature (mode, active pose, morph names, point count) changes; otherwise the last read is reused.
  * strength sliders live at DescID (4000, 1001 + 100 * index) (GetMorphID) and are not in the
    tag's description, so the generic parameter serializer does not see them.
  * the tag writes its result into the owner's OWN points (no deform cache): in animate mode the mesh
    shows base + strength x offsets, in edit mode with a pose selected it shows that pose. Only in edit
    mode on the base pose are the mesh points the base. The real base is the base pose (morph 0) read in
    absolute mode; after new base points are written into the mesh, storing morph 0 again makes the tag
    use them. See mesh_shows_base, base_points and store_base.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import c4d

from ...core import protocol
from ...core.compat import integer_types, is_fatal, string_types
from ...core.log import get_logger
from .. import compat
from . import params, tracks

LOG = get_logger("morph")

CHUNK = 128
MAX_MORPHS = 1000
MAX_POINTS = 5000000

POSE_MORPH_TYPES = compat.type_ids("Tposemorph") | frozenset([1024237])

ID_POINTS = getattr(c4d, "ID_CA_POSE_POINTS", 1017)
ID_MODE = getattr(c4d, "ID_CA_POSE_MODE", 1001)
MODE_EDIT = getattr(c4d, "ID_CA_POSE_MODE_EDIT", 0)
MODE_ANIMATE = getattr(c4d, "ID_CA_POSE_MODE_ANIMATE", 1)

# The edit/animate switch is a per-user view state: the receiver always animates (in edit mode the
# sliders do nothing and the mesh shows whatever pose is selected locally).
LOCAL_PARAMS = {ID_MODE: None}

# The last read of each tag: {tag uuid: (signature, morph list, applied, point count)} - see the module docstring.
# Every session keeps its own (Tracker.morph_cache) and passes it in.
_CACHE_LIMIT = 4096


def _is_number(value):
    return (isinstance(value, float) or isinstance(value, integer_types)) and not isinstance(value, bool)


def is_pose_morph(tag):
    return tag.GetType() in POSE_MORPH_TYPES and hasattr(tag, "GetMorphCount")


def _flags(name):
    return getattr(c4d, name)


def _get(atom, ident):
    try:
        return atom.GetParameter(c4d.DescID(c4d.DescLevel(ident)), compat.DESCFLAGS_GET)
    except Exception:
        return None


def _in_edit_mode(tag):
    return _get(tag, ID_MODE) == MODE_EDIT


def points_tags(obj):
    """The Pose Morph tags on obj that morph its points."""
    found = []
    tag = obj.GetFirstTag()
    while tag is not None:
        if is_pose_morph(tag) and _get(tag, ID_POINTS) and tag.GetMorphCount() > 0:
            found.append(tag)
        tag = tag.GetNext()
    return found


def mesh_shows_base(obj):
    """Are the object's own points its base mesh? Not while a Pose Morph tag animates them or shows a
    pose being sculpted (measured: the tag writes into the points, there is no deform cache)."""
    for tag in points_tags(obj):
        if not _in_edit_mode(tag):
            return False
        try:
            if tag.GetActiveMorphIndex() > 0:
                return False
        except Exception:
            return False
    return True


def base_points(obj):
    """The base mesh as the first Pose Morph tag stores it (morph 0 in absolute mode), or None."""
    tags = points_tags(obj)
    if not tags:
        return None
    tag = tags[0]
    doc = tag.GetDocument()
    base = tag.GetMorph(0)
    if doc is None or base is None:
        return None
    if not base.SetMode(doc, tag, _flags("CAMORPH_MODE_FLAGS_ALL") | _flags("CAMORPH_MODE_FLAGS_EXPAND"),
                        _flags("CAMORPH_MODE_ABS")):
        return None
    try:
        node = base.GetFirst()
        if node is None:
            return None
        return [node.GetPoint(index) for index in range(node.GetPointCount())]
    finally:
        _collapse(base, doc, tag)


def store_base(obj):
    """New base points were just written into the mesh: make every Pose Morph tag on it use them."""
    for tag in points_tags(obj):
        doc = tag.GetDocument()
        if doc is not None:
            tag.GetMorph(0).Store(doc, tag, _flags("CAMORPH_DATA_FLAGS_POINTS"))
            tag.UpdateMorphs()


def _owner_points(tag):
    owner = tag.GetObject()
    if owner is None or not hasattr(owner, "GetPointCount"):
        return None
    return owner.GetPointCount()


def _signature(tag, point_count):
    names = []
    for index in range(min(tag.GetMorphCount(), MAX_MORPHS)):
        morph = tag.GetMorph(index)
        names.append(compat.from_c4d_str(morph.GetName()) if morph is not None else None)
    try:
        active = tag.GetActiveMorphIndex()
    except Exception:
        active = None
    return (_get(tag, ID_MODE), active, bool(_get(tag, ID_POINTS)), point_count, tuple(names))


def _expand(morph, doc, tag):
    return morph.SetMode(doc, tag, _flags("CAMORPH_MODE_FLAGS_ALL") | _flags("CAMORPH_MODE_FLAGS_EXPAND"),
                         _flags("CAMORPH_MODE_REL"))


def _collapse(morph, doc, tag):
    morph.SetMode(doc, tag, _flags("CAMORPH_MODE_FLAGS_ALL") | _flags("CAMORPH_MODE_FLAGS_COLLAPSE"),
                  _flags("CAMORPH_MODE_AUTO"))


def _read_offsets(morph, doc, tag, point_count):
    """{chunk: packed offsets} of one morph, without the all-zero chunks; None when unreadable."""
    if not _expand(morph, doc, tag):
        return None
    try:
        node = morph.GetFirst()
        if node is None or node.GetPointCount() != point_count:
            return None
        chunks = {}
        for start in range(0, point_count, CHUNK):
            flat = []
            moved = False
            for index in range(start, min(start + CHUNK, point_count)):
                v = node.GetPoint(index)
                flat.extend((v.x, v.y, v.z))
                moved = moved or v.x != 0.0 or v.y != 0.0 or v.z != 0.0
            if moved:
                chunks[str(start // CHUNK)] = protocol.pack_floats(flat)
        return chunks
    finally:
        _collapse(morph, doc, tag)


def _number(tag, index):
    """The number of the morph at `index` (its slider is (4000, 1001 + 100 * number)), or None."""
    try:
        desc_id = tag.GetMorphID(index)
        return (desc_id[desc_id.GetDepth() - 1].id - 1001) // 100
    except Exception:
        return None


# R19 gives a new morph the next number instead of the lowest free one (measured: [0, 3, 4] here for [0, 1, 2] sent), so the
# numbers cannot be made equal. The sliders that XPresso ports name by the sender's number are translated instead:
# {tag uuid: {sender number: number here}}, see local_slider.
NUMBER_MAP = {}


def _mapped(uuid, numbers, local):
    """Are the numbers here the ones a rebuild made for `numbers` (so the tag is as it should be)?"""
    mapping = NUMBER_MAP.get(uuid)
    return mapping is not None and len(local) == len(numbers) and [mapping.get(n) for n in numbers] == local


def local_slider(uuid, desc_id):
    """The slider DescID of the morph tag `uuid` here for the sender's slider `desc_id` (the same one when the numbers agree)."""
    mapping = NUMBER_MAP.get(uuid)
    if not mapping:
        return desc_id
    depth = desc_id.GetDepth()
    if depth != 2 or desc_id[0].id != 4000:
        return desc_id
    last = desc_id[1]
    number, offset = divmod(last.id - 1000, 100)   # a morph owns the ids 1000 + 100 * number ... +99 (its slider is +1)
    if last.id < 1000 or number not in mapping:
        return desc_id
    return c4d.DescID(desc_id[0], c4d.DescLevel(1000 + 100 * mapping[number] + offset, last.dtype, last.creator))


def sender_key(uuid, key):
    """The key of a slider (or a track on it) as the sender numbered it, for a key read from this side: the inverse of
    local_slider, so what this side reports about its own tracks reads like what the host holds (same track, same uuid)."""
    mapping = NUMBER_MAP.get(uuid)
    if not mapping or not isinstance(key, string_types):
        return key
    inverse = dict((here, sent) for sent, here in mapping.items())
    try:
        desc_id = params.desc_id_of(key)
    except (ValueError, TypeError):
        return key
    if desc_id.GetDepth() != 2 or desc_id[0].id != 4000 or desc_id[1].id < 1000:
        return key
    number, offset = divmod(desc_id[1].id - 1000, 100)
    if number not in inverse:
        return key
    last = desc_id[1]
    return params.key_of(c4d.DescID(desc_id[0], c4d.DescLevel(1000 + 100 * inverse[number] + offset, last.dtype, last.creator)))


def _numbers_of(morphs):
    """The morph numbers in `data`, or None when they are missing (an older sender) or make no sense."""
    numbers = [m.get("i") if isinstance(m, dict) else None for m in morphs]
    if not numbers or any(not isinstance(n, integer_types) or isinstance(n, bool) or n < 0 or n > 10 * MAX_MORPHS
                          for n in numbers) or len(set(numbers)) != len(numbers) or numbers[0] != 0:
        return None
    return numbers


def _read_morphs(tag, doc, point_count):
    morphs = []
    for index in range(min(tag.GetMorphCount(), MAX_MORPHS)):
        morph = tag.GetMorph(index)
        entry = {"n": compat.from_c4d_str(morph.GetName())}
        number = _number(tag, index)
        if number is not None:
            entry["i"] = number
        if index > 0 and point_count:
            offsets = _read_offsets(morph, doc, tag, point_count)
            if offsets is not None:
                entry["d"] = offsets
        morphs.append(entry)
    return morphs


def _remember(cache, uuid, signature, morphs, count, applied=True):
    if not uuid:
        return
    if len(cache) > _CACHE_LIMIT:
        cache.clear()
    cache[uuid] = (signature, morphs, applied, count)


def _animated_keys(tag):
    keys = set()
    for track in tracks.iter_tracks(tag):
        try:
            keys.add(params.key_of(track.GetDescriptionID()))
        except Exception:
            continue
    return keys


def _strengths(tag, count):
    animated = _animated_keys(tag)
    values = []
    for index in range(count):
        try:
            desc_id = tag.GetMorphID(index)
            value = None if params.key_of(desc_id) in animated else tag.GetParameter(desc_id, compat.DESCFLAGS_GET)
        except Exception:
            value = None
        values.append(float(value) if _is_number(value) else None)
    return values


def dump(tag, cache):
    """The tag's morphs and strengths (see the module docstring), or None for a tag without morphs."""
    doc = tag.GetDocument()
    count = min(tag.GetMorphCount(), MAX_MORPHS)
    if doc is None or count == 0:
        return None
    point_count = _owner_points(tag) if _get(tag, ID_POINTS) else None
    uuid = compat.read_uid(tag)
    signature = _signature(tag, point_count)
    cached = cache.get(uuid) if uuid else None
    if cached is not None and (cached[0] == signature or (_in_edit_mode(tag) and len(cached[1]) == count)):
        # Unchanged shapes; in edit mode the pose being sculpted is read once it is stored.
        morphs, sent_count = cached[1], cached[3]
    else:
        morphs, sent_count = _read_morphs(tag, doc, point_count), point_count or 0
        _remember(cache, uuid, signature, morphs, sent_count)
    return {"n": sent_count, "m": _copy(morphs), "s": _strengths(tag, len(morphs))}


def _copy(morphs):
    """A copy the shadow may patch without touching the cache."""
    result = []
    for morph in morphs:
        entry = dict(morph)
        if isinstance(entry.get("d"), dict):
            entry["d"] = dict(entry["d"])
        result.append(entry)
    return result


def without_strengths(data):
    """`data` without the strengths, to tell a rig moving sliders from an edit of the shapes."""
    if not isinstance(data, dict):
        return data
    return dict((key, value) for key, value in data.items() if key != "s")


# Loading ----------------------------------------------------------------------------------------

def _offsets(chunks, point_count):
    """{chunk index: [floats]} validated against the point count."""
    result = {}
    if not isinstance(chunks, dict):
        return result
    for key, packed in chunks.items():
        index = int(key)
        expected = min(CHUNK, point_count - index * CHUNK) * 3
        if index < 0 or expected <= 0 or not isinstance(packed, string_types):
            raise ValueError("bad morph chunk %r" % (key,))
        values = protocol.unpack_floats(packed)
        if len(values) != expected:
            raise ValueError("morph chunk %r has the wrong size" % (key,))
        result[index] = values
    return result


def _write_offsets(morph, doc, tag, point_count, new, old):
    """Write the chunks of `new` ({chunk: packed}) that differ from `old` (same form; None = unknown,
    write every chunk). A missing chunk is all zero."""
    total = (point_count + CHUNK - 1) // CHUNK
    if old is None:
        changed = [str(k) for k in range(total)]
    else:
        changed = [k for k in set(new) | set(old) if new.get(k) != old.get(k)]
    if not changed:
        return
    decoded = _offsets(new, point_count)
    if not _expand(morph, doc, tag):
        raise ValueError("could not expand morph")
    try:
        node = morph.GetFirst()
        if node is None or node.GetPointCount() != point_count:
            raise ValueError("morph does not match the mesh")
        for key in changed:
            index = int(key)
            values = decoded.get(index)
            start = index * CHUNK
            for k in range(min(CHUNK, point_count - start)):
                if values is None:
                    node.SetPoint(start + k, c4d.Vector(0, 0, 0))
                else:
                    node.SetPoint(start + k, c4d.Vector(values[k * 3], values[k * 3 + 1], values[k * 3 + 2]))
    finally:
        _collapse(morph, doc, tag)


def _rebuild(tag, doc, names, numbers=None):
    """Make the tag hold exactly len(names) morphs with these names, each starting as the base shape. With
    `numbers`, morph i gets number numbers[i]: AddMorph takes the lowest free number (measured on 2023), so every
    number up to the highest is first held by a placeholder; each morph frees its own number just before it is
    added, and the placeholders left over (numbers the sender no longer uses) are removed."""
    try:
        tag.ExitEdit(doc, True)
    except Exception:
        pass
    flags = _flags("CAMORPH_DATA_FLAGS_POINTS")
    for index in range(tag.GetMorphCount() - 1, 0, -1):
        tag.RemoveMorph(index)
    if tag.GetMorphCount() == 0:
        tag.InitMorphs()
        tag.AddMorph().Store(doc, tag, flags)
    if numbers is not None:
        for _number_ in range(1, max(numbers) + 1):
            tag.AddMorph().Store(doc, tag, flags)
    for position, name in enumerate(names[1:], 1):
        if numbers is not None:
            for index in range(1, tag.GetMorphCount()):
                if _number(tag, index) == numbers[position]:
                    tag.RemoveMorph(index)
                    break
        morph = tag.AddMorph()
        morph.Store(doc, tag, flags)
        morph.SetName(compat.native_str(name))
    if numbers is not None:
        # the placeholders left over sit right after the base, the real morphs follow them
        for _extra in range(tag.GetMorphCount() - len(names)):
            tag.RemoveMorph(1)
        local = [_number(tag, index) for index in range(tag.GetMorphCount())]
        uuid = compat.read_uid(tag)
        if local != numbers:
            LOG.warning("Pose morph numbers differ from the sender: %s here, %s sent (XPresso ports and tracks are translated)",
                        local, numbers)
            if uuid and len(local) == len(numbers):
                NUMBER_MAP[uuid] = dict(zip(numbers, local))
        elif uuid:
            NUMBER_MAP.pop(uuid, None)


def load(tag, data, cache):
    """Make the tag's morphs and strengths match `data`. Point offsets are only written where they
    differ from what this tag holds; a different morph list rebuilds the tag. Returns True when done."""
    doc = tag.GetDocument()
    if doc is None or not isinstance(data, dict) or not isinstance(data.get("m"), list):
        return False
    morphs = data["m"][:MAX_MORPHS]
    names = [m.get("n") if isinstance(m, dict) and isinstance(m.get("n"), string_types) else "" for m in morphs]
    if not names:
        return False
    point_count = _owner_points(tag) if _get(tag, ID_POINTS) else None
    sent_count = data.get("n")
    usable = bool(point_count) and sent_count == point_count and point_count <= MAX_POINTS
    if point_count and sent_count != point_count:
        LOG.warning("Pose morph points skipped: %s points sent, the mesh here has %s", sent_count, point_count)
    uuid = compat.read_uid(tag)
    current = [compat.from_c4d_str(tag.GetMorph(i).GetName()) for i in range(tag.GetMorphCount())]
    numbers = _numbers_of(morphs)
    cached = cache.get(uuid)
    if cached is not None and not cached[2]:
        cached = None  # what we remembered was never written here (the mesh did not match)
    local = [_number(tag, i) for i in range(tag.GetMorphCount())]
    if current != names or (numbers is not None and local != numbers and not _mapped(uuid, numbers, local)):
        _rebuild(tag, doc, names, numbers)
        previous = None  # a new morph stores whatever the mesh shows (maybe animated): write every chunk
    elif cached is not None and len(cached[1]) == len(names):
        previous = [m.get("d") or {} for m in cached[1]]
    else:
        previous = None  # unknown content: write every chunk
    if usable:
        offsets = []
        for index in range(len(names)):
            new = (morphs[index] if isinstance(morphs[index], dict) else {}).get("d") or {}
            _offsets(new, point_count)  # validate everything before touching the scene
            offsets.append(new)
        for index in range(1, len(names)):
            old = previous[index] if previous is not None else None
            _write_offsets(tag.GetMorph(index), doc, tag, point_count, offsets[index], old)
    tag.UpdateMorphs()
    if _get(tag, ID_MODE) != MODE_ANIMATE:
        tag.SetParameter(c4d.DescID(c4d.DescLevel(ID_MODE)), MODE_ANIMATE, compat.DESCFLAGS_SET)
    strengths = data.get("s") if isinstance(data.get("s"), list) else []
    for index, value in enumerate(strengths[:len(names)]):
        if _is_number(value):
            try:
                tag.SetParameter(tag.GetMorphID(index), float(value), compat.DESCFLAGS_SET)
            except BaseException as exc_:
                if is_fatal(exc_):
                    raise
    # Remember what was sent even when the points could not be written, so this side never reports
    # its own (unwritten) shapes as an edit; "applied" tells the next load whether they are really here.
    stored = _copy([m if isinstance(m, dict) else {"n": ""} for m in morphs])
    _remember(cache, uuid, _signature(tag, point_count), stored, sent_count if _is_number(sent_count) else 0,
              usable)
    return True
