# -*- coding: utf-8 -*-
"""A server without Cinema 4D (server/run_server.py, the Docker image): the first person to join shares the scene
they have open, the next one joins it, as with Blender's Multi-User dedicated server. Real Cinema 4D documents,
real sockets: run_server.py runs as its own process, and two full clients (session + scene sync) run in this c4dpy,
each with its own document made active before it is pumped (the way two Cinema 4Ds would see it).

    c4dpy tests/c4d_real/dedicated_server_check.py [scene.c4d]

Without a scene argument the rig scene of pairlib is used. The last line says how many checks failed (c4dpy's own
exit status is not reliable)."""
from __future__ import print_function
import os, socket, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pairlib
from pairlib import c4d, documents, log, walk, SCENE

from c4d_collab_lib.c4d_adapter.sync import SceneSync, same_document
from c4d_collab_lib.core.session import CONNECTED, CollabSession

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
PASSWORD = "check-1234"
SERVER_LOG = os.path.join(tempfile.gettempdir(), "c4d_collab_dedicated_server_check.log")
FAILED = []


def check(name, ok, detail=""):
    print("  %s %s %s" % ("PASS" if ok else "FAIL", name, detail))
    sys.stdout.flush()
    if not ok:
        FAILED.append(name)


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class Client(object):
    def __init__(self, name, doc=None):
        self.name = name
        self.session = CollabSession(client_info={"app": "c4dpy check"})
        self.sync = SceneSync(self.session)
        self.doc = doc

    def activate(self):
        if self.doc is not None:
            documents.SetActiveDocument(self.doc)

    def pump(self):
        self.activate()
        self.session.pump()
        if self.doc is None and self.sync.doc is not None:
            self.doc = self.sync.doc
        self.activate()
        self.sync.tick()
        self.sync.advance(time.time() + 10.0)


def settle(clients, rounds=10, timeout=120.0):
    deadline = time.time() + timeout
    quiet = 0
    while time.time() < deadline:
        for client in clients:
            client.pump()
        busy = any(c.session.mirror is not None and (c.session.mirror.pending_count or not c.session.mirror.synced)
                   for c in clients if c.session.active)
        quiet = 0 if busy else quiet + 1
        if quiet >= rounds:
            return True
        time.sleep(0.02)
    return False


def names(doc):
    return sorted(o.GetName() for o in walk(doc))


def python_command():
    for candidate in (["py", "-3"], ["python3"], ["python"]):
        try:
            subprocess.check_call(candidate + ["-c", "import sys"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return candidate
        except (OSError, subprocess.CalledProcessError):
            continue
    raise SystemExit("no Python 3 found to run server/run_server.py")


scene = sys.argv[1] if len(sys.argv) > 1 else SCENE
port = free_port()
server_log = open(SERVER_LOG, "w")
server = subprocess.Popen(python_command() + [os.path.join(ROOT, "server", "run_server.py"), "--host", "127.0.0.1",
                                              "--port", str(port), "--password", PASSWORD],
                          stdout=server_log, stderr=subprocess.STDOUT)
clients = []
try:
    time.sleep(1.5)
    check("run_server.py started", server.poll() is None)

    mine = documents.LoadDocument(scene, c4d.SCENEFILTER_OBJECTS | c4d.SCENEFILTER_MATERIALS)
    documents.InsertBaseDocument(mine)
    before = names(mine)
    log("scene", os.path.basename(scene), "objects:", len(before))

    first = Client("First", mine)
    clients.append(first)
    first.activate()
    first.session.join("127.0.0.1", port, "First", password=PASSWORD)
    check("first joiner connected", settle([first]) and first.session.state == CONNECTED)
    check("first joiner was asked to share their scene", first.session.seeds_scene)
    check("first joiner's own document is the session document", same_document(first.sync.doc, mine))
    check("nothing in the first joiner's document changed", names(mine) == before)
    log("first joiner shared, log:", list(first.session.log)[-1:])

    second = Client("Second")
    clients.append(second)
    second.session.join("127.0.0.1", port, "Second", password=PASSWORD)
    check("second joiner connected", settle(clients) and second.session.state == CONNECTED)
    check("second joiner is a guest", not second.session.seeds_scene and not second.session.leads)
    check("second joiner got a new document", second.doc is not None and not same_document(second.doc, mine))
    got = names(second.doc) if second.doc is not None else []
    missing = sorted(set(before) - set(got))
    check("second joiner has every object", not missing, "%d of %d, missing %s" % (len(got), len(before), missing[:5]))

    # An edit on the guest reaches the first joiner through the server.
    target = None
    for obj in walk(second.doc):
        if obj.GetType() == c4d.Onull or obj.GetType() == c4d.Ocube:
            target = obj
            break
    if target is None:
        target = second.doc.GetFirstObject()
    before_pos = target.GetRelPos()
    moved = before_pos + c4d.Vector(37, 0, 0)
    second.activate()
    target.SetRelPos(moved)
    target.Message(c4d.MSG_UPDATE)
    c4d.EventAdd()
    settle(clients)
    uuid = second.sync.registry.uuid_of(target)
    there = first.sync.registry.get(uuid)
    check("guest edit reached the first joiner", there is not None and (there.GetRelPos() - moved).GetLength() < 0.01,
          "%s: %s" % (target.GetName(), there.GetRelPos() if there is not None else None))

    # A third person joining later is a guest too: the scene already has content.
    third = Client("Third")
    clients.append(third)
    third.session.join("127.0.0.1", port, "Third", password=PASSWORD)
    check("late joiner connected", settle(clients) and third.session.state == CONNECTED)
    check("late joiner is a guest", not third.session.seeds_scene)
    late = third.sync.registry.get(uuid) if third.doc is not None else None
    check("late joiner sees the edit", late is not None and (late.GetRelPos() - moved).GetLength() < 0.01)
finally:
    for client in clients:
        client.session.leave()
    server.terminate()
    server.wait()
    server_log.close()

print(open(SERVER_LOG).read())
print("%d check(s) failed%s" % (len(FAILED), (": " + ", ".join(FAILED)) if FAILED else ""))
