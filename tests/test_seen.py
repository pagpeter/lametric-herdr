import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from seen import DoneTracker  # noqa: E402


def agent(pane, status, seq, tab="t1", focused=False, completion=None):
    a = {"pane_id": pane, "agent_status": status, "state_change_seq": seq,
         "tab_id": tab, "focused": focused}
    if completion is not None:
        a["completion_seq"] = completion
    return a


def statuses(tracker, agents):
    return [tracker.status(a) for a in agents]


class RemoteTest(unittest.TestCase):
    def test_finished_while_machine_selected_but_server_claims_seen(self):
        # The reported bug: ec2 selected, its tab focused, codex finishes and
        # the remote server says idle (seen). The sidebar shows done.
        t = DoneTracker(trust_server_seen=False)
        t.update([agent("p1", "idle", 1, focused=True)], selected=True)
        t.update([agent("p1", "working", 2, focused=True)], selected=True)
        finished = [agent("p1", "idle", 3, focused=True, completion=3)]
        t.update(finished, selected=True)
        self.assertEqual(statuses(t, finished), ["done"])

    def test_finished_while_viewing_another_machine(self):
        t = DoneTracker(trust_server_seen=False)
        t.update([agent("p1", "working", 1, focused=True)], selected=False)
        finished = [agent("p1", "idle", 2, focused=True)]
        t.update(finished, selected=False)
        self.assertEqual(statuses(t, finished), ["done"])

    def test_switching_to_the_machine_marks_its_focused_tab_seen(self):
        t = DoneTracker(trust_server_seen=False)
        t.update([agent("p1", "working", 1, focused=True)], selected=False)
        finished = [agent("p1", "idle", 2, focused=True)]
        t.update(finished, selected=False)
        t.on_selected(finished)
        self.assertEqual(statuses(t, finished), ["idle"])

    def test_switching_tabs_marks_the_new_tab_seen(self):
        t = DoneTracker(trust_server_seen=False)
        t.update([agent("p1", "working", 1, tab="t1"), agent("p2", "idle", 1, tab="t2", focused=True)], True)
        t.update([agent("p1", "idle", 2, tab="t1"), agent("p2", "idle", 1, tab="t2", focused=True)], True)
        now = [agent("p1", "idle", 2, tab="t1", focused=True), agent("p2", "idle", 1, tab="t2")]
        t.update(now, selected=True)
        self.assertEqual(statuses(t, now), ["idle", "idle"])

    def test_startup_uses_completion_seq(self):
        t = DoneTracker(trust_server_seen=False)
        agents = [agent("p1", "idle", 5, completion=5), agent("p2", "idle", 4)]
        t.update(agents, selected=True)
        self.assertEqual(statuses(t, agents), ["done", "idle"])


class LocalTest(unittest.TestCase):
    def test_finished_while_watching_is_seen(self):
        t = DoneTracker(trust_server_seen=True)
        t.update([agent("p1", "working", 1, focused=True)], selected=True)
        finished = [agent("p1", "idle", 2, focused=True)]
        t.update(finished, selected=True)
        self.assertEqual(statuses(t, finished), ["idle"])

    def test_server_done_until_server_marks_seen(self):
        # Finished while the terminal was unfocused: the local server says done.
        t = DoneTracker(trust_server_seen=True)
        t.update([agent("p1", "working", 1, focused=True)], selected=True)
        done = [agent("p1", "done", 2, focused=True)]
        t.update(done, selected=True)
        self.assertEqual(statuses(t, done), ["done"])
        seen = [agent("p1", "idle", 2, focused=True)]
        t.update(seen, selected=True)
        self.assertEqual(statuses(t, seen), ["idle"])

    def test_server_seen_ignored_while_tui_shows_another_machine(self):
        t = DoneTracker(trust_server_seen=True)
        t.update([agent("p1", "working", 1, focused=True)], selected=False)
        finished = [agent("p1", "idle", 2, focused=True)]
        t.update(finished, selected=False)
        self.assertEqual(statuses(t, finished), ["done"])

    def test_startup_trusts_server_when_selected(self):
        t = DoneTracker(trust_server_seen=True)
        agents = [agent("p1", "idle", 5, completion=5), agent("p2", "done", 7)]
        t.update(agents, selected=True)
        self.assertEqual(statuses(t, agents), ["idle", "done"])


class GeneralTest(unittest.TestCase):
    def test_stale_completion_seq_is_ignored(self):
        t = DoneTracker(trust_server_seen=False)
        agents = [agent("p1", "idle", 6, completion=5)]
        t.update(agents, selected=False)
        self.assertEqual(statuses(t, agents), ["idle"])

    def test_new_work_resets_done(self):
        t = DoneTracker(trust_server_seen=False)
        t.update([agent("p1", "working", 1)], False)
        t.update([agent("p1", "idle", 2)], False)
        again = [agent("p1", "working", 3)]
        t.update(again, False)
        self.assertEqual(statuses(t, again), ["working"])

    def test_closed_panes_are_forgotten(self):
        t = DoneTracker(trust_server_seen=False)
        t.update([agent("p1", "working", 1)], False)
        t.update([agent("p1", "idle", 2)], False)
        t.update([], False)
        self.assertEqual((t.completed, t.acknowledged, t.working, t.server_status), ({}, {}, set(), {}))


if __name__ == "__main__":
    unittest.main()
