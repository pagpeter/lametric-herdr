"""Reading agent state from herdr: the local API socket and the herdr CLI."""

from __future__ import annotations

import json
import os
import socket
import subprocess
from typing import Any

BIN = os.environ.get("HERDR_BIN_PATH") or "herdr"

# The agent fields the plugin uses; everything else is dropped so that
# comparing two polls only reacts to changes that matter.
AGENT_FIELDS = (
    "pane_id",
    "tab_id",
    "focused",
    "agent",
    "agent_status",
    "state_change_seq",
    "completion_seq",
    "terminal_title_stripped",
    "cwd",
    "foreground_cwd",
)

GENERIC_TITLES = {"", "claude code", "claude", "codex", "pi"}

Agent = dict[str, Any]


def cli_json(*args: str, timeout: float = 15) -> Any:
    out = subprocess.run(
        [BIN, *args], capture_output=True, text=True, timeout=timeout, check=True
    ).stdout
    return json.loads(out)


def selection_file() -> str:
    """Where the herdr TUI records the machine it is showing."""
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "herdr", "client", "endpoint-selection.json")


def selected_machine() -> str | None:
    """Saved-machine id the TUI is showing, or None for Local."""
    try:
        with open(selection_file()) as fh:
            return json.load(fh).get("selected_profile")
    except (OSError, ValueError, AttributeError):
        return None


def local_socket_path() -> str:
    """The API socket of the herdr server this plugin runs under."""
    if os.environ.get("HERDR_SOCKET_PATH"):
        return os.environ["HERDR_SOCKET_PATH"]
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "herdr", "herdr.sock")


def local_agents(path: str) -> list[Agent]:
    """`agent.list` over the API socket, which serves one request per connection."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(5)
        sock.connect(path)
        sock.sendall(b'{"id":"lametric","method":"agent.list","params":{}}\n')
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    return _trim(json.loads(buf)["result"]["agents"])


def machine_agents(machine_id: str, timeout: float) -> list[Agent]:
    """`agent list` on a saved machine. herdr owns and reuses the SSH connection."""
    out = subprocess.run(
        [BIN, "--machine", machine_id, "agent", "list"],
        capture_output=True, text=True, timeout=timeout,
    )
    if out.returncode != 0:
        raise RuntimeError((out.stderr or out.stdout).strip()[:300] or f"exit {out.returncode}")
    return _trim(json.loads(out.stdout)["result"]["agents"])


def _trim(agents: list[Agent]) -> list[Agent]:
    return sorted(
        ({k: a.get(k) for k in AGENT_FIELDS} for a in agents),
        key=lambda a: pane_sort_key(a.get("pane_id") or ""),
    )


def pane_sort_key(pane_id: str) -> list[int]:
    """herdr ids are base-36 counters: "wE:p1" sorts as [14, 1]."""
    key = []
    for part in pane_id.split(":"):
        try:
            key.append(int(part[1:], 36))
        except ValueError:
            key.append(-1)
    return key


def label(agent: Agent) -> str:
    """Short human name: the terminal title, or the directory for generic titles."""
    title = (agent.get("terminal_title_stripped") or "").strip()
    if title.lower() in GENERIC_TITLES or title.lower() == str(agent.get("agent", "")).lower():
        cwd = agent.get("foreground_cwd") or agent.get("cwd") or ""
        title = os.path.basename(cwd.rstrip("/")) or title or "agent"
    return title
