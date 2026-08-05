from __future__ import annotations

import hashlib
import hmac
import re
import threading
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping

import requests
from requests.adapters import HTTPAdapter


NATIVE_PAY_URL = "https://api.pay.yungouos.com/api/pay/wxpay/nativePay"
ORDER_QUERY_URL = "https://api.pay.yungouos.com/api/system/order/getPayOrderInfo"
_QR_RE = re.compile(r"^weixin://", re.IGNORECASE)


class PaymentError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderOrder:
    paid: bool
    provider_order_no: str
    payment_no: str
    amount_cents: int | None


def payment_sign(params: Mapping[str, str], payment_key: str) -> str:
    pairs = [f"{key}={value}" for key, value in sorted(params.items()) if value != ""]
    payload = "&".join([*pairs, f"key={payment_key}"])
    return hashlib.md5(payload.encode("utf-8"), usedforsecurity=False).hexdigest().upper()


def verify_callback(
    values: Mapping[str, str], merchant_id: str, payment_key: str
) -> bool:
    sign = values.get("sign", "").strip().upper()
    if not sign or values.get("mchId", "").strip() != merchant_id:
        return False
    required = ("code", "orderNo", "outTradeNo", "payNo", "money", "mchId")
    signed: dict[str, str] = {}
    for key in required:
        value = values.get(key, "").strip()
        if not value:
            return False
        signed[key] = value
    return hmac.compare_digest(payment_sign(signed, payment_key), sign)


def money_to_cents(value: str | None) -> int | None:
    try:
        amount = Decimal((value or "").strip())
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
        return None
    return int(amount * 100)


class YunGouOSClient:
    def __init__(
        self,
        merchant_id: str,
        payment_key: str,
        *,
        timeout: float = 8.0,
        session_factory=requests.Session,
    ) -> None:
        self.merchant_id = merchant_id
        self.payment_key = payment_key
        self.timeout = timeout
        self._session_factory = session_factory
        self._local = threading.local()

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = self._session_factory()
            adapter = HTTPAdapter(pool_connections=8, pool_maxsize=16, max_retries=0)
            session.mount("https://", adapter)
            self._local.session = session
        return session

    def create_native_payment(
        self,
        *,
        provider_order_no: str,
        amount: str,
        body: str,
        notify_url: str,
        attach: str,
    ) -> str:
        required = {
            "body": body,
            "mch_id": self.merchant_id,
            "out_trade_no": provider_order_no,
            "total_fee": amount,
        }
        form = {
            **required,
            "attach": attach,
            "notify_url": notify_url,
            "sign": payment_sign(required, self.payment_key),
            "type": "1",
        }
        try:
            response = self._session().post(
                NATIVE_PAY_URL,
                data=form,
                headers={"Accept": "application/json"},
                timeout=(3.0, self.timeout),
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise PaymentError("PAYMENT_PROVIDER_FETCH_FAILED") from exc
        if str(payload.get("code")) != "0":
            raise PaymentError("PAYMENT_PROVIDER_REJECTED")
        qr_content = _extract_qr_content(payload.get("data"))
        if len(qr_content) > 4096 or any(ord(char) < 32 for char in qr_content):
            raise PaymentError("PAYMENT_PROVIDER_INVALID_QR")
        return qr_content

    def query_order(self, provider_order_no: str) -> ProviderOrder:
        required = {
            "out_trade_no": provider_order_no,
            "mch_id": self.merchant_id,
        }
        params = {**required, "sign": payment_sign(required, self.payment_key)}
        try:
            response = self._session().get(
                ORDER_QUERY_URL,
                params=params,
                headers={"Accept": "application/json"},
                timeout=(3.0, self.timeout),
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise PaymentError("PAYMENT_PROVIDER_QUERY_FAILED") from exc
        if str(payload.get("code")) != "0" or not isinstance(payload.get("data"), dict):
            raise PaymentError("PAYMENT_PROVIDER_QUERY_REJECTED")
        data = payload["data"]
        returned_order = _read_string(data, "outTradeNo", "out_trade_no")
        returned_merchant = _read_string(data, "mchid", "mchId", "mch_id")
        if returned_order != provider_order_no or returned_merchant != self.merchant_id:
            raise PaymentError("PAYMENT_PROVIDER_QUERY_ORDER_MISMATCH")
        return ProviderOrder(
            paid=_read_string(data, "payStatus", "pay_status", "status") == "1",
            provider_order_no=returned_order,
            payment_no=_read_string(data, "payNo", "pay_no"),
            amount_cents=money_to_cents(_read_string(data, "money")),
        )


def _extract_qr_content(data: object) -> str:
    candidates: list[object]
    if isinstance(data, dict):
        candidates = [data.get(key) for key in ("code_url", "codeUrl", "qr_url", "qrUrl")]
    else:
        candidates = [data]
    for candidate in candidates:
        if isinstance(candidate, str) and _QR_RE.match(candidate.strip()):
            return candidate.strip()
    raise PaymentError("PAYMENT_PROVIDER_MISSING_QR_CONTENT")


def _read_string(record: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return ""
