from __future__ import annotations

import unittest

from bili_drop_guard import watcher as watcher_module
from bili_drop_guard.bilibili import RoomInfo
from bili_drop_guard.watcher import HeartbeatState, LiveWatcher, WatchOptions


class StartHeartbeatSessionCallsRoomEntryActionFirstTest(unittest.TestCase):
    def test_room_entry_action_is_called_before_enter_room_heartbeat(self) -> None:
        calls: list[str] = []

        class FakeClient:
            def room_entry_action(self, room: RoomInfo) -> dict:
                calls.append(f"entry:{room.room_id}")
                return {}

            def enter_room_heartbeat(self, room: RoomInfo) -> dict:
                calls.append(f"x25kn_enter:{room.room_id}")
                return {"heartbeat_interval": 30, "timestamp": 100, "secret_key": "k", "secret_rule": [0]}

        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _m: None)
        room = RoomInfo(room_id=23612045, live_status=1)

        live_watcher._start_heartbeat_session(FakeClient(), room, HeartbeatState())

        self.assertEqual(calls, ["entry:23612045", "x25kn_enter:23612045"])

    def test_room_entry_action_failure_does_not_abort_heartbeat(self) -> None:
        logs: list[str] = []

        class FlakyClient:
            def room_entry_action(self, room: RoomInfo) -> dict:
                raise RuntimeError("activity api down")

            def enter_room_heartbeat(self, room: RoomInfo) -> dict:
                return {"heartbeat_interval": 30, "timestamp": 100, "secret_key": "k", "secret_rule": [0]}

        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), logs.append)
        room = RoomInfo(room_id=23612045, live_status=1)

        state = live_watcher._start_heartbeat_session(FlakyClient(), room, HeartbeatState())

        # 心跳能继续：interval 来自 enter_room_heartbeat 的成功返回
        self.assertEqual(state.interval, 30)
        # 第一次失败会写一条聚合日志（之后每 50 次再写一条，避免刷屏）
        self.assertTrue(any("上报进入直播间累计失败 1 次" in message for message in logs))

    def test_room_entry_failure_aggregated_log_does_not_spam(self) -> None:
        logs: list[str] = []
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), logs.append)
        # 触发 20 次失败，应该只写一条 "累计失败 1 次"
        for _ in range(20):
            live_watcher._record_room_entry_failure()
        room_entry_logs = [m for m in logs if "上报进入直播间" in m]
        self.assertEqual(len(room_entry_logs), 1)
        self.assertIn("累计失败 1 次", room_entry_logs[0])


class WorkerStaggerTest(unittest.TestCase):
    def test_workers_stagger_start_one_per_second_by_worker_id(self) -> None:
        # 这个测试不实际等待，只验证逻辑：worker_id N 调用 _stop.wait(N-1)
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _m: None)
        wait_calls: list[float] = []
        original_wait = live_watcher._stop.wait

        def recording_wait(timeout=None):
            wait_calls.append(float(timeout) if timeout is not None else -1.0)
            # 立刻设置 stop event，让 worker 跑完一次就退出
            live_watcher._stop.set()
            return True

        live_watcher._stop.wait = recording_wait  # type: ignore[method-assign]

        # 模拟启动 worker #3
        live_watcher._heartbeat_watch_worker(3, RoomInfo(room_id=1, live_status=1))

        # worker #3 应该 wait 2 秒（worker_id - 1）
        self.assertEqual(wait_calls[0], 2.0)

    def test_worker_id_one_does_not_stagger(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _m: None)
        live_watcher._stop.set()  # 立刻停，避免进入心跳循环
        wait_calls: list[float] = []
        original_wait = live_watcher._stop.wait

        def recording_wait(timeout=None):
            wait_calls.append(float(timeout) if timeout is not None else -1.0)
            return True

        live_watcher._stop.wait = recording_wait  # type: ignore[method-assign]

        live_watcher._heartbeat_watch_worker(1, RoomInfo(room_id=1, live_status=1))

        # worker #1 不应该有 stagger wait（它会立刻进入主循环，主循环里的 wait 不算 stagger）
        # 主循环里第一次 wait 是在 except 或正常 wait state.interval 时，但因为 _stop 已 set，会立即退出
        # 所以 wait_calls 可能为 0 或包含其他 wait（不应该是 stagger 那个）
        # 我们只关心：第一个 wait（如果有）不是 worker_id-1=0
        # 实际上 worker 1 在 stop 立刻 set 的情况下，wait 不被调用
        self.assertNotIn(0.0, wait_calls)


if __name__ == "__main__":
    unittest.main()
