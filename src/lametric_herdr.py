#!/usr/bin/env python3
"""herdr plugin: agent state from all your herdr machines on a LaMetric Time.

All live work happens in the background watcher (watcher.py); herdr hooks
only make sure it is running, so installing the plugin is the whole setup.

Commands:
  ensure         start the watcher unless it is running (used by hooks)
  refresh        redraw the clock and re-read machines and config
  restart        restart the watcher
  stop           stop the watcher
  status         show the watcher, device, and per-machine state
  test           check the device connection and send a test alert
  toggle-alerts  turn notifications on/off (the clock keeps updating)
  preview        draw the local agents in this terminal
"""

from __future__ import annotations

import json
import signal
import sys
import time

import config
import herdr
import pixels
import watcher
from lametric import LaMetric, LaMetricError


def cmd_ensure() -> int:
    if watcher.running_pid() is None:
        watcher.spawn()
    return 0


def cmd_refresh() -> int:
    if not watcher.send(signal.SIGUSR1):
        watcher.spawn()
    return 0


def cmd_stop() -> int:
    if not watcher.send(signal.SIGTERM):
        print("watcher not running")
        return 0
    deadline = time.time() + 15  # it lets in-flight remote calls finish first
    while time.time() < deadline:
        if watcher.running_pid() is None:
            return 0
        time.sleep(0.1)
    print("watcher did not stop", file=sys.stderr)
    return 1


def cmd_restart() -> int:
    cmd_stop()
    watcher.spawn()
    return 0


def cmd_status() -> int:
    pid = watcher.running_pid()
    print(f"watcher: {f'running, pid {pid}' if pid else 'not running'}")
    print(f"alerts: {'on' if config.alerts_enabled() else 'off'}")
    try:
        status = json.loads(watcher.STATUS_FILE.read_text())
    except (OSError, ValueError):
        return 0
    print(f"updated {time.time() - status['t']:.0f}s ago, device: {status['device']}")
    for src in status["sources"]:
        print(f"  {src['label']:24} {src['state']:12} {' '.join(src['statuses'])}")
    return 0


def cmd_test() -> int:
    cfg = config.load()
    if not cfg["LAMETRIC_HOST"] or not cfg["LAMETRIC_API_KEY"]:
        print(f"not configured: set LAMETRIC_HOST and LAMETRIC_API_KEY in {config.ENV_FILE}",
              file=sys.stderr)
        return 1
    dev = LaMetric(cfg["LAMETRIC_HOST"], cfg["LAMETRIC_API_KEY"])
    try:
        info = dev.device()
        print(f"connected: {info.get('name')} ({info.get('model')}, os {info.get('os_version')})")
        dev.notify(pixels.alert_frames("done", "herdr connected"), cfg["ALERT_PRIORITY"], 1)
    except LaMetricError as e:
        print(e, file=sys.stderr)
        return 1
    return cmd_refresh()


def cmd_toggle_alerts() -> int:
    enabled = not config.alerts_enabled()
    config.set_alerts_enabled(enabled)
    print(f"alerts {'on' if enabled else 'off'}")
    return 0


def cmd_preview() -> int:
    agents = herdr.local_agents(herdr.local_socket_path())
    print(pixels.ansi(pixels.agent_grid([a["agent_status"] for a in agents][:16])))
    for a in agents:
        print(f"  {a['pane_id']:8} {a['agent_status']:8} {herdr.label(a)}")
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


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in COMMANDS:
        print(__doc__, file=sys.stderr)
        return 2
    return COMMANDS[argv[1]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
