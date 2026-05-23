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


if __name__ == "__main__":
    unittest.main()
