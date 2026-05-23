from __future__ import annotations

import threading
import unittest

from bili_drop_guard.watcher import LiveWatcher, WatchOptions, WatchWorkerStatus


class GetWatchStatusSnapshotTest(unittest.TestCase):
    def _new_watcher(self, worker_count: int) -> LiveWatcher:
        watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1", watch_threads=worker_count), lambda _m: None)
        watcher._watch_worker_count = worker_count
        for worker_id in range(1, worker_count + 1):
            watcher._set_watch_status(worker_id, "正常", interval=60)
        return watcher

    def test_snapshot_returns_workers_sorted_by_id(self) -> None:
        watcher = self._new_watcher(5)

        snapshot, summary = watcher.get_watch_status_snapshot()

        self.assertEqual([row.worker_id for row in snapshot], [1, 2, 3, 4, 5])
        self.assertTrue(all(isinstance(row, WatchWorkerStatus) for row in snapshot))
        self.assertIn("5/5 正常", summary)

    def test_snapshot_supports_one_hundred_workers(self) -> None:
        watcher = self._new_watcher(100)

        snapshot, summary = watcher.get_watch_status_snapshot()

        self.assertEqual(len(snapshot), 100)
        self.assertEqual(snapshot[0].worker_id, 1)
        self.assertEqual(snapshot[-1].worker_id, 100)
        self.assertIn("100/100 正常", summary)

    def test_snapshot_carries_interval_and_message(self) -> None:
        watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1", watch_threads=2), lambda _m: None)
        watcher._watch_worker_count = 2
        watcher._set_watch_status(1, "正常", interval=45, message="ok")
        watcher._set_watch_status(2, "暂时失败", message="超时")

        snapshot, _summary = watcher.get_watch_status_snapshot()

        self.assertEqual(snapshot[0].state, "正常")
        self.assertEqual(snapshot[0].interval, 45)
        self.assertEqual(snapshot[0].message, "ok")
        self.assertEqual(snapshot[1].state, "暂时失败")
        self.assertIsNone(snapshot[1].interval)
        self.assertEqual(snapshot[1].message, "超时")

    def test_snapshot_thread_safe_under_concurrent_writes(self) -> None:
        watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1", watch_threads=20), lambda _m: None)
        watcher._watch_worker_count = 20
        stop = threading.Event()

        def writer() -> None:
            counter = 0
            while not stop.is_set():
                for worker_id in range(1, 21):
                    watcher._set_watch_status(worker_id, "正常", interval=60 + (counter % 5))
                counter += 1

        writers = [threading.Thread(target=writer, daemon=True) for _ in range(4)]
        for thread in writers:
            thread.start()
        try:
            for _ in range(200):
                snapshot, summary = watcher.get_watch_status_snapshot()
                self.assertEqual(len(snapshot), 20)
                self.assertIn("/20", summary)
        finally:
            stop.set()
            for thread in writers:
                thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
