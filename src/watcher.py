"""The background watcher: agent state from every herdr machine -> LaMetric clockface.

- Local agents: `agent.list` over the herdr socket every second.
- Saved machines: `herdr --machine <id> agent list` every 2 seconds. herdr owns
  and reuses the SSH connection, so the plugin holds no credentials and runs
  no code of its own on remote machines.

Polling rather than subscribing: herdr only offers per-pane status
subscriptions, and "done" turns into "idle" without any event when a pane is
viewed.

One watcher runs per user, enforced by an flock held for the process
lifetime. The plugin's hooks start it on demand (`lametric_herdr.py ensure`);
it exits by itself when the plugin is disabled, unlinked, or reinstalled.
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
from typing import Any, Optional

import config
import herdr
import pixels
from lametric import LaMetric, LaMetricError
from seen import DoneTracker

LOCAL_INTERVAL_S = 1.0
REMOTE_INTERVAL_S = 2.0
REMOTE_TIMEOUT_S = 120  # the first call to a machine may have to establish SSH
OFFLINE_GRACE_S = 60  # keep a lost machine's agents this long before dropping them
RENDER_DEBOUNCE_S = 0.2
DEVICE_RETRY_S = 15
HOUSEKEEPING_S = 15  # also how quickly an uninstall is noticed
GIVE_UP_AFTER_S = 600  # exit when herdr stays unreachable this long

LOCK_FILE = config.STATE_DIR / "watcher.lock"
PID_FILE = config.STATE_DIR / "watcher.pid"
STATUS_FILE = config.STATE_DIR / "status.json"
LOG_FILE = config.STATE_DIR / "watcher.log"
LOG_MAX_BYTES = 1_000_000


# --- process management (used by the CLI) ---------------------------------


def running_pid() -> Optional[int]:
    """PID of the running watcher, or None. A free lock means none is running."""
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "a") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            try:
                return int(PID_FILE.read_text())
            except (OSError, ValueError):
                return -1  # running, pid not written yet
        fcntl.flock(fh, fcntl.LOCK_UN)
    return None


def spawn() -> None:
    """Start a detached watcher. If one is already running, the new one exits."""
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_MAX_BYTES:
        LOG_FILE.replace(LOG_FILE.with_suffix(".log.1"))
    with open(LOG_FILE, "a") as log_fh:
        subprocess.Popen(
            [sys.executable, str(config.SRC / "watcher.py")],
            cwd=str(config.PLUGIN_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=log_fh,
            start_new_session=True,  # outlive the hook process that started it
        )


def send(sig: int) -> bool:
    pid = running_pid()
    if not pid or pid < 0:
        return False
    try:
        os.kill(pid, sig)
        return True
    except OSError:
        return False


def code_version() -> float:
    """Changes when the plugin's code is replaced in place, as older herdr reinstalls do."""
    return max((f.stat().st_mtime for f in config.SRC.glob("*.py")), default=0.0)


# --- the watcher ------------------------------------------------------------


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


class Poller(threading.Thread):
    """Polls one herdr server and queues (source id, result) when it changes."""

    def __init__(self, sid: str, results: queue.Queue, machine_id: str = "") -> None:
        super().__init__(daemon=True, name=f"poll-{sid}")
        self.sid = sid
        self.results = results
        self.machine_id = machine_id
        self.stopped = threading.Event()

    def fetch(self) -> list[herdr.Agent]:
        if self.machine_id:
            return herdr.machine_agents(self.machine_id, REMOTE_TIMEOUT_S)
        return herdr.local_agents(herdr.local_socket_path())

    def interval(self) -> float:
        if not self.machine_id:
            return LOCAL_INTERVAL_S
        try:
            return max(float(config.load()["REMOTE_POLL_SECONDS"]), 1.0)
        except ValueError:
            return REMOTE_INTERVAL_S

    def run(self) -> None:
        interval = self.interval()
        last: Any = None
        backoff = 0.0
        while not self.stopped.is_set():
            started = time.time()
            try:
                result: dict[str, Any] = {"agents": self.fetch()}
                backoff = 0.0
            except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as e:
                result = {"error": str(e)}
                backoff = min(max(backoff * 2, 5.0), 60.0)
            if result != last:
                result["took"] = time.time() - started
                self.results.put((self.sid, result))
                last = {k: v for k, v in result.items() if k != "took"}
            self.stopped.wait(backoff or interval)


class Source:
    """What the watcher knows about one herdr server."""

    def __init__(self, sid: str, label: str, poller: Poller) -> None:
        self.sid = sid
        self.label = label
        self.poller = poller
        self.agents: Optional[list[herdr.Agent]] = None
        self.state = "connecting"
        self.lost_at: Optional[float] = None
        self.tracker = DoneTracker(trust_server_seen=sid == "local")

    def statuses(self) -> dict[str, str]:
        """pane -> status as the herdr TUI shows it (done vs idle by what you have seen)."""
        return {a["pane_id"]: self.tracker.status(a) for a in self.agents or []}


class Watcher:
    def __init__(self) -> None:
        self.results: queue.Queue = queue.Queue()
        self.sources: dict[str, Source] = {}
        self.order: list[str] = []  # local first, then `herdr machine list` order
        self.cfg = config.load()
        self.dev = self.connect_device()
        self.pushed_icon: Optional[str] = None
        self.dirty_since: Optional[float] = None
        self.force = True
        self.device_state = "unknown"
        self.device_retry_at = 0.0
        self.next_housekeeping = 0.0
        self.failing_since: Optional[float] = None
        self.selection_mtime = 0.0
        self.selected = "local"  # the machine the herdr TUI is showing
        self.code_version = code_version()
        self.exit_action = ""  # "restart" or "handover" once the loop ends
        self.running = True

    def connect_device(self) -> Optional[LaMetric]:
        if not self.cfg["LAMETRIC_HOST"] or not self.cfg["LAMETRIC_API_KEY"]:
            return None
        return LaMetric(self.cfg["LAMETRIC_HOST"], self.cfg["LAMETRIC_API_KEY"])

    # --- sources -----------------------------------------------------------

    def add_source(self, sid: str, label: str, machine_id: str = "") -> None:
        poller = Poller(sid, self.results, machine_id)
        self.sources[sid] = Source(sid, label, poller)
        poller.start()

    def sync_machines(self) -> None:
        machines = [m for m in herdr.cli_json("machine", "list", "--json") if m.get("enabled", True)]
        for m in machines:
            if m["id"] not in self.sources:
                log(f"watching machine {m.get('label')} ({m['target']})")
                self.add_source(m["id"], m.get("label") or m["target"], machine_id=m["id"])
        order = ["local"] + [m["id"] for m in machines]
        for sid in [s for s in self.sources if s not in order]:
            log(f"no longer watching {self.sources[sid].label}")
            self.sources.pop(sid).poller.stopped.set()
        if order != self.order:
            self.order = order
            self.mark_dirty()

    def installation(self) -> str:
        """"current", "removed" (uninstalled or disabled), or "moved" (the
        plugin was reinstalled or linked from another directory)."""
        plugins = herdr.cli_json("plugin", "list", "--plugin", config.PLUGIN_ID, "--json")["result"]["plugins"]
        entry = next((p for p in plugins if p.get("plugin_id") == config.PLUGIN_ID), None)
        if not entry or not entry.get("enabled"):
            return "removed"
        root = entry.get("plugin_root")
        if root and os.path.realpath(root) != str(config.PLUGIN_ROOT):
            return "moved"
        return "current"

    def housekeeping(self, now: float) -> None:
        self.cfg = config.load()
        self.dev = self.connect_device()
        try:
            installation = self.installation()
            if installation == "removed":
                log("plugin disabled or removed")
                self.reset_clockface()
                self.running = False
                return
            if installation == "moved":
                log("plugin installed elsewhere; handing over")
                self.exit_action = "handover"
                self.running = False
                return
            if code_version() != self.code_version:
                log("plugin code changed; restarting")
                self.exit_action = "restart"
                self.running = False
                return
            self.sync_machines()
            self.failing_since = None
        except (subprocess.SubprocessError, OSError, ValueError, KeyError) as e:
            self.failing_since = self.failing_since or now
            log(f"herdr CLI failed: {e}")
            if now - self.failing_since >= GIVE_UP_AFTER_S:
                log("herdr unreachable for 10 minutes; exiting")
                self.running = False

    def check_selection(self) -> None:
        """Follow the machine the TUI shows; its focused tab counts as seen."""
        try:
            mtime = os.stat(herdr.selection_file()).st_mtime
        except OSError:
            mtime = 0.0
        if mtime == self.selection_mtime:
            return
        self.selection_mtime = mtime
        selected = herdr.selected_machine() or "local"
        if selected != self.selected:
            self.selected = selected
            src = self.sources.get(selected)
            if src and src.agents:
                src.tracker.on_selected(src.agents)
                self.mark_dirty()

    def expire_lost(self, now: float) -> None:
        for src in self.sources.values():
            if src.lost_at and src.agents is not None and now - src.lost_at > OFFLINE_GRACE_S:
                src.agents, src.state = None, "offline"
                self.mark_dirty()

    # --- poll results --------------------------------------------------------

    def handle(self, sid: str, result: dict[str, Any]) -> None:
        src = self.sources.get(sid)
        if not src:
            return
        if "error" in result:
            log(f"[{src.label}] {result['error']}")
            src.state = "unreachable"
            src.lost_at = src.lost_at or time.time()
            return
        agents = result["agents"]
        before = src.statuses() if src.agents is not None else None
        src.tracker.update(agents, selected=sid == self.selected)
        if src.state != "online":
            log(f"[{src.label}] online, {len(agents)} agents ({result['took']:.1f}s)")
        src.agents, src.state, src.lost_at = agents, "online", None
        if before is not None:
            self.alert_transitions(before, src)
        self.mark_dirty()

    def alert_transitions(self, before: dict[str, str], src: Source) -> None:
        if not self.dev or not config.alerts_enabled():
            return
        wanted = config.alert_statuses(self.cfg)
        after = src.statuses()
        for a in src.agents or []:
            status, prev = after[a["pane_id"]], before.get(a["pane_id"])
            if status in wanted and prev is not None and prev != status:
                try:
                    self.dev.notify(pixels.alert_frames(status, herdr.label(a)),
                                    self.cfg["ALERT_PRIORITY"], int(self.cfg["ALERT_CYCLES"]))
                except (LaMetricError, ValueError) as e:
                    log(f"alert failed: {e}")

    # --- device ------------------------------------------------------------------

    def mark_dirty(self) -> None:
        self.dirty_since = self.dirty_since or time.time()

    def statuses(self) -> list[str]:
        return [
            status
            for sid in self.order
            if sid in self.sources
            for status in self.sources[sid].statuses().values()
        ]

    def render(self, now: float) -> None:
        if self.dirty_since is None or now - self.dirty_since < RENDER_DEBOUNCE_S:
            return
        if now < self.device_retry_at:
            return
        icon = pixels.clock_icon(self.statuses())
        if icon != self.pushed_icon or self.force:
            if not self.dev:
                self.device_state = "not configured"
            else:
                try:
                    self.dev.set_clockface(icon)
                    self.pushed_icon, self.force, self.device_state = icon, False, "ok"
                except LaMetricError as e:
                    if self.device_state != "error":
                        log(f"device: {e}")
                    self.device_state = "error"
                    self.device_retry_at = now + DEVICE_RETRY_S
                    self.write_status(now)
                    return
        self.dirty_since = None
        self.write_status(now)

    def reset_clockface(self) -> None:
        if not self.dev:
            return
        try:
            self.dev.set_clockface("")
            log("clockface reset")
        except LaMetricError as e:
            log(f"could not reset clockface: {e}")

    def write_status(self, now: float) -> None:
        status = {
            "t": now,
            "pid": os.getpid(),
            "device": self.device_state,
            "sources": [
                {
                    "label": src.label,
                    "state": src.state,
                    "statuses": list(src.statuses().values()),
                }
                for src in (self.sources[sid] for sid in self.order if sid in self.sources)
            ],
        }
        tmp = STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(status))
        tmp.replace(STATUS_FILE)

    # --- main loop -------------------------------------------------------------

    def on_refresh(self, *_: Any) -> None:
        self.force = True
        self.next_housekeeping = 0
        self.device_retry_at = 0
        self.mark_dirty()

    def on_stop(self, *_: Any) -> None:
        self.running = False

    def run(self) -> None:
        signal.signal(signal.SIGUSR1, self.on_refresh)
        signal.signal(signal.SIGTERM, self.on_stop)
        signal.signal(signal.SIGINT, self.on_stop)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        log(f"watcher started, pid {os.getpid()}")
        self.add_source("local", "Local")
        self.order = ["local"]
        self.mark_dirty()

        while self.running:
            now = time.time()
            if now >= self.next_housekeeping:
                self.housekeeping(now)
                self.next_housekeeping = now + HOUSEKEEPING_S
            self.check_selection()
            self.expire_lost(now)
            try:
                while True:
                    self.handle(*self.results.get(timeout=0.1))
            except queue.Empty:
                pass
            except Exception as e:  # one bad poll result must not kill the watcher
                log(f"error handling poll result: {e!r}")
            self.render(time.time())

        for src in self.sources.values():
            src.poller.stopped.set()
        for src in self.sources.values():
            src.poller.join(timeout=5)  # let in-flight `herdr --machine` calls finish
        log("watcher stopped")


def main() -> int:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(LOCK_FILE, "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0  # another watcher is already running
    PID_FILE.write_text(str(os.getpid()))
    w = Watcher()
    try:
        w.run()
    finally:
        PID_FILE.unlink(missing_ok=True)
        lock.close()  # release before a replacement tries to take it
    if w.exit_action == "restart":
        spawn()
    elif w.exit_action == "handover":
        # Start the new installation's watcher through herdr, which runs the
        # new copy's code with its own environment.
        subprocess.run([herdr.BIN, "plugin", "action", "invoke", f"{config.PLUGIN_ID}.refresh"],
                       capture_output=True, timeout=15)
    return 0


if __name__ == "__main__":
    sys.exit(main())
