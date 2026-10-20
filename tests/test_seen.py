import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from seen import DoneTracker  # noqa: E402


def agent(pane, status, seq, tab="w1:t1", focused=False):
    return {"pane_id": pane, "agent_status": status, "state_change_seq": seq,
            "tab_id": tab, "focused": focused}


class DoneTrackerTest(unittest.TestCase):
    def statuses(self, tracker, agents):
        return [tracker.status(a) for a in agents]

    def test_finished_off_screen_stays_done_even_if_server_says_idle(self):
        # The remote server marks its active tab seen while the TUI shows
        # another machine; the TUI (and we) still show it as done.
        t = DoneTracker()
        t.update([agent("p1", "working", 1, focused=True)], on_screen=False)
        finished = [agent("p1", "idle", 2, focused=True)]
        t.update(finished, on_screen=False)
        self.assertEqual(self.statuses(t, finished), ["done"])

    def test_viewing_the_tab_clears_done(self):
        t = DoneTracker()
        t.update([agent("p1", "working", 1)], on_screen=False)
        finished = [agent("p1", "idle", 2, focused=True)]
        t.update(finished, on_screen=False)
        t.update(finished, on_screen=True)
        self.assertEqual(self.statuses(t, finished), ["idle"])

    def test_finishing_while_on_screen_is_never_done(self):
        t = DoneTracker()
        t.update([agent("p1", "working", 1, focused=True)], on_screen=True)
        finished = [agent("p1", "idle", 2, focused=True)]
        t.update(finished, on_screen=True)
        self.assertEqual(self.statuses(t, finished), ["idle"])

    def test_only_the_focused_tab_counts_as_seen(self):
        t = DoneTracker()
        t.update([agent("p1", "working", 1, tab="t1"), agent("p2", "working", 1, tab="t2")], False)
        after = [agent("p1", "idle", 2, tab="t1", focused=True), agent("p2", "idle", 2, tab="t2")]
        t.update(after, on_screen=True)
        self.assertEqual(self.statuses(t, after), ["idle", "done"])

    def test_startup_counts_existing_idle_as_seen_but_keeps_server_done(self):
        t = DoneTracker()
        agents = [agent("p1", "idle", 5), agent("p2", "done", 7)]
        t.update(agents, on_screen=False)
        self.assertEqual(self.statuses(t, agents), ["idle", "done"])

    def test_new_work_resets_done(self):
        t = DoneTracker()
        t.update([agent("p1", "working", 1)], False)
        t.update([agent("p1", "idle", 2)], False)
        again = [agent("p1", "working", 3)]
        t.update(again, False)
        self.assertEqual(self.statuses(t, again), ["working"])

    def test_closed_panes_are_forgotten(self):
        t = DoneTracker()
        t.update([agent("p1", "working", 1)], False)
        t.update([agent("p1", "idle", 2)], False)
        t.update([], False)
        self.assertEqual((t.completed, t.acknowledged, t.working), ({}, {}, set()))


if __name__ == "__main__":
    unittest.main()
