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


if __name__ == "__main__":
    unittest.main()
