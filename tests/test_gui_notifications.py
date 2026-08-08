from __future__ import annotations

import json
import queue
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from bili_drop_guard import gui


class FakeVar:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class FakeText:
    def __init__(self, value: str = "") -> None:
        self.value = value
        self.focused = False

    def get(self, _start: str, _end: str) -> str:
        return self.value

    def delete(self, _start: str, _end: str) -> None:
        self.value = ""

    def insert(self, _index: str, value: str) -> None:
        self.value = value

    def focus_set(self) -> None:
        self.focused = True


class FakeLogText:
    def __init__(self) -> None:
        self.value = ""
        self.seen_end = False

    def configure(self, **_kwargs) -> None: ...

    def delete(self, _start: str, _end: str) -> None:
        self.value = ""

    def insert(self, _index: str, value: str) -> None:
        self.value += value

    def get(self, _start: str, _end: str) -> str:
        return self.value

    def see(self, _index: str) -> None:
        self.seen_end = True


class FakeBoolVar:
    def __init__(self, value: bool) -> None:
        self.value = value

    def get(self) -> bool:
        return self.value

    def set(self, value: bool) -> None:
        self.value = value


class CookieBilibiliValidationTest(unittest.TestCase):
    def test_validation_worker_uses_bilibili_login_endpoint_client(self) -> None:
        app = object.__new__(gui.App)
        app.log_queue = queue.Queue()
        login = SimpleNamespace(logged_in=True, uname="测试账号", mid=12345, message="已登录")

        with patch("bili_drop_guard.gui.BilibiliClient") as client_class:
            client_class.return_value.check_login.return_value = login
            app._validate_cookie_worker("SESSDATA=x; bili_jct=y; DedeUserID=1", 7)

        client_class.assert_called_once_with("SESSDATA=x; bili_jct=y; DedeUserID=1")
        client_class.return_value.check_login.assert_called_once_with()
        client_class.return_value.close.assert_called_once_with()
        message = app.log_queue.get_nowait()
        payload = json.loads(message.removeprefix("__COOKIE_VERIFY__:"))
        self.assertTrue(payload["logged_in"])
        self.assertEqual(payload["uname"], "测试账号")
        self.assertEqual(payload["mid"], 12345)

    def test_success_result_is_labeled_as_bilibili_validation(self) -> None:
        app = object.__new__(gui.App)
        cookie = "SESSDATA=x; bili_jct=y; DedeUserID=1"
        app.cookie_text = FakeText(cookie)
        app.cookie_validation_var = FakeVar("B站验证中…")
        app._cookie_validation_generation = 3
        app._cookie_validation_cookie = cookie
        logs: list[str] = []
        app._log = logs.append
        app._show_notice = lambda *_args, **_kwargs: None

        app._apply_cookie_validation_result(
            {"generation": 3, "logged_in": True, "uname": "测试账号", "mid": 12345, "message": "已登录"}
        )

        self.assertEqual(app.cookie_validation_var.get(), "B站已登录")
        self.assertEqual(logs, ["B 站验证通过：测试账号（12345）"])


class BackendNetworkLabelTest(unittest.TestCase):
    def test_normal_routes_show_totalv2_rate_on_main_status(self) -> None:
        rows = [
            gui.WatchWorkerStatus(worker_id=index, state="正常", interval=60, message="")
            for index in range(1, 101)
        ]

        self.assertEqual(gui._backend_network_label(rows, 2.4), "B站 2.4x")

    def test_normal_routes_show_sampling_before_totalv2_has_two_samples(self) -> None:
        rows = [gui.WatchWorkerStatus(worker_id=1, state="正常", interval=60, message="")]

        self.assertEqual(gui._backend_network_label(rows, None), "实绩采样中")

    def test_room_entry_failure_wins_over_accepted_routes(self) -> None:
        rows = [
            gui.WatchWorkerStatus(worker_id=1, state="正常", interval=60, message=""),
            gui.WatchWorkerStatus(worker_id=2, state="暂时失败", interval=None, message="进入直播间注册失败"),
        ]

        self.assertEqual(gui._backend_network_label(rows, 1.0), "异常")

    def test_rebuilding_sessions_is_visible_on_main_status(self) -> None:
        rows = [gui.WatchWorkerStatus(worker_id=1, state="重连中", interval=None, message="B站实绩停滞")]

        self.assertEqual(gui._backend_network_label(rows, 0.0), "会话重建中")

    def test_failure_has_priority_over_rebuilding_session(self) -> None:
        rows = [
            gui.WatchWorkerStatus(worker_id=1, state="重连中", interval=None, message="B站实绩停滞"),
            gui.WatchWorkerStatus(worker_id=2, state="暂时失败", interval=None, message="请求失败"),
        ]

        self.assertEqual(gui._backend_network_label(rows, 0.0), "异常")


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
        self.assertTrue(app._is_notification_message("[默认账号] 批量领取完成：新领取 7 个，已领取过 0 个，失败 0 个"))
        self.assertFalse(app._is_notification_message("[默认账号] 房间 23612045：直播中"))

    def test_parallel_start_message_is_notification(self) -> None:
        # 多账号启动消息以「已启动 」(空格) 开头，必须能触发通知。
        app = self._app()
        self.assertTrue(app._is_notification_message("已启动 5 个账号并行：房间 23612045，每账号 1 路，自动领奖=开启"))


class ProgressVisualRoutingTest(unittest.TestCase):
    class DummyVar:
        def __init__(self, value: str = "") -> None:
            self.value = value

        def set(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    class DummyRing:
        def __init__(self) -> None:
            self.states: list[dict[str, object]] = []

        def set_state(self, **kwargs: object) -> None:
            self.states.append(kwargs)

    def _app(self) -> gui.App:
        app = object.__new__(gui.App)
        app.progress_ring = self.DummyRing()
        app.progress_title_var = self.DummyVar()
        app.progress_detail_var = self.DummyVar()
        app.reward_title_var = self.DummyVar("检查中")
        app.reward_detail_var = self.DummyVar()
        app.reward_status_var = self.DummyVar()
        app._activity_target_minutes = []
        app._progress_terminal = False
        app.progress_snapshot = ""
        app.started_at = None
        app.watcher = None
        return app

    def test_remember_activity_targets_parses_target_minutes(self) -> None:
        app = self._app()
        gui.App._remember_activity_targets(
            app, "任务已识别：第 1 组\nA：目标 30 分钟\nB：目标 120 分钟"
        )
        self.assertEqual(app._activity_target_minutes, [30.0, 120.0])

    def test_detected_task_waits_for_real_bilibili_progress(self) -> None:
        app = self._app()
        app.watcher = SimpleNamespace(running=True)
        app.started_at = datetime.now() - timedelta(minutes=45)
        gui.App._remember_activity_targets(app, "A：目标 30 分钟\nB：目标 60 分钟")

        gui.App._sync_progress_visual(app, "活动任务已识别，正在等待 B 站同步当前分钟数")

        self.assertEqual(app.progress_title_var.get(), "任务已识别")
        self.assertEqual(app.reward_title_var.get(), "未到领取条件")
        self.assertEqual(app.reward_status_var.get(), "领奖：未到条件")
        self.assertFalse(app._progress_terminal)

    def test_real_progress_from_bilibili_updates_progress_card(self) -> None:
        app = self._app()
        app.watcher = SimpleNamespace(running=True)
        app.started_at = datetime.now() - timedelta(minutes=45)
        app._activity_target_minutes = [30.0, 60.0]

        gui.App._sync_progress_visual(app, "A：257/300 分钟，还差 43 分钟")

        self.assertEqual(app.progress_title_var.get(), "257 / 300 分钟")
        self.assertTrue(app._progress_terminal)

    def test_multi_reward_progress_selects_next_unfinished_target(self) -> None:
        app = self._app()

        gui.App._sync_progress_visual(
            app,
            "7月31日｜观看直播60分钟：84 / 60 分钟，已完成\n"
            "7月31日｜观看直播120分钟：84 / 120 分钟，还差 36 分钟\n"
            "7月31日｜观看直播180分钟：84 / 180 分钟，还差 96 分钟",
        )

        self.assertEqual(app.progress_title_var.get(), "84 / 120 分钟")
        self.assertEqual(app.reward_detail_var.get(), "还差 36 分钟")

    def test_compact_multi_reward_progress_uses_confirmed_current_minutes(self) -> None:
        app = self._app()

        gui.App._sync_progress_visual(
            app,
            "当前可挂：7月31日，共 5 个奖励\n"
            "观看直播（当前：84 分钟）\n"
            "  ████████████████████   60 分钟  ✓ 待领取\n"
            "  ██████████████░░░░░░  120 分钟  还差 36 分钟\n"
            "  █████████░░░░░░░░░░░  180 分钟  还差 96 分钟",
        )

        self.assertEqual(app.progress_title_var.get(), "84 / 120 分钟")
        self.assertEqual(app.reward_detail_var.get(), "还差 36 分钟")

    def test_task_progress_failure_is_waiting_not_claim_failure(self) -> None:
        app = self._app()

        gui.App._sync_progress_visual(app, "掉宝任务进度检查失败：接口暂时不可用")

        self.assertEqual(app.progress_title_var.get(), "等待任务进度")
        self.assertEqual(app.reward_title_var.get(), "未到领取条件")
        self.assertEqual(app.reward_status_var.get(), "领奖：未到条件")
        self.assertNotEqual(app.reward_status_var.get(), "领奖：失败")

    def test_claim_failure_sets_claim_failure(self) -> None:
        app = self._app()

        gui.App._sync_progress_visual(app, "领取失败：活动任务：Cookie 已过期")

        self.assertEqual(app.reward_title_var.get(), "领取失败")
        self.assertEqual(app.reward_status_var.get(), "领奖：失败")

    def test_claimable_message_updates_reward_card(self) -> None:
        app = self._app()

        gui.App._sync_progress_visual(app, "检测到 4 个奖励可以领取，正在排队领取")

        self.assertEqual(app.reward_title_var.get(), "4 个")
        self.assertEqual(app.reward_detail_var.get(), "已进入自动领取队列，请等待领取结果")
        self.assertEqual(app.reward_status_var.get(), "领奖：4 个可领")
        self.assertEqual(app.progress_title_var.get(), "4 个奖励可领取")

    def test_claimable_message_with_auto_claim_off_prompts_manual_action(self) -> None:
        app = self._app()

        gui.App._sync_progress_visual(app, "检测到 7 个奖励可以领取；自动领取已关闭，请点击“领取奖励”")

        self.assertEqual(app.reward_title_var.get(), "7 个")
        self.assertEqual(app.reward_detail_var.get(), "自动领取已关闭，请点击上方“领取奖励”")
        self.assertEqual(app.reward_status_var.get(), "领奖：7 个可领")

    def test_claim_completion_summary_updates_reward_card(self) -> None:
        app = self._app()

        gui.App._sync_progress_visual(app, "批量领取完成：新领取 7 个，已领取过 0 个，失败 0 个")

        self.assertEqual(app.reward_title_var.get(), "领取完成")
        self.assertEqual(app.reward_status_var.get(), "领奖：已完成")

    def test_no_claimable_after_refresh_updates_reward_card(self) -> None:
        app = self._app()

        gui.App._sync_progress_visual(app, "已刷新任务进度，但仍未检测到可领取任务；如果 B 站页面显示已完成，请稍后再点领取")

        self.assertEqual(app.reward_title_var.get(), "未到领取条件")
        self.assertEqual(app.reward_status_var.get(), "领奖：未到条件")

    def test_activity_task_detected_updates_progress_card(self) -> None:
        app = self._app()

        gui.App._sync_progress_visual(app, "活动任务已识别，正在等待 B 站同步当前分钟数")

        self.assertEqual(app.progress_title_var.get(), "任务已识别")
        self.assertEqual(app.reward_title_var.get(), "未到领取条件")
        self.assertEqual(app.reward_status_var.get(), "领奖：未到条件")

    def test_activity_progress_empty_is_waiting_not_failure(self) -> None:
        app = self._app()

        gui.App._sync_progress_visual(app, "活动任务进度接口暂未返回可显示的奖励进度，已识别 9 个任务，稍后继续刷新")

        self.assertEqual(app.progress_title_var.get(), "等待 B 站同步当前分钟数")
        self.assertEqual(app.reward_title_var.get(), "未到领取条件")
        self.assertEqual(app.reward_status_var.get(), "领奖：未到条件")

    def test_no_activity_task_clears_stale_progress_snapshot(self) -> None:
        app = self._app()
        app._activity_target_minutes = [30.0, 60.0]
        app.progress_snapshot = "[12:00]\n掉宝任务旧快照"
        app._progress_terminal = False

        gui.App._sync_progress_visual(app, "当前直播页没有本次活动任务，已清空旧任务缓存")

        self.assertEqual(app._activity_target_minutes, [])
        self.assertEqual(app.progress_snapshot, "")
        self.assertEqual(app.progress_title_var.get(), "当前直播页暂无掉宝任务")
        self.assertEqual(app.reward_status_var.get(), "领奖：无任务")
        self.assertTrue(app._progress_terminal)

    def test_temporary_activity_parse_failure_stays_in_retry_state(self) -> None:
        app = self._app()
        app._activity_target_minutes = [60.0]
        app.progress_snapshot = "[12:00]\n掉宝任务旧快照"
        app._progress_terminal = True

        gui.App._sync_progress_visual(
            app,
            "当前直播页暂时没有读到可跟踪的掉宝任务：活动配置解析失败",
        )

        self.assertEqual(app._activity_target_minutes, [60.0])
        self.assertEqual(app.progress_snapshot, "[12:00]\n掉宝任务旧快照")
        self.assertEqual(app.progress_title_var.get(), "等待任务进度")
        self.assertFalse(app._progress_terminal)

    def test_incomplete_progress_updates_reward_remaining_minutes(self) -> None:
        app = self._app()

        gui.App._sync_progress_visual(app, "第 1 组｜战令等级直升：257/300 分钟，还差 43 分钟")

        self.assertEqual(app.progress_title_var.get(), "257 / 300 分钟")
        self.assertEqual(app.reward_title_var.get(), "未到领取条件")
        self.assertEqual(app.reward_detail_var.get(), "还差 43 分钟")
        self.assertEqual(app.reward_status_var.get(), "领奖：未到条件")

    def test_login_message_updates_cookie_status(self) -> None:
        app = self._app()
        app.cookie_validation_var = self.DummyVar("Cookie 已填写")

        gui.App._sync_progress_visual(app, "账号登录正常：圣光____（93693916）")

        self.assertEqual(app.cookie_validation_var.get(), "Cookie 已登录")

    def test_skipped_claim_updates_reward_card(self) -> None:
        app = self._app()

        gui.App._sync_progress_visual(app, "已跳过：第 1 组｜电竞补给 已经领取过")

        self.assertEqual(app.reward_title_var.get(), "已跳过")
        self.assertEqual(app.reward_status_var.get(), "领奖：已完成")

    def test_all_received_snapshot_updates_reward_card(self) -> None:
        app = self._app()

        gui.App._sync_progress_visual(app, "全部奖励已领取：7月5日，共 9 个奖励")

        self.assertEqual(app.progress_title_var.get(), "已领取")
        self.assertEqual(app.reward_title_var.get(), "已领取")
        self.assertEqual(app.reward_status_var.get(), "领奖：已完成")


class LogDrainRoutingTest(unittest.TestCase):
    def _app(self) -> gui.App:
        app = object.__new__(gui.App)
        app.log_queue = queue.Queue()
        app.logged: list[str] = []
        app.progressed: list[str] = []
        app.snapshots: list[str] = []
        app.after_calls: list[tuple[int, object]] = []
        app._log = app.logged.append  # type: ignore[method-assign]
        app._progress_log = app.progressed.append  # type: ignore[method-assign]
        app._progress_snapshot_log = app.snapshots.append  # type: ignore[method-assign]
        app._notify_from_message = lambda _message: None  # type: ignore[method-assign]
        app.after = lambda delay, callback: app.after_calls.append((delay, callback))  # type: ignore[method-assign]
        return app

    def test_progress_messages_are_also_written_to_run_log(self) -> None:
        app = self._app()
        app.log_queue.put("[默认账号] 开始领取奖励：会按顺序一个一个领取")

        gui.App._drain_logs(app)

        self.assertIn("[默认账号] 开始领取奖励：会按顺序一个一个领取", app.progressed)
        self.assertIn("[默认账号] 开始领取奖励：会按顺序一个一个领取", app.logged)

    def test_task_snapshot_is_also_written_to_run_log(self) -> None:
        app = self._app()
        app.log_queue.put("[默认账号] 掉宝任务：\n观看 30 分钟：10/30 分钟")

        gui.App._drain_logs(app)

        self.assertEqual(app.snapshots, ["[默认账号]\n观看 30 分钟：10/30 分钟"])
        self.assertIn("[默认账号] 掉宝任务：\n观看 30 分钟：10/30 分钟", app.logged)


    def test_room_messages_do_not_go_to_task_progress(self) -> None:
        app = self._app()
        app.log_queue.put("[默认账号] 房间 23612045：直播中｜赛事｜主播 守望先锋电竞｜人气 616365")

        gui.App._drain_logs(app)

        self.assertEqual(app.progressed, [])
        self.assertIn("[默认账号] 房间 23612045：直播中｜赛事｜主播 守望先锋电竞｜人气 616365", app.logged)


class LogFormatTest(unittest.TestCase):
    def test_multiline_log_entry_indents_continuation_lines(self) -> None:
        app = object.__new__(gui.App)

        entry = gui.App._format_log_entry(app, "掉宝任务：\n当前可挂：第 1 组\n奖励：205/240 分钟")

        lines = entry.splitlines()
        self.assertRegex(lines[0], r"^\[\d\d:\d\d:\d\d\] 掉宝任务：$")
        self.assertEqual(lines[1], "           当前可挂：第 1 组")
        self.assertEqual(lines[2], "           奖励：205/240 分钟")
        self.assertTrue(entry.endswith("\n\n"))


class LogViewFilterTest(unittest.TestCase):
    def _app(self) -> gui.App:
        app = object.__new__(gui.App)
        app.log_text = FakeLogText()
        app.log_entries = []
        app.log_view_var = FakeVar("task")
        app.auto_scroll_var = FakeBoolVar(True)
        app.log_view_buttons = {}
        return app

    def test_log_view_filters_task_and_room_entries(self) -> None:
        app = self._app()

        gui.App._log(app, "掉宝任务：当前可挂")
        gui.App._log(app, "房间 23612045：直播中｜赛事｜主播 守望先锋电竞｜人气 616365")

        self.assertIn("掉宝任务", app.log_text.value)
        self.assertNotIn("房间 23612045", app.log_text.value)

        app.log_view_var.set("room")
        gui.App._render_log_text(app)

        self.assertIn("房间 23612045", app.log_text.value)
        self.assertNotIn("掉宝任务", app.log_text.value)

    def test_clear_log_removes_only_current_view(self) -> None:
        app = self._app()
        gui.App._log(app, "掉宝任务：当前可挂")
        gui.App._log(app, "房间 23612045：直播中")

        gui.App._clear_log(app)

        self.assertTrue(any(kind == "room" for kind, _entry in app.log_entries))
        self.assertFalse(any(kind == "task" and "掉宝任务" in entry for kind, entry in app.log_entries))

    def test_clear_room_log_writes_confirmation_to_current_view(self) -> None:
        app = self._app()
        app.log_view_var.set("room")
        gui.App._log(app, "房间 23612045：直播中")

        gui.App._clear_log(app)

        self.assertIn("日志已清空", app.log_text.value)
        self.assertTrue(any(kind == "room" and "日志已清空" in entry for kind, entry in app.log_entries))

        gui.App._log(app, "房间 23612045：直播中｜赛事｜人气 1")

        self.assertIn("房间 23612045", app.log_text.value)

class GuiAccountSelectionTest(unittest.TestCase):
    def test_toggling_account_does_not_change_current_editor_or_cookie(self) -> None:
        app = object.__new__(gui.App)
        app.account_checks = {"小号": FakeBoolVar(True)}
        app.account_name_var = FakeVar("主号")
        app.cookie_text = FakeText("unsaved-cookie")
        config = gui.AppConfig(
            cookie="SESSDATA=b",
            account_name="小号",
            accounts=[gui.AccountProfile(name="小号", cookie="SESSDATA=b")],
            active_accounts=["小号"],
        )
        app.config_data = config
        app._current_config = lambda: config  # type: ignore[method-assign]
        app.logs: list[str] = []
        app._log = app.logs.append  # type: ignore[method-assign]

        with patch.object(gui, "save_config") as saved:
            gui.App._on_account_check_toggled(app, "小号")

        self.assertEqual(app.account_name_var.get(), "主号")
        self.assertEqual(app.cookie_text.get("1.0", "end"), "unsaved-cookie")
        saved.assert_called_once_with(config)
        self.assertTrue(any("小号：参与挂机" in item for item in app.logs))

    def test_new_account_clears_editor_and_uses_unique_name(self) -> None:
        app = object.__new__(gui.App)
        app.config_data = SimpleNamespace(
            accounts=[gui.AccountProfile(name="默认账号", cookie="SESSDATA=a")],
            account_name="默认账号",
        )
        app.account_name_var = FakeVar("默认账号")
        app.selected_account_var = FakeVar("默认账号")
        app.cookie_text = FakeText("SESSDATA=a")
        app.cookie_validation_var = FakeVar("")
        app.editing_account_name = "默认账号"
        app.logs: list[str] = []
        app._log = app.logs.append  # type: ignore[method-assign]
        app._refresh_cookie_placeholder = lambda: None  # type: ignore[method-assign]
        app._refresh_summary_bar = lambda: None  # type: ignore[method-assign]
        app._refresh_account_selector = lambda: None  # type: ignore[method-assign]
        app._refresh_account_editor_actions = lambda: None  # type: ignore[method-assign]

        gui.App._new_account(app)

        self.assertEqual(app.account_name_var.get(), "账号 2")
        self.assertEqual(app.cookie_text.get("1.0", "end"), "")
        self.assertEqual(app.cookie_validation_var.get(), "Cookie 未填写")

    def test_edit_account_loads_saved_cookie(self) -> None:
        app = object.__new__(gui.App)
        app.config_data = SimpleNamespace(
            accounts=[
                gui.AccountProfile(name="主号", cookie="SESSDATA=a"),
                gui.AccountProfile(name="小号", cookie="SESSDATA=b"),
            ],
            account_name="主号",
        )
        app.account_name_var = FakeVar("主号")
        app.selected_account_var = FakeVar("主号")
        app.cookie_text = FakeText("SESSDATA=a")
        app.cookie_validation_var = FakeVar("")
        app.editing_account_name = "主号"
        app.logs: list[str] = []
        app._log = app.logs.append  # type: ignore[method-assign]
        app._refresh_cookie_placeholder = lambda: None  # type: ignore[method-assign]
        app._refresh_summary_bar = lambda: None  # type: ignore[method-assign]
        app._refresh_account_selector = lambda: None  # type: ignore[method-assign]
        app._refresh_account_editor_actions = lambda: None  # type: ignore[method-assign]

        gui.App._select_account_for_edit(app, "小号")

        self.assertEqual(app.account_name_var.get(), "小号")
        self.assertEqual(app.cookie_text.get("1.0", "end"), "SESSDATA=b")
        self.assertEqual(app.cookie_validation_var.get(), "Cookie 已填写")
        self.assertTrue(any("已切换账号：小号" in item for item in app.logs))

    def test_switching_dirty_account_asks_before_discard_and_never_saves(self) -> None:
        app = object.__new__(gui.App)
        app.config_data = SimpleNamespace(
            accounts=[
                gui.AccountProfile(name="主号", cookie="SESSDATA=a"),
                gui.AccountProfile(name="小号", cookie="SESSDATA=b"),
            ],
            account_name="主号",
        )
        app.account_name_var = FakeVar("主号")
        app.selected_account_var = FakeVar("主号")
        app.cookie_text = FakeText("SESSDATA=changed")
        app.cookie_validation_var = FakeVar("")
        app.editing_account_name = "主号"
        app.logs: list[str] = []
        saved: list[str] = []
        app._log = app.logs.append  # type: ignore[method-assign]
        app._save = lambda: saved.append(app.account_name_var.get()) or SimpleNamespace(account_name=app.account_name_var.get())  # type: ignore[method-assign]
        app._refresh_cookie_placeholder = lambda: None  # type: ignore[method-assign]
        app._refresh_summary_bar = lambda: None  # type: ignore[method-assign]
        app._refresh_account_selector = lambda: None  # type: ignore[method-assign]
        app._refresh_account_editor_actions = lambda: None  # type: ignore[method-assign]

        confirmation: dict[str, object] = {}
        with patch.object(
            gui,
            "build_confirmation_dialog",
            side_effect=lambda _parent, **kwargs: confirmation.update(kwargs),
        ):
            gui.App._select_account_for_edit(app, "小号")

        self.assertEqual(saved, [])
        self.assertEqual(app.account_name_var.get(), "主号")
        self.assertEqual(app.cookie_text.get("1.0", "end"), "SESSDATA=changed")
        self.assertEqual(confirmation["confirm_text"], "放弃并切换")
        confirmation["on_confirm"]()  # type: ignore[operator]
        self.assertEqual(app.account_name_var.get(), "小号")
        self.assertEqual(app.cookie_text.get("1.0", "end"), "SESSDATA=b")

    def test_select_current_account_is_a_noop(self) -> None:
        app = object.__new__(gui.App)
        app.config_data = SimpleNamespace(
            accounts=[gui.AccountProfile(name="主号", cookie="SESSDATA=a")],
        )
        app.account_name_var = FakeVar("主号")
        app.cookie_text = FakeText("SESSDATA=a")
        app.editing_account_name = "主号"
        app.logs: list[str] = []
        app._log = app.logs.append  # type: ignore[method-assign]

        gui.App._select_account_for_edit(app, "主号")

        self.assertFalse(app.cookie_text.focused)
        self.assertEqual(app.logs, [])

    def test_reset_room_id_restores_default(self) -> None:
        app = object.__new__(gui.App)
        app.room_var = FakeVar("123")
        app.logs: list[str] = []
        app._log = app.logs.append  # type: ignore[method-assign]
        app._refresh_summary_bar = lambda: None  # type: ignore[method-assign]

        gui.App._reset_room_id(app)

        self.assertEqual(app.room_var.get(), gui.DEFAULT_ROOM_ID)
        self.assertTrue(any(gui.DEFAULT_ROOM_ID in item for item in app.logs))

    def test_open_live_room_uses_current_or_default_room(self) -> None:
        app = object.__new__(gui.App)
        app.room_var = FakeVar("")
        app.logs: list[str] = []
        opened: list[str] = []
        app._log = app.logs.append  # type: ignore[method-assign]
        app._refresh_summary_bar = lambda: None  # type: ignore[method-assign]
        original_open = gui.webbrowser.open
        gui.webbrowser.open = opened.append  # type: ignore[assignment]
        try:
            gui.App._open_live_room(app)
        finally:
            gui.webbrowser.open = original_open  # type: ignore[assignment]

        self.assertEqual(opened, [f"https://live.bilibili.com/{gui.DEFAULT_ROOM_ID}"])
        self.assertEqual(app.room_var.get(), gui.DEFAULT_ROOM_ID)

    def test_accounts_with_current_cookie_adds_new_account_without_overwriting_existing(self) -> None:
        app = object.__new__(gui.App)
        app.config_data = SimpleNamespace(
            accounts=[gui.AccountProfile(name="默认账号", cookie="SESSDATA=a")],
        )
        app.account_name_var = FakeVar("账号 2")
        app.cookie_text = FakeText("SESSDATA=b")

        accounts = gui.App._accounts_with_current_cookie(app)

        self.assertEqual([(item.name, item.cookie) for item in accounts],
                         [("账号 2", "SESSDATA=b"), ("默认账号", "SESSDATA=a")])

    def test_current_config_excludes_unsaved_new_account_draft(self) -> None:
        app = object.__new__(gui.App)
        app.config_data = SimpleNamespace(
            accounts=[gui.AccountProfile(name="默认账号", cookie="SESSDATA=a")],
            account_name="默认账号",
        )
        app.account_checks = {"默认账号": FakeBoolVar(True)}
        app.account_name_var = FakeVar("账号 2")
        app.cookie_text = FakeText("SESSDATA=b")
        app.room_var = FakeVar("23612045")
        app.interval_var = FakeVar("10")
        app.auto_claim_var = FakeBoolVar(True)
        app.task_ids_text = FakeText("")
        app.watch_threads_var = FakeVar("1")
        app.notify_url_var = FakeVar("")
        app.editing_account_name = None

        config = gui.App._current_config(app)

        self.assertEqual([(item.name, item.cookie) for item in config.accounts],
                         [("默认账号", "SESSDATA=a")])
        self.assertEqual(config.active_accounts, ["默认账号"])

    def test_close_persists_runtime_settings_without_saving_account_draft(self) -> None:
        app = object.__new__(gui.App)
        app.preview_mode = False
        app.config_data = gui.AppConfig(
            cookie="SESSDATA=saved",
            account_name="主号",
            accounts=[gui.AccountProfile(name="主号", cookie="SESSDATA=saved")],
            room_id="23612045",
            watch_threads=10,
            active_accounts=["主号"],
        )
        app.editing_account_name = "主号"
        app.account_checks = {"主号": FakeBoolVar(True)}
        app.room_var = FakeVar("https://live.bilibili.com/123456")
        app.interval_var = FakeVar("35")
        app.auto_claim_var = FakeBoolVar(True)
        app.task_ids_text = FakeText("")
        app.watch_threads_var = FakeVar("100")
        app.notify_url_var = FakeVar("")

        with patch.object(gui, "save_config") as saved:
            gui.App._save_runtime_settings_on_close(app)

        persisted = saved.call_args.args[0]
        self.assertEqual(persisted.room_id, "123456")
        self.assertEqual(persisted.watch_threads, 100)
        self.assertTrue(persisted.auto_claim)
        self.assertEqual(persisted.accounts[0].cookie, "SESSDATA=saved")

    def test_close_keeps_last_valid_room_when_input_is_invalid(self) -> None:
        app = object.__new__(gui.App)
        app.preview_mode = False
        app.config_data = gui.AppConfig(
            account_name="主号",
            accounts=[gui.AccountProfile(name="主号", cookie="SESSDATA=saved")],
            room_id="23612045",
            active_accounts=["主号"],
        )
        app.editing_account_name = "主号"
        app.account_checks = {"主号": FakeBoolVar(True)}
        app.room_var = FakeVar("abc")
        app.interval_var = FakeVar("10")
        app.auto_claim_var = FakeBoolVar(False)
        app.task_ids_text = FakeText("")
        app.watch_threads_var = FakeVar("10")
        app.notify_url_var = FakeVar("")

        with patch.object(gui, "save_config") as saved:
            gui.App._save_runtime_settings_on_close(app)

        self.assertEqual(app.room_var.get(), "23612045")
        self.assertEqual(saved.call_args.args[0].room_id, "23612045")

    def test_claim_rejects_invalid_room_before_config_sanitization(self) -> None:
        app = object.__new__(gui.App)
        app.watcher = None
        app.room_var = FakeVar("abc")
        app._account_editor_is_dirty = lambda: False  # type: ignore[method-assign]
        notices: list[tuple[str, str]] = []
        app._show_notice = lambda title, body, **_kwargs: notices.append((title, body))  # type: ignore[method-assign]
        app._current_config = lambda: self.fail("无效房号不应进入配置归一化")  # type: ignore[method-assign]

        gui.App._claim(app)

        self.assertEqual(notices[0][0], "直播间号无效")

    def test_new_account_name_change_marks_draft_dirty(self) -> None:
        app = object.__new__(gui.App)
        app.editing_account_name = None
        app._draft_account_name = "账号 2"
        app.account_name_var = FakeVar("比赛账号")
        app.cookie_text = FakeText("")

        self.assertTrue(gui.App._account_editor_is_dirty(app))

    def test_explicit_rename_preserves_participation_state(self) -> None:
        app = object.__new__(gui.App)
        app.config_data = gui.AppConfig(
            cookie="SESSDATA=a",
            account_name="主号",
            accounts=[
                gui.AccountProfile(name="主号", cookie="SESSDATA=a"),
                gui.AccountProfile(name="小号", cookie="SESSDATA=b"),
            ],
            room_id="23612045",
            active_accounts=["主号"],
        )
        app.editing_account_name = "主号"
        app._draft_account_name = ""
        app.selected_account_var = FakeVar("主号")
        app.account_name_var = FakeVar("比赛账号")
        app.cookie_text = FakeText("SESSDATA=renamed")
        app.cookie_validation_var = FakeVar("")
        app.account_checks = {"主号": FakeBoolVar(True), "小号": FakeBoolVar(False)}
        app.room_var = FakeVar("23612045")
        app.interval_var = FakeVar("10")
        app.auto_claim_var = FakeBoolVar(True)
        app.task_ids_text = FakeText("")
        app.watch_threads_var = FakeVar("1")
        app.notify_url_var = FakeVar("")
        app.logs: list[str] = []
        app._log = app.logs.append  # type: ignore[method-assign]
        app._refresh_account_selector = lambda: None  # type: ignore[method-assign]
        app._refresh_account_editor_actions = lambda: None  # type: ignore[method-assign]

        with patch.object(gui, "save_config") as saved:
            gui.App._save_account(app)

        saved.assert_called_once()
        self.assertEqual(
            [(item.name, item.cookie) for item in app.config_data.accounts],
            [("比赛账号", "SESSDATA=renamed"), ("小号", "SESSDATA=b")],
        )
        self.assertEqual(app.config_data.active_accounts, ["比赛账号"])
        self.assertEqual(app.editing_account_name, "比赛账号")

    def test_deleting_other_account_keeps_draft_and_persisted_current_account(self) -> None:
        app = object.__new__(gui.App)
        app.config_data = gui.AppConfig(
            cookie="SESSDATA=b",
            account_name="B",
            accounts=[
                gui.AccountProfile(name="A", cookie="SESSDATA=a"),
                gui.AccountProfile(name="B", cookie="SESSDATA=b"),
                gui.AccountProfile(name="C", cookie="SESSDATA=c"),
            ],
            active_accounts=["B"],
        )
        app.editing_account_name = None
        app._draft_account_name = "账号 4"
        app.selected_account_var = FakeVar("")
        app.account_name_var = FakeVar("比赛新号")
        app.cookie_text = FakeText("SESSDATA=draft")
        app.cookie_validation_var = FakeVar("Cookie 已填写")
        app.account_checks = {
            "A": FakeBoolVar(False),
            "B": FakeBoolVar(True),
            "C": FakeBoolVar(False),
        }
        app.room_var = FakeVar("23612045")
        app.interval_var = FakeVar("10")
        app.auto_claim_var = FakeBoolVar(True)
        app.task_ids_text = FakeText("")
        app.watch_threads_var = FakeVar("1")
        app.notify_url_var = FakeVar("")
        app.logs: list[str] = []
        app._log = app.logs.append  # type: ignore[method-assign]
        app._refresh_account_selector = lambda: None  # type: ignore[method-assign]
        app._refresh_account_editor_actions = lambda: None  # type: ignore[method-assign]
        app._refresh_summary_bar = lambda: None  # type: ignore[method-assign]

        with patch.object(gui, "save_config"):
            gui.App._perform_delete_account(app, "C")

        self.assertEqual(app.config_data.account_name, "B")
        self.assertEqual(app.editing_account_name, None)
        self.assertEqual(app.account_name_var.get(), "比赛新号")
        self.assertEqual(app.cookie_text.get("1.0", "end"), "SESSDATA=draft")

    def test_start_uses_saved_config_when_building_account_options(self) -> None:
        app = object.__new__(gui.App)
        before_save = gui.AppConfig(
            cookie="SESSDATA=a",
            account_name="主号",
            accounts=[
                gui.AccountProfile(name="主号", cookie="SESSDATA=a"),
                gui.AccountProfile(name="小号", cookie="SESSDATA=b"),
            ],
            room_id="23612045",
            watch_threads=1,
            active_accounts=["主号"],
        )
        after_save = gui.AppConfig(
            cookie="SESSDATA=b",
            account_name="小号",
            accounts=[
                gui.AccountProfile(name="主号", cookie="SESSDATA=a"),
                gui.AccountProfile(name="小号", cookie="SESSDATA=b"),
            ],
            room_id="23612045",
            watch_threads=1,
            active_accounts=["小号"],
        )
        built_from: list[gui.AppConfig] = []

        class DummyWatcher:
            running = False

            def __init__(self, account_options, log) -> None:  # type: ignore[no-untyped-def]
                self.account_options = account_options
                self.log = log

            def start(self) -> None:
                self.running = True

            def get_watch_status_snapshot(self):
                return [], "后台计时状态：启动中"

        original_build = gui.build_account_options
        original_watcher = gui.MultiAccountWatcher
        gui.build_account_options = lambda cfg: built_from.append(cfg) or [("小号", object())]  # type: ignore[assignment]
        gui.MultiAccountWatcher = DummyWatcher  # type: ignore[assignment]
        try:
            app.watch_threads_var = FakeVar("1")
            app.account_checks = {"小号": FakeBoolVar(True)}
            app.watcher = None
            app._current_config = lambda: before_save  # type: ignore[method-assign]
            app._save = lambda: after_save  # type: ignore[method-assign]
            app._log = lambda _message: None  # type: ignore[method-assign]
            app._progress_log = lambda _message: None  # type: ignore[method-assign]
            app._notify_from_message = lambda _message: None  # type: ignore[method-assign]
            app._thread_log = lambda _message: None  # type: ignore[method-assign]
            app._refresh_backend_summary = lambda _snapshot=None: None  # type: ignore[method-assign]
            app._set_status = lambda _message: None  # type: ignore[method-assign]
            app.watch_status_card = SimpleNamespace(update_snapshot=lambda _rows, _summary: None)
            app.elapsed_status_var = FakeVar("")
            app.reward_status_var = FakeVar("")
            app.reward_title_var = FakeVar("")
            app.reward_detail_var = FakeVar("")

            gui.App._start(app)
        finally:
            gui.build_account_options = original_build
            gui.MultiAccountWatcher = original_watcher  # type: ignore[assignment]

        self.assertEqual(built_from, [after_save])


if __name__ == "__main__":
    unittest.main()
