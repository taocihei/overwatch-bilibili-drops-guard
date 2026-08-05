from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import requests

from bili_drop_guard.sponsor import (
    DEFAULT_SPONSOR_API_URL,
    SPONSOR_API_ENV,
    SponsorClient,
    SponsorError,
    SponsorOrder,
    SponsorUnavailable,
    create_checkout_intent_id,
    load_or_create_install_id,
    normalize_amount,
)


INSTALL_ID = "desktop-0123456789abcdef0123456789abcdef"
INTENT_ID = "checkout-0123456789abcdef0123456789abcdef"


class _Response:
    def __init__(
        self,
        payload: object | None = None,
        *,
        content: bytes = b"",
        content_type: str = "application/json",
    ) -> None:
        self.payload = payload
        self.content = content
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self.payload


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request(self, method: str, url: str, **kwargs: object) -> _Response:
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def get(self, url: str, **kwargs: object) -> _Response:
        self.calls.append(("GET_IMAGE", url, kwargs))
        return self.responses.pop(0)


class SponsorClientTest(unittest.TestCase):
    def test_default_service_url_is_used_without_environment_override(self) -> None:
        with patch.dict(os.environ, {SPONSOR_API_ENV: ""}, clear=False):
            client = SponsorClient.from_environment()
        self.assertEqual(client.base_url, DEFAULT_SPONSOR_API_URL)

    def test_environment_can_override_default_service_url(self) -> None:
        override = "https://pay.example.com/api/sponsor/"
        with patch.dict(os.environ, {SPONSOR_API_ENV: override}, clear=False):
            client = SponsorClient.from_environment()
        self.assertEqual(client.base_url, override.rstrip("/"))

    def test_new_session_client_keeps_configuration_without_sharing_session(self) -> None:
        client = SponsorClient(
            "https://pay.example.com/api/sponsor",
            timeout=(1.5, 4.0),
        )

        isolated = client.new_session_client()

        self.assertEqual(isolated.base_url, client.base_url)
        self.assertEqual(isolated.timeout, client.timeout)
        self.assertIsNot(isolated, client)
        self.assertIsNot(isolated.session, client.session)

    def test_remote_service_requires_https(self) -> None:
        with self.assertRaisesRegex(SponsorUnavailable, "HTTPS"):
            SponsorClient("http://example.com/api")

    def test_local_http_service_is_allowed_for_development(self) -> None:
        client = SponsorClient("http://127.0.0.1:8080/api", session=_Session([]))
        self.assertEqual(client.base_url, "http://127.0.0.1:8080/api")

    def test_warm_up_uses_health_endpoint_and_reuses_session(self) -> None:
        session = _Session(
            [
                _Response({"ok": True}),
                _Response(
                    {
                        "data": {
                            "order_id": "order-1",
                            "qr_url": "https://images.example.com/qr.png",
                        }
                    }
                ),
            ]
        )
        client = SponsorClient("https://pay.example.com/api/sponsor", session=session)

        self.assertTrue(client.warm_up())
        client.create_order(
            "10",
            app_version="0.5.13",
            install_id=INSTALL_ID,
            checkout_intent_id=INTENT_ID,
        )

        self.assertEqual(
            [(method, url) for method, url, _kwargs in session.calls],
            [
                ("GET", "https://pay.example.com/api/health"),
                ("POST", "https://pay.example.com/api/sponsor/orders"),
            ],
        )

    def test_warm_up_failure_is_silent_and_retryable(self) -> None:
        class FailingSession:
            def request(self, *_args: object, **_kwargs: object) -> _Response:
                raise requests.ConnectionError("cold")

        client = SponsorClient(
            "https://pay.example.com/api/sponsor",
            session=FailingSession(),
        )

        self.assertFalse(client.warm_up())

    def test_create_order_uses_yungouos_provider_and_validates_qr_url(self) -> None:
        session = _Session(
            [
                _Response(
                    {
                        "ok": True,
                        "data": {
                            "order_id": "order-1",
                            "qr_url": "https://images.example.com/qr.png",
                            "fallback_qr_url": "https://pay.example.com/api/sponsor/qr/order-1",
                            "expires_at": "2026-07-30T12:00:00Z",
                            "expires_in_seconds": 845,
                        },
                    }
                )
            ]
        )
        client = SponsorClient("https://pay.example.com/api/sponsor", session=session)

        order = client.create_order(
            "6",
            app_version="0.5.4",
            install_id=INSTALL_ID,
            checkout_intent_id=INTENT_ID,
        )

        self.assertEqual(order.order_id, "order-1")
        method, url, kwargs = session.calls[0]
        self.assertEqual(
            (method, url),
            ("POST", "https://pay.example.com/api/sponsor/orders"),
        )
        self.assertEqual(kwargs["json"]["amount"], "6.00")
        self.assertEqual(kwargs["json"]["provider"], "yungouos")
        self.assertEqual(kwargs["json"]["install_id"], INSTALL_ID)
        self.assertEqual(kwargs["json"]["checkout_intent_id"], INTENT_ID)
        self.assertEqual(order.expires_in_seconds, 845)
        self.assertEqual(
            order.fallback_qr_url,
            "https://pay.example.com/api/sponsor/qr/order-1",
        )

    def test_query_order_normalizes_success_state(self) -> None:
        session = _Session([_Response({"data": {"status": "success"}})])
        client = SponsorClient("https://pay.example.com/api/sponsor", session=session)

        status = client.query_order("order / 1")

        self.assertTrue(status.paid)
        self.assertTrue(status.terminal)
        self.assertTrue(session.calls[0][1].endswith("/orders/order%20%2F%201"))

    def test_query_order_rejects_unknown_state(self) -> None:
        client = SponsorClient(
            "https://pay.example.com/api/sponsor",
            session=_Session([_Response({"data": {"status": "mystery"}})]),
        )
        with self.assertRaisesRegex(SponsorError, "未知订单状态"):
            client.query_order("order-1")

    def test_download_qr_requires_an_image(self) -> None:
        client = SponsorClient(
            "https://pay.example.com/api/sponsor",
            session=_Session(
                [_Response(content=b"<html>", content_type="text/html")]
            ),
        )
        with self.assertRaisesRegex(SponsorError, "没有返回图片"):
            client.download_qr("https://images.example.com/qr.png")

    def test_download_order_qr_uses_fallback_after_direct_url_fails(self) -> None:
        session = _Session(
            [
                _Response(content=b"<html>", content_type="text/html"),
                _Response(content=b"\x89PNG\r\n\x1a\nimage", content_type="image/png"),
            ]
        )
        client = SponsorClient("https://pay.example.com/api/sponsor", session=session)
        order = SponsorOrder(
            order_id="order-1",
            qr_url="https://images.example.com/direct.png",
            fallback_qr_url="https://pay.example.com/api/sponsor/qr/order-1",
        )

        content = client.download_order_qr(order)

        self.assertTrue(content.startswith(b"\x89PNG"))
        self.assertEqual(
            [call[1] for call in session.calls],
            [
                "https://images.example.com/direct.png",
                "https://pay.example.com/api/sponsor/qr/order-1",
            ],
        )

    def test_create_order_accepts_raw_wechat_qr_content(self) -> None:
        content = "weixin://wxpay/bizpayurl?pr=test-order"
        session = _Session(
            [
                _Response(
                    {
                        "ok": True,
                        "data": {
                            "order_id": "order-raw",
                            "qr_content": content,
                            "qr_url": "https://pay.example.com/api/sponsor/qr/order-raw",
                        },
                    }
                )
            ]
        )
        client = SponsorClient("https://pay.example.com/api/sponsor", session=session)

        order = client.create_order(
            "5",
            app_version="0.5.20",
            install_id=INSTALL_ID,
            checkout_intent_id=INTENT_ID,
        )

        self.assertEqual(order.qr_content, content)

    def test_raw_wechat_content_is_rendered_locally_without_download(self) -> None:
        session = _Session([])
        client = SponsorClient("https://pay.example.com/api/sponsor", session=session)
        order = SponsorOrder(
            order_id="order-raw",
            qr_content="weixin://wxpay/bizpayurl?pr=test-order",
        )

        content = client.download_order_qr(order)

        self.assertTrue(content.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(session.calls, [])

    def test_request_network_failure_is_friendly(self) -> None:
        class FailingSession:
            def request(self, *_args: object, **_kwargs: object) -> _Response:
                raise requests.ConnectionError("offline")

        client = SponsorClient(
            "https://pay.example.com/api/sponsor",
            session=FailingSession(),
        )
        with self.assertRaisesRegex(SponsorUnavailable, "暂时不可用"):
            client.create_order(
                "6.00",
                app_version="0.5.4",
                install_id=INSTALL_ID,
                checkout_intent_id=INTENT_ID,
            )

    def test_service_error_prefers_error_field(self) -> None:
        client = SponsorClient(
            "https://pay.example.com/api/sponsor",
            session=_Session(
                [_Response({"ok": False, "error": "支付通道暂时繁忙"})]
            ),
        )
        with self.assertRaisesRegex(SponsorError, "支付通道暂时繁忙"):
            client.create_order(
                "3.00",
                app_version="0.5.4",
                install_id=INSTALL_ID,
                checkout_intent_id=INTENT_ID,
            )

    def test_batch_reserve_matches_server_contract(self) -> None:
        orders = [
            {
                "order_id": f"order-{amount}",
                "amount": amount,
                "qr_content": f"weixin://wxpay/bizpayurl?pr={amount}",
                "allocation": "created",
            }
            for amount in ("5.00", "10.00", "20.00", "50.00", "100.00")
        ]
        session = _Session(
            [
                _Response(
                    {
                        "ok": True,
                        "data": {
                            "checkout_intent_id": INTENT_ID,
                            "orders": orders,
                        },
                    }
                )
            ]
        )
        client = SponsorClient("https://pay.example.com/api/sponsor", session=session)

        batch = client.reserve_orders(
            ["5", "10", "20", "50", "100"],
            app_version="0.5.22",
            install_id=INSTALL_ID,
            checkout_intent_id=INTENT_ID,
        )

        self.assertEqual(batch.checkout_intent_id, INTENT_ID)
        self.assertEqual([order.amount for order in batch.orders], [
            "5.00", "10.00", "20.00", "50.00", "100.00"
        ])
        payload = session.calls[0][2]["json"]
        self.assertEqual(payload["amounts"], [
            "5.00", "10.00", "20.00", "50.00", "100.00"
        ])
        self.assertEqual(payload["install_id"], INSTALL_ID)
        self.assertEqual(payload["checkout_intent_id"], INTENT_ID)

    def test_install_id_is_persisted_and_checkout_intents_rotate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "sponsor-install-id"
            first = load_or_create_install_id(path)
            second = load_or_create_install_id(path)

        self.assertEqual(first, second)
        self.assertTrue(first.startswith("desktop-"))
        self.assertNotEqual(create_checkout_intent_id(), create_checkout_intent_id())

    def test_preset_and_custom_amounts_are_supported_within_range(self) -> None:
        self.assertEqual(str(normalize_amount("5")), "5.00")
        self.assertEqual(str(normalize_amount("37.5")), "37.50")
        self.assertEqual(str(normalize_amount("100")), "100.00")
        with self.assertRaisesRegex(SponsorError, "1–9999"):
            normalize_amount("0")
        with self.assertRaisesRegex(SponsorError, "1–9999"):
            normalize_amount("10000")


if __name__ == "__main__":
    unittest.main()
