# -*- coding: utf-8 -*-
"""Standalone C4D Collab session server (no Cinema 4D needed).

    py server/run_server.py --port 47800 --password secret

Normally the host's Cinema 4D runs this same server internally; use this
script (or the Docker image) when a machine without C4D should host sessions.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import argparse
import logging
import os
import signal
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for candidate in (os.path.join(HERE, "..", "plugin", "c4d_collab"), os.path.join(HERE, "lib")):
    if os.path.isdir(os.path.join(candidate, "c4d_collab_lib")):
        sys.path.insert(0, os.path.abspath(candidate))
        break

from c4d_collab_lib.core import protocol  # noqa: E402
from c4d_collab_lib.core.server import SessionServer  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="C4D Collab session server")
    parser.add_argument("--host", default="0.0.0.0", help="interface to bind (default: all)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("C4D_COLLAB_PORT", protocol.DEFAULT_PORT)))
    parser.add_argument("--password", default=os.environ.get("C4D_COLLAB_PASSWORD", ""))
    parser.add_argument("--name", default=os.environ.get("C4D_COLLAB_SESSION", "C4D Collab"))
    parser.add_argument("--max-clients", type=int, default=16)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    server = SessionServer(host=args.host, port=args.port, password=args.password,
                           session_name=args.name, max_clients=args.max_clients, first_joiner_seeds=True)
    port = server.start()
    print("C4D Collab server '%s' on %s:%d (password: %s). Ctrl+C to stop."
          % (args.name, args.host, port, "yes" if args.password else "no"))
    print("The first person to join shares the scene they have open; everyone after them joins that scene.")

    stopping = []
    signal.signal(signal.SIGINT, lambda *_: stopping.append(True))
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))
    while not stopping and server.running:
        time.sleep(0.2)
    server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
