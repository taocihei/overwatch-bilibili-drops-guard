from __future__ import annotations

import json
import unittest
from typing import Any

from bili_drop_guard.bilibili import BilibiliClient, RoomInfo, calc_heartbeat_sign


class FakeResponse:
    status_code = 200
    text = ""
    headers = {"Content-Type": "application/json"}

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._payload = {"code": 0, "data": data or {}}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class RecordingSession:
    def __init__(self, response_data: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.response_data = response_data or {}

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("POST", url, kwargs))
        return FakeResponse(self.response_data)

    def close(self) -> None:
        return None


class X25KnProtocolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = BilibiliClient(
            "SESSDATA=session; bili_jct=csrf-value; buvid3=browser-buvid",
            session_buvid="AUTO1234567890123456",
            session_device_uuid="12345678-1234-1234-1234-123456789abc",
        )
        self.addCleanup(self.client.close)
        self.client.session.close()
        self.session = RecordingSession(
            {
                "heartbeat_interval": 60,
                "timestamp": 123,
                "secret_key": "secret",
                "secret_rule": [0],
            }
        )
        self.client.session = self.session  # type: ignore[assignment]
        self.client._wbi_signed_params = (  # type: ignore[method-assign]
            lambda params: {**params, "wts": "100", "w_rid": "signed"}
        )
        self.room = RoomInfo(
            room_id=23612045,
            live_status=1,
            anchor_uid=365902357,
            parent_area_id=13,
            area_id=561,
        )

    def test_enter_uses_empty_post_body_and_complete_wbi_query(self) -> None:
        self.client.enter_room_heartbeat(self.room)

        method, url, kwargs = self.session.calls[-1]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/x25Kn/E"))
        self.assertNotIn("data", kwargs)
        self.assertNotIn("json", kwargs)
        params = kwargs["params"]
        self.assertEqual(params["ruid"], 365902357)
        self.assertEqual(params["web_location"], "444.8")
        self.assertEqual(params["csrf"], "csrf-value")
        self.assertEqual(params["wts"], "100")
        self.assertEqual(params["w_rid"], "signed")
        self.assertEqual(
            json.loads(params["device"]),
            ["AUTO1234567890123456", "12345678-1234-1234-1234-123456789abc"],
        )
        self.assertIn("/blanc/23612045?liteVersion=true", kwargs["headers"]["Referer"])

    def test_continue_includes_ruid_trackid_web_location_and_signatures(self) -> None:
        self.client.in_room_heartbeat(
            self.room,
            sequence=2,
            interval=60,
            ets=123,
            secret_key="secret",
            secret_rule=[0],
        )

        _, url, kwargs = self.session.calls[-1]
        self.assertTrue(url.endswith("/x25Kn/X"))
        self.assertNotIn("data", kwargs)
        params = kwargs["params"]
        self.assertEqual(params["ruid"], 365902357)
        self.assertEqual(params["trackid"], -99998)
        self.assertEqual(params["web_location"], "444.8")
        self.assertEqual(params["csrf"], "csrf-value")
        expected_unsigned = {
            key: value
            for key, value in params.items()
            if key not in {"s", "wts", "w_rid"}
        }
        self.assertEqual(
            params["s"],
            calc_heartbeat_sign(expected_unsigned, [0]),
        )

    def test_room_entry_uses_json_body_and_signed_csrf_query(self) -> None:
        self.client.room_entry_action(self.room)

        _, url, kwargs = self.session.calls[-1]
        self.assertTrue(url.endswith("/roomEntryAction"))
        self.assertEqual(kwargs["json"], {"room_id": 23612045, "platform": "pc"})
        self.assertEqual(
            kwargs["params"],
            {"csrf": "csrf-value", "wts": "100", "w_rid": "signed"},
        )


if __name__ == "__main__":
    unittest.main()
