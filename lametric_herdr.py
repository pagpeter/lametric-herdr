#!/usr/bin/env python3
"""herdr plugin: mirror agent state from all herdr machines onto a LaMetric Time.

All live work happens in one background watcher (watcher.py). herdr hooks only
make sure it is running, so installing the plugin is the whole setup.

Commands:
  ensure         start the watcher unless it is already running (hooks)
  refresh        force the watcher to redraw the clock (starts it if needed)
  restart        restart the watcher
  stop           stop the watcher
  status         show the watcher and per-machine state
  test           check the device connection and send a test alert
  toggle-alerts  turn transient notifications on/off (the clock keeps updating)
  preview        render the current local agents in this terminal
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pixels
from lametric import LaMetric, LaMetricError

ROOT = Path(__file__).resolve().parent
CONFIG_DIR = Path(os.environ.get("HERDR_PLUGIN_CONFIG_DIR") or ROOT / ".local" / "config")
STATE_DIR = Path(os.environ.get("HERDR_PLUGIN_STATE_DIR") or ROOT / ".local" / "state")
HERDR = os.environ.get("HERDR_BIN_PATH") or "herdr"
PLUGIN_ID = os.environ.get("HERDR_PLUGIN_ID") or "lametric-herdr"

LOCK_FILE = STATE_DIR / "watcher.lock"
PID_FILE = STATE_DIR / "watcher.pid"
STATUS_FILE = STATE_DIR / "status.json"
LOG_FILE = STATE_DIR / "watcher.log"

GENERIC_TITLES = {"", "claude code", "claude", "codex", "pi"}


# --- config & state ---------------------------------------------------------


def load_config() -> Dict[str, str]:
    cfg = {
        "LAMETRIC_HOST": "",
        "LAMETRIC_API_KEY": "",
        "ALERT_ON": "blocked,done",
        "ALERT_PRIORITY": "info",
        "ALERT_CYCLES": "1",
        "REMOTE_POLL_SECONDS": "",
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


def alerts_enabled() -> bool:
    try:
        return bool(json.loads((STATE_DIR / "state.json").read_text()).get("alerts", True))
    except (OSError, ValueError):
        return True


def set_alerts(enabled: bool) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "state.json").write_text(json.dumps({"alerts": enabled}))


def device(cfg: Dict[str, str]) -> Optional[LaMetric]:
    if not cfg["LAMETRIC_HOST"] or not cfg["LAMETRIC_API_KEY"]:
        return None
    return LaMetric(cfg["LAMETRIC_HOST"], cfg["LAMETRIC_API_KEY"])


# --- herdr ------------------------------------------------------------------


def herdr_json(*args: str) -> Any:
    out = subprocess.run(
        [HERDR, *args], capture_output=True, text=True, timeout=15, check=True
    ).stdout
    return json.loads(out)


def pane_sort_key(pane_id: str) -> List[int]:
    # herdr ids are base-36 counters: "wE:p1" -> [14, 1]
    key = []
    for part in pane_id.split(":"):
        try:
            key.append(int(part[1:], 36))
        except ValueError:
            key.append(-1)
    return key


def label(agent: Dict[str, Any]) -> str:
    title = (agent.get("terminal_title_stripped") or "").strip()
    if title.lower() in GENERIC_TITLES or title.lower() == str(agent.get("agent", "")).lower():
        cwd = agent.get("foreground_cwd") or agent.get("cwd") or ""
        title = os.path.basename(cwd.rstrip("/")) or title or "agent"
    return title


# --- rendering --------------------------------------------------------------


def clock_icon(statuses: List[str]) -> str:
    return pixels.data_uri(pixels.agent_grid(statuses[:16]))


def alert_frames(status: str, text: str) -> List[Dict[str, Any]]:
    return [{"icon": pixels.data_uri(pixels.alert_icon(status)), "text": text}]


# --- watcher process management ---------------------------------------------


def watcher_pid() -> Optional[int]:
    """PID of the running watcher, or None. The watcher holds an exclusive
    flock for its whole lifetime, so a free lock means it is not running."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "a") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            try:
                return int(PID_FILE.read_text().strip())
            except (OSError, ValueError):
                return -1
        fcntl.flock(fh, fcntl.LOCK_UN)
    return None


def start_watcher() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as log_fh:
        subprocess.Popen(
            [sys.executable, str(ROOT / "watcher.py")],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=log_fh,
            start_new_session=True,  # survive the hook process that started it
            close_fds=True,
        )


def signal_watcher(sig: int) -> bool:
    pid = watcher_pid()
    if pid and pid > 0:
        try:
            os.kill(pid, sig)
            return True
        except OSError:
            pass
    return False


# --- commands ---------------------------------------------------------------


def cmd_ensure() -> int:
    if watcher_pid() is None:
        start_watcher()
    return 0


def cmd_refresh() -> int:
    if not signal_watcher(signal.SIGUSR1):
        start_watcher()
    return 0


def cmd_stop() -> int:
    if not signal_watcher(signal.SIGTERM):
        print("watcher not running", file=sys.stderr)
        return 0
    for _ in range(150):  # the watcher lets in-flight remote calls finish
        if watcher_pid() is None:
            return 0
        time.sleep(0.1)
    print("watcher did not stop", file=sys.stderr)
    return 1


def cmd_restart() -> int:
    cmd_stop()
    start_watcher()
    return 0


def cmd_status() -> int:
    pid = watcher_pid()
    print(f"watcher: {'running, pid ' + str(pid) if pid else 'not running'}")
    print(f"alerts: {'on' if alerts_enabled() else 'off'}")
    try:
        status = json.loads(STATUS_FILE.read_text())
    except (OSError, ValueError):
        return 0
    age = time.time() - status.get("t", 0)
    print(f"updated {age:.0f}s ago, device: {status.get('device')}")
    for src in status.get("sources", []):
        agents = " ".join(src.get("statuses", []))
        print(f"  {src['label']:24} {src['state']:12} {agents}")
    return 0


def cmd_test() -> int:
    cfg = load_config()
    dev = device(cfg)
    if not dev:
        print(f"not configured: set LAMETRIC_HOST and LAMETRIC_API_KEY in {CONFIG_DIR / '.env'}",
              file=sys.stderr)
        return 1
    try:
        info = dev.device()
        print(f"connected: {info.get('name')} ({info.get('model')}, os {info.get('os_version')})")
        dev.notify(alert_frames("done", "herdr connected"), cfg["ALERT_PRIORITY"], 1)
    except LaMetricError as e:
        print(str(e), file=sys.stderr)
        return 1
    return cmd_refresh()


def cmd_toggle_alerts() -> int:
    enabled = not alerts_enabled()
    set_alerts(enabled)
    print(f"alerts {'on' if enabled else 'off'}")
    return 0


def cmd_preview() -> int:
    agents = sorted(herdr_json("agent", "list")["result"]["agents"],
                    key=lambda a: pane_sort_key(a.get("pane_id", "")))
    statuses = [a.get("agent_status", "unknown") for a in agents]
    print(pixels.ansi(pixels.agent_grid(statuses[:16])))
    for a in agents:
        print(f"  {a.get('pane_id'):8} {a.get('agent_status'):8} {label(a)}")
    return 0


COMMANDS = {
    "ensure": cmd_ensure,
    "refresh": cmd_refresh,
    "restart": cmd_restart,
    "stop": cmd_stop,
    "status": cmd_status,
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
