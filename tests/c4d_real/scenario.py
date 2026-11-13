# -*- coding: utf-8 -*-
"""Phase 3 scenario against the REAL Cinema 4D API (Python 2.7 and 3.x).

Runs inside Cinema 4D (c4dpy, or the plugin's self-test command). Two
processes play "host" and "guest"; they coordinate through the session's own
presence messages, so no other channel is needed.

Configured with environment variables (or run_scenario() arguments):
    C4D_COLLAB_ROLE     host | guest
    C4D_COLLAB_PORT     port to host on / join
    C4D_COLLAB_RESULTS  path of the JSON result file to write
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import io
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
PLUGIN_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "plugin", "c4d_collab"))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

import c4d  # noqa: E402
from c4d import documents  # noqa: E402

from c4d_collab_lib.c4d_adapter import compat  # noqa: E402
from c4d_collab_lib.c4d_adapter.sync import SceneSync  # noqa: E402
from c4d_collab_lib.core.log import get_logger, log_to_file  # noqa: E402
from c4d_collab_lib.core.session import CollabSession  # noqa: E402

PASSWORD = "selftest-1234"
STEP_TIMEOUT = 30.0
# How long to wait for the other side to show up (a human may have to click "Self-Test").
PEER_WAIT = float(os.environ.get("C4D_COLLAB_WAIT", "90"))
ROOT_NEW_NAME = "Kök Şekil"


class Scenario(object):
    def __init__(self, role, port, results_path):
        self.role = role
        self.port = int(port)
        self.results_path = results_path
        self.checks = []
        self.free_active = False   # True: the test plays a user who changed the active document itself
        self.session = CollabSession(client_info=compat.client_info("selftest"))
        self.sync = SceneSync(self.session)
        self.doc = None
        if os.environ.get("C4D_COLLAB_TRACE_OPS") or os.path.exists(os.path.join(HERE, "out", "trace.enabled")):
            self._trace_ops()

    def _trace_ops(self):
        """Log a one-line summary of every op this side sends (debugging aid)."""
        original = self.session.send_ops
        trace = get_logger("trace")

        def traced(ops):
            for op in ops:
                patch = op.get("patch") or {}
                trace.info("SEND %s %s %s patch=%s", op.get("op"), op.get("uuid", "")[:8], op.get("name", ""),
                           json.dumps(patch, sort_keys=True)[:300])
            original(ops)
        self.session.send_ops = traced

    # Helpers ---------------------------------------------------------------------------

    def check(self, name, ok, detail=""):
        self.checks.append({"name": name, "ok": bool(ok), "detail": "%s" % (detail,)})
        print("[%s] %s %s %s" % (self.role, "PASS" if ok else "FAIL", name, detail))

    def pump(self, seconds=0.0):
        end = time.time() + seconds
        while True:
            if self.doc is not None and not self.free_active:
                documents.SetActiveDocument(self.doc)
            self.session.pump()
            if self.doc is None and self.sync.doc is not None:
                self.doc = self.sync.doc
            self.sync.tick()
            self.sync.advance(time.time())  # other users' transforms glide in real time
            if time.time() >= end:
                return
            time.sleep(0.01)

    def wait(self, what, condition, timeout=STEP_TIMEOUT):
        end = time.time() + timeout
        while time.time() < end:
            self.pump()
            try:
                if condition():
                    return True
            except Exception:
                pass
            time.sleep(0.02)
        self.check("wait: " + what, False, "timed out after %ds" % timeout)
        return False

    def signal(self, step):
        self.session.set_presence(step=step)
        self.pump(0.2)

    def peer_data(self):
        for user_id, data in self.session.mirror.presence.items():
            if user_id != self.session.mirror.user_id:
                return data
        return {}

    def peer_step(self):
        for user_id, data in self.session.mirror.presence.items():
            if user_id != self.session.mirror.user_id:
                return data.get("step", 0)
        return 0

    def find(self, name):
        return self.doc.SearchObject(compat.native_str(name)) if self.doc is not None else None

    def tree(self):
        def tags(obj):
            result = []
            tag = obj.GetFirstTag()
            while tag is not None:
                if tag.GetType() in TREE_TAGS:
                    result.append([compat.from_c4d_str(tag.GetName()), tag.GetType()])
                tag = tag.GetNext()
            return result

        def walk(obj):
            items = []
            while obj is not None:
                if obj.GetType() not in LOCAL_ONLY:
                    items.append([compat.from_c4d_str(obj.GetName()), obj.GetType(), tags(obj), walk(obj.GetDown())])
                obj = obj.GetNext()
            return items
        return walk(self.doc.GetFirstObject())

    # Roles --------------------------------------------------------------------------------

    def run(self):
        log_to_file(compat.native_str(self.results_path + ".log"))
        try:
            getattr(self, "run_" + self.role)()
        except Exception:
            self.check("unexpected exception", False, traceback.format_exc())
        finally:
            try:
                self.session.leave("selftest finished")
            except Exception:
                pass
            self.write()

    def write(self):
        payload = {
            "role": self.role, "c4d": c4d.GetC4DVersion(), "python": sys.version.split()[0],
            "passed": sum(1 for c in self.checks if c["ok"]), "failed": sum(1 for c in self.checks if not c["ok"]),
            "checks": self.checks, "log": list(self.session.log),
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        if isinstance(text, bytes):  # Python 2 returns str when everything is ASCII
            text = text.decode("utf-8")
        with io.open(self.results_path, "w", encoding="utf-8") as handle:
            handle.write(text)

    # Host -----------------------------------------------------------------------------

    def build_scene(self):
        doc = documents.BaseDocument()
        doc.SetDocumentName(compat.native_str("selftest-host"))
        documents.InsertBaseDocument(doc)
        documents.SetActiveDocument(doc)
        root = c4d.BaseObject(c4d.Onull)
        root.SetName(compat.native_str("Root"))
        doc.InsertObject(root)
        cube = c4d.BaseObject(c4d.Ocube)
        cube.SetName(compat.native_str("Cube"))
        cube[c4d.PRIM_CUBE_LEN] = c4d.Vector(300, 200, 100)
        cube.SetAbsPos(c4d.Vector(10, 20, 30))
        doc.InsertObject(cube, root)
        fps = doc.GetFps()
        track = c4d.CTrack(cube, pos_x_id())
        cube.InsertTrackSorted(track)
        add_key(track.GetCurve(), 0, fps, 0.0)
        add_key(track.GetCurve(), 30, fps, 100.0)
        phong = cube.MakeTag(c4d.Tphong)
        phong.SetName(compat.native_str("Phong"))
        python_tag = cube.MakeTag(getattr(c4d, "Tpython", 1022749), phong)
        if python_tag is not None:
            python_tag.SetName(compat.native_str("Script"))
        target = root.MakeTag(c4d.Ttargetexpression)
        target.SetName(compat.native_str("Target"))
        target[c4d.TARGETEXPRESSIONTAG_LINK] = cube
        generator = c4d.BaseObject(getattr(c4d, "Opython", 1023866))  # R19 has no constant
        generator.SetName(compat.native_str("PyGen"))
        doc.InsertObject(generator, None, root)
        poly = c4d.PolygonObject(4, 1)
        poly.SetName(compat.native_str("Poly"))
        for index, point in enumerate([c4d.Vector(0, 0, 0), c4d.Vector(100, 0, 0),
                                       c4d.Vector(100, 100, 0), c4d.Vector(0, 100, 0)]):
            poly.SetPoint(index, point)
        poly.SetPolygon(0, c4d.CPolygon(0, 1, 2, 3))
        poly.Message(c4d.MSG_UPDATE)
        doc.InsertObject(poly, None, generator)
        c4d.EventAdd()
        return doc

    def build_modeling_content(self):
        """A 20x20 quad grid with selection and UVW tags, and a bezier spline."""
        doc = self.doc
        mesh = c4d.PolygonObject(441, 400)
        mesh.SetName(compat.native_str("Mesh"))
        mesh.SetAllPoints([c4d.Vector(x * 10, y * 10, 0) for y in range(21) for x in range(21)])
        for y in range(20):
            for x in range(20):
                a = y * 21 + x
                mesh.SetPolygon(y * 20 + x, c4d.CPolygon(a, a + 1, a + 22, a + 21))
        mesh.Message(c4d.MSG_UPDATE)
        doc.InsertObject(mesh)
        selection = mesh.MakeTag(c4d.Tpolygonselection)
        selection.SetName(compat.native_str("PolySel"))
        for index in (0, 1, 2, 3, 40, 41, 399):
            selection.GetBaseSelect().Select(index)
        uvw = mesh.MakeTag(c4d.Tuvw)
        uvw.SetName(compat.native_str("UVs"))
        mesh.ResizeObject(441, 400)  # a fresh UVW tag has 0 entries until its owner is resized
        for index in range(uvw.GetDataCount()):
            u, v = (index % 20) / 20.0, (index // 20) / 20.0
            uvw.SetSlow(index, c4d.Vector(u, v, 0), c4d.Vector(u + 0.05, v, 0),
                        c4d.Vector(u + 0.05, v + 0.05, 0), c4d.Vector(u, v + 0.05, 0))
        spline = c4d.BaseObject(c4d.Ospline)
        spline.SetName(compat.native_str("Curve"))
        spline.ResizeObject(6, 1)
        spline.SetSegment(0, 6, False)
        doc.InsertObject(spline)
        spline[c4d.SPLINEOBJECT_TYPE] = c4d.SPLINETYPE_BEZIER
        for index in range(6):
            spline.SetPoint(index, c4d.Vector(index * 10, (index % 2) * 20, 0))
            spline.SetTangent(index, c4d.Vector(-3, index, 0), c4d.Vector(3, -index, 0))
        spline.Message(c4d.MSG_UPDATE)
        c4d.EventAdd()

    def run_host(self):
        self.doc = self.build_scene()
        self.session.host("Host", self.port, password=PASSWORD, bind="127.0.0.1")
        if not self.wait("guest joined and verified the scene", lambda: self.peer_step() >= 1, PEER_WAIT):
            return
        self.check("host: guest joined", len(self.session.users()) == 2)

        cube = self.find("Cube")
        cube[c4d.PRIM_CUBE_LEN] = c4d.Vector(400, 200, 100)
        cube.SetAbsPos(c4d.Vector(cube.GetAbsPos().x, -50, 0))
        self.signal(2)

        if self.wait("guest renamed Root and added a Cone",
                     lambda: self.find(ROOT_NEW_NAME) is not None and self.find("Cone") is not None):
            self.check("host: rename from guest arrived (Turkish letters)", True)
            self.check("host: object created by guest arrived", self.find("Cone").GetType() == c4d.Ocone)

        if not self.wait("guest step 2", lambda: self.peer_step() >= 2):
            return
        cube = self.find("Cube")
        poly = self.find("Poly")
        cube.Remove()
        self.doc.InsertObject(cube, None, poly)       # move Cube to root, after Poly
        phong = cube.GetFirstTag()
        while phong is not None and phong.GetType() != c4d.Tphong:
            phong = phong.GetNext()
        if phong is not None:
            phong.Remove()                            # delete the Phong tag
        poly.SetPoint(0, c4d.Vector(-25, -25, 0))     # modeling edit
        poly.Message(c4d.MSG_UPDATE)
        c4d.EventAdd()
        self.signal(3)

        if not self.wait("guest verified step 3", lambda: self.peer_step() >= 3):
            return
        server = self.session.server
        before = server.scene.seq
        self.pump(2.0)
        self.check("host: no echo loop (server sequence stable for 2s)", server.scene.seq == before,
                   "seq %d -> %d" % (before, server.scene.seq))

        # Phase 4: lock the cube by selecting it; the guest's edits must bounce off.
        cube = self.find("Cube")
        cube.SetBit(c4d.BIT_ACTIVE)
        cube_uuid = compat.read_uid(cube)
        if self.wait("lock granted", lambda: server.locks.owner(cube_uuid) == self.session.mirror.user_id):
            self.check("host: selecting locks the object", True)
        seq_locked = server.scene.seq
        self.signal(4)
        if not self.wait("guest tried to edit the locked cube", lambda: self.peer_step() >= 4):
            return
        cube = self.find("Cube")
        self.check("host: guest's forbidden edits never reached the server", server.scene.seq == seq_locked,
                   "seq %d -> %d" % (seq_locked, server.scene.seq))
        self.check("host: cube unchanged", cube is not None and cube[c4d.PRIM_CUBE_SUBX] != 99)
        cube.DelBit(c4d.BIT_ACTIVE)
        if self.wait("lock released", lambda: server.locks.owner(cube_uuid) is None):
            self.check("host: deselecting releases the lock", True)
        self.signal(5)
        if self.wait("guest edit after release", lambda: self.find("Cube")[c4d.PRIM_CUBE_SUBX] == 7):
            self.check("host: guest can edit after the lock is released", True)
        self.signal(6)

        # Phase 5: edit animation and document time settings.
        if not self.wait("guest step 6", lambda: self.peer_step() >= 6):
            return
        fps = self.doc.GetFps()
        curve = self.find("Cube").FindCTrack(pos_x_id()).GetCurve()
        curve.GetKey(1).SetValue(curve, 250.0)
        add_key(curve, 15, fps, 50.0)
        curve.GetKey(0).SetValueRight(curve, 7.0)       # tangent: moves no dirty counter
        self.doc.SetFps(25)
        self.doc.SetMaxTime(c4d.BaseTime(5.0))
        c4d.EventAdd()
        self.signal(7)

        if not self.wait("guest animated something", lambda: self.peer_step() >= 8, 60):
            return
        sub_id = c4d.DescID(c4d.DescLevel(c4d.PRIM_CUBE_SUBX, c4d.DTYPE_LONG, 0))
        if self.wait("guest's new track", lambda: self.find("Cube").FindCTrack(sub_id) is not None):
            self.check("host: track created by guest arrived", key_list(self.find("Cube").FindCTrack(sub_id))
                       == [(0.0, 1.0), (0.4, 6.0)], key_list(self.find("Cube").FindCTrack(sub_id)))
        guest_id = [u for u in self.session.mirror.presence if u != self.session.mirror.user_id]
        frame = self.session.mirror.presence.get(guest_id[0], {}).get("frame") if guest_id else None
        self.check("host: sees the guest's current frame", frame == 10, frame)
        self.check("host: own frame not changed by the guest", self.doc.GetTime().GetFrame(self.doc.GetFps()) == 0)
        before = server.scene.seq
        self.pump(2.5)
        self.check("host: no echo with animation (2.5s)", server.scene.seq == before,
                   "seq %d -> %d" % (before, server.scene.seq))
        self.signal(9)

        # Phase 6: modeling. Create content, then edit it.
        self.build_modeling_content()
        self.signal(10)
        if not self.wait("guest verified the new mesh", lambda: self.peer_step() >= 11, 60):
            return
        mesh = self.find("Mesh")
        selection = tag_by_name(mesh, "PolySel")
        uvw = tag_by_name(mesh, "UVs")
        spline = self.find("Curve")
        mesh.SetPoint(300, c4d.Vector(-5, -5, 9))
        mesh.Message(c4d.MSG_UPDATE)
        selection.GetBaseSelect().DeselectAll()           # moves no dirty counter
        for index in (10, 11, 12, 200):
            selection.GetBaseSelect().Select(index)
        uvw.SetSlow(299, c4d.Vector(0.9, 0.8, 0), c4d.Vector(0.7, 0.6, 0), c4d.Vector(0.5, 0.4, 0), c4d.Vector(0.25, 0.125, 0))
        spline.SetTangent(1, c4d.Vector(-9, 9, 0), c4d.Vector(9, -9, 0))   # moves no dirty counter
        spline.SetSegment(0, 6, True)                                       # neither
        c4d.EventAdd()
        self.signal(12)

        if not self.wait("guest edited the mesh", lambda: self.peer_step() >= 13, 60):
            return
        mesh = self.find("Mesh")
        if self.wait("guest's point edit", lambda: self.find("Mesh").GetPoint(5) == c4d.Vector(1, 2, 3)):
            self.check("host: point edit from guest arrived", True)
        if self.wait("guest's selection edit", lambda: 100 in selected_indices(tag_by_name(self.find("Mesh"), "PolySel"), 400)):
            self.check("host: selection tag edit from guest arrived", True)

        # Real topology change: extrude one polygon (adds points and polygons, C4D resizes the UVW tag).
        mesh = self.find("Mesh")
        mesh.GetPolygonS().DeselectAll()
        mesh.GetPolygonS().Select(0)
        settings = c4d.BaseContainer()
        settings[c4d.MDATA_EXTRUDE_OFFSET] = 20.0
        c4d.utils.SendModelingCommand(command=c4d.ID_MODELING_EXTRUDE_TOOL, list=[mesh],
                                      mode=c4d.MODELINGCOMMANDMODE_POLYGONSELECTION, bc=settings, doc=self.doc)
        c4d.EventAdd()
        points, polys = mesh.GetPointCount(), mesh.GetPolygonCount()
        self.check("host: extrude added geometry", points > 441 and polys > 400, "%d/%d" % (points, polys))
        self.session.set_presence(points=points, polys=polys)
        self.signal(14)

        if not self.wait("guest verified the extrude", lambda: self.peer_step() >= 15, 60):
            return

        # Types that do not exist on the other machine (a plugin it lacks, a newer C4D): a real
        # Cinema 4D raises a bare BaseException when asked to create them. Found by the Phase 7 run.
        self.session.send_ops([
            {"op": "create", "uuid": "a" * 32, "kind": "object", "ctype": 1000099, "parent": None, "prev": None,
             "name": "FromAPlugin", "data": {"p": {}}},
            {"op": "create", "uuid": "b" * 32, "kind": "tag", "ctype": 1000098, "parent": "a" * 32, "prev": None,
             "name": "UnknownTag", "data": {"p": {}}},
        ])
        self.signal(16)
        if not self.wait("guest handled the unknown types", lambda: self.peer_step() >= 17, 60):
            return
        server = self.session.server
        before = server.scene.seq
        self.pump(2.5)
        self.check("host: no echo after modeling (2.5s)", server.scene.seq == before, "seq %d -> %d" % (before, server.scene.seq))
        self.check("host: unknown types stay untouched on the server", server.scene.get("a" * 32).ctype == 1000099)
        self.signal(18)
        if not self.wait("guest finished modeling checks", lambda: self.peer_step() >= 19, 30):
            return

        # Phase 8: materials and shaders (the screenshot of R19 vs 2023 showed textures missing).
        material = c4d.BaseMaterial(c4d.Mmaterial)
        material.SetName("Wood")
        material[c4d.MATERIAL_COLOR_COLOR] = c4d.Vector(0.5, 0.3, 0.1)
        bitmap = c4d.BaseShader(c4d.Xbitmap)
        bitmap[c4d.BITMAPSHADER_FILENAME] = TEXTURE_PATH
        material.InsertShader(bitmap)
        material[c4d.MATERIAL_COLOR_SHADER] = bitmap
        self.doc.InsertMaterial(material)
        texture_tag = self.find("Cube").MakeTag(c4d.Ttexture)
        texture_tag[c4d.TEXTURETAG_MATERIAL] = material
        c4d.EventAdd()
        self.signal(20)
        if not self.wait("guest verified the material", lambda: self.peer_step() >= 21, 60):
            return
        material[c4d.MATERIAL_COLOR_COLOR] = c4d.Vector(0.0, 1.0, 0.0)
        bitmap[c4d.BITMAPSHADER_FILENAME] = TEXTURE_PATH_2
        c4d.EventAdd()
        self.signal(22)
        self.wait("guest verified material edits", lambda: self.peer_step() >= 23 or len(self.session.users()) < 2, 30)

        # Phase 9: a rig (joints, Skin deformer, Weight tag) and an empty mesh
        body = c4d.BaseObject(c4d.Opolygon)
        body.SetName("Body")
        body.ResizeObject(4, 1)
        body.SetAllPoints([c4d.Vector(0, 0, 0), c4d.Vector(100, 0, 0), c4d.Vector(100, 100, 0), c4d.Vector(0, 100, 0)])
        body.SetPolygon(0, c4d.CPolygon(0, 1, 2, 3))
        self.doc.InsertObject(body)
        hips = c4d.BaseObject(c4d.Ojoint)
        hips.SetName("Hips")
        self.doc.InsertObject(hips)
        spine = c4d.BaseObject(c4d.Ojoint)
        spine.SetName("Spine")
        self.doc.InsertObject(spine, hips)
        spine.SetRelPos(c4d.Vector(0, 100, 0))
        skin = c4d.BaseObject(c4d.Oskin)
        skin.SetName("Skin")
        self.doc.InsertObject(skin, body)
        weights = body.MakeTag(c4d.Tweights)
        weights.SetName("Weights")
        weights.AddJoint(hips)
        weights.AddJoint(spine)
        weights.SetWeightMap(0, [1.0, 0.5, 0.25, 0.0])
        weights.SetWeightMap(1, [0.0, 0.5, 0.75, 1.0])
        empty = c4d.BaseObject(c4d.Opolygon)
        empty.SetName("EmptyMesh")
        self.doc.InsertObject(empty)
        c4d.EventAdd()
        self.signal(24)
        self.wait("guest verified the rig", lambda: self.peer_step() >= 25 or len(self.session.users()) < 2, 60)
        server = self.session.server
        before = server.scene.seq
        self.pump(2.5)
        self.check("host: no echo after the rig (2.5s)", server.scene.seq == before, "seq %d -> %d" % (before, server.scene.seq))
        self.check("host: the report names what stays local", self.sync.tracker.unsynced_report() is None
                   or isinstance(self.sync.tracker.unsynced_report(), str))

        # Phase 8: a face with a Pose Morph tag; the tag animates the mesh's own points
        face = make_grid(self.doc, "Face", 10)
        pose = face.MakeTag(c4d.Tposemorph)
        pose.SetName("Pose Morph")
        pose[c4d.ID_CA_POSE_POINTS] = True
        pose.ExitEdit(self.doc, True)
        pose.InitMorphs()
        pose.AddMorph().Store(self.doc, pose, c4d.CAMORPH_DATA_FLAGS_POINTS)
        smile = pose.AddMorph()
        smile.Store(self.doc, pose, c4d.CAMORPH_DATA_FLAGS_POINTS)
        smile.SetName("smile")
        set_offsets(self.doc, pose, smile, {5: c4d.Vector(0, 0, 50), 100: c4d.Vector(0, 2, 0)})
        pose.UpdateMorphs()
        pose[c4d.ID_CA_POSE_MODE] = c4d.ID_CA_POSE_MODE_ANIMATE
        pose[pose.GetMorphID(1)] = 0.5
        evaluate(self.doc)
        self.check("host: the tag animates the mesh itself", abs(face.GetPoint(5).z - 25) < 1e-6, face.GetPoint(5))
        self.signal(26)
        if not self.wait("guest verified the face", lambda: self.peer_step() >= 27 or len(self.session.users()) < 2, 60):
            return
        sent = []
        original = self.session.send_ops
        self.session.send_ops = lambda ops: (sent.extend(ops), original(ops))[1]
        for value in (0.2, 0.8, 1.0):  # a rig moving the slider; the scene is evaluated each time
            pose[pose.GetMorphID(1)] = value
            evaluate(self.doc)
            self.pump(0.3)
        self.session.send_ops = original
        geometry = [op for op in sent if "g" in (op.get("patch") or {}).get("sub", {})
                    or "g" in (op.get("patch") or {}).get("set", {})]
        self.check("host: slider moves are sent", bool(sent), len(sent))
        self.check("host: slider moves never send the animated face as geometry", not geometry, geometry[:1])
        shared = self.session.mirror.scene.get(compat.read_uid(face))
        from c4d_collab_lib.core import protocol
        base_z = protocol.unpack_floats(shared.data["g"]["pts"]["0"])[17] if shared is not None else None
        self.check("host: the shared mesh is the base, not the smile", base_z == 0.0, base_z)
        self.signal(28)
        self.wait("guest verified the slider", lambda: self.peer_step() >= 29 or len(self.session.users()) < 2, 60)
        before = server.scene.seq
        for _ in range(5):
            evaluate(self.doc)
            self.pump(0.3)
        self.check("host: no echo after the face (1.5s, evaluating)", server.scene.seq == before,
                   "seq %d -> %d" % (before, server.scene.seq))
        server = self.session.server
        before = server.scene.seq
        self.pump(1.5)
        self.check("host: no echo after material edits", server.scene.seq == before, "seq %d -> %d" % (before, server.scene.seq))

        # Phase 8: opening another project leaves the session (guest first, then the host)
        self.signal(30)
        if not self.wait("the guest left after opening another project", lambda: len(self.session.users()) == 1, 30):
            return
        self.check("host: the guest that opened another project is gone from the user list", len(self.session.users()) == 1)
        self.check("host: the session goes on for the host", self.session.active and self.session.server is not None)
        self.free_active = True
        other = documents.BaseDocument()
        documents.InsertBaseDocument(other)
        documents.SetActiveDocument(other)
        self.pump(1.0)
        self.check("host: opening another project ended the session", not self.session.active
                   and self.session.server is None, "server %r" % (self.session.server,))
        self.check("host: it says why", any("The session ended: you opened another project" in line
                                           for line in self.session.log))

    # Guest -------------------------------------------------------------------------------

    def run_guest(self):
        before = documents.GetActiveDocument()
        deadline = time.time() + PEER_WAIT
        while True:  # the host may not be up yet: retry until it answers
            self.session.join("127.0.0.1", self.port, "Guest", password=PASSWORD)
            end = time.time() + 10
            while time.time() < end and self.session.active and not (self.doc is not None and self.find("Cube")):
                self.pump()
                time.sleep(0.02)
            if self.doc is not None and self.find("Cube") is not None:
                break
            self.session.leave("retry")
            if time.time() > deadline:
                self.check("wait: scene received", False, "host never answered")
                return
            time.sleep(2)
        # An empty untitled document is closed by C4D when another one opens (Phase 0).
        replaced = not before.IsAlive() or self.doc != before
        self.check("guest: scene arrived in a new document", replaced)
        expected = [
            ["Root", c4d.Onull, [["Target", c4d.Ttargetexpression]],
             [["Cube", c4d.Ocube, [["Phong", c4d.Tphong]], []]]],
            ["Poly", c4d.Opolygon, [], []],
        ]
        self.check("guest: hierarchy and tags match", self.tree() == expected, self.tree())
        self.check("guest: no Python generator received", self.find("PyGen") is None)
        cube = self.find("Cube")
        has_python_tag = False
        tag = cube.GetFirstTag()
        while tag is not None:
            has_python_tag = has_python_tag or tag.GetType() == 1022749
            tag = tag.GetNext()
        self.check("guest: no Python tag received", not has_python_tag)
        self.check("guest: cube size", cube[c4d.PRIM_CUBE_LEN] == c4d.Vector(300, 200, 100), cube[c4d.PRIM_CUBE_LEN])
        # X is animated, so only the static Y/Z travel as plain values.
        position = cube.GetAbsPos()
        self.check("guest: cube position (static components of an animated vector)",
                   (position.y, position.z) == (20, 30), position)
        target = self.find("Root").GetFirstTag()
        link = target[c4d.TARGETEXPRESSIONTAG_LINK] if target is not None else None
        self.check("guest: target tag links to the guest's own cube", link is not None and link == cube)
        self.check("guest: animation track arrived with its keys",
                   key_list(cube.FindCTrack(pos_x_id())) == [(0.0, 0.0), (1.0, 100.0)],
                   key_list(cube.FindCTrack(pos_x_id())))
        poly = self.find("Poly")
        self.check("guest: polygon geometry", poly.GetPointCount() == 4 and poly.GetPolygonCount() == 1
                   and poly.GetPoint(2) == c4d.Vector(100, 100, 0))
        self.signal(1)

        if not self.wait("host step 2", lambda: self.peer_step() >= 2):
            return
        ok = self.wait("cube edit from host", lambda: self.find("Cube")[c4d.PRIM_CUBE_LEN].x == 400)
        if ok:
            self.check("guest: parameter edit from host arrived", True)
            position = self.find("Cube").GetAbsPos()
            self.check("guest: transform edit from host arrived", (position.y, position.z) == (-50, 0), position)
        self.find("Root").SetName(compat.native_str(ROOT_NEW_NAME))
        cone = c4d.BaseObject(c4d.Ocone)
        cone.SetName(compat.native_str("Cone"))
        self.doc.InsertObject(cone)
        c4d.EventAdd()
        self.signal(2)

        if not self.wait("host step 3", lambda: self.peer_step() >= 3):
            return

        def moved():
            cube = self.find("Cube")
            return cube is not None and cube.GetUp() is None and cube.GetPred() is not None \
                and compat.from_c4d_str(cube.GetPred().GetName()) == "Poly"
        if self.wait("Cube moved to root after Poly", moved):
            self.check("guest: reparent/reorder from host arrived", True)
        cube = self.find("Cube")
        tag = cube.GetFirstTag()
        phong_left = False
        while tag is not None:
            phong_left = phong_left or tag.GetType() == c4d.Tphong
            tag = tag.GetNext()
        self.check("guest: tag deletion from host arrived", not phong_left)
        self.check("guest: point edit from host arrived",
                   self.find("Poly").GetPoint(0) == c4d.Vector(-25, -25, 0), self.find("Poly").GetPoint(0))
        self.signal(3)

        # Phase 4: the host selected (locked) the cube.
        if not self.wait("host step 4", lambda: self.peer_step() >= 4):
            return
        cube = self.find("Cube")
        cube_uuid = compat.read_uid(cube)
        self.check("guest: sees the cube locked by the host", not self.session.mirror.editable(cube_uuid))
        cube.SetBit(c4d.BIT_ACTIVE)
        self.pump(0.5)
        self.check("guest: cannot select the locked cube", not self.find("Cube").GetBit(c4d.BIT_ACTIVE))
        cube = self.find("Cube")
        cube[c4d.PRIM_CUBE_SUBX] = 99
        cube.SetName(compat.native_str("Hacked"))
        self.pump(1.0)
        cube = self.find("Cube")
        self.check("guest: forbidden edit undone (name and parameter)",
                   cube is not None and cube[c4d.PRIM_CUBE_SUBX] != 99, cube and cube[c4d.PRIM_CUBE_SUBX])
        cube.Remove()
        self.pump(1.0)
        restored = self.find("Cube")
        self.check("guest: forbidden delete undone", restored is not None
                   and restored[c4d.PRIM_CUBE_LEN].x == 400, restored and restored[c4d.PRIM_CUBE_LEN])
        self.signal(4)

        if not self.wait("host released the cube", lambda: self.peer_step() >= 5):
            return
        self.wait("cube editable again", lambda: self.session.mirror.editable(cube_uuid))
        self.find("Cube")[c4d.PRIM_CUBE_SUBX] = 7
        self.pump(0.5)
        self.signal(5)
        if not self.wait("host step 6", lambda: self.peer_step() >= 6, 20):
            return
        self.signal(6)

        # Phase 5
        if not self.wait("host step 7", lambda: self.peer_step() >= 7):
            return

        def keys_now():
            return key_list(self.find("Cube").FindCTrack(pos_x_id()))
        if self.wait("key edits from host", lambda: keys_now() == [(0.0, 0.0), (0.5, 50.0), (1.0, 250.0)]):
            self.check("guest: key value edit and new key arrived", True)
        curve = self.find("Cube").FindCTrack(pos_x_id()).GetCurve()
        if self.wait("tangent edit (periodic scan)", lambda: abs(curve.GetKey(0).GetValueRight() - 7.0) < 1e-6):
            self.check("guest: tangent edit without dirty counter arrived", True)
        if self.wait("document settings", lambda: self.doc.GetFps() == 25 and abs(self.doc.GetMaxTime().Get() - 5.0) < 1e-6):
            self.check("guest: fps and time range arrived", True)

        cube = self.find("Cube")
        fps = self.doc.GetFps()
        track = c4d.CTrack(cube, c4d.DescID(c4d.DescLevel(c4d.PRIM_CUBE_SUBX, c4d.DTYPE_LONG, 0)))
        cube.InsertTrackSorted(track)
        add_key(track.GetCurve(), 0, fps, 1.0)
        add_key(track.GetCurve(), 10, fps, 6.0)
        self.doc.SetTime(c4d.BaseTime(10, fps))
        c4d.EventAdd()
        self.pump(1.5)
        self.signal(8)
        self.wait("host finished", lambda: self.peer_step() >= 9, 30)
        self.signal(9)

        # Phase 6: modeling
        if not self.wait("host built the mesh", lambda: self.peer_step() >= 10 and self.find("Mesh") is not None
                         and self.find("Curve") is not None
                         and tag_by_name(self.find("Mesh"), "UVs") is not None
                         and tag_by_name(self.find("Mesh"), "UVs").GetDataCount() == 400
                         and tag_by_name(self.find("Mesh"), "PolySel") is not None, 60):
            return
        mesh = self.find("Mesh")
        self.check("guest: mesh geometry arrived", mesh.GetPointCount() == 441 and mesh.GetPolygonCount() == 400,
                   "%d/%d" % (mesh.GetPointCount(), mesh.GetPolygonCount()))
        self.check("guest: mesh point values", mesh.GetPoint(300) == c4d.Vector((300 % 21) * 10, (300 // 21) * 10, 0),
                   mesh.GetPoint(300))
        poly = mesh.GetAllPolygons()[41]
        self.check("guest: mesh polygon values", (poly.a, poly.b, poly.c, poly.d) == (41 // 20 * 21 + 41 % 20, 41 // 20 * 21 + 41 % 20 + 1,
                                                                                  41 // 20 * 21 + 41 % 20 + 22, 41 // 20 * 21 + 41 % 20 + 21))
        selection = tag_by_name(mesh, "PolySel")
        self.check("guest: polygon selection tag arrived", selected_indices(selection, 400) == [0, 1, 2, 3, 40, 41, 399],
                   selected_indices(selection, 400))
        uvw = tag_by_name(mesh, "UVs")
        self.check("guest: UVW tag arrived (400 entries, values)", uvw.GetDataCount() == 400 and uv_of(uvw, 7) == expected_uv(7)
                   and uv_of(uvw, 399) == expected_uv(399), uv_of(uvw, 7))
        spline = self.find("Curve")
        tangent = spline.GetTangent(3)
        self.check("guest: spline arrived (points, segment, tangents)",
                   spline.GetPointCount() == 6 and spline.GetSegment(0)["cnt"] == 6 and not spline.GetSegment(0)["closed"]
                   and tangent["vl"] == c4d.Vector(-3, 3, 0) and tangent["vr"] == c4d.Vector(3, -3, 0),
                   (spline.GetPointCount(), spline.GetSegment(0), tangent))
        self.signal(11)

        if not self.wait("host edited the mesh", lambda: self.peer_step() >= 12 and self.find("Mesh").GetPoint(300) == c4d.Vector(-5, -5, 9), 60):
            return
        mesh = self.find("Mesh")
        self.check("guest: point edit from host arrived", True)
        if self.wait("host's selection edit", lambda: selected_indices(tag_by_name(self.find("Mesh"), "PolySel"), 400) == [10, 11, 12, 200]):
            self.check("guest: selection tag edit from host arrived", True)
        if self.wait("host's UV edit", lambda: uv_of(tag_by_name(self.find("Mesh"), "UVs"), 299)[0] == (0.9, 0.8)):
            self.check("guest: UV edit from host arrived", True)
        spline = self.find("Curve")
        if self.wait("host's spline edits", lambda: spline.GetTangent(1)["vr"] == c4d.Vector(9, -9, 0) and spline.GetSegment(0)["closed"]):
            self.check("guest: spline tangent and closed flag arrived", True)

        mesh.SetPoint(5, c4d.Vector(1, 2, 3))
        mesh.Message(c4d.MSG_UPDATE)
        tag_by_name(mesh, "PolySel").GetBaseSelect().Select(100)
        c4d.EventAdd()
        self.signal(13)

        if not self.wait("host extruded", lambda: self.peer_step() >= 14 and self.peer_data().get("polys"), 60):
            return
        points, polys = self.peer_data()["points"], self.peer_data()["polys"]
        if self.wait("topology change arrived", lambda: self.find("Mesh").GetPointCount() == points
                     and self.find("Mesh").GetPolygonCount() == polys):
            self.check("guest: extrude (topology change) arrived", True)
        mesh = self.find("Mesh")
        uvw = tag_by_name(mesh, "UVs")
        self.check("guest: UVW tag follows the new polygon count", uvw.GetDataCount() == polys,
                   "%d vs %d" % (uvw.GetDataCount(), polys))
        self.check("guest: untouched UVs preserved", uv_of(uvw, 7) == expected_uv(7))
        self.signal(15)

        if not self.wait("host sent unknown types", lambda: self.peer_step() >= 16 and self.find("FromAPlugin") is not None, 60):
            return
        placeholder = self.find("FromAPlugin")
        self.check("guest: unknown object type became a harmless placeholder", placeholder.GetType() == c4d.Onull,
                   placeholder.GetType())
        self.check("guest: unknown tag type was skipped", placeholder.GetFirstTag() is None)
        placeholder.SetAbsPos(c4d.Vector(1, 2, 3))   # editing the placeholder must not touch the original
        self.pump(1.0)
        self.signal(17)
        self.wait("host finished modeling", lambda: self.peer_step() >= 18, 30)
        self.signal(19)

        # Phase 8: materials
        if not self.wait("host built a material", lambda: self.peer_step() >= 20 and material_named(self.doc, "Wood"), 60):
            return
        material = material_named(self.doc, "Wood")
        self.wait("material shader arrived", lambda: material.GetFirstShader() is not None
                  and material[c4d.MATERIAL_COLOR_SHADER] is not None, 20)
        shader = material.GetFirstShader()
        self.check("guest: material colour arrived", material[c4d.MATERIAL_COLOR_COLOR] == c4d.Vector(0.5, 0.3, 0.1))
        self.check("guest: bitmap shader arrived with its file", shader is not None and shader.GetType() == c4d.Xbitmap
                   and same_path(shader[c4d.BITMAPSHADER_FILENAME], TEXTURE_PATH))
        self.check("guest: the colour channel points at the guest's own shader",
                   material[c4d.MATERIAL_COLOR_SHADER] == shader)
        texture_tag = self.find("Cube").GetFirstTag()
        while texture_tag is not None and texture_tag.GetType() != c4d.Ttexture:
            texture_tag = texture_tag.GetNext()
        self.check("guest: the texture tag uses the guest's material",
                   texture_tag is not None and texture_tag[c4d.TEXTURETAG_MATERIAL] == material)
        self.signal(21)
        if not self.wait("host edited the material", lambda: self.peer_step() >= 22, 60):
            return
        self.wait("colour edit arrived", lambda: material[c4d.MATERIAL_COLOR_COLOR] == c4d.Vector(0.0, 1.0, 0.0), 20)
        self.check("guest: material colour edit arrived", material[c4d.MATERIAL_COLOR_COLOR] == c4d.Vector(0.0, 1.0, 0.0))
        self.wait("file edit arrived", lambda: same_path(shader[c4d.BITMAPSHADER_FILENAME], TEXTURE_PATH_2), 20)
        self.check("guest: shader file edit arrived", same_path(shader[c4d.BITMAPSHADER_FILENAME], TEXTURE_PATH_2))
        self.signal(23)

        # Phase 9: rig
        if not self.wait("host built a rig", lambda: self.peer_step() >= 24 and self.find("Body") is not None
                         and self.find("EmptyMesh") is not None, 60):
            return
        body = self.find("Body")
        self.wait("weight tag arrived", lambda: tag_of_type(body, c4d.Tweights) is not None
                  and tag_of_type(body, c4d.Tweights).GetJointCount() == 2, 20)
        weights = tag_of_type(body, c4d.Tweights)
        self.check("guest: the Weight tag has both joints", weights is not None and weights.GetJointCount() == 2,
                   None if weights is None else weights.GetJointCount())
        if weights is not None and weights.GetJointCount() == 2:
            names = [weights.GetJoint(i, self.doc).GetName() for i in range(2)]
            self.check("guest: joints are the guest's own objects, in order", names == ["Hips", "Spine"], names)
            first = [weights.GetWeight(0, point) for point in range(4)]
            second = [weights.GetWeight(1, point) for point in range(4)]
            self.check("guest: skin weights arrived (including the last point)",
                       abs(first[1] - 0.5) < 1e-3 and abs(second[2] - 0.75) < 1e-3 and abs(second[3] - 1.0) < 1e-3,
                       "%s %s" % (first, second))
        self.check("guest: the Skin deformer arrived", self.find("Skin") is not None and self.find("Skin").GetType() == c4d.Oskin)
        self.check("guest: an empty mesh arrived", self.find("EmptyMesh").GetPointCount() == 0)
        self.signal(25)

        # Phase 8: Pose Morph
        if not self.wait("host built a face", lambda: self.peer_step() >= 26 and self.find("Face") is not None
                         and tag_of_type(self.find("Face"), c4d.Tposemorph) is not None
                         and tag_of_type(self.find("Face"), c4d.Tposemorph).GetMorphCount() == 2, 60):
            return
        face = self.find("Face")
        pose = tag_of_type(face, c4d.Tposemorph)
        names = [pose.GetMorph(i).GetName() for i in range(pose.GetMorphCount())]
        self.check("guest: the pose morphs arrived", names[1:] == ["smile"], names)
        offsets = read_offsets(self.doc, pose, pose.GetMorph(1))
        self.check("guest: the smile's points arrived", offsets is not None and offsets[5] == c4d.Vector(0, 0, 50)
                   and offsets[100] == c4d.Vector(0, 2, 0) and offsets[6] == c4d.Vector(0, 0, 0),
                   None if offsets is None else (offsets[5], offsets[100]))
        self.check("guest: the strength arrived", abs(pose[pose.GetMorphID(1)] - 0.5) < 1e-6, pose[pose.GetMorphID(1)])
        self.check("guest: the tag animates (not in edit mode)", pose[c4d.ID_CA_POSE_MODE] == c4d.ID_CA_POSE_MODE_ANIMATE,
                   pose[c4d.ID_CA_POSE_MODE])
        evaluate(self.doc)
        self.check("guest: the face shows half a smile", abs(face.GetPoint(5).z - 25) < 1e-6, face.GetPoint(5))
        self.check("guest: the base is the host's base", abs(face.GetPoint(6).z) < 1e-6, face.GetPoint(6))
        self.signal(27)
        if not self.wait("host moved the slider", lambda: self.peer_step() >= 28
                         and abs(pose[pose.GetMorphID(1)] - 1.0) < 1e-6, 60):
            return
        evaluate(self.doc)
        self.check("guest: the slider move arrived and animates the face", abs(face.GetPoint(5).z - 50) < 1e-6,
                   face.GetPoint(5))
        self.signal(29)

        # Phase 8: this user opens another project and leaves the session
        if not self.wait("host ready for the leave test", lambda: self.peer_step() >= 30, 60):
            return
        self.free_active = True
        other = documents.BaseDocument()
        documents.InsertBaseDocument(other)
        documents.SetActiveDocument(other)
        self.pump(1.0)
        self.check("guest: opening another project left the session", not self.session.active)
        self.check("guest: it says why", any("You left the session: you opened another project" in line
                                           for line in self.session.log))
        self.check("guest: sync is detached", not self.sync.attached)


TEXTURE_PATH = "C:/textures/wood.png"
TEXTURE_PATH_2 = "C:/textures/oak.png"


def same_path(value, expected):
    return str(value).replace("\\", "/") == expected


def make_grid(doc, name, size):
    count = (size + 1) * (size + 1)
    mesh = c4d.PolygonObject(count, size * size)
    mesh.SetAllPoints([c4d.Vector(10 * (i % (size + 1)), 10 * (i // (size + 1)), 0) for i in range(count)])
    for y in range(size):
        for x in range(size):
            a = y * (size + 1) + x
            mesh.SetPolygon(y * size + x, c4d.CPolygon(a, a + 1, a + size + 2, a + size + 1))
    mesh.SetName(name)
    mesh.Message(c4d.MSG_UPDATE)
    doc.InsertObject(mesh)
    return mesh


def evaluate(doc):
    doc.ExecutePasses(None, True, True, True, getattr(c4d, "BUILDFLAGS_NONE", 0))


def set_offsets(doc, tag, morph, offsets):
    morph.SetMode(doc, tag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_EXPAND, c4d.CAMORPH_MODE_REL)
    node = morph.GetFirst()
    for index, value in offsets.items():
        node.SetPoint(index, value)
    morph.SetMode(doc, tag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_COLLAPSE, c4d.CAMORPH_MODE_AUTO)


def read_offsets(doc, tag, morph):
    if not morph.SetMode(doc, tag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_EXPAND, c4d.CAMORPH_MODE_REL):
        return None
    node = morph.GetFirst()
    values = [node.GetPoint(i) for i in range(node.GetPointCount())]
    morph.SetMode(doc, tag, c4d.CAMORPH_MODE_FLAGS_ALL | c4d.CAMORPH_MODE_FLAGS_COLLAPSE, c4d.CAMORPH_MODE_AUTO)
    return values


def tag_of_type(obj, ctype):
    tag = obj.GetFirstTag()
    while tag is not None:
        if tag.GetType() == ctype:
            return tag
        tag = tag.GetNext()
    return None


def material_named(doc, name):
    material = doc.GetFirstMaterial()
    while material is not None:
        if material.GetName() == name:
            return material
        material = material.GetNext()
    return None


def tag_by_name(obj, name):
    tag = obj.GetFirstTag() if obj is not None else None
    while tag is not None:
        if compat.from_c4d_str(tag.GetName()) == name:
            return tag
        tag = tag.GetNext()
    return None


def selected_indices(tag, count):
    select = tag.GetBaseSelect()
    return [i for i in range(count) if select.IsSelected(i)]


def uv_of(tag, index):
    corners = tag.GetSlow(index)
    return [(round(corners[k].x, 5), round(corners[k].y, 5)) for k in ("a", "b", "c", "d")]


def expected_uv(index):
    u, v = (index % 20) / 20.0, (index // 20) / 20.0
    f32 = lambda x: round(x, 5)
    return [(f32(u), f32(v)), (f32(u + 0.05), f32(v)), (f32(u + 0.05), f32(v + 0.05)), (f32(u), f32(v + 0.05))]


def pos_x_id():
    return c4d.DescID(c4d.DescLevel(c4d.ID_BASEOBJECT_REL_POSITION, c4d.DTYPE_VECTOR, 0),
                      c4d.DescLevel(c4d.VECTOR_X, c4d.DTYPE_REAL, 0))


def add_key(curve, frame, fps, value):
    added = curve.AddKey(c4d.BaseTime(frame, fps))
    key = added["key"] if isinstance(added, dict) else added
    key.SetValue(curve, value)
    return key


def key_list(track):
    if track is None:
        return None
    curve = track.GetCurve()
    return [(round(curve.GetKey(i).GetTime().Get(), 3), round(curve.GetKey(i).GetValue(), 3))
            for i in range(curve.GetKeyCount())]


LOCAL_ONLY = compat.type_ids("Opython") | frozenset([1023866])
TREE_TAGS = compat.type_ids("Tphong", "Ttargetexpression", "Tpython") | frozenset([1022749])


def run_scenario(role=None, port=None, results=None):
    role = role or os.environ.get("C4D_COLLAB_ROLE", "host")
    port = port or os.environ.get("C4D_COLLAB_PORT", "47911")
    results = results or os.environ.get("C4D_COLLAB_RESULTS", os.path.join(HERE, "result_%s.json" % role))
    scenario = Scenario(role, port, results)
    scenario.run()
    return scenario


if __name__ == "__main__":
    run_scenario()
