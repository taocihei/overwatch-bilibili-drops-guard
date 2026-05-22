from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .bilibili import normalize_room_id


APP_DIR = Path.home() / "AppData" / "Roaming" / "OverwatchBiliDrops"
CONFIG_PATH = APP_DIR / "config.json"
LEGACY_DEFAULT_TASK_IDS = (
    "6ERAcwloghvqrb00,6ERAcwloghvqnk00,6ERAcwloghvql500,"
    "6ERAcwloghvq2v00,6ERAxwloghv0cj00,6ERAcwloghvwuf00,"
    "6ERAcwloghvqks00,6ERAcwloghvqka00,6ERAcwloghvwbs00"
)
DEFAULT_TASK_IDS = ""
DEFAULT_ROOM_ID = "23612045"
MIN_CHECK_INTERVAL = 10
MAX_CHECK_INTERVAL = 600
MAX_WATCH_WINDOWS = 20
DEFAULT_CHECK_INTERVAL = 10
LEGACY_DEFAULT_CHECK_INTERVAL = 60
CONFIG_VERSION = 2


@dataclass
class AppConfig:
    cookie: str = ""
    room_id: str = DEFAULT_ROOM_ID
    check_interval: int = DEFAULT_CHECK_INTERVAL
    auto_claim: bool = True
    task_ids: str = DEFAULT_TASK_IDS
    watch_threads: int = 1
    config_version: int = CONFIG_VERSION


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
        config_version = _coerce_int(data.get("config_version"), 1, 1, CONFIG_VERSION)
        if config_version < 2 and data.get("check_interval") == LEGACY_DEFAULT_CHECK_INTERVAL:
            data["check_interval"] = DEFAULT_CHECK_INTERVAL
        data["config_version"] = CONFIG_VERSION
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
    room_id = normalize_room_id(str(config.room_id or "")) or DEFAULT_ROOM_ID
    return AppConfig(
        cookie=str(config.cookie or ""),
        room_id=room_id,
        check_interval=_coerce_int(config.check_interval, DEFAULT_CHECK_INTERVAL, MIN_CHECK_INTERVAL, MAX_CHECK_INTERVAL),
        auto_claim=_coerce_bool(config.auto_claim, True),
        task_ids=str(config.task_ids or ""),
        watch_threads=_coerce_int(config.watch_threads, 1, 1, MAX_WATCH_WINDOWS),
        config_version=CONFIG_VERSION,
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
