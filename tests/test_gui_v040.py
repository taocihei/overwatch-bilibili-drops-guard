from __future__ import annotations

import queue
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

import tkinter as tk

from bili_drop_guard import gui
from bili_drop_guard.watcher import WatchWorkerStatus


class _HiddenRootCase(unittest.TestCase):
    """涉及真实 widget 的 GUI 测试基类。"""

    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self) -> None:
        self.root.update_idletasks()
        self.root.destroy()


class RoundedPanelLayoutTest(_HiddenRootCase):
    def test_auto_height_panel_does_not_keep_canvas_default_height(self) -> None:
        panel = gui.RoundedPanel(self.root, fill=gui.SURFACE, background=gui.APP_BG, padding=(14, 10))
        panel.pack(fill="x")
        tk.Label(panel.inner, text="使用说明", bg=gui.SURFACE).pack()

        self.root.update_idletasks()

        configured_height = int(panel.cget("height"))
        self.assertGreater(configured_height, 30)
        self.assertLess(configured_height, 80)


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
            self.assertEqual(toplevel.title(), "上手指引")
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


if __name__ == "__main__":
    unittest.main()
