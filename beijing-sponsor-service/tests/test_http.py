from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

import requests

from sponsor_service.config import Config
from sponsor_service.http_server import SponsorHTTPServer
from sponsor_service.payment import ProviderOrder
from sponsor_service.service import SponsorService


class FakePayment:
    def create_native_payment(self, *, provider_order_no: str, **_kwargs: object) -> str:
        return f"weixin://wxpay/{provider_order_no}"

    def query_order(self, provider_order_no: str) -> ProviderOrder:
        return ProviderOrder(False, provider_order_no, "", None)


class HTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        config = Config(
            merchant_id="merchant",
            payment_key="secret",
            callback_url="https://api.example.com/api/sponsor/callback",
            db_path=Path(self.temporary.name) / "orders.sqlite3",
            pool_target=1,
        )
        self.service = SponsorService(config, payment=FakePayment())
        self.service.fill_pool_once()
        self.server = SponsorHTTPServer(("127.0.0.1", 0), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temporary.cleanup()

    def test_health_and_single_order_contract(self) -> None:
        health = requests.get(f"{self.base}/api/health", timeout=2)
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["ok"])
        response = requests.post(
            f"{self.base}/api/sponsor/orders",
            json={
                "amount": "5.00",
                "install_id": "install-12345678",
                "checkout_intent_id": "checkout-12345678",
                "app_version": "0.5.22",
                "provider": "yungouos",
            },
            timeout=2,
        )
        self.assertEqual(response.status_code, 200)
        order = response.json()["data"]
        self.assertEqual(order["allocation"], "pool")
        self.assertTrue(order["qr_content"].startswith("weixin://"))
        status = requests.get(
            f"{self.base}/api/sponsor/orders/{order['order_id']}", timeout=2
        )
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["data"]["status"], "pending")

    def test_batch_contract_and_input_validation(self) -> None:
        response = requests.post(
            f"{self.base}/api/sponsor/orders",
            json={
                "amounts": ["5", "10", "20", "50", "100"],
                "install_id": "install-abcdefgh",
                "checkout_intent_id": "checkout-batch-1",
            },
            timeout=3,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["data"]["orders"]), 5)
        invalid = requests.post(
            f"{self.base}/api/sponsor/orders",
            json={"amount": "5.00"},
            timeout=2,
        )
        self.assertEqual(invalid.status_code, 400)


if __name__ == "__main__":
    unittest.main()
