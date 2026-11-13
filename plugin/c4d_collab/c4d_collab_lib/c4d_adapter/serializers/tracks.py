"""Animation tracks (CTrack -> CCurve -> CKey) and document time settings.

Track node (kind "track", parent = the animated object/tag):
    uuid  = md5(owner uuid | parameter key): every client derives the same id
    data  = {"d": parameter key,            # DescID of the animated parameter
             "p": {track settings},          # before/after loop, offset... (generic)
             "k": {"<seconds>": key}}        # keyed by time -> keyframe-level patches
    key   = {"v": value, "i": interpolation, "tl"/"tr": tangent times (s),
             "vl"/"vr": tangent values, "q": {key option id: value}}

Document node (kind "doc", uuid "document"):
    data  = {"fps": int, "min"/"max"/"lmin"/"lmax": seconds}

Measured in Phase 5 (ARCHITECTURE.md section 9): only value tracks (CTbase) are
synced; tangent and track-setting edits do not move any dirty counter, so the
tracker also compares track content periodically.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import hashlib

import c4d

from ...core.compat import is_fatal
from .. import compat
from . import doclayers, params

VALUE_TRACK = getattr(c4d, "CTbase", 5350)
DOC_UUID = "document"
TIME_DIGITS = 6

# Key options read/written directly by id (cheaper than walking each key's description).
# Missing ids on old versions simply read back as None and are skipped.
KEY_OPTIONS = [
    (919, "DTYPE_BOOL"),   # auto tangents
    (923, "DTYPE_BOOL"),   # clamp
    (915, "DTYPE_BOOL"),   # break tangents
    (917, "DTYPE_BOOL"),   # lock tangent angles
    (918, "DTYPE_BOOL"),   # lock tangent lengths
    (902, "DTYPE_BOOL"),   # mute
    (1244, "DTYPE_BOOL"),  # weighted tangent
    (1246, "DTYPE_BOOL"),  # automatic weighting
    (1245, "DTYPE_BOOL"),  # remove overshooting
]


def track_uuid(owner_uuid, param_key):
    return hashlib.md5(("%s|%s" % (owner_uuid, param_key)).encode("utf-8")).hexdigest()


def time_key(seconds):
    return "%.*f" % (TIME_DIGITS, seconds)


def supported(track):
    return track.GetType() == VALUE_TRACK and track.GetCurve() is not None


def iter_tracks(atom):
    track = atom.GetFirstCTrack()
    while track is not None:
        yield track
        track = track.GetNext()


VECTOR_COMPONENTS = dict((getattr(c4d, name, default), index) for index, (name, default) in
                         enumerate((("VECTOR_X", 1000), ("VECTOR_Y", 1001), ("VECTOR_Z", 1002))))


OVERRIDE_TOLERANCE = 5e-4   # a hand edit smaller than this (cm, radians) cannot be seen


def _expected_value(track, doc):
    """What the track gives at the playhead, or None."""
    time, fps = doc.GetTime(), doc.GetFps()
    try:
        return float(track.GetValue(doc, time, fps))
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
    try:
        return float(track.GetCurve().GetValue(time, fps))
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        return None


def overridden_ids(atom, seen=None, owner=None):
    """{(first-level id, vector component index or None)} of the track-driven parameters that someone changed by hand
    and did not key: the value is not what the track gives at the playhead AND it is not the value this parameter
    had at the last look (`seen`, {(owner, key): (value, frame)}, filled here). Evaluation always leaves a parameter equal to
    its track, so this is an edit, and the others should see it whatever frame they are on. A parameter that
    something else drives (a constraint holding a scale at 1) differs from its track all the time, but never moves:
    it is not reported."""
    doc = atom.GetDocument()
    if doc is None or seen is None:
        return set()
    frame = doc.GetTime().GetFrame(doc.GetFps())
    found = set()
    for track in iter_tracks(atom):
        if not supported(track):
            continue
        desc_id = track.GetDescriptionID()
        if desc_id.GetDepth() < 1:
            continue
        try:
            live = float(atom[desc_id])
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            continue
        key = (owner, params.key_of(desc_id))
        previous = seen.get(key)
        seen[key] = (live, frame)
        # a value that moved because the playhead moved is the animation, not an edit: only a change on the same frame counts
        if previous is None or previous[1] != frame or abs(live - previous[0]) <= OVERRIDE_TOLERANCE:
            continue
        expected = _expected_value(track, doc)
        if expected is not None and abs(live - expected) > OVERRIDE_TOLERANCE + 1e-6 * abs(expected):
            first = desc_id[0].id
            found.add((first, VECTOR_COMPONENTS.get(desc_id[1].id) if desc_id.GetDepth() > 1 else None))
    return found


def animated_ids(atom, seen=None, owner=None):
    """Parameters driven by a track, as {first-level id: None | {vector component index}}.

    Their values are not synced as plain parameters (the track defines them and
    playback would spam updates). If only X of a position is animated, Y and Z
    still sync normally. The exception is a value someone changed by hand and did not key
    (overridden_ids): that is an edit, it is synced like any other value."""
    result = {}
    overridden = overridden_ids(atom, seen, owner)
    for track in iter_tracks(atom):
        desc_id = track.GetDescriptionID()
        if desc_id.GetDepth() < 1:
            continue
        first = desc_id[0].id
        component = VECTOR_COMPONENTS.get(desc_id[1].id) if desc_id.GetDepth() > 1 else None
        if (first, component) in overridden:
            continue
        if component is None:
            result[first] = None
        elif first not in result or result[first] is not None:
            result.setdefault(first, set()).add(component)
    return result


def _option_ids():
    resolved = []
    for ident, dtype_name in KEY_OPTIONS:
        dtype = getattr(c4d, dtype_name, None)
        if dtype is not None:
            resolved.append((ident, dtype))
    return resolved


def _dump_key(key):
    data = {
        "v": key.GetValue(),
        "i": key.GetInterpolation(),
        "tl": key.GetTimeLeft().Get(), "vl": key.GetValueLeft(),
        "tr": key.GetTimeRight().Get(), "vr": key.GetValueRight(),
    }
    options = {}
    for ident, dtype in _option_ids():
        try:
            value = key.GetParameter(c4d.DescID(c4d.DescLevel(ident, dtype, 0)), compat.DESCFLAGS_GET)
        except Exception:
            value = None
        if value is not None:
            options[str(ident)] = int(value) if isinstance(value, bool) else value
    if options:
        data["q"] = options
    return data


def dump_track(track, uuid_of):
    curve = track.GetCurve()
    keys = {}
    for index in range(curve.GetKeyCount()):
        key = curve.GetKey(index)
        keys[time_key(key.GetTime().Get())] = _dump_key(key)
    return {
        "d": params.key_of(track.GetDescriptionID()),
        "p": params.dump(track, uuid_of),
        "k": keys,
    }


def _add_key(curve, seconds):
    added = curve.AddKey(c4d.BaseTime(seconds))
    return added["key"] if isinstance(added, dict) else added


def load_keys(track, keys):
    """Rebuild the curve from key data (simple and exact; curves are small)."""
    curve = track.GetCurve()
    curve.FlushKeys()
    for name in sorted(keys, key=float):
        data = keys[name]
        key = _add_key(curve, float(name))
        if key is None:
            continue
        key.SetValue(curve, float(data.get("v", 0.0)))
        key.SetInterpolation(curve, int(data.get("i", 1)))
        for ident, value in (data.get("q") or {}).items():
            try:
                key.SetParameter(c4d.DescID(c4d.DescLevel(int(ident))), value, compat.DESCFLAGS_SET)
            except Exception:
                pass
        key.SetTimeLeft(curve, c4d.BaseTime(float(data.get("tl", 0.0))))
        key.SetValueLeft(curve, float(data.get("vl", 0.0)))
        key.SetTimeRight(curve, c4d.BaseTime(float(data.get("tr", 0.0))))
        key.SetValueRight(curve, float(data.get("vr", 0.0)))


def find_or_create_track(owner, param_key):
    desc_id = params.desc_id_of(param_key)
    if not isinstance(owner, c4d.BaseObject):   # the sliders of a Pose Morph tag are numbered by the sender (morph.NUMBER_MAP)
        from . import morph
        uuid = compat.read_uid(owner)
        desc_id = morph.local_slider(uuid, desc_id) if uuid else desc_id
    track = owner.FindCTrack(desc_id)
    if track is None:
        track = c4d.CTrack(owner, desc_id)
        owner.InsertTrackSorted(track)
    return track


def load_track(track, data, atom_of, keys=True, settings=True):
    if settings:
        params.load(track, data.get("p") or {}, atom_of)
    if keys:
        load_keys(track, data.get("k") or {})


# Document time settings ------------------------------------------------------------

def dump_document(doc):
    data = {
        "fps": doc.GetFps(),
        "min": doc.GetMinTime().Get(), "max": doc.GetMaxTime().Get(),
        "lmin": doc.GetLoopMinTime().Get(), "lmax": doc.GetLoopMaxTime().Get(),
    }
    layers = doclayers.dump(doc)
    if layers:
        data["ly"] = layers
    return data


def load_document(doc, data):
    if "fps" in data:
        doc.SetFps(int(data["fps"]))
    for field, setter in (("min", doc.SetMinTime), ("max", doc.SetMaxTime),
                          ("lmin", doc.SetLoopMinTime), ("lmax", doc.SetLoopMaxTime)):
        if field in data:
            setter(c4d.BaseTime(float(data[field])))
    if data.get("ly"):
        doclayers.load(doc, data["ly"])
