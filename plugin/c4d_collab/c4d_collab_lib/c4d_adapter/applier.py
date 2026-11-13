"""Apply confirmed remote ops to the session document (main thread only).

Each op is first applied to the shadow (which validates it); only then is the
Cinema 4D scene touched. No undo steps are recorded for remote changes.

Phase 4 adds two ways to undo local changes that are not allowed:
- restore(uuids): put atoms back as the shadow has them (edit, move or delete
  of something another user has locked; nothing was sent).
- reconcile(truth, uuid): the server rejected one of our ops; make shadow and
  scene match the server-confirmed state (the mirror) again.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import copy

import c4d

from ..core import diff as diffmod
from ..core.compat import is_fatal
from ..core.log import get_logger
from ..core.model import OpError
import time

from . import compat
from .serializers import assets, deformers, doclayers, hiddendata, morph, nodes, params, rig, tracks, xpresso
from .tween import TransformTweens, glidable_keys

LOG = get_logger("applier")


class Applier(object):
    def __init__(self, shadow, registry, tracker):
        self.shadow = shadow
        self.registry = registry
        self.tracker = tracker
        self.tweens = TransformTweens(tracker)  # transforms of other users glide instead of stepping
        self.textured = set()  # materials and shaders with texture files (see repair_textures)
        self.layered = set()   # materials that carry their reflectance layers (see repair_textures)

    def _doc_create_as_update(self, op):
        """The guest scanned its own (empty) document before the scene arrived, so the host's "create document" met
        a node that exists and was skipped: the layers never came, and every rig joint stayed visible. Make it an update."""
        if op.get("op") == "create" and op.get("kind") == "doc" and op.get("uuid") in self.shadow:
            node = self.shadow.get(op["uuid"])
            patch = diffmod.diff(node.data, op.get("data") or {})
            return {"op": "update", "uuid": op["uuid"], "patch": patch} if patch else {"op": "update", "uuid": op["uuid"]}
        return op

    def apply(self, doc, ops):
        """Apply ops in order. Returns the number applied to the scene."""
        applied = 0
        touched = set()
        created_shaders = set()
        textured = set()
        weight_tags = set()
        graph_tags = set()
        new_objects = set()
        linking = set()
        binding = set()
        constraints = set()
        for op in ops:
            try:
                op = self._doc_create_as_update(op)
                self.shadow.apply(op)
            except OpError as exc:
                LOG.error("Skipping remote %s %s: %s", op.get("op"), op.get("uuid"), exc)
                continue
            try:
                getattr(self, "_" + op["op"])(doc, op)
                applied += 1
            except BaseException as exc_:
                if is_fatal(exc_):
                    raise
                LOG.exception("Failed to apply remote %s %s", op.get("op"), op.get("uuid"))
            touched.add(op["uuid"])
            node = self.shadow.get(op["uuid"])
            if node is not None and node.parent:
                touched.add(node.parent)  # inserting a tag/child also bumps the parent's counters
            if node is not None and node.kind in ("material", "shader") and op["op"] in ("create", "update") \
                    and node.data.get("f"):
                textured.add(op["uuid"])
            if node is not None and node.kind == "material" and op["op"] in ("create", "update") and "rl" in node.data:
                self.layered.add(op["uuid"])
            if node is not None and node.kind == "shader" and op["op"] == "create":
                created_shaders.add(op["uuid"])
            if node is not None and node.kind == "tag" and "cw" in node.data:
                weight_tags.add(op["uuid"])
            if node is not None and node.kind == "tag" and "xp" in node.data and op["op"] == "create":
                graph_tags.add(op["uuid"])
            if node is not None and node.kind == "object" and op["op"] == "create":
                new_objects.add(op["uuid"])
            if node is not None and node.kind == "tag" and op["op"] == "create" and node.ctype in rig.CONSTRAINT_TYPES:
                constraints.add(op["uuid"])
            if node is not None and node.kind in ("object", "tag") and op["op"] in ("create", "update"):
                linking.add(op["uuid"])
                if node.kind == "object" and deformers.is_mesh_deformer(self.registry.get(op["uuid"])) \
                        and (op["op"] == "create"
                             or deformers.touches_cages(nodes.data_change(op.get("patch") or {})["params"])):
                    binding.add(op["uuid"])
        if created_shaders:
            touched |= self._relink(created_shaders)
        if linking:
            touched |= self._relink_forward(linking)
        if binding:
            touched |= self._bind_deformers(binding)
        if new_objects:
            touched |= self._restore_transforms(new_objects)
            touched |= self._reset_solved_scale(new_objects)
        self.textured |= textured
        touched |= self.repair_textures()
        if graph_tags:
            touched |= self._build_graphs(graph_tags)
        if constraints:
            touched |= self._settle_constraints(doc, constraints)
        if weight_tags:
            touched |= self._reload_weights(weight_tags)
        # Echo suppression: whatever we just changed must not look like a local edit.
        for uuid in touched:
            atom = self.registry.get(uuid)
            if atom is not None:
                self.tracker.remember(uuid, atom)
        self.tracker.baseline(doc)
        return applied

    # Placement ------------------------------------------------------------------------

    def _place(self, doc, atom, node):
        parent = self.registry.get(node.parent) if node.parent else None
        prev = self.registry.get(node.prev) if node.prev else None
        if node.kind == "track":
            if parent is None:
                raise ValueError("track without owner")
            parent.InsertTrackSorted(atom)  # C4D keeps tracks in its own order
            return
        if node.kind == "tag":
            if parent is None:
                raise ValueError("tag without object")
            parent.InsertTag(atom, prev)
        elif node.kind == "material":
            doc.InsertMaterial(atom, prev)
        elif node.kind == "shader":
            if parent is None:
                raise ValueError("shader without material")
            parent.InsertShader(atom, prev)
        else:
            doc.InsertObject(atom, parent, prev)

    # Ops -------------------------------------------------------------------------------

    def _create(self, doc, op):
        self._materialize(doc, self.shadow.get(op["uuid"]))

    def _materialize(self, doc, node):
        """Build, fill and insert the atom for a shadow node. Returns it (or None)."""
        if node.kind == "doc":
            self._load_document(doc, node.data)
            return None
        if node.kind == "track":
            return self._materialize_track(node)
        if node.kind == "asset":
            assets.store(node.uuid, node.name, node.data.get("z"))
            return None
        atom = self._build(node)
        if atom is None:
            return None
        compat.write_uid(atom, node.uuid)
        atom.SetName(compat.native_str(node.name))
        if node.kind == "tag":
            self._place(doc, atom, node)  # selection and UVW tags need their owner to load
        if node.uuid not in self.tracker.skip:
            # an XPresso graph links to objects that may not exist yet: apply() builds it once the batch is in
            self._load(atom, node, dict(nodes.FULL_CHANGE, xp=False))
            if node.kind in ("material", "shader"):
                assets.apply_files(atom, node.data)
        if node.kind != "tag":
            self._place(doc, atom, node)
            if compat.from_c4d_str(atom.GetName()) != node.name:
                # 2023 renames a material that is inserted next to one of the same name ("skin" -> "skin.1"; the R19
                # host has a "skin" for each character): the name went back to the host as the guest's edit
                atom.SetName(compat.native_str(node.name))
        self.registry.set(node.uuid, atom)
        self.tracker.remember(node.uuid, atom)
        return atom

    def _materialize_track(self, node):
        owner = self.registry.get(node.parent)
        if owner is None or node.parent in self.tracker.skip:
            return None
        track = tracks.find_or_create_track(owner, node.data["d"])
        tracks.load_track(track, node.data, self.registry.get)
        self.registry.set(node.uuid, track)
        self.tracker.remember(node.uuid, track)
        self.tracker.remember_track(node.uuid, track)
        return track

    def _build(self, node):
        if node.kind in ("material", "shader"):
            return self._build_shading(node)
        if node.kind == "tag":
            if not nodes.creatable_tag_type(node.ctype):
                LOG.warning("Not creating tag %s of type %s (unsupported or carries code)", node.uuid, node.ctype)
                self.tracker.skip.add(node.uuid)
                return None
            tag = compat.allocate(c4d.BaseTag, node.ctype)
            if tag is not None and not nodes.is_xpresso_tag(tag) and nodes.carries_code(tag, nodes.CODE_TAGS):
                LOG.warning("Refusing tag %s: its type carries code", node.uuid)
                tag = None
            if tag is None:
                LOG.warning("Tag type %s not available here, skipping %s", node.ctype, node.uuid)
                self.tracker.skip.add(node.uuid)
            return tag
        atom = None
        if nodes.creatable_object_type(node.ctype):
            atom = compat.allocate(c4d.BaseObject, node.ctype)
            if atom is not None and nodes.carries_code(atom, nodes.CODE_OBJECTS):
                atom = None  # an id we did not know, but the type is a code carrier
        if atom is None:
            # Unknown here (newer C4D, missing plugin) or code-carrying: keep the
            # hierarchy with a placeholder that we never send back.
            LOG.warning("Placeholder for %s (type %s not available here)", node.uuid, node.ctype)
            self.tracker.skip.add(node.uuid)
            atom = c4d.BaseObject(c4d.Onull)
        return atom

    def _build_shading(self, node):
        factory = c4d.BaseMaterial if node.kind == "material" else c4d.BaseShader
        atom = compat.allocate(factory, node.ctype)
        if atom is not None and nodes.carries_code(atom, nodes.CODE_SHADERS):
            LOG.warning("Refusing %s %s: its type carries code", node.kind, node.uuid)
            atom = None
        if atom is None:
            LOG.warning("%s type %s not available here, skipping %s", node.kind, node.ctype, node.uuid)
            self.tracker.skip.add(node.uuid)
            if node.kind == "material":
                atom = c4d.BaseMaterial(c4d.Mmaterial)  # keeps links from texture tags valid
        return atom

    def _relink(self, uuids):
        """Shader links (a material's colour channel -> its bitmap shader) can only be set once the
        shader exists, which it does not when the material itself is created. Re-apply the link
        parameters of every material that got new shaders. Returns the uuids it touched."""
        roots = set()
        for uuid in uuids:
            node = self.shadow.get(uuid)
            while node is not None and node.kind == "shader":
                node = self.shadow.get(node.parent)
            if node is not None and node.kind == "material":
                roots.add(node.uuid)
        touched = set()
        for root in roots:
            for uuid in [root] + list(self.shadow.descendants(root)):
                node = self.shadow.get(uuid)
                atom = self.registry.get(uuid)
                if node is None or atom is None or node.kind not in ("material", "shader") \
                        or uuid in self.tracker.skip:
                    continue
                values = node.data.get("p") or {}
                links = [key for key, value in values.items() if isinstance(value, dict) and "L" in value]
                if links:
                    try:
                        params.load(atom, values, self.registry.get, links)
                    except BaseException as exc_:
                        if is_fatal(exc_):
                            raise
                        LOG.exception("Could not link %s", uuid)
                    touched.add(uuid)
        return touched

    def _relink_forward(self, uuids):
        """Objects and tags are built level by level, so a link to something deeper (an IK tag on the
        thigh that names the end joint) found no target when it was loaded. Now everything of this batch
        exists: write those links again. Returns the uuids it touched."""
        touched = set()
        for uuid in uuids:
            node = self.shadow.get(uuid)
            atom = self.registry.get(uuid)
            if node is None or atom is None or uuid in self.tracker.skip:
                continue
            values = node.data.get("p") or {}
            if not any(isinstance(v, dict) and (v.get("L") or v.get("I")) for v in values.values()):
                continue
            try:
                if params.relink(atom, values, self.registry.get):
                    touched.add(uuid)
            except BaseException as exc_:
                if is_fatal(exc_):
                    raise
                LOG.exception("Could not link %s", uuid)
        return touched

    def _settle_constraints(self, doc, uuids):
        """A new Constraint tag works its offsets out from its targets the first time it is evaluated, which is
        not what the host had (measured on a real rig: up to 13 m away, and the guest then sent them back as its
        own edit). Let it evaluate once, then write the host's values again. Returns the uuids it touched."""
        doc.ExecutePasses(None, True, True, True, compat.BUILD_FLAGS)
        touched = set()
        for uuid in uuids:
            node = self.shadow.get(uuid)
            atom = self.registry.get(uuid)
            if node is None or atom is None or uuid in self.tracker.skip:
                continue
            values = node.data.get("p") or {}
            try:
                if "hd" in node.data:
                    hiddendata.load(atom, node.data["hd"])   # the evaluation above may have changed them
                params.load(atom, values, self.registry.get, [
                    key for key, value in values.items()
                    if not (isinstance(value, dict) and (value.get("L") or value.get("I")))])
            except BaseException as exc_:
                if is_fatal(exc_):
                    raise
                LOG.exception("Could not settle constraint %s", uuid)
            touched.add(uuid)
        return touched

    def _bind_deformers(self, uuids):
        """Mesh deformers compute their bind data from their cages: once the cages are in place (after
        _relink_forward), initialise them. Returns the uuids it touched."""
        touched = set()
        for uuid in uuids:
            node = self.shadow.get(uuid)
            atom = self.registry.get(uuid)
            if node is None or atom is None or uuid in self.tracker.skip \
                    or not deformers.has_cages(node.data.get("p")):
                continue
            cages = [self.registry.get(cage) for cage in deformers.cage_uuids(node.data.get("p"))]
            before = [tag for cage in cages if cage is not None for tag in _tags(cage)]
            if deformers.initialize(atom):
                touched.add(uuid)
            # Initialising adds its own tags to the cages (Display, Compositing: the host's cages do not have
            # them). The Display tag switches the cage to its own shading mode: on the guest the cages were drawn as
            # flat-coloured blocks over the textured body while the rig moved (live: R19 host, 2023 guest; R19's
            # Initialize does not add them, so a 2023 host never showed it). They go, so the cages look as on the host;
            # any other tag it adds belongs to this computer and is never sent.
            for cage in cages:
                for tag in list(_tags(cage)) if cage is not None else ():   # a list: tags are removed on the way
                    if any(tag == known for known in before):
                        continue
                    if tag.GetType() in deformers.INITIALIZE_TAGS:
                        tag.Remove()
                        continue
                    self.tracker.skip.add(self.registry.ensure(tag))
                    touched.add(self.registry.uuid_of(tag))
        return touched

    def _load_document(self, doc, data):
        """Frame range, frame rate and layers; the layers are registered so objects can link to them."""
        tracks.load_document(doc, data)
        for layer, _parent in doclayers.layers_of(doc):
            uuid = compat.read_uid(layer)
            if uuid:
                self.registry.set(uuid, layer)

    def _restore_transforms(self, uuids):
        """A Protection tag limits the position, rotation and scale of its object as soon as it is inserted and its
        limits are written, which put the object back to its limits (a slider at 0 instead of where the host had
        it: measured on a real rig). Once all tags are in, write the transform again where it is not what the host
        sent. Returns the uuids it touched."""
        done = set()
        for uuid in uuids:
            node = self.shadow.get(uuid)
            atom = self.registry.get(uuid)
            if node is None or atom is None or uuid in self.tracker.skip:
                continue
            wanted = node.data.get("p") or {}
            live = params.dump(atom, self.registry.uuid_of, params.TRANSFORM_IDS)
            keys = [key for key, value in wanted.items() if key in live and not _same_value(live[key], value)
                    and not (isinstance(value, dict) and None in (value.get("V") or ()))]
            keys = [key for key in keys if int(key.split(":", 1)[0]) in params.TRANSFORM_IDS]
            if keys:
                params.load(atom, wanted, self.registry.get, keys)
                done.add(uuid)
        return done

    def _reset_solved_scale(self, uuids):
        """The scale of a joint that an IK tag solves is the solver's result, and the solver stretches the scale a
        joint has now: a guest that joined while the host's leg was stretched (scale 1.5) stretched it once more
        (2.0, measured on a real rig). Such a joint starts at the scale a rig keeps joints at, 1, and the guest's own
        solver stretches it to the host's. Returns the uuids it touched."""
        solved = self.tracker.solved_joints(fresh=True)
        done = set()
        for uuid in uuids:
            atom = self.registry.get(uuid)
            if uuid not in solved or atom is None or uuid in self.tracker.skip:
                continue
            scale = atom.GetRelScale()
            if max(abs(scale.x - 1.0), abs(scale.y - 1.0), abs(scale.z - 1.0)) > 1e-6:
                atom.SetRelScale(c4d.Vector(1.0, 1.0, 1.0))
                done.add(uuid)
        return done

    def repair_textures(self):
        """Point the texture files of materials and shaders this side cannot find at their cached copies. Cinema 4D shortens
        a path inside a texture folder to a bare name some time after it was written (not at once, measured), and a guest
        whose document has no folder cannot find that name: the rig's skin stayed black. Returns the uuids it changed."""
        done = set()
        for uuid in list(self.textured):
            node, atom = self.shadow.get(uuid), self.registry.get(uuid)
            if node is None or atom is None:
                self.textured.discard(uuid)
            elif uuid not in self.tracker.skip and assets.apply_files(atom, node.data):
                done.add(uuid)
        for uuid in list(self.layered):   # a layer that appeared later (R19 adds one when the Reflection channel is switched on)
            node, atom = self.shadow.get(uuid), self.registry.get(uuid)
            if node is None or atom is None:
                self.layered.discard(uuid)
            elif uuid not in self.tracker.skip and nodes.layers.has_extra(atom, node.data["rl"]):
                nodes.layers.sync(atom, node.data["rl"])
                done.add(uuid)
        return done

    def _build_graphs(self, uuids):
        """XPresso graphs link to objects, tags and materials, which may be created after the tag in the same
        batch: build them now that everything exists. Returns the uuids it touched."""
        done = set()
        for uuid in uuids:
            node = self.shadow.get(uuid)
            atom = self.registry.get(uuid)
            if node is None or atom is None or uuid in self.tracker.skip:
                continue
            self._load(atom, node, {"params": set(), "geo": None, "sel": False, "uv": None, "xp": True})
            done.add(uuid)
        return done

    def _reload_weights(self, uuids):
        """Skin weights name their joints, which may be created after the tag in the same batch (deeper
        in the hierarchy): attach them again now that everything exists."""
        done = set()
        for uuid in uuids:
            node = self.shadow.get(uuid)
            atom = self.registry.get(uuid)
            if node is None or atom is None or uuid in self.tracker.skip:
                continue
            self._load(atom, node, {"params": set(), "geo": None, "sel": False, "uv": None, "cw": True})
            done.add(uuid)
        return done

    def _update(self, doc, op):
        uuid = op["uuid"]
        node = self.shadow.get(uuid)
        if node is not None and node.kind == "doc":
            self._load_document(doc, node.data)
            return
        atom = self.registry.get(uuid)
        if atom is None:
            return
        if node.kind == "track":
            patch = op.get("patch") or {}
            touched = set(patch.get("set", {})) | set(patch.get("sub", {}))
            try:
                tracks.load_track(atom, node.data, self.registry.get,
                                  keys="k" in touched, settings="p" in touched)
            except BaseException as exc_:
                if is_fatal(exc_):
                    raise
                LOG.exception("Could not load track %s", uuid)
            self.tracker.remember(uuid, atom)
            self.tracker.remember_track(uuid, atom)
            return
        if "name" in op:
            atom.SetName(compat.native_str(node.name))
        patch = op.get("patch") or {}
        if uuid not in self.tracker.skip and patch:
            change = nodes.data_change(patch)
            if (node.kind == "object" and "name" not in op and change["geo"] is None and not change["sel"]
                    and change["uv"] is None):
                keys = glidable_keys(change["params"], node.data.get("p") or {})
                if keys:
                    self.tweens.start(uuid, atom, keys, node.data["p"], time.time())
                    self.tracker.remember(uuid, atom)
                    return
            self._load(atom, node, change)
            if node.kind in ("material", "shader"):
                assets.apply_files(atom, node.data)
            if change["geo"] == nodes.geometry.FULL:
                self._reload_dependents(uuid)  # a topology change resizes UVW/selection tags
        self.tracker.remember(uuid, atom)

    def _load(self, atom, node, change):
        """Load node data; bad data (e.g. invalid geometry) is logged, never fatal,
        so the object itself and everything below it still arrive."""
        try:
            nodes.load_node(atom, node.data, self.registry.get, change, self.tracker.morph_cache)
        except BaseException as exc_:
            if is_fatal(exc_):
                raise
            LOG.exception("Could not load data of %s %s", node.kind, node.uuid)

    def _reload_dependents(self, owner_uuid):
        """Re-apply selection, UVW and pose morph tag content from the shadow after the owner's
        element counts changed (tags whose size no longer matches are skipped)."""
        for tag_node in self.shadow.children(owner_uuid, "tag"):
            if "s" not in tag_node.data and "uv" not in tag_node.data and "pm" not in tag_node.data:
                continue
            tag = self.registry.get(tag_node.uuid)
            if tag is not None and tag_node.uuid not in self.tracker.skip:
                self._load(tag, tag_node, {"params": set(), "geo": None, "sel": True, "uv": nodes.geometry.FULL,
                                           "pm": True})
                self.tracker.remember(tag_node.uuid, tag)

    def _move(self, doc, op):
        self.tweens.cancel(op["uuid"])
        atom = self.registry.get(op["uuid"])
        node = self.shadow.get(op["uuid"])
        if atom is None or node is None or node.kind in ("track", "doc"):
            return  # track order is C4D's own business
        atom.Remove()
        self._place(doc, atom, self.shadow.get(op["uuid"]))
        self.tracker.remember(op["uuid"], atom)

    def _delete(self, doc, op):
        uuid = op["uuid"]
        self.tweens.cancel(uuid)
        for child in list(self.registry.uuids()):
            if child not in self.shadow:
                self.tweens.cancel(child)
        atom = self.registry.get(uuid)
        if atom is not None:
            atom.Remove()
        # The shadow already dropped the subtree; forget every atom that is gone.
        for known in self.registry.uuids():
            if known not in self.shadow:
                self.registry.discard(known)
                self.tracker.forget(known)
                xpresso.forget(known)
                morph.NUMBER_MAP.pop(known, None)

    # Restore / reconcile (Phase 4) --------------------------------------------------------

    def restore(self, doc, uuids):
        """Put the given nodes (and, for deleted ones, their subtree) back as the shadow has them."""
        roots = [u for u in uuids if u in self.shadow
                 and not any(a in uuids for a in self.shadow.ancestors(u))]
        touched = set()
        for uuid in roots:
            self.tweens.cancel(uuid)
            node = self.shadow.get(uuid)
            atom = self.registry.get(uuid)
            try:
                if node.kind == "doc":
                    self._load_document(doc, node.data)
                    continue
                if atom is None or not _in_document(atom, doc):
                    self._rebuild(doc, node, touched)
                    continue
                if node.kind == "track":
                    tracks.load_track(atom, node.data, self.registry.get)
                    self.tracker.remember_track(uuid, atom)
                    touched.add(uuid)
                    continue
                atom.SetName(compat.native_str(node.name))
                if uuid not in self.tracker.skip:
                    self._load(atom, node, None)
                    self._reload_dependents(uuid)
                if node.kind in ("object", "tag") and self._parent_uuid(atom) != node.parent:
                    atom.Remove()
                    self._place(doc, atom, node)
                touched.add(uuid)
            except BaseException as exc_:
                if is_fatal(exc_):
                    raise
                LOG.exception("Could not restore %s", uuid)
        for uuid in list(touched) + [self.shadow.get(u).parent for u in touched if self.shadow.get(u)]:
            atom = self.registry.get(uuid) if uuid else None
            if atom is not None:
                self.tracker.remember(uuid, atom)
        self.tracker.baseline(doc)
        return len(roots)

    def _rebuild(self, doc, node, touched):
        """Recreate a node and everything below it from the shadow (parents first)."""
        queue = [node]
        while queue:
            current = queue.pop(0)
            if self._materialize(doc, current) is not None:
                touched.add(current.uuid)
            queue.extend(self.shadow.children(current.uuid))

    def _parent_uuid(self, atom):
        parent = atom.GetObject() if isinstance(atom, c4d.BaseTag) else atom.GetUp()
        return compat.read_uid(parent) if parent is not None else None

    def reconcile(self, doc, truth, uuid):
        """Make shadow and scene match `truth` (the mirror's SceneState) for uuid."""
        target = truth.get(uuid)
        current = self.shadow.get(uuid)
        ops = []
        if target is None and current is not None:
            ops.append({"op": "delete", "uuid": uuid})
        elif target is not None and current is None:
            queue = [target]
            while queue:
                node = queue.pop(0)
                if node.uuid not in self.shadow:
                    op = node.to_create_op()
                    op["prev"] = self._valid_prev(op.get("prev"), op.get("parent"), node.kind, ops)
                    ops.append(op)
                queue.extend(truth.children(node.uuid))
        elif target is not None:
            if (current.parent, current.prev) != (target.parent, target.prev):
                ops.append({"op": "move", "uuid": uuid, "parent": target.parent,
                            "prev": self._valid_prev(target.prev, target.parent, target.kind, ops)})
            ops.append({"op": "update", "uuid": uuid, "name": target.name,
                        "patch": {"set": copy.deepcopy(target.data)} if target.data else {}})
        if ops:
            self.apply(doc, ops)
        return len(ops)

    def _valid_prev(self, prev, parent, kind, pending):
        """prev if it is (or is about to be) a sibling in the shadow, else None (= first)."""
        if prev is None:
            return None
        if any(op.get("uuid") == prev for op in pending):
            return prev
        node = self.shadow.get(prev)
        return prev if node is not None and node.parent == parent and node.kind == kind else None


def _same_value(live, wanted, tolerance=1e-4):
    if isinstance(live, dict) and isinstance(wanted, dict) and "V" in live and "V" in wanted:
        return len(live["V"]) == len(wanted["V"]) and all(
            b is None or abs(a - b) <= tolerance for a, b in zip(live["V"], wanted["V"]))
    return live == wanted


def _tags(obj):
    tag = obj.GetFirstTag()
    while tag is not None:
        yield tag
        tag = tag.GetNext()


def _in_document(atom, doc):
    try:
        return atom.GetDocument() == doc
    except BaseException as exc_:
        if is_fatal(exc_):
            raise
        return False
