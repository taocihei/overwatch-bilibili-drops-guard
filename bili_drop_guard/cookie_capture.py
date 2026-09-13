from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import websocket

from .config import APP_DIR


CookieLog = Callable[[str], None]


class CaptureCancelled(RuntimeError):
    pass


def _check_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise CaptureCancelled("自动获取已取消")


@dataclass
class CapturedCookie:
    browser: str
    cookie_header: str


@dataclass
class AttachedBrowser:
    process: subprocess.Popen[Any]
    profile_dir: Path
    debug_port: int = 0


BILIBILI_LOGIN_URL = "https://passport.bilibili.com/login"


def _browser_startupinfo() -> subprocess.STARTUPINFO | None:
    if os.name != "nt":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 1  # SW_SHOWNORMAL; inherit the caller's interactive desktop.
    return si


def open_bilibili_login_page(log: CookieLog | None = None) -> str:
    browser = _find_local_browser()
    if browser:
        try:
            subprocess.Popen(
                [browser, "--new-window", BILIBILI_LOGIN_URL],
                startupinfo=_browser_startupinfo(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            browser_name = "Edge" if "edge" in browser.lower() else "Chrome"
            _log(log, f"已打开 {browser_name} 的 B 站登录页")
            return browser_name
        except Exception:
            pass

    try:
        import webbrowser
        if webbrowser.open(BILIBILI_LOGIN_URL, new=1):
            _log(log, "已用系统默认浏览器打开 B 站登录页")
            return "默认浏览器"
    except Exception:
        pass

    if os.name == "nt":
        os.startfile(BILIBILI_LOGIN_URL)
        _log(log, "已用系统默认浏览器打开 B 站登录页")
        return "默认浏览器"
    raise RuntimeError("未能启动浏览器打开登录页")


def capture_bilibili_cookie(timeout_seconds: int = 180, log: CookieLog | None = None,
                            cancel_event: threading.Event | None = None) -> CapturedCookie:
    _check_cancelled(cancel_event)
    errors: list[str] = []
    for browser_name in ("Edge", "Chrome"):
        _check_cancelled(cancel_event)
        attached = None
        connection = None
        try:
            _log(log, f"正在打开 {browser_name} 登录窗口（无需下载驱动）")
            attached = _launch_browser_for_attach(browser_name, log, cancel_event)
            if attached is None:
                continue
            connection = _CookieBrowser(attached.debug_port, cancel_event)
        except CaptureCancelled:
            if attached is not None:
                _close_attached_browser(attached)
            raise
        except Exception as exc:
            if attached is not None:
                _close_attached_browser(attached)
            errors.append(f"{browser_name}：{exc}")
            _log(log, f"{browser_name} 自动获取连接失败：{exc}")
            continue
        try:
            return _wait_for_cookie(connection, browser_name, timeout_seconds, log, cancel_event)
        finally:
            try:
                connection.close()
            finally:
                _close_attached_browser(attached)
    raise RuntimeError("；".join(errors) or "未找到 Edge/Chrome，请先安装其中一种浏览器")


class _CookieBrowser:
    """Own one browser-level CDP connection; never invoke a WebDriver manager."""

    def __init__(self, port: int, cancel_event: threading.Event | None = None) -> None:
        self._cancel_event = cancel_event
        self._next_id = 0
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/json/version", timeout=3) as response:
            endpoint = json.load(response).get("webSocketDebuggerUrl", "")
        parsed = urlsplit(endpoint)
        if parsed.scheme != "ws" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.port != port:
            raise RuntimeError("浏览器返回的本机调试地址无效")
        _check_cancelled(cancel_event)
        self._socket = websocket.create_connection(
            endpoint, timeout=0.5, suppress_origin=True,
            http_no_proxy=["localhost", "127.0.0.1", "::1"],
        )

    def call(self, method: str, params: dict | None = None) -> dict:
        _check_cancelled(self._cancel_event)
        self._next_id += 1
        request_id = self._next_id
        self._socket.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            _check_cancelled(self._cancel_event)
            try:
                raw = self._socket.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not raw:
                raise RuntimeError("登录浏览器已关闭，请重新点击自动获取")
            message = json.loads(raw)
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(f"浏览器命令 {method} 失败")
            return message.get("result") or {}
        raise RuntimeError("连接登录浏览器超时，请重试")

    def close(self) -> None:
        try:
            # Closing our dedicated browser must also work after cancellation.
            self._next_id += 1
            self._socket.send(json.dumps({"id": self._next_id, "method": "Browser.close"}))
        except (OSError, websocket.WebSocketException):
            pass
        finally:
            self._socket.close()


def _wait_for_cookie(driver: Any, browser_name: str, timeout_seconds: int, log: CookieLog | None,
                     cancel_event: threading.Event | None = None) -> CapturedCookie:
    _check_cancelled(cancel_event)
    _log(log, "浏览器已打开，请完成 B 站登录；检测到 SESSDATA 后会自动关闭浏览器")
    deadline = time.monotonic() + max(30, timeout_seconds)
    last_hint_at = 0.0

    while time.monotonic() < deadline:
        _check_cancelled(cancel_event)
        cookies = _read_bilibili_cookies(driver)
        cookie_map = {item.get("name"): item.get("value") for item in cookies if item.get("name")}
        if cookie_map.get("SESSDATA"):
            cookie_header = _build_cookie_header(cookies)
            return CapturedCookie(browser=browser_name, cookie_header=cookie_header)

        now = time.monotonic()
        if now - last_hint_at >= 10:
            _log(log, "尚未检测到登录 Cookie，请确认已在打开的浏览器里完成登录")
            last_hint_at = now
        if cancel_event is not None:
            cancel_event.wait(2)
        else:
            time.sleep(2)

    raise RuntimeError("等待登录超时，未检测到 SESSDATA Cookie")


def _read_bilibili_cookies(driver: Any) -> list[dict[str, Any]]:
    # Storage.getCookies includes HttpOnly cookies in this isolated browser profile.
    cookies = driver.call("Storage.getCookies").get("cookies") or []

    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if not name or value is None:
            continue
        domain = str(cookie.get("domain") or "")
        normalized_domain = domain.lstrip(".").lower()
        if domain and normalized_domain != "bilibili.com" and not normalized_domain.endswith(".bilibili.com"):
            continue
        expires = cookie.get("expires") or cookie.get("expiry")
        try:
            # CDP uses -1 for session cookies, not an expired Unix timestamp.
            if expires and 0 < float(expires) <= time.time():
                continue
        except (TypeError, ValueError):
            pass
        if not value and str(name) in {"SESSDATA", "bili_jct", "DedeUserID"}:
            continue
        key = (str(name), domain, str(cookie.get("path") or ""))
        deduped[key] = cookie
    return list(deduped.values())


def _build_cookie_header(cookies: list[dict[str, Any]]) -> str:
    preferred = {"SESSDATA": 0, "bili_jct": 1, "DedeUserID": 2, "DedeUserID__ckMd5": 3, "buvid3": 4, "buvid4": 5}
    # Chromium 可能同时返回相同 name 的 host-only、父域和旧 path Cookie。Cookie header
    # 中重复 name 的解析结果取决于顺序，必须先确定唯一候选，避免旧值覆盖有效登录态。
    selected: dict[str, dict[str, Any]] = {}
    for item in cookies:
        name = str(item.get("name") or "")
        if not name or item.get("value") is None:
            continue
        current = selected.get(name)
        if current is None or _cookie_specificity(item) > _cookie_specificity(current):
            selected[name] = item
    sorted_cookies = sorted(
        selected.values(),
        key=lambda item: (preferred.get(str(item.get("name")), 100), str(item.get("name"))),
    )
    return "; ".join(
        f"{item['name']}={item['value']}"
        for item in sorted_cookies
        if item.get("name") and item.get("value") is not None
    )


def _cookie_specificity(cookie: dict[str, Any]) -> tuple[int, int, float]:
    domain = str(cookie.get("domain") or "").lstrip(".").lower()
    broad_domain = 2 if domain == "bilibili.com" else 1
    root_path = 1 if str(cookie.get("path") or "/") == "/" else 0
    try:
        expiry = float(cookie.get("expires") or cookie.get("expiry") or 0)
    except (TypeError, ValueError):
        expiry = 0.0
    return broad_domain, root_path, expiry


def _launch_browser_for_attach(browser_name: str, log: CookieLog | None = None,
                               cancel_event: threading.Event | None = None) -> AttachedBrowser | None:
    _check_cancelled(cancel_event)
    browser = _find_local_browser(browser_name)
    if not browser:
        return None

    port = _find_free_port()
    profile_dir = _capture_profile_dir(browser_name)
    try:
        process = subprocess.Popen(
            [
                browser,
                "--new-window",
                f"--remote-debugging-port={port}",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={profile_dir}",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-first-run",
                "--no-default-browser-check",
                "--start-maximized",
                BILIBILI_LOGIN_URL,
            ],
            startupinfo=_browser_startupinfo(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        _remove_capture_profile(profile_dir)
        raise
    attached = AttachedBrowser(process=process, profile_dir=profile_dir, debug_port=port)
    try:
        if not _wait_for_debugger_port(port, timeout_seconds=15, cancel_event=cancel_event):
            raise RuntimeError(f"{browser_name} 已启动但调试端口未就绪，请重试或使用“只打开登录页”")
    except Exception:
        _close_attached_browser(attached)
        raise
    _log(log, f"已拉起 {browser_name} 的 B 站登录页，正在连接浏览器读取 Cookie")
    return attached


def _capture_profile_dir(browser_name: str) -> Path:
    safe_name = "".join(ch for ch in browser_name.lower() if ch.isalnum() or ch in {"-", "_"}) or "browser"
    parent = APP_DIR / "cookie-browser-profile" / safe_name
    parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="capture-", dir=parent))


def _close_attached_browser(attached_browser: AttachedBrowser) -> None:
    process = attached_browser.process
    try:
        if process.poll() is None:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                        timeout=5, check=False,
                    )
    finally:
        if process.poll() is not None:
            _remove_capture_profile(attached_browser.profile_dir)


def _remove_capture_profile(profile_dir: Path) -> None:
    target = profile_dir.resolve()
    root = (APP_DIR / "cookie-browser-profile").resolve()
    if not target.is_relative_to(root) or not target.name.startswith("capture-"):
        return
    for attempt in range(3):
        try:
            shutil.rmtree(target)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt < 2:
                time.sleep(0.1)


def _load_webdriver_class(module_name: str) -> Any:
    module = import_module(module_name)
    return getattr(module, "WebDriver")


def _wait_for_debugger_port(port: int, timeout_seconds: float = 15.0,
                            cancel_event: threading.Event | None = None) -> bool:
    deadline = time.monotonic() + timeout_seconds
    url = f"http://127.0.0.1:{port}/json/version"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        _check_cancelled(cancel_event)
        try:
            with opener.open(url, timeout=0.5) as response:
                return response.status == 200
        except Exception:
            if cancel_event is not None:
                cancel_event.wait(0.25)
            else:
                time.sleep(0.25)
    return False


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _find_local_browser(preferred: str = "") -> str:
    preferred = preferred.lower()
    candidates = [
        ("edge", Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
        ("edge", Path(os.environ.get("ProgramFiles", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
        ("edge", Path(os.environ.get("LocalAppData", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
        ("chrome", Path(os.environ.get("ProgramFiles", "")) / "Google" / "Chrome" / "Application" / "chrome.exe"),
        ("chrome", Path(os.environ.get("ProgramFiles(x86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe"),
        ("chrome", Path(os.environ.get("LocalAppData", "")) / "Google" / "Chrome" / "Application" / "chrome.exe"),
    ]
    if preferred:
        ordered = [item for item in candidates if preferred in item[0]]
    else:
        ordered = candidates
    for _name, candidate in ordered:
        if candidate.exists():
            return str(candidate)
    return ""


def _log(log: CookieLog | None, message: str) -> None:
    if log:
        log(message)
