from __future__ import annotations

from typing import Any

import requests


def send_notification(url: str, title: str, message: str, level: str = "info") -> bool:
    url = str(url or "").strip()
    if not url:
        return False
    payload: dict[str, Any] = {
        "title": title,
        "message": message,
        "level": level,
        "source": "OverwatchBiliDrops",
    }
    response = requests.post(url, json=payload, timeout=8)
    response.raise_for_status()
    return True
