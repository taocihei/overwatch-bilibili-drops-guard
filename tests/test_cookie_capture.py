from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bili_drop_guard import cookie_capture
from bili_drop_guard.cookie_capture import BILIBILI_LOGIN_URL, _launch_browser_for_attach, _wait_for_cookie, open_bilibili_login_page


class FakeDriver:
    def __init__(self, cdp_cookies: list[dict[str, str]] | None = None) -> None:
        self.opened_url = ""
        self.current_url = ""
        self.cdp_cookies = cdp_cookies or []

    def get(self, url: str) -> None:
        self.opened_url = url
        self.current_url = url

    def get_cookies(self) -> list[dict[str, str]]:
        return [
            {"name": "DedeUserID", "value": "10001"},
            {"name": "SESSDATA", "value": "abc"},
            {"name": "bili_jct", "value": "csrf"},
        ]

    def execute_cdp_cmd(self, command: str, params: dict[str, object]) -> dict[str, object]:
        self.assert_equal_command(command)
        return {"cookies": self.cdp_cookies}

    def assert_equal_command(self, command: str) -> None:
        if command != "Network.getAllCookies":
            raise AssertionError(command)


class CdpOnlyDriver(FakeDriver):
    def get_cookies(self) -> list[dict[str, str]]:
        return []


class CookieCaptureTest(unittest.TestCase):
    def test_wait_for_cookie_builds_cookie_header(self) -> None:
        logs: list[str] = []
        driver = FakeDriver()

        result = _wait_for_cookie(driver, "Edge", 30, logs.append)

        self.assertEqual(driver.opened_url, "https://passport.bilibili.com/login")
        self.assertEqual(result.browser, "Edge")
        self.assertIn("SESSDATA=abc", result.cookie_header)
        self.assertIn("bili_jct=csrf", result.cookie_header)
        self.assertTrue(logs)

    def test_wait_for_cookie_reads_chromium_all_cookies(self) -> None:
        logs: list[str] = []
        driver = CdpOnlyDriver(
            cdp_cookies=[
                {"name": "SESSDATA", "value": "abc", "domain": ".bilibili.com", "path": "/"},
                {"name": "bili_jct", "value": "csrf", "domain": ".bilibili.com", "path": "/"},
                {"name": "other", "value": "ignored", "domain": ".example.com", "path": "/"},
            ]
        )

        result = _wait_for_cookie(driver, "Chrome", 30, logs.append)

        self.assertEqual(result.browser, "Chrome")
        self.assertIn("SESSDATA=abc", result.cookie_header)
        self.assertIn("bili_jct=csrf", result.cookie_header)
        self.assertNotIn("other=ignored", result.cookie_header)

    def test_open_login_page_prefers_local_browser(self) -> None:
        calls: list[list[str]] = []
        original_find = cookie_capture._find_local_browser
        original_popen = cookie_capture.subprocess.Popen
        try:
            cookie_capture._find_local_browser = lambda preferred="": r"C:\Edge\msedge.exe"
            cookie_capture.subprocess.Popen = lambda args, **_kwargs: calls.append(args)

            browser_name = open_bilibili_login_page()
        finally:
            cookie_capture._find_local_browser = original_find
            cookie_capture.subprocess.Popen = original_popen

        self.assertEqual(browser_name, "Edge")
        self.assertEqual(calls, [[r"C:\Edge\msedge.exe", BILIBILI_LOGIN_URL]])

    def test_launch_browser_for_attach_sets_debugger_address(self) -> None:
        calls: list[list[str]] = []

        class FakeOptions:
            debugger_address = ""

        original_find = cookie_capture._find_local_browser
        original_port = cookie_capture._find_free_port
        original_wait = cookie_capture._wait_for_debugger_port
        original_popen = cookie_capture.subprocess.Popen
        original_app_dir = cookie_capture.APP_DIR
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                cookie_capture._find_local_browser = lambda preferred="": r"C:\Edge\msedge.exe"
                cookie_capture._find_free_port = lambda: 45678
                cookie_capture._wait_for_debugger_port = lambda port, timeout_seconds=15.0: True
                cookie_capture.subprocess.Popen = lambda args, **_kwargs: calls.append(args)
                cookie_capture.APP_DIR = Path(temp_dir)
                options = FakeOptions()

                attached = _launch_browser_for_attach("Edge", options, None)
            finally:
                cookie_capture._find_local_browser = original_find
                cookie_capture._find_free_port = original_port
                cookie_capture._wait_for_debugger_port = original_wait
                cookie_capture.subprocess.Popen = original_popen
                cookie_capture.APP_DIR = original_app_dir

        self.assertIsNotNone(attached)
        self.assertEqual(options.debugger_address, "127.0.0.1:45678")
        self.assertIn(BILIBILI_LOGIN_URL, calls[0])
        self.assertTrue(any("cookie-browser-profile" in str(item) for item in calls[0]))


if __name__ == "__main__":
    unittest.main()
