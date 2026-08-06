from __future__ import annotations

import threading
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import tkinter as tk
from tkinter import ttk

from bili_drop_guard import gui
from bili_drop_guard.sponsor import SponsorOrderBatch, SponsorError
from bili_drop_guard.watcher import WatchWorkerStatus


_SHARED_ROOT: tk.Tk | None = None


def _shared_hidden_root() -> tk.Tk:
    """Keep one Tcl interpreter alive; Python 3.14/Windows is flaky when Tk is reloaded repeatedly."""

    global _SHARED_ROOT
    if _SHARED_ROOT is None:
        _SHARED_ROOT = tk.Tk()
        _SHARED_ROOT.withdraw()
    return _SHARED_ROOT


class _HiddenRootCase(unittest.TestCase):
    """涉及真实 widget 的 GUI 测试基类。"""

    def setUp(self) -> None:
        self.root = _shared_hidden_root()

    def tearDown(self) -> None:
        for child in self.root.winfo_children():
            child.destroy()
        self.root.update_idletasks()


class BackendNetworkLabelTest(unittest.TestCase):
    def test_transient_heartbeat_retry_has_an_explicit_status(self) -> None:
        rows = [WatchWorkerStatus(worker_id=1, state="重试中", interval=15, message="原会话重试")]

        self.assertEqual(gui._backend_network_label(rows, None), "心跳重试中")


class RoundedPanelLayoutTest(_HiddenRootCase):
    def test_auto_height_panel_does_not_keep_canvas_default_height(self) -> None:
        panel = gui.RoundedPanel(self.root, fill=gui.SURFACE, background=gui.APP_BG, padding=(14, 10))
        panel.pack(fill="x")
        tk.Label(panel.inner, text="使用说明", bg=gui.SURFACE).pack()

        self.root.update_idletasks()

        configured_height = int(panel.cget("height"))
        self.assertGreater(configured_height, 30)
        self.assertLess(configured_height, 80)


class SmallWindowLayoutRegressionTest(unittest.TestCase):
    def test_three_digit_watch_connection_value_is_not_clipped(self) -> None:
        app = gui.App(preview_mode=True)
        try:
            app.geometry("1280x840+0+0")
            app.watch_threads_var.set(100)
            for _ in range(5):
                app.update()

            number_inputs: list[gui.NumberInput] = []

            def collect(widget: tk.Misc) -> None:
                for child in widget.winfo_children():
                    if isinstance(child, gui.NumberInput):
                        number_inputs.append(child)
                    collect(child)

            collect(app.execution_panel)
            self.assertEqual(len(number_inputs), 1)
            number_input = number_inputs[0]
            self.assertEqual(app.watch_threads_var.get(), 100)
            self.assertGreaterEqual(number_input.entry.winfo_width(), number_input.entry.winfo_reqwidth())
        finally:
            app.destroy()

    def test_default_window_shows_complete_settings_and_expanded_log_area(self) -> None:
        app = gui.App(preview_mode=True)
        try:
            app.geometry("1280x840+0+0")
            for _ in range(5):
                app.update()

            first, last = app.settings_canvas.yview()
            self.assertAlmostEqual(first, 0.0, places=3)
            self.assertAlmostEqual(last, 1.0, places=3)
            self.assertLess(app.room_entry.winfo_rootx(), app.monitor.winfo_rootx())
            self.assertGreaterEqual(app.start_button.winfo_rootx(), app.monitor.winfo_rootx())
            self.assertLessEqual(
                app.controls.winfo_rootx() + app.controls.winfo_width(),
                app.monitor.winfo_rootx() + app.monitor.winfo_width(),
            )
            self.assertLessEqual(app.brand_logo.winfo_width(), 34)
            buttons: list[str] = []

            def collect_buttons(widget: tk.Misc) -> None:
                for child in widget.winfo_children():
                    if isinstance(child, gui.LabelButton):
                        buttons.append(str(child.text))
                    collect_buttons(child)

            collect_buttons(app)
            self.assertNotIn("粘贴", buttons)
            self.assertGreaterEqual(app.log_wrap.winfo_height(), 300)
            self.assertEqual(app.log_empty_canvas.winfo_manager(), "place")
            self.assertEqual(app.log_empty_label.winfo_manager(), "place")
            self.assertLess(app.log_empty_label.winfo_y(), 80)
            self.assertEqual(app.progress_ring.caption, "")
            ring_text = [
                app.progress_ring.itemcget(item, "text")
                for item in app.progress_ring.find_all()
                if app.progress_ring.type(item) == "text"
            ]
            self.assertNotIn("点开始挂宝", ring_text)
        finally:
            app.destroy()

    def test_status_metrics_keep_complete_labels_in_independent_rows(self) -> None:
        app = gui.App(preview_mode=True)
        try:
            app.geometry("1280x840+0+0")
            for _ in range(5):
                app.update()

            self.assertEqual(
                [label.cget("text") for label in app.status_metric_labels],
                ["启动时间", "运行时长", "下次计时", "网络状态"],
            )
            self.assertEqual(len(app.status_metric_rows), 4)
            for row, label in zip(app.status_metric_rows, app.status_metric_labels):
                self.assertGreaterEqual(label.winfo_width(), label.winfo_reqwidth())
                self.assertGreaterEqual(label.winfo_rootx(), row.winfo_rootx() + 20)
                self.assertLessEqual(
                    label.winfo_rootx() + label.winfo_width(),
                    row.winfo_rootx() + row.winfo_width(),
                )
        finally:
            app.destroy()

    def test_default_runtime_metrics_keep_label_and_value_separated(self) -> None:
        app = gui.App(preview_mode=True)
        try:
            app.geometry("1280x840+0+0")
            for _ in range(5):
                app.update()

            labels: list[tk.Label] = []

            def collect(widget: tk.Misc) -> None:
                for child in widget.winfo_children():
                    if isinstance(child, tk.Label) and child.cget("text") == "启动时间":
                        labels.append(child)
                    collect(child)

            collect(app)
            self.assertEqual(len(labels), 1)
            label = labels[0]
            value = label.master.grid_slaves(row=0, column=2)[0]
            gap = value.winfo_rootx() - (label.winfo_rootx() + label.winfo_width())
            self.assertGreaterEqual(gap, 8)
            self.assertGreaterEqual(label.winfo_width(), label.winfo_reqwidth())
            self.assertGreaterEqual(value.winfo_width(), value.winfo_reqwidth())
        finally:
            app.destroy()

    def test_two_accounts_keep_account_actions_visible_at_default_size(self) -> None:
        app = gui.App(preview_mode=True)
        try:
            app.config_data = gui.AppConfig(
                cookie="SESSDATA=a",
                account_name="默认账号",
                accounts=[
                    gui.AccountProfile(name="默认账号", cookie="SESSDATA=a"),
                    gui.AccountProfile(name="备用账号", cookie="SESSDATA=b"),
                ],
                active_accounts=["默认账号"],
            )
            app.editing_account_name = "默认账号"
            app.account_name_var.set("默认账号")
            app.cookie_text.delete("1.0", "end")
            app.cookie_text.insert("1.0", "SESSDATA=a")
            app._build_account_checklist()
            app.geometry("1280x840+0+0")
            for _ in range(6):
                app.update()

            canvas_bottom = app.settings_canvas.winfo_rooty() + app.settings_canvas.winfo_height()
            actions_bottom = app.save_account_button.winfo_rooty() + app.save_account_button.winfo_height()
            self.assertLessEqual(actions_bottom, canvas_bottom)
        finally:
            app.destroy()


class SmallWindowAndButtonLayoutTest(_HiddenRootCase):
    def test_button_without_explicit_width_requests_enough_space_for_text(self) -> None:
        button = gui.LabelButton(
            self.root,
            "重试获取二维码",
            lambda: None,
            fill=gui.SECONDARY,
        )
        button.pack()
        self.root.update_idletasks()
        text_bounds = button.bbox("all")
        self.assertIsNotNone(text_bounds)
        assert text_bounds is not None
        self.assertLessEqual(text_bounds[2], button.winfo_reqwidth())

    def test_primary_and_account_actions_are_reachable_at_minimum_size(self) -> None:
        app = gui.App(preview_mode=True)
        try:
            app.geometry("1080x660+0+0")
            for _ in range(5):
                app.update()

            self.assertLessEqual(
                app.controls.winfo_rootx() + app.controls.winfo_width(),
                app.winfo_rootx() + app.winfo_width(),
            )

            app.settings_canvas.yview_moveto(1.0)
            for _ in range(5):
                app.update()
            wanted = {"添加账号", "取消修改", "B站验证", "清空", "▶ 开始挂宝", "领取奖励"}
            found: dict[str, tk.Misc] = {}

            def collect(widget: tk.Misc) -> None:
                for child in widget.winfo_children():
                    text = getattr(child, "text", "")
                    if text in wanted:
                        found[str(text)] = child
                    collect(child)

            collect(app)
            self.assertEqual(set(found), wanted)
            root_right = app.winfo_rootx() + app.winfo_width()
            root_bottom = app.winfo_rooty() + app.winfo_height()
            for name, widget in found.items():
                self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), root_right, name)
                self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), root_bottom, name)
        finally:
            app.destroy()


class WatchStatusCardCollapsedTest(_HiddenRootCase):
    def test_collapsed_card_shows_summary_text(self) -> None:
        card = gui.WatchStatusCard(self.root)

        card.update_snapshot([], "等待挂宝开始")

        self.assertEqual(card.summary_var.get(), "等待挂宝开始")
        self.assertFalse(card.is_expanded())

    def test_collapsed_card_updates_summary_when_snapshot_changes(self) -> None:
        card = gui.WatchStatusCard(self.root)

        card.update_snapshot(
            [WatchWorkerStatus(worker_id=i, state="正常", interval=60, message="") for i in range(1, 21)],
            "后台计时状态：20/20 正常，下一次约 60 秒后",
        )

        self.assertIn("20/20 正常", card.summary_var.get())


class WatchStatusCardExpandedTest(_HiddenRootCase):
    def test_expanded_card_renders_one_row_per_worker(self) -> None:
        card = gui.WatchStatusCard(self.root)
        snapshot = [
            WatchWorkerStatus(worker_id=1, state="正常", interval=60, message=""),
            WatchWorkerStatus(worker_id=2, state="等待开播", interval=None, message="房间未开播"),
            WatchWorkerStatus(worker_id=3, state="正常", interval=58, message=""),
        ]

        card.update_snapshot(snapshot, "后台计时状态：2/3 正常")
        card.toggle()  # expand

        rendered = card.rendered_rows_for_test()
        self.assertEqual(len(rendered), 3)
        labels = [row["label"] for row in rendered]
        self.assertEqual(labels[0], "#01")
        self.assertEqual(labels[2], "#03")

    def test_expanded_card_supports_one_hundred_workers(self) -> None:
        card = gui.WatchStatusCard(self.root)
        snapshot = [
            WatchWorkerStatus(worker_id=i, state="正常", interval=60, message="")
            for i in range(1, 101)
        ]

        card.update_snapshot(snapshot, "后台计时状态：100/100 正常")
        card.toggle()

        rendered = card.rendered_rows_for_test()
        self.assertEqual(len(rendered), 100)
        self.assertEqual(rendered[0]["label"], "#001")
        self.assertEqual(rendered[-1]["label"], "#100")

    def test_expanded_card_shows_interval_for_normal_state(self) -> None:
        card = gui.WatchStatusCard(self.root)
        snapshot = [
            WatchWorkerStatus(worker_id=1, state="正常", interval=45, message=""),
            WatchWorkerStatus(worker_id=2, state="暂时失败", interval=None, message="网络超时"),
        ]

        card.update_snapshot(snapshot, "后台计时状态：1/2 正常")
        card.toggle()

        rendered = card.rendered_rows_for_test()
        self.assertIn("下一次 45s", rendered[0]["detail"])
        self.assertIn("网络超时", rendered[1]["detail"])


class AccountChecklistTest(_HiddenRootCase):
    def _editor_app(self) -> gui.App:
        app = object.__new__(gui.App)
        app.config_data = SimpleNamespace(
            accounts=[
                gui.AccountProfile(name="主号", cookie="SESSDATA=a"),
                gui.AccountProfile(name="小号", cookie="SESSDATA=b"),
            ],
            account_name="主号",
            active_accounts=["主号"],
        )
        app.account_name_var = tk.StringVar(master=self.root, value="主号")
        app.cookie_text = tk.Text(self.root)
        app.cookie_text.insert("1.0", "SESSDATA=a")
        app.editing_account_name = "主号"
        app._delete_account = lambda _name=None: None  # type: ignore[method-assign]
        return app

    def test_rebuild_preserves_unsaved_checked_accounts(self) -> None:
        app = object.__new__(gui.App)
        app._account_check_frame = tk.Frame(self.root)
        app.config_data = SimpleNamespace(
            accounts=[
                gui.AccountProfile(name="主号", cookie="SESSDATA=a"),
                gui.AccountProfile(name="小号", cookie="SESSDATA=b"),
            ],
            account_name="主号",
            active_accounts=["主号"],
        )
        app.account_name_var = tk.StringVar(master=self.root, value="主号")
        app.editing_account_name = "主号"
        app.account_checks = {
            "主号": tk.BooleanVar(master=self.root, value=True),
            "小号": tk.BooleanVar(master=self.root, value=True),
        }
        app._on_account_check_toggled = lambda _name: None  # type: ignore[method-assign]
        app._select_account_for_edit = lambda _name: None  # type: ignore[method-assign]
        app._delete_account = lambda _name=None: None  # type: ignore[method-assign]

        gui.App._build_account_checklist(app)

        self.assertTrue(app.account_checks["主号"].get())
        self.assertTrue(app.account_checks["小号"].get())

        descendants: list[tk.Misc] = []

        def walk(widget: tk.Misc) -> None:
            for child in widget.winfo_children():
                descendants.append(child)
                walk(child)

        walk(app._account_check_frame)
        row_buttons = [child.text for child in descendants if isinstance(child, gui.LabelButton)]
        self.assertEqual(row_buttons.count("删除"), 2)
        self.assertNotIn("正在编辑", row_buttons)
        self.assertNotIn("编辑", row_buttons)
        self.assertEqual(sum(isinstance(child, gui.AccountCheck) for child in descendants), 2)
        self.assertFalse(any(isinstance(child, (tk.Checkbutton, ttk.Checkbutton)) for child in descendants))

    def test_custom_account_check_toggles_by_keyboard_action(self) -> None:
        value = tk.BooleanVar(master=self.root, value=False)
        calls: list[bool] = []
        check = gui.AccountCheck(
            self.root,
            value,
            lambda: calls.append(value.get()),
            background=gui.SURFACE,
        )

        result = check._toggle()

        self.assertEqual(result, "break")
        self.assertTrue(value.get())
        self.assertEqual(calls, [True])

    def test_clearing_cookie_does_not_implicitly_delete_saved_account(self) -> None:
        app = self._editor_app()
        app.cookie_text.delete("1.0", "end")

        accounts = gui.App._accounts_with_current_cookie(app)

        self.assertEqual([(account.name, account.cookie) for account in accounts], [
            ("主号", "SESSDATA=a"),
            ("小号", "SESSDATA=b"),
        ])

    def test_rename_replaces_original_account_instead_of_duplicating_it(self) -> None:
        app = self._editor_app()
        app.account_name_var.set("主账号")
        app.cookie_text.delete("1.0", "end")
        app.cookie_text.insert("1.0", "SESSDATA=renamed")

        accounts = gui.App._accounts_with_current_cookie(app)

        self.assertEqual([(account.name, account.cookie) for account in accounts], [
            ("主账号", "SESSDATA=renamed"),
            ("小号", "SESSDATA=b"),
        ])


class ManualRefreshButtonTest(unittest.TestCase):
    def _new_app(self) -> gui.App:
        app = object.__new__(gui.App)
        app.watcher = None
        app._log_calls: list[str] = []
        app._log = app._log_calls.append  # type: ignore[method-assign]
        return app

    def test_handle_manual_refresh_logs_when_no_watcher(self) -> None:
        app = self._new_app()

        gui.App._handle_manual_refresh(app)

        self.assertTrue(any("请先开始挂宝" in message for message in app._log_calls))

    def test_handle_manual_refresh_calls_refresh_progress_once(self) -> None:
        app = self._new_app()
        fake_watcher = MagicMock()
        fake_watcher.running = True
        app.watcher = fake_watcher

        gui.App._handle_manual_refresh(app)

        fake_watcher.refresh_progress_once.assert_called_once()

    def test_handle_manual_refresh_skips_when_watcher_not_running(self) -> None:
        app = self._new_app()
        fake_watcher = MagicMock()
        fake_watcher.running = False
        app.watcher = fake_watcher

        gui.App._handle_manual_refresh(app)

        fake_watcher.refresh_progress_once.assert_not_called()


class RediscoverTasksButtonTest(unittest.TestCase):
    def _new_app(self) -> gui.App:
        app = object.__new__(gui.App)
        app.watcher = None
        app._log_calls: list[str] = []
        app._log = app._log_calls.append  # type: ignore[method-assign]
        return app

    def test_handle_rediscover_logs_when_no_watcher(self) -> None:
        app = self._new_app()

        gui.App._handle_rediscover_tasks(app)

        self.assertTrue(any("请先开始挂宝" in message for message in app._log_calls))

    def test_handle_rediscover_calls_watcher_method(self) -> None:
        app = self._new_app()
        fake_watcher = MagicMock()
        fake_watcher.running = True
        app.watcher = fake_watcher

        gui.App._handle_rediscover_tasks(app)

        fake_watcher.rediscover_tasks_once.assert_called_once()

    def test_handle_rediscover_skips_when_watcher_not_running(self) -> None:
        app = self._new_app()
        fake_watcher = MagicMock()
        fake_watcher.running = False
        app.watcher = fake_watcher

        gui.App._handle_rediscover_tasks(app)

        fake_watcher.rediscover_tasks_once.assert_not_called()


class OnboardingGuideTest(_HiddenRootCase):
    def test_build_onboarding_guide_creates_toplevel(self) -> None:
        toplevel = gui.build_onboarding_guide(self.root)
        try:
            self.assertIsInstance(toplevel, tk.Toplevel)
            self.assertIsInstance(toplevel, gui.AppDialog)
            self.assertEqual(toplevel.title(), "上手指引")
            self.assertEqual(int(toplevel.overrideredirect()), 1)
        finally:
            toplevel.destroy()

    def test_onboarding_guide_contains_four_step_titles(self) -> None:
        toplevel = gui.build_onboarding_guide(self.root)
        try:
            texts: list[str] = []

            def collect(widget: tk.Misc) -> None:
                for child in widget.winfo_children():
                    try:
                        text = child.cget("text")
                    except tk.TclError:
                        text = ""
                    if isinstance(text, str) and text:
                        texts.append(text)
                    collect(child)

            collect(toplevel)
            combined = "\n".join(texts)
            self.assertIn("获取 Cookie", combined)
            self.assertIn("确认直播间", combined)
            self.assertIn("开始计时", combined)
            self.assertIn("领取奖励", combined)
        finally:
            toplevel.destroy()

    def test_onboarding_guide_can_be_dismissed(self) -> None:
        toplevel = gui.build_onboarding_guide(self.root)

        toplevel.destroy()

        self.assertFalse(toplevel.winfo_exists())


class AppDialogTest(_HiddenRootCase):
    def test_dialog_is_centered_and_supports_titlebar_dragging(self) -> None:
        dialog = gui.AppDialog(self.root, title="测试弹窗", width=360, height=260)
        try:
            self.root.update()
            original_x = dialog.winfo_x()
            original_y = dialog.winfo_y()
            dialog._begin_move(SimpleNamespace(x_root=original_x + 20, y_root=original_y + 20))  # type: ignore[arg-type]
            dialog._move(SimpleNamespace(x_root=original_x + 60, y_root=original_y + 55))  # type: ignore[arg-type]
            self.root.update_idletasks()

            self.assertGreaterEqual(dialog.winfo_x(), original_x)
            self.assertGreaterEqual(dialog.winfo_y(), original_y)
            self.assertIs(dialog.content.master, dialog.winfo_children()[0])
        finally:
            dialog.destroy()

    def test_visible_parent_dialog_uses_exact_parent_center(self) -> None:
        self.root.deiconify()
        self.root.geometry("900x640+140+90")
        self.root.update()
        dialog = gui.AppDialog(self.root, title="居中测试", width=420, height=300)
        try:
            for _ in range(3):
                self.root.update()
            expected_x = self.root.winfo_rootx() + (self.root.winfo_width() - 420) // 2
            expected_y = self.root.winfo_rooty() + (self.root.winfo_height() - 300) // 2
            self.assertLessEqual(abs(dialog.winfo_x() - expected_x), 1)
            self.assertLessEqual(abs(dialog.winfo_y() - expected_y), 1)
        finally:
            dialog.destroy()

    def test_pointer_click_reactivates_borderless_dialog(self) -> None:
        dialog = gui.AppDialog(self.root, title="激活测试", width=360, height=260)
        try:
            dialog.bring_to_front = MagicMock()  # type: ignore[method-assign]
            dialog._activate_from_pointer(SimpleNamespace(widget=dialog.titlebar))  # type: ignore[arg-type]
            dialog.bring_to_front.assert_called_once_with(focus=True)
        finally:
            dialog.destroy()

    def test_pointer_click_forces_keyboard_focus_back_to_entry(self) -> None:
        self.root.deiconify()
        dialog = gui.AppDialog(self.root, title="输入测试", width=360, height=260)
        entry = tk.Entry(dialog.content)
        entry.pack()
        try:
            for _ in range(3):
                self.root.update()
            dialog._activate_from_pointer(SimpleNamespace(widget=entry))  # type: ignore[arg-type]
            for _ in range(3):
                self.root.update()

            self.assertIs(dialog.focus_get(), entry)
        finally:
            dialog.destroy()


class SponsorDialogFlowTest(unittest.TestCase):
    def test_default_amount_automatically_requests_qr_without_generate_button(self) -> None:
        app = gui.App(preview_mode=True)
        app.withdraw()
        start_order = MagicMock()
        app._start_sponsor_order = start_order  # type: ignore[method-assign]
        try:
            with patch.object(gui.AppDialog, "_show_centered"):
                dialog = app._show_sponsor_dialog()
                dialog.after(220, dialog.quit)
                dialog.mainloop()

            texts: list[str] = []

            def collect(widget: tk.Misc) -> None:
                for child in widget.winfo_children():
                    text = getattr(child, "text", "")
                    if text:
                        texts.append(str(text))
                    try:
                        configured = child.cget("text")
                    except tk.TclError:
                        configured = ""
                    if configured:
                        texts.append(str(configured))
                    collect(child)

            collect(dialog)
            start_order.assert_called_once_with()
            self.assertNotIn("生成赞助二维码", texts)
            self.assertNotIn("确认金额", texts)
            self.assertEqual(dialog.title_label.winfo_manager(), "")
            self.assertIn("支持作者", texts)
            self.assertIn("已经支持过？", texts)
            self.assertIn("查看交流群  →", texts)
        finally:
            app._close_sponsor_dialog()
            app.destroy()

    def test_selecting_another_amount_refreshes_chips_and_order(self) -> None:
        amount_var = MagicMock()
        buttons = {
            amount: MagicMock()
            for amount in ("5.00", "10.00", "20.00", "50.00", "100.00", "other")
        }
        app = object.__new__(gui.App)
        app._sponsor_amount_var = amount_var
        app._sponsor_amount_choice = "10.00"
        app._sponsor_order = object()
        app._sponsor_amount_buttons = buttons
        app._sponsor_custom_frame = MagicMock()
        app._sponsor_custom_frame.winfo_manager.return_value = ""
        app._cancel_custom_sponsor_job = MagicMock()
        app._start_sponsor_order = MagicMock()

        gui.App._select_sponsor_amount(app, "20.00")

        amount_var.set.assert_called_once_with("20.00")
        app._start_sponsor_order.assert_called_once_with()
        self.assertEqual(buttons["20.00"].outline, gui.ACCENT_BORDER)
        self.assertEqual(buttons["10.00"].outline, gui.SUBTLE_OUTLINE)

    def test_sponsor_dialog_lists_requested_amounts_and_retry_button_fits(self) -> None:
        app = gui.App(preview_mode=True)
        app.geometry("1280x840+80+60")
        start_order = MagicMock()
        app._start_sponsor_order = start_order  # type: ignore[method-assign]
        try:
            dialog = app._show_sponsor_dialog()
            for _ in range(4):
                app.update()
            self.assertEqual(
                tuple(app._sponsor_amount_buttons),
                ("5.00", "10.00", "20.00", "50.00", "100.00", "other"),
            )
            app._show_sponsor_retry("重试获取二维码")
            for _ in range(3):
                app.update()
            button_bottom = (
                app._sponsor_retry_button.winfo_rooty()
                + app._sponsor_retry_button.winfo_height()
            )
            self.assertLessEqual(button_bottom, dialog.winfo_rooty() + dialog.winfo_height() - 12)
        finally:
            app._close_sponsor_dialog()
            app.destroy()

    def test_reopening_existing_sponsor_dialog_raises_without_recentering(self) -> None:
        app = gui.App(preview_mode=True)
        app.withdraw()
        app._start_sponsor_order = MagicMock()  # type: ignore[method-assign]
        try:
            with patch.object(gui.AppDialog, "_show_centered"):
                dialog = app._show_sponsor_dialog()
            dialog.bring_to_front = MagicMock()  # type: ignore[method-assign]

            reopened = app._show_sponsor_dialog()

            self.assertIs(reopened, dialog)
            dialog.bring_to_front.assert_called_once_with()
        finally:
            app._close_sponsor_dialog()
            app.destroy()

    def test_claimed_sponsor_can_reveal_group_without_payment_check(self) -> None:
        app = gui.App(preview_mode=True)
        app.withdraw()
        app._start_sponsor_order = MagicMock()  # type: ignore[method-assign]
        try:
            with patch.object(gui.AppDialog, "_show_centered"):
                app._show_sponsor_dialog()

            app._reveal_sponsor_group()
            app.update_idletasks()

            self.assertTrue(app._sponsor_group_revealed)
            self.assertFalse(bool(app._sponsor_group_prompt.winfo_manager()))
            self.assertTrue(bool(app._sponsor_success_frame.winfo_manager()))
            self.assertEqual(app._sponsor_status_var.get(), "群号已显示，可直接复制加入")
        finally:
            app._close_sponsor_dialog()
            app.destroy()

    def test_other_amount_automatically_generates_after_input_stops(self) -> None:
        app = gui.App(preview_mode=True)
        app.withdraw()
        app._start_sponsor_order = MagicMock()  # type: ignore[method-assign]
        try:
            with patch.object(gui.AppDialog, "_show_centered"):
                app._show_sponsor_dialog()
            app._start_sponsor_order.reset_mock()
            app._select_sponsor_amount("other")
            app._sponsor_custom_amount_var.set("38.5")
            app.after(600, app.quit)
            app.mainloop()

            self.assertEqual(app._sponsor_amount_var.get(), "38.50")
            self.assertEqual(app._sponsor_amount_choice, "other")
            app._start_sponsor_order.assert_called_once_with()
        finally:
            app._close_sponsor_dialog()
            app.destroy()

    def test_custom_amount_waits_until_typing_stops(self) -> None:
        custom_var = MagicMock()
        custom_var.get.return_value = "38.5"
        app = SimpleNamespace(
            _sponsor_amount_choice="other",
            _sponsor_custom_amount_var=custom_var,
            _sponsor_status_var=MagicMock(),
            _sponsor_custom_job=None,
            _cancel_custom_sponsor_job=MagicMock(),
            after=MagicMock(return_value="job-1"),
            _confirm_custom_sponsor_amount=MagicMock(),
        )

        gui.App._schedule_custom_sponsor_amount(app)

        app.after.assert_called_once_with(500, app._confirm_custom_sponsor_amount)
        self.assertEqual(app._sponsor_custom_job, "job-1")

    def test_refreshing_amount_resets_qr_label_to_text_dimensions(self) -> None:
        amount_var = MagicMock()
        amount_var.get.return_value = "10.00"
        qr_label = MagicMock()
        app = SimpleNamespace(
            _sponsor_dialog_exists=MagicMock(return_value=True),
            _cancel_sponsor_poll=MagicMock(),
            _hide_sponsor_retry=MagicMock(),
            _sponsor_loading=False,
            _sponsor_generation=3,
            _sponsor_amount_var=amount_var,
            _sponsor_order=object(),
            _sponsor_success_shown=True,
            _sponsor_success_frame=MagicMock(),
            _sponsor_status_var=MagicMock(),
            _sponsor_qr_label=qr_label,
            _cached_sponsor_order=MagicMock(return_value=None),
            _sponsor_warm_ready=threading.Event(),
            _sponsor_warm_started=False,
            _sponsor_http_client=None,
            _warm_sponsor_service=MagicMock(),
        )
        app._sponsor_warm_ready.set()

        with patch.object(gui.threading, "Thread"):
            gui.App._start_sponsor_order(app)

        qr_label.configure.assert_called_once_with(
            image="",
            text="正在生成 ¥10 二维码…",
            fg=gui.ACCENT,
            width=28,
            height=12,
        )

    def test_cached_sponsor_order_is_painted_without_network_wait(self) -> None:
        amount_var = MagicMock()
        amount_var.get.return_value = "10.00"
        cached = (MagicMock(), MagicMock(), b"qr")
        finish = MagicMock()
        app = SimpleNamespace(
            _sponsor_dialog_exists=MagicMock(return_value=True),
            _cancel_sponsor_poll=MagicMock(),
            _hide_sponsor_retry=MagicMock(),
            _sponsor_loading=False,
            _sponsor_generation=0,
            _sponsor_amount_var=amount_var,
            _sponsor_order=None,
            _sponsor_success_shown=False,
            _sponsor_success_frame=MagicMock(),
            _sponsor_status_var=MagicMock(),
            _sponsor_qr_label=MagicMock(),
            _cached_sponsor_order=MagicMock(return_value=cached),
            _finish_sponsor_order=finish,
        )

        gui.App._start_sponsor_order(app)

        finish.assert_called_once_with(
            1,
            "10.00",
            *cached,
            cache_result=False,
        )

    def test_startup_prioritizes_default_then_batches_remaining_presets(self) -> None:
        priority_client = MagicMock()
        priority_client.create_order.return_value = gui.SponsorOrder(
            order_id="order-10.00",
            amount="10.00",
        )
        priority_client.download_order_qr.return_value = b"qr"
        client = MagicMock()
        amounts = ["10.00", "5.00", "20.00", "50.00", "100.00"]
        remaining_amounts = ["5.00", "20.00", "50.00", "100.00"]
        client.reserve_orders.return_value = SponsorOrderBatch(
            checkout_intent_id="checkout-0123456789abcdef",
            orders=tuple(
                gui.SponsorOrder(order_id=f"order-{amount}", amount=amount)
                for amount in remaining_amounts
            ),
        )
        client.download_order_qr.return_value = b"qr"

        class ImmediateThread:
            def __init__(self, *, target, **_kwargs) -> None:
                self.target = target

            def start(self) -> None:
                self.target()

            def join(self) -> None:
                return None

        app = object.__new__(gui.App)
        app.preview_mode = False
        app._sponsor_warm_started = False
        app._sponsor_warm_ready = threading.Event()
        app._sponsor_prefetch_ready = threading.Event()
        app._sponsor_order_cache = {}
        app._sponsor_cache_lock = threading.RLock()
        app._sponsor_order_inflight = {}
        app._sponsor_order_errors = {}
        app._persist_sponsor_order_cache = MagicMock()
        app._sponsor_install_id = "desktop-0123456789abcdef"
        app._sponsor_checkout_intent_id = "checkout-0123456789abcdef"

        with (
            patch.object(
                gui.SponsorClient,
                "from_environment",
                side_effect=[priority_client, client],
            ),
            patch.object(gui.threading, "Thread", ImmediateThread),
        ):
            gui.App._warm_sponsor_service(app)

        priority_client.create_order.assert_called_once_with(
            "10.00",
            app_version=gui.__version__,
            install_id=app._sponsor_install_id,
            checkout_intent_id=app._sponsor_checkout_intent_id,
        )
        client.reserve_orders.assert_called_once_with(
            tuple(remaining_amounts),
            app_version=gui.__version__,
            install_id=app._sponsor_install_id,
            checkout_intent_id=app._sponsor_checkout_intent_id,
        )
        self.assertEqual(set(app._sponsor_order_cache), set(amounts))
        self.assertTrue(app._sponsor_warm_ready.is_set())
        self.assertTrue(app._sponsor_prefetch_ready.is_set())

    def test_batch_failure_falls_back_to_independent_single_order_clients(self) -> None:
        priority_client = MagicMock(name="priority")
        batch_client = MagicMock()
        batch_client.reserve_orders.side_effect = SponsorError("old service")
        fallback_clients = [MagicMock(name=f"fallback-{index}") for index in range(4)]
        created_amounts: list[str] = []
        for client in [priority_client, *fallback_clients]:
            client.create_order.side_effect = lambda amount, **_kwargs: (
                created_amounts.append(amount)
                or gui.SponsorOrder(order_id=f"order-{amount}", amount=amount)
            )
            client.download_order_qr.return_value = b"qr"

        class ImmediateThread:
            def __init__(self, *, target, **_kwargs) -> None:
                self.target = target

            def start(self) -> None:
                self.target()

            def join(self) -> None:
                return None

        app = object.__new__(gui.App)
        app.preview_mode = False
        app._sponsor_warm_started = False
        app._sponsor_warm_ready = threading.Event()
        app._sponsor_prefetch_ready = threading.Event()
        app._sponsor_order_cache = {}
        app._sponsor_cache_lock = threading.RLock()
        app._sponsor_order_inflight = {}
        app._sponsor_order_errors = {}
        app._persist_sponsor_order_cache = MagicMock()
        app._sponsor_install_id = "desktop-0123456789abcdef"
        app._sponsor_checkout_intent_id = "checkout-0123456789abcdef"

        with (
            patch.object(
                gui.SponsorClient,
                "from_environment",
                side_effect=[priority_client, batch_client, *fallback_clients],
            ),
            patch.object(gui.threading, "Thread", ImmediateThread),
        ):
            gui.App._warm_sponsor_service(app)

        self.assertEqual(
            created_amounts,
            ["10.00", "5.00", "20.00", "50.00", "100.00"],
        )
        self.assertTrue(all(client.create_order.call_count == 1 for client in fallback_clients))
        self.assertEqual(set(app._sponsor_order_cache), set(created_amounts))
        self.assertTrue(app._sponsor_prefetch_ready.is_set())

    def test_same_amount_uses_singleflight_and_shares_cached_result(self) -> None:
        app = object.__new__(gui.App)
        app._sponsor_order_cache = {}
        app._sponsor_cache_lock = threading.RLock()
        app._sponsor_order_inflight = {}
        app._sponsor_order_errors = {}
        app._persist_sponsor_order_cache = MagicMock()
        app._sponsor_install_id = "desktop-0123456789abcdef"
        app._sponsor_checkout_intent_id = "checkout-0123456789abcdef"
        client = MagicMock()
        entered = threading.Event()
        release = threading.Event()

        def create_order(amount: str, **_kwargs: object) -> gui.SponsorOrder:
            entered.set()
            self.assertTrue(release.wait(2.0))
            return gui.SponsorOrder(order_id=f"order-{amount}")

        client.create_order.side_effect = create_order
        client.download_order_qr.return_value = b"qr"
        results: list[tuple[object, object, bytes]] = []

        def fetch() -> None:
            results.append(gui.App._get_or_create_sponsor_order(app, "10.00"))

        with patch.object(gui.SponsorClient, "from_environment", return_value=client):
            first = threading.Thread(target=fetch)
            second = threading.Thread(target=fetch)
            first.start()
            self.assertTrue(entered.wait(1.0))
            second.start()
            release.set()
            first.join(2.0)
            second.join(2.0)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(client.create_order.call_count, 1)
        self.assertEqual(len(results), 2)
        self.assertIs(results[0][1], results[1][1])
        self.assertEqual(results[0][2], results[1][2])
        self.assertIn("10.00", app._sponsor_order_cache)

    def test_sponsor_order_cache_expires_before_payment_order(self) -> None:
        client = MagicMock()
        order = MagicMock()
        app = SimpleNamespace(
            _sponsor_order_cache={
                "10.00": (
                    100.0,
                    client,
                    order,
                    b"qr",
                )
            },
            _sponsor_cache_lock=threading.RLock(),
            _sponsor_cache_lifetime_seconds=MagicMock(
                return_value=gui.SPONSOR_ORDER_CACHE_TTL_SECONDS
            ),
            _remove_cached_sponsor_order=MagicMock(),
        )

        with patch.object(
            gui.time,
            "time",
            return_value=100.0 + gui.SPONSOR_ORDER_CACHE_TTL_SECONDS,
        ):
            result = gui.App._cached_sponsor_order(app, "10.00")

        self.assertIsNone(result)
        app._remove_cached_sponsor_order.assert_called_once_with("10.00")

    def test_sponsor_order_cache_rejects_absolute_provider_expiry(self) -> None:
        client = MagicMock()
        order = gui.SponsorOrder(
            order_id="order_expired_123",
            expires_at=(datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
            expires_in_seconds=6_000,
        )
        app = SimpleNamespace(
            _sponsor_order_cache={"10.00": (time.time(), client, order, b"qr")},
            _sponsor_cache_lock=threading.RLock(),
            _remove_cached_sponsor_order=MagicMock(),
        )
        app._sponsor_cache_lifetime_seconds = gui.App._sponsor_cache_lifetime_seconds
        app._sponsor_order_has_expired = gui.App._sponsor_order_has_expired

        result = gui.App._cached_sponsor_order(app, "10.00")

        self.assertIsNone(result)
        app._remove_cached_sponsor_order.assert_called_once_with("10.00")

    def test_expired_provider_status_automatically_refreshes_once(self) -> None:
        app = SimpleNamespace(
            _sponsor_generation=4,
            _sponsor_dialog_exists=MagicMock(return_value=True),
            _forget_current_sponsor_order=MagicMock(),
            _rotate_sponsor_checkout_intent=MagicMock(),
            _sponsor_auto_refresh_attempts=0,
            _sponsor_status_var=MagicMock(),
            _start_sponsor_order=MagicMock(),
            _show_sponsor_retry=MagicMock(),
        )

        gui.App._apply_sponsor_status(app, 4, gui.SponsorOrderStatus(state="expired"))

        app._start_sponsor_order.assert_called_once_with(automatic_retry=True)
        app._show_sponsor_retry.assert_not_called()
        self.assertEqual(app._sponsor_auto_refresh_attempts, 1)

    def test_sponsor_order_cache_survives_application_restart(self) -> None:
        client = MagicMock()
        client.base_url = "https://sponsor.example/api/sponsor"
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        order = gui.SponsorOrder(order_id="order_12345678", expires_at=expires_at)
        qr_data = b"\x89PNG\r\n\x1a\nfast-qr"

        with tempfile.TemporaryDirectory() as temporary_directory:
            cache_directory = Path(temporary_directory)
            cache_path = cache_directory / "sponsor-orders.json"
            writer = object.__new__(gui.App)
            writer._sponsor_cache_lock = threading.RLock()
            writer._sponsor_order_cache = {}
            with (
                patch.object(gui, "APP_DIR", cache_directory),
                patch.object(gui, "SPONSOR_ORDER_CACHE_PATH", cache_path),
            ):
                gui.App._cache_sponsor_order(
                    writer,
                    "10.00",
                    client,
                    order,
                    qr_data,
                )

                reader = object.__new__(gui.App)
                reader._sponsor_cache_lock = threading.RLock()
                reader._sponsor_order_cache = {}
                reader._sponsor_http_client = None
                with patch.object(
                    gui.SponsorClient,
                    "from_environment",
                    return_value=client,
                ):
                    gui.App._load_persistent_sponsor_order_cache(reader)

            cached = reader._sponsor_order_cache["10.00"]
            self.assertEqual(cached[2].order_id, order.order_id)
            self.assertEqual(cached[3], qr_data)
            self.assertIs(reader._sponsor_http_client, client)


if __name__ == "__main__":
    unittest.main()
