#!/usr/bin/env python3
"""herdr plugin: mirror agent state onto a LaMetric Time.

Commands:
  event          event hook entrypoint (reads HERDR_PLUGIN_EVENT_JSON)
  refresh        push the current state to the widget
  test           send a test notification and refresh the widget
  toggle-alerts  turn transient notifications on/off (widget keeps updating)
  preview        render what the device would show in this terminal
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import pixels
from lametric import LaMetric, LaMetricError

ROOT = Path(__file__).resolve().parent
CONFIG_DIR = Path(os.environ.get("HERDR_PLUGIN_CONFIG_DIR") or ROOT / ".local" / "config")
STATE_DIR = Path(os.environ.get("HERDR_PLUGIN_STATE_DIR") or ROOT / ".local" / "state")
HERDR = os.environ.get("HERDR_BIN_PATH") or "herdr"

STATUS_ORDER = ["blocked", "done", "working", "unknown", "idle"]
GENERIC_TITLES = {"", "claude code", "claude", "codex", "pi"}


# --- config & state ---------------------------------------------------------


def load_config() -> Dict[str, str]:
    cfg = {
        "LAMETRIC_HOST": "",
        "LAMETRIC_API_KEY": "",
        "LAMETRIC_PUSH_URL": "",
        "ALERT_ON": "blocked,done",
        "ALERT_PRIORITY": "info",
        "ALERT_CYCLES": "1",
        "DEBUG": "0",
    }
    env_file = CONFIG_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            cfg[key.strip()] = value.strip().strip("'\"")
    for key in cfg:
        if os.environ.get(key):
            cfg[key] = os.environ[key]
    return cfg


def load_state() -> Dict[str, Any]:
    try:
        return json.loads((STATE_DIR / "state.json").read_text())
    except (OSError, ValueError):
        return {"alerts": True, "panes": {}}


def save_state(state: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_DIR / "state.json.tmp"
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_DIR / "state.json")


@contextmanager
def locked() -> Iterator[None]:
    """Event hooks can run concurrently; serialize read-modify-write."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / "lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def debug(cfg: Dict[str, str], name: str, payload: Any) -> None:
    if cfg["DEBUG"] in ("1", "true", "yes"):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_DIR / "debug.jsonl", "a") as fh:
            fh.write(json.dumps({"t": time.time(), name: payload}) + "\n")


# --- herdr ------------------------------------------------------------------


def list_agents() -> List[Dict[str, Any]]:
    out = subprocess.run(
        [HERDR, "agent", "list"], capture_output=True, text=True, timeout=10, check=True
    ).stdout
    agents = json.loads(out)["result"]["agents"]
    return sorted(agents, key=lambda a: _pane_sort_key(a.get("pane_id", "")))


def _pane_sort_key(pane_id: str) -> List[Any]:
    # "w12:p3" -> [12, 3] so workspaces order numerically
    return [int(p[1:]) if p[1:].isdigit() else p for p in pane_id.split(":")]


def label(agent: Dict[str, Any]) -> str:
    title = (agent.get("title") or agent.get("terminal_title_stripped") or "").strip()
    if title.lower() in GENERIC_TITLES or title.lower() == str(agent.get("agent", "")).lower():
        cwd = agent.get("foreground_cwd") or agent.get("cwd") or ""
        title = os.path.basename(cwd.rstrip("/")) or title or "agent"
    return title


# --- rendering --------------------------------------------------------------


def summary_text(statuses: List[str]) -> str:
    if not statuses:
        return "-"
    for status, word in (("blocked", "WAIT"), ("done", "DONE"), ("working", "RUN")):
        n = statuses.count(status)
        if n:
            return f"{n} {word}"
    return "IDLE"


def widget_frames(agents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    statuses = [a.get("agent_status", "unknown") for a in agents][:16]
    icon = pixels.data_uri(pixels.agent_grid(statuses))
    return [{"index": 0, "icon": icon, "text": summary_text(statuses)}]


def alert_frames(status: str, text: str) -> List[Dict[str, Any]]:
    return [{"icon": pixels.data_uri(pixels.alert_icon(status)), "text": text}]


# --- commands ---------------------------------------------------------------


def device(cfg: Dict[str, str]) -> Optional[LaMetric]:
    if not cfg["LAMETRIC_HOST"] or not cfg["LAMETRIC_API_KEY"]:
        log(f"not configured: set LAMETRIC_HOST and LAMETRIC_API_KEY in {CONFIG_DIR / '.env'}")
        return None
    return LaMetric(cfg["LAMETRIC_HOST"], cfg["LAMETRIC_API_KEY"], cfg["LAMETRIC_PUSH_URL"])


def push_widget(cfg: Dict[str, str], dev: Optional[LaMetric], agents: List[Dict[str, Any]]) -> None:
    frames = widget_frames(agents)
    debug(cfg, "widget", frames[0]["text"])
    if not dev:
        return
    dev.set_clockface(frames[0]["icon"])
    if cfg["LAMETRIC_PUSH_URL"]:
        dev.push_widget(frames)


def cmd_event() -> int:
    cfg = load_config()
    event = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON") or "{}")
    debug(cfg, "event", event)
    data = event.get("data") or {}
    pane_id = data.get("pane_id")
    status = data.get("agent_status")
    dev = device(cfg)

    with locked():
        state = load_state()
        agents = list_agents()
        errors = []
        try:
            push_widget(cfg, dev, agents)
        except LaMetricError as e:
            errors.append(e)

        previous = state["panes"].get(pane_id) if pane_id else None
        alert_on = {s.strip() for s in cfg["ALERT_ON"].split(",") if s.strip()}
        if dev and state.get("alerts", True) and status in alert_on and status != previous:
            agent = next((a for a in agents if a.get("pane_id") == pane_id), {})
            text = label({**agent, **{k: v for k, v in data.items() if v}})
            try:
                dev.notify(alert_frames(status, text), cfg["ALERT_PRIORITY"], int(cfg["ALERT_CYCLES"]))
            except LaMetricError as e:
                errors.append(e)

        state["panes"] = {a["pane_id"]: a.get("agent_status") for a in agents if a.get("pane_id")}
        if pane_id and status:
            state["panes"][pane_id] = status
        save_state(state)

    for e in errors:
        log(str(e))
    # Device errors are logged but not fatal: herdr should not see a failing hook
    # just because the clock is offline.
    return 0


def cmd_refresh() -> int:
    cfg = load_config()
    dev = device(cfg)
    with locked():
        agents = list_agents()
        state = load_state()
        state["panes"] = {a["pane_id"]: a.get("agent_status") for a in agents if a.get("pane_id")}
        save_state(state)
        try:
            push_widget(cfg, dev, agents)
        except LaMetricError as e:
            log(str(e))
            return 1
    return 0


def cmd_test() -> int:
    cfg = load_config()
    dev = device(cfg)
    if not dev:
        return 1
    try:
        info = dev.device()
        log(f"connected: {info.get('name')} ({info.get('model')}, os {info.get('os_version')})")
        dev.notify(alert_frames("done", "herdr connected"), cfg["ALERT_PRIORITY"], 1)
    except LaMetricError as e:
        log(str(e))
        return 1
    return cmd_refresh()


def cmd_toggle_alerts() -> int:
    with locked():
        state = load_state()
        state["alerts"] = not state.get("alerts", True)
        save_state(state)
    log(f"alerts {'on' if state['alerts'] else 'off'}")
    return 0


def cmd_preview() -> int:
    agents = list_agents()
    statuses = [a.get("agent_status", "unknown") for a in agents][:16]
    print(pixels.ansi(pixels.agent_grid(statuses)), f"  {summary_text(statuses)}")
    for a in agents:
        print(f"  {a.get('pane_id'):8} {a.get('agent_status'):8} {label(a)}")
    print()
    for status in ("blocked", "done"):
        print(pixels.ansi(pixels.alert_icon(status)), f"  {status} alert")
    return 0


COMMANDS = {
    "event": cmd_event,
    "refresh": cmd_refresh,
    "test": cmd_test,
    "toggle-alerts": cmd_toggle_alerts,
    "preview": cmd_preview,
}


def main(argv: List[str]) -> int:
    if len(argv) < 2 or argv[1] not in COMMANDS:
        print(__doc__, file=sys.stderr)
        return 2
    return COMMANDS[argv[1]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
