from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Config:
    merchant_id: str
    payment_key: str
    callback_url: str
    db_path: Path
    listen_host: str = "127.0.0.1"
    listen_port: int = 8765
    pool_target: int = 3
    pool_interval_seconds: float = 15.0
    order_lifetime_seconds: int = 6_600
    expiry_guard_seconds: int = 60
    provider_timeout_seconds: float = 8.0
    provider_query_interval_seconds: float = 11.0
    status_token_secret: str = ""
    log_level: str = "INFO"

    @classmethod
    def from_environment(cls, *, require_credentials: bool = True) -> "Config":
        merchant_id = os.getenv("YUNGOUOS_MERCHANT_ID", "").strip()
        payment_key = os.getenv("YUNGOUOS_PAYMENT_KEY", "").strip()
        callback_url = os.getenv("SPONSOR_CALLBACK_URL", "").strip()
        if require_credentials and (not merchant_id or not payment_key):
            raise ValueError("YunGouOS credentials are not configured")
        _validate_callback_url(callback_url, required=require_credentials)
        return cls(
            merchant_id=merchant_id,
            payment_key=payment_key,
            callback_url=callback_url,
            db_path=Path(
                os.getenv(
                    "SPONSOR_DB_PATH",
                    str(Path(__file__).resolve().parent.parent / "data" / "orders.sqlite3"),
                )
            ),
            listen_host=os.getenv("SPONSOR_LISTEN_HOST", "127.0.0.1").strip(),
            listen_port=_env_int("SPONSOR_LISTEN_PORT", 8765, 1, 65535),
            pool_target=_env_int("SPONSOR_POOL_TARGET", 3, 0, 20),
            pool_interval_seconds=_env_float(
                "SPONSOR_POOL_INTERVAL_SECONDS", 15.0, 1.0, 3600.0
            ),
            order_lifetime_seconds=_env_int(
                "SPONSOR_ORDER_LIFETIME_SECONDS", 6_600, 300, 86_400
            ),
            expiry_guard_seconds=_env_int(
                "SPONSOR_EXPIRY_GUARD_SECONDS", 60, 10, 1800
            ),
            provider_timeout_seconds=_env_float(
                "SPONSOR_PROVIDER_TIMEOUT_SECONDS", 8.0, 1.0, 30.0
            ),
            provider_query_interval_seconds=_env_float(
                "SPONSOR_PROVIDER_QUERY_INTERVAL_SECONDS", 11.0, 1.0, 300.0
            ),
            status_token_secret=os.getenv("SPONSOR_STATUS_TOKEN_SECRET", "").strip(),
            log_level=os.getenv("SPONSOR_LOG_LEVEL", "INFO").strip().upper(),
        )


def _validate_callback_url(value: str, *, required: bool) -> None:
    if not value:
        if required:
            raise ValueError("SPONSOR_CALLBACK_URL is not configured")
        return
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("SPONSOR_CALLBACK_URL must be a public HTTPS URL")


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value
