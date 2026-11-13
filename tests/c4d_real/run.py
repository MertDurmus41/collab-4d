# -*- coding: utf-8 -*-
"""Run the real-Cinema-4D scenario: two headless c4dpy processes (host + guest).

    py tests/c4d_real/run.py
    py tests/c4d_real/run.py --c4dpy "C:/Program Files/c4dpy.exe"

Prints every check and exits with 0 only if all of them passed.
Results and per-role logs are written to tests/c4d_real/out/.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import argparse
import io
import json
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_C4DPY = [
    r"C:\Program Files\c4dpy.exe",
    r"C:\Program Files\Maxon Cinema 4D 2023\c4dpy.exe",
    r"C:\Program Files\Maxon Cinema 4D 2024\c4dpy.exe",
]


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--c4dpy", default=next((p for p in DEFAULT_C4DPY if os.path.exists(p)), None))
    parser.add_argument("--timeout", type=float, default=1200)
    parser.add_argument("--manual-peer", choices=("host", "guest"),
                        help="run only the other role here; the given role is played by a Cinema 4D "
                             "GUI via the Script Manager: c4d.CallCommand(1000004)")
    parser.add_argument("--label", default="manual", help="name used for the manual peer's result file")
    args = parser.parse_args()
    if not args.c4dpy:
        print("c4dpy not found; pass --c4dpy")
        return 2

    out = os.path.join(HERE, "out")
    if not os.path.isdir(out):
        os.makedirs(out)
    port = free_port()
    procs = {}
    roles = ("host", "guest")
    if args.manual_peer:
        manual_result = os.path.join(out, "result_%s_%s.json" % (args.manual_peer, args.label))
        if os.path.exists(manual_result):
            os.remove(manual_result)
        with io.open(os.path.join(out, "task.json"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"role": args.manual_peer, "port": port, "results": manual_result}))
        roles = [r for r in roles if r != args.manual_peer]
        print("Waiting for the %s in Cinema 4D: Script Manager, c4d.CallCommand(1000004) (Collab 4D Self-Test)" % args.manual_peer)
        sys.stdout.flush()
    for role in roles:
        result = os.path.join(out, "result_%s.json" % role)
        if os.path.exists(result):
            os.remove(result)
        env = dict(os.environ, C4D_COLLAB_ROLE=role, C4D_COLLAB_PORT=str(port), C4D_COLLAB_RESULTS=result,
                   C4D_COLLAB_WAIT="900" if args.manual_peer else "90")
        console = io.open(os.path.join(out, "console_%s.txt" % role), "wb")
        procs[role] = (subprocess.Popen([args.c4dpy, os.path.join(HERE, "scenario.py")], env=env,
                                        stdin=subprocess.DEVNULL, stdout=console, stderr=subprocess.STDOUT),
                       result, console)
        if role == "host" and not args.manual_peer:
            time.sleep(8)  # let the host start Cinema 4D and open the port

    deadline = time.time() + args.timeout
    for role, (proc, _, console) in procs.items():
        try:
            proc.wait(max(1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            proc.kill()
            print("%s: killed after timeout" % role)
        console.close()

    if args.manual_peer:
        while not os.path.exists(manual_result) and time.time() < deadline + 60:
            time.sleep(1)
        procs[args.manual_peer] = (None, manual_result, None)

    failed = 0
    for role, (_, result, _) in procs.items():
        if not os.path.exists(result):
            print("%s: NO RESULT (see out/console_%s.txt)" % (role, role))
            failed += 1
            continue
        with io.open(result, encoding="utf-8") as handle:
            data = json.load(handle)
        print("\n== %s  (C4D %s, Python %s): %d passed, %d failed"
              % (role, data["c4d"], data["python"], data["passed"], data["failed"]))
        for item in data["checks"]:
            print("  %s %s%s" % ("PASS" if item["ok"] else "FAIL", item["name"],
                                 "" if item["ok"] else "  -> " + item["detail"][:500]))
        failed += data["failed"]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
