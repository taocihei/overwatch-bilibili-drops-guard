from __future__ import annotations

import queue
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

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


class FakeBoolVar:
    def __init__(self, value: bool) -> None:
        self.value = value

    def get(self) -> bool:
        return self.value

    def set(self, value: bool) -> None:
        self.value = value


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

    def test_compute_local_progress_tier_picks_current_tier(self) -> None:
        app = self._app()
        self.assertEqual(
            gui.App._compute_local_progress_tier(app, 40.0, [30, 60, 120]),
            (40.0, 60.0, 20.0, False),
        )

    def test_compute_local_progress_tier_marks_all_done(self) -> None:
        app = self._app()
        self.assertEqual(
            gui.App._compute_local_progress_tier(app, 200.0, [30, 60, 120]),
            (120.0, 120.0, 0.0, True),
        )

    def test_compute_local_progress_tier_without_targets(self) -> None:
        app = self._app()
        self.assertIsNone(gui.App._compute_local_progress_tier(app, 10.0, []))

    def test_remember_activity_targets_parses_target_minutes(self) -> None:
        app = self._app()
        gui.App._remember_activity_targets(
            app, "任务已识别：第 1 组\nA：目标 30 分钟\nB：目标 120 分钟"
        )
        self.assertEqual(app._activity_target_minutes, [30.0, 120.0])

    def test_detected_task_renders_local_progress_from_elapsed(self) -> None:
        app = self._app()
        app.watcher = SimpleNamespace(running=True)
        app.started_at = datetime.now() - timedelta(minutes=45)
        gui.App._remember_activity_targets(app, "A：目标 30 分钟\nB：目标 60 分钟")

        gui.App._sync_progress_visual(app, "活动任务已识别，正在等待 B 站同步当前分钟数")

        self.assertEqual(app.progress_title_var.get(), "45 / 60 分钟")
        self.assertEqual(app.reward_detail_var.get(), "还差 15 分钟")
        self.assertEqual(app.reward_status_var.get(), "领奖：未到条件")
        self.assertFalse(app._progress_terminal)

    def test_real_progress_from_bilibili_overrides_local_estimate(self) -> None:
        app = self._app()
        app.watcher = SimpleNamespace(running=True)
        app.started_at = datetime.now() - timedelta(minutes=45)
        app._activity_target_minutes = [30.0, 60.0]

        gui.App._sync_progress_visual(app, "A：257/300 分钟，还差 43 分钟")

        self.assertEqual(app.progress_title_var.get(), "257 / 300 分钟")
        self.assertTrue(app._progress_terminal)

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

        self.assertEqual(app.reward_title_var.get(), "4 次")
        self.assertEqual(app.reward_status_var.get(), "领奖：4 次可领")
        self.assertEqual(app.progress_title_var.get(), "4 个奖励可领取")

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

    def test_no_activity_task_clears_stale_local_progress(self) -> None:
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


class LogFormatTest(unittest.TestCase):
    def test_multiline_log_entry_indents_continuation_lines(self) -> None:
        app = object.__new__(gui.App)

        entry = gui.App._format_log_entry(app, "掉宝任务：\n当前可挂：第 1 组\n奖励：205/240 分钟")

        lines = entry.splitlines()
        self.assertRegex(lines[0], r"^\[\d\d:\d\d:\d\d\] 掉宝任务：$")
        self.assertEqual(lines[1], "           当前可挂：第 1 组")
        self.assertEqual(lines[2], "           奖励：205/240 分钟")
        self.assertTrue(entry.endswith("\n\n"))


class GuiAccountSelectionTest(unittest.TestCase):
    def test_toggling_account_does_not_change_current_editor_or_cookie(self) -> None:
        app = object.__new__(gui.App)
        app.account_checks = {"小号": FakeBoolVar(True)}
        app.account_name_var = FakeVar("主号")
        app.cookie_text = FakeText("unsaved-cookie")
        app.logs: list[str] = []
        app._log = app.logs.append  # type: ignore[method-assign]

        gui.App._on_account_check_toggled(app, "小号")

        self.assertEqual(app.account_name_var.get(), "主号")
        self.assertEqual(app.cookie_text.get("1.0", "end"), "unsaved-cookie")
        self.assertTrue(any("已勾选账号参与挂机：小号" in item for item in app.logs))

    def test_new_account_clears_editor_and_uses_unique_name(self) -> None:
        app = object.__new__(gui.App)
        app.config_data = SimpleNamespace(
            accounts=[gui.AccountProfile(name="默认账号", cookie="SESSDATA=a")],
            account_name="默认账号",
        )
        app.account_name_var = FakeVar("默认账号")
        app.cookie_text = FakeText("SESSDATA=a")
        app.cookie_validation_var = FakeVar("")
        app.logs: list[str] = []
        app._log = app.logs.append  # type: ignore[method-assign]
        app._refresh_cookie_placeholder = lambda: None  # type: ignore[method-assign]
        app._refresh_summary_bar = lambda: None  # type: ignore[method-assign]

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
        app.cookie_text = FakeText("SESSDATA=a")
        app.cookie_validation_var = FakeVar("")
        app.logs: list[str] = []
        app._log = app.logs.append  # type: ignore[method-assign]
        app._refresh_cookie_placeholder = lambda: None  # type: ignore[method-assign]
        app._refresh_summary_bar = lambda: None  # type: ignore[method-assign]
        app._refresh_account_selector = lambda: None  # type: ignore[method-assign]

        gui.App._select_account_for_edit(app, "小号")

        self.assertEqual(app.account_name_var.get(), "小号")
        self.assertEqual(app.cookie_text.get("1.0", "end"), "SESSDATA=b")
        self.assertEqual(app.cookie_validation_var.get(), "Cookie 已填写")
        self.assertTrue(any("当前编辑账号：小号" in item for item in app.logs))

    def test_edit_account_saves_unsaved_cookie_before_switching(self) -> None:
        app = object.__new__(gui.App)
        app.config_data = SimpleNamespace(
            accounts=[
                gui.AccountProfile(name="主号", cookie="SESSDATA=a"),
                gui.AccountProfile(name="小号", cookie="SESSDATA=b"),
            ],
            account_name="主号",
        )
        app.account_name_var = FakeVar("主号")
        app.cookie_text = FakeText("SESSDATA=changed")
        app.cookie_validation_var = FakeVar("")
        app.logs: list[str] = []
        saved: list[str] = []
        app._log = app.logs.append  # type: ignore[method-assign]
        app._save = lambda: saved.append(app.account_name_var.get()) or SimpleNamespace(account_name=app.account_name_var.get())  # type: ignore[method-assign]
        app._refresh_cookie_placeholder = lambda: None  # type: ignore[method-assign]
        app._refresh_summary_bar = lambda: None  # type: ignore[method-assign]
        app._refresh_account_selector = lambda: None  # type: ignore[method-assign]

        gui.App._select_account_for_edit(app, "小号")

        self.assertEqual(saved, ["主号"])
        self.assertEqual(app.account_name_var.get(), "小号")
        self.assertEqual(app.cookie_text.get("1.0", "end"), "SESSDATA=b")
        self.assertTrue(any("已先保存当前账号：主号" in item for item in app.logs))

    def test_select_current_account_gives_feedback(self) -> None:
        app = object.__new__(gui.App)
        app.account_name_var = FakeVar("主号")
        app.cookie_text = FakeText("SESSDATA=a")
        app.logs: list[str] = []
        app._log = app.logs.append  # type: ignore[method-assign]

        gui.App._select_account_for_edit(app, "主号")

        self.assertTrue(app.cookie_text.focused)
        self.assertTrue(any("正在编辑账号：主号" in item for item in app.logs))

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

    def test_current_config_new_account_is_saved_and_marked_active(self) -> None:
        app = object.__new__(gui.App)
        app.config_data = SimpleNamespace(
            accounts=[gui.AccountProfile(name="默认账号", cookie="SESSDATA=a")],
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

        config = gui.App._current_config(app)

        self.assertEqual([(item.name, item.cookie) for item in config.accounts],
                         [("账号 2", "SESSDATA=b"), ("默认账号", "SESSDATA=a")])
        self.assertEqual(config.active_accounts, ["默认账号", "账号 2"])

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
