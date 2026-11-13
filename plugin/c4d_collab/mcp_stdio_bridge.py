#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MCP server (stdio) for Cinema 4D: lets an AI app such as Claude Code use the running Cinema 4D.

The AI app starts this script; it talks to Cinema 4D over the local command channel that the
"MCP" page of Edit > Preferences switches on (see ARCHITECTURE.md, section "MCP"):

    claude mcp add cinema4d -- python /path/to/mcp_stdio_bridge.py --config /path/to/c4d_collab_mcp.json

The exact line is written to the config file (key "claude_code") when the server is switched on.
Standard library only; runs on Python 2.7 and 3.x. Nothing but protocol messages is written to stdout.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import argparse
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from c4d_collab_lib import PLUGIN_VERSION  # noqa: E402
from c4d_collab_lib.core import mcp_spec  # noqa: E402
from c4d_collab_lib.core.compat import error_text  # noqa: E402
from c4d_collab_lib.core.rpc import RpcClient, RpcError  # noqa: E402

DEFAULT_PROTOCOL = "2024-11-05"
NOT_RUNNING = ("Cinema 4D is not reachable. Start Cinema 4D and switch the server on: "
               "Edit > Preferences > MCP > Enable MCP Server.")


def log(text):
    sys.stderr.write("[c4d-mcp] %s\n" % text)
    sys.stderr.flush()


def read_config(path):
    """The settings Cinema 4D wrote when the server was switched on (see mcp_service.write_config)."""
    try:
        with io.open(path, encoding="utf-8") as handle:
            config = json.load(handle)
        config["port"] = int(config["port"])
        config["key"] = config["key"]
        return config
    except (IOError, OSError, ValueError, KeyError, TypeError):
        raise RpcError(NOT_RUNNING + " (no readable config file: %s)" % path)


def enabled_tools(config_path):
    """The tools to show: those Cinema 4D has switched on (Preferences > MCP > Toolset and Security). With
    no readable config (Cinema 4D not running) all are listed, so the AI app still learns what exists."""
    try:
        listed = read_config(config_path).get("enabled_tools")
    except RpcError:
        return list(mcp_spec.TOOLS)
    if not isinstance(listed, list):
        return list(mcp_spec.TOOLS)
    return [tool for tool in mcp_spec.TOOLS if tool["name"] in listed]


def call_cinema(config_path, name, arguments):
    config = read_config(config_path)
    port, key = config["port"], config["key"]
    wait = config.get("timeout")
    # what the command may wait for the main thread (Request Timeout) plus what it may take to run
    timeout = mcp_spec.TOOL_TIMEOUTS.get(name, 60) + (wait if isinstance(wait, (int, float)) else 30.0)
    try:
        client = RpcClient(port, key, timeout=10.0)
    except RpcError:
        raise
    except (IOError, OSError, EnvironmentError) as exc:
        raise RpcError("%s (%s)" % (NOT_RUNNING, error_text(exc)))
    try:
        return client.call(name, arguments, timeout=timeout)
    finally:
        client.close()


def tool_result(text, error=False):
    return {"content": [{"type": "text", "text": text}], "isError": bool(error)}


def handle(message, config_path):
    """The reply to one request, or None for notifications."""
    method, ident = message.get("method"), message.get("id")
    params = message.get("params") or {}
    if ident is None:
        return None   # notifications (initialized, cancelled...) need no answer
    if method == "initialize":
        version = params.get("protocolVersion") if isinstance(params, dict) else None
        return {"protocolVersion": version or DEFAULT_PROTOCOL, "capabilities": {"tools": {}},
                "serverInfo": {"name": mcp_spec.SERVER_NAME, "version": PLUGIN_VERSION}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [{"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
                          for t in enabled_tools(config_path)]}
    if method == "tools/call":
        name = params.get("name") if isinstance(params, dict) else None
        arguments = params.get("arguments") if isinstance(params, dict) else None
        if name not in mcp_spec.TOOL_NAMES:
            return tool_result("Unknown tool: %s" % (name,), True)
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return tool_result("arguments must be an object", True)
        try:
            result = call_cinema(config_path, name, arguments)
        except RpcError as exc:
            return tool_result(error_text(exc), True)
        text = json.dumps(result, indent=2, ensure_ascii=False)
        return tool_result(text if not isinstance(text, bytes) else text.decode("utf-8"))
    raise LookupError(method)


def reply(stream, ident, result=None, error=None):
    message = {"jsonrpc": "2.0", "id": ident}
    if error is not None:
        message["error"] = error
    else:
        message["result"] = result
    text = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    data = (text if not isinstance(text, bytes) else text.decode("utf-8")) + "\n"
    stream.write(data.encode("utf-8"))
    stream.flush()


def binary(stream, mode):
    """The raw byte stream behind stdin/stdout, with no newline translation on Windows."""
    if sys.platform == "win32":
        try:
            import msvcrt
            msvcrt.setmode(stream.fileno(), os.O_BINARY)
        except (ImportError, IOError, OSError, ValueError):
            pass
    return getattr(stream, "buffer", stream)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Cinema 4D MCP server (stdio)")
    parser.add_argument("--config", required=True, help="the c4d_collab_mcp.json written by Cinema 4D")
    args = parser.parse_args(argv)
    stdin, stdout = binary(sys.stdin, "rb"), binary(sys.stdout, "wb")
    for raw in iter(stdin.readline, b""):
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError("not an object")
        except ValueError:
            reply(stdout, None, error={"code": -32700, "message": "parse error"})
            continue
        try:
            result = handle(message, args.config)
        except LookupError:
            if message.get("id") is not None:
                reply(stdout, message["id"], error={"code": -32601, "message": "method not found"})
            continue
        except Exception as exc:   # a bug here must not end the session
            log("internal error: %r" % (exc,))
            if message.get("id") is not None:
                reply(stdout, message["id"], error={"code": -32603, "message": "internal error"})
            continue
        if result is not None and message.get("id") is not None:
            reply(stdout, message["id"], result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
