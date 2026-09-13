from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bili_drop_guard import cookie_capture
from bili_drop_guard.cookie_capture import (
    BILIBILI_LOGIN_URL,
    _build_cookie_header,
    _launch_browser_for_attach,
    _read_bilibili_cookies,
    _wait_for_cookie,
    open_bilibili_login_page,
)


class FakeDriver:
    def __init__(self, cdp_cookies: list[dict[str, str]] | None = None) -> None:
        self.opened_url = ""
        self.current_url = ""
        self.cdp_cookies = cdp_cookies or []

    def set_page_load_timeout(self, timeout: int) -> None:
        self.page_load_timeout = timeout

    def get(self, url: str) -> None:
        self.opened_url = url
        self.current_url = url

    def get_cookies(self) -> list[dict[str, str]]:
        return [
            {"name": "DedeUserID", "value": "10001"},
            {"name": "SESSDATA", "value": "abc"},
            {"name": "bili_jct", "value": "csrf"},
        ]

    def call(self, command: str) -> dict[str, object]:
        self.assert_equal_command(command)
        return {"cookies": self.cdp_cookies or self.get_cookies()}

    def assert_equal_command(self, command: str) -> None:
        if command != "Storage.getCookies":
            raise AssertionError(command)


class CdpOnlyDriver(FakeDriver):
    def get_cookies(self) -> list[dict[str, str]]:
        return []


class CookieCaptureTest(unittest.TestCase):
    @unittest.skipUnless(cookie_capture.os.name == "nt", "Windows startup flags")
    def test_browser_is_shown_on_inherited_desktop(self) -> None:
        startup = cookie_capture._browser_startupinfo()
        self.assertTrue(startup.dwFlags & cookie_capture.subprocess.STARTF_USESHOWWINDOW)
        self.assertEqual(startup.wShowWindow, 1)
        self.assertIsNone(getattr(startup, "lpDesktop", None))

    def test_failed_edge_connection_is_closed_before_chrome_fallback(self) -> None:
        edge, chrome, connection = MagicMock(), MagicMock(), MagicMock()
        result = cookie_capture.CapturedCookie("Chrome", "SESSDATA=fixture")
        closed = []
        def connect(port, cancel):
            if port == edge.debug_port:
                raise OSError("cannot connect")
            self.assertEqual(closed, [edge])
            return connection
        with (patch.object(cookie_capture, "_launch_browser_for_attach", side_effect=[edge, chrome]),
              patch.object(cookie_capture, "_CookieBrowser", side_effect=connect),
              patch.object(cookie_capture, "_wait_for_cookie", return_value=result),
              patch.object(cookie_capture, "_close_attached_browser", side_effect=closed.append)):
            self.assertIs(cookie_capture.capture_bilibili_cookie(), result)
        self.assertEqual(closed, [edge, chrome])

    def test_capture_does_not_resolve_or_download_webdriver(self) -> None:
        attached, connection = MagicMock(), MagicMock()
        result = cookie_capture.CapturedCookie("Edge", "SESSDATA=fixture")
        with (patch.object(cookie_capture, "_launch_browser_for_attach", return_value=attached),
              patch.object(cookie_capture, "_CookieBrowser", return_value=connection),
              patch.object(cookie_capture, "_load_webdriver_class", side_effect=AssertionError("driver lookup")),
              patch.object(cookie_capture, "_wait_for_cookie", return_value=result),
              patch.object(cookie_capture, "_close_attached_browser") as close):
            self.assertIs(cookie_capture.capture_bilibili_cookie(), result)
        connection.close.assert_called_once()
        close.assert_called_once_with(attached)

    def test_closing_login_does_not_open_another_browser(self) -> None:
        attached, connection = MagicMock(), MagicMock()
        with (patch.object(cookie_capture, "_launch_browser_for_attach", return_value=attached) as launch,
              patch.object(cookie_capture, "_CookieBrowser", return_value=connection),
              patch.object(cookie_capture, "_wait_for_cookie", side_effect=RuntimeError("browser closed")),
              patch.object(cookie_capture, "_close_attached_browser") as close):
            with self.assertRaisesRegex(RuntimeError, "browser closed"):
                cookie_capture.capture_bilibili_cookie()
        launch.assert_called_once()
        connection.close.assert_called_once()
        close.assert_called_once_with(attached)

    def test_profiles_are_unique_and_cleanup_only_removes_owned_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.object(cookie_capture, "APP_DIR", Path(temp)):
            first = cookie_capture._capture_profile_dir("Edge")
            second = cookie_capture._capture_profile_dir("Edge")
            legacy = first.parent / "Default"
            legacy.mkdir()
            self.assertNotEqual(first, second)
            cookie_capture._remove_capture_profile(legacy)
            cookie_capture._remove_capture_profile(first)
            self.assertFalse(first.exists())
            self.assertTrue(second.exists())
            self.assertTrue(legacy.exists())

    def test_launch_failure_removes_new_profile(self) -> None:
        with (tempfile.TemporaryDirectory() as temp,
              patch.object(cookie_capture, "APP_DIR", Path(temp)),
              patch.object(cookie_capture, "_find_local_browser", return_value="msedge.exe"),
              patch.object(cookie_capture.subprocess, "Popen", side_effect=OSError("launch failed"))):
            with self.assertRaisesRegex(OSError, "launch failed"):
                _launch_browser_for_attach("Edge")
            self.assertEqual(list(Path(temp).rglob("capture-*")), [])

    def test_cdp_matches_response_id_and_ignores_events(self) -> None:
        connection = object.__new__(cookie_capture._CookieBrowser)
        connection._cancel_event = threading.Event()
        connection._next_id = 0
        connection._socket = MagicMock()
        connection._socket.recv.side_effect = [
            cookie_capture.websocket.WebSocketTimeoutException(),
            json.dumps({"method": "Target.targetCreated"}),
            json.dumps({"id": 9, "result": {}}),
            json.dumps({"id": 1, "result": {"cookies": []}}),
        ]
        self.assertEqual(connection.call("Storage.getCookies"), {"cookies": []})
        sent = json.loads(connection._socket.send.call_args.args[0])
        self.assertEqual(sent["method"], "Storage.getCookies")

    def test_cdp_rejects_non_local_debugger_endpoint(self) -> None:
        opener = MagicMock()
        opener.open.return_value = io.BytesIO(json.dumps({
            "webSocketDebuggerUrl": "ws://example.com:1234/devtools/browser/test"
        }).encode())
        with (patch.object(cookie_capture.urllib.request, "build_opener", return_value=opener),
              patch.object(cookie_capture.websocket, "create_connection") as connect):
            with self.assertRaisesRegex(RuntimeError, "调试地址无效"):
                cookie_capture._CookieBrowser(1234)
        connect.assert_not_called()

    def test_cookie_transport_failure_is_not_reported_as_logged_out(self) -> None:
        connection = MagicMock()
        connection.call.side_effect = OSError("connection lost")
        with self.assertRaisesRegex(OSError, "connection lost"):
            _read_bilibili_cookies(connection)

    def test_cookie_reader_keeps_session_cookie_but_rejects_expired_cookie(self) -> None:
        connection = MagicMock()
        connection.call.return_value = {"cookies": [
            {"name": "SESSDATA", "value": "session", "domain": ".bilibili.com", "expires": -1},
            {"name": "expired", "value": "old", "domain": ".bilibili.com", "expires": 1},
        ]}
        self.assertEqual([item["name"] for item in _read_bilibili_cookies(connection)], ["SESSDATA"])

    def test_wait_for_cookie_builds_cookie_header(self) -> None:
        logs: list[str] = []
        driver = FakeDriver()

        result = _wait_for_cookie(driver, "Edge", 30, logs.append)

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

    def test_cookie_reader_rejects_lookalike_domain(self) -> None:
        driver = CdpOnlyDriver(
            cdp_cookies=[
                {"name": "SESSDATA", "value": "evil", "domain": ".evilbilibili.com", "path": "/"},
                {"name": "SESSDATA", "value": "valid", "domain": ".bilibili.com", "path": "/"},
            ]
        )

        cookies = _read_bilibili_cookies(driver)

        self.assertEqual([(item["name"], item["value"]) for item in cookies], [("SESSDATA", "valid")])

    def test_cookie_header_selects_one_stable_value_per_name(self) -> None:
        header = _build_cookie_header([
            {"name": "SESSDATA", "value": "host-old", "domain": ".passport.bilibili.com", "path": "/login"},
            {"name": "SESSDATA", "value": "shared-valid", "domain": ".bilibili.com", "path": "/"},
            {"name": "bili_jct", "value": "csrf", "domain": ".bilibili.com", "path": "/"},
        ])

        self.assertEqual(header.count("SESSDATA="), 1)
        self.assertIn("SESSDATA=shared-valid", header)

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
        self.assertEqual(calls, [[r"C:\Edge\msedge.exe", "--new-window", BILIBILI_LOGIN_URL]])

    def test_launch_browser_for_attach_returns_debugger_port(self) -> None:
        calls: list[list[str]] = []

        original_find = cookie_capture._find_local_browser
        original_port = cookie_capture._find_free_port
        original_wait = cookie_capture._wait_for_debugger_port
        original_popen = cookie_capture.subprocess.Popen
        original_app_dir = cookie_capture.APP_DIR
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                cookie_capture._find_local_browser = lambda preferred="": r"C:\Edge\msedge.exe"
                cookie_capture._find_free_port = lambda: 45678
                cookie_capture._wait_for_debugger_port = lambda port, timeout_seconds=15.0, cancel_event=None: True
                cookie_capture.subprocess.Popen = lambda args, **_kwargs: calls.append(args)
                cookie_capture.APP_DIR = Path(temp_dir)
                attached = _launch_browser_for_attach("Edge", None)
            finally:
                cookie_capture._find_local_browser = original_find
                cookie_capture._find_free_port = original_port
                cookie_capture._wait_for_debugger_port = original_wait
                cookie_capture.subprocess.Popen = original_popen
                cookie_capture.APP_DIR = original_app_dir

        self.assertIsNotNone(attached)
        self.assertEqual(attached.debug_port, 45678)
        self.assertIn(BILIBILI_LOGIN_URL, calls[0])
        self.assertTrue(any("cookie-browser-profile" in str(item) for item in calls[0]))


if __name__ == "__main__":
    unittest.main()
