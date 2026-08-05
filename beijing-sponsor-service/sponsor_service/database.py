from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS sponsor_orders (
    id TEXT PRIMARY KEY NOT NULL,
    provider_order_no TEXT NOT NULL UNIQUE,
    amount_cents INTEGER NOT NULL,
    status TEXT NOT NULL,
    qr_content TEXT NOT NULL DEFAULT '',
    app_version TEXT NOT NULL DEFAULT 'unknown',
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    paid_at INTEGER,
    payment_no TEXT,
    install_id TEXT,
    checkout_intent_id TEXT,
    reserved_at INTEGER,
    state_version INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS sponsor_orders_status_idx
    ON sponsor_orders(status);
CREATE INDEX IF NOT EXISTS sponsor_orders_expiry_idx
    ON sponsor_orders(expires_at);
CREATE INDEX IF NOT EXISTS sponsor_orders_pool_idx
    ON sponsor_orders(amount_cents, status, install_id, expires_at);
CREATE UNIQUE INDEX IF NOT EXISTS sponsor_orders_checkout_unique
    ON sponsor_orders(install_id, checkout_intent_id, amount_cents);
CREATE TABLE IF NOT EXISTS sponsor_provider_state (
    key TEXT PRIMARY KEY NOT NULL,
    checked_at INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO sponsor_provider_state(key, checked_at)
    VALUES ('order-query', 0);
"""


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.read() as connection:
            connection.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=10.0,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
