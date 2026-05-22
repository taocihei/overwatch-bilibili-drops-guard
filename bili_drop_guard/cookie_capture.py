from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .config import APP_DIR


CookieLog = Callable[[str], None]


@dataclass
class CapturedCookie:
    browser: str
    cookie_header: str


@dataclass
class AttachedBrowser:
    process: subprocess.Popen[Any]
    profile_dir: Path


BILIBILI_LOGIN_URL = "https://passport.bilibili.com/login"


def open_bilibili_login_page(log: CookieLog | None = None) -> str:
    browser = _find_local_browser()
    if browser:
        subprocess.Popen([browser, BILIBILI_LOGIN_URL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        browser_name = "Edge" if "edge" in browser.lower() else "Chrome"
        _log(log, f"已打开 {browser_name} 的 B 站登录页")
        return browser_name

    os.startfile(BILIBILI_LOGIN_URL)
    _log(log, "已用系统默认浏览器打开 B 站登录页")
    return "默认浏览器"


def capture_bilibili_cookie(timeout_seconds: int = 180, log: CookieLog | None = None) -> CapturedCookie:
    try:
        from selenium.common.exceptions import WebDriverException
        from selenium.webdriver.chrome.options import Options as ChromeOptions
        from selenium.webdriver.edge.options import Options as EdgeOptions
    except ImportError as exc:
        raise RuntimeError("缺少 Selenium 依赖，请先运行：python -m pip install -r requirements.txt") from exc

    errors: list[str] = []
    candidates: list[tuple[str, str, Any]] = [
        ("Edge", "selenium.webdriver.edge.webdriver", EdgeOptions),
        ("Chrome", "selenium.webdriver.chrome.webdriver", ChromeOptions),
    ]

    for browser_name, driver_module, options_factory in candidates:
        driver = None
        attached_browser: AttachedBrowser | None = None
        try:
            options = options_factory()
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--disable-infobars")
            options.add_argument("--start-maximized")
            _log(log, f"正在拉起独立 {browser_name} 登录窗口")
            attached_browser = _launch_browser_for_attach(browser_name, options, log)
            if not attached_browser:
                raise RuntimeError(f"未找到本机 {browser_name} 浏览器")
            driver_factory = _load_webdriver_class(driver_module)
            driver = driver_factory(options=options)
            return _wait_for_cookie(driver, browser_name, timeout_seconds, log)
        except WebDriverException as exc:
            errors.append(f"{browser_name} 启动失败：{exc.msg or exc}")
        except Exception as exc:
            errors.append(f"{browser_name} 获取失败：{exc}")
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
            if attached_browser is not None:
                _close_attached_browser(attached_browser)

    try:
        browser_name = open_bilibili_login_page(log)
        errors.append(f"已为你打开 {browser_name} 登录页，但自动读取 Cookie 失败")
    except Exception as exc:
        errors.append(f"兜底打开浏览器失败：{exc}")
    raise RuntimeError("；".join(errors) or "未能启动 Edge/Chrome 自动获取 Cookie")


def _wait_for_cookie(driver: Any, browser_name: str, timeout_seconds: int, log: CookieLog | None) -> CapturedCookie:
    driver.get(BILIBILI_LOGIN_URL)
    _log(log, "浏览器已打开，请完成 B 站登录；检测到 SESSDATA 后会自动关闭浏览器")
    deadline = time.time() + max(30, timeout_seconds)
    last_hint_at = 0.0
    last_live_probe_at = 0.0

    while time.time() < deadline:
        cookies = _read_bilibili_cookies(driver)
        cookie_map = {item.get("name"): item.get("value") for item in cookies if item.get("name")}
        if cookie_map.get("SESSDATA"):
            cookie_header = _build_cookie_header(cookies)
            return CapturedCookie(browser=browser_name, cookie_header=cookie_header)

        now = time.time()
        if now - last_live_probe_at >= 20:
            _open_live_probe(driver)
            last_live_probe_at = now
        if now - last_hint_at >= 10:
            _log(log, "尚未检测到登录 Cookie，请确认已在打开的浏览器里完成登录")
            last_hint_at = now
        time.sleep(2)

    raise RuntimeError("等待登录超时，未检测到 SESSDATA Cookie")


def _read_bilibili_cookies(driver: Any) -> list[dict[str, Any]]:
    cookies: list[dict[str, Any]] = []
    try:
        result = driver.execute_cdp_cmd("Network.getAllCookies", {})
        cookies.extend(result.get("cookies") or [])
    except Exception:
        pass

    try:
        cookies.extend(driver.get_cookies())
    except Exception:
        pass

    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if not name or value is None:
            continue
        domain = str(cookie.get("domain") or "")
        if domain and "bilibili.com" not in domain:
            continue
        key = (str(name), domain, str(cookie.get("path") or ""))
        deduped[key] = cookie
    return list(deduped.values())


def _build_cookie_header(cookies: list[dict[str, Any]]) -> str:
    preferred = {"SESSDATA": 0, "bili_jct": 1, "DedeUserID": 2, "DedeUserID__ckMd5": 3, "buvid3": 4, "buvid4": 5}
    sorted_cookies = sorted(cookies, key=lambda item: (preferred.get(str(item.get("name")), 100), str(item.get("name"))))
    return "; ".join(
        f"{item['name']}={item['value']}"
        for item in sorted_cookies
        if item.get("name") and item.get("value") is not None
    )


def _open_live_probe(driver: Any) -> None:
    try:
        current_url = str(getattr(driver, "current_url", "") or "")
        if "passport.bilibili.com" in current_url:
            return
        driver.get("https://live.bilibili.com")
    except Exception:
        pass


def _launch_browser_for_attach(browser_name: str, options: Any, log: CookieLog | None) -> AttachedBrowser | None:
    browser = _find_local_browser(browser_name)
    if not browser:
        return None

    port = _find_free_port()
    profile_dir = APP_DIR / "cookie-browser-profile" / browser_name.lower() / uuid4().hex
    profile_dir.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            browser,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            BILIBILI_LOGIN_URL,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    options.debugger_address = f"127.0.0.1:{port}"
    if not _wait_for_debugger_port(port, timeout_seconds=15):
        try:
            process.terminate()
        except Exception:
            pass
        raise RuntimeError(f"{browser_name} 已启动但调试端口未就绪，请重试或使用“只打开登录页”")
    _log(log, f"已拉起 {browser_name} 的 B 站登录页，正在连接浏览器读取 Cookie")
    return AttachedBrowser(process=process, profile_dir=profile_dir)


def _close_attached_browser(attached_browser: AttachedBrowser) -> None:
    process = attached_browser.process
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=3)
        return
    except Exception:
        pass
    try:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        pass


def _load_webdriver_class(module_name: str) -> Any:
    module = import_module(module_name)
    return getattr(module, "WebDriver")


def _wait_for_debugger_port(port: int, timeout_seconds: float = 15.0) -> bool:
    deadline = time.time() + timeout_seconds
    url = f"http://127.0.0.1:{port}/json/version"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                return response.status == 200
        except Exception:
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
