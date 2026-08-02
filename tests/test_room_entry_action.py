from __future__ import annotations

import unittest

from bili_drop_guard.bilibili import RoomInfo
from bili_drop_guard.watcher import HeartbeatState, LiveWatcher, WatchOptions


class _CompleteHeartbeatClient:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def room_entry_action(self, room: RoomInfo) -> dict:
        self.calls.append("entry")
        return {}

    def enter_room_heartbeat(self, room: RoomInfo) -> dict:
        self.calls.append("legacy")
        return {"heartbeat_interval": 60, "timestamp": 100, "secret_key": "legacy", "secret_rule": [0]}

    def get_live_play_url(self, room: RoomInfo) -> str:
        self.calls.append("play")
        return "https://example.com/live.flv"

    def start_live_watch_session(self, room: RoomInfo, play_url: str) -> dict:
        self.calls.append("official")
        return {"hbil": 60, "sid": "sid-1", "stky": "key-1"}


class RoomEntryActionTest(unittest.TestCase):
    def test_room_entry_action_runs_before_both_watch_protocols(self) -> None:
        calls: list[str] = []
        watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)

        watcher._start_heartbeat_session(
            _CompleteHeartbeatClient(calls),
            RoomInfo(room_id=23612045, live_status=1),
            HeartbeatState(),
        )

        self.assertEqual(calls, ["entry", "legacy", "play", "official"])

    def test_room_entry_action_failure_does_not_block_watch_protocols(self) -> None:
        calls: list[str] = []
        logs: list[str] = []

        class FlakyEntryClient(_CompleteHeartbeatClient):
            def room_entry_action(self, room: RoomInfo) -> dict:
                self.calls.append("entry-failed")
                raise RuntimeError("activity api down")

        watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), logs.append)
        state = watcher._start_heartbeat_session(
            FlakyEntryClient(calls),
            RoomInfo(room_id=23612045, live_status=1),
            HeartbeatState(),
        )

        self.assertEqual(calls, ["entry-failed", "legacy", "play", "official"])
        self.assertEqual(state.session_id, "sid-1")
        self.assertTrue(any("累计失败 1 次" in message for message in logs))

    def test_room_entry_failure_log_is_aggregated(self) -> None:
        logs: list[str] = []
        watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), logs.append)

        for _ in range(20):
            watcher._record_room_entry_failure()

        failure_logs = [message for message in logs if "上报进入直播间累计失败" in message]
        self.assertEqual(failure_logs, ["上报进入直播间累计失败 1 次（不影响心跳，可能是代理抖动）"])

    def test_one_hundred_workers_are_staggered_over_about_fifteen_seconds(self) -> None:
        watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)

        self.assertEqual(watcher._watch_start_delay(1), 0.0)
        self.assertAlmostEqual(watcher._watch_start_delay(100), 14.85)


if __name__ == "__main__":
    unittest.main()
