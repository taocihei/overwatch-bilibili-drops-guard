from __future__ import annotations

import threading
import unittest

from bili_drop_guard.browser_watch import BrowserWatchOptions, BrowserWatchSession
from bili_drop_guard.config import MAX_WATCH_WINDOWS


class FakeSwitchTo:
    def __init__(self, driver: "FakeDriver") -> None:
        self.driver = driver

    def new_window(self, kind: str) -> None:
        self.driver.created_window_kinds.append(kind)
        handle = f"window-{len(self.driver.window_handles) + 1}"
        self.driver.window_handles.append(handle)
        self.driver.current_window_handle = handle

    def window(self, handle: str) -> None:
        self.driver.current_window_handle = handle


class FakeDriver:
    def __init__(self) -> None:
        self.urls: list[str] = []
        self.cookies: list[dict[str, object]] = []
        self.scripts: list[str] = []
        self.window_handles = ["window-1"]
        self.current_window_handle = "window-1"
        self.created_window_kinds: list[str] = []
        self.switch_to = FakeSwitchTo(self)
        self.quit_called = False

    def get(self, url: str) -> None:
        self.urls.append(url)

    def add_cookie(self, cookie_data: dict[str, object]) -> None:
        self.cookies.append(cookie_data)

    def execute_script(self, script: str) -> None:
        self.scripts.append(script)
        return [{"paused": False, "readyState": 4, "currentTime": 1.0, "src": "live.flv"}]

    def quit(self) -> None:
        self.quit_called = True


class BrowserWatchSessionTest(unittest.TestCase):
    def test_open_watch_windows_uses_configured_window_count(self) -> None:
        driver = FakeDriver()
        logs: list[str] = []
        session = BrowserWatchSession(
            BrowserWatchOptions(
                cookie="SESSDATA=abc; bili_jct=csrf",
                room_id="https://live.bilibili.com/123456",
                window_count=3,
                refresh_interval=10,
                page_settle_seconds=0,
            ),
            logs.append,
            driver_factory=lambda: driver,
        )
        stop_event = threading.Event()
        stop_event.set()

        session.run(stop_event)

        self.assertEqual(driver.urls.count("https://live.bilibili.com/123456"), 3)
        self.assertEqual(driver.created_window_kinds, ["window", "window"])
        self.assertEqual(len(driver.cookies), 2)
        self.assertTrue(driver.quit_called)
        self.assertTrue(any("3 个直播窗口正在计时" in message for message in logs))

    def test_open_watch_windows_clamps_excessive_window_count(self) -> None:
        driver = FakeDriver()
        session = BrowserWatchSession(
            BrowserWatchOptions(
                cookie="SESSDATA=abc",
                room_id="123456",
                window_count=9999,
                refresh_interval=10,
                page_settle_seconds=0,
            ),
            lambda _message: None,
            driver_factory=lambda: driver,
        )
        stop_event = threading.Event()
        stop_event.set()

        session.run(stop_event)

        self.assertEqual(driver.urls.count("https://live.bilibili.com/123456"), MAX_WATCH_WINDOWS)

    def test_missing_sessdata_stops_before_opening_live_windows(self) -> None:
        driver = FakeDriver()
        session = BrowserWatchSession(
            BrowserWatchOptions(
                cookie="bili_jct=csrf",
                room_id="123456",
                window_count=2,
                refresh_interval=10,
                page_settle_seconds=0,
            ),
            lambda _message: None,
            driver_factory=lambda: driver,
        )
        stop_event = threading.Event()
        stop_event.set()

        with self.assertRaisesRegex(RuntimeError, "SESSDATA"):
            session.run(stop_event)

        self.assertNotIn("https://live.bilibili.com/123456", driver.urls)
        self.assertTrue(driver.quit_called)


if __name__ == "__main__":
    unittest.main()
