from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
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
MAX_WATCH_THREADS = 100
DEFAULT_CHECK_INTERVAL = 10
LEGACY_DEFAULT_CHECK_INTERVAL = 60
CONFIG_VERSION = 3


@dataclass
class AccountProfile:
    name: str = "默认账号"
    cookie: str = ""


@dataclass
class AppConfig:
    cookie: str = ""
    account_name: str = "默认账号"
    accounts: list[AccountProfile] = field(default_factory=list)
    room_id: str = DEFAULT_ROOM_ID
    check_interval: int = DEFAULT_CHECK_INTERVAL
    auto_claim: bool = True
    task_ids: str = DEFAULT_TASK_IDS
    watch_threads: int = 1
    notify_url: str = ""
    active_accounts: list[str] = field(default_factory=list)
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
        if config_version < 3 and data.get("cookie") and not data.get("accounts"):
            data["accounts"] = [{"name": data.get("account_name") or "默认账号", "cookie": data.get("cookie")}]
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
    accounts = _sanitize_accounts(config.accounts, str(config.cookie or ""), str(config.account_name or "默认账号"))
    account_name = str(config.account_name or "").strip() or (accounts[0].name if accounts else "默认账号")
    active_cookie = str(config.cookie or "")
    for account in accounts:
        if account.name == account_name:
            active_cookie = account.cookie
            break
    known_names = {account.name for account in accounts}
    active_accounts = [
        name for name in (config.active_accounts or [])
        if isinstance(name, str) and name in known_names
    ]
    return AppConfig(
        cookie=active_cookie,
        account_name=account_name,
        accounts=accounts,
        room_id=room_id,
        check_interval=_coerce_int(config.check_interval, DEFAULT_CHECK_INTERVAL, MIN_CHECK_INTERVAL, MAX_CHECK_INTERVAL),
        auto_claim=_coerce_bool(config.auto_claim, True),
        task_ids=str(config.task_ids or ""),
        watch_threads=_coerce_int(config.watch_threads, 1, 1, MAX_WATCH_THREADS),
        notify_url=str(config.notify_url or "").strip(),
        active_accounts=active_accounts,
        config_version=CONFIG_VERSION,
    )


def _sanitize_accounts(value: object, fallback_cookie: str, fallback_name: str) -> list[AccountProfile]:
    accounts: list[AccountProfile] = []
    seen: set[str] = set()
    if isinstance(value, list):
        for item in value:
            if isinstance(item, AccountProfile):
                name = item.name
                cookie = item.cookie
            elif isinstance(item, dict):
                name = str(item.get("name") or "")
                cookie = str(item.get("cookie") or "")
            else:
                continue
            name = name.strip() or f"账号 {len(accounts) + 1}"
            cookie = cookie.strip()
            if not cookie or name in seen:
                continue
            accounts.append(AccountProfile(name=name, cookie=cookie))
            seen.add(name)
    fallback_name = fallback_name.strip() or "默认账号"
    fallback_cookie = fallback_cookie.strip()
    if fallback_cookie and fallback_name not in seen:
        accounts.insert(0, AccountProfile(name=fallback_name, cookie=fallback_cookie))
    return accounts


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
