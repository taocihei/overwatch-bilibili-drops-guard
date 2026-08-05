from __future__ import annotations

import unittest
from unittest.mock import patch

from bili_drop_guard.bilibili import BilibiliClient, RoomInfo
from bili_drop_guard.skynet_signer import sign_live_watch_payload
from bili_drop_guard.watcher import HeartbeatState, LiveWatcher, WatchOptions


class FakeResponse:
    def __init__(self, payload: dict, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = {"Content-Type": "application/json"}
        self.text = ""

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class RecordingSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.posts: list[tuple[str, dict]] = []

    def post(self, url: str, **kwargs) -> FakeResponse:
        self.posts.append((url, dict(kwargs.get("json") or {})))
        return self.responses.pop(0)

    def close(self) -> None:
        return None


class LiveWatchProtocolTest(unittest.TestCase):
    def test_official_te9kl_then_s82tq_flow_uses_rotated_session_state(self) -> None:
        client = BilibiliClient(
            "DedeUserID=93693916; SESSDATA=a; bili_jct=b",
            session_buvid="AUTO1234567890123456",
        )
        self.addCleanup(client.close)
        session = RecordingSession(
            [
                FakeResponse({"code": 0, "data": {"hbil": 50, "sid": "sid-1", "stky": "key-1"}}),
                FakeResponse({"code": 0, "data": {"hbil": 50, "sid": "sid-2", "stky": "key-2"}}),
                FakeResponse({"code": 0, "data": {"hbil": 45, "sid": "sid-3", "stky": "key-3"}}),
            ]
        )
        client.session = session  # type: ignore[assignment]
        room = RoomInfo(room_id=23612045, live_status=1)
        signed_payloads: list[dict] = []

        def sign(payload: dict) -> str:
            signed_payloads.append(dict(payload))
            return "f" * 32

        with patch("bili_drop_guard.bilibili.sign_live_watch_payload", side_effect=sign):
            started = client.start_live_watch_session(room, "https://example.com/live.flv")
            continued = client.continue_live_watch_session(
                room,
                "https://example.com/live.flv",
                1,
                str(started["sid"]),
                str(started["stky"]),
            )

        self.assertEqual([url.rsplit("/", 1)[-1] for url, _ in session.posts], ["te9Kl", "te9Kl", "s82Tq"])
        first = session.posts[0][1]
        self.assertEqual(first["qid"], 0)
        self.assertNotIn("csn", first)
        self.assertNotIn("sid", first)
        self.assertNotIn("stky", first)

        checked = session.posts[1][1]
        self.assertEqual(checked["qid"], 1)
        self.assertEqual(checked["sid"], "sid-1")
        self.assertEqual(checked["stky"], "key-1")
        self.assertEqual(checked["csn"], "f" * 32)
        self.assertEqual(signed_payloads[0], {key: value for key, value in checked.items() if key != "csn"})

        heartbeat = session.posts[2][1]
        self.assertEqual(heartbeat["qid"], 1)
        self.assertEqual(heartbeat["sid"], "sid-2")
        self.assertEqual(heartbeat["stky"], "key-2")
        self.assertEqual(continued, {"hbil": 45, "sid": "sid-3", "stky": "key-3"})

    def test_invalid_live_watch_response_is_not_treated_as_success(self) -> None:
        client = BilibiliClient(
            "DedeUserID=93693916; SESSDATA=a; bili_jct=b",
            session_buvid="AUTO1234567890123456",
        )
        self.addCleanup(client.close)
        client.session = RecordingSession([FakeResponse({"code": 0, "data": {"hbil": 50}})])  # type: ignore[assignment]

        with self.assertRaisesRegex(RuntimeError, "hbil/sid/stky"):
            client.start_live_watch_session(RoomInfo(room_id=23612045, live_status=1), "https://example.com/live.flv")

    def test_watcher_uses_x25kn_only_and_increments_sequence(self) -> None:
        calls: list[tuple] = []

        class FakeClient:
            def room_entry_action(self, room: RoomInfo) -> dict:
                calls.append(("entry", room.room_id))
                return {}

            def enter_room_heartbeat(self, room: RoomInfo) -> dict:
                calls.append(("legacy-start", room.room_id))
                return {"heartbeat_interval": 60, "timestamp": 100, "secret_key": "legacy-1", "secret_rule": [2, 5]}

            def in_room_heartbeat(
                self,
                room: RoomInfo,
                sequence: int,
                interval: int,
                ets: int,
                secret_key: str,
                secret_rule: list[int],
            ) -> dict:
                calls.append(("legacy-heartbeat", room.room_id, sequence, interval, ets, secret_key, secret_rule))
                return {"heartbeat_interval": 60, "timestamp": 200, "secret_key": "legacy-2", "secret_rule": [1, 4]}

            def get_live_play_url(self, room: RoomInfo) -> str:
                calls.append(("play", room.room_id))
                return "https://example.com/live.flv"

            def start_live_watch_session(self, room: RoomInfo, play_url: str) -> dict:
                calls.append(("start", room.room_id, play_url))
                return {"hbil": 50, "sid": "sid-1", "stky": "key-1"}

            def continue_live_watch_session(
                self,
                room: RoomInfo,
                play_url: str,
                qid: int,
                session_id: str,
                stky: str,
            ) -> dict:
                calls.append(("heartbeat", room.room_id, play_url, qid, session_id, stky))
                return {"hbil": 45, "sid": "sid-2", "stky": "key-2"}

        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _m: None)
        room = RoomInfo(room_id=23612045, live_status=1)
        state = live_watcher._start_heartbeat_session(FakeClient(), room, HeartbeatState())  # type: ignore[arg-type]
        state = live_watcher._continue_heartbeat_session(FakeClient(), room, state.qid, state)  # type: ignore[arg-type]

        self.assertEqual(state.interval, 60)
        self.assertEqual(state.qid, 2)
        self.assertEqual(state.legacy_sequence, 2)
        self.assertEqual(state.legacy_ets, 200)
        self.assertEqual(
            [call[0] for call in calls],
            ["entry", "legacy-start", "legacy-heartbeat"],
        )
        self.assertEqual(calls[-1][2], 1)


class SkynetSignerTest(unittest.TestCase):
    def test_python_wasm_bridge_matches_official_signer_fixture(self) -> None:
        payload = {
            "uid": 93693916,
            "buvid": "AUTO1234567890123456",
            "platform": "web",
            "room_id": 23612045,
            "play_url": "https://example.com/live.flv",
            "qid": 1,
            "sid": "sid-test",
            "cts": 1785680000000,
            "stky": "key-test",
            "screen_status": 50,
            "click_status": 60,
        }
        self.assertEqual(sign_live_watch_payload(payload), "a9bc0ec6e57b91192940708eae750d58")


if __name__ == "__main__":
    unittest.main()
