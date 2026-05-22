from __future__ import annotations

import unittest
from threading import Lock

from bili_drop_guard.bilibili import RoomInfo
from bili_drop_guard import watcher
from bili_drop_guard.watcher import LiveWatcher, WatchOptions


class RecordingWatcher(LiveWatcher):
    def __init__(self, options: WatchOptions) -> None:
        super().__init__(options, lambda _message: None)
        self.started_workers: list[int] = []
        self.started_workers_lock = Lock()

    def _heartbeat_watch_worker(self, worker_id: int, room: RoomInfo | None) -> None:
        with self.started_workers_lock:
            self.started_workers.append(worker_id)


class LiveWatcherTest(unittest.TestCase):
    def setUp(self) -> None:
        self._claim_submit_delay = watcher.CLAIM_SUBMIT_DELAY_SECONDS
        self._claim_rate_limit_delay = watcher.CLAIM_RATE_LIMIT_DELAY_SECONDS
        watcher.CLAIM_SUBMIT_DELAY_SECONDS = 0
        watcher.CLAIM_RATE_LIMIT_DELAY_SECONDS = 0

    def tearDown(self) -> None:
        watcher.CLAIM_SUBMIT_DELAY_SECONDS = self._claim_submit_delay
        watcher.CLAIM_RATE_LIMIT_DELAY_SECONDS = self._claim_rate_limit_delay

    def test_watch_threads_start_background_heartbeat_workers(self) -> None:
        live_watcher = RecordingWatcher(WatchOptions(cookie="a=b", room_id="1", watch_threads=3))

        live_watcher._start_watch_threads(RoomInfo(room_id=1, live_status=1))
        for thread in live_watcher._watch_threads:
            thread.join(timeout=2)

        self.assertEqual(live_watcher.started_workers, [1, 2, 3])

    def test_extract_heartbeat_state_keeps_fallback_values(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)

        state = live_watcher._extract_heartbeat_state(
            {"heartbeat_interval": 30, "timestamp": 123, "secret_key": "key", "secret_rule": [1, "2"]},
        )
        next_state = live_watcher._extract_heartbeat_state({}, fallback=state)

        self.assertEqual(next_state.interval, 30)
        self.assertEqual(next_state.ets, 123)
        self.assertEqual(next_state.secret_key, "key")
        self.assertEqual(next_state.secret_rule, [1, 2])

    def test_extract_web_heartbeat_interval_uses_next_interval(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)

        self.assertEqual(live_watcher._extract_web_heartbeat_interval({"next_interval": "45"}, 60), 45)
        self.assertEqual(live_watcher._extract_web_heartbeat_interval({}, 60), 60)
        self.assertEqual(live_watcher._extract_web_heartbeat_interval({"next_interval": "bad"}, 30), 30)

    def test_claim_worker_uses_single_sequential_path(self) -> None:
        calls: list[tuple[int, str | None]] = []

        class FakeClient:
            def __init__(self, cookie: str) -> None:
                self.cookie = cookie

            def get_user_task_progress(self, up_id: int, task_id: str | None = None) -> dict[str, object]:
                return {}

            def discover_live_activity_tasks(self, room_id: str) -> dict[str, object]:
                return {"tasks": []}

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
            waits: list[float] = []
            watcher.CLAIM_SUBMIT_DELAY_SECONDS = 7
            live_watcher._wait_between_claims = waits.append  # type: ignore[method-assign]

            live_watcher._claim_completed_worker()
        finally:
            watcher.BilibiliClient = original_client

        self.assertEqual(calls, [(100, "task-a"), (100, "task-b")])
        self.assertEqual(waits, [7])
        self.assertIn("开始领取奖励：会按顺序一个一个领取，避免太快导致失败", logs)

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
        self.assertEqual(result, "已领取：手动填写的任务")

    def test_activity_task_claim_uses_activity_mission_api(self) -> None:
        activity_calls: list[str] = []
        live_calls: list[tuple[int, str | None]] = []

        class FakeClient:
            def __init__(self, cookie: str) -> None:
                self.cookie = cookie

            def claim_activity_mission_reward(self, task_id: str) -> dict[str, object]:
                activity_calls.append(task_id)
                return {}

            def claim_user_task_rewards(self, up_id: int, task_id: str | None = None) -> dict[str, object]:
                live_calls.append((up_id, task_id))
                return {}

        original_client = watcher.BilibiliClient
        watcher.BilibiliClient = FakeClient
        try:
            live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
            live_watcher._activity_task_ids.add("activity-a")

            result = live_watcher._claim_one_task(100, "activity-a")
        finally:
            watcher.BilibiliClient = original_client

        self.assertEqual(activity_calls, ["activity-a"])
        self.assertEqual(live_calls, [])
        self.assertEqual(result, "已领取：活动任务")

    def test_activity_task_claim_retries_after_rate_limit(self) -> None:
        calls: list[str] = []
        waits: list[float] = []

        class FakeClient:
            def __init__(self, cookie: str) -> None:
                self.cookie = cookie

            def claim_activity_mission_reward(self, task_id: str) -> dict[str, object]:
                calls.append(task_id)
                if len(calls) == 1:
                    raise RuntimeError("请求频率过高，请稍后再试")
                return {}

        original_client = watcher.BilibiliClient
        watcher.BilibiliClient = FakeClient
        try:
            live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
            live_watcher._activity_task_ids.add("activity-a")
            live_watcher._wait_between_claims = waits.append  # type: ignore[method-assign]

            result = live_watcher._claim_one_task(100, "activity-a")
        finally:
            watcher.BilibiliClient = original_client

        self.assertEqual(calls, ["activity-a", "activity-a"])
        self.assertEqual(waits, [watcher.CLAIM_RATE_LIMIT_DELAY_SECONDS])
        self.assertEqual(result, "已领取：活动任务")

    def test_activity_task_claim_does_not_retry_non_rate_limit_error(self) -> None:
        calls: list[str] = []

        class FakeClient:
            def __init__(self, cookie: str) -> None:
                self.cookie = cookie

            def claim_activity_mission_reward(self, task_id: str) -> dict[str, object]:
                calls.append(task_id)
                raise RuntimeError("csrf 校验失败")

        original_client = watcher.BilibiliClient
        watcher.BilibiliClient = FakeClient
        try:
            live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
            live_watcher._activity_task_ids.add("activity-a")

            with self.assertRaisesRegex(RuntimeError, "csrf"):
                live_watcher._claim_one_task(100, "activity-a")
        finally:
            watcher.BilibiliClient = original_client

        self.assertEqual(calls, ["activity-a"])

    def test_activity_task_claim_raises_after_rate_limit_attempts(self) -> None:
        calls: list[str] = []

        class FakeClient:
            def __init__(self, cookie: str) -> None:
                self.cookie = cookie

            def claim_activity_mission_reward(self, task_id: str) -> dict[str, object]:
                calls.append(task_id)
                raise RuntimeError("请求频率过高，请稍后再试")

        original_client = watcher.BilibiliClient
        watcher.BilibiliClient = FakeClient
        try:
            live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
            live_watcher._activity_task_ids.add("activity-a")

            with self.assertRaisesRegex(RuntimeError, "请求频率过高"):
                live_watcher._claim_one_task(100, "activity-a")
        finally:
            watcher.BilibiliClient = original_client

        self.assertEqual(calls, ["activity-a"] * watcher.CLAIM_RATE_LIMIT_ATTEMPTS)

    def test_non_activity_task_claim_retries_after_rate_limit(self) -> None:
        calls: list[tuple[int, str | None]] = []

        class FakeClient:
            def __init__(self, cookie: str) -> None:
                self.cookie = cookie

            def claim_user_task_rewards(self, up_id: int, task_id: str | None = None) -> dict[str, object]:
                calls.append((up_id, task_id))
                if len(calls) == 1:
                    raise RuntimeError("请求频率过高，请稍后再试")
                return {}

        original_client = watcher.BilibiliClient
        watcher.BilibiliClient = FakeClient
        try:
            live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)

            result = live_watcher._claim_one_task(100, "task-a")
        finally:
            watcher.BilibiliClient = original_client

        self.assertEqual(calls, [(100, "task-a"), (100, "task-a")])
        self.assertEqual(result, "已领取：手动填写的任务")

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
        self.assertTrue(any("已自动找到任务列表，无需手动填写" in message for message in logs))

    def test_activity_task_progress_auto_discovers_ids_from_live_page(self) -> None:
        logs: list[str] = []
        calls: list[list[str]] = []

        class FakeClient:
            def discover_live_activity_tasks(self, room_id: str) -> dict[str, object]:
                return {
                    "tasks": [
                        {
                            "task_id": "activity-a",
                            "task_name": "观看 30 分钟",
                            "award_name": "奖励 A",
                            "group_label": "5月22日",
                            "current": 0,
                            "target": 30,
                        },
                        {
                            "task_id": "activity-b",
                            "task_name": "观看 60 分钟",
                            "award_name": "奖励 B",
                            "group_label": "5月23日",
                            "current": 0,
                            "target": 60,
                        },
                    ]
                }

            def get_activity_task_progress(self, task_ids: list[str]) -> dict[str, object]:
                calls.append(task_ids)
                return {
                    "list": [
                        {
                            "task_id": "activity-a",
                            "task_name": "观看 30 分钟",
                            "task_status": 2,
                            "indicators": [{"cur_value": 30, "limit": 30}],
                        },
                        {
                            "task_id": "activity-b",
                            "task_name": "观看 60 分钟",
                            "task_status": 1,
                            "indicators": [{"cur_value": 12, "limit": 60}],
                        },
                    ]
                }

        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="23612045", task_ids=["stale-id"]), logs.append)

        found_claimable = live_watcher._check_activity_task_progress(FakeClient())

        self.assertTrue(found_claimable)
        self.assertEqual(calls, [["activity-a", "activity-b"]])
        self.assertEqual(live_watcher._activity_task_ids, {"activity-a", "activity-b"})
        self.assertEqual(live_watcher._claimable_task_ids, {"activity-a"})
        self.assertEqual(live_watcher._activity_task_meta["activity-a"]["group_label"], "5月22日")
        self.assertTrue(any("5月22日｜观看 30 分钟｜奖励 A" in message for message in logs))
        self.assertTrue(any("已找到本次活动任务" in message for message in logs))

    def test_activity_totalv2_result_marks_queried_ids_as_activity_tasks(self) -> None:
        class FakeClient:
            def discover_live_activity_tasks(self, room_id: str) -> dict[str, object]:
                raise RuntimeError("活动页临时失败")

            def get_activity_task_progress(self, task_ids: list[str]) -> dict[str, object]:
                return {
                    "list": [
                        {
                            "task_id": "manual-activity",
                            "task_name": "观看 30 分钟",
                            "task_status": 2,
                            "indicators": [{"cur_value": 30, "limit": 30}],
                        }
                    ]
                }

        live_watcher = LiveWatcher(
            WatchOptions(cookie="a=b", room_id="23612045", task_ids=["manual-activity"]),
            lambda _message: None,
        )

        found_claimable = live_watcher._check_activity_task_progress(FakeClient())

        self.assertTrue(found_claimable)
        self.assertIn("manual-activity", live_watcher._activity_task_ids)
        self.assertIn("manual-activity", live_watcher._claimable_task_ids)

    def test_manual_claim_refreshes_activity_progress_even_when_live_task_api_fails(self) -> None:
        class FakeClient:
            def __init__(self, cookie: str) -> None:
                self.cookie = cookie

            def get_user_task_progress(self, up_id: int, task_id: str | None = None) -> dict[str, object]:
                raise RuntimeError("旧接口没有活动进度")

            def discover_live_activity_tasks(self, room_id: str) -> dict[str, object]:
                return {
                    "tasks": [
                        {"task_id": "activity-a", "task_name": "观看 30 分钟", "current": 0, "target": 30},
                    ]
                }

            def get_activity_task_progress(self, task_ids: list[str]) -> dict[str, object]:
                return {
                    "list": [
                        {
                            "task_id": "activity-a",
                            "task_name": "观看 30 分钟",
                            "task_status": 2,
                            "indicators": [{"cur_value": 30, "limit": 30}],
                        }
                    ]
                }

        original_client = watcher.BilibiliClient
        watcher.BilibiliClient = FakeClient
        try:
            live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="23612045"), lambda _message: None)
            live_watcher._refresh_claimable_tasks(100)
        finally:
            watcher.BilibiliClient = original_client

        self.assertEqual(live_watcher._claimable_task_ids, {"activity-a"})

    def test_activity_discovery_replaces_stale_task_ids_on_page_update(self) -> None:
        class FakeClient:
            def discover_live_activity_tasks(self, room_id: str) -> dict[str, object]:
                return {
                    "tasks": [
                        {
                            "task_id": "new-task",
                            "task_name": "观看 60 分钟",
                            "group_label": "5月25日",
                            "current": 0,
                            "target": 60,
                        }
                    ]
                }

        logs: list[str] = []
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="23612045"), logs.append)
        live_watcher._activity_task_ids.update({"old-task"})
        live_watcher._activity_task_meta["old-task"] = {"group_label": "5月22日"}
        live_watcher._claimable_task_ids.add("old-task")

        task_ids = live_watcher._discover_activity_task_ids(FakeClient(), announce_progress=False)

        self.assertEqual(task_ids, ["new-task"])
        self.assertEqual(live_watcher._activity_task_ids, {"new-task"})
        self.assertEqual(live_watcher._claimable_task_ids, set())
        self.assertEqual(live_watcher._activity_task_meta["new-task"]["group_label"], "5月25日")
        self.assertTrue(any("活动任务已更新" in message for message in logs))

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

    def test_activity_totalv2_status_two_claimable_three_received(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        progress = {
            "list": [
                {"task_id": "activity-a", "task_name": "观看 30 分钟", "task_status": 2, "indicators": [{"cur_value": 30, "limit": 30}]},
                {"task_id": "activity-b", "task_name": "观看 60 分钟", "task_status": 3, "indicators": [{"cur_value": 60, "limit": 60}]},
            ]
        }

        claimable = live_watcher._find_claimable_task_refs(progress)

        self.assertEqual(claimable, [("观看 30 分钟", "activity-a")])

    def test_task_summary_uses_user_friendly_remaining_and_claim_status(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        progress = {
            "list": [
                {
                    "task_id": "activity-a",
                    "task_name": "观看守望先锋电竞直播间60分钟",
                    "award_name": "电竞补给",
                    "group_label": "5月22日",
                    "task_status": 1,
                    "indicators": [{"cur_value": 12, "limit": 60}],
                },
                {
                    "task_id": "activity-b",
                    "task_name": "观看守望先锋电竞直播间90分钟",
                    "award_name": "观赛派对",
                    "group_label": "5月22日",
                    "task_status": 2,
                    "indicators": [{"cur_value": 90, "limit": 90}],
                },
                {
                    "task_id": "activity-c",
                    "task_name": "观看守望先锋电竞直播间30分钟",
                    "award_name": "头像",
                    "group_label": "5月22日",
                    "task_status": 3,
                    "indicators": [{"cur_value": 30, "limit": 30}],
                },
            ]
        }

        summary = live_watcher._summarize_task(progress)

        self.assertIn("还差 48 分钟", summary)
        self.assertIn("已完成，待领取", summary)
        self.assertIn("已领取", summary)
        self.assertNotIn("状态=", summary)

    def test_task_summary_focuses_current_activity_group(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        progress = {
            "list": [
                {
                    "task_id": "day-a-30",
                    "task_name": "观看守望先锋电竞直播间30分钟",
                    "award_name": "第一天奖励",
                    "group_label": "5月22日",
                    "group_index": 0,
                    "task_status": 1,
                    "indicators": [{"cur_value": 0, "limit": 30}],
                },
                {
                    "task_id": "day-b-30",
                    "task_name": "观看守望先锋电竞直播间30分钟",
                    "award_name": "第二天奖励",
                    "group_label": "5月23日",
                    "group_index": 1,
                    "task_status": 1,
                    "indicators": [{"cur_value": 0, "limit": 30}],
                },
            ]
        }

        summary = live_watcher._summarize_task(progress)

        self.assertIn("当前可挂：5月22日", summary)
        self.assertIn("第一天奖励", summary)
        self.assertNotIn("第二天奖励", summary)

    def test_task_summary_skips_empty_placeholder_task(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        progress = {"task": {"name": "任务", "current": 0, "target": 0}}

        summary = live_watcher._summarize_task(progress)

        self.assertEqual(summary, "")

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
