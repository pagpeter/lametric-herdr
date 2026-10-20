"""Which finished agents you have not looked at yet ("done" vs "idle").

The herdr sidebar tracks viewed completions in the TUI itself, and the API
does not expose that. The local server's `seen` flag follows your terminal
focus but not which machine the TUI shows; a remote server's flag is not
reliable at all (it counts its active tab as seen even when you never looked).
So:

- An agent completes when it goes from working to idle, the server says done,
  or (herdr >= 0.9.5) its `completion_seq` matches `state_change_seq`.
- Local: seen when the server says so while the TUI shows Local.
- Remote: seen only once you navigate to it after it finished, by switching
  to that machine or to the pane's tab.
- Either: switching to the machine or tab marks what is in view as seen.

Seen is tracked per completion with herdr's per-pane `state_change_seq`.
"""

from __future__ import annotations

from typing import Any, Optional

Agent = dict[str, Any]


class DoneTracker:
    def __init__(self, trust_server_seen: bool) -> None:
        self.trust_server_seen = trust_server_seen  # true for the local server
        self.started = False
        self.working: set[str] = set()
        self.completed: dict[str, int] = {}  # pane -> seq of its completion
        self.acknowledged: dict[str, int] = {}  # pane -> highest seq seen
        self.server_status: dict[str, str] = {}
        self.focused_tab: Optional[str] = None

    def update(self, agents: list[Agent], selected: bool) -> None:
        """Apply a poll. `selected`: the TUI is showing this machine."""
        panes = {a["pane_id"] for a in agents}
        for tracked in (self.completed, self.acknowledged, self.server_status):
            for pane in [p for p in tracked if p not in panes]:
                del tracked[pane]
        self.working &= panes
        focused_tab = next((a.get("tab_id") for a in agents if a.get("focused")), None)

        if not self.started:
            # On the machine the TUI shows, trust the server's seen flag;
            # elsewhere count server-known completions as unseen.
            self.started = True
            self.focused_tab = focused_tab
            trust = self.trust_server_seen and selected
            for a in agents:
                finished = a["agent_status"] == "done" or (not trust and _server_completed(a))
                if not finished:
                    self.acknowledged[a["pane_id"]] = _seq(a)

        for a in agents:
            pane, status, seq = a["pane_id"], a["agent_status"], _seq(a)
            previous = self.server_status.get(pane)
            self.server_status[pane] = status
            if status == "working":
                self.working.add(pane)
                self.completed.pop(pane, None)
            elif status == "blocked":
                self.completed.pop(pane, None)
            elif status in ("idle", "done"):
                finished_work = pane in self.working
                self.working.discard(pane)
                trust = self.trust_server_seen and selected
                if self.completed.get(pane) == seq:
                    if trust and previous == "done" and status == "idle":
                        self.acknowledge_pane(a)  # the server saw you view it
                elif finished_work or status == "done" or _server_completed(a):
                    self.completed[pane] = seq
                    if trust and status == "idle":
                        self.acknowledge_pane(a)  # finished while you watched
                else:
                    self.completed.pop(pane, None)
            else:
                self.working.discard(pane)
                self.completed.pop(pane, None)

        if selected and focused_tab != self.focused_tab:
            self.acknowledge_tab(agents, focused_tab)  # you switched tabs
        self.focused_tab = focused_tab

    def on_selected(self, agents: list[Agent]) -> None:
        """The TUI switched to this machine: its focused tab is now in view."""
        self.acknowledge_tab(agents, next((a.get("tab_id") for a in agents if a.get("focused")), None))

    def acknowledge_tab(self, agents: list[Agent], tab: Optional[str]) -> None:
        if tab is not None:
            for a in agents:
                if a.get("tab_id") == tab:
                    self.acknowledge_pane(a)

    def acknowledge_pane(self, agent: Agent) -> None:
        pane = agent["pane_id"]
        self.acknowledged[pane] = max(self.acknowledged.get(pane, -1), _seq(agent))

    def status(self, agent: Agent) -> str:
        status = agent["agent_status"]
        if status not in ("idle", "done"):
            return status
        pane = agent["pane_id"]
        unseen = pane in self.completed and self.acknowledged.get(pane, -1) < self.completed[pane]
        return "done" if unseen else "idle"


def _seq(agent: Agent) -> int:
    return int(agent.get("state_change_seq") or 0)


def _server_completed(agent: Agent) -> bool:
    """herdr >= 0.9.5 reports whether the current idle period followed real work."""
    completion = agent.get("completion_seq")
    return completion is not None and completion == _seq(agent)
