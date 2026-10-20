"""Minimal client for a local herdr API socket (one request per connection)."""

import json
import os
import socket

FIELDS = ("pane_id", "agent", "agent_status", "terminal_title_stripped", "cwd", "foreground_cwd")


def socket_path(session):
    if session.startswith("/"):
        return session
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    root = os.path.join(base, "herdr")
    if session and session != "default":
        root = os.path.join(root, "sessions", session)
    return os.path.join(root, "herdr.sock")


def agent_list(path):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        sock.connect(path)
        sock.sendall(b'{"id":"lametric","method":"agent.list","params":{}}\n')
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    finally:
        sock.close()
    agents = json.loads(buf)["result"]["agents"]
    return [{k: a.get(k) for k in FIELDS} for a in agents]
