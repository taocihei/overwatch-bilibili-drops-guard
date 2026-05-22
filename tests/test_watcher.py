from __future__ import annotations

import unittest
from threading import Lock

from bili_drop_guard import watcher
from bili_drop_guard.watcher import LiveWatcher, WatchOptions


class RecordingWatcher(LiveWatcher):
    def __init__(self, options: WatchOptions) -> None:
        super().__init__(options, lambda _message: None)
        self.started_workers: list[int] = []
        self.started_workers_lock = Lock()

    def _browser_watch_worker(self, worker_id: int) -> None:
        with self.started_workers_lock:
            self.started_workers.append(worker_id)


class LiveWatcherTest(unittest.TestCase):
    def test_watch_threads_start_browser_window_manager(self) -> None:
        live_watcher = RecordingWatcher(WatchOptions(cookie="a=b", room_id="1", watch_threads=3))

        live_watcher._start_watch_threads()
        for thread in live_watcher._watch_threads:
            thread.join(timeout=2)

        self.assertEqual(live_watcher.started_workers, [3])

    def test_claim_worker_uses_single_sequential_path(self) -> None:
        calls: list[tuple[int, str | None]] = []

        class FakeClient:
            def __init__(self, cookie: str) -> None:
                self.cookie = cookie

            def claim_user_task_rewards(self, up_id: int, task_id: str | None = None) -> dict[str, object]:
                calls.append((up_id, task_id))
                return {}

        original_client = watcher.BilibiliClient
        watcher.BilibiliClient = FakeClient
        try:
            logs: list[str] = []
            live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1", watch_threads=5), logs.append)
            live_watcher._last_up_id = 100
            live_watcher._claimable_task_ids.update({"task-a", "task-b"})

            live_watcher._claim_completed_worker()
        finally:
            watcher.BilibiliClient = original_client

        self.assertEqual(calls, [(100, "task-a"), (100, "task-b")])
        self.assertIn("开始领取奖励：领奖固定使用 1 个线程提交请求", logs)

    def test_claim_worker_refreshes_progress_before_claiming(self) -> None:
        calls: list[tuple[int, str | None]] = []
        progress = {"tasks": [{"task_id": "task-a", "name": "观看 30 分钟", "current": 30, "target": 30}]}

        class FakeClient:
            def __init__(self, cookie: str) -> None:
                self.cookie = cookie

            def get_user_task_progress(self, up_id: int, task_id: str | None = None) -> dict[str, object]:
                return progress

            def claim_user_task_rewards(self, up_id: int, task_id: str | None = None) -> dict[str, object]:
                calls.append((up_id, task_id))
                return {}

        original_client = watcher.BilibiliClient
        watcher.BilibiliClient = FakeClient
        try:
            logs: list[str] = []
            live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), logs.append)
            live_watcher._last_up_id = 100

            live_watcher._claim_completed_worker()
        finally:
            watcher.BilibiliClient = original_client

        self.assertEqual(calls, [(100, "task-a")])
        self.assertIn("领取前刷新任务进度", logs)

    def test_failed_claim_can_retry(self) -> None:
        calls: list[tuple[int, str | None]] = []

        class FakeClient:
            def __init__(self, cookie: str) -> None:
                self.cookie = cookie

            def claim_user_task_rewards(self, up_id: int, task_id: str | None = None) -> dict[str, object]:
                calls.append((up_id, task_id))
                if len(calls) == 1:
                    raise RuntimeError("临时失败")
                return {}

        original_client = watcher.BilibiliClient
        watcher.BilibiliClient = FakeClient
        try:
            live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
            with self.assertRaises(RuntimeError):
                live_watcher._claim_one_task(100, "task-a")

            result = live_watcher._claim_one_task(100, "task-a")
        finally:
            watcher.BilibiliClient = original_client

        self.assertEqual(calls, [(100, "task-a"), (100, "task-a")])
        self.assertEqual(result, "任务 task-a 领取请求已提交")

    def test_auto_discovers_task_ids_from_progress(self) -> None:
        logs: list[str] = []
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1", task_ids=[]), logs.append)
        progress = {
            "list": [
                {"task_id": "task-a", "task_name": "观看 10 分钟", "current": 1, "target": 10},
                {"taskId": "task-b", "name": "观看 20 分钟", "current": 20, "target": 20, "is_receive": 1},
            ]
        }

        found_claimable = live_watcher._check_and_claim_task(
            type("Client", (), {"get_user_task_progress": lambda self, up_id: progress})(), 100
        )

        self.assertEqual(live_watcher._known_task_ids, {"task-a", "task-b"})
        self.assertFalse(found_claimable)
        self.assertTrue(any("已自动识别任务 ID：task-a, task-b" in message for message in logs))

    def test_claimable_task_id_goes_to_specific_queue(self) -> None:
        logs: list[str] = []
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1", task_ids=[]), logs.append)
        progress = {"tasks": [{"id": "task-a", "name": "观看 10 分钟", "current": 10, "target": 10}]}

        found_claimable = live_watcher._check_and_claim_task(
            type("Client", (), {"get_user_task_progress": lambda self, up_id: progress})(), 100
        )

        self.assertEqual(live_watcher._claimable_task_ids, {"task-a"})
        self.assertTrue(found_claimable)
        self.assertFalse(live_watcher._claimable_general)

    def test_received_task_is_not_claimable_even_when_progress_full(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        progress = {
            "tasks": [
                {"id": "task-a", "name": "观看 10 分钟", "current": 10, "target": 10, "receive_status": 2},
                {"id": "task-b", "name": "观看 20 分钟", "current": 20, "target": 20, "reward_status": "received"},
            ]
        }

        claimable = live_watcher._find_claimable_task_refs(progress)

        self.assertEqual(claimable, [])

    def test_explicit_unclaimable_status_overrides_finished_status(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        progress = {
            "tasks": [
                {"id": "task-a", "name": "观看 10 分钟", "status": 1, "can_receive": 0},
            ]
        }

        claimable = live_watcher._find_claimable_task_refs(progress)

        self.assertEqual(claimable, [])

    def test_explicit_task_check_reports_claimable(self) -> None:
        progress = {"task": {"task_id": "task-a", "name": "观看 30 分钟", "current": 30, "target": 30}}

        class FakeClient:
            def __init__(self, cookie: str) -> None:
                self.cookie = cookie

            def get_user_task_progress(self, up_id: int, task_id: str | None = None) -> dict[str, object]:
                return progress

        original_client = watcher.BilibiliClient
        watcher.BilibiliClient = FakeClient
        try:
            live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1", task_ids=["task-a"]), lambda _message: None)

            found_claimable = live_watcher._check_explicit_task_ids(100)
        finally:
            watcher.BilibiliClient = original_client

        self.assertTrue(found_claimable)
        self.assertEqual(live_watcher._claimable_task_ids, {"task-a"})

    def test_merge_configured_and_discovered_task_ids(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)

        self.assertEqual(
            live_watcher._merge_task_ids(["task-b", "task-a"], {"task-a", "task-c"}),
            ["task-b", "task-a", "task-c"],
        )


if __name__ == "__main__":
    unittest.main()
