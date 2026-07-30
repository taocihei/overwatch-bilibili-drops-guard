from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import requests

from bili_drop_guard.sponsor import (
    DEFAULT_SPONSOR_API_URL,
    SPONSOR_API_ENV,
    SponsorClient,
    SponsorError,
    SponsorUnavailable,
    normalize_amount,
)


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

    def test_remote_service_requires_https(self) -> None:
        with self.assertRaisesRegex(SponsorUnavailable, "HTTPS"):
            SponsorClient("http://example.com/api")

    def test_local_http_service_is_allowed_for_development(self) -> None:
        client = SponsorClient("http://127.0.0.1:8080/api", session=_Session([]))
        self.assertEqual(client.base_url, "http://127.0.0.1:8080/api")

    def test_create_order_uses_yungouos_provider_and_validates_qr_url(self) -> None:
        session = _Session(
            [
                _Response(
                    {
                        "ok": True,
                        "data": {
                            "order_id": "order-1",
                            "qr_url": "https://images.example.com/qr.png",
                            "expires_at": "2026-07-30T12:00:00Z",
                        },
                    }
                )
            ]
        )
        client = SponsorClient("https://pay.example.com/api/sponsor", session=session)

        order = client.create_order("6", app_version="0.5.4")

        self.assertEqual(order.order_id, "order-1")
        method, url, kwargs = session.calls[0]
        self.assertEqual(
            (method, url),
            ("POST", "https://pay.example.com/api/sponsor/orders"),
        )
        self.assertEqual(kwargs["json"]["amount"], "6.00")
        self.assertEqual(kwargs["json"]["provider"], "yungouos")

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

    def test_request_network_failure_is_friendly(self) -> None:
        class FailingSession:
            def request(self, *_args: object, **_kwargs: object) -> _Response:
                raise requests.ConnectionError("offline")

        client = SponsorClient(
            "https://pay.example.com/api/sponsor",
            session=FailingSession(),
        )
        with self.assertRaisesRegex(SponsorUnavailable, "暂时不可用"):
            client.create_order("6.00", app_version="0.5.4")

    def test_service_error_prefers_error_field(self) -> None:
        client = SponsorClient(
            "https://pay.example.com/api/sponsor",
            session=_Session(
                [_Response({"ok": False, "error": "支付通道暂时繁忙"})]
            ),
        )
        with self.assertRaisesRegex(SponsorError, "支付通道暂时繁忙"):
            client.create_order("3.00", app_version="0.5.4")

    def test_amounts_are_restricted_to_visible_presets(self) -> None:
        self.assertEqual(str(normalize_amount("3")), "3.00")
        with self.assertRaisesRegex(SponsorError, "界面提供"):
            normalize_amount("4")


if __name__ == "__main__":
    unittest.main()
