from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, InvalidOperation
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .payment import PaymentError
from .service import SponsorService, ServiceError


ORDER_PATH = re.compile(r"^/api/sponsor/orders/([a-f0-9]{32})$")
CLIENT_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
MIN_AMOUNT_CENTS = 100
MAX_AMOUNT_CENTS = 999_900
MAX_BATCH_SIZE = 5


class SponsorHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], service: SponsorService) -> None:
        super().__init__(address, SponsorRequestHandler)
        self.service = service


class SponsorRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SponsorService/0.5.22"

    @property
    def service(self) -> SponsorService:
        return self.server.service  # type: ignore[attr-defined,no-any-return]

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/api/health":
            self._json(
                {
                    "ok": True,
                    "service": "overwatch-bilibili-drops-sponsor",
                    "version": __version__,
                    "pool": {str(key): value for key, value in self.service.pool_counts().items()},
                }
            )
            return
        match = ORDER_PATH.fullmatch(path)
        if match:
            query = parse_qs(urlsplit(self.path).query)
            token = self.headers.get("X-Status-Token", "") or query.get("token", [""])[0]
            try:
                status = self.service.get_status(match.group(1), token=token)
            except ServiceError as exc:
                if str(exc) == "STATUS_TOKEN_INVALID":
                    self._json({"ok": False, "error": "状态凭证无效。"}, 403)
                    return
                raise
            if status is None:
                self._json({"ok": False, "error": "订单不存在。"}, 404)
            else:
                self._json({"ok": True, "data": status})
            return
        self._json({"ok": False, "error": "接口不存在。"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/api/sponsor/orders":
            self._create_orders()
            return
        if path == "/api/sponsor/callback":
            self._callback()
            return
        self._json({"ok": False, "error": "接口不存在。"}, 404)

    def _create_orders(self) -> None:
        try:
            payload = self._read_json(4096)
            provider = payload.get("provider")
            if provider is not None and str(provider).lower() != "yungouos":
                raise RequestError("不支持该支付通道。")
            amounts, is_batch = _normalize_amounts(payload)
            install_id = _client_key(payload.get("install_id"), 16, 128)
            checkout_id = _client_key(payload.get("checkout_intent_id"), 8, 128)
            if not install_id:
                raise RequestError("缺少有效的客户端安装标识。")
            if not checkout_id:
                raise RequestError("缺少有效的结算意图标识。")
            app_version = _safe_text(payload.get("app_version"), 24) or "unknown"
            results = self.service.reserve_many(
                amounts,
                install_id=install_id,
                checkout_intent_id=checkout_id,
                app_version=app_version,
            )
            orders = [self.service.order_payload(result) for result in results]
            status = 201 if any(result.allocation == "created" for result in results) else 200
            data: dict[str, Any] | Any
            if is_batch:
                data = {"checkout_intent_id": checkout_id, "orders": orders}
            else:
                data = orders[0]
            self._json({"ok": True, "data": data}, status)
        except RequestError as exc:
            self._json({"ok": False, "error": str(exc)}, exc.status)
        except PaymentError:
            logging.getLogger("sponsor.http").exception("payment provider failure")
            self._json({"ok": False, "error": "支付通道暂时繁忙，请稍后重试。"}, 502)
        except ServiceError:
            logging.getLogger("sponsor.http").exception("order service failure")
            self._json({"ok": False, "error": "订单正在生成，请稍后重试。"}, 503)
        except Exception:
            logging.getLogger("sponsor.http").exception("unexpected create-order failure")
            self._json({"ok": False, "error": "服务暂时繁忙，请稍后重试。"}, 500)

    def _callback(self) -> None:
        try:
            raw = self._read_body(8192).decode("utf-8")
            parsed = parse_qs(raw, keep_blank_values=True, strict_parsing=False)
            values = {key: items[-1] if items else "" for key, items in parsed.items()}
            body, status = self.service.process_callback(values)
        except (UnicodeDecodeError, RequestError):
            body, status = "FAIL", 400
        except Exception:
            logging.getLogger("sponsor.http").exception("callback processing failure")
            body, status = "FAIL", 500
        self._text(body, status)

    def _read_json(self, limit: int) -> dict[str, Any]:
        raw = self._read_body(limit)
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RequestError("请求数据格式无效。") from exc
        if not isinstance(value, dict):
            raise RequestError("请求数据格式无效。")
        return value

    def _read_body(self, limit: int) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise RequestError("Content-Length 无效。") from exc
        if length <= 0:
            raise RequestError("请求内容为空。")
        if length > limit:
            raise RequestError("请求内容过大。", 413)
        body = self.rfile.read(length)
        if len(body) != length:
            raise RequestError("请求内容不完整。")
        return body

    def _json(self, value: object, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, value: str, status: int = 200) -> None:
        body = value.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, message: str, *args: object) -> None:
        logging.getLogger("sponsor.access").info(
            "%s %s", self.address_string(), message % args
        )


class RequestError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _normalize_amounts(payload: dict[str, Any]) -> tuple[list[int], bool]:
    is_batch = isinstance(payload.get("amounts"), list)
    raw_values = payload["amounts"] if is_batch else [payload.get("amount")]
    if not raw_values or len(raw_values) > MAX_BATCH_SIZE:
        raise RequestError("每次最多可预留 5 个赞助金额。")
    result: list[int] = []
    for value in raw_values:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise RequestError("赞助金额需要在 1–9999 元之间。") from exc
        if not number.is_finite() or number.as_tuple().exponent < -2:
            raise RequestError("赞助金额需要在 1–9999 元之间。")
        cents = int(number * 100)
        if cents < MIN_AMOUNT_CENTS or cents > MAX_AMOUNT_CENTS:
            raise RequestError("赞助金额需要在 1–9999 元之间。")
        if cents in result:
            raise RequestError("批量预留金额不能重复。")
        result.append(cents)
    return result, is_batch


def _client_key(value: object, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip()
    if not minimum <= len(normalized) <= maximum or not CLIENT_KEY.fullmatch(normalized):
        return ""
    return normalized


def _safe_text(value: object, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().replace("\r", " ").replace("\n", " ").replace("\t", " ")[:maximum]
