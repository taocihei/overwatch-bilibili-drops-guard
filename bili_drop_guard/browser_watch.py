from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from .bilibili import normalize_room_id, parse_cookie_header
from .config import MAX_WATCH_WINDOWS


LogSink = Callable[[str], None]


@dataclass
class BrowserWatchOptions:
    cookie: str
    room_id: str
    window_count: int = 1
    refresh_interval: int = 30
    page_settle_seconds: float = 2.0


class BrowserWatchSession:
    def __init__(
        self,
        options: BrowserWatchOptions,
        log: LogSink,
        driver_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.options = options
        self.log = log
        self._driver_factory = driver_factory
        self._handles: list[str] = []

    def run(self, stop_event: threading.Event) -> None:
        room_id = normalize_room_id(self.options.room_id)
        if not room_id:
            self.log("直播间号格式不正确，无法打开直播窗口")
            return

        driver = self._create_driver()
        try:
            self._seed_cookies(driver)
            self._open_watch_windows(driver, room_id)
            self.log(f"浏览器观看已启动：{len(self._handles)} 个直播窗口正在计时")
            while not stop_event.wait(max(10, int(self.options.refresh_interval or 30))):
                self._keep_windows_alive(driver)
        finally:
            try:
                driver.quit()
            except Exception:
                pass
            self.log("浏览器观看窗口已关闭")

    def _create_driver(self) -> Any:
        if self._driver_factory:
            return self._driver_factory()

        try:
            from selenium import webdriver
            from selenium.common.exceptions import WebDriverException
            from selenium.webdriver.chrome.options import Options as ChromeOptions
            from selenium.webdriver.edge.options import Options as EdgeOptions
        except ImportError as exc:
            raise RuntimeError("缺少 Selenium 依赖，请先运行：python -m pip install -r requirements.txt") from exc

        errors: list[str] = []
        candidates: list[tuple[str, Any, Any]] = [
            ("Edge", webdriver.Edge, EdgeOptions),
            ("Chrome", webdriver.Chrome, ChromeOptions),
        ]
        for browser_name, driver_factory, options_factory in candidates:
            try:
                options = options_factory()
                self._apply_browser_options(options)
                self.log(f"正在启动 {browser_name} 打开直播窗口")
                return driver_factory(options=options)
            except WebDriverException as exc:
                errors.append(f"{browser_name} 启动失败：{exc.msg or exc}")
            except Exception as exc:
                errors.append(f"{browser_name} 启动失败：{exc}")
        raise RuntimeError("；".join(errors) or "未能启动 Edge/Chrome 观看直播")

    def _apply_browser_options(self, options: Any) -> None:
        for argument in (
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--autoplay-policy=no-user-gesture-required",
            "--mute-audio",
            "--start-maximized",
        ):
            options.add_argument(argument)

    def _seed_cookies(self, driver: Any) -> None:
        cookies = parse_cookie_header(self.options.cookie)
        if not cookies:
            raise RuntimeError("Cookie 为空，无法以登录状态打开直播窗口，请先获取或粘贴 B 站 Cookie")
        if not cookies.get("SESSDATA"):
            raise RuntimeError("Cookie 缺少 SESSDATA，直播窗口无法确认登录状态，请重新获取 Cookie")

        driver.get("https://www.bilibili.com")
        loaded_count = 0
        for name, value in cookies.items():
            cookie_data = {
                "name": name,
                "value": value,
                "domain": ".bilibili.com",
                "path": "/",
            }
            if name in {"SESSDATA", "bili_jct"}:
                cookie_data["secure"] = True
            if self._add_cookie(driver, cookie_data):
                loaded_count += 1
        if loaded_count == 0:
            raise RuntimeError("未能向浏览器写入任何 B 站 Cookie，请重新获取 Cookie")
        driver.get("https://www.bilibili.com")
        self.log(f"已向浏览器写入 {loaded_count} 个 B 站 Cookie")

    def _add_cookie(self, driver: Any, cookie_data: dict[str, Any]) -> bool:
        try:
            driver.add_cookie(cookie_data)
            return True
        except Exception:
            fallback = {key: value for key, value in cookie_data.items() if key != "domain"}
            try:
                driver.add_cookie(fallback)
                return True
            except Exception:
                return False

    def _open_watch_windows(self, driver: Any, room_id: str) -> None:
        window_count = min(max(1, int(self.options.window_count or 1)), MAX_WATCH_WINDOWS)
        live_url = f"https://live.bilibili.com/{room_id}"
        self._handles = []

        for index in range(window_count):
            if index > 0:
                self._open_new_window(driver)
            driver.get(live_url)
            self._handles.append(str(driver.current_window_handle))
            self._prepare_live_page(driver)
            self.log(f"直播窗口 {index + 1}/{window_count} 已打开：{live_url}")

    def _open_new_window(self, driver: Any) -> None:
        if hasattr(driver, "switch_to") and hasattr(driver.switch_to, "new_window"):
            driver.switch_to.new_window("window")
            return
        driver.execute_script("window.open('about:blank', '_blank');")
        driver.switch_to.window(driver.window_handles[-1])

    def _keep_windows_alive(self, driver: Any) -> None:
        for index, handle in enumerate(list(self._handles), start=1):
            try:
                driver.switch_to.window(handle)
                self._prepare_live_page(driver)
            except Exception as exc:
                self.log(f"直播窗口 {index} 保活失败：{exc}")

    def _prepare_live_page(self, driver: Any) -> None:
        if self.options.page_settle_seconds > 0:
            time.sleep(self.options.page_settle_seconds)
        try:
            result = driver.execute_script(
                """
                const videos = Array.from(document.querySelectorAll('video'));
                videos.forEach((video) => {
                    video.muted = true;
                    video.volume = 0;
                    const playResult = video.play();
                    if (playResult && typeof playResult.catch === 'function') {
                        playResult.catch(() => {});
                    }
                });
                return videos.map((video) => ({
                    paused: video.paused,
                    readyState: video.readyState,
                    currentTime: video.currentTime,
                    src: video.currentSrc || video.src || ''
                }));
                """
            )
        except Exception as exc:
            self.log(f"直播页面播放脚本执行失败：{exc}")
            return

        if not result:
            self.log("直播页面暂未检测到视频元素，可能尚未加载完成或被登录/风控页面拦截")
            return
        if not any(not item.get("paused") for item in result if isinstance(item, dict)):
            self.log("直播页面视频仍处于暂停状态，已尝试静音播放；如果进度不增长，请检查页面是否有弹窗")
