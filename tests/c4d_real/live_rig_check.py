# -*- coding: utf-8 -*-
"""A real rig through a real session: one c4dpy hosts a scene file, another one joins over the network (the own
CollabSession / SceneSync of the plugin, no shortcut).  Reports how far the objects of the guest are from those of the
host, whether the rig of the host stayed as it was when the guest joined, and what the guest sent back on its own.

    c4dpy tests/c4d_real/live_rig_check.py [scene.c4d]          (starts both processes, about a minute)
    c4dpy tests/c4d_real/live_rig_check.py --manual-guest       (guest = a Cinema 4D with the GUI, e.g. R19)
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import io
import json
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
SCENE = os.environ.get("LIVE_RIG_SCENE") or next(
    (a for a in sys.argv[1:] if a.lower().endswith(".c4d")), None) or r"C:\Users\PC\Desktop\C4D Things\Fight_test11\Fight_test11.c4d"
OUT = os.path.join(HERE, "out")
SHARED = os.path.join(OUT, "live_rig_host.json")
C4DPY = next((p for p in (r"C:\Program Files\c4dpy.exe", r"C:\Program Files\Maxon Cinema 4D 2024\c4dpy.exe")
              if os.path.exists(p)), None)


def launch_with_manual_guest():
    """The host runs in c4dpy; the guest is a Cinema 4D with the GUI (R19: no c4dpy): there, the Collab 4D Self-Test
    (c4d.CallCommand(1000004) in the Script Manager; it is hidden from the menu) joins and runs the same checks. Needs selftest.enabled next to the .pyp (see selftest.py)."""
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    paths = dict((role, os.path.join(OUT, "live_rig_result_%s.json" % role)) for role in ("host", "guest"))
    for path in list(paths.values()) + [SHARED]:
        if os.path.exists(path):
            os.remove(path)
    with io.open(os.path.join(OUT, "task.json"), "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"script": os.path.abspath(__file__), "env": {
            "C4D_COLLAB_ROLE": "guest", "C4D_COLLAB_PORT": str(port), "C4D_COLLAB_RESULTS": paths["guest"],
            "LIVE_RIG_SCENE": SCENE}}))
    env = dict(os.environ, C4D_COLLAB_ROLE="host", C4D_COLLAB_PORT=str(port), LIVE_RIG_SCENE=SCENE,
               C4D_COLLAB_RESULTS=paths["host"])
    console = io.open(os.path.join(OUT, "live_rig_console_host.txt"), "wb")
    host = subprocess.Popen([C4DPY, os.path.abspath(__file__)], env=env, stdin=subprocess.DEVNULL, stdout=console,
                            stderr=subprocess.STDOUT)
    print("Host is up on port %d. In the Cinema 4D that is the guest, Script Manager: c4d.CallCommand(1000004) (Collab 4D Self-Test, hidden from the menu)." % port)
    sys.stdout.flush()
    try:
        host.wait(900)
    except subprocess.TimeoutExpired:
        host.kill()
    deadline = time.time() + 60
    while not os.path.exists(paths["guest"]) and time.time() < deadline:
        time.sleep(1)
    for role in ("host", "guest"):
        if not os.path.exists(paths[role]):
            print("%s: NO RESULT" % role)
            continue
        with io.open(paths[role], encoding="utf-8") as handle:
            data = json.load(handle)
        print("== %s (C4D %s, Python %s): %d passed, %d failed" % (
            role, data["c4d"], data["python"], data["passed"], data["failed"]))
        for item in data["checks"]:
            print("  %s %s  %s" % ("PASS" if item["ok"] else "FAIL", item["name"], item["detail"][:300]))


def launch():
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    for name in ("live_rig_host.json", "live_rig_guest.json"):
        if os.path.exists(os.path.join(OUT, name)):
            os.remove(os.path.join(OUT, name))
    procs = []
    for role in ("host", "guest"):
        env = dict(os.environ, C4D_COLLAB_ROLE=role, C4D_COLLAB_PORT=str(port), LIVE_RIG_SCENE=SCENE,
                   C4D_COLLAB_RESULTS=os.path.join(OUT, "live_rig_result_%s.json" % role))
        console = io.open(os.path.join(OUT, "live_rig_console_%s.txt" % role), "wb")
        procs.append(subprocess.Popen([C4DPY, os.path.abspath(__file__)], env=env, stdin=subprocess.DEVNULL,
                                      stdout=console, stderr=subprocess.STDOUT))
        if role == "host":
            time.sleep(20)
    for proc in procs:
        proc.wait(600)
    for role in ("host", "guest"):
        path = os.path.join(OUT, "live_rig_result_%s.json" % role)
        if not os.path.exists(path):
            print("%s: NO RESULT (see out/live_rig_console_%s.txt)" % (role, role))
            continue
        with io.open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        print("== %s: %d passed, %d failed" % (role, data["passed"], data["failed"]))
        for item in data["checks"]:
            print("  %s %s  %s" % ("PASS" if item["ok"] else "FAIL", item["name"], item["detail"][:400]))


def snapshot(doc, uuid_of):
    """{uuid: [12 numbers of the global matrix]} of every object."""
    result = {}
    stack = [doc.GetFirstObject()]
    while stack:
        obj = stack.pop()
        while obj is not None:
            uuid = uuid_of(obj)
            if uuid:
                m = obj.GetMg()
                result[uuid] = [m.off.x, m.off.y, m.off.z, m.v1.x, m.v1.y, m.v1.z, m.v2.x, m.v2.y, m.v2.z,
                                m.v3.x, m.v3.y, m.v3.z]
            if obj.GetDown() is not None:
                stack.append(obj.GetDown())
            obj = obj.GetNext()
    return result


def mesh_points(doc, uuid_of):
    """{uuid: [x, y, z, ...]} the points of every polygon object as they are drawn (after Skin, Pose Morph and
    deformers), in world space. The matrices of the objects can all agree while a Skin draws the mesh metres away."""
    import c4d
    result = {}
    stack = [doc.GetFirstObject()]
    while stack:
        obj = stack.pop()
        while obj is not None:
            uuid = uuid_of(obj)
            if uuid and obj.GetType() == c4d.Opolygon:
                shown = obj.GetDeformCache() or obj
                mg = obj.GetMg()
                flat = []
                for point in shown.GetAllPoints():
                    world = mg * point
                    flat.extend([round(world.x, 3), round(world.y, 3), round(world.z, 3)])
                result[uuid] = flat
            if obj.GetDown() is not None:
                stack.append(obj.GetDown())
            obj = obj.GetNext()
    return result


def point_distance(a, b):
    if len(a) != len(b):
        return float("inf")
    worst = 0.0
    for index in range(0, len(a), 3):
        worst = max(worst, ((a[index] - b[index]) ** 2 + (a[index + 1] - b[index + 1]) ** 2
                            + (a[index + 2] - b[index + 2]) ** 2) ** 0.5)
    return worst


OTHER_FRAMES = (0, 100)


def distance(a, b):
    return max(abs(x - y) for x, y in zip(a, b))


def run_role():
    import scenario
    from scenario import Scenario, documents, c4d, compat

    class RigScenario(Scenario):
        def sent_ops(self):
            sent = []
            original = self.session.send_ops
            self.session.send_ops = lambda ops: (sent.extend(ops), original(ops))[1]
            return sent

        def evaluate(self):
            self.doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_0)

        def run_host(self):
            self.doc = documents.LoadDocument(SCENE, c4d.SCENEFILTER_OBJECTS | c4d.SCENEFILTER_MATERIALS)
            documents.InsertBaseDocument(self.doc)
            documents.SetActiveDocument(self.doc)
            self.evaluate()
            self.session.host("Host", self.port, password=scenario.PASSWORD, bind="127.0.0.1")
            self.pump(3)   # the objects get their ids
            fps = self.doc.GetFps()
            home = self.doc.GetTime()
            other_frames = {}
            for frame in OTHER_FRAMES:   # what the rig looks like on other frames: the guest has to agree there too
                self.doc.SetTime(c4d.BaseTime(frame, fps))
                self.evaluate()
                self.evaluate()
                other_frames[str(frame)] = mesh_points(self.doc, compat.read_uid)
            self.doc.SetTime(home)
            self.evaluate()
            self.pump(1)
            for _ in range(3):
                self.evaluate()   # the drawn meshes (Skin, Pose Morph) settle after a few evaluations
            before = snapshot(self.doc, compat.read_uid)
            with io.open(SHARED, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"m": before, "p": mesh_points(self.doc, compat.read_uid), "o": other_frames,
                                         "frame": self.doc.GetTime().GetFrame(self.doc.GetFps())}))
            if not self.wait("guest joined", lambda: len(self.session.users()) == 2, 120):
                return
            self.pump(30)   # the guest builds the rig and looks at itself
            self.evaluate()
            after = snapshot(self.doc, compat.read_uid)
            moved = [(distance(before[u], after[u]), u) for u in before if u in after]
            worst = max(moved)[0] if moved else 0.0
            names = {}
            stack = [self.doc.GetFirstObject()]
            while stack:
                obj = stack.pop()
                while obj is not None:
                    names[compat.read_uid(obj)] = compat.from_c4d_str(obj.GetName())
                    if obj.GetDown() is not None:
                        stack.append(obj.GetDown())
                    obj = obj.GetNext()
            shifted = ["%s %.3f" % (names.get(u), d) for d, u in sorted(moved, reverse=True)[:4] if d > 0.01]
            self.check("host: own rig stayed as it was when the guest joined (worst %.4f)" % worst, worst < 0.05,
                       "objects %d, moved more than 0.01: %s" % (len(moved), shifted))

        def run_guest(self):
            sent = self.sent_ops()
            deadline = time.time() + 120
            while True:
                self.session.join("127.0.0.1", self.port, "Guest", password=scenario.PASSWORD)
                end = time.time() + 20
                while time.time() < end and self.session.active and not (
                        self.doc is not None and self.doc.GetFirstObject()):
                    self.pump()
                    time.sleep(0.02)
                if self.doc is not None and self.doc.GetFirstObject() is not None:
                    break
                self.session.leave("retry")
                if time.time() > deadline:
                    self.check("wait: scene received", False, "host never answered")
                    return
                time.sleep(2)
            self.pump(20)    # everything of the snapshot is in, the rig evaluates
            self.evaluate()
            self.pump(3)
            self.evaluate()
            host = json.load(io.open(SHARED, encoding="utf-8"))
            mine = snapshot(self.doc, compat.read_uid)
            rows = sorted(((distance(host["m"][u], mine[u]), u) for u in host["m"] if u in mine), reverse=True)
            self.check("guest: objects paired with the ones of the host: %d of %d" % (len(rows), len(host["m"])),
                       len(rows) >= len(host["m"]) * 0.97)
            over = len([1 for d, _u in rows if d > 0.01])
            worst = rows[0][0] if rows else 0.0
            self.check("guest: rig matches the host (worst %.3f; %d of %d objects more than 0.01 away)"
                       % (worst, over, len(rows)), worst < 1.0,
                       "frame host %s guest %s" % (host["frame"], self.doc.GetTime().GetFrame(self.doc.GetFps())))
            mine_points = mesh_points(self.doc, compat.read_uid)
            names = {}
            stack = [self.doc.GetFirstObject()]
            while stack:
                obj = stack.pop()
                while obj is not None:
                    names[compat.read_uid(obj)] = compat.from_c4d_str(obj.GetName())
                    if obj.GetDown() is not None:
                        stack.append(obj.GetDown())
                    obj = obj.GetNext()
            meshes = sorted(((point_distance(host["p"][u], mine_points[u]), u) for u in host["p"] if u in mine_points),
                            reverse=True)
            off = [(d, u) for d, u in meshes if d > 1.0]
            self.check("guest: meshes drawn where the host draws them (worst %.2f cm; %d of %d meshes more than 1 cm away)"
                       % (meshes[0][0] if meshes else 0.0, len(off), len(meshes)), len(off) <= 2 and (not meshes or meshes[0][0] < 50),
                       ", ".join("%s %.1f" % (names.get(u), d) for d, u in off[:6]))
            for frame in OTHER_FRAMES:
                self.doc.SetTime(c4d.BaseTime(frame, self.doc.GetFps()))
                self.evaluate()
                self.evaluate()
                there = mesh_points(self.doc, compat.read_uid)
                seen = sorted(((point_distance(host["o"][str(frame)][u], there[u]), u) for u in host["o"][str(frame)]
                               if u in there), reverse=True)
                wrong = [(d, u) for d, u in seen if d > 1.0]
                self.check("guest: on frame %d the meshes are where the host draws them (worst %.2f cm; %d of %d more than 1 cm)"
                           % (frame, seen[0][0] if seen else 0.0, len(wrong), len(seen)),
                           len(wrong) <= 60 and (not seen or seen[0][0] < 50),
                           ", ".join("%s %.1f" % (names.get(u), d) for d, u in wrong[:6]))
            noise = [op for op in sent if op.get("op") in ("update", "create", "delete", "move")]
            self.check("guest: did not send its own rig evaluation back (%d ops)" % len(noise), len(noise) <= 12,
                       "; ".join("%s %s" % (op.get("op"), json.dumps(op.get("patch"))[:90]) for op in noise[:6]))
            errors = [line for line in self.session.log if "Failed" in line or "Traceback" in line]
            self.check("guest: no errors in the session log (%d)" % len(errors), not errors, "; ".join(errors[:3]))

    RigScenario(os.environ["C4D_COLLAB_ROLE"], os.environ["C4D_COLLAB_PORT"], os.environ["C4D_COLLAB_RESULTS"]).run()


if __name__ == "__main__":
    if os.environ.get("C4D_COLLAB_ROLE"):
        run_role()
    elif "--manual-guest" in sys.argv:
        launch_with_manual_guest()
    else:
        launch()
