from __future__ import annotations

import unittest
from datetime import date, timedelta
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

    def test_watch_status_summary_reports_all_background_workers(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1", watch_threads=20), lambda _message: None)
        live_watcher._watch_worker_count = 20
        for worker_id in range(1, 21):
            live_watcher._set_watch_status(worker_id, "正常", interval=60)

        summary, normal_count, problem_count = live_watcher._watch_status_summary_info()

        self.assertEqual(normal_count, 20)
        self.assertEqual(problem_count, 0)
        self.assertIn("20/20 正常", summary)
        self.assertIn("下一次约 60 秒后", summary)

    def test_watch_threads_allow_one_hundred_workers(self) -> None:
        live_watcher = RecordingWatcher(WatchOptions(cookie="a=b", room_id="1", watch_threads=100))

        live_watcher._start_watch_threads(RoomInfo(room_id=1, live_status=1))
        for thread in live_watcher._watch_threads:
            thread.join(timeout=2)

        self.assertEqual(len(live_watcher.started_workers), 100)
        self.assertEqual(live_watcher.started_workers[0], 1)
        self.assertEqual(live_watcher.started_workers[-1], 100)

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

    def test_live_watch_uses_enter_and_in_room_heartbeat(self) -> None:
        calls: list[tuple[str, int]] = []

        class FakeClient:
            def enter_room_heartbeat(self, room: RoomInfo) -> dict[str, object]:
                calls.append(("E", room.room_id))
                return {"heartbeat_interval": 30, "timestamp": 100, "secret_key": "secret", "secret_rule": [0]}

            def in_room_heartbeat(
                self,
                room: RoomInfo,
                sequence: int,
                interval: int,
                ets: int,
                secret_key: str,
                secret_rule: list[int],
            ) -> dict[str, object]:
                calls.append((f"X{sequence}:{interval}:{ets}:{secret_key}:{secret_rule[0]}", room.room_id))
                return {"heartbeat_interval": 45, "timestamp": 200}

        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        room = RoomInfo(room_id=23612045, live_status=1)

        state = live_watcher._start_heartbeat_session(FakeClient(), room, watcher.HeartbeatState())
        next_state = live_watcher._continue_heartbeat_session(FakeClient(), room, 1, state)

        self.assertEqual(calls, [("E", 23612045), ("X1:30:100:secret:0", 23612045)])
        self.assertEqual(next_state.interval, 45)
        self.assertEqual(next_state.ets, 200)
        self.assertEqual(next_state.secret_key, "secret")

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

    def test_claim_treats_already_claimed_as_success(self) -> None:
        # B 站对已领取的奖励返回“任务奖励已经领取”(code 202031)。这不是失败：
        # 奖励已经到手，应当当成成功、标记已领、并移出待领队列，避免反复重试和误报失败。
        calls: list[str] = []

        class FakeClient:
            def __init__(self, cookie: str) -> None:
                self.cookie = cookie

            def claim_activity_mission_reward(self, task_id: str) -> dict[str, object]:
                calls.append(task_id)
                raise RuntimeError("任务奖励已经领取")

        original_client = watcher.BilibiliClient
        watcher.BilibiliClient = FakeClient
        try:
            live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
            live_watcher._activity_task_ids.add("activity-a")
            live_watcher._claimable_task_ids.add("activity-a")

            result = live_watcher._claim_one_task(100, "activity-a")
        finally:
            watcher.BilibiliClient = original_client

        self.assertEqual(calls, ["activity-a"])  # 不重试
        self.assertIn("已领取", result)
        self.assertNotIn("失败", result)
        self.assertNotIn("activity-a", live_watcher._claimable_task_ids)
        self.assertIn("100:activity-a", live_watcher._claimed_markers)

    def test_already_claimed_error_matches_only_specific_messages(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        self.assertTrue(live_watcher._is_already_claimed_error(RuntimeError("任务奖励已经领取")))
        self.assertTrue(live_watcher._is_already_claimed_error(RuntimeError("请勿重复领取")))
        # 含“已领取”但语义是失败/未领取的消息，不应被当成已领取成功
        self.assertFalse(live_watcher._is_already_claimed_error(RuntimeError("请确认任务是否已领取")))
        self.assertFalse(live_watcher._is_already_claimed_error(RuntimeError("网络超时")))

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
        today = date.today()
        yesterday = today - timedelta(days=1)
        today_label = f"{today.month}月{today.day}日"
        yesterday_label = f"{yesterday.month}月{yesterday.day}日"

        class FakeClient:
            def discover_live_activity_tasks(self, room_id: str) -> dict[str, object]:
                return {
                    "tasks": [
                        {
                            "task_id": "activity-a",
                            "task_name": "观看 30 分钟",
                            "award_name": "奖励 A",
                            "group_label": yesterday_label,
                            "current": 0,
                            "target": 30,
                        },
                        {
                            "task_id": "activity-b",
                            "task_name": "观看 60 分钟",
                            "award_name": "奖励 B",
                            "group_label": today_label,
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
        self.assertEqual(live_watcher._activity_task_meta["activity-a"]["group_label"], yesterday_label)
        self.assertTrue(any(f"{today_label}｜观看 60 分钟｜奖励 B" in message for message in logs))
        self.assertFalse(any(f"{yesterday_label}｜观看 30 分钟｜奖励 A" in message for message in logs))
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

    def test_activity_progress_falls_back_to_mission_info_when_totalv2_empty(self) -> None:
        logs: list[str] = []

        class FakeClient:
            def discover_live_activity_tasks(self, room_id: str) -> dict[str, object]:
                return {
                    "tasks": [
                        {"task_id": "activity-a", "task_name": "观看 300 分钟", "award_name": "补给", "current": 0, "target": 300},
                    ]
                }

            def get_activity_task_progress(self, task_ids: list[str]) -> dict[str, object]:
                return {"list": []}

            def get_activity_mission_progress(self, task_ids: list[str]) -> dict[str, object]:
                return {
                    "tasks": [
                        {
                            "task_id": "activity-a",
                            "task_name": "观看 300 分钟",
                            "award_name": "补给",
                            "cur_value": 257,
                            "limit": 300,
                            "task_status": 1,
                        }
                    ]
                }

        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="23612045"), logs.append)

        found_claimable = live_watcher._check_activity_task_progress(FakeClient())

        self.assertFalse(found_claimable)
        self.assertTrue(any("257/300 分钟" in message for message in logs))
        self.assertFalse(any("暂未返回可显示" in message for message in logs))

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
        self.assertIn("✓ 待领取", summary)
        self.assertIn("观看守望先锋电竞直播间（当前：90 分钟）", summary)
        self.assertNotIn("30 分钟  ✓ 已领取", summary)
        self.assertNotIn("状态=", summary)

    def test_task_summary_compacts_watch_steps_like_user_progress(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        progress = {
            "list": [
                {
                    "task_id": "activity-30",
                    "task_name": "观看守望先锋电竞直播间30分钟",
                    "group_label": "5月23日",
                    "task_status": 3,
                    "indicators": [{"cur_value": 30, "limit": 30}],
                },
                {
                    "task_id": "activity-60",
                    "task_name": "观看守望先锋电竞直播间60分钟",
                    "group_label": "5月23日",
                    "task_status": 2,
                    "indicators": [{"cur_value": 60, "limit": 60}],
                },
                {
                    "task_id": "activity-90",
                    "task_name": "观看守望先锋电竞直播间90分钟",
                    "group_label": "5月23日",
                    "task_status": 1,
                    "indicators": [{"cur_value": 42, "limit": 90}],
                },
            ]
        }

        summary = live_watcher._summarize_task(progress)

        self.assertIn("观看守望先锋电竞直播间（当前：60 分钟）", summary)
        self.assertNotIn("30 分钟  ✓ 已领取", summary)
        self.assertIn("60 分钟  ✓ 待领取", summary)
        self.assertIn("90 分钟  还差 48 分钟", summary)
        self.assertNotIn("观看守望先锋电竞直播间30分钟：", summary)

    def test_task_summary_ignores_all_received_watch_steps(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        progress = {
            "list": [
                {
                    "task_id": "activity-30",
                    "task_name": "观看守望先锋电竞直播间30分钟",
                    "group_label": "5月23日",
                    "task_status": 3,
                    "indicators": [{"cur_value": 30, "limit": 30}],
                },
                {
                    "task_id": "activity-60",
                    "task_name": "观看守望先锋电竞直播间60分钟",
                    "group_label": "5月23日",
                    "task_status": 3,
                    "indicators": [{"cur_value": 60, "limit": 60}],
                },
            ]
        }

        summary = live_watcher._summarize_task(progress)

        self.assertEqual(summary, "")

    def test_record_task_progress_deduplicates_unchanged_summary(self) -> None:
        logs: list[str] = []
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), logs.append)
        progress = {"tasks": [{"task_id": "task-a", "name": "观看 30 分钟", "current": 10, "target": 30}]}

        live_watcher._record_task_progress(progress, announce_claimable=False)
        live_watcher._record_task_progress(progress, announce_claimable=False)

        self.assertEqual(sum(1 for message in logs if message.startswith("掉宝任务：")), 1)

    def test_record_task_progress_suppresses_startup_all_zero_snapshot(self) -> None:
        logs: list[str] = []
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), logs.append)
        zero_progress = {
            "tasks": [
                {"task_id": f"task-{index}", "name": f"奖励 {index}", "current": 0, "target": minutes}
                for index, minutes in enumerate((30, 60, 120, 180, 240, 300), start=1)
            ]
        }
        real_progress = {
            "tasks": [
                {"task_id": f"task-real-{index}", "name": f"真实奖励 {index}", "current": 257, "target": minutes}
                for index, minutes in enumerate((300, 360, 420, 480), start=1)
            ]
        }

        live_watcher._record_task_progress(zero_progress, announce_claimable=False)
        live_watcher._record_task_progress(real_progress, announce_claimable=False)

        task_logs = [message for message in logs if message.startswith("掉宝任务：")]
        self.assertEqual(len(task_logs), 1)
        self.assertIn("257/300 分钟", task_logs[0])
        self.assertNotIn("0/30 分钟", task_logs[0])
        self.assertIn("活动任务已识别，正在等待 B 站返回真实进度", logs)

    def test_task_summary_focuses_today_activity_group(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        today = date.today()
        yesterday = today - timedelta(days=1)
        today_label = f"{today.month}月{today.day}日"
        yesterday_label = f"{yesterday.month}月{yesterday.day}日"
        progress = {
            "list": [
                {
                    "task_id": "day-a-30",
                    "task_name": "观看守望先锋电竞直播间30分钟",
                    "award_name": "第一天奖励",
                    "group_label": yesterday_label,
                    "group_index": 0,
                    "task_status": 1,
                    "indicators": [{"cur_value": 0, "limit": 30}],
                },
                {
                    "task_id": "day-b-30",
                    "task_name": "观看守望先锋电竞直播间30分钟",
                    "award_name": "第二天奖励",
                    "group_label": today_label,
                    "group_index": 1,
                    "task_status": 1,
                    "indicators": [{"cur_value": 0, "limit": 30}],
                },
            ]
        }

        summary = live_watcher._summarize_task(progress)

        self.assertIn(f"当前可挂：{today_label}", summary)
        self.assertIn("第二天奖励", summary)
        self.assertNotIn("第一天奖励", summary)

    def test_task_summary_merges_today_groups_split_across_indexes(self) -> None:
        # B 站把同一天的任务拆进多个 EraTasklistPc 组（组数 > 日期 Tab 数），
        # 这些组共用同一个日期标签但 group_index 不同。聚焦今天时必须把它们合并，
        # 否则高档位奖励（在第二个组里）会被整组隐藏。
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        today = date.today()
        today_label = f"{today.month}月{today.day}日"
        progress = {
            "list": [
                {
                    "task_id": "today-low",
                    "task_name": "观看守望先锋电竞直播间30分钟",
                    "award_name": "低档奖励",
                    "group_label": today_label,
                    "group_index": 2,
                    "task_status": 1,
                    "indicators": [{"cur_value": 0, "limit": 30}],
                },
                {
                    "task_id": "today-high",
                    "task_name": "观看守望先锋电竞直播间300分钟",
                    "award_name": "高档奖励",
                    "group_label": today_label,
                    "group_index": 3,
                    "task_status": 1,
                    "indicators": [{"cur_value": 0, "limit": 300}],
                },
            ]
        }

        summary = live_watcher._summarize_task(progress)

        self.assertIn(f"当前可挂：{today_label}", summary)
        self.assertIn("低档奖励", summary)
        self.assertIn("高档奖励", summary)

    def test_task_summary_falls_back_to_active_group_when_today_missing(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        # 用相对的过去日期，确保两个标签都不是“今天”，否则今天恰好等于硬编码日期时
        # 会走进 today 分支，测不到这里要验证的回退逻辑。
        today = date.today()
        older_label = f"{(today - timedelta(days=3)).month}月{(today - timedelta(days=3)).day}日"
        newer_label = f"{(today - timedelta(days=2)).month}月{(today - timedelta(days=2)).day}日"
        progress = {
            "list": [
                {
                    "task_id": "day-a-30",
                    "task_name": "观看守望先锋电竞直播间30分钟",
                    "award_name": "第一天奖励",
                    "group_label": older_label,
                    "group_index": 0,
                    "task_status": 1,
                    "indicators": [{"cur_value": 12, "limit": 30}],
                },
                {
                    "task_id": "day-b-30",
                    "task_name": "观看守望先锋电竞直播间30分钟",
                    "award_name": "第二天奖励",
                    "group_label": newer_label,
                    "group_index": 1,
                    "task_status": 1,
                    "indicators": [{"cur_value": 0, "limit": 30}],
                },
            ]
        }

        summary = live_watcher._summarize_task(progress)

        self.assertIn(f"当前可挂：{older_label}", summary)
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
