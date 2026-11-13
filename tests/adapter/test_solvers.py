# -*- coding: utf-8 -*-
"""What an IK / IK-Spline solver writes is not an edit, and which parameters an XPresso port of a Constraint tag drives.
Both were measured on a real rig (limb stretch, head world/local switch); the checks against Cinema 4D itself are in
tests/c4d_real/limb_check.py. Python 2.7 and 3.x."""
from __future__ import absolute_import, division, print_function, unicode_literals

import unittest

from test_sync import SyncTestCase

IK_TAG, IK_SPLINE_TAG = 1019561, 1019862
ENABLE, USE_IK, IK_FK, IK_STRENGTH, IK_END = 1000999, 2034, 2002, 2026, 2000
SPLINE_END, SPLINE_STRENGTH = 2002, 2010


class _Tag(object):
    """A tag that answers GetParameter by the id of the description level."""

    def __init__(self, values):
        self.values = values

    def GetParameter(self, desc_id, flags):
        return self.values.get(desc_id[0].id)


class _Registry(object):
    def __init__(self):
        self.atoms = {}

    def get(self, uuid):
        return self.atoms.get(uuid)

    def uuid_of(self, atom):
        for uuid, other in self.atoms.items():
            if other is atom:
                return uuid
        return None


class SolvedJointsTest(SyncTestCase):
    def setUp(self):
        SyncTestCase.setUp(self)
        from c4d_collab_lib.c4d_adapter import tracker
        from c4d_collab_lib.core.model import Node, SceneState
        self.tracker_module = tracker
        self.shadow = SceneState()
        self.registry = _Registry()
        self.tracker = tracker.Tracker(self.shadow, self.registry)
        self.Node = Node
        # thigh > knee > end > toe, as joints
        for uuid, parent in (("thigh", None), ("knee", "thigh"), ("end", "knee"), ("toe", "end")):
            self.add(uuid, "object", 1019362, parent, {"p": {"905:23:5155": {"V": [1.0, 1.5, 1.0]},
                                                                "903:23:5155": {"V": [0.0, 10.0, 0.0]},
                                                                "900:130:0": "x"}})
            self.registry.atoms[uuid] = object()

    def add(self, uuid, kind, ctype, parent, data):
        self.shadow.nodes[uuid] = self.Node(uuid, kind, ctype, parent, None, uuid, data)

    def solver(self, ctype, values):
        self.add("tag", "tag", ctype, "thigh", {})
        self.registry.atoms["tag"] = _Tag(values)
        self.tracker._ik_chains = None

    def ik(self, overrides=None):
        values = {ENABLE: 1, USE_IK: 1, IK_FK: 0.0, IK_STRENGTH: 1.0, IK_END: self.registry.atoms["end"]}
        values.update(overrides or {})
        self.solver(IK_TAG, values)

    def test_the_chain_of_an_ik_tag_is_solved(self):
        self.ik()
        self.assertEqual(self.tracker.solved_joints(), set(["thigh", "knee", "end"]))   # the toe is below the end

    def test_a_tag_that_is_not_in_use_solves_nothing(self):
        for overrides in ({ENABLE: 0}, {USE_IK: 0}, {IK_FK: 1.0}, {IK_STRENGTH: 0.0}):
            self.ik(overrides)
            self.assertEqual(self.tracker.solved_joints(), set(), overrides)   # FK: the joints are posed by hand

    def test_the_end_joint_must_be_below_the_tag(self):
        self.ik()
        self.registry.atoms["tag"].values[IK_END] = None   # no end joint
        self.tracker._ik_chains = None
        self.assertEqual(self.tracker.solved_joints(), set())
        self.shadow.nodes["end"].parent = None   # the end joint is not a descendant of the tag's object
        self.registry.atoms["tag"].values[IK_END] = self.registry.atoms["end"]
        self.tracker._ik_chains = None
        self.assertEqual(self.tracker.solved_joints(), set())

    def test_an_ik_spline_tag_solves_its_chain_too(self):
        self.solver(IK_SPLINE_TAG, {ENABLE: 1, SPLINE_STRENGTH: 1.0, SPLINE_END: self.registry.atoms["knee"]})
        self.assertEqual(self.tracker.solved_joints(), set(["thigh", "knee"]))

    def test_the_chain_is_read_again_on_request(self):
        self.ik()
        self.assertEqual(len(self.tracker.solved_joints()), 3)
        self.registry.atoms["tag"].values[ENABLE] = 0
        self.assertEqual(len(self.tracker.solved_joints()), 3)                # per scan
        self.assertEqual(len(self.tracker.solved_joints(fresh=True)), 0)

    def test_a_stretched_scale_is_the_solver_not_an_edit(self):
        self.ik()
        node = self.shadow.nodes["thigh"]
        data = {"p": {"905:23:5155": {"V": [1.0, 2.0, 1.0]},      # the solver stretched the thigh
                      "903:23:5155": {"V": [0.0, 12.0, 0.0]},
                      "900:130:0": "renamed"}}
        masked = self.tracker._mask(self.registry.atoms["thigh"], "thigh", node, data)
        self.assertEqual(masked["p"]["905:23:5155"], {"V": [1.0, 1.5, 1.0]})
        self.assertEqual(masked["p"]["903:23:5155"], {"V": [0.0, 10.0, 0.0]})
        self.assertEqual(masked["p"]["900:130:0"], "renamed")                # anything else is still an edit
        self.assertEqual(data["p"]["905:23:5155"], {"V": [1.0, 2.0, 1.0]})   # the input is not changed

    def test_a_joint_that_is_not_solved_keeps_its_edits(self):
        self.ik()
        node = self.shadow.nodes["toe"]
        data = {"p": {"905:23:5155": {"V": [1.0, 2.0, 1.0]}}}
        masked = self.tracker._mask(self.registry.atoms["toe"], "toe", node, data)
        self.assertEqual(masked["p"]["905:23:5155"], {"V": [1.0, 2.0, 1.0]})


    def constraint(self, owner, enabled=1):
        self.add("constraint", "tag", 1019364, owner, {})
        self.registry.atoms["constraint"] = _Tag({ENABLE: enabled})
        self.tracker._constrained = None

    def test_an_object_moved_by_a_constraint_is_not_edited_by_scrubbing(self):
        self.constraint("toe")
        self.assertEqual(self.tracker.constrained_objects(), set(["toe"]))
        node = self.shadow.nodes["toe"]
        data = {"p": {"904:23:5155": {"V": [0.1, 0.2, 0.3]}, "900:130:0": "renamed"}}
        node.data["p"]["904:23:5155"] = {"V": [0.0, 0.0, 0.0]}
        masked = self.tracker._mask(self.registry.atoms["toe"], "toe", node, data)
        self.assertEqual(masked["p"]["904:23:5155"], {"V": [0.0, 0.0, 0.0]})   # the rotation the constraint wrote
        self.assertEqual(masked["p"]["900:130:0"], "renamed")                  # a rename is still an edit

    def test_a_constraint_that_is_off_moves_nothing(self):
        self.constraint("toe", enabled=0)
        self.assertEqual(self.tracker.constrained_objects(), set())


class _Container(object):
    def __init__(self, name):
        self.name = name

    def GetType(self, key):
        return 130

    def __getitem__(self, key):
        return self.name


class _Constraint(object):
    """A Constraint tag whose description lists the parameters of every mode, as a real one does."""

    def __init__(self, c4d, modes):
        self.c4d = c4d
        self.modes = modes
        self.entries = [   # (id, name, creator, parent group)
            (2203, "Strength", 1019364, 6002), (8502, "Weight", 1019364, 7002),      # Parent: not in use
            (2116, "Strength", 1019364, 6000),                                          # Transform
            (10002, "Weight", 0, 120000), (10001, "Target", 0, 7000),                  # Transform targets
            (10012, "Weight", 0, 120001), (10011, "Target", 0, 7000),
            (20002, "Weight", 0, 120000), (20001, "Target", 0, 7002),                  # Parent targets
        ]

    def __getitem__(self, ident):
        return 1 if ident in self.modes else 0

    def GetDescription(self, flags):
        c4d = self.c4d
        for ident, name, creator, group in self.entries:
            yield (_Container(name), c4d.DescID(c4d.DescLevel(ident, 19, creator)), c4d.DescID(c4d.DescLevel(group)))


class _Port(object):
    def __init__(self, main_id, name):
        self.main_id, self.name = main_id, name

    def GetMainID(self):
        return self.main_id

    def GetName(self, node):
        return self.name


class ConstraintPortsTest(SyncTestCase):
    def setUp(self):
        SyncTestCase.setUp(self)
        from c4d_collab_lib.c4d_adapter.serializers import xpresso
        self.xpresso = xpresso

    def test_the_parameters_of_the_mode_in_use_are_taken(self):
        strength, weights = self.xpresso._constraint_parameters(_Constraint(self.c4d, [1000]))   # Transform
        self.assertEqual(strength[0].id, 2116)
        self.assertEqual([w[0].id for w in weights], [10002, 10012])   # not 8502, not the Parent targets

    def test_the_parent_mode_has_its_own(self):
        strength, weights = self.xpresso._constraint_parameters(_Constraint(self.c4d, [1002]))   # Parent
        self.assertEqual(strength[0].id, 2203)
        self.assertEqual([w[0].id for w in weights], [20002])

    def test_weight_ports_follow_the_order_of_their_numbers(self):
        tag = _Constraint(self.c4d, [1000])
        ports = [_Port(10000003, "local"), _Port(10000002, "world")]   # the first to be used is the first target
        specs = [{"m": 10000003}, {"m": 10000002}]
        self.xpresso._constraint_ports(None, ports, specs, tag)
        self.assertEqual(specs[1]["d"], "10002:19:0")
        self.assertEqual(specs[0]["d"], "10012:19:0")

    def test_the_strength_port_drives_the_strength_of_the_mode_in_use(self):
        tag = _Constraint(self.c4d, [1000])
        specs = [{"m": 10000000, "d": "2203:19:1019364"}]   # what the name alone finds: the first "Strength"
        self.xpresso._constraint_ports(None, [_Port(10000000, "Strength")], specs, tag)
        self.assertEqual(specs[0]["d"], "2116:19:1019364")

    def test_a_port_without_a_target_has_no_parameter(self):
        tag = _Constraint(self.c4d, [1000])
        ports = [_Port(10000000, "a"), _Port(10000001, "b"), _Port(10000002, "c")]   # three ports, two targets
        specs = [{"m": 10000000}, {"m": 10000001}, {"m": 10000002}]
        self.xpresso._constraint_ports(None, ports, specs, tag)
        self.assertEqual([s.get("d") for s in specs], ["10002:19:0", "10012:19:0", None])


if __name__ == "__main__":
    unittest.main()


class _NamedContainer(object):
    def __getitem__(self, key):
        return "Position . X"


class _StrictDescription(object):
    def __init__(self):
        self.calls = []

    def GetParameter(self, *args):
        self.calls.append(len(args))
        if len(args) < 2:
            raise TypeError("Required argument 'ar' (pos 2) not found")
        return _NamedContainer()


class Release19Test(SyncTestCase):
    def test_a_port_can_be_named_where_get_parameter_wants_two_arguments(self):
        from c4d_collab_lib.c4d_adapter.serializers import xpresso
        description = _StrictDescription()
        self.assertEqual(xpresso._level_name(description, object()), "Position . X")
        self.assertEqual(description.calls, [1, 2])    # tried plainly, then with the AtomArray argument


class SkippedTagTest(SyncTestCase):
    """A tag only this computer has (the one a Mesh deformer's Initialize adds to a cage) sits before the shared ones:
    the shared tag after it follows it in the scan, but not in the session. Each scan sent a move the session
    rejected (R19 log: 439,000 of them), at the price of every scan."""

    def test_a_local_tag_before_shared_ones_is_not_a_move(self):
        import logging
        import fake_scene as fs
        from c4d_collab_lib.c4d_adapter import compat

        doc = fs.BaseDocument()
        obj = fs.BaseObject(fs.Ojoint)
        obj.SetName(fs.native("Cage"))
        doc.InsertObject(obj)
        obj.MakeTag(fs.Tphong)
        host = self.host(doc)
        guest = self.join()
        cage = self.find(guest.doc, "Cage")
        local = cage.MakeTag(fs.Ttexture)           # inserted first, as Initialize does
        compat.write_uid(local, compat.new_uid())
        guest.sync.tracker.skip.add(compat.read_uid(local))

        rejected = []

        class Catch(logging.Handler):
            def emit(self, record):
                if "rejected by shadow" in record.getMessage():
                    rejected.append(record.getMessage())

        handler = Catch()
        logger = logging.getLogger("c4d_collab.tracker")
        logger.addHandler(handler)
        try:
            guest.activate()
            ops = guest.sync.tracker.scan(guest.doc, force=True)
            ops += guest.sync.tracker.scan(guest.doc, force=True)
        finally:
            logger.removeHandler(handler)
        self.assertEqual(rejected, [])
        self.assertEqual([op for op in ops if op["op"] == "move"], [])
        self.assertIsNotNone(host)
