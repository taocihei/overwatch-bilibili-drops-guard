from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import requests

import bili_drop_guard.bilibili as bilibili_module
from bili_drop_guard.bilibili import (
    BilibiliClient,
    _activity_period_from_panel,
    _decode_json_response,
    _extract_json_assignments,
    _extract_tab_labels,
    _group_label_for_index,
    _iter_nested_dicts,
    _normalize_activity_task_progress,
    _response_server_timestamp,
    normalize_room_id,
)


class BilibiliRoomTest(unittest.TestCase):
    def test_nested_dict_iterator_handles_deep_activity_state(self) -> None:
        root: dict[str, object] = {}
        cursor = root
        for index in range(2_000):
            child: dict[str, object] = {"index": index}
            cursor["child"] = child
            cursor = child

        nodes = list(_iter_nested_dicts(root))

        self.assertEqual(len(nodes), 2_001)
        self.assertIs(nodes[0], root)
        self.assertEqual(nodes[-1]["index"], 1_999)

    def test_nested_dict_iterator_ignores_container_cycles(self) -> None:
        root: dict[str, object] = {}
        root["self"] = root

        self.assertEqual(list(_iter_nested_dicts(root)), [root])

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

    def test_response_server_timestamp_reads_http_date(self) -> None:
        response = requests.Response()
        response.headers["Date"] = "Thu, 30 Jul 2026 16:53:00 GMT"

        server_time = _response_server_timestamp(response)

        self.assertEqual(
            server_time,
            datetime(2026, 7, 30, 16, 53, tzinfo=timezone.utc).timestamp(),
        )

    def test_wbi_keys_are_shared_between_clients(self) -> None:
        bilibili_module._WBI_KEY_CACHE = None
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = "application/json"
        response._content = (
            b'{"code":0,"data":{"wbi_img":{'
            b'"img_url":"https://i0.hdslb.com/bfs/wbi/img_key.png",'
            b'"sub_url":"https://i0.hdslb.com/bfs/wbi/sub_key.png"}}}'
        )
        calls = {"count": 0}

        def fake_get(*_args, **_kwargs):
            calls["count"] += 1
            return response

        first = BilibiliClient("")
        second = BilibiliClient("")
        first.session.get = fake_get  # type: ignore[method-assign]
        second.session.get = fake_get  # type: ignore[method-assign]

        self.assertEqual(first._get_wbi_keys(), ("img_key", "sub_key"))
        self.assertEqual(second._get_wbi_keys(), ("img_key", "sub_key"))
        self.assertEqual(calls["count"], 1)
        bilibili_module._WBI_KEY_CACHE = None

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

    def test_extract_json_assignments_ignores_wrapped_object_arguments(self) -> None:
        html = """
        window.__initialState=Object.assign({}, source);
        window.__initialState=Promise.resolve({taskId:"not-direct"});
        """

        self.assertEqual(_extract_json_assignments(html, "window.__initialState"), [])

    def test_activity_period_does_not_leak_from_nested_panel(self) -> None:
        panel = {
            "name": "EvaTabs.Panel",
            "slots": [
                {
                    "children": [
                        {
                            "name": "EvaText",
                            "props": {
                                "content": "赛程说明：2026年7月1日 10:00-2026年7月2日 10:00",
                            },
                        },
                        {
                            "name": "EvaTabs.Panel",
                            "slots": [
                                {
                                    "children": [
                                        {
                                            "name": "EvaText",
                                            "props": {
                                                "content": (
                                                    "活动任务有效统计时间："
                                                    "2026年7月30日 17:30-2026年7月31日 17:00"
                                                ),
                                            },
                                        }
                                    ]
                                }
                            ],
                        },
                    ]
                }
            ],
        }

        self.assertEqual(_activity_period_from_panel(panel), {})

    def test_discover_live_activity_tasks_reads_eva_page_data(self) -> None:
        html = """
        <script>
        window.__initialState = {"BaseInfo":{"title":"小的页面状态"}};
        window.__BILIACT_PAGEINFO__ = {"activity_id":"activity-a"};
        window.__BILIACT_EVAPAGEDATA__ = {
            "layerTree": [
                {
                    "type": "Component",
                    "name": "EvaPage",
                    "slots": [
                        {
                            "children": [
                                {
                                    "type": "Component",
                                    "name": "EraTasklistPc",
                                    "props": {
                                        "tasklist": [
                                            {
                                                "taskId": "task-30",
                                                "taskName": "观看守望先锋电竞直播间30分钟",
                                                "awardName": "战令等级直升",
                                                "taskStatus": 1,
                                                "counter": "counter-a",
                                                "indicators": [{"cur_value": 12, "limit": 30}]
                                            }
                                        ]
                                    },
                                    "slots": []
                                }
                            ]
                        }
                    ]
                }
            ]
        };
        </script>
        """
        response = requests.Response()
        response.status_code = 200
        response.url = "https://live.bilibili.com/23612045"
        response._content = html.encode("utf-8")
        client = BilibiliClient("")
        client.session.get = lambda *_args, **_kwargs: response  # type: ignore[method-assign]

        result = client.discover_live_activity_tasks("23612045")

        self.assertEqual(len(result["tasks"]), 1)
        task = result["tasks"][0]
        self.assertEqual(task["task_id"], "task-30")
        self.assertEqual(task["task_name"], "观看守望先锋电竞直播间30分钟")
        self.assertEqual(task["award_name"], "战令等级直升")
        self.assertEqual(task["current"], 12)
        self.assertEqual(task["target"], 30)

    def test_discover_live_activity_tasks_keeps_old_initial_state_shape(self) -> None:
        html = """
        <script>
        window.__initialState = {
            "EvaTabs.Panel": [
                {"tabItem": {"tabItemProps": {"textContent": {"content": "6月6日"}}}}
            ],
            "EraTasklistPc": [
                {
                    "tasklist": [
                        {
                            "taskId": "task-60",
                            "taskName": "观看守望先锋电竞直播间60分钟",
                            "awardName": "电竞补给",
                            "checkpoints": [{"alias": "观看 60 分钟", "awardname": "电竞补给", "list": [{"cur_value": 60, "limit": 60}]}]
                        }
                    ]
                }
            ]
        };
        window.__BILIACT_PAGEINFO__ = {"activity_id":"activity-a"};
        </script>
        """
        response = requests.Response()
        response.status_code = 200
        response.url = "https://live.bilibili.com/23612045"
        response._content = html.encode("utf-8")
        client = BilibiliClient("")
        client.session.get = lambda *_args, **_kwargs: response  # type: ignore[method-assign]

        result = client.discover_live_activity_tasks("23612045")

        self.assertEqual(result["tasks"][0]["task_id"], "task-60")
        self.assertEqual(result["tasks"][0]["group_label"], "6月6日")
        self.assertEqual(result["tasks"][0]["target"], 60)

    def test_discover_live_activity_tasks_flattens_new_checkpoint_template(self) -> None:
        html = """
        <script>
        window.__BILIACT_EVAPAGEDATA__ = {
            "layerTree": [{
                "name": "EvaTabs.Panel",
                "props": {
                    "tabItem": {
                        "tabItemProps": {
                            "textContent": {"content": "DAY1观赛奖励"}
                        }
                    }
                },
                "slots": [{"children": [{
                    "name": "EraTasklistPc",
                    "props": {
                        "tasklist": [{
                            "taskId": "parent-day-1",
                            "taskName": "7月29日 观看守望先锋电竞直播间",
                            "checkpoints": [
                                {
                                    "alias": "观看直播60分钟",
                                    "awardname": "头像",
                                    "awardsid": "award-60",
                                    "ztasksid": "claim-60",
                                    "status": 3,
                                    "list": [{"cur_value": 60, "limit": 60}]
                                },
                                {
                                    "alias": "观看直播120分钟",
                                    "awardname": "战令等级直升",
                                    "awardsid": "award-120",
                                    "ztasksid": "claim-120",
                                    "status": 2,
                                    "list": [{"cur_value": 120, "limit": 120}]
                                }
                            ]
                        }]
                    }
                }]}]
            }]
        };
        </script>
        """
        response = requests.Response()
        response.status_code = 200
        response.url = "https://live.bilibili.com/23612045"
        response._content = html.encode("utf-8")
        client = BilibiliClient("")
        client.session.get = lambda *_args, **_kwargs: response  # type: ignore[method-assign]

        result = client.discover_live_activity_tasks("23612045")

        self.assertEqual(result["tracking_task_ids"], ["parent-day-1"])
        self.assertEqual([task["task_id"] for task in result["tasks"]], ["claim-60", "claim-120"])
        self.assertEqual(result["tasks"][0]["parent_task_id"], "parent-day-1")
        self.assertEqual(result["tasks"][0]["group_label"], "7月29日")
        self.assertEqual(result["tasks"][0]["award_sid"], "award-60")
        self.assertEqual(result["tasks"][1]["award_name"], "战令等级直升")
        self.assertEqual(result["tasks"][1]["target"], 120)
        self.assertEqual(result["tasks"][1]["task_status"], 2)

    def test_discover_live_activity_tasks_accepts_javascript_object_literal(self) -> None:
        html = """
        <script>
        window.__initialState={BaseInfo:{title:'B站直播OW'}},
        window.__BILIACT_EVAPAGEDATA__={
            layerTree:[{
                name:"EvaTabs.Panel",
                props:{
                    tabItem:{
                        tabItemProps:{textContent:{content:"DAY2观赛奖励"}}
                    }
                },
                slots:[{children:[{
                    name:"EvaText",
                    props:{
                        content:"Day2活动任务有效统计时间：2026年7月30日 17:30-2026年7月31日 17:00"
                    }
                },{
                    fakeHiddenInLayer:!1,
                    hiddenInLayer:!1,
                    name:"EraTasklistPc",
                    props:{
                        tasklist:[{
                            taskId:"parent-day-2",
                            taskName:"7月30日 观看 !0 守望先锋电竞直播间",
                            showBtn:!0,
                            checkpoints:[{
                                alias:"观看 !0 直播60分钟",
                                awardname:'电竞补给',
                                ztasksid:"claim-day-2-60",
                                status:1,
                                list:[{cur_value:0,limit:60}]
                            }]
                        }]
                    }
                }]}]
            }]
        };
        </script>
        """
        response = requests.Response()
        response.status_code = 200
        response.url = "https://live.bilibili.com/23612045"
        response.headers["Date"] = "Thu, 30 Jul 2026 16:53:00 GMT"
        response._content = html.encode("utf-8")
        client = BilibiliClient("")
        client.session.get = lambda *_args, **_kwargs: response  # type: ignore[method-assign]

        result = client.discover_live_activity_tasks("23612045")

        self.assertEqual(result["tracking_task_ids"], ["parent-day-2"])
        self.assertEqual(result["tasks"][0]["task_id"], "claim-day-2-60")
        self.assertEqual(result["tasks"][0]["award_name"], "电竞补给")
        self.assertIn("!0", result["tasks"][0]["task_name"])
        self.assertEqual(result["tasks"][0]["group_label"], "7月30日")
        self.assertEqual(result["tasks"][0]["target"], 60)
        self.assertEqual(
            result["server_time"],
            datetime(2026, 7, 30, 16, 53, tzinfo=timezone.utc).timestamp(),
        )
        china_timezone = timezone(timedelta(hours=8))
        self.assertEqual(
            result["tasks"][0]["active_start"],
            int(datetime(2026, 7, 30, 17, 30, tzinfo=china_timezone).timestamp()),
        )
        self.assertEqual(
            result["tasks"][0]["active_end"],
            int(datetime(2026, 7, 31, 17, 0, tzinfo=china_timezone).timestamp()),
        )

    def test_normalize_totalv2_uses_parent_for_tracking_and_sid_for_claim(self) -> None:
        progress = {
            "list": [
                {
                    "task_id": "parent-day-1",
                    "task_name": "7月29日 观看守望先锋电竞直播间",
                    "statistic_type": 1,
                    "check_points": [
                        {
                            "alias": "观看直播60分钟",
                            "award_name": "头像",
                            "award_sid": "award-60",
                            "sid": "claim-60",
                            "status": 3,
                            "list": [{"cur_value": 60, "limit": 60}],
                        },
                        {
                            "alias": "观看直播120分钟",
                            "award_name": "战令等级直升",
                            "award_sid": "award-120",
                            "sid": "claim-120",
                            "status": 2,
                            "list": [{"cur_value": 120, "limit": 120}],
                        },
                    ],
                }
            ]
        }

        result = _normalize_activity_task_progress(progress, ["parent-day-1"])

        self.assertEqual(result["tracking_task_ids"], ["parent-day-1"])
        self.assertEqual([task["task_id"] for task in result["list"]], ["claim-60", "claim-120"])
        self.assertEqual(result["list"][0]["task_status"], 3)
        self.assertEqual(result["list"][1]["task_status"], 2)
        self.assertEqual(result["list"][1]["group_label"], "7月29日")

    def test_normalize_totalv2_prefers_parent_indicator_current_minutes(self) -> None:
        progress = {
            "list": [{
                "task_id": "parent-day-1",
                "task_name": "7月31日 观看守望先锋电竞直播间",
                "statistic_type": 1,
                "indicators": [{"cur_value": 84, "limit": 300}],
                "check_points": [
                    {
                        "alias": "观看直播60分钟",
                        "sid": "claim-60",
                        "status": 3,
                        "list": [{"cur_value": 60, "limit": 60}],
                    },
                    {
                        "alias": "观看直播120分钟",
                        "sid": "claim-120",
                        "status": 1,
                        "list": [{"cur_value": 60, "limit": 120}],
                    },
                ],
            }]
        }

        result = _normalize_activity_task_progress(progress, ["parent-day-1"])

        self.assertEqual([task["current"] for task in result["list"]], [84, 84])
        self.assertEqual([task["target"] for task in result["list"]], [60, 120])

    def test_normalize_totalv2_keeps_legacy_direct_single_checkpoint_parent_id(self) -> None:
        progress = {
            "list": [
                {
                    "task_id": "legacy-parent",
                    "task_name": "旧版直接任务",
                    "task_type": 1,
                    "statistic_type": 1,
                    "task_status": 2,
                    "checkpoints": [],
                    "check_points": [
                        {
                            "alias": "完成旧版任务",
                            "sid": "legacy-child",
                            "status": 2,
                            "list": [{"cur_value": 1, "limit": 1}],
                        }
                    ],
                }
            ]
        }

        result = _normalize_activity_task_progress(progress, ["legacy-parent"])

        self.assertEqual(result["tracking_task_ids"], ["legacy-parent"])
        self.assertEqual([task["task_id"] for task in result["list"]], ["legacy-parent"])

    def test_normalize_totalv2_retains_all_queried_parents_on_partial_response(self) -> None:
        progress = {
            "list": [
                {
                    "task_id": "parent-a",
                    "task_type": 2,
                    "statistic_type": 1,
                    "check_points": [
                        {
                            "sid": "claim-a",
                            "status": 1,
                            "list": [{"cur_value": 0, "limit": 60}],
                        }
                    ],
                }
            ]
        }

        result = _normalize_activity_task_progress(progress, ["parent-a", "parent-b"])

        self.assertEqual(result["tracking_task_ids"], ["parent-a", "parent-b"])
        self.assertEqual([task["task_id"] for task in result["list"]], ["claim-a"])

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
