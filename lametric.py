"""Minimal client for the LaMetric Time local API (v2)."""

from __future__ import annotations

import base64
import json
import ssl
import urllib.error
import urllib.request
from typing import Any, Optional, Tuple

TIMEOUT_S = 4


class LaMetricError(Exception):
    pass


class LaMetric:
    def __init__(self, host: str, api_key: str) -> None:
        self.host = host
        self.api_key = api_key

    def _request(self, method: str, url: str, body: Any = None) -> Tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        token = base64.b64encode(f"dev:{self.api_key}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
        # Port 4343 uses a self-signed device certificate.
        ctx = ssl._create_unverified_context() if url.startswith("https") else None
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=ctx) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise LaMetricError(f"{method} {url}: HTTP {e.code} {e.read()[:300]!r}") from e
        except (urllib.error.URLError, OSError) as e:
            raise LaMetricError(f"{method} {url}: {e}") from e

    def _api(self, path: str) -> str:
        return f"http://{self.host}:8080/api/v2{path}"

    def device(self) -> Any:
        return self._request("GET", self._api("/device"))[1]

    def clock_widget_id(self) -> str:
        apps = self._request("GET", self._api("/device/apps/com.lametric.clock"))[1]
        return next(iter(apps["widgets"]))

    def set_clockface(self, icon: str, widget_id: str = "", activate: bool = False) -> None:
        """Replace the clock app's icon; the clock keeps drawing the time next to it.
        An empty icon resets the clock to its plain face."""
        widget_id = widget_id or self.clock_widget_id()
        params = {"type": "custom", "icon": icon} if icon else {"type": "none"}
        body = {"id": "clock.clockface", "params": params, "activate": activate}
        path = f"/device/apps/com.lametric.clock/widgets/{widget_id}/actions"
        self._request("POST", self._api(path), body)

    def notify(self, frames: list, priority: str = "info", cycles: int = 1) -> Optional[Any]:
        # No "sound" key: notifications stay silent.
        body = {
            "priority": priority,
            "icon_type": "none",
            "lifeTime": 120000,
            "model": {"cycles": cycles, "frames": frames},
        }
        return self._request("POST", self._api("/device/notifications"), body)[1]
