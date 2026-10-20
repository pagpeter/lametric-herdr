"""Plugin paths, user config, and small persisted settings."""

from __future__ import annotations

import json
import os
from pathlib import Path

SRC = Path(__file__).resolve().parent
PLUGIN_ROOT = SRC.parent
PLUGIN_ID = os.environ.get("HERDR_PLUGIN_ID") or "lametric-herdr"

# herdr provides these to plugin commands; the fallbacks keep the scripts
# runnable from a checkout without herdr.
CONFIG_DIR = Path(os.environ.get("HERDR_PLUGIN_CONFIG_DIR") or PLUGIN_ROOT / ".local" / "config")
STATE_DIR = Path(os.environ.get("HERDR_PLUGIN_STATE_DIR") or PLUGIN_ROOT / ".local" / "state")

ENV_FILE = CONFIG_DIR / ".env"
SETTINGS_FILE = STATE_DIR / "settings.json"

DEFAULTS = {
    "LAMETRIC_HOST": "",
    "LAMETRIC_API_KEY": "",
    "ALERT_ON": "blocked,done",
    "ALERT_PRIORITY": "info",
    "ALERT_CYCLES": "1",
    "REMOTE_POLL_SECONDS": "",
}


def load() -> dict[str, str]:
    """Defaults, overridden by the plugin's .env file, overridden by the environment."""
    cfg = dict(DEFAULTS)
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                cfg[key.strip()] = value.strip().strip("'\"")
    for key in DEFAULTS:
        if os.environ.get(key):
            cfg[key] = os.environ[key]
    return cfg


def alert_statuses(cfg: dict[str, str]) -> set[str]:
    return {s.strip() for s in cfg["ALERT_ON"].split(",") if s.strip()}


def alerts_enabled() -> bool:
    try:
        return bool(json.loads(SETTINGS_FILE.read_text()).get("alerts", True))
    except (OSError, ValueError):
        return True


def set_alerts_enabled(enabled: bool) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps({"alerts": enabled}))
