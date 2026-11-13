# -*- coding: utf-8 -*-
"""End-to-end scene sync: two fake Cinema 4D instances, one real server.

Runs on Python 2.7 and 3.x. Covers seeding, joining into a new document,
edits in both directions, hierarchy/order, deletes, geometry, links, echo
suppression and the security rules for code-carrying types.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import os
import socket
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "plugin", "c4d_collab")))

import fake_c4d  # noqa: E402
import fake_scene as fs  # noqa: E402

PASSWORD = "secret"


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class Instance(object):
    """One simulated Cinema 4D: session + scene sync + its active document."""

    def __init__(self, c4d, doc=None):
        from c4d_collab_lib.c4d_adapter.sync import SceneSync
        from c4d_collab_lib.core.session import CollabSession
        self.c4d = c4d
        self.session = CollabSession(client_info={"app": "test"})
        self.sync = SceneSync(self.session)
        self.doc = doc

    def activate(self):
        if self.doc is not None:
            self.c4d.scene_state.active = self.doc

    def pump(self):
        self.activate()
        self.session.pump()
        if self.doc is None and self.sync.doc is not None:
            self.doc = self.sync.doc  # guest: the snapshot was built into a new document
        self.activate()
        self.sync.tick()
        self.sync.advance(time.time())  # other users' transforms glide in real time


class SyncTestCase(unittest.TestCase):
    def setUp(self):
        fake_c4d.uninstall()
        self.c4d = fake_c4d.install(getattr(self, "profile", None))
        self.instances = []

    def tearDown(self):
        for instance in self.instances:
            instance.session.leave()
        fake_c4d.uninstall()

    # Helpers ---------------------------------------------------------------------------

    def host(self, doc):
        instance = Instance(self.c4d, doc)
        self.instances.append(instance)
        self.port = free_port()
        instance.activate()
        instance.session.host("Host", self.port, password=PASSWORD, bind="127.0.0.1")
        self.settle()
        return instance

    def join(self, name="Guest"):
        instance = Instance(self.c4d)
        self.instances.append(instance)
        instance.session.join("127.0.0.1", self.port, name, password=PASSWORD)
        self.settle()
        self.assertIsNotNone(instance.doc, "guest did not receive the scene")
        return instance

    def settle(self, rounds=8, timeout=5.0):
        """Pump everyone until nothing is pending for a few consecutive rounds."""
        deadline = time.time() + timeout
        quiet = 0
        while time.time() < deadline:
            for instance in self.instances:
                instance.pump()
            busy = any(i.session.mirror is not None and (i.session.mirror.pending_count or not i.session.mirror.synced)
                       for i in self.instances if i.session.active)
            quiet = 0 if busy else quiet + 1
            if quiet >= rounds:
                for instance in self.instances:
                    instance.sync.advance(time.time() + 10.0)  # finish any glide still running
                return
            time.sleep(0.01)
        raise AssertionError("session did not settle")

    def edit(self, instance, action):
        """Run a local edit with the instance's document active, then sync."""
        instance.activate()
        action()
        self.settle()

    @staticmethod
    def find(doc, name):
        return doc.SearchObject(name)

    def build_scene(self):
        doc = fs.BaseDocument()
        root = fs.BaseObject(fs.Onull)
        root.SetName(fs.native("Root"))
        doc.InsertObject(root)
        cube = fs.BaseObject(fs.Ocube)
        cube.SetName(fs.native("Cube"))
        doc.InsertObject(cube, root)
        cube[fs.PRIM_CUBE_LEN] = fs.Vector(300, 200, 100)
        cube[fs.REL_POSITION] = fs.Vector(10, 20, 30)
        phong = cube.MakeTag(fs.Tphong)
        phong.SetName(fs.native("Phong"))
        phong[fs.PHONG_ANGLE] = 0.5
        script = cube.MakeTag(fs.Tpython, phong)
        script.SetName(fs.native("Script"))
        script[1000] = str("import os; os.system('evil')")
        target = root.MakeTag(fs.Ttarget)
        target.SetName(fs.native("Target"))
        target[fs.TARGET_LINK] = cube
        generator = fs.BaseObject(fs.Opython)
        generator.SetName(fs.native("PyGen"))
        doc.InsertObject(generator, None, root)
        hidden_child = fs.BaseObject(fs.Onull)
        hidden_child.SetName(fs.native("UnderPyGen"))
        doc.InsertObject(hidden_child, generator)
        poly = fs.BaseObject(fs.Opolygon)
        poly.SetName(fs.native("Poly"))
        poly.ResizeObject(4, 1)
        poly.SetAllPoints([fs.Vector(0, 0, 0), fs.Vector(100, 0, 0), fs.Vector(100, 100, 0), fs.Vector(0, 100, 0)])
        poly.SetPolygon(0, fs.CPolygon(0, 1, 2, 3))
        doc.InsertObject(poly, None, generator)
        return doc

    def expected_guest_tree(self):
        return [
            ("Root", fs.Onull, [("Target", fs.Ttarget)],
             [("Cube", fs.Ocube, [("Phong", fs.Tphong)], [])]),
            ("Poly", fs.Opolygon, [], []),
        ]

    def assert_trees_equal(self, a, b):
        self.assertEqual(fs.walk(a.doc, shared_only=True), fs.walk(b.doc, shared_only=True))

    def assert_no_echo(self):
        seq = self.instances[0].session.server.scene.seq
        self.settle()
        self.assertEqual(self.instances[0].session.server.scene.seq, seq, "edits are echoing back and forth")


class ForwardLinkTest(SyncTestCase):
    """The guest's scene is built level by level, so a tag can point at an object that does not exist yet
    when the tag is built (an IK tag on the thigh naming the end joint, three levels deeper). Found in a real
    rig: the IK chain lost its end joint and the legs came out wrong."""

    def scene(self):
        doc = fs.BaseDocument()
        root = fs.BaseObject(fs.Onull)
        root.SetName(fs.native("Root"))
        doc.InsertObject(root)
        node = root
        for name in ("A", "B", "Deep"):
            child = fs.BaseObject(fs.Onull)
            child.SetName(fs.native(name))
            doc.InsertObject(child, node)
            node = child
        tag = root.MakeTag(fs.Ttarget)
        tag.SetName(fs.native("Target"))
        tag[fs.TARGET_LINK] = node
        return doc

    def test_a_link_to_a_deeper_object_arrives(self):
        self.host(self.scene())
        guest = self.join()
        deep = self.find(guest.doc, "Deep")
        self.assertIsNotNone(deep)
        self.assertIs(self.find(guest.doc, "Root").GetFirstTag()[fs.TARGET_LINK], deep,
                      "the link points at the guest's own deeper object")
        self.assert_no_echo()

    def test_a_link_set_later_to_a_new_deeper_object_arrives(self):
        host = self.host(self.scene())
        guest = self.join()
        root_a = self.find(host.doc, "Root")
        late = fs.BaseObject(fs.Onull)
        late.SetName(fs.native("Late"))

        self.edit(host, lambda: host.doc.InsertObject(late, self.find(host.doc, "B")))
        self.edit(host, lambda: root_a.GetFirstTag().__setitem__(fs.TARGET_LINK, late))
        self.assertIs(self.find(guest.doc, "Root").GetFirstTag()[fs.TARGET_LINK], self.find(guest.doc, "Late"))
        self.assert_no_echo()


class MeshDeformerTest(SyncTestCase):
    """Include/Exclude lists travel, and a Mesh deformer is initialised again on the guest: the bind data it
    computes from its cages is not a parameter, without it the guest's mesh stays where it was bound (the parts
    of the character floated around in the first live run)."""

    def cages(self, *objects):
        data = fs.InExcludeData()
        for obj in objects:
            data.InsertObject(obj, 1)
        return data

    def scene(self):
        doc = fs.BaseDocument()
        root = fs.BaseObject(fs.Onull)
        root.SetName(fs.native("Root"))
        doc.InsertObject(root)
        body = fs.BaseObject(fs.Opolygon)
        body.SetName(fs.native("Body"))
        doc.InsertObject(body, root)
        deformer = fs.BaseObject(fs.Omeshdeformer)
        deformer.SetName(fs.native("Deformer"))
        doc.InsertObject(deformer, body)
        parent = root
        for name in ("Rig", "A", "Cage", "Cage2"):   # the cages are deeper than the deformer
            child = fs.BaseObject(fs.Onull)
            child.SetName(fs.native(name))
            doc.InsertObject(child, parent)
            parent = child
        deformer[fs.MESHDEFORMER_CAGES] = self.cages(self.find_in(doc, "Cage"))
        return doc

    @staticmethod
    def find_in(doc, name):
        return doc.SearchObject(name)

    def members(self, instance):
        data = instance.doc.SearchObject("Deformer")[fs.MESHDEFORMER_CAGES]
        return [(data.ObjectFromIndex(instance.doc, i).GetName(), data.GetFlags(i)) for i in range(data.GetObjectCount())]

    def presses(self, instance):
        return getattr(instance.doc.SearchObject("Deformer"), "_pressed", [])

    def test_the_cages_arrive_as_the_guests_own_objects_and_the_deformer_is_initialised_once(self):
        host = self.host(self.scene())
        guest = self.join()
        self.assertEqual(self.members(guest), [("Cage", 1)])
        data = guest.doc.SearchObject("Deformer")[fs.MESHDEFORMER_CAGES]
        self.assertIs(data.ObjectFromIndex(guest.doc, 0), guest.doc.SearchObject("Cage"), "the guest's own cage")
        self.assertEqual(self.presses(guest), [(fs.MESHDEFORMER_INITIALIZE,)], "initialised once, after the cage existed")
        self.assertEqual(self.presses(host), [], "the host's own bind is not touched")
        self.assert_no_echo()

    def test_the_display_tags_initialize_puts_on_the_cages_are_removed(self):
        """Initialize gives each cage a Display tag (its own shading mode) and a Compositing tag; the host's cages have
        neither. Kept, the guest drew its cages as flat-coloured blocks over the textured body (live: R19 host, 2023
        guest)."""
        host = self.host(self.scene())
        guest = self.join()
        cage = guest.doc.SearchObject("Cage")
        types = []
        tag = cage.GetFirstTag()
        while tag is not None:
            types.append(tag.GetType())
            tag = tag.GetNext()
        self.assertNotIn(fs.TAG_DISPLAY, types)
        self.assertNotIn(fs.TAG_COMPOSITING, types)
        self.assertEqual(len(self.presses(guest)), 1)
        self.assertIsNotNone(host)
        self.assert_no_echo()

    def test_a_new_cage_is_followed_and_initialised_again(self):
        host = self.host(self.scene())
        guest = self.join()
        deformer = host.doc.SearchObject("Deformer")
        self.edit(host, lambda: deformer.__setitem__(fs.MESHDEFORMER_CAGES, self.cages(host.doc.SearchObject("Cage2"))))
        self.assertEqual(self.members(guest), [("Cage2", 1)])
        self.assertEqual(len(self.presses(guest)), 2)
        self.assert_no_echo()

    def test_other_changes_of_the_deformer_do_not_initialise_it_again(self):
        host = self.host(self.scene())
        guest = self.join()
        deformer = host.doc.SearchObject("Deformer")
        self.edit(host, lambda: deformer.__setitem__(1004, 0.5))
        self.assertEqual(guest.doc.SearchObject("Deformer")[1004], 0.5)
        self.assertEqual(len(self.presses(guest)), 1)

    def test_a_deformer_without_cages_is_not_initialised(self):
        doc = self.scene()
        doc.SearchObject("Deformer")[fs.MESHDEFORMER_CAGES] = self.cages()
        self.host(doc)
        guest = self.join()
        self.assertEqual(self.presses(guest), [])


class SeedAndJoinTest(SyncTestCase):
    def test_guest_receives_scene_without_code_objects(self):
        host = self.host(self.build_scene())
        guest = self.join()
        self.assertIsNot(guest.doc, host.doc)
        self.assertEqual(fs.walk(guest.doc), self.expected_guest_tree())

        cube = self.find(guest.doc, "Cube")
        self.assertEqual(cube[fs.PRIM_CUBE_LEN], fs.Vector(300, 200, 100))
        self.assertEqual(cube[fs.REL_POSITION], fs.Vector(10, 20, 30))
        self.assertEqual(cube.GetFirstTag()[fs.PHONG_ANGLE], 0.5)
        # The target tag's link points at the guest's own cube, not the host's.
        self.assertIs(self.find(guest.doc, "Root").GetFirstTag()[fs.TARGET_LINK], cube)
        poly = self.find(guest.doc, "Poly")
        self.assertEqual(poly.GetAllPoints()[2], fs.Vector(100, 100, 0))
        self.assertEqual([(p.a, p.b, p.c, p.d) for p in poly.GetAllPolygons()], [(0, 1, 2, 3)])
        self.assert_no_echo()

    def test_guest_document_is_new_and_users_scene_untouched(self):
        mine = self.c4d.scene_state.active
        mine.InsertObject(fs.BaseObject(fs.Onull))
        before = fs.walk(mine)
        host_doc = self.build_scene()
        host = self.host(host_doc)
        guest = self.join()
        self.assertEqual(fs.walk(mine), before)
        self.assertIs(self.c4d.scene_state.active, guest.doc)


class DedicatedServerTest(SyncTestCase):
    """A server without Cinema 4D (run_server.py, Docker): the first person to join shares their open scene,
    the next one joins it in a new document, the way Blender's Multi-User fills a waiting dedicated server."""

    def test_first_joiner_seeds_and_second_receives_the_scene(self):
        from c4d_collab_lib.core.server import SessionServer
        server = SessionServer(host="127.0.0.1", port=free_port(), password=PASSWORD, first_joiner_seeds=True)
        self.port = server.start()
        self.addCleanup(server.stop)
        mine = self.build_scene()
        first = Instance(self.c4d, mine)
        self.instances.append(first)
        first.activate()
        first.session.join("127.0.0.1", self.port, "First", password=PASSWORD)
        self.settle()
        self.assertTrue(first.session.seeds_scene)
        self.assertIs(first.sync.doc, mine)          # their own document became the session document
        self.assertGreater(server.scene.seq, 0)

        second = self.join("Second")
        self.assertIsNot(second.doc, mine)
        self.assertEqual(fs.walk(second.doc), self.expected_guest_tree())

        self.edit(second, lambda: self.find(second.doc, "Cube").__setitem__(fs.REL_POSITION, fs.Vector(1, 2, 3)))
        self.assertEqual(self.find(mine, "Cube")[fs.REL_POSITION], fs.Vector(1, 2, 3))

    def test_first_joiner_frame_leads(self):
        from c4d_collab_lib.core.server import SessionServer
        server = SessionServer(host="127.0.0.1", port=free_port(), password=PASSWORD, first_joiner_seeds=True)
        self.port = server.start()
        self.addCleanup(server.stop)
        mine = self.build_scene()
        mine.SetTime(fs.BaseTime(120, mine.GetFps()))
        first = Instance(self.c4d, mine)
        self.instances.append(first)
        first.activate()
        first.session.join("127.0.0.1", self.port, "First", password=PASSWORD)
        self.settle()
        second = self.join("Second")
        self.assertEqual(second.doc.GetTime().GetFrame(second.doc.GetFps()), 120)
        self.assertEqual(mine.GetTime().GetFrame(mine.GetFps()), 120)


class JoinFrameTest(SyncTestCase):
    """A rig is posed by its animation: the guest must evaluate it on the host's frame, or it is another pose."""

    def frame_of(self, instance):
        return instance.doc.GetTime().GetFrame(instance.doc.GetFps())

    def test_guest_starts_on_the_hosts_frame(self):
        doc = self.build_scene()
        doc.SetTime(fs.BaseTime(185, doc.GetFps()))
        host = self.host(doc)
        guest = self.join()
        self.assertEqual(self.frame_of(host), 185)
        self.assertEqual(self.frame_of(guest), 185)

    def test_late_guest_gets_the_frame_the_host_is_on_now(self):
        host = self.host(self.build_scene())
        host.activate()
        host.doc.SetTime(fs.BaseTime(40, host.doc.GetFps()))
        time.sleep(0.3)  # frame updates go out at most five times a second
        self.settle()
        guest = self.join()
        self.assertEqual(self.frame_of(guest), 40)

    def test_each_user_keeps_their_own_playhead_afterwards(self):
        host = self.host(self.build_scene())
        guest = self.join()
        guest.activate()
        guest.doc.SetTime(fs.BaseTime(10, guest.doc.GetFps()))
        host.activate()
        host.doc.SetTime(fs.BaseTime(50, host.doc.GetFps()))
        self.settle()
        self.assertEqual(self.frame_of(guest), 10)
        self.assertEqual(self.frame_of(host), 50)


class EditTest(SyncTestCase):
    def setUp(self):
        SyncTestCase.setUp(self)
        self.other = fs.BaseDocument()   # another project that was already open when the session started
        self.c4d.documents.InsertBaseDocument(self.other)
        self.a = self.host(self.build_scene())
        self.b = self.join()

    def test_transform_and_params_both_ways(self):
        cube_a = self.find(self.a.doc, "Cube")
        self.edit(self.a, lambda: cube_a.__setitem__(fs.REL_POSITION, fs.Vector(1, 2, 3)))
        cube_b = self.find(self.b.doc, "Cube")
        self.assertEqual(cube_b[fs.REL_POSITION], fs.Vector(1, 2, 3))

        self.edit(self.b, lambda: cube_b.__setitem__(fs.PRIM_CUBE_SUBX, 7))
        self.assertEqual(cube_a[fs.PRIM_CUBE_SUBX], 7)

        root_b = self.find(self.b.doc, "Root")
        self.edit(self.b, lambda: root_b.SetName(fs.native("Renamed ş")))
        self.assertIsNotNone(self.find(self.a.doc, "Renamed ş"))
        self.assert_no_echo()

    def test_create_reparent_reorder_delete(self):
        def add():
            extra = fs.BaseObject(fs.Onull)
            extra.SetName(fs.native("Extra"))
            self.b.doc.InsertObject(extra)  # first at root
        self.edit(self.b, add)
        self.assert_trees_equal(self.a, self.b)

        cube_a = self.find(self.a.doc, "Cube")
        poly_a = self.find(self.a.doc, "Poly")

        def reparent():
            cube_a.Remove()
            self.a.doc.InsertObject(cube_a, None, poly_a)  # root, after Poly
        self.edit(self.a, reparent)
        self.assert_trees_equal(self.a, self.b)
        self.assertIsNone(self.find(self.b.doc, "Cube").GetUp())

        def delete_root():
            self.find(self.a.doc, "Root").Remove()
        self.edit(self.a, delete_root)
        self.assert_trees_equal(self.a, self.b)
        self.assertIsNone(self.find(self.b.doc, "Root"))
        self.assert_no_echo()

    def test_tags_added_and_removed(self):
        cube_b = self.find(self.b.doc, "Cube")

        def add_tag():
            tag = cube_b.MakeTag(fs.Tphong)
            tag.SetName(fs.native("Phong2"))
        self.edit(self.b, add_tag)
        tags_a = [t.GetName() for t in self.find(self.a.doc, "Cube")._tags]
        self.assertIn("Phong2", tags_a)

        self.edit(self.b, lambda: cube_b.GetFirstTag().Remove())
        self.assertEqual(fs.walk(self.b.doc)[0][3][0][2], [("Phong", fs.Tphong)])
        self.assert_trees_equal(self.a, self.b)

    def test_geometry_edit(self):
        poly_b = self.find(self.b.doc, "Poly")
        self.edit(self.b, lambda: poly_b.SetPoint(0, fs.Vector(-5, -5, 0)))
        self.assertEqual(self.find(self.a.doc, "Poly").GetAllPoints()[0], fs.Vector(-5, -5, 0))

    def test_switching_document_pauses_sync(self):
        cube_b = self.find(self.b.doc, "Cube")
        self.c4d.scene_state.active = self.other
        cube_b[fs.PRIM_CUBE_SUBX] = 3  # edit while the session doc is not active
        self.b.sync.tick()
        self.assertTrue(any("paused" in line for line in self.b.session.log))
        self.settle()  # pump() re-activates the session doc -> the edit goes out
        self.assertEqual(self.find(self.a.doc, "Cube")[fs.PRIM_CUBE_SUBX], 3)
        self.assertTrue(any("resumed" in line for line in self.b.session.log))

    def test_late_joiner_sees_current_state(self):
        cube_a = self.find(self.a.doc, "Cube")
        self.edit(self.a, lambda: cube_a.__setitem__(fs.PRIM_CUBE_SUBX, 9))
        c = self.join("Late")
        self.assertEqual(self.find(c.doc, "Cube")[fs.PRIM_CUBE_SUBX], 9)
        self.assertEqual(fs.walk(c.doc), fs.walk(self.b.doc))


class LeaveOnOtherProjectTest(SyncTestCase):
    """Opening another project ends a user's part in the session; a quick look at another tab does not."""

    def setUp(self):
        SyncTestCase.setUp(self)
        from c4d_collab_lib.c4d_adapter import sync as sync_module
        self.sync_module = sync_module
        self.old_grace = sync_module.SWITCH_GRACE
        self.tab = fs.BaseDocument()   # a project that was already open before the session
        self.c4d.documents.InsertBaseDocument(self.tab)
        self.a = self.host(self.build_scene())
        self.b = self.join()

    def tearDown(self):
        self.sync_module.SWITCH_GRACE = self.old_grace
        SyncTestCase.tearDown(self)

    def open_project(self):
        """File > Open / File > New: a new document that becomes the active one."""
        project = fs.BaseDocument()
        self.c4d.documents.InsertBaseDocument(project)
        self.c4d.scene_state.active = project
        return project

    def users_seen_by(self, instance, count, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline and len(instance.session.users()) != count:
            time.sleep(0.03)
            instance.pump()
        return len(instance.session.users())

    def test_a_guest_who_opens_another_project_leaves(self):
        self.assertEqual(len(self.a.session.users()), 2)
        self.open_project()
        self.b.sync.tick()
        self.assertFalse(self.b.session.active, "the guest is out of the session")
        self.assertFalse(self.b.sync.attached)
        self.assertTrue(any("You left the session: you opened another project" in l for l in self.b.session.log))
        self.assertEqual(self.users_seen_by(self.a, 1), 1, "the others see the guest go")
        self.assertTrue(self.a.session.active, "the host's session goes on")

    def test_a_host_who_opens_another_project_ends_the_session(self):
        self.open_project()
        self.a.sync.tick()
        self.assertFalse(self.a.session.active)
        self.assertIsNone(self.a.session.server, "the server is stopped")
        self.assertTrue(any("The session ended: you opened another project" in l for l in self.a.session.log))
        deadline = time.time() + 3
        while time.time() < deadline and self.b.session.active:
            time.sleep(0.03)
            self.b.pump()
        self.assertFalse(self.b.session.active, "the guests are told and leave too")

    def test_a_tab_that_was_open_before_only_pauses_and_resumes(self):
        self.c4d.scene_state.active = self.tab
        self.b.sync.tick()
        self.assertTrue(self.b.session.active)
        self.assertTrue(any("paused" in l and "within 10 s" in l for l in self.b.session.log))
        self.c4d.scene_state.active = self.b.doc
        self.b.sync.tick()
        self.assertTrue(self.b.session.active)
        self.assertTrue(any("resumed" in l for l in self.b.session.log))

    def test_staying_in_that_other_tab_too_long_leaves(self):
        self.sync_module.SWITCH_GRACE = 0.2
        self.c4d.scene_state.active = self.tab
        self.b.sync.tick()
        self.assertTrue(self.b.session.active, "not yet")
        time.sleep(0.3)
        self.b.sync.tick()
        self.assertFalse(self.b.session.active)
        self.assertTrue(any("you stayed in another project" in l for l in self.b.session.log))

    def test_coming_back_in_time_starts_the_clock_over(self):
        self.sync_module.SWITCH_GRACE = 0.3
        self.c4d.scene_state.active = self.tab
        self.b.sync.tick()
        time.sleep(0.2)
        self.c4d.scene_state.active = self.b.doc
        self.b.sync.tick()
        self.c4d.scene_state.active = self.tab
        self.b.sync.tick()
        time.sleep(0.2)
        self.b.sync.tick()
        self.assertTrue(self.b.session.active, "0.2 s twice is not 0.3 s in a row")

    def test_closing_the_session_document_leaves(self):
        self.c4d.documents.state.KillDocument(self.b.doc)
        self.b.sync.tick()
        self.assertFalse(self.b.session.active)
        self.assertTrue(any("the session document was closed" in l for l in self.b.session.log))

    def test_one_can_join_again_after_leaving(self):
        self.open_project()
        self.b.sync.tick()
        self.assertFalse(self.b.session.active)
        self.b.doc = None
        self.b.session.join("127.0.0.1", self.port, "Guest", password=PASSWORD)
        self.settle()
        self.assertIsNotNone(self.b.doc)
        self.assertTrue(self.b.session.active)
        self.assertEqual(len(self.a.session.users()), 2)
        self.assertIsNotNone(self.find(self.b.doc, "Cube"), "the scene came again")

    def test_nobody_else_is_affected(self):
        c = self.join("Third")
        self.open_project()
        self.b.sync.tick()
        self.assertEqual(self.users_seen_by(self.a, 2), 2, "host and the third user remain")
        self.assertTrue(c.session.active)
        self.assertTrue(self.a.session.active)


class SecurityTest(SyncTestCase):
    def test_remote_code_types_are_never_created(self):
        host = self.host(fs.BaseDocument())
        guest = self.join()
        evil = [
            {"op": "create", "uuid": "a" * 32, "kind": "object", "ctype": fs.Opython, "parent": None,
             "prev": None, "name": "Evil", "data": {"p": {"1000:130:0": "import os"}}},
            {"op": "create", "uuid": "b" * 32, "kind": "object", "ctype": fs.Onull, "parent": None,
             "prev": "a" * 32, "name": "Holder", "data": {}},
            {"op": "create", "uuid": "c" * 32, "kind": "tag", "ctype": fs.Tpython, "parent": "b" * 32,
             "prev": None, "name": "EvilTag", "data": {"p": {"1000:130:0": "import os"}}},
        ]
        host.session.send_ops(evil)  # a malicious peer could send these by hand
        self.settle()
        evil_obj = self.find(guest.doc, "Evil")
        self.assertIsNotNone(evil_obj, "hierarchy is kept with a placeholder")
        self.assertEqual(evil_obj.GetType(), fs.Onull)
        self.assertEqual(self.find(guest.doc, "Holder")._tags, [])
        self.assert_no_echo()

    def test_code_types_are_recognised_by_name_when_id_is_unknown(self):
        # R19 lacks c4d.Opython; COFFEE ids are not in our list. The type name still blocks them.
        doc = fs.BaseDocument()
        holder = fs.BaseObject(fs.Onull)
        holder.SetName(fs.native("Holder"))
        doc.InsertObject(holder)
        holder.MakeTag(fs.Tcoffee_unknown).SetName(fs.native("CoffeeTag"))
        effector = fs.BaseObject(fs.Ocoffee_unknown)
        effector.SetName(fs.native("CoffeeEffector"))
        doc.InsertObject(effector, None, holder)
        host = self.host(doc)
        guest = self.join()
        self.assertEqual(fs.walk(guest.doc), [("Holder", fs.Onull, [], [])])
        # ...and if a peer sends them anyway, they are not created.
        host.session.send_ops([
            {"op": "create", "uuid": "e" * 32, "kind": "object", "ctype": fs.Ocoffee_unknown, "parent": None,
             "prev": None, "name": "Sneaky", "data": {}},
            {"op": "create", "uuid": "9" * 32, "kind": "tag", "ctype": fs.Tcoffee_unknown,
             "parent": host.session.server.scene.children(None)[0].uuid, "prev": None, "name": "SneakyTag", "data": {}},
        ])
        self.settle()
        self.assertEqual(self.find(guest.doc, "Sneaky").GetType(), fs.Onull)
        self.assertEqual(self.find(guest.doc, "Holder")._tags, [])

    def test_unknown_type_from_newer_version_gets_placeholder(self):
        host = self.host(fs.BaseDocument())
        guest = self.join()
        host.session.send_ops([{"op": "create", "uuid": "f" * 32, "kind": "object", "ctype": fs.Ofuture,
                                "parent": None, "prev": None, "name": "FromTheFuture", "data": {"p": {}}}])
        self.settle()
        placeholder = self.find(guest.doc, "FromTheFuture")
        self.assertEqual(placeholder.GetType(), fs.Onull)
        # Editing the placeholder never overwrites the original on other machines.
        self.edit(guest, lambda: placeholder.__setitem__(fs.REL_POSITION, fs.Vector(5, 5, 5)))
        self.assertEqual(host.session.server.scene.get("f" * 32).ctype, fs.Ofuture)
        self.assertEqual(host.session.server.scene.get("f" * 32).data, {"p": {}})

    def test_bad_geometry_is_rejected(self):
        host = self.host(fs.BaseDocument())
        guest = self.join()
        from c4d_collab_lib.core import protocol
        bad = {"n": [3, 1], "pts": protocol.pack_floats([0.0] * 9), "polys": protocol.pack_ints([0, 1, 2, 99])}
        host.session.send_ops([{"op": "create", "uuid": "d" * 32, "kind": "object", "ctype": fs.Opolygon,
                                "parent": None, "prev": None, "name": "Bad", "data": {"p": {}, "g": bad}}])
        self.settle()
        self.assertEqual(self.find(guest.doc, "Bad").GetAllPolygons(), [])




class PatchKeysTest(unittest.TestCase):
    """The log line of an edit names the parameters it touches (traces a rig or texture change to what was sent)."""

    def test_nested_parameter_keys(self):
        fake_c4d.install(None)
        from c4d_collab_lib.c4d_adapter.sync import patch_keys
        patch = {"sub": {"p": {"sub": {"903:23:5155": {"set": {"V": [1.0, 2.0, 3.0]}}, "898:133:110050": {"set": "MESH"}}},
                         "pts": {"set": "abc"}}}
        self.assertEqual(patch_keys(patch), ["p.898:133:110050", "p.903:23:5155", "pts"])
        self.assertEqual(patch_keys(None), [])
        self.assertEqual(patch_keys({"set": 1}), [])


if __name__ == "__main__":
    unittest.main()


class RigNoiseTest(SyncTestCase):
    """Both sides evaluate the same rig, and an evaluated rig settles within a hair: that is not an edit, or the two
    send each other's noise back and forth and a locked joint is "undone" over and over (seen live)."""

    def build_joint(self):
        doc = self.build_scene()
        joint = fs.BaseObject(fs.Ojoint)
        joint.SetName(fs.native("Knee"))
        doc.InsertObject(joint)
        return doc

    def test_a_hair_of_difference_on_a_joint_is_not_sent_but_a_real_move_is(self):
        host = self.host(self.build_joint())
        guest = self.join()
        joint = self.find(guest.doc, "Knee")
        self.edit(guest, lambda: joint.__setitem__(fs.REL_ROTATION, fs.Vector(0.0, 0.02, 0.0)))
        self.assertEqual(self.find(host.doc, "Knee")[fs.REL_ROTATION], fs.Vector(0, 0, 0), "noise went out")
        self.edit(guest, lambda: joint.__setitem__(fs.REL_ROTATION, fs.Vector(0.0, 0.5, 0.0)))
        self.assertEqual(self.find(host.doc, "Knee")[fs.REL_ROTATION], fs.Vector(0.0, 0.5, 0.0))

    def test_an_edit_of_an_ordinary_object_is_still_sent_when_small(self):
        host = self.host(self.build_joint())
        guest = self.join()
        cube = self.find(guest.doc, "Cube")
        self.edit(guest, lambda: cube.__setitem__(fs.REL_POSITION, fs.Vector(10.5, 20, 30)))
        self.assertEqual(self.find(host.doc, "Cube")[fs.REL_POSITION], fs.Vector(10.5, 20, 30))

    def test_skin_weights_that_differ_by_rounding_are_the_same_skin(self):
        from c4d_collab_lib.c4d_adapter.serializers import rig
        from c4d_collab_lib.core import protocol
        base = {"j": ["a"], "r": {"a": [0.0] * 49}, "w": {"a": protocol.pack_ints([0, 100, 65535])}}
        rounded = {"j": ["a"], "r": {"a": [0.0] * 49}, "w": {"a": protocol.pack_ints([0, 101, 65534])}}
        moved = {"j": ["a"], "r": {"a": [0.0] * 49}, "w": {"a": protocol.pack_ints([0, 200, 65535])}}
        self.assertTrue(rig.same_weights(base, rounded))
        self.assertFalse(rig.same_weights(base, moved))
        self.assertFalse(rig.same_weights(base, {"j": ["b"], "r": {}, "w": {}}))
