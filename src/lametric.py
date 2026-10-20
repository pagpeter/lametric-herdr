"""Minimal client for the LaMetric Time local API (v2)."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any

TIMEOUT_S = 4
CLOCK_APP = "com.lametric.clock"


class LaMetricError(Exception):
    pass


class LaMetric:
    def __init__(self, host: str, api_key: str) -> None:
        self.base = f"http://{host}:8080/api/v2"
        token = base64.b64encode(f"dev:{api_key}".encode()).decode()
        self.auth = f"Basic {token}"
        self._clock_widget = ""

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        url = self.base + path
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"Content-Type": "application/json", "Authorization": self.auth},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raise LaMetricError(f"{method} {url}: HTTP {e.code} {e.read()[:300]!r}") from e
        except (urllib.error.URLError, OSError) as e:
            raise LaMetricError(f"{method} {url}: {e}") from e
        return json.loads(raw) if raw else None

    def device(self) -> Any:
        return self._request("GET", "/device")

    def set_clockface(self, icon: str) -> None:
        """Show `icon` (a data URI) next to the time in the clock app.
        An empty icon restores the plain clock face."""
        if not self._clock_widget:
            widgets = self._request("GET", f"/device/apps/{CLOCK_APP}")["widgets"]
            self._clock_widget = next(iter(widgets))
        params = {"type": "custom", "icon": icon} if icon else {"type": "none"}
        try:
            self._request(
                "POST",
                f"/device/apps/{CLOCK_APP}/widgets/{self._clock_widget}/actions",
                {"id": "clock.clockface", "params": params, "activate": False},
            )
        except LaMetricError:
            self._clock_widget = ""  # look it up again next time
            raise

    def notify(self, frames: list[dict[str, Any]], priority: str = "info", cycles: int = 1) -> None:
        # No "sound" key: notifications stay silent.
        self._request("POST", "/device/notifications", {
            "priority": priority,
            "icon_type": "none",
            "lifeTime": 120000,
            "model": {"cycles": cycles, "frames": frames},
        })
