#!/usr/bin/env python3
"""Long-running watcher: agent state from all herdr machines -> LaMetric clockface.

Started by the plugin's hooks (see `lametric_herdr.py ensure`); one instance
per user, enforced by an flock held for the process lifetime.

- Local agents: `agent.list` over the local herdr socket every second.
- Saved machines: `herdr --machine <id> agent list` every 2s, so herdr owns
  the SSH connection and reuses it between calls. The plugin holds no
  credentials and runs no code of its own on the remote.

Polling rather than subscribing: herdr only offers per-pane status
subscriptions, and "done" becomes "idle" without any event when a pane is
viewed.
"""

from __future__ import annotations

import fcntl
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

import herdr_socket
import lametric_herdr as plugin
from lametric import LaMetric, LaMetricError

LOCAL_INTERVAL_S = 1.0
# Remote calls ride herdr's shared SSH connection (one multiplexed master per
# machine), so each poll is a cheap command on an open connection.
REMOTE_INTERVAL_S = 2.0
REMOTE_TIMEOUT_S = 120  # the first call may have to establish SSH
OFFLINE_GRACE_S = 60  # keep a lost machine's agents this long before dropping them
RENDER_DEBOUNCE_S = 0.2
DEVICE_RETRY_S = 15
HOUSEKEEPING_S = 60


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def remote_interval() -> float:
    configured = plugin.load_config().get("REMOTE_POLL_SECONDS", "")
    try:
        return max(float(configured), 1.0) if configured else REMOTE_INTERVAL_S
    except ValueError:
        return REMOTE_INTERVAL_S


class Poller(threading.Thread):
    """Polls one herdr server and reports changes to the watcher's queue."""

    def __init__(self, sid: str, label: str, events: "queue.Queue", machine: str = ""):
        super().__init__(daemon=True, name=f"poll-{label}")
        self.sid = sid
        self.label = label
        self.events = events
        self.machine = machine
        self.stopped = threading.Event()
        self.local_socket = herdr_socket.socket_path(os.environ.get("HERDR_SOCKET_PATH") or "default")

    def fetch(self) -> List[Dict[str, Any]]:
        if not self.machine:
            return herdr_socket.agent_list(self.local_socket)
        out = subprocess.run(
            [plugin.HERDR, "--machine", self.machine, "agent", "list"],
            capture_output=True, text=True, timeout=REMOTE_TIMEOUT_S,
        )
        if out.returncode != 0:
            raise RuntimeError((out.stderr or out.stdout).strip()[:300] or f"exit {out.returncode}")
        agents = json.loads(out.stdout)["result"]["agents"]
        return [{k: a.get(k) for k in herdr_socket.FIELDS} for a in agents]

    def run(self) -> None:
        interval = remote_interval() if self.machine else LOCAL_INTERVAL_S
        last: Any = None
        backoff = 0.0
        while not self.stopped.is_set():
            started = time.time()
            try:
                current: Dict[str, Any] = {"agents": self.fetch()}
                backoff = 0.0
            except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as e:
                current = {"error": str(e)}
                backoff = min(max(backoff * 2, 5.0), 60.0)
            if current != last:
                self.events.put((self.sid, current, time.time() - started))
                last = current
            self.stopped.wait(backoff or interval)

    def stop(self) -> None:
        self.stopped.set()


class Source:
    def __init__(self, sid: str, label: str):
        self.sid = sid
        self.label = label
        self.agents: Optional[List[Dict[str, Any]]] = None
        self.state = "connecting"
        self.lost_at: Optional[float] = None
        self.poller: Optional[Poller] = None


class Watcher:
    def __init__(self) -> None:
        self.events: "queue.Queue" = queue.Queue()
        self.sources: Dict[str, Source] = {}
        self.order: List[str] = []
        self.cfg = plugin.load_config()
        self.dev: Optional[LaMetric] = plugin.device(self.cfg)
        self.clock_widget = ""
        self.pushed_icon: Optional[str] = None
        self.dirty_at: Optional[float] = None
        self.force = True
        self.device_state = "unknown"
        self.device_retry_at = 0.0
        self.next_housekeeping = 0.0
        self.housekeeping_failures = 0
        self.running = True

    # --- sources -----------------------------------------------------------

    def add_source(self, sid: str, label: str, machine: str = "") -> None:
        src = Source(sid, label)
        src.poller = Poller(sid, label, self.events, machine)
        src.poller.start()
        self.sources[sid] = src

    def sync_machines(self) -> None:
        machines = [m for m in plugin.herdr_json("machine", "list", "--json") if m.get("enabled", True)]
        order = ["local"] + [m["id"] for m in machines]
        for m in machines:
            if m["id"] not in self.sources:
                log(f"watching machine {m.get('label')} ({m['target']})")
                self.add_source(m["id"], m.get("label") or m["target"], machine=m["id"])
        for sid in [s for s in self.sources if s not in order]:
            log(f"no longer watching {self.sources[sid].label}")
            self.sources.pop(sid).poller.stop()
        if order != self.order:
            self.order = order
            self.mark_dirty()

    def still_wanted(self) -> bool:
        """Exit when the plugin is disabled, removed, or reinstalled elsewhere."""
        result = plugin.herdr_json("plugin", "list", "--plugin", plugin.PLUGIN_ID, "--json")
        entries = result if isinstance(result, list) else result.get("result", {}).get("plugins", [])
        entry = next((p for p in entries if p.get("plugin_id") == plugin.PLUGIN_ID), None)
        if not entry or not entry.get("enabled", False):
            log("plugin disabled or removed")
            return False
        root = entry.get("plugin_root")
        if root and os.path.realpath(root) != str(plugin.ROOT):
            log(f"plugin now lives at {root}; handing over")
            return False
        return True

    def housekeeping(self) -> None:
        self.cfg = plugin.load_config()
        self.dev = plugin.device(self.cfg)
        try:
            if not self.still_wanted():
                self.running = False
                return
            self.sync_machines()
            self.housekeeping_failures = 0
        except (subprocess.SubprocessError, OSError, ValueError, KeyError, AttributeError) as e:
            self.housekeeping_failures += 1
            log(f"housekeeping failed ({self.housekeeping_failures}): {e}")
            if self.housekeeping_failures >= 10:
                log("herdr unreachable for 10 minutes; exiting")
                self.running = False

    def expire_lost(self, now: float) -> None:
        for src in self.sources.values():
            if src.lost_at and src.agents is not None and now - src.lost_at > OFFLINE_GRACE_S:
                src.agents = None
                src.state = "offline"
                self.mark_dirty()

    # --- poll results --------------------------------------------------------

    def handle(self, sid: str, msg: Dict[str, Any], took: float) -> None:
        src = self.sources.get(sid)
        if not src:
            return
        if "agents" in msg:
            agents = sorted(msg["agents"], key=lambda a: plugin.pane_sort_key(a.get("pane_id") or ""))
            self.alert_transitions(src, agents)
            if src.state != "online":
                log(f"[{src.label}] online, {len(agents)} agents ({took:.1f}s)")
            src.agents, src.state, src.lost_at = agents, "online", None
            self.mark_dirty()
        else:
            log(f"[{src.label}] {msg['error']}")
            src.state = "unreachable"
            if src.lost_at is None:
                src.lost_at = time.time()

    def alert_transitions(self, src: Source, agents: List[Dict[str, Any]]) -> None:
        if src.agents is None or not self.dev or not plugin.alerts_enabled():
            return  # no baseline yet, so nothing is a transition
        before = {a.get("pane_id"): a.get("agent_status") for a in src.agents}
        alert_on = {s.strip() for s in self.cfg["ALERT_ON"].split(",") if s.strip()}
        for a in agents:
            status, prev = a.get("agent_status"), before.get(a.get("pane_id"))
            if status in alert_on and prev is not None and prev != status:
                try:
                    self.dev.notify(plugin.alert_frames(status, plugin.label(a)),
                                    self.cfg["ALERT_PRIORITY"], int(self.cfg["ALERT_CYCLES"]))
                except LaMetricError as e:
                    log(f"alert failed: {e}")

    # --- rendering -------------------------------------------------------------

    def mark_dirty(self) -> None:
        if self.dirty_at is None:
            self.dirty_at = time.time()

    def statuses(self) -> List[str]:
        out: List[str] = []
        for sid in self.order:
            src = self.sources.get(sid)
            if src and src.agents:
                out += [a.get("agent_status") or "unknown" for a in src.agents]
        return out

    def render(self, now: float) -> None:
        if self.dirty_at is None or now - self.dirty_at < RENDER_DEBOUNCE_S:
            return
        if now < self.device_retry_at:
            return
        icon = plugin.clock_icon(self.statuses())
        if icon != self.pushed_icon or self.force:
            if not self.dev:
                self.device_state = "not configured"
            else:
                try:
                    if not self.clock_widget:
                        self.clock_widget = self.dev.clock_widget_id()
                    self.dev.set_clockface(icon, self.clock_widget)
                    self.pushed_icon, self.force = icon, False
                    self.device_state = "ok"
                except LaMetricError as e:
                    if self.device_state != "error":
                        log(f"device: {e}")
                    self.device_state = "error"
                    self.clock_widget = ""
                    self.device_retry_at = now + DEVICE_RETRY_S
                    self.write_status(now)
                    return
        self.dirty_at = None
        self.write_status(now)

    def write_status(self, now: float) -> None:
        status = {
            "t": now,
            "pid": os.getpid(),
            "device": self.device_state,
            "sources": [
                {
                    "label": self.sources[sid].label,
                    "state": self.sources[sid].state,
                    "statuses": [a.get("agent_status") for a in self.sources[sid].agents or []],
                }
                for sid in self.order
                if sid in self.sources
            ],
        }
        tmp = plugin.STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(status))
        tmp.replace(plugin.STATUS_FILE)

    # --- main loop -------------------------------------------------------------

    def request_refresh(self, *_: Any) -> None:
        self.force = True
        self.next_housekeeping = 0
        self.device_retry_at = 0
        self.mark_dirty()

    def request_stop(self, *_: Any) -> None:
        self.running = False

    def run(self) -> None:
        signal.signal(signal.SIGUSR1, self.request_refresh)
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        log(f"watcher started, pid {os.getpid()}")
        self.add_source("local", "Local")
        self.order = ["local"]
        self.mark_dirty()
        while self.running:
            now = time.time()
            if now >= self.next_housekeeping:
                self.housekeeping()
                self.next_housekeeping = now + HOUSEKEEPING_S
            self.expire_lost(now)
            try:
                while True:
                    self.handle(*self.events.get(timeout=0.1))
            except queue.Empty:
                pass
            except Exception as e:  # one bad poll result must not kill the watcher
                log(f"error handling poll result: {e!r}")
            self.render(time.time())
        for src in self.sources.values():
            src.poller.stop()
        for src in self.sources.values():
            src.poller.join(timeout=5)  # let in-flight `herdr --machine` calls finish
        log("watcher stopped")


def main() -> int:
    plugin.STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(plugin.LOCK_FILE, "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0  # another watcher is already running
    plugin.PID_FILE.write_text(str(os.getpid()))
    try:
        Watcher().run()
    finally:
        try:
            plugin.PID_FILE.unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
