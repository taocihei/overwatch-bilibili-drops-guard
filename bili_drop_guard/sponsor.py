from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
import re
import threading
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlparse

import requests
import qrcode


SPONSOR_API_ENV = "BILI_DROPS_SPONSOR_API_URL"
DEFAULT_SPONSOR_API_URL = (
    "https://overwatch-bili-drops-sponsor.wise-mint-4391.chatgpt.site/api/sponsor"
)
SPONSOR_QQ_GROUP = "1012969672"
DEFAULT_TIMEOUT = (5, 15)
WARM_UP_TIMEOUT = (5, 12)
SPONSOR_PRESET_AMOUNTS = (
    Decimal("5.00"),
    Decimal("10.00"),
    Decimal("20.00"),
    Decimal("50.00"),
    Decimal("100.00"),
)
MIN_SPONSOR_AMOUNT = Decimal("1.00")
MAX_SPONSOR_AMOUNT = Decimal("9999.00")


class SponsorError(RuntimeError):
    """赞助服务返回了无法继续处理的结果。"""


class SponsorUnavailable(SponsorError):
    """赞助服务尚未配置或暂时不可用。"""


@dataclass(frozen=True)
class SponsorOrder:
    order_id: str
    amount: str = ""
    qr_url: str = ""
    fallback_qr_url: str = ""
    expires_at: str = ""
    qr_content: str = ""
    expires_in_seconds: int = 0


@dataclass(frozen=True)
class SponsorOrderBatch:
    checkout_intent_id: str
    orders: tuple[SponsorOrder, ...]


@dataclass(frozen=True)
class SponsorOrderStatus:
    state: str
    message: str = ""

    @property
    def paid(self) -> bool:
        return self.state == "paid"

    @property
    def terminal(self) -> bool:
        return self.state in {"paid", "expired", "closed"}


class SponsorClient:
    """桌面端赞助接口客户端。

    商户密钥只保存在服务端。服务端调用 YunGouOS 原生扫码支付接口，
    桌面端只接收二维码地址和脱敏后的订单状态。
    """

    def __init__(
        self,
        base_url: str,
        *,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = _normalize_base_url(base_url)
        self.session = session or requests.Session()
        self.timeout = timeout
        self._session_lock = threading.RLock()

    @classmethod
    def from_environment(cls) -> SponsorClient:
        configured_url = os.environ.get(SPONSOR_API_ENV, "").strip()
        return cls(configured_url or DEFAULT_SPONSOR_API_URL)

    def new_session_client(self) -> SponsorClient:
        """Return an equivalent client backed by a new HTTP session.

        Sponsor prefetches and status checks run concurrently.  Sharing one
        ``requests.Session`` would serialize them behind ``_session_lock`` and
        makes changing an amount appear much slower than the payment service.
        """

        return SponsorClient(self.base_url, timeout=self.timeout)

    def close(self) -> None:
        self.session.close()

    def warm_up(self) -> bool:
        """提前唤醒赞助服务并复用已建立的 HTTPS 连接。"""

        health_url = (
            f"{self.base_url.removesuffix('/sponsor')}/health"
            if self.base_url.endswith("/sponsor")
            else f"{self.base_url}/health"
        )
        try:
            with self._session_lock:
                response = self.session.request(
                    "GET",
                    health_url,
                    timeout=WARM_UP_TIMEOUT,
                )
                response.raise_for_status()
        except (requests.RequestException, ValueError):
            return False
        return True

    def create_order(
        self,
        amount: str | Decimal,
        *,
        app_version: str,
        install_id: str,
        checkout_intent_id: str,
    ) -> SponsorOrder:
        normalized_amount = normalize_amount(amount)
        normalized_install_id = normalize_client_key(install_id, minimum_length=16)
        normalized_intent_id = normalize_client_key(checkout_intent_id, minimum_length=8)
        payload = self._request_json(
            "POST",
            "/orders",
            json={
                "amount": f"{normalized_amount:.2f}",
                "product": "守望先锋 B站直播挂宝赞助",
                "app_version": app_version,
                "provider": "yungouos",
                "install_id": normalized_install_id,
                "checkout_intent_id": normalized_intent_id,
            },
        )
        return self._parse_order(payload)

    def reserve_orders(
        self,
        amounts: tuple[str | Decimal, ...] | list[str | Decimal],
        *,
        app_version: str,
        install_id: str,
        checkout_intent_id: str,
    ) -> SponsorOrderBatch:
        normalized_amounts = tuple(normalize_amount(amount) for amount in amounts)
        if not normalized_amounts or len(normalized_amounts) > 5:
            raise SponsorError("批量预留需要包含 1–5 个赞助金额")
        if len(set(normalized_amounts)) != len(normalized_amounts):
            raise SponsorError("批量预留金额不能重复")
        normalized_install_id = normalize_client_key(install_id, minimum_length=16)
        normalized_intent_id = normalize_client_key(checkout_intent_id, minimum_length=8)
        payload = self._request_json(
            "POST",
            "/orders",
            json={
                "amounts": [f"{amount:.2f}" for amount in normalized_amounts],
                "product": "守望先锋 B站直播挂宝赞助",
                "app_version": app_version,
                "provider": "yungouos",
                "install_id": normalized_install_id,
                "checkout_intent_id": normalized_intent_id,
            },
        )
        response_intent_id = normalize_client_key(
            str(payload.get("checkout_intent_id") or ""),
            minimum_length=8,
        )
        if response_intent_id != normalized_intent_id:
            raise SponsorError("赞助服务返回的结算意图标识不匹配")
        raw_orders = payload.get("orders")
        if not isinstance(raw_orders, list) or len(raw_orders) != len(normalized_amounts):
            raise SponsorError("赞助服务返回的批量订单数量不匹配")
        orders = tuple(
            self._parse_order(raw_order)
            for raw_order in raw_orders
            if isinstance(raw_order, dict)
        )
        if len(orders) != len(normalized_amounts):
            raise SponsorError("赞助服务返回的批量订单格式无效")
        expected_amounts = {f"{amount:.2f}" for amount in normalized_amounts}
        returned_amounts = {order.amount for order in orders}
        if returned_amounts != expected_amounts:
            raise SponsorError("赞助服务返回的批量订单金额不匹配")
        return SponsorOrderBatch(
            checkout_intent_id=response_intent_id,
            orders=orders,
        )

    @staticmethod
    def _parse_order(payload: dict[str, object]) -> SponsorOrder:
        order_id = str(payload.get("order_id") or "").strip()
        try:
            amount = f"{normalize_amount(str(payload.get('amount') or '')):.2f}"
        except SponsorError:
            amount = ""
        qr_url = str(payload.get("qr_url") or "").strip()
        fallback_qr_url = str(payload.get("fallback_qr_url") or "").strip()
        qr_content = str(payload.get("qr_content") or "").strip()
        if not order_id:
            raise SponsorError("赞助服务未返回订单号")
        if qr_url and not _is_safe_remote_url(qr_url):
            raise SponsorError("赞助服务返回的二维码地址无效")
        if fallback_qr_url and not _is_safe_remote_url(fallback_qr_url):
            raise SponsorError("赞助服务返回的备用二维码地址无效")
        if qr_content and not _is_safe_qr_content(qr_content):
            raise SponsorError("赞助服务返回的二维码内容无效")
        if not qr_content and not qr_url and not fallback_qr_url:
            raise SponsorError("赞助服务未返回二维码")
        try:
            expires_in_seconds = max(
                0,
                min(24 * 60 * 60, int(float(payload.get("expires_in_seconds") or 0))),
            )
        except (TypeError, ValueError):
            expires_in_seconds = 0
        return SponsorOrder(
            order_id=order_id,
            amount=amount,
            qr_url=qr_url,
            fallback_qr_url=fallback_qr_url,
            expires_at=str(payload.get("expires_at") or "").strip(),
            qr_content=qr_content,
            expires_in_seconds=expires_in_seconds,
        )

    def query_order(self, order_id: str) -> SponsorOrderStatus:
        normalized_id = str(order_id or "").strip()
        if not normalized_id:
            raise SponsorError("缺少赞助订单号")
        payload = self._request_json("GET", f"/orders/{quote(normalized_id, safe='')}")
        state = str(payload.get("status") or "").strip().lower()
        aliases = {
            "success": "paid",
            "succeeded": "paid",
            "pending": "pending",
            "paying": "pending",
            "expired": "expired",
            "closed": "closed",
        }
        state = aliases.get(state, state)
        if state not in {"pending", "paid", "expired", "closed"}:
            raise SponsorError("赞助服务返回了未知订单状态")
        return SponsorOrderStatus(
            state=state,
            message=str(payload.get("message") or "").strip(),
        )

    def download_qr(self, qr_url: str) -> bytes:
        if not _is_safe_remote_url(qr_url):
            raise SponsorError("二维码地址无效")
        try:
            with self._session_lock:
                response = self.session.get(qr_url, timeout=self.timeout)
                response.raise_for_status()
        except requests.RequestException as exc:
            raise SponsorUnavailable("二维码加载失败，请稍后重试") from exc
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if content_type and not content_type.startswith("image/"):
            raise SponsorError("二维码地址没有返回图片")
        if not response.content:
            raise SponsorError("二维码图片内容为空")
        return response.content

    def download_order_qr(self, order: SponsorOrder) -> bytes:
        """优先本地生成微信付款码，兼容旧服务的远程二维码。"""

        if order.qr_content:
            return _render_qr_png(order.qr_content)

        urls = tuple(dict.fromkeys(filter(None, (order.qr_url, order.fallback_qr_url))))
        last_error: SponsorError | None = None
        for url in urls:
            try:
                return self.download_qr(url)
            except SponsorError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise SponsorError("赞助服务未返回二维码地址")

    def _request_json(self, method: str, path: str, **kwargs: object) -> dict[str, object]:
        try:
            with self._session_lock:
                response = self.session.request(
                    method,
                    f"{self.base_url}{path}",
                    timeout=self.timeout,
                    **kwargs,
                )
                response.raise_for_status()
                payload = response.json()
        except requests.RequestException as exc:
            raise SponsorUnavailable("赞助服务暂时不可用，请稍后重试") from exc
        except ValueError as exc:
            raise SponsorError("赞助服务返回的数据格式无效") from exc
        if not isinstance(payload, dict):
            raise SponsorError("赞助服务返回的数据格式无效")
        if payload.get("ok") is False:
            raise SponsorError(
                str(
                    payload.get("error")
                    or payload.get("message")
                    or "赞助服务请求失败"
                )
            )
        data = payload.get("data", payload)
        if not isinstance(data, dict):
            raise SponsorError("赞助服务返回的数据格式无效")
        return data


def normalize_amount(value: str | Decimal) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise SponsorError("赞助金额无效") from exc
    if not amount.is_finite() or amount < MIN_SPONSOR_AMOUNT or amount > MAX_SPONSOR_AMOUNT:
        raise SponsorError("赞助金额需在 1–9999 元之间")
    return amount


def normalize_client_key(
    value: str,
    *,
    minimum_length: int,
    maximum_length: int = 128,
) -> str:
    normalized = str(value or "").strip()
    if (
        len(normalized) < minimum_length
        or len(normalized) > maximum_length
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", normalized) is None
    ):
        raise SponsorError("赞助客户端标识无效")
    return normalized


def create_checkout_intent_id() -> str:
    return f"checkout-{uuid.uuid4().hex}"


def load_or_create_install_id(path: Path) -> str:
    """Load the stable anonymous installation ID, atomically creating it once."""

    try:
        current = path.read_text(encoding="ascii").strip()
        return normalize_client_key(current, minimum_length=16)
    except (OSError, UnicodeError, SponsorError):
        pass

    install_id = f"desktop-{uuid.uuid4().hex}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temporary_path.write_text(install_id, encoding="ascii")
        os.replace(temporary_path, path)
        persisted = path.read_text(encoding="ascii").strip()
        return normalize_client_key(persisted, minimum_length=16)
    except (OSError, UnicodeError, SponsorError):
        return install_id


def _normalize_base_url(value: str) -> str:
    url = str(value or "").strip().rstrip("/")
    if not url:
        raise SponsorUnavailable("赞助通道尚未配置")
    parsed = urlparse(url)
    is_local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and is_local):
        raise SponsorUnavailable("赞助服务地址必须使用 HTTPS")
    if not parsed.netloc or parsed.username or parsed.password:
        raise SponsorUnavailable("赞助服务地址无效")
    return url


def _is_safe_remote_url(value: str) -> bool:
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return False
    is_local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    return bool(
        parsed.netloc
        and not parsed.username
        and not parsed.password
        and (parsed.scheme == "https" or (parsed.scheme == "http" and is_local))
    )


def _is_safe_qr_content(value: str) -> bool:
    normalized = str(value or "").strip()
    return bool(
        normalized.lower().startswith("weixin://")
        and len(normalized) <= 4096
        and not any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    )


def _render_qr_png(content: str) -> bytes:
    if not _is_safe_qr_content(content):
        raise SponsorError("二维码内容无效")
    try:
        image = qrcode.make(content)
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()
    except Exception as exc:
        raise SponsorError("二维码生成失败，请稍后重试") from exc
