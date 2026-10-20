import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config  # noqa: E402
import herdr  # noqa: E402


class HerdrTest(unittest.TestCase):
    def test_pane_ids_sort_as_base36(self):
        ids = ["wE:p1", "w10:p2", "w4:p1", "wD:p1", "w4:p10", "w4:p2"]
        self.assertEqual(
            sorted(ids, key=herdr.pane_sort_key),
            ["w4:p1", "w4:p2", "w4:p10", "wD:p1", "wE:p1", "w10:p2"],
        )

    def test_trim_keeps_known_fields_in_pane_order(self):
        agents = herdr._trim([
            {"pane_id": "wE:p1", "agent_status": "idle", "revision": 3},
            {"pane_id": "w4:p1", "agent_status": "done", "focused": True},
        ])
        self.assertEqual([a["pane_id"] for a in agents], ["w4:p1", "wE:p1"])
        self.assertEqual(set(agents[0]), set(herdr.AGENT_FIELDS))

    def test_label_uses_title(self):
        self.assertEqual(herdr.label({"terminal_title_stripped": "Fix the parser"}), "Fix the parser")

    def test_label_falls_back_to_directory_for_generic_titles(self):
        agent = {"terminal_title_stripped": "Claude Code", "agent": "claude",
                 "foreground_cwd": "/home/me/src/my-project/"}
        self.assertEqual(herdr.label(agent), "my-project")


class ConfigTest(unittest.TestCase):
    def test_env_file_overrides_defaults_and_environment_overrides_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "# comment\n"
                "LAMETRIC_HOST=10.0.0.5\n"
                "LAMETRIC_API_KEY='abc'\n"
                "ALERT_ON=blocked\n"
            )
            with mock.patch.object(config, "ENV_FILE", env_file), \
                    mock.patch.dict(os.environ, {"ALERT_ON": "done"}):
                cfg = config.load()
        self.assertEqual(cfg["LAMETRIC_HOST"], "10.0.0.5")
        self.assertEqual(cfg["LAMETRIC_API_KEY"], "abc")
        self.assertEqual(cfg["ALERT_ON"], "done")
        self.assertEqual(cfg["ALERT_CYCLES"], "1")
        self.assertEqual(config.alert_statuses(cfg), {"done"})

    def test_alerts_flag_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(config, "STATE_DIR", Path(tmp)), \
                    mock.patch.object(config, "SETTINGS_FILE", Path(tmp) / "settings.json"):
                self.assertTrue(config.alerts_enabled())
                config.set_alerts_enabled(False)
                self.assertFalse(config.alerts_enabled())


if __name__ == "__main__":
    unittest.main()
