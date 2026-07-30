from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlparse

import requests


SPONSOR_API_ENV = "BILI_DROPS_SPONSOR_API_URL"
DEFAULT_SPONSOR_API_URL = (
    "https://overwatch-bili-drops-sponsor.wise-mint-4391.chatgpt.site/api/sponsor"
)
SPONSOR_QQ_GROUP = "1012969672"
DEFAULT_TIMEOUT = (5, 15)
ALLOWED_AMOUNTS = (Decimal("3.00"), Decimal("6.00"), Decimal("10.00"))


class SponsorError(RuntimeError):
    """赞助服务返回了无法继续处理的结果。"""


class SponsorUnavailable(SponsorError):
    """赞助服务尚未配置或暂时不可用。"""


@dataclass(frozen=True)
class SponsorOrder:
    order_id: str
    qr_url: str
    expires_at: str = ""


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

    @classmethod
    def from_environment(cls) -> SponsorClient:
        configured_url = os.environ.get(SPONSOR_API_ENV, "").strip()
        return cls(configured_url or DEFAULT_SPONSOR_API_URL)

    def create_order(self, amount: str | Decimal, *, app_version: str) -> SponsorOrder:
        normalized_amount = normalize_amount(amount)
        payload = self._request_json(
            "POST",
            "/orders",
            json={
                "amount": f"{normalized_amount:.2f}",
                "product": "守望先锋 B站直播挂宝赞助",
                "app_version": app_version,
                "provider": "yungouos",
            },
        )
        order_id = str(payload.get("order_id") or "").strip()
        qr_url = str(payload.get("qr_url") or "").strip()
        if not order_id:
            raise SponsorError("赞助服务未返回订单号")
        if not _is_safe_remote_url(qr_url):
            raise SponsorError("赞助服务返回的二维码地址无效")
        return SponsorOrder(
            order_id=order_id,
            qr_url=qr_url,
            expires_at=str(payload.get("expires_at") or "").strip(),
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

    def _request_json(self, method: str, path: str, **kwargs: object) -> dict[str, object]:
        try:
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
    if amount not in ALLOWED_AMOUNTS:
        raise SponsorError("请选择界面提供的赞助金额")
    return amount


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
