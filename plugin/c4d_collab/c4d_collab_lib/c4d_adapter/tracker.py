"""Find local scene changes and turn them into ops (main thread only).

Two-level scan (measured in Phase 0, ARCHITECTURE.md section 9):
1. Document dirty counters unchanged -> nothing to do (almost free).
2. Otherwise walk the object tree: structure (parent/prev) is compared with
   the shadow, and only atoms whose own dirty counters moved are serialized
   and diffed.

The shadow (a core SceneState) is what this client believes everyone else
has: our ops are applied to it as soon as they are generated, remote ops by
the applier.

Locks (Phase 4): `editable(uuid)` / `subtree_editable(uuid)` come from the
session mirror. Changes to nodes another user has locked are never sent;
instead they are collected in `restore` (put the node back as the shadow
has it) and new objects under a locked parent in `discard` (remove them).
Moving only the *order* of a locked node is ignored: it also happens when an
unlocked sibling moves.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import time

import c4d

from ..core import diff as diffmod
from ..core.compat import is_fatal, string_types
from ..core.log import get_logger
from ..core.model import OpError
from . import compat
from .serializers import assets, doclayers, geometry, morph, nodes, params, rig, tracks, xpresso

LOG = get_logger("tracker")

# Content that moves no dirty counter (measured): animation tangents and track
# settings (Phase 5), selection tags and spline tangents/closed flags (Phase 6).
# It is compared on this interval, stretched to ten times the scan's own cost so
# the check never takes more than about 10% of the main thread.
TRACK_SCAN_INTERVAL = 1.0

# An atom whose serialization took longer than this is not read again until
# HOLD_FACTOR times that long has passed (huge meshes while dragging points).
SLOW_SERIALIZE = 0.03
HOLD_FACTOR = 4

# Periodic content checks (UVW tags especially) that cost more than this are dropped for
# that atom: the counters and topology changes still catch its edits, and a check that
# freezes the interface for a moment every few seconds is worse than a late one.
UNCOUNTED_MAX_COST = 0.05

# While objects are dragged, only the selected objects are looked at (a few atoms instead of the whole
# scene: measured 12 ms for 1500 objects + 120 materials vs. well under 1 ms). Everything else is
# still found by a full scan at least this often, and at once when the fast look finds nothing.
FULL_SCAN_INTERVAL = 0.5
FULL_SCAN_DUTY = 9.0     # after a full scan of cost c the next one waits at least 9 c
BIG_SCAN = 0.15          # a full scan this slow (a big rig on R19) freezes the viewport for that long: it waits 19 c, so the
BIG_SCAN_DUTY = 19.0     # playhead and the rig copies run smoothly between scans (about 5% of the main thread) ...
MAX_FULL_WAIT = 5.0      # ... but never longer than this: a new object or a material edit reaches the others within seconds
SLOW_SCAN = 0.03         # slower full scans are written to the log (once per 10 s)


def _strip_param_deletions(patch):
    """Never remove plain parameters (other C4D versions may know more of them)."""
    if not patch:
        return None
    sub = patch.get("sub", {})
    if "p" in sub:
        sub["p"].pop("del", None)
        if not sub["p"]:
            del sub["p"]
        if not sub:
            patch.pop("sub")
    return patch or None


# A rig that is evaluated writes its results with a little noise (IK and constraints settle within a hair, measured on
# a real rig: joints 0.02, nulls 0.005 cm, joint lengths 0.002). Both sides evaluate the same rig, so sending each
# other's noise back as "edits" made them fight (the host's values were overwritten, a locked object was undone over
# and over). Position (cm), rotation (rad) and scale changes below these are not edits; a joint's tolerance is larger.
NOISE = {903: 0.02, 904: 0.003, 905: 0.001}
JOINT_NOISE = {903: 0.05, 904: 0.03, 905: 0.01, 2007: 0.01}
JOINT_TYPE = 1019362   # c4d.Ojoint
IK_SPLINE_TAG = 1019862
IK_TAG = 1019561
TAG_ENABLE = "1000999:400006001:0"
# Solvers that write the joints of a chain: tag type -> (parameter of the end joint, of the strength, switches). A switch
# is (parameter, in use from, in use below): IK needs "Use IK" on and IK/FK (1 = FK: the joints are posed by hand)
# below 1; an IK-Spline tag needs nothing more than Enable and Strength. Parameters are read by their description id:
# tag[2002] looks into the container of the tag and finds nothing in a tag that was made on the guest.
SOLVERS = {IK_TAG: ("2000:133:0", "2026:19:0", (("2034:400006001:0", 0.5, None), ("2002:19:0", None, 0.999))),
           IK_SPLINE_TAG: ("2002:133:0", "2010:19:0", ())}
# What an IK solver writes into the joints of its chain: position, rotation, scale, joint length. Stretch works on the
# scale a joint has *now*: the host's stretched joints, sent as edits, were stretched once more by the guest's own
# solver, every evaluation again (measured on a real rig: the host's thigh at scale 1.5, the guest's at 2.0, 2.5, ...
# as the goal moved away; the leg mesh followed). Both sides run the solver, so its results are not edits.
SOLVED_TRANSFORM_IDS = frozenset([903, 904, 905, 2007])


def _transform_id(key):
    try:
        return int(key.split(":", 1)[0])
    except ValueError:
        return None


def _noise_of(key, tolerances):
    try:
        return tolerances.get(int(key.split(":", 1)[0]))
    except ValueError:
        return None


def _drop_noise(old, patch, ctype):
    """Remove from `patch` the changes of transform values that are only the rig settling."""
    params_patch = (patch.get("sub") or {}).get("p")
    if not params_patch:
        return patch
    known = (old or {}).get("p") or {}
    tolerances = JOINT_NOISE if ctype == JOINT_TYPE else NOISE
    for key in list((params_patch.get("set") or {}).keys()):
        tolerance = _noise_of(key, tolerances)
        before, after = known.get(key), params_patch["set"][key]
        if tolerance is not None and isinstance(before, (int, float)) and isinstance(after, (int, float)) \
                and abs(before - after) <= tolerance:
            del params_patch["set"][key]
    for key in list((params_patch.get("sub") or {}).keys()):
        tolerance = _noise_of(key, tolerances)
        changed = (params_patch["sub"][key].get("set") or {}).get("V")
        before = (known.get(key) or {}).get("V") if isinstance(known.get(key), dict) else None
        if tolerance is not None and isinstance(changed, list) and isinstance(before, list) \
                and len(changed) == len(before) and len(params_patch["sub"][key]) == 1 \
                and all(isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= tolerance
                        for a, b in zip(changed, before)):
            del params_patch["sub"][key]
    for section in ("set", "sub"):
        if section in params_patch and not params_patch[section]:
            del params_patch[section]
    if not params_patch:
        del patch["sub"]["p"]
        if not patch["sub"]:
            del patch["sub"]
    return patch


def _patch_keys(level):
    return set(level.get("set", {})) | set(level.get("sub", {})) | set(level.get("del", ()))


def _differs_where(applied, data, patch):
    """Does `data` differ from `applied` in any value that `patch` (shadow -> data) changes? Parameters are compared
    one by one, so a matrix-only reading (other parameters taken from the shadow) is judged by what it changes."""
    for key in _patch_keys(patch):
        if key == "p" and isinstance(patch.get("sub", {}).get("p"), dict):
            known, live = applied.get("p") or {}, data.get("p") or {}
            for param in _patch_keys(patch["sub"]["p"]):
                if diffmod.diff({"v": known.get(param)}, {"v": live.get(param)}):
                    return True
        elif diffmod.diff({"v": applied.get(key)}, {"v": data.get(key)}):
            return True
    return False


def _param_patch(old, new, ctype=None):
    """Diff of node data where parameters are only ever set, never removed: a peer
    on another C4D version may know parameters we do not have."""
    patch = diffmod.diff(old, new)
    if not patch:
        return None
    patch = _drop_noise(old, patch, ctype)
    if not patch:
        return None
    sub = patch.get("sub", {})
    if "p" in sub:
        sub["p"].pop("del", None)
        if not sub["p"]:
            del sub["p"]
        if not sub:
            patch.pop("sub")
    patch.pop("del", None)  # never drop whole sections either ("g" for non-polygons)
    return patch or None


# A Vibrate tag (camera shake) moves its object a little differently on every evaluation, on every computer. Changes of
# the transform of such an object below these (cm, radians, scale) are the shake, not an edit: the guest answered a join
# with its own shake, which the host then applied to its camera (measured: 0.3 cm).
SHAKE_TAGS = frozenset([5698])   # c4d.Tvibrate
SHAKE = {903: 1.5, 904: 0.02, 905: 0.02}
# A channel the tag shakes with a large amplitude moves further than these (measured: Camera.1 of Fight_test11 shakes its
# scale with amplitude 2, 0.58 on the guest against 0.762 on the host at the same frame, and the guest sent it on every
# join). The channels a Vibrate tag in use shakes are therefore never an edit while the object is not selected; a user
# who moves it selects it first, and then only the small tolerances above apply.
SHAKE_CHANNELS = ((903, "VIBRATEEXPRESSION_POS_ENABLE", 1000), (904, "VIBRATEEXPRESSION_ROT_ENABLE", 1006),
                  (905, "VIBRATEEXPRESSION_SCL_ENABLE", 1003))


def shaken_channels(atom):
    """The transform parameter ids (903 position, 904 rotation, 905 scale) a Vibrate tag in use on `atom` shakes."""
    shaken = set()
    tag = atom.GetFirstTag() if hasattr(atom, "GetFirstTag") else None
    while tag is not None:
        if tag.GetType() in SHAKE_TAGS:
            try:
                enabled = tag.GetParameter(c4d.DescID(c4d.DescLevel(1000999)), compat.DESCFLAGS_GET)   # the tag's Enable
            except BaseException as exc_:
                if is_fatal(exc_):
                    raise
                enabled = True
            if enabled is None or enabled:
                for ident, name, fallback in SHAKE_CHANNELS:
                    try:
                        if tag.GetParameter(c4d.DescID(c4d.DescLevel(getattr(c4d, name, fallback))), compat.DESCFLAGS_GET):
                            shaken.add(ident)
                    except BaseException as exc_:
                        if is_fatal(exc_):
                            raise
        tag = tag.GetNext()
    return shaken


def _mask_shake(old, new, shaken=(), selected=True):
    known = (old or {}).get("p") or {}
    if not isinstance(new.get("p"), dict):
        return new
    masked = dict(new["p"])
    for key, value in new["p"].items():
        try:
            ident = int(key.split(":", 1)[0])
        except ValueError:
            continue
        tolerance = SHAKE.get(ident)
        before = known.get(key)
        if tolerance is None or key not in known:
            continue
        if ident in shaken and not selected:
            masked[key] = before
        elif (isinstance(value, dict) and isinstance(before, dict)
                and isinstance(value.get("V"), list) and isinstance(before.get("V"), list)
                and len(value["V"]) == len(before["V"])
                and all(isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= tolerance
                        for a, b in zip(value["V"], before["V"]))):
            masked[key] = before
    return dict(new, p=masked)


def _mask_driven(old, new, modes, strengths):
    """`new` with what an XPresso graph writes taken from `old`. Both sides evaluate the same graph, so the value of
    such a parameter is the graph's result, not an edit; sent, it made host and guest overwrite each other (the guest
    answered every join with its own evaluation of the rig). modes: {parameter key: set of vector components, or None
    for all of it}; strengths: the sliders of a Pose Morph tag are driven."""
    if not (modes or strengths) or not isinstance(old, dict):
        return new
    result = dict(new)
    known = old.get("p") or {}
    if modes and isinstance(new.get("p"), dict):
        masked = dict(new["p"])
        for key, value in new["p"].items():
            if key not in modes or key not in known:
                continue
            components = modes[key]
            if components is None:
                masked[key] = known[key]
            elif isinstance(value, dict) and isinstance(known[key], dict) and "V" in value and "V" in known[key]:
                vector = list(value["V"])
                for component in components:
                    if component < len(vector) and component < len(known[key]["V"]):
                        vector[component] = known[key]["V"][component]
                masked[key] = {"V": vector}
        result["p"] = masked
    if strengths and isinstance(new.get("pm"), dict) and "s" in (old.get("pm") or {}):
        result["pm"] = dict(new["pm"], s=old["pm"]["s"])
    return result


# Tags that make parameters of their own after they were built, when the scene is first evaluated or something is
# selected (a Visual Selector tag fills 1003 and the container 1023; R19 never sends them, and a 2023 guest sent them on
# its first click). A parameter such a tag has and the sender's data had not is never an edit.
LATER_PARAMS_TAGS = frozenset([1026275])   # Visual Selector


def _without_dtype(key):
    """A parameter key without the data types of its levels ("1041670:15:110050" and "1041670:400006001:110050" are the
    icon colour of an object as 2023 reports it before and after the object is drawn): id and creator only."""
    levels = []
    for level in str(key).split("/"):
        parts = level.split(":")
        levels.append("%s:%s" % (parts[0], parts[-1]) if len(parts) == 3 else level)
    return "/".join(levels)


def _same_value(a, b):
    """Equal values, a bool and the number it stands for included (a parameter reported as a bool and as an integer)."""
    if isinstance(a, bool) or isinstance(b, bool):
        if isinstance(a, (bool, int, float)) and isinstance(b, (bool, int, float)):
            return float(a) == float(b)
    return diffmod.diff({"v": a}, {"v": b}) is None


def leaf_keys(patch, prefix="", depth=0):
    """The keys a patch sets, drops or changes below its sub-patches, as paths (for the log of a put-back)."""
    if not isinstance(patch, dict) or depth > 6:
        return []
    found = [prefix + str(key) for key in sorted(patch.get("set") or (), key=str)]
    found.extend(prefix + str(key) + "(del)" for key in patch.get("del") or ())
    for key in sorted(patch.get("sub") or (), key=str):
        found.extend(leaf_keys(patch["sub"][key], prefix + str(key) + ".", depth + 1))
    return found


class Tracker(object):
    def __init__(self, shadow, registry, editable=None, subtree_editable=None):
        self.shadow = shadow
        self.registry = registry
        self.editable = editable or (lambda uuid: True)
        self.subtree_editable = subtree_editable or (lambda uuid: True)
        self.skip = set()          # uuids we never send (e.g. placeholders for unknown types)
        self.restore = set()       # uuids to put back from the shadow (filled by scan)
        self.discard = []          # local atoms to remove (filled by scan)
        self._dirty = {}           # uuid -> dirty tuple at last serialization
        self._doc_dirty = None
        self._failed = set()
        self._track_cache = {}     # track uuid -> live content last seen/applied
        self.morph_cache = {}      # pose morph tag uuid -> last read/applied shapes (serializers/morph.py)
        self._next_track_scan = 0.0
        self._hold = {}            # uuid -> time before which it is not serialized again
        self._retopologized = set()  # objects whose point/polygon counts changed in this scan
        self._too_costly = set()     # atoms whose periodic content check is skipped (see above)
        self._next_full_scan = 0.0
        self._last_scan_cost = 0.0
        self._anim_seen = {}   # (owner uuid, track parameter key) -> its value at the last look (tracks.overridden_ids)
        self.restore_why = {}   # uuid -> why it is put back (the keys that differ), for the log
        self._applied = {}     # uuid -> its data as read back right after remote data was written (see _capture_applied)
        self._extras = {}      # uuid -> {parameter: value} this version has and the sender's data had not (see _capture_applied)
        self._materials_scanned = True
        self.unsynced = {}           # what stays local, counted by the last seeding scan
        self._next_slow_log = 0.0
        self._count_unsynced = False
        self._pending_assets = []    # asset create ops found while serializing, emitted before the op that needs them
        self._asset_bytes = 0        # what we have sent in this session (capped)
        self._driven = None          # uuid -> what an XPresso graph writes there (see _driven_modes), per scan
        self._ik_splines = None      # uuids of splines that an IK-Spline tag moves, per scan
        self._ik_chains = None       # uuids of joints an IK tag solves (see solved_joints), per scan
        self._constrained = None     # uuids of objects a Constraint tag in use moves (see constrained_objects), per scan

    # Echo suppression ------------------------------------------------------------

    def remember(self, uuid, atom):
        """Store the atom's current dirty counters (call after applying remote data), and how this Cinema 4D reads the
        atom right after it was written (see _as_applied)."""
        self._dirty[uuid] = self._dirty_of(atom)
        self._capture_applied(uuid, atom)

    def _capture_applied(self, uuid, atom):
        """What this side reads back from an atom just written from remote data. Another version stores some values
        differently (R19 host, 2023 guest: UVW, Texture and Phong tags, joint transforms): read back, they differ from
        the sender's data without anyone touching them. Selecting an object moves its counters, and that difference
        then went out as an edit, or was "undone" from the shadow when the sender had the object locked, which left
        the guest's rig off its body with flat-coloured parts.

        Also kept, for every kind of node: the parameters this version has and the sender's did not (2023 against R19:
        the icon colour of every object, material and shader, a camera and a Bitmap setting). They are not edits while
        they keep the value read here; a guest sent all of them on every join once the texture repair made the
        materials be read again (live, and replayed with the R19 host's scene)."""
        node = self.shadow.get(uuid)
        if node is None:
            self._applied.pop(uuid, None)
            self._extras.pop(uuid, None)
            return
        applied_kind = node.kind in ("object", "tag") and not (node.kind == "tag" and nodes.is_weight_tag(atom))
        if not applied_kind and node.kind not in ("material", "shader"):
            self._applied.pop(uuid, None)
            self._extras.pop(uuid, None)
            return
        try:
            data = self._serialize(atom, uuid, node.kind, False, tracks.animated_ids(atom) if applied_kind else {})
            if data is not None:
                data = self._mask(atom, uuid, node, data)
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            data = None
        known = node.data.get("p") if isinstance(node.data.get("p"), dict) else {}
        live = data.get("p") if isinstance(data, dict) and isinstance(data.get("p"), dict) else {}
        extras = dict((key, value) for key, value in live.items() if key not in known)
        if extras:
            self._extras[uuid] = extras
        else:
            self._extras.pop(uuid, None)
        if data is None or not applied_kind:
            self._applied.pop(uuid, None)
        else:
            self._applied[uuid] = data

    def _drop_unsent(self, uuid, known, patch):
        """`patch` without what is no edit whatever the counters say: a parameter this version has and the sender's data
        had not, still at the value read when the data arrived (see _capture_applied), and a vector component this side
        leaves out (None: animated here) where the shadow holds a number (live: Plane.4, Plane.1, Camera and Mert went
        out as [null, null, null] on every join)."""
        params_patch = (patch or {}).get("sub", {}).get("p")
        if not isinstance(params_patch, dict):
            return patch
        known_p = (known or {}).get("p") if isinstance((known or {}).get("p"), dict) else {}
        extras = dict((_without_dtype(key), value) for key, value in (self._extras.get(uuid) or {}).items())
        node = self.shadow.get(uuid)
        later_params_local = node is not None and node.kind == "tag" and node.ctype in LATER_PARAMS_TAGS
        sets = params_patch.get("set") or {}
        for key in list(sets):
            if key not in known_p and later_params_local:
                del sets[key]
            elif key not in known_p and _without_dtype(key) in extras                     and _same_value(extras[_without_dtype(key)], sets[key]):
                del sets[key]
            elif sets[key] is None and key in known_p:
                del sets[key]
        subs = params_patch.get("sub") or {}
        for key in list(subs):
            vector = (subs[key].get("set") or {}).get("V")
            before = known_p.get(key).get("V") if isinstance(known_p.get(key), dict) else None
            if isinstance(vector, list) and isinstance(before, list) and len(vector) == len(before)                     and len(subs[key]) == 1 and all(a is None or diffmod.diff({"v": a}, {"v": b}) is None
                                                    for a, b in zip(vector, before)):
                del subs[key]
        for section in ("set", "sub"):
            if section in params_patch and not params_patch[section]:
                del params_patch[section]
        if not params_patch:
            del patch["sub"]["p"]
            if not patch["sub"]:
                del patch["sub"]
        return patch or None

    def _as_applied(self, uuid, data, patch):
        """Is every value `patch` changes what this side read right after applying remote data (no local edit since)?"""
        applied = self._applied.get(uuid)
        return applied is not None and not _differs_where(applied, data, patch)

    def baseline(self, doc):
        self._doc_dirty = compat.doc_dirty(doc)

    def remember_track(self, uuid, track):
        """Store a track's live content (call after applying remote keys)."""
        try:
            self._track_cache[uuid] = tracks.dump_track(track, self.registry.uuid_of)
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            self._track_cache.pop(uuid, None)

    def forget(self, uuid):
        self._dirty.pop(uuid, None)
        self._applied.pop(uuid, None)
        self._extras.pop(uuid, None)
        self._track_cache.pop(uuid, None)

    @staticmethod
    def _dirty_of(atom):
        return (atom.GetDirty(compat.DIRTY_DATA), atom.GetDirty(compat.DIRTY_MATRIX))

    # Scan ------------------------------------------------------------------------

    def scan(self, doc, force=False, quick_only=False):
        """Return the list of ops describing local changes since the last scan. `quick_only`: just the selected objects
        (a few atoms), for the moments when a full scan would freeze the viewport (the timeline is playing)."""
        if quick_only and not force:
            return self._scan_selected(doc)
        doc_dirty = compat.doc_dirty(doc)
        now = time.time()
        # Without detailed counters (R19) the "animation" counter is the one counter that moves on every
        # change, so it says nothing: tracks are then compared on their interval only.
        tracks_due = force or now >= self._next_track_scan or self._doc_dirty is None or (
            compat.HAS_DETAILED_HDIRTY and doc_dirty["ANIMATION"] != self._doc_dirty["ANIMATION"])
        if not force and doc_dirty == self._doc_dirty and not tracks_due:
            return []
        previous = self._doc_dirty
        if (not force and not tracks_due and previous is not None and now < self._next_full_scan
                and self._objects_only(previous, doc_dirty)):
            quick = self._scan_selected(doc)
            if quick:
                return quick  # _doc_dirty stays: the next full scan still sees everything else
            if compat.OBJECT_ONLY_COUNTERS is None and self._last_scan_cost > BIG_SCAN:
                # R19 with a big scene: the counters cannot tell what changed (a playing rig moves them all the time), so
                # a slow full scan would follow every tick: it waits for its turn instead. A cheap scan runs at once.
                return []
        materials_due = (force or tracks_due or previous is None or compat.MATERIAL_COUNTERS is None
                         or any(previous.get(key) != doc_dirty.get(key) for key in compat.MATERIAL_COUNTERS))
        if tracks_due:
            self._next_track_scan = now + TRACK_SCAN_INTERVAL
        scan_started = time.time()
        structure_changed = self._doc_dirty is None or any(
            doc_dirty[k] != self._doc_dirty[k] for k in ("OBJECT_HIERARCHY", "TAG"))
        self._doc_dirty = doc_dirty
        self.restore = set()
        self.discard = []
        self._retopologized = set()
        xpresso.begin_scan()
        self._driven = None
        self._ik_splines = None
        self._ik_chains = None
        self._constrained = None

        if structure_changed:
            # Deleting something another user has locked must be undone BEFORE we
            # compare positions, or the neighbours' shifted order would be sent.
            blocked = self._blocked_deletions(doc)
            if blocked:
                self.restore = blocked
                self._doc_dirty = None  # rescan right after the restore
                return []

        ops = []
        seen = set()
        self._count_unsynced = force or previous is None  # only the sharing scan counts: it costs time
        if self._count_unsynced:
            self.unsynced = {}
        self._track_owners = set()  # atoms whose tracks were compared in this scan
        self._scan_tracks = tracks_due
        self._visit_document(doc, ops)
        self._materials_scanned = materials_due
        if materials_due:
            self._visit_materials(doc, seen, ops)
        stack = [(doc.GetFirstObject(), None)]
        prev_by_parent = {}
        while stack:
            obj, parent_uuid = stack.pop()
            while obj is not None:
                if nodes.object_supported(obj):
                    prev = prev_by_parent.get((parent_uuid, "object"))
                    uuid = self._visit(obj, "object", parent_uuid, prev, seen, ops)
                    following = obj.GetNext()
                    if uuid is None:
                        obj = following  # discarded: its subtree goes with it
                        continue
                    if uuid not in self.skip:
                        prev_by_parent[(parent_uuid, "object")] = uuid
                    self._visit_tracks(obj, uuid, seen, ops)
                    self._visit_tags(obj, uuid, seen, ops)
                    if obj.GetDown() is not None:
                        # Finish this level later; children first keeps parents before kids.
                        stack.append((following, parent_uuid))
                        stack.append((obj.GetDown(), uuid))
                        break
                    obj = following
                else:
                    self._note_unsynced("object", obj, with_children=True)
                    obj = obj.GetNext()  # code-carrying objects and their subtree stay local

        self._deletions(seen, ops)
        if tracks_due:
            self._next_track_scan = now + max(TRACK_SCAN_INTERVAL, (time.time() - scan_started) * 10)
        # a full scan never takes more than about a tenth of the main thread, however big the scene is
        cost = time.time() - scan_started
        self._last_scan_cost = cost
        wait = cost * (BIG_SCAN_DUTY if cost > BIG_SCAN else FULL_SCAN_DUTY)
        self._next_full_scan = time.time() + max(FULL_SCAN_INTERVAL, min(wait, MAX_FULL_WAIT))
        if cost > SLOW_SCAN and time.time() >= self._next_slow_log:
            self._next_slow_log = time.time() + 10.0
            LOG.info("Full scan took %d ms (%d nodes known)", cost * 1000, len(self.shadow))
        return ops

    @staticmethod
    def _objects_only(previous, current):
        """True when no counter moved except the ones objects being dragged move. On versions without
        detailed counters (R19) this cannot be told: the quick look is tried and, finding nothing,
        gives way to the full scan."""
        if compat.OBJECT_ONLY_COUNTERS is None:
            return True
        return all(previous.get(key) == current.get(key) for key in current
                   if key not in compat.OBJECT_ONLY_COUNTERS)

    def _scan_selected(self, doc):
        """Changes of the selected objects only (the ones the user is dragging)."""
        ops = []
        seen = set()
        self.restore = set()
        self.discard = []
        self._retopologized = set()
        self._scan_tracks = False
        for obj in doc.GetActiveObjects(compat.DESCFLAGS_GET_ACTIVE) or []:
            if not nodes.object_supported(obj):
                continue
            uuid = compat.read_uid(obj)
            node = self.shadow.get(uuid) if uuid else None
            if node is None or node.kind != "object":
                continue  # not known yet: only the full scan creates things
            self._visit(obj, "object", node.parent, node.prev, seen, ops)
        return ops

    def _blocked_deletions(self, doc):
        """uuids of locally deleted nodes that we are not allowed to delete."""
        present = set()
        stack = [doc.GetFirstObject()]
        while stack:
            obj = stack.pop()
            while obj is not None:
                if nodes.object_supported(obj):
                    present.add(compat.read_uid(obj))
                    tag = obj.GetFirstTag()
                    while tag is not None:
                        present.add(compat.read_uid(tag))
                        tag = tag.GetNext()
                    if obj.GetDown() is not None:
                        stack.append(obj.GetDown())
                obj = obj.GetNext()
        gone = set(n.uuid for n in self.shadow.nodes.values()
                   if n.kind in ("object", "tag") and n.uuid not in present)
        return set(u for u in gone if self.shadow.get(u).parent not in gone and not self.subtree_editable(u))

    # Materials and shaders -------------------------------------------------------------

    MAX_SHADER_DEPTH = 32

    def _visit_materials(self, doc, seen, ops):
        """Materials (document level) and their shader trees, before the objects: texture tags
        link to materials and shaders link to each other, so every atom gets its id first."""
        taken = set()
        self._assign_shading_uids(doc.GetFirstMaterial(), taken, True, 0)
        prev = None
        material = doc.GetFirstMaterial()
        while material is not None:
            if nodes.shader_supported(material):
                uuid = self._visit(material, "material", None, prev, seen, ops)
                if uuid is not None:
                    prev = uuid
                    self._visit_tracks(material, uuid, seen, ops)
                    self._visit_shaders(material, uuid, seen, ops, 0)
            material = material.GetNext()

    def _assign_shading_uids(self, atom, taken, siblings, depth):
        while atom is not None and depth < self.MAX_SHADER_DEPTH:
            taken.add(self.registry.ensure_uid(atom, taken))
            self._assign_shading_uids(atom.GetFirstShader(), taken, True, depth + 1)
            if not siblings:
                break
            atom = atom.GetNext()

    def _visit_shaders(self, owner, owner_uuid, seen, ops, depth):
        if depth >= self.MAX_SHADER_DEPTH:
            return
        prev = None
        shader = owner.GetFirstShader()
        while shader is not None:
            if nodes.shader_supported(shader):
                uuid = self._visit(shader, "shader", owner_uuid, prev, seen, ops)
                if uuid is not None:
                    prev = uuid
                    self._visit_tracks(shader, uuid, seen, ops)
                    self._visit_shaders(shader, uuid, seen, ops, depth + 1)
            shader = shader.GetNext()

    def _visit_tags(self, obj, obj_uuid, seen, ops):
        prev = None
        tag = obj.GetFirstTag()
        while tag is not None:
            if nodes.tag_supported(tag):
                uuid = self._visit(tag, "tag", obj_uuid, prev, seen, ops)
                if uuid is not None:
                    if uuid not in self.skip:   # a tag of ours alone is no sibling to the others
                        prev = uuid
                    self._visit_tracks(tag, uuid, seen, ops)
            else:
                self._note_unsynced("tag", tag)
            tag = tag.GetNext()

    def _note_unsynced(self, kind, atom, with_children=False):
        """Count what stays local (code objects and tags, tags without a serializer) for the report."""
        if not self._count_unsynced:
            return
        try:
            label = "%s (%s)" % (compat.from_c4d_str(atom.GetTypeName()), kind)
            count = 1
            if with_children:
                stack = [atom.GetDown()]
                while stack:
                    child = stack.pop()
                    while child is not None:
                        count += 1
                        if child.GetDown() is not None:
                            stack.append(child.GetDown())
                        child = child.GetNext()
            self.unsynced[label] = self.unsynced.get(label, 0) + count
        except BaseException as exc_:
            if is_fatal(exc_):
                raise

    def unsynced_report(self):
        """Text such as "3 x XPresso (tag), 12 x Python Generator (object)", or None."""
        if not self.unsynced:
            return None
        items = sorted(self.unsynced.items(), key=lambda item: -item[1])[:8]
        return ", ".join("%d x %s" % (count, label) for label, count in items)

    # Animation (Phase 5) -----------------------------------------------------------

    def _visit_document(self, doc, ops):
        live = tracks.dump_document(doc)
        for layer, _parent in doclayers.layers_of(doc):
            uuid = compat.read_uid(layer)
            if uuid:
                self.registry.set(uuid, layer)   # objects name their layer by its uuid
        node = self.shadow.get(tracks.DOC_UUID)
        if node is None:
            self._emit(ops, {"op": "create", "uuid": tracks.DOC_UUID, "kind": "doc", "ctype": 0,
                             "parent": None, "prev": None, "name": "", "data": live})
            return
        patch = diffmod.diff(node.data, live)
        if patch:
            self._emit(ops, {"op": "update", "uuid": tracks.DOC_UUID, "patch": patch})

    def _visit_tracks(self, atom, owner_uuid, seen, ops):
        if not self._scan_tracks or owner_uuid in self.skip:
            return
        self._track_owners.add(owner_uuid)
        prev = None
        for track in tracks.iter_tracks(atom):
            if not tracks.supported(track):
                continue
            try:
                live = tracks.dump_track(track, self.registry.uuid_of)
            except BaseException as exc_:
                if is_fatal(exc_):
                    raise
                LOG.exception("Cannot read a track of %s", owner_uuid)
                continue
            if isinstance(live.get("d"), string_types):
                live["d"] = morph.sender_key(owner_uuid, live["d"])   # R19: this side's slider number back to the sender's
            uuid = tracks.track_uuid(owner_uuid, live["d"])
            seen.add(uuid)
            self.registry.set(uuid, track)
            node = self.shadow.get(uuid)
            if node is None:
                if not self.editable(owner_uuid):
                    self.discard.append(track)  # animating something another user has locked
                    continue
                self._track_cache[uuid] = live
                self._emit(ops, {"op": "create", "uuid": uuid, "kind": "track", "ctype": tracks.VALUE_TRACK,
                                 "parent": owner_uuid, "prev": prev, "name": "", "data": live})
                self._emit_live_value(atom, owner_uuid, track, ops)
                prev = uuid
                continue
            prev = uuid
            if diffmod.diff(self._track_cache.get(uuid), live) is None:
                continue
            if not self.editable(uuid):
                self.restore.add(uuid)
                self.restore_why[uuid] = ["track"]
                continue
            self._track_cache[uuid] = live
            patch = _strip_param_deletions(diffmod.diff(node.data, live))
            if patch:
                self._emit(ops, {"op": "update", "uuid": uuid, "patch": patch})
                self._emit_live_value(atom, owner_uuid, track, ops)

    def _emit_live_value(self, atom, owner_uuid, track, ops):
        """A key was made or changed: the value as this user sees it goes out with it, so the others see the new pose
        whatever frame they are on (their own evaluation takes it back when their playhead moves)."""
        node = self.shadow.get(owner_uuid)
        desc_id = track.GetDescriptionID()
        if node is None or desc_id.GetDepth() < 1 or not isinstance(node.data.get("p"), dict):
            return
        try:
            live = params.dump(atom, self.registry.uuid_of, set([desc_id[0].id]), None)
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            return
        values = dict(node.data["p"])
        values.update(live)
        patch = diffmod.diff(node.data, dict(node.data, p=values))
        if patch:
            self._emit(ops, {"op": "update", "uuid": owner_uuid, "patch": patch})

    def _visit(self, atom, kind, parent, prev, seen, ops):
        uuid = self.registry.ensure_uid(atom, seen)
        seen.add(uuid)
        self.registry.set(uuid, atom)
        node = self.shadow.get(uuid)
        if node is None and uuid in self.skip:
            return uuid  # ours alone (a tag the Mesh deformer's Initialize added to a cage): never sent
        if node is None or node.kind != kind:
            if not self.editable(parent):
                self.discard.append(atom)  # created under something another user has locked
                return None
            self._emit(ops, self._create_op(atom, uuid, kind, parent, prev))
            return uuid
        if node.parent != parent:
            if self.subtree_editable(uuid) and self.editable(parent):
                self._emit(ops, {"op": "move", "uuid": uuid, "parent": parent, "prev": prev})
            else:
                self.restore.add(uuid)
        elif node.prev != prev and self.subtree_editable(uuid):
            self._emit(ops, {"op": "move", "uuid": uuid, "parent": parent, "prev": prev})
        if uuid in self.skip:
            return uuid
        dirty = self._dirty_of(atom)
        previous = self._dirty.get(uuid)
        if dirty == previous and kind == "tag" and parent in self._retopologized and nodes.is_uvw_tag(atom):
            # C4D rewrites UVW data when its owner's polygon count changes (extrude, cut...)
            # and moves no counter on the tag itself (measured), so look at it anyway.
            previous = None
        if dirty != previous:
            if not self.editable(uuid):
                if self._really_changed(atom, uuid, kind, parent):
                    self.restore.add(uuid)  # someone else holds the lock: put it back
                else:
                    self._dirty[uuid] = dirty  # a counter moved but nothing differs (rig evaluation)
                return uuid
            started = time.time()
            if started < self._hold.get(uuid, 0.0):
                return uuid  # still cooling down after a slow read; retried on a later scan
            self._dirty[uuid] = dirty
            matrix_only = (kind == "object" and previous is not None and dirty[0] == previous[0])
            data = self._serialize(atom, uuid, kind, matrix_only, tracks.animated_ids(atom, self._anim_seen, uuid))
            cost = time.time() - started
            if cost > SLOW_SERIALIZE:
                self._hold[uuid] = started + cost * HOLD_FACTOR
            if data is not None:
                if matrix_only:
                    merged_params = dict(node.data.get("p") or {})
                    merged_params.update(data["p"])
                    merged = dict(node.data)
                    merged["p"] = merged_params
                    data = merged
                data = self._mask(atom, uuid, node, data)
                op = {"op": "update", "uuid": uuid}
                patch = self._drop_unsent(uuid, node.data, _param_patch(node.data, data, node.ctype))
                if patch and self._as_applied(uuid, data, patch):
                    patch = None  # how this version stores what was received, not an edit (see _capture_applied)
                if patch:
                    self._applied.pop(uuid, None)  # a real edit: from now on the shadow holds this side's own reading
                    op["patch"] = patch
                    geometry_patch = patch.get("sub", {}).get("g", {})
                    if "g" in patch.get("set", {}) or "n" in geometry_patch.get("set", {}):
                        self._retopologized.add(uuid)
                name = compat.from_c4d_str(atom.GetName())
                if name != node.name:
                    op["name"] = name
                if len(op) > 2:
                    self._emit(ops, op)
        if self._scan_tracks or (kind == "tag" and parent in self._retopologized):
            # Selection tags also follow their owner's element counts at once, so a
            # topology change travels as one batch instead of trailing by a scan interval.
            self._visit_uncounted(atom, uuid, ops)
        return uuid

    def _driven_modes(self, uuid):
        """What the XPresso graphs of the session write on the node `uuid` ({} when nothing)."""
        if self._driven is None:
            self._driven = {}
            for node in self.shadow.nodes.values():
                if node.kind == "tag" and "xp" in node.data:
                    for target, modes in xpresso.driven_of(node.data["xp"]).items():
                        merged = self._driven.setdefault(target, {})
                        for key, components in modes.items():
                            if key in merged and merged[key] is not None and components is not None:
                                merged[key] = merged[key] | components
                            elif key not in merged or components is None:
                                merged[key] = components
        return self._driven.get(uuid) or {}

    def _spline_targets(self):
        """uuids of the splines an IK-Spline tag moves: their points are the result of the tag, on every computer."""
        if self._ik_splines is None:
            self._ik_splines = set()
            for node in self.shadow.nodes.values():
                if node.kind == "tag" and node.ctype == IK_SPLINE_TAG:
                    for value in (node.data.get("p") or {}).values():
                        if isinstance(value, dict) and isinstance(value.get("L"), string_types):
                            self._ik_splines.add(value["L"])
        return self._ik_splines

    def solved_joints(self, fresh=False):
        """uuids of the joints that an IK tag in use solves: the object that holds the tag down to its end joint. Read
        from the live tags (a switch an XPresso graph drives is masked in the shadow, which would keep an old value)."""
        if fresh or self._ik_chains is None:
            self._ik_chains = set()
            for node in self.shadow.nodes.values():
                if node.kind != "tag" or node.ctype not in SOLVERS:
                    continue
                tag = self.registry.get(node.uuid)
                if tag is None:
                    continue
                end_id, strength_id, switches = SOLVERS[node.ctype]
                try:
                    def read(key):
                        return tag.GetParameter(params.desc_id_of(key), compat.DESCFLAGS_GET)
                    in_use = read(TAG_ENABLE) and read(strength_id) > 0.001
                    for switch, at_least, at_most in switches:
                        value = read(switch)
                        in_use = in_use and (at_least is None or value >= at_least) and (at_most is None or value < at_most)
                    end = read(end_id)
                except BaseException as exc_:
                    if is_fatal(exc_):
                        raise
                    continue
                end_uuid = self.registry.uuid_of(end) if end is not None else None
                if not in_use or not end_uuid or node.parent is None:
                    continue
                current, chain = end_uuid, []
                while current is not None and current in self.shadow.nodes and len(chain) < 64:
                    chain.append(current)
                    if current == node.parent:
                        break
                    current = self.shadow.nodes[current].parent
                else:
                    chain = []
                self._ik_chains.update(chain)
        return self._ik_chains

    def constrained_objects(self):
        """uuids of the objects that a Constraint tag in use moves: the tag writes their transform on every
        evaluation, at the frame of whoever evaluates (measured: scrubbing the timeline alone sent the head, the elbows
        and the tools as "edits", and the other computer, on another frame, took them)."""
        if self._constrained is None:
            self._constrained = set()
            for node in self.shadow.nodes.values():
                if node.kind != "tag" or node.ctype not in rig.CONSTRAINT_TYPES or node.parent is None:
                    continue
                tag = self.registry.get(node.uuid)
                try:
                    if tag is not None and tag.GetParameter(params.desc_id_of(TAG_ENABLE), compat.DESCFLAGS_GET):
                        self._constrained.add(node.parent)
                except BaseException as exc_:
                    if is_fatal(exc_):
                        raise
        return self._constrained

    def _mask(self, atom, uuid, node, data):
        if (node.kind == "object" and isinstance(data.get("p"), dict)
                and (uuid in self.solved_joints() or uuid in self.constrained_objects())):
            known = node.data.get("p") or {}
            data = dict(data, p=dict((key, known[key] if key in known and "/" not in key
                                     and _transform_id(key) in SOLVED_TRANSFORM_IDS else value)
                                     for key, value in data["p"].items()))
        if node.kind == "tag" and node.ctype != IK_SPLINE_TAG and "hd" in node.data:
            # hidden settings are written when the tag is made; a solver also keeps counters of its own there that
            # move on every evaluation (found by the live session test: a Constraint tag sent one on each join)
            data = dict(data, hd=node.data["hd"])
        if node.kind == "tag" and "xp" in node.data and uuid in xpresso.DEGRADED:
            # a graph this Cinema 4D could not build completely (R19: ports it cannot make) is not an edit: sending it
            # would make the host rebuild its working graph from the missing connections
            data = dict(data, xp=node.data["xp"])
        if node.kind == "object" and "g" in node.data and "g" in data and uuid in self._spline_targets():
            data = dict(data, g=node.data["g"])
        modes = self._driven_modes(uuid)
        strengths = bool(modes) and node.kind == "tag" and nodes.is_pose_morph_tag(atom)
        data = _mask_driven(node.data, data, modes, strengths)
        if node.kind == "tag" and nodes.is_pose_morph_tag(atom) and isinstance(data.get("p"), dict):
            # A Pose Morph tag lists a parameter per morph that exists on this computer only (an XPresso port made on
            # it shows one the host does not have): the morphs travel in "pm", new parameters are never an edit.
            known = (node.data.get("p") or {})
            data = dict(data, p=dict((k, v) for k, v in data["p"].items() if k in known))
        if node.kind == "object" and any(tag.ctype in SHAKE_TAGS for tag in self.shadow.children(uuid, "tag")):
            selected = bool(atom.GetBit(c4d.BIT_ACTIVE)) if hasattr(atom, "GetBit") else True
            data = _mask_shake(node.data, data, shaken_channels(atom), selected)
        return data

    def _really_changed(self, atom, uuid, kind, parent):
        """Does a locked node differ from what the session has? A rig that is evaluated moves the dirty
        counters of its joints and weight tags all the time without changing them; restoring those on
        every evaluation made the host fight its own rig (seen live: "'Weight' is locked by Mert, your
        change was undone" over and over, each time reloading skin weights)."""
        if kind == "tag" and nodes.is_weight_tag(atom):
            return False  # weights are compared by the periodic check, never by counters
        node = self.shadow.get(uuid)
        if node is None:
            return True
        pose_morph = kind == "tag" and nodes.is_pose_morph_tag(atom)
        started = time.time()
        if started < self._hold.get(uuid, 0.0):
            return False
        data = self._serialize(atom, uuid, kind, False, tracks.animated_ids(atom, self._anim_seen, uuid))
        cost = time.time() - started
        if cost > SLOW_SERIALIZE:
            self._hold[uuid] = started + cost * HOLD_FACTOR
        if data is None:
            return False
        data = self._mask(atom, uuid, node, data)
        known = node.data
        if pose_morph:
            # Sliders driven by the rig (XPresso) move on every evaluation; only the shapes count.
            known = dict(known, pm=morph.without_strengths(known.get("pm")))
            data = dict(data, pm=morph.without_strengths(data.get("pm")))
        if compat.from_c4d_str(atom.GetName()) != node.name:
            self.restore_why[uuid] = ["name"]
            return True
        patch = self._drop_unsent(uuid, known, _param_patch(known, data, node.ctype))
        if patch is None:
            return False
        applied = self._applied.get(uuid)
        if applied is not None and pose_morph:
            applied = dict(applied, pm=morph.without_strengths(applied.get("pm")))
        # what this version reads back from data the holder of the lock sent is not a change (see _capture_applied)
        changed = applied is None or _differs_where(applied, data, patch)
        if changed:
            self.restore_why[uuid] = leaf_keys(patch)
        return changed

    def _visit_uncounted(self, atom, uuid, ops):
        """Selection tags, UVW tags, pose morphs and splines: their edits may move no dirty counter."""
        if uuid in self._too_costly and uuid not in self._retopologized:
            return
        started = time.time()
        extra = nodes.dump_uncounted(atom, self.registry.ensure, self.morph_cache)
        # A pose morph check only costs much when its shapes really changed (it reads the morph list
        # otherwise), so it is never dropped: its edits move no counter at all.
        pose_morph = "pm" in (extra or {})
        if time.time() - started > UNCOUNTED_MAX_COST and not pose_morph:
            self._too_costly.add(uuid)
        if not extra:
            return
        node = self.shadow.get(uuid)
        known = dict((key, node.data[key]) for key in extra if key in node.data)
        if "g" in extra and "g" in known and uuid in self._spline_targets():
            extra = dict(extra, g=known["g"])   # the points are what an IK-Spline tag made of them
        if "pm" in extra and self._driven_modes(uuid):
            extra = _mask_driven(node.data, extra, {}, True)   # sliders driven by a graph: not an edit
        if "cw" in extra and rig.same_weights(known.get("cw"), extra["cw"]):
            extra = dict(extra, cw=known["cw"])   # the same skin, give or take Cinema 4D's rounding
        tangents = (known.get("g") or {}).get("tan")
        if tangents is not None and "tan" not in (extra.get("g") or {}) and getattr(atom, "GetTangentCount", None) \
                and atom.GetTangentCount() == 0:
            extra = dict(extra, g=dict(extra["g"], tan=tangents))  # this spline type keeps none: not a deletion
        patch = diffmod.diff(known, extra)
        if patch is None:
            return
        if not self.editable(uuid):
            if pose_morph and diffmod.diff(dict(known, pm=morph.without_strengths(known.get("pm"))),
                                           dict(extra, pm=morph.without_strengths(extra["pm"]))) is None:
                return  # only sliders moved: the rig evaluating, not an edit to undo
            self.restore.add(uuid)
            self.restore_why[uuid] = leaf_keys(patch)
            return
        self._emit(ops, {"op": "update", "uuid": uuid, "patch": patch})

    def _create_op(self, atom, uuid, kind, parent, prev):
        self._dirty[uuid] = self._dirty_of(atom)
        data = self._serialize(atom, uuid, kind, False, tracks.animated_ids(atom, self._anim_seen, uuid)) or {}
        return {"op": "create", "uuid": uuid, "kind": kind, "ctype": atom.GetType(),
                "parent": parent, "prev": prev, "name": compat.from_c4d_str(atom.GetName()),
                "data": data}

    def _serialize(self, atom, uuid, kind, matrix_only, animated=None):
        try:
            if kind == "object":
                if matrix_only or atom.GetType() not in geometry.POLYGON_TYPES or morph.mesh_shows_base(atom):
                    return nodes.dump_object(atom, self.registry.uuid_of, matrix_only, animated)
                # A Pose Morph tag animates this mesh: its points move on every slider change and are
                # not the base. Not even read while the shared base still fits.
                node = self.shadow.get(uuid)
                known = node.data.get("g") if node is not None else None
                if known is not None and known.get("n") == [atom.GetPointCount(), atom.GetPolygonCount()]:
                    data = nodes.dump_object(atom, self.registry.uuid_of, False, animated, with_geometry=False)
                    data["g"] = known
                    return data
                data = nodes.dump_object(atom, self.registry.uuid_of, False, animated)
                self._base_geometry(atom, data)
                return data
            if kind in ("material", "shader"):
                node = self.shadow.get(uuid)
                data = nodes.dump_shading(atom, self.registry.uuid_of, animated, node.data if node else None)
                self._collect_assets(data)
                return data
            return nodes.dump_tag(atom, self.registry.uuid_of, animated, self.registry.ensure, self.morph_cache)
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            if uuid not in self._failed:  # log once per node, keep scanning the rest
                self._failed.add(uuid)
                LOG.exception("Cannot serialize %s %s", kind, uuid)
            return None

    @staticmethod
    def _base_geometry(atom, data):
        """The mesh's points show an animated or sculpted pose; sending them would spread a face
        expression into everyone's base mesh. With nothing fitting shared yet (new object, changed
        counts) the base is read from the tag's base pose. Base edits travel when made in edit mode on
        the base pose."""
        base = morph.base_points(atom)
        if base is not None and len(base) == data["g"]["n"][0]:
            data["g"]["pts"] = geometry.pack_points(base)

    def _deletions(self, seen, ops):
        kinds = ("object", "tag", "material", "shader") if self._materials_scanned else ("object", "tag")
        gone = [n for n in self.shadow.nodes.values() if n.uuid not in seen and (
            n.kind in kinds or (n.kind == "track" and n.parent in self._track_owners))]
        gone_ids = set(n.uuid for n in gone)
        for node in gone:
            if node.parent in gone_ids:
                continue  # removed together with its parent
            if self.subtree_editable(node.uuid):
                self._emit(ops, {"op": "delete", "uuid": node.uuid})
            else:
                self.restore.add(node.uuid)  # deleting something locked by another user: recreate
        for uuid in gone_ids:
            if uuid in self.restore or any(a in self.restore for a in self.shadow.ancestors(uuid)):
                continue
            self._dirty.pop(uuid, None)
            self._hold.pop(uuid, None)
            self._track_cache.pop(uuid, None)
            self._too_costly.discard(uuid)
            self.morph_cache.pop(uuid, None)
            self.registry.discard(uuid)

    def _collect_assets(self, data):
        """Texture files named in a material/shader that the session does not have yet."""
        for key, info in (data.get("f") or {}).items():
            sha = info["h"]
            if sha in self.shadow or any(op["uuid"] == sha for op in self._pending_assets):
                continue
            path = data["p"][key]
            packed = assets.encode(path)
            if packed is None or self._asset_bytes + len(packed) > assets.MAX_SESSION_BYTES:
                LOG.warning("Not sending texture %s (unreadable or session limit reached)", path)
                data["f"] = dict((k, v) for k, v in data["f"].items() if k != key)
                continue
            self._asset_bytes += len(packed)
            self._pending_assets.append({"op": "create", "uuid": sha, "kind": "asset", "ctype": 0, "parent": None,
                                         "prev": None, "name": info["n"], "data": {"z": packed}})
        if not data.get("f"):
            data.pop("f", None)

    def _emit(self, ops, op):
        """Record op locally so later comparisons in this scan see the new state."""
        if self._pending_assets:
            pending, self._pending_assets = self._pending_assets, []
            for asset_op in pending:
                self._emit(ops, asset_op)  # the files first: the material that uses them follows
        try:
            self.shadow.apply(op)
        except OpError as exc:
            LOG.error("Local op rejected by shadow (%s %s): %s", op.get("op"), op.get("uuid"), exc)
            return
        ops.append(op)
