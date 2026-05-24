from __future__ import annotations

import queue
import unittest
from types import SimpleNamespace

from bili_drop_guard import gui


class FakeVar:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value


class GuiNotificationTest(unittest.TestCase):
    def _new_app(self) -> gui.App:
        app = object.__new__(gui.App)
        app.notify_url_var = FakeVar("https://example.com/hook")
        app.account_name_var = FakeVar("主账号")
        app.config_data = SimpleNamespace(notify_url="", account_name="主账号")
        app.notification_history = {}
        app.notification_failure_history = {}
        app.notification_pending = set()
        app.log_queue = queue.Queue()
        return app

    def test_notification_sends_start_message_and_limits_per_account(self) -> None:
        app = self._new_app()
        sent: list[tuple[str, str, str, str]] = []

        class ImmediateThread:
            def __init__(self, target, args, daemon) -> None:  # type: ignore[no-untyped-def]
                self.target = target
                self.args = args
                self.daemon = daemon

            def start(self) -> None:
                self.target(*self.args)

        def fake_send(url: str, title: str, message: str, level: str) -> bool:
            sent.append((url, title, message, level))
            return True

        original_thread = gui.threading.Thread
        original_send = gui.send_notification
        gui.threading.Thread = ImmediateThread  # type: ignore[assignment]
        gui.send_notification = fake_send  # type: ignore[assignment]
        try:
            app._notify_from_message("已启动：房间 23612045")
            app._notify_from_message("已启动：房间 23612045")
            app.account_name_var = FakeVar("小号")
            app._notify_from_message("已启动：房间 23612045")
        finally:
            gui.threading.Thread = original_thread  # type: ignore[assignment]
            gui.send_notification = original_send  # type: ignore[assignment]

        self.assertEqual(len(sent), 2)
        self.assertIn("https://example.com/hook|主账号|已启动：房间 23612045", app.notification_history)
        self.assertIn("https://example.com/hook|小号|已启动：房间 23612045", app.notification_history)
        self.assertEqual(app.notification_pending, set())

    def test_failed_notification_uses_short_failure_backoff(self) -> None:
        app = self._new_app()
        attempts = 0

        class ImmediateThread:
            def __init__(self, target, args, daemon) -> None:  # type: ignore[no-untyped-def]
                self.target = target
                self.args = args
                self.daemon = daemon

            def start(self) -> None:
                self.target(*self.args)

        def fake_send(_url: str, _title: str, _message: str, _level: str) -> bool:
            nonlocal attempts
            attempts += 1
            raise RuntimeError("webhook error")

        original_thread = gui.threading.Thread
        original_send = gui.send_notification
        gui.threading.Thread = ImmediateThread  # type: ignore[assignment]
        gui.send_notification = fake_send  # type: ignore[assignment]
        try:
            app._notify_from_message("已领取：电竞补给")
            app._notify_from_message("已领取：电竞补给")
        finally:
            gui.threading.Thread = original_thread  # type: ignore[assignment]
            gui.send_notification = original_send  # type: ignore[assignment]

        self.assertEqual(attempts, 1)
        self.assertEqual(app.notification_pending, set())
        self.assertEqual(len(app.notification_history), 0)
        self.assertIn("https://example.com/hook|主账号|已领取：电竞补给", app.notification_failure_history)
        self.assertIn("通知发送失败：webhook error", app.log_queue.get_nowait())


class GuiMessageRoutingTest(unittest.TestCase):
    """多账号会给日志加 [账号名] 前缀，分流逻辑必须忽略该前缀。"""

    def _app(self) -> gui.App:
        return object.__new__(gui.App)

    def test_split_account_prefix(self) -> None:
        app = self._app()
        self.assertEqual(app._split_account_prefix("[默认账号] 掉宝任务：x"), ("[默认账号]", "掉宝任务：x"))
        self.assertEqual(app._split_account_prefix("掉宝任务：x"), ("", "掉宝任务：x"))

    def test_progress_message_recognized_with_account_prefix(self) -> None:
        app = self._app()
        self.assertTrue(app._is_progress_message("[默认账号] 掉宝任务：当前可挂"))
        self.assertTrue(app._is_progress_message("[默认账号] 房间 23612045：直播中"))
        self.assertTrue(app._is_progress_message("[小号] 后台计时状态：40/40 正常"))
        self.assertTrue(app._is_progress_message("掉宝任务：x"))
        self.assertFalse(app._is_progress_message("[默认账号] 上报进入直播间累计失败 1 次"))

    def test_notification_message_recognized_with_account_prefix(self) -> None:
        app = self._app()
        self.assertTrue(app._is_notification_message("[默认账号] 已领取：电竞补给"))
        self.assertTrue(app._is_notification_message("已领取：电竞补给"))
        self.assertFalse(app._is_notification_message("[默认账号] 房间 23612045：直播中"))


if __name__ == "__main__":
    unittest.main()
