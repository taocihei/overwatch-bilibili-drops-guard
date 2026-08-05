from __future__ import annotations

import unittest

from sponsor_service.payment import YunGouOSClient, payment_sign, verify_callback


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class FakeSession:
    def __init__(self) -> None:
        self.form: dict[str, str] = {}

    def mount(self, _prefix: str, _adapter: object) -> None:
        return None

    def post(self, _url: str, *, data: dict, **_kwargs: object) -> FakeResponse:
        self.form = data
        return FakeResponse({"code": 0, "data": "weixin://wxpay/native-test"})

    def get(self, _url: str, *, params: dict, **_kwargs: object) -> FakeResponse:
        return FakeResponse(
            {
                "code": 0,
                "data": {
                    "outTradeNo": params["out_trade_no"],
                    "mchid": params["mch_id"],
                    "payStatus": 1,
                    "payNo": "WX001",
                    "money": "5.00",
                },
            }
        )


class PaymentTests(unittest.TestCase):
    def test_native_payment_requests_type_one_and_returns_qr_content(self) -> None:
        session = FakeSession()
        client = YunGouOSClient("merchant", "secret", session_factory=lambda: session)
        result = client.create_native_payment(
            provider_order_no="SP001",
            amount="5.00",
            body="sponsor",
            notify_url="https://api.example.com/api/sponsor/callback",
            attach="sponsor:001",
        )
        self.assertEqual(result, "weixin://wxpay/native-test")
        self.assertEqual(session.form["type"], "1")
        self.assertEqual(session.form["out_trade_no"], "SP001")

    def test_callback_signature_uses_six_documented_fields(self) -> None:
        signed = {
            "code": "1",
            "orderNo": "Y001",
            "outTradeNo": "SP001",
            "payNo": "WX001",
            "money": "5.00",
            "mchId": "merchant",
        }
        values = {**signed, "sign": payment_sign(signed, "secret")}
        self.assertTrue(verify_callback(values, "merchant", "secret"))
        values["money"] = "10.00"
        self.assertFalse(verify_callback(values, "merchant", "secret"))

    def test_query_validates_and_normalizes_provider_order(self) -> None:
        client = YunGouOSClient("merchant", "secret", session_factory=FakeSession)
        result = client.query_order("SP001")
        self.assertTrue(result.paid)
        self.assertEqual(result.amount_cents, 500)
        self.assertEqual(result.payment_no, "WX001")


if __name__ == "__main__":
    unittest.main()
