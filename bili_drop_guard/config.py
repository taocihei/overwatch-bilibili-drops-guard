from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


APP_DIR = Path.home() / "AppData" / "Roaming" / "OverwatchBiliDrops"
CONFIG_PATH = APP_DIR / "config.json"
LEGACY_DEFAULT_TASK_IDS = (
    "6ERAcwloghvqrb00,6ERAcwloghvqnk00,6ERAcwloghvql500,"
    "6ERAcwloghvq2v00,6ERAxwloghv0cj00,6ERAcwloghvwuf00,"
    "6ERAcwloghvqks00,6ERAcwloghvqka00,6ERAcwloghvwbs00"
)
DEFAULT_TASK_IDS = ""
MIN_CHECK_INTERVAL = 10
MAX_CHECK_INTERVAL = 600
MAX_WATCH_WINDOWS = 20


@dataclass
class AppConfig:
    cookie: str = ""
    room_id: str = ""
    check_interval: int = 60
    auto_claim: bool = True
    task_ids: str = DEFAULT_TASK_IDS
    watch_threads: int = 1


def load_config() -> AppConfig:
    if not CONFIG_PATH.exists():
        return AppConfig()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if "watch_threads" not in data and "claim_threads" in data:
            data["watch_threads"] = data["claim_threads"]
        data.pop("claim_threads", None)
        if data.get("task_ids") == LEGACY_DEFAULT_TASK_IDS:
            data["task_ids"] = DEFAULT_TASK_IDS
        return sanitize_config(AppConfig(**{**asdict(AppConfig()), **data}))
    except Exception:
        return AppConfig()


def save_config(config: AppConfig) -> None:
    config = sanitize_config(config)
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def sanitize_config(config: AppConfig) -> AppConfig:
    return AppConfig(
        cookie=str(config.cookie or ""),
        room_id=str(config.room_id or ""),
        check_interval=_coerce_int(config.check_interval, 60, MIN_CHECK_INTERVAL, MAX_CHECK_INTERVAL),
        auto_claim=_coerce_bool(config.auto_claim, True),
        task_ids=str(config.task_ids or ""),
        watch_threads=_coerce_int(config.watch_threads, 1, 1, MAX_WATCH_WINDOWS),
    )


def _coerce_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value or default)
    except (TypeError, ValueError):
        number = default
    return min(max(number, minimum), maximum)


def _coerce_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default
