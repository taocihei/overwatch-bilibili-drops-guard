from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from .config import Config
from .database import Database
from .payment import PaymentError, YunGouOSClient, money_to_cents, verify_callback


PRESET_AMOUNTS_CENTS = (500, 1_000, 2_000, 5_000, 10_000)
ORDER_COLUMNS = """
id, provider_order_no, amount_cents, status, qr_content, app_version,
created_at, expires_at, paid_at, payment_no, install_id,
checkout_intent_id, reserved_at, state_version, last_error
"""


class ServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReservationResult:
    order: sqlite3.Row
    allocation: str


class SponsorService:
    def __init__(
        self,
        config: Config,
        *,
        database: Database | None = None,
        payment: YunGouOSClient | None = None,
        clock=time.time,
    ) -> None:
        self.config = config
        self.database = database or Database(config.db_path)
        self.database.initialize()
        self.payment = payment or YunGouOSClient(
            config.merchant_id,
            config.payment_key,
            timeout=config.provider_timeout_seconds,
        )
        self._clock = clock
        self.log = logging.getLogger("sponsor.service")

    def reserve_many(
        self,
        amounts_cents: list[int],
        *,
        install_id: str,
        checkout_intent_id: str,
        app_version: str,
    ) -> list[ReservationResult]:
        workers = max(1, min(5, len(amounts_cents)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="reserve") as pool:
            futures = [
                pool.submit(
                    self.reserve,
                    amount_cents,
                    install_id=install_id,
                    checkout_intent_id=checkout_intent_id,
                    app_version=app_version,
                )
                for amount_cents in amounts_cents
            ]
            return [future.result() for future in futures]

    def reserve(
        self,
        amount_cents: int,
        *,
        install_id: str,
        checkout_intent_id: str,
        app_version: str,
    ) -> ReservationResult:
        now = self._now_ms()
        created_id = ""
        with self.database.transaction(immediate=True) as connection:
            self._expire_orders(connection, now)
            existing = connection.execute(
                f"""SELECT {ORDER_COLUMNS} FROM sponsor_orders
                    WHERE install_id = ? AND checkout_intent_id = ?
                      AND amount_cents = ? LIMIT 1""",
                (install_id, checkout_intent_id, amount_cents),
            ).fetchone()
            if existing and existing["status"] != "failed":
                existing_status = existing["status"]
                # Another request owns a provider call for this exact intent.
                if existing_status == "creating":
                    created_id = existing["id"]
                else:
                    return ReservationResult(existing, "existing")
            else:
                if existing:
                    connection.execute("DELETE FROM sponsor_orders WHERE id = ?", (existing["id"],))
                pooled = connection.execute(
                    f"""SELECT {ORDER_COLUMNS} FROM sponsor_orders
                        WHERE amount_cents = ? AND status = 'pending'
                          AND install_id IS NULL AND checkout_intent_id IS NULL
                          AND expires_at > ?
                        ORDER BY created_at ASC LIMIT 1""",
                    (amount_cents, now + self.config.expiry_guard_seconds * 1000),
                ).fetchone()
                if pooled:
                    changed = connection.execute(
                        """UPDATE sponsor_orders
                           SET install_id = ?, checkout_intent_id = ?, reserved_at = ?,
                               app_version = ?, state_version = state_version + 1
                           WHERE id = ? AND install_id IS NULL
                             AND checkout_intent_id IS NULL""",
                        (
                            install_id,
                            checkout_intent_id,
                            now,
                            app_version,
                            pooled["id"],
                        ),
                    ).rowcount
                    if changed != 1:
                        raise ServiceError("SPONSOR_POOL_CLAIM_FAILED")
                    claimed = self._get_by_id(connection, pooled["id"])
                    return ReservationResult(claimed, "pool")

                created_id = uuid.uuid4().hex
                connection.execute(
                    """INSERT INTO sponsor_orders(
                           id, provider_order_no, amount_cents, status, qr_content,
                           app_version, created_at, expires_at, install_id,
                           checkout_intent_id, reserved_at, state_version
                       ) VALUES (?, ?, ?, 'creating', '', ?, ?, ?, ?, ?, ?, 0)""",
                    (
                        created_id,
                        self._provider_order_no(),
                        amount_cents,
                        app_version,
                        now,
                        now + self.config.order_lifetime_seconds * 1000,
                        install_id,
                        checkout_intent_id,
                        now,
                    ),
                )
                owns_creation = True

        if locals().get("owns_creation", False):
            return ReservationResult(self._finish_creation(created_id), "created")
        return ReservationResult(self._wait_for_creation(created_id), "concurrent")

    def fill_pool_once(self, *, target: int | None = None) -> int:
        desired = self.config.pool_target if target is None else max(0, target)
        created = 0
        self.expire_pending()
        for amount_cents in PRESET_AMOUNTS_CENTS:
            while True:
                order_id = self._begin_pool_creation(amount_cents, desired)
                if not order_id:
                    break
                try:
                    self._finish_creation(order_id)
                    created += 1
                except Exception:
                    self.log.exception("pool fill failed for amount_cents=%s", amount_cents)
                    break
        return created

    def pool_counts(self) -> dict[int, int]:
        now = self._now_ms()
        with self.database.read() as connection:
            rows = connection.execute(
                """SELECT amount_cents, COUNT(*) AS count FROM sponsor_orders
                   WHERE status = 'pending' AND install_id IS NULL
                     AND checkout_intent_id IS NULL AND expires_at > ?
                   GROUP BY amount_cents""",
                (now + self.config.expiry_guard_seconds * 1000,),
            ).fetchall()
        counts = {amount: 0 for amount in PRESET_AMOUNTS_CENTS}
        counts.update({int(row["amount_cents"]): int(row["count"]) for row in rows})
        return counts

    def order_payload(self, result: ReservationResult) -> dict[str, object]:
        row = result.order
        expires_in = max(0, (int(row["expires_at"]) - self._now_ms()) // 1000)
        payload: dict[str, object] = {
            "order_id": row["id"],
            "amount": f"{int(row['amount_cents']) / 100:.2f}",
            "qr_url": "",
            "fallback_qr_url": "",
            "qr_content": row["qr_content"],
            "expires_at": _iso_from_ms(int(row["expires_at"])),
            "expires_in_seconds": expires_in,
            "state_version": int(row["state_version"]),
            "allocation": result.allocation,
        }
        token = self.status_token(row["id"])
        if token:
            payload["status_token"] = token
        return payload

    def get_status(self, order_id: str, *, token: str = "") -> dict[str, object] | None:
        if token and not self.valid_status_token(order_id, token):
            raise ServiceError("STATUS_TOKEN_INVALID")
        now = self._now_ms()
        with self.database.transaction(immediate=True) as connection:
            self._expire_orders(connection, now)
            row = self._get_by_id(connection, order_id, required=False)
        if not row:
            return None
        if row["status"] == "pending" and self._acquire_query_slot(now):
            row = self._reconcile(row)
        status = _public_status(row["status"])
        return {
            "status": status,
            "message": _status_message(status),
            "state_version": int(row["state_version"]),
            "paid_at": _iso_from_ms(row["paid_at"]) if row["paid_at"] else None,
        }

    def process_callback(self, values: dict[str, str]) -> tuple[str, int]:
        if not verify_callback(values, self.config.merchant_id, self.config.payment_key):
            return "FAIL", 400
        provider_order_no = values.get("outTradeNo", "").strip()
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT id, amount_cents, status FROM sponsor_orders WHERE provider_order_no = ?",
                (provider_order_no,),
            ).fetchone()
            if not row:
                return "FAIL", 404
            if money_to_cents(values.get("money")) != int(row["amount_cents"]):
                return "FAIL", 400
            if values.get("code", "").strip() == "1" and row["status"] != "paid":
                connection.execute(
                    """UPDATE sponsor_orders
                       SET status = 'paid', paid_at = ?, payment_no = ?,
                           state_version = state_version + 1
                       WHERE id = ? AND status != 'paid'""",
                    (self._now_ms(), values.get("payNo", "").strip(), row["id"]),
                )
        return "SUCCESS", 200

    def expire_pending(self) -> int:
        with self.database.transaction(immediate=True) as connection:
            return self._expire_orders(connection, self._now_ms())

    def status_token(self, order_id: str) -> str:
        secret = self.config.status_token_secret
        if not secret:
            return ""
        digest = hmac.new(secret.encode(), order_id.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def valid_status_token(self, order_id: str, token: str) -> bool:
        expected = self.status_token(order_id)
        return bool(expected and hmac.compare_digest(expected, token))

    def _begin_pool_creation(self, amount_cents: int, target: int) -> str:
        if target <= 0:
            return ""
        now = self._now_ms()
        with self.database.transaction(immediate=True) as connection:
            inventory = connection.execute(
                """SELECT COUNT(*) FROM sponsor_orders
                   WHERE amount_cents = ? AND status IN ('creating', 'pending')
                     AND install_id IS NULL AND checkout_intent_id IS NULL
                     AND expires_at > ?""",
                (amount_cents, now + self.config.expiry_guard_seconds * 1000),
            ).fetchone()[0]
            if int(inventory) >= target:
                return ""
            order_id = uuid.uuid4().hex
            connection.execute(
                """INSERT INTO sponsor_orders(
                       id, provider_order_no, amount_cents, status, qr_content,
                       app_version, created_at, expires_at, state_version
                   ) VALUES (?, ?, ?, 'creating', '', 'pool', ?, ?, 0)""",
                (
                    order_id,
                    self._provider_order_no(),
                    amount_cents,
                    now,
                    now + self.config.order_lifetime_seconds * 1000,
                ),
            )
            return order_id

    def _finish_creation(self, order_id: str) -> sqlite3.Row:
        with self.database.read() as connection:
            row = self._get_by_id(connection, order_id)
        try:
            qr_content = self.payment.create_native_payment(
                provider_order_no=row["provider_order_no"],
                amount=f"{int(row['amount_cents']) / 100:.2f}",
                body="守望先锋B站直播挂宝赞助",
                notify_url=self.config.callback_url,
                attach=f"sponsor:{row['id']}",
            )
        except Exception as exc:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """UPDATE sponsor_orders SET status = 'failed', last_error = ?,
                       state_version = state_version + 1
                       WHERE id = ? AND status = 'creating'""",
                    (str(exc)[:160], order_id),
                )
            raise
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """UPDATE sponsor_orders SET qr_content = ?, status = 'pending',
                   state_version = state_version + 1
                   WHERE id = ? AND status = 'creating'""",
                (qr_content, order_id),
            )
            return self._get_by_id(connection, order_id)

    def _wait_for_creation(self, order_id: str, timeout: float = 12.0) -> sqlite3.Row:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.database.read() as connection:
                row = self._get_by_id(connection, order_id, required=False)
            if not row:
                raise ServiceError("SPONSOR_ORDER_NOT_FOUND")
            if row["status"] == "failed":
                raise ServiceError("SPONSOR_ORDER_CREATION_FAILED")
            if row["status"] != "creating":
                return row
            time.sleep(0.035)
        raise ServiceError("SPONSOR_ORDER_CREATION_TIMEOUT")

    def _acquire_query_slot(self, now: int) -> bool:
        threshold = now - int(self.config.provider_query_interval_seconds * 1000)
        with self.database.transaction(immediate=True) as connection:
            changed = connection.execute(
                """UPDATE sponsor_provider_state SET checked_at = ?
                   WHERE key = 'order-query' AND checked_at <= ?""",
                (now, threshold),
            ).rowcount
        return changed == 1

    def _reconcile(self, row: sqlite3.Row) -> sqlite3.Row:
        try:
            provider = self.payment.query_order(row["provider_order_no"])
        except PaymentError:
            return row
        if not provider.paid or provider.amount_cents != int(row["amount_cents"]):
            return row
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """UPDATE sponsor_orders SET status = 'paid', paid_at = ?, payment_no = ?,
                   state_version = state_version + 1
                   WHERE id = ? AND status = 'pending'""",
                (self._now_ms(), provider.payment_no, row["id"]),
            )
            return self._get_by_id(connection, row["id"])

    @staticmethod
    def _expire_orders(connection: sqlite3.Connection, now: int) -> int:
        return connection.execute(
            """UPDATE sponsor_orders SET status = 'expired',
               state_version = state_version + 1
               WHERE status IN ('pending', 'creating') AND expires_at <= ?""",
            (now,),
        ).rowcount

    @staticmethod
    def _get_by_id(
        connection: sqlite3.Connection, order_id: str, *, required: bool = True
    ) -> sqlite3.Row | None:
        row = connection.execute(
            f"SELECT {ORDER_COLUMNS} FROM sponsor_orders WHERE id = ? LIMIT 1",
            (order_id,),
        ).fetchone()
        if required and not row:
            raise ServiceError("SPONSOR_ORDER_NOT_FOUND")
        return row

    def _now_ms(self) -> int:
        return int(self._clock() * 1000)

    @staticmethod
    def _provider_order_no() -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"SP{stamp}{secrets.token_hex(6).upper()}"


class PoolWorker:
    def __init__(self, service: SponsorService) -> None:
        self.service = service
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="payment-pool", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.service.fill_pool_once()
            except Exception:
                self.service.log.exception("background pool fill failed")
            self._stop.wait(self.service.config.pool_interval_seconds)


def _iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _public_status(status: str) -> str:
    if status in {"paid", "expired", "closed"}:
        return status
    return "pending"


def _status_message(status: str) -> str:
    return {
        "paid": "支付成功",
        "expired": "二维码已过期",
        "closed": "订单已关闭",
    }.get(status, "等待扫码支付")
