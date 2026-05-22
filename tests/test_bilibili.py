from __future__ import annotations

import unittest

import requests

from bili_drop_guard.bilibili import BilibiliClient, _decode_json_response, _extract_tab_labels, _group_label_for_index, normalize_room_id


class BilibiliRoomTest(unittest.TestCase):
    def test_normalize_room_id_accepts_number(self) -> None:
        self.assertEqual(normalize_room_id(" 123456 "), "123456")

    def test_normalize_room_id_accepts_live_url(self) -> None:
        self.assertEqual(normalize_room_id("https://live.bilibili.com/123456?spm_id_from=333"), "123456")

    def test_normalize_room_id_accepts_default_overwatch_url(self) -> None:
        url = "https://live.bilibili.com/23612045?live_from=82002&spm_id_from=333.788.top_right_bar_window_dynamic.content.click"

        self.assertEqual(normalize_room_id(url), "23612045")

    def test_normalize_room_id_accepts_blanc_url(self) -> None:
        self.assertEqual(normalize_room_id("https://live.bilibili.com/blanc/123456"), "123456")

    def test_normalize_room_id_rejects_unrelated_text(self) -> None:
        self.assertEqual(normalize_room_id("房间：123456"), "")

    def test_decode_json_response_reports_html_response(self) -> None:
        response = requests.Response()
        response.status_code = 200
        response._content = b"<html>blocked</html>"
        response.headers["Content-Type"] = "text/html"

        with self.assertRaisesRegex(RuntimeError, "未返回 JSON"):
            _decode_json_response(response)

    def test_decode_json_response_reports_http_status(self) -> None:
        response = requests.Response()
        response.status_code = 412
        response.url = "https://api.bilibili.com/test"
        response._content = "风控".encode("utf-8")
        response.headers["Content-Type"] = "text/plain"

        with self.assertRaisesRegex(RuntimeError, "HTTP 412"):
            _decode_json_response(response)

    def test_extract_tab_labels_and_maps_extra_groups_to_last_date(self) -> None:
        state = {
            "EvaTabs.Panel": [
                {"tabItem": {"tabItemProps": {"textContent": {"content": "5月22日"}}}},
                {"tabItem": {"tabItemProps": {"textContent": {"content": "5月23日"}}}},
                {"tabItem": {"tabItemProps": {"textContent": {"content": "5月24日"}}}},
            ]
        }

        labels = _extract_tab_labels(state)

        self.assertEqual(labels, ["5月22日", "5月23日", "5月24日"])
        self.assertEqual(_group_label_for_index(labels, 0), "5月22日")
        self.assertEqual(_group_label_for_index(labels, 1), "5月23日")
        self.assertEqual(_group_label_for_index(labels, 2), "5月24日")
        self.assertEqual(_group_label_for_index(labels, 3), "5月24日")

    def test_activity_mission_claim_payload_includes_csrf(self) -> None:
        client = BilibiliClient("SESSDATA=abc; bili_jct=csrf-token")
        captured: dict[str, object] = {}

        def fake_info(task_id: str) -> dict[str, object]:
            return {
                "act_id": "activity-id",
                "act_name": "活动",
                "task_name": "观看 60 分钟",
                "reward_info": {"award_name": "电竞补给"},
            }

        def fake_post(url: str, room_id: int, data: dict[str, object], params: dict[str, object] | None = None) -> dict[str, object]:
            captured["url"] = url
            captured["room_id"] = room_id
            captured["data"] = data
            captured["params"] = params
            return {}

        client.get_activity_mission_info = fake_info  # type: ignore[method-assign]
        client._wbi_signed_params = lambda params: {"signed": "1", **params}  # type: ignore[method-assign]
        client._post_form = fake_post  # type: ignore[method-assign]

        client.claim_activity_mission_reward("task-a")

        data = captured["data"]
        self.assertIsInstance(data, dict)
        self.assertEqual(data["csrf"], "csrf-token")
        self.assertEqual(data["csrf_token"], "csrf-token")
        self.assertEqual(data["task_id"], "task-a")

    def test_activity_mission_claim_requires_bili_jct(self) -> None:
        client = BilibiliClient("SESSDATA=abc")

        with self.assertRaisesRegex(RuntimeError, "bili_jct"):
            client.claim_activity_mission_reward("task-a")

    def test_user_task_claim_requires_bili_jct(self) -> None:
        client = BilibiliClient("SESSDATA=abc")

        with self.assertRaisesRegex(RuntimeError, "bili_jct"):
            client.claim_user_task_rewards(100, "task-a")

    def test_user_task_claim_stops_on_csrf_error(self) -> None:
        client = BilibiliClient("SESSDATA=abc; bili_jct=csrf-token")
        calls: list[dict[str, object]] = []

        def fake_post(url: str, room_id: int, data: dict[str, object], params: dict[str, object] | None = None) -> dict[str, object]:
            calls.append(data)
            raise RuntimeError("csrf 校验失败")

        client._post_form = fake_post  # type: ignore[method-assign]

        with self.assertRaisesRegex(RuntimeError, "csrf"):
            client.claim_user_task_rewards(100, "task-a")

        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
