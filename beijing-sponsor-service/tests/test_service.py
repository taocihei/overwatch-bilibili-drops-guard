from __future__ import annotations

import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sponsor_service.config import Config
from sponsor_service.database import Database
from sponsor_service.payment import ProviderOrder, payment_sign
from sponsor_service.service import ServiceError, SponsorService


class FakePayment:
    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.calls: list[str] = []
        self.lock = threading.Lock()

    def create_native_payment(self, *, provider_order_no: str, **_kwargs: object) -> str:
        with self.lock:
            self.calls.append(provider_order_no)
        if self.delay:
            time.sleep(self.delay)
        return f"weixin://wxpay/{provider_order_no}"

    def query_order(self, provider_order_no: str) -> ProviderOrder:
        return ProviderOrder(False, provider_order_no, "", None)


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.config = Config(
            merchant_id="merchant",
            payment_key="secret",
            callback_url="https://api.example.com/api/sponsor/callback",
            db_path=Path(self.temporary.name) / "orders.sqlite3",
            pool_target=2,
            status_token_secret="status-secret",
        )
        self.payment = FakePayment()
        self.service = SponsorService(self.config, payment=self.payment)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_pool_is_prebuilt_and_claimed_atomically(self) -> None:
        self.assertEqual(self.service.fill_pool_once(), 10)
        self.assertEqual(self.service.pool_counts()[500], 2)
        left = self.service.reserve(
            500,
            install_id="install-12345678",
            checkout_intent_id="checkout-11111111",
            app_version="0.5.22",
        )
        right = self.service.reserve(
            500,
            install_id="install-87654321",
            checkout_intent_id="checkout-22222222",
            app_version="0.5.22",
        )
        self.assertEqual(left.allocation, "pool")
        self.assertEqual(right.allocation, "pool")
        self.assertNotEqual(left.order["id"], right.order["id"])

    def test_same_install_and_checkout_is_idempotent_under_concurrency(self) -> None:
        payment = FakePayment(delay=0.08)
        service = SponsorService(self.config, payment=payment)

        def reserve():
            return service.reserve(
                777,
                install_id="install-12345678",
                checkout_intent_id="checkout-12345678",
                app_version="0.5.22",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            first, second = [future.result() for future in [executor.submit(reserve), executor.submit(reserve)]]
        self.assertEqual(len(payment.calls), 1)
        self.assertEqual(first.order["id"], second.order["id"])
        self.assertEqual({first.allocation, second.allocation}, {"created", "concurrent"})

    def test_batch_preserves_order_and_returns_only_qr_content(self) -> None:
        self.service.fill_pool_once(target=1)
        results = self.service.reserve_many(
            [500, 1000, 2000, 5000, 10000],
            install_id="install-12345678",
            checkout_intent_id="checkout-batch-123",
            app_version="0.5.22",
        )
        self.assertEqual([row.order["amount_cents"] for row in results], [500, 1000, 2000, 5000, 10000])
        payload = self.service.order_payload(results[0])
        self.assertTrue(str(payload["qr_content"]).startswith("weixin://"))
        self.assertEqual(payload["qr_url"], "")
        self.assertTrue(payload["status_token"])

    def test_callback_is_signed_idempotent_and_status_is_client_compatible(self) -> None:
        result = self.service.reserve(
            500,
            install_id="install-12345678",
            checkout_intent_id="checkout-paid-123",
            app_version="0.5.22",
        )
        signed = {
            "code": "1",
            "orderNo": "Y001",
            "outTradeNo": result.order["provider_order_no"],
            "payNo": "WX001",
            "money": "5.00",
            "mchId": "merchant",
        }
        values = {**signed, "sign": payment_sign(signed, "secret")}
        self.assertEqual(self.service.process_callback(values), ("SUCCESS", 200))
        self.assertEqual(self.service.process_callback(values), ("SUCCESS", 200))
        # No token remains compatible with the current desktop client.
        status = self.service.get_status(result.order["id"])
        self.assertEqual(status["status"], "paid")
        with self.assertRaisesRegex(ServiceError, "STATUS_TOKEN_INVALID"):
            self.service.get_status(result.order["id"], token="wrong")


if __name__ == "__main__":
    unittest.main()
