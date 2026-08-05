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
    def test_room_entry_action_runs_before_x25kn_enter(self) -> None:
        calls: list[str] = []
        watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)

        watcher._start_heartbeat_session(
            _CompleteHeartbeatClient(calls),
            RoomInfo(room_id=23612045, live_status=1),
            HeartbeatState(),
        )

        self.assertEqual(calls, ["entry", "legacy"])

    def test_room_entry_action_failure_blocks_invalid_x25kn_session(self) -> None:
        calls: list[str] = []
        logs: list[str] = []

        class FlakyEntryClient(_CompleteHeartbeatClient):
            def room_entry_action(self, room: RoomInfo) -> dict:
                self.calls.append("entry-failed")
                raise RuntimeError("activity api down")

        watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), logs.append)
        with self.assertRaisesRegex(RuntimeError, "activity api down"):
            watcher._start_heartbeat_session(
                FlakyEntryClient(calls),
                RoomInfo(room_id=23612045, live_status=1),
                HeartbeatState(),
            )

        self.assertEqual(calls, ["entry-failed"])

    def test_one_hundred_workers_match_competitor_one_second_stagger(self) -> None:
        watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)

        self.assertEqual(watcher._watch_start_delay(1), 0.0)
        self.assertEqual(watcher._watch_start_delay(100), 99.0)


if __name__ == "__main__":
    unittest.main()
