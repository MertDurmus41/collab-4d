# -*- coding: utf-8 -*-
"""Smooth motion and lighter updates: object glides, camera send rate, repaint vs re-evaluate,
panel caching. Python 2.7 and 3.x."""
from __future__ import absolute_import, division, print_function, unicode_literals

import math
import time
import unittest

import fake_c4d
import fake_scene as fs
from test_sync import SyncTestCase


class RememberRecorder(object):
    def __init__(self):
        self.calls = []

    def remember(self, uuid, atom):
        self.calls.append(uuid)


class TweenTest(unittest.TestCase):
    def setUp(self):
        fake_c4d.uninstall()
        self.c4d = fake_c4d.install()
        from c4d_collab_lib.c4d_adapter import tween
        from c4d_collab_lib.c4d_adapter.serializers import params
        self.tween, self.params = tween, params
        self.tracker = RememberRecorder()
        self.tweens = tween.TransformTweens(self.tracker)
        self.atom = fs.BaseObject(fs.Onull)
        self.key = "%d:%d:0" % (fs.REL_POSITION, fs.DTYPE_VECTOR)
        self.rot_key = "%d:%d:0" % (fs.REL_ROTATION, fs.DTYPE_VECTOR)

    def tearDown(self):
        fake_c4d.uninstall()

    def data(self, key, x, y=0.0, z=0.0):
        return {key: {"V": [x, y, z]}}

    def x(self):
        return self.atom[fs.REL_POSITION].x

    def test_glides_smoothly_and_ends_exactly_on_the_target(self):
        data = self.data(self.key, 100.0)
        self.tweens.start("n1", self.atom, [self.key], data, 0.0)
        shown = []
        now = 0.0
        while self.tweens.step(now):
            shown.append(self.x())
            now += 0.01
        shown.append(self.x())
        self.assertGreater(len(shown), 3, "spread over several frames")
        self.assertTrue(all(a <= b for a, b in zip(shown, shown[1:])), "monotonic: no overshoot")
        self.assertEqual(shown[-1], 100.0)
        self.assertEqual(len(self.tweens), 0)
        self.assertGreaterEqual(len(self.tracker.calls), len(shown) - 1, "every step is remembered (no echo)")

    def test_steady_updates_move_at_constant_speed(self):
        """Updates every 50 ms, 10 cm each: the shown speed must not dip to zero between updates."""
        positions = []
        now = 0.0
        for index in range(1, 13):
            self.tweens.start("n1", self.atom, [self.key], self.data(self.key, 10.0 * index), now)
            for _ in range(5):  # ten animation frames per update interval
                now += 0.01
                self.tweens.step(now)
                positions.append(self.x())
        speeds = [b - a for a, b in zip(positions[10:-10], positions[11:-9])]
        self.assertGreater(min(speeds), 0.0, "never stands still")
        self.assertLess(max(speeds) - min(speeds), 0.2 * max(speeds), "speed stays about constant")

    def test_delay_follows_the_update_rhythm(self):
        self.tweens.start("n1", self.atom, [self.key], self.data(self.key, 10.0), 1.0)
        first = self.tweens._active[("n1", self.key)]["segments"][-1]
        self.assertAlmostEqual(first[1] - first[0], self.tween.DEFAULT_DELAY)
        self.tweens.start("n1", self.atom, [self.key], self.data(self.key, 20.0), 1.05)
        last = self.tweens._active[("n1", self.key)]["segments"][-1]
        self.assertAlmostEqual(last[1] - 1.05, 0.0608, delta=0.005)  # about 1.25 intervals late, rhythm smoothed
        self.tweens.start("n1", self.atom, [self.key], self.data(self.key, 30.0), 3.0)  # after a pause
        last = self.tweens._active[("n1", self.key)]["segments"][-1]
        self.assertAlmostEqual(last[1] - last[0], self.tween.DEFAULT_DELAY)

    def test_retargeting_continues_without_a_jump(self):
        self.tweens.start("n1", self.atom, [self.key], self.data(self.key, 100.0), 0.0)
        self.tweens.step(0.03)
        midway = self.x()
        self.assertTrue(0 < midway < 100)
        self.tweens.start("n1", self.atom, [self.key], self.data(self.key, 0.0), 0.03)
        self.tweens.step(0.0301)
        self.assertAlmostEqual(self.x(), midway, delta=1.0)

    def test_rotation_takes_the_short_way_round(self):
        self.atom[fs.REL_ROTATION] = fs.Vector(math.pi - 0.1, 0, 0)
        self.tweens.start("n1", self.atom, [self.rot_key], self.data(self.rot_key, -math.pi + 0.1), 0.0)
        self.tweens.step(0.02)
        heading = self.atom[fs.REL_ROTATION].x
        self.assertGreater(heading, math.pi - 0.1, "keeps turning forward through +-180 degrees, not back through 0")
        self.tweens.step(10.0)
        self.assertAlmostEqual(self.atom[fs.REL_ROTATION].x, -math.pi + 0.1)

    def test_cancel_clear_and_dead_atoms(self):
        self.tweens.start("n1", self.atom, [self.key], self.data(self.key, 100.0), 0.0)
        self.tweens.cancel("n1")
        self.assertEqual(len(self.tweens), 0)
        self.tweens.start("n2", self.atom, [self.key], self.data(self.key, 5.0), 0.0)
        self.tweens.clear()
        self.assertFalse(self.tweens.step(0.5))

        class Dead(object):
            def GetParameter(self, *args):
                raise ReferenceError("gone")

            def SetParameter(self, *args):
                raise ReferenceError("gone")
        self.tweens.start("n3", Dead(), [self.key], self.data(self.key, 5.0), 0.0)
        self.assertFalse(self.tweens.step(0.01), "a vanished object ends its glide instead of raising")

    def test_only_plain_transform_updates_may_glide(self):
        glidable = self.tween.glidable_keys
        position = self.data(self.key, 1.0)
        self.assertEqual(glidable([self.key], position), [self.key])
        other = "%d:%d:0" % (fs.PRIM_CUBE_LEN, fs.DTYPE_VECTOR)
        self.assertIsNone(glidable([other], self.data(other, 1.0)), "object parameters apply at once")
        self.assertIsNone(glidable([self.key], {self.key: {"V": [None, 2.0, 3.0]}}), "animated components")
        self.assertIsNone(glidable([self.key + "/1:2:3"], position))
        self.assertIsNone(glidable([], position))
        self.assertIsNone(glidable(["garbage"], {}))


class GlideInSessionTest(SyncTestCase):
    def setUp(self):
        SyncTestCase.setUp(self)
        self.a = self.host(self.build_scene())
        self.b = self.join()

    def test_remote_move_glides_and_does_not_echo(self):
        cube_a, cube_b = self.find(self.a.doc, "Cube"), self.find(self.b.doc, "Cube")
        start = cube_b[fs.REL_POSITION].x
        seq = self.a.session.server.scene.seq
        self.a.activate()
        cube_a[fs.REL_POSITION] = fs.Vector(start + 500, 20, 30)
        self.a.sync.tick()
        deadline = time.time() + 5
        while not len(self.b.sync.applier.tweens) and time.time() < deadline:
            self.b.activate()
            self.b.session.pump()  # receive, but do not advance the glide
            time.sleep(0.01)
        self.assertEqual(len(self.b.sync.applier.tweens), 1, "the move is gliding, not applied at once")
        self.assertEqual(cube_b[fs.REL_POSITION].x, start)
        self.b.sync.advance(time.time() + 0.03)
        self.assertTrue(start <= cube_b[fs.REL_POSITION].x < start + 500)
        self.b.sync.advance(time.time() + 10.0)
        self.assertEqual(cube_b[fs.REL_POSITION], fs.Vector(start + 500, 20, 30))
        self.settle()
        self.assertEqual(self.a.session.server.scene.seq, seq + 1, "exactly the one original update, no echo")

    def test_a_delete_during_a_glide_is_safe(self):
        cube_a = self.find(self.a.doc, "Cube")
        self.a.activate()
        cube_a[fs.REL_POSITION] = fs.Vector(900, 0, 0)
        self.a.sync.tick()
        deadline = time.time() + 5
        while not len(self.b.sync.applier.tweens) and time.time() < deadline:
            self.b.activate()
            self.b.session.pump()
            time.sleep(0.01)
        self.edit(self.a, lambda: cube_a.Remove())
        self.assertEqual(len(self.b.sync.applier.tweens), 0)
        self.assertIsNone(self.find(self.b.doc, "Cube"))


class CameraRateTest(SyncTestCase):
    def setUp(self):
        SyncTestCase.setUp(self)
        from c4d_collab_lib.c4d_adapter import sync as sync_module
        self.sync_module = sync_module
        self.a = self.host(self.build_scene())
        self.sent = []
        original = self.a.session.set_presence

        def spy(**fields):
            if "cam" in fields:
                self.sent.append(fields["cam"])
            original(**fields)
        self.a.session.set_presence = spy

    def look(self, x, y=0.0, z=0.0):
        self.a.doc.camera_matrix = fs.Matrix(fs.Vector(x, y, z))

    def test_updates_are_frequent_but_limited_and_skip_tiny_moves(self):
        self.look(0.0)
        self.a.sync.tick()
        self.assertEqual(len(self.sent), 1)
        self.look(50.0)
        self.a.sync.tick()  # too soon after the last one
        self.assertEqual(len(self.sent), 1, "limited to the send interval")
        time.sleep(self.sync_module.CAMERA_INTERVAL + 0.02)
        self.a.sync.tick()
        self.assertEqual(len(self.sent), 2, "20 per second are allowed (it was 5)")
        self.look(50.1)
        time.sleep(self.sync_module.CAMERA_INTERVAL + 0.02)
        self.a.sync.tick()
        self.assertEqual(len(self.sent), 2, "a 1 mm wobble is not worth a message")
        self.look(52.0)
        time.sleep(self.sync_module.CAMERA_INTERVAL + 0.02)
        self.a.sync.tick()
        self.assertEqual(len(self.sent), 3)

    def test_send_interval_is_at_most_a_twentieth_of_a_second_and_dead_band_is_small(self):
        self.assertLessEqual(self.sync_module.CAMERA_INTERVAL, 0.05)
        self.assertLessEqual(self.sync_module.CAMERA_POSITION_STEP, 1.0)


class CameraEasingInSessionTest(SyncTestCase):
    def setUp(self):
        SyncTestCase.setUp(self)
        from c4d_collab_lib.c4d_adapter import presence, tracker
        self.presence = presence
        self._interval = tracker.TRACK_SCAN_INTERVAL
        self.tracker_module = tracker
        tracker.TRACK_SCAN_INTERVAL = 0.05
        self.a = self.host(self.build_scene())
        self.b = self.join()
        presence.reset_smoothing()

    def tearDown(self):
        self.tracker_module.TRACK_SCAN_INTERVAL = self._interval
        SyncTestCase.tearDown(self)

    def eye_x(self):
        content = self.presence.current_overlay(self.b.doc, (0.0, 0.0, -500.0), self.b.session, self.b.sync)
        return content.lines[0][0][0]

    def test_remote_camera_eases_to_its_new_pose(self):
        self.a.doc.camera_matrix = fs.Matrix(fs.Vector(0, 0, 0))
        for _ in range(3):
            time.sleep(0.06)
            self.settle()
        self.assertAlmostEqual(self.eye_x(), 0.0, places=1)
        self.a.doc.camera_matrix = fs.Matrix(fs.Vector(200, 0, 0))
        for _ in range(3):
            time.sleep(0.06)
            self.settle()
        first = self.eye_x()
        self.assertTrue(0.0 < first < 200.0, "it is on its way, not teleported (%.1f)" % first)
        clock = time.time() + 5.0
        for _ in range(60):
            clock += 0.1
            self.presence.advance(self.b.session, clock)
        self.assertAlmostEqual(self.eye_x(), 200.0, places=1)

    def test_animation_step_reports_whether_it_is_still_moving(self):
        self.a.doc.camera_matrix = fs.Matrix(fs.Vector(0, 0, 0))
        for _ in range(3):
            time.sleep(0.06)
            self.settle()
        self.presence.advance(self.b.session, time.time())
        self.a.doc.camera_matrix = fs.Matrix(fs.Vector(300, 0, 0))
        for _ in range(3):
            time.sleep(0.06)
            self.settle()
        clock = time.time() + 0.01
        self.assertTrue(self.presence.advance(self.b.session, clock))
        for _ in range(40):
            clock += 0.1
            self.presence.advance(self.b.session, clock)
        self.assertFalse(self.presence.advance(self.b.session, clock + 0.1))


class RepaintTest(unittest.TestCase):
    """Presence-only events repaint the views; anything that changes the scene re-evaluates it."""

    class FakeSession(object):
        active = True
        mirror = None

        def __init__(self, kinds):
            self.last_kinds = kinds

        def pump(self):
            return 1

    def setUp(self):
        fake_c4d.uninstall()
        self.c4d = fake_c4d.install()
        self.c4d.draw_views = []
        self.c4d.DrawViews = lambda flags=0: self.c4d.draw_views.append(flags)
        from c4d_collab_lib.c4d_adapter import bridge
        self.bridge = bridge
        self._saved = (bridge._SESSION, bridge._SYNC)
        bridge._SYNC = None

    def tearDown(self):
        self.bridge._SESSION, self.bridge._SYNC = self._saved
        fake_c4d.uninstall()

    def test_presence_only_events_repaint_without_re_evaluating_the_scene(self):
        self.bridge._SESSION = self.FakeSession({"presence", "locks"})
        before = len(self.c4d.event_adds)
        self.bridge.pump()
        self.assertEqual(len(self.c4d.draw_views), 1)
        self.assertEqual(len(self.c4d.event_adds), before)

    def test_scene_changes_re_evaluate_the_scene(self):
        self.bridge._SESSION = self.FakeSession({"presence", "remote_ops"})
        before = len(self.c4d.event_adds)
        self.bridge.pump()
        self.assertEqual(len(self.c4d.event_adds), before + 1)
        self.assertEqual(self.c4d.draw_views, [])

    def test_an_idle_pump_does_nothing_expensive(self):
        class Idle(self.FakeSession):
            def pump(self):
                return 0
        self.bridge._SESSION = Idle(set())
        before = len(self.c4d.event_adds)
        self.bridge.pump()
        self.assertEqual((len(self.c4d.draw_views), len(self.c4d.event_adds)), (0, before))


class PanelCostTest(unittest.TestCase):
    def setUp(self):
        fake_c4d.uninstall()
        self.c4d = fake_c4d.install()
        from c4d_collab_lib.c4d_adapter import ui_dialog
        self.ui = ui_dialog
        ui_dialog._address_cache["at"] = None

    def tearDown(self):
        fake_c4d.uninstall()

    def test_address_lookup_is_cached(self):
        import socket
        calls = []
        original = socket.gethostbyname_ex
        socket.gethostbyname_ex = lambda name: calls.append(name) or (name, [], ["192.168.1.5", "127.0.0.1"])
        try:
            first = self.ui.local_addresses()
            second = self.ui.local_addresses()
        finally:
            socket.gethostbyname_ex = original
        self.assertEqual((first, second), (["192.168.1.5"], ["192.168.1.5"]))
        self.assertEqual(len(calls), 1, "a DNS lookup on every refresh could stall the interface")

    def fake_network(self, lan, routes):
        """lan: what gethostbyname_ex lists; routes: {probe address: our address on that route}."""
        import socket
        ui = self.ui
        original = (socket.gethostbyname_ex, ui._route_source)
        socket.gethostbyname_ex = lambda name: (name, [], list(lan) + ["127.0.0.1"])
        ui._route_source = lambda target: routes.get(target)

        def restore():
            socket.gethostbyname_ex, ui._route_source = original
            ui._address_cache["at"] = None
        self.addCleanup(restore)
        ui._address_cache["at"] = None

    def test_tailscale_range(self):
        ui = self.ui
        self.assertTrue(ui.in_tailscale_range("100.64.0.1"))
        self.assertTrue(ui.in_tailscale_range("100.127.255.254"))
        for address in ("100.63.0.1", "100.128.0.1", "10.0.0.1", "192.168.1.5", "", None, "100.x.1.1", "100.64.1"):
            self.assertFalse(ui.in_tailscale_range(address), address)

    def test_tailscale_address_is_shown_apart_from_the_local_ones(self):
        self.fake_network(["192.168.1.5", "100.101.1.2"],
                          {"100.100.100.100": "100.101.1.2", "8.8.8.8": "192.168.1.5"})
        self.assertEqual(self.ui.tailscale_address(), "100.101.1.2")
        self.assertEqual(self.ui.local_addresses(), ["192.168.1.5"])
        text = self.ui.share_text(47800)
        self.assertIn("Tailscale 100.101.1.2 (any home)", text)
        self.assertIn("192.168.1.5 (same network)", text)
        self.assertIn("47800", text)
        self.assertNotIn("see README", text)

    def test_without_tailscale_the_line_points_to_it(self):
        self.fake_network(["192.168.1.5"], {"100.100.100.100": "192.168.1.5", "8.8.8.8": "192.168.1.5"})
        self.assertIsNone(self.ui.tailscale_address())
        text = self.ui.share_text(47800)
        self.assertIn("192.168.1.5 (same network)", text)
        self.assertIn("other homes: Tailscale, see README", text)

    def test_a_providers_100_64_address_is_not_taken_for_tailscale(self):
        """Some providers give the computer itself an address from the same block (carrier-grade NAT): then every
        route leaves from it, which Tailscale never does."""
        self.fake_network(["100.70.3.4"], {"100.100.100.100": "100.70.3.4", "8.8.8.8": "100.70.3.4"})
        self.assertIsNone(self.ui.tailscale_address())
        self.assertEqual(self.ui.local_addresses(), ["100.70.3.4"])

    def test_no_network_at_all(self):
        self.fake_network([], {})
        self.assertIsNone(self.ui.tailscale_address())
        self.assertIn("your IP address", self.ui.share_text(47800))

    def test_route_probe_sends_nothing_and_never_raises(self):
        self.assertIsNone(self.ui._route_source("not an address"))

    def test_idle_panel_shows_the_tailscale_hint(self):
        dialog = self.ui.CollabDialog()
        dialog.CreateLayout()
        dialog.refresh()
        self.assertEqual(dialog.GetString(self.ui.ID_SHARE), self.ui.IDLE_HINT)

    def test_unchanged_text_is_not_rewritten(self):
        dialog = self.ui.CollabDialog()
        dialog.CreateLayout()
        writes = []
        original = dialog.SetString
        dialog.SetString = lambda widget, text: writes.append((widget, text)) or original(widget, text)
        dialog._show(self.ui.ID_LOG, "hello")
        dialog._show(self.ui.ID_LOG, "hello")
        dialog._show(self.ui.ID_LOG, "hello again")
        self.assertEqual([text for _w, text in writes], ["hello", "hello again"])

    def test_panel_pumps_at_about_30_hz_and_refreshes_twice_a_second(self):
        self.assertLessEqual(self.ui.PUMP_MS, 40)
        self.assertGreaterEqual(self.ui.REFRESH_MS // self.ui.PUMP_MS, 10)


if __name__ == "__main__":
    unittest.main()


class QuickScanTest(SyncTestCase):
    """While an object is dragged only the selected objects are looked at, not the whole scene."""

    def setUp(self):
        SyncTestCase.setUp(self)
        self.a = self.host(self.build_scene())
        self.b = self.join()

    def spy(self, instance):
        from c4d_collab_lib.c4d_adapter import compat
        # the fake moves every document counter at once, like R19's single counter: quick looks are tried
        for name, value in (("OBJECT_ONLY_COUNTERS", None), ("HAS_DETAILED_HDIRTY", False)):
            self.addCleanup(setattr, compat, name, getattr(compat, name))
            setattr(compat, name, value)
        visited = []
        tracker = instance.sync.tracker
        original = tracker._visit

        def visit(atom, kind, *args):
            visited.append(atom.GetName())
            return original(atom, kind, *args)
        tracker._visit = visit
        return visited

    def test_a_selected_object_is_found_without_walking_the_scene(self):
        cube = self.find(self.a.doc, "Cube")
        self.edit(self.a, lambda: cube.SetBit(fs.BIT_ACTIVE))
        time.sleep(0.6)  # past the full-scan interval
        self.a.sync.tracker.scan(self.a.doc, force=True)  # a full scan just ran: quick looks are allowed
        visited = self.spy(self.a)
        cube[fs.REL_POSITION] = fs.Vector(7, 8, 9)
        ops = self.a.sync.tracker.scan(self.a.doc)
        self.assertEqual(len(ops), 1)
        self.assertEqual(visited, ["Cube"], "only the selected object was visited")

    def test_other_changes_are_still_found_by_the_full_scan(self):
        cube = self.find(self.a.doc, "Cube")
        poly = self.find(self.a.doc, "Poly")
        self.edit(self.a, lambda: cube.SetBit(fs.BIT_ACTIVE))
        self.a.sync.tracker.scan(self.a.doc, force=True)
        self.edit(self.a, lambda: poly.SetName(fs.native("Renamed")))
        self.assertEqual(self.find(self.b.doc, "Renamed").GetName(), "Renamed")

    def test_evaluations_are_throttled_but_never_lost(self):
        from c4d_collab_lib.c4d_adapter import bridge
        calls = []
        original = self.c4d.EventAdd
        self.c4d.EventAdd = lambda *args: calls.append(1)
        self.addCleanup(setattr, self.c4d, "EventAdd", original)
        bridge._LAST_EVAL = 0.0
        self.assertTrue(bridge._evaluate())
        self.assertFalse(bridge._evaluate(), "a second request right away waits")
        self.assertTrue(bridge._EVAL_PENDING)
        bridge._LAST_EVAL = time.time() - 1.0
        bridge.animate(self.a.session)  # nothing is gliding, but the postponed evaluation happens now
        self.assertEqual(len(calls), 2)
        self.assertFalse(bridge._EVAL_PENDING)

    def test_the_two_timers_do_not_double_the_work(self):
        from c4d_collab_lib.c4d_adapter import bridge
        pumps = []
        original = bridge.pump
        bridge.pump = lambda: pumps.append(1)
        self.addCleanup(setattr, bridge, "pump", original)
        bridge._LAST_PUMP = 0.0
        bridge.timer_pump()
        bridge.timer_pump()
        self.assertEqual(len(pumps), 1)

    def test_the_tick_pauses_in_proportion_to_its_cost(self):
        from c4d_collab_lib.c4d_adapter import bridge
        self.assertGreater(bridge.MAX_TICK_BREATHER, 0)
