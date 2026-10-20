"""Which finished agents you have not looked at yet ("done" vs "idle").

herdr's API reports a server-side `seen` flag, but the TUI tracks viewed
completions itself, per machine, and that is what its sidebar shows. A remote
server, for example, counts a pane in its own active tab as seen even while
the TUI is showing another machine. This mirrors the TUI's logic
(herdr src/client/shell/endpoint_agent_state.rs):

- an agent completes when it goes from working to idle, or the server says done;
- it stays done until its pane has been on screen at or after that change,
  tracked with herdr's per-pane `state_change_seq`.

"On screen" is approximated as: its machine is the one selected in the TUI,
and the pane is in that machine's focused tab.
"""

from __future__ import annotations

from typing import Any

Agent = dict[str, Any]


class DoneTracker:
    def __init__(self) -> None:
        self.started = False
        self.working: set[str] = set()
        self.completed: dict[str, int] = {}  # pane -> seq of the completion
        self.acknowledged: dict[str, int] = {}  # pane -> highest seq seen on screen

    def update(self, agents: list[Agent], on_screen: bool) -> None:
        panes = {a["pane_id"] for a in agents}
        for tracked in (self.completed, self.acknowledged):
            for pane in [p for p in tracked if p not in panes]:
                del tracked[pane]
        self.working &= panes

        if not self.started:
            # Like the TUI on connect: whatever is already finished counts as
            # seen unless the server itself still reports it as done.
            self.started = True
            for a in agents:
                if a["agent_status"] != "done":
                    self.acknowledged[a["pane_id"]] = _seq(a)

        for a in agents:
            pane, status = a["pane_id"], a["agent_status"]
            if status == "working":
                self.working.add(pane)
                self.completed.pop(pane, None)
            elif status == "blocked":
                self.completed.pop(pane, None)
            elif status in ("idle", "done"):
                finished_work = pane in self.working
                self.working.discard(pane)
                if finished_work or status == "done" or self.completed.get(pane) == _seq(a):
                    self.completed[pane] = _seq(a)
                else:
                    self.completed.pop(pane, None)
            else:
                self.working.discard(pane)
                self.completed.pop(pane, None)

        if on_screen:
            self.acknowledge(agents)

    def acknowledge(self, agents: list[Agent]) -> None:
        """Mark the panes of the focused tab as seen."""
        focused_tab = next((a.get("tab_id") for a in agents if a.get("focused")), None)
        if focused_tab is None:
            return
        for a in agents:
            if a.get("tab_id") == focused_tab:
                pane = a["pane_id"]
                self.acknowledged[pane] = max(self.acknowledged.get(pane, -1), _seq(a))

    def status(self, agent: Agent) -> str:
        status = agent["agent_status"]
        if status not in ("idle", "done"):
            return status
        pane = agent["pane_id"]
        unseen = pane in self.completed and self.acknowledged.get(pane, -1) < self.completed[pane]
        return "done" if unseen else "idle"


def _seq(agent: Agent) -> int:
    return int(agent.get("state_change_seq") or 0)
