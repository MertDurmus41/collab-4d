"""Scene synchronization: connects the session events with tracker and applier.

- Host: when the (empty) session snapshot arrives, the active document becomes
  the session document and its whole scene is sent as create ops (seed). The
  first person to join an empty server without Cinema 4D (run_server.py,
  Docker) seeds the same way (session.leads).
- Guest: the snapshot is built into a NEW document, so the user's open scene
  is never touched.
- Every tick (timer, EVMSG_CHANGE, viewport drag) local changes are scanned
  and sent; remote ops are applied after flushing our own pending changes.
- Locks (Phase 4): selecting an object or tag locks it, deselecting releases
  it. Objects locked by someone else are deselected again; edits to them are
  put back (restore) and changes the server refused are reconciled.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import gzip
import json
import os
import time

import c4d
from c4d import documents

from ..core.compat import is_fatal
from ..core.log import get_logger
from ..core.model import SceneState
from . import compat, ids, presence
from .applier import Applier
from .serializers import assets, morph, xpresso
from .tracker import Tracker

LOG = get_logger("sync")


GET_ACTIVE_FLAGS = compat._first_flag("GETACTIVEOBJECTFLAGS_NONE", "GETACTIVEOBJECTFLAGS_0")
FRAME_PRESENCE_INTERVAL = 0.2  # at most 5 frame updates per second while scrubbing/playing
CAMERA_INTERVAL = 0.05         # camera poses go out at up to 20 per second (the receiver eases them)
CAMERA_POSITION_STEP = 0.5     # cm: smaller moves are not worth a message
CAMERA_AXIS_STEP = 0.002
BIT_ACTIVE = getattr(c4d, "BIT_ACTIVE", 0)


def _camera_moved(old, new):
    """A new camera pose is only worth sending when it differs visibly from the last one sent."""
    for index in range(3):
        if abs(new[index] - old[index]) > CAMERA_POSITION_STEP:
            return True
    return any(abs(new[index] - old[index]) > CAMERA_AXIS_STEP for index in range(3, 12))


def patch_keys(patch, prefix="", depth=0):
    """The leaf keys a patch touches, as paths ("p.903:23:5155", "pts"): enough to tell what an op changes."""
    if not isinstance(patch, dict) or depth > 6:
        return []
    found = []
    if depth == 0 and isinstance(patch.get("set", {}), dict) and isinstance(patch.get("del", []), list):   # what the node itself sets or drops (a document's "ly"); nested sets stay unnamed
        found = [str(key) for key in sorted(patch.get("set") or (), key=str)]
        found.extend(str(key) + "(del)" for key in patch.get("del") or ())
    for key in sorted(patch.get("sub") or (), key=str):
        name = prefix + str(key)
        found.extend(patch_keys(patch["sub"][key], name + ".", depth + 1) or [name])
    return found


def same_document(a, b):
    if a is None or b is None:
        return False
    try:
        return a == b
    except Exception:  # pragma: no cover
        return a is b


def open_documents():
    """Every document that is open right now (the order Cinema 4D lists them in)."""
    found, doc, guard = [], documents.GetFirstDocument(), 0
    while doc is not None and guard < 1000:
        found.append(doc)
        doc = doc.GetNext()
        guard += 1
    return found


# Staying in another, already open project for longer than this ends the session for this user. A quick
# look at another tab does not: sync only pauses and resumes. A project that is OPENED while in a session
# (File > Open, File > New) ends it at once.
SWITCH_GRACE = 10.0
JOIN_DETAIL_SECONDS = 60.0  # local edits this soon after joining are logged in full (see _log_join_detail)


class SceneSync(object):
    def __init__(self, session):
        self.session = session
        self.doc = None
        self.paused_reason = None
        self._paused_at = 0.0
        self._known_docs = []     # the documents that were open when the session started
        self._reset_state()
        session.add_listener(self.on_event)

    # Session events (main thread) -------------------------------------------------------

    def on_event(self, session, event):
        kind = event[0]
        if kind == "snapshot":
            if session.leads:
                self._seed(documents.GetActiveDocument())
            else:
                self._build_guest_document(event[1])
        elif kind == "presence":
            self._align_time(event[2])
        elif kind == "remote_ops":
            self.apply_remote(event[2])
        elif kind == "rejected":
            self._on_rejected(event[1])
        elif kind == "lock_denied":
            self._on_lock_denied(event[1])
        elif kind == "locks":
            self._on_locks(event[1])
        elif kind == "closed":
            self.detach()

    def _reset_state(self):
        xpresso.clear_session()   # rig copies, graph marks and morph numbers belong to one session
        morph.NUMBER_MAP.clear()
        self.shadow = SceneState()
        self.registry = ids.Registry()
        self.tracker = Tracker(self.shadow, self.registry,
                               editable=self._editable, subtree_editable=self._subtree_editable)
        self.applier = Applier(self.shadow, self.registry, self.tracker)
        self._selected = set()
        self._warned = set()
        self._frame = None
        self._frame_sent_at = 0.0
        self._time_aligned = False
        self._camera = None
        self._camera_sent_at = 0.0
        self._overlay_object = None
        self._playhead, self._playhead_moved_at = None, 0.0
        self._textures_checked = self._remote_logged = 0.0
        self._joined_at = 0.0

    # Lock queries (the mirror knows who holds what) ---------------------------------------

    def _editable(self, uuid):
        mirror = self.session.mirror
        return mirror is None or mirror.editable(uuid)

    def _subtree_editable(self, uuid):
        mirror = self.session.mirror
        return mirror is None or mirror.subtree_editable(uuid)

    def _owner_name(self, uuid):
        mirror = self.session.mirror
        owner = mirror.owner(uuid) if mirror else None
        return mirror.user_name(owner) if owner else None

    def _label(self, uuid):
        node = self.shadow.get(uuid)
        return node.name if node is not None else uuid

    def _seed(self, doc):
        self._reset_state()
        self.doc = doc
        self._known_docs = open_documents()
        self._assign_all_uids(doc)
        ops = self.tracker.scan(doc, force=True)
        self.session.send_ops(ops)
        self._overlay_object = presence.ensure(doc)
        self.session.set_presence(host=True)  # guests start on the frame the host is on
        self.session.note("Shared your scene (%d items)" % len(ops))
        report = self.tracker.unsynced_report()
        if report:
            LOG.info("Not shared (stays local on this computer): %s", report)
            self.session.note("Not shared (stays local): %s" % report)

    def _build_guest_document(self, ops):
        self._reset_state()
        doc = documents.BaseDocument()
        doc.SetDocumentName(compat.native_str("Collab 4D - %s" % (self.session.address or "session")))
        assets.prepare_project(doc)   # a texture name Cinema 4D shortens is found in its folder (see assets.project_dir)
        documents.InsertBaseDocument(doc)
        documents.SetActiveDocument(doc)
        self.doc = doc
        self._known_docs = open_documents()
        self._keep_snapshot(ops)
        self.applier.apply(doc, ops)
        self._joined_at = time.time()
        self.tracker.scan(doc, force=True)  # fills dirty caches; should produce no ops
        self._overlay_object = presence.ensure(doc)
        c4d.EventAdd()

    def _assign_all_uids(self, doc):
        """Give every atom an id first, so links between objects resolve in the first scan."""
        taken = set()
        stack = [doc.GetFirstObject()]
        while stack:
            obj = stack.pop()
            while obj is not None:
                taken.add(self.registry.ensure_uid(obj, taken))
                tag = obj.GetFirstTag()
                while tag is not None:
                    taken.add(self.registry.ensure_uid(tag, taken))
                    tag = tag.GetNext()
                if obj.GetDown() is not None:
                    stack.append(obj.GetDown())
                obj = obj.GetNext()

    def advance(self, now):
        """Step the glides of other users' transforms. True while any is still running."""
        if not self.attached:
            return False
        return self.applier.tweens.step(now)

    def detach(self):
        self._overlay_object = None
        presence.remove(self.doc)
        presence.reset_smoothing()
        self.applier.tweens.clear()
        self.doc = None
        self.paused_reason = None
        self._known_docs = []
        self._reset_state()

    # Ticks ----------------------------------------------------------------------------

    @property
    def attached(self):
        return self.doc is not None and ids.is_alive(self.doc)

    def _was_open(self, doc):
        return any(same_document(doc, known) for known in self._known_docs)

    def _leave(self, why):
        """Leave the session because of what the user did with their documents. For the host this ends the
        session for everyone: the server runs in this Cinema 4D."""
        hosting = self.session.is_host
        self.session.note("%s: %s" % ("The session ended" if hosting else "You left the session", why))
        self.session.leave("host ended the session (%s)" % why if hosting else why)

    def _usable(self):
        if self.doc is not None and not ids.is_alive(self.doc):
            self._leave("the session document was closed")
            return False
        if not self.attached or not self.session.mirror or not self.session.mirror.synced:
            return False
        active = documents.GetActiveDocument()
        if not same_document(active, self.doc):
            if active is not None and not self._was_open(active):
                self._leave("you opened another project")   # a document that did not exist when we started
                return False
            now = time.time()
            if self.paused_reason is None:
                self._paused_at = now
                self.paused_reason = ("Sync paused: switch back to the session document within %d s or you leave "
                                      "the session" % SWITCH_GRACE)
                self.session.note(self.paused_reason)
            elif now - self._paused_at > SWITCH_GRACE:
                self._leave("you stayed in another project")
            return False
        if self.paused_reason is not None:
            self.paused_reason = None
            self.session.note("Sync resumed")
        return True

    def tick(self):
        """Send local changes and keep locks in line with the selection.
        Returns the number of ops sent."""
        if not self._usable():
            return 0
        animating = self._animating()
        if animating:
            # the timeline is playing or being dragged: a full scan of a big rig would freeze the viewport, but what the
            # user moves with the playhead running (a control in a looped preview) still goes out
            ops = self.tracker.scan(self.doc, quick_only=True)
        else:
            ops = self.tracker.scan(self.doc)
            if not ops and self.tracker.restore:
                # A forbidden delete was found before anything was compared: put it
                # back first, then look for the user's other (allowed) changes.
                self._undo_forbidden_changes()
                ops = self.tracker.scan(self.doc)
        if ops:
            self._log_ops("Local edits sent", ops)
            if time.time() - self._joined_at < JOIN_DETAIL_SECONDS:
                self._log_join_detail(ops)
            self.session.send_ops(ops)
        self._undo_forbidden_changes()
        if not animating:
            self._repair_textures()   # after a scan: a layer or file the user just changed must be seen before it is repaired
        self._sync_selection()
        self._sync_frame()
        self._sync_camera()
        self._keep_overlay_last()
        return len(ops)

    PLAYHEAD_QUIET = 0.6   # seconds without a playhead move before scanning again

    def _animating(self):
        """Is the timeline playing, or was the playhead moved a moment ago? Then the scene changes only by evaluation (a
        scan of a big rig on R19 takes half a second, a hitch every few seconds of playback) and the scan waits."""
        now = time.time()
        try:
            frame = self.doc.GetTime().GetFrame(self.doc.GetFps())
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            return False
        if self._playhead is not None and frame != self._playhead:
            self._playhead_moved_at = now
        self._playhead = frame
        return compat.is_playing() or now - self._playhead_moved_at < self.PLAYHEAD_QUIET

    def _repair_textures(self):
        """Every couple of seconds: texture files this computer cannot find point at their cached copies (applier.py)."""
        now = time.time()
        if now - self._textures_checked < 2.0 or not (self.applier.textured or self.applier.layered):
            return
        self._textures_checked = now
        for uuid in self.applier.repair_textures():
            atom = self.registry.get(uuid)
            if atom is not None:
                self.tracker.remember(uuid, atom)   # what we wrote must not look like a local edit

    def _keep_overlay_last(self):
        """The overlay object must stay the last object so it is drawn after the scene (see presence.py)."""
        holder = self._overlay_object
        if holder is not None and ids.is_alive(holder):
            presence.move_last(self.doc, holder)

    def _sync_camera(self):
        """Share where we are looking from, so the others can see our camera in their viewport."""
        now = time.time()
        if now - self._camera_sent_at < CAMERA_INTERVAL:
            return
        camera = presence.viewer_camera(self.doc)
        if camera is None:
            return
        values = presence.camera_values(camera)
        if self._camera is None or _camera_moved(self._camera, values):
            self._camera = values
            self._camera_sent_at = now
            self.session.set_presence(cam=values)

    def _align_time(self, data):
        """The guest's new document starts on frame 0 while the host's rig is posed by its animation at the host's
        frame; a rig evaluated on another frame is a different pose (measured on a real rig: 496 of 744 objects
        off, up to 15 m). Once, at the start, the guest moves to the host's frame; after that everyone keeps their
        own playhead."""
        if self._time_aligned or self.session.leads or self.doc is None or not isinstance(data, dict):
            return
        frame = data.get("frame")
        if not data.get("host") or not isinstance(frame, (int, float)):
            return
        self._time_aligned = True
        doc = self.doc
        if doc.GetTime().GetFrame(doc.GetFps()) != frame:
            doc.SetTime(c4d.BaseTime(frame, doc.GetFps()))
            self._frame = None  # tell the others about our new frame
            c4d.EventAdd()

    def _sync_frame(self):
        """Tell the others which frame we are on (each user keeps their own playhead)."""
        frame = self.doc.GetTime().GetFrame(self.doc.GetFps())
        now = time.time()
        if frame != self._frame and now - self._frame_sent_at >= FRAME_PRESENCE_INTERVAL:
            self._frame = frame
            self._frame_sent_at = now
            self.session.set_presence(frame=frame)

    def _undo_forbidden_changes(self):
        restore = self.tracker.restore
        discard = self.tracker.discard
        self.tracker.restore, self.tracker.discard = set(), []
        if restore:
            self._log_restore(restore)
            self.applier.restore(self.doc, restore)
            for uuid in sorted(restore):
                self._warn("'%s' is locked by %s, your change was undone"
                           % (self._label(uuid), self._owner_name(uuid) or "another user"))
            c4d.EventAdd()
        if discard:
            for atom in discard:
                atom.Remove()
            self.session.note("Removed %d new object(s): their parent is locked by another user" % len(discard))
            self.tracker.baseline(self.doc)
            c4d.EventAdd()

    def _log_restore(self, restore):
        """Which locked objects are put back and what differed (an object another user holds that this side's evaluation
        changed is rewritten from the shadow: the body of a rig can leave with it)."""
        why = self.tracker.restore_why
        lines = []
        for uuid in sorted(restore, key=self._label)[:10]:
            lines.append("%s [%s]" % (self._label(uuid), ",".join((why.get(uuid) or ["?"])[:6])))
        LOG.info("Put back (locked by another user): %d (%s%s)", len(restore), "; ".join(lines), "; ..." if len(restore) > 10 else "")
        for uuid in restore:
            why.pop(uuid, None)

    def _warn(self, text):
        """Status bar only: these repeat while someone drags, the Log panel is for who joined and left."""
        c4d.StatusSetText(compat.native_str(text))

    # Selection -> locks --------------------------------------------------------------

    def _selected_atoms(self):
        atoms = list(self.doc.GetActiveObjects(GET_ACTIVE_FLAGS) or [])
        atoms.extend(self.doc.GetActiveTags() or [])
        return atoms

    def _sync_selection(self):
        client = self.session.client
        if client is None:
            return
        current = set()
        for atom in self._selected_atoms():
            uuid = compat.read_uid(atom)
            if uuid is None or uuid not in self.shadow:
                continue
            if not self._editable(uuid):
                atom.DelBit(BIT_ACTIVE)  # someone else is working on it
                if uuid not in self._warned:
                    self._warned.add(uuid)
                    self._warn("'%s' is being edited by %s" % (self._label(uuid), self._owner_name(uuid)))
                c4d.EventAdd()
                continue
            current.add(uuid)
        if current == self._selected:
            return
        mirror = self.session.mirror
        to_lock = sorted(u for u in current - self._selected if mirror is None or mirror.locks.get(u) is None)
        to_release = sorted(self._selected - current)
        if to_release:
            client.release(to_release)
        if to_lock:
            client.lock(to_lock)
        self._selected = current
        self._warned &= current
        self.session.set_presence(selection=sorted(current))

    # Server answers ------------------------------------------------------------------

    def _on_lock_denied(self, uuids):
        for uuid in uuids:
            atom = self.registry.get(uuid)
            if atom is not None:
                atom.DelBit(BIT_ACTIVE)
            self._selected.discard(uuid)
            self._warn("'%s' is being edited by %s" % (self._label(uuid), self._owner_name(uuid) or "another user"))
        c4d.EventAdd()

    def _on_locks(self, changes):
        """Someone else got a lock on something we had selected (race): let go of it."""
        mirror = self.session.mirror
        for uuid, owner in changes.items():
            if owner is not None and mirror is not None and owner != mirror.user_id and uuid in self._selected:
                self._on_lock_denied([uuid])

    def _on_rejected(self, rejected):
        if not self.attached or self.session.mirror is None:
            return
        uuids = []
        for op, reason in rejected:
            uuid = op.get("uuid")
            if uuid and uuid not in uuids:
                uuids.append(uuid)
        for uuid in uuids:
            self.applier.reconcile(self.doc, self.session.mirror.scene, uuid)
        if uuids:
            self._warn("%d change(s) refused by the server were undone" % len(uuids))
            c4d.EventAdd()

    def _log_remote(self, ops):
        """What the other side sends, named, at most one line a second (an edit that moves a rig on this side must be
        traceable to the object it came for)."""
        now = time.time()
        if len(ops) < 1 or now - self._remote_logged < 1.0:
            return
        self._remote_logged = now
        self._log_ops("Remote edits", ops)

    def _keep_snapshot(self, ops):
        """The scene as received, in <prefs>/c4d_collab_last_snapshot.json.gz: a guest on another version answers a join
        with edits nobody made (live, R19 host, 2023 guest: Plane.4, Plane.1, Camera, Cobblestone, skin... on every join,
        not with a 2023 host), and the received data is what explains it when it is replayed on another computer."""
        try:
            path = os.path.join(compat.prefs_path(), "c4d_collab_last_snapshot.json.gz")
            with gzip.open(path, "wb") as handle:
                handle.write(json.dumps(ops).encode("utf-8"))
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            LOG.warning("Could not keep the received scene for diagnosis")

    def _log_join_detail(self, ops):
        """Right after joining nobody has edited anything yet: whatever goes out then is a value this version reads
        differently, logged in full so it can be traced."""
        for op in ops[:40]:
            node = self.shadow.get(op.get("uuid"))
            LOG.info("Sent after joining: %s %s %s", op.get("op"), node.name if node is not None else op.get("uuid"),
                     json.dumps(op.get("patch"), sort_keys=True)[:1500])

    def _log_ops(self, title, ops):
        """One line naming each op's node and the parameters it touches (an edit that moves a rig or takes a texture
        away must be traceable to what was sent and for which object)."""
        names = []
        for op in ops:
            node = self.shadow.get(op.get("uuid"))
            label = "%s %s" % (op.get("op"), node.name if node is not None else op.get("name", "?"))
            keys = patch_keys(op.get("patch"))
            if keys:
                label += " [%s%s]" % (",".join(keys[:6]), ",..." if len(keys) > 6 else "")
            if label not in names:
                names.append(label)
        LOG.info("%s: %d ops (%s%s)", title, len(ops), "; ".join(names[:12]), "; ..." if len(names) > 12 else "")

    def apply_remote(self, ops):
        if not self.attached:
            return
        if same_document(documents.GetActiveDocument(), self.doc):
            self.tick()  # flush our own changes first so the baseline does not hide them
        self._log_remote(ops)
        self.applier.apply(self.doc, ops)
