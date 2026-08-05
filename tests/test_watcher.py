from __future__ import annotations

import threading
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from unittest.mock import patch

from bili_drop_guard.bilibili import LoginInfo, RoomInfo
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
        self.assertIn("20/20 心跳已接受", summary)
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
            {"hbil": 30, "sid": "sid-1", "stky": "key-1", "play_url": "https://example.com/live.flv", "qid": 4},
        )
        next_state = live_watcher._extract_heartbeat_state({}, fallback=state)

        self.assertEqual(next_state.interval, 30)
        self.assertEqual(next_state.official_interval, 30)
        self.assertEqual(next_state.session_id, "sid-1")
        self.assertEqual(next_state.stky, "key-1")
        self.assertEqual(next_state.play_url, "https://example.com/live.flv")
        self.assertEqual(next_state.qid, 4)

    def test_live_watch_matches_competitor_x25kn_lifecycle(self) -> None:
        calls: list[str] = []

        class FakeClient:
            def room_entry_action(self, room: RoomInfo) -> dict[str, object]:
                calls.append("ENTRY")
                return {}

            def enter_room_heartbeat(self, room: RoomInfo) -> dict[str, object]:
                calls.append("X25_E")
                return {
                    "heartbeat_interval": 30,
                    "timestamp": 100,
                    "secret_key": "legacy",
                    "secret_rule": [0],
                }

            def in_room_heartbeat(self, *args, **kwargs) -> dict[str, object]:
                calls.append("X25_X")
                return {
                    "heartbeat_interval": 45,
                    "timestamp": 200,
                    "secret_key": "legacy-2",
                    "secret_rule": [1],
                }

            def get_live_play_url(self, _room):
                raise AssertionError("competitor route must not start the player protocol")

            def start_live_watch_session(self, *_args, **_kwargs):
                raise AssertionError("competitor route must not call te9Kl")

            def continue_live_watch_session(self, *_args, **_kwargs):
                raise AssertionError("competitor route must not call s82Tq")

        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        room = RoomInfo(room_id=23612045, live_status=1)

        state = live_watcher._start_heartbeat_session(FakeClient(), room, watcher.HeartbeatState())
        next_state = live_watcher._continue_heartbeat_session(FakeClient(), room, state.qid, state)

        self.assertEqual(calls, ["ENTRY", "X25_E", "X25_X"])
        self.assertEqual(state.legacy_sequence, 1)
        self.assertEqual(next_state.legacy_sequence, 2)
        self.assertEqual(next_state.interval, 45)

    def test_room_entry_must_succeed_before_x25kn_enter(self) -> None:
        calls: list[str] = []

        class FakeClient:
            def room_entry_action(self, _room):
                calls.append("ENTRY")
                raise RuntimeError("entry rejected")

            def enter_room_heartbeat(self, _room):
                calls.append("X25_E")
                return {}

        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)

        with self.assertRaisesRegex(RuntimeError, "entry rejected"):
            live_watcher._start_heartbeat_session(
                FakeClient(), RoomInfo(room_id=1, live_status=1), watcher.HeartbeatState()
            )

        self.assertEqual(calls, ["ENTRY"])

    def test_x25kn_failure_is_propagated_for_route_rebuild(self) -> None:
        class FakeClient:
            def in_room_heartbeat(self, *_args, **_kwargs):
                raise RuntimeError("x25 rejected")

        state = watcher.HeartbeatState(
            legacy_interval=60,
            legacy_ets=100,
            legacy_secret_key="legacy",
            legacy_secret_rule=[0],
            legacy_sequence=1,
        )
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)

        with self.assertRaisesRegex(RuntimeError, "x25 rejected"):
            live_watcher._continue_heartbeat_session(
                FakeClient(), RoomInfo(room_id=1, live_status=1), state.qid, state
            )

    def test_x25kn_rotation_reuses_previous_fields_when_response_omits_them(self) -> None:
        class FakeClient:
            def in_room_heartbeat(self, *_args, **_kwargs):
                return {"heartbeat_interval": 45}

        state = watcher.HeartbeatState(
            legacy_interval=60,
            legacy_ets=100,
            legacy_secret_key="legacy",
            legacy_secret_rule=[2, 5],
            legacy_sequence=4,
        )
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)

        next_state = live_watcher._continue_heartbeat_session(
            FakeClient(), RoomInfo(room_id=1, live_status=1), state.qid, state
        )

        self.assertEqual(next_state.legacy_interval, 45)
        self.assertEqual(next_state.legacy_ets, 100)
        self.assertEqual(next_state.legacy_secret_key, "legacy")
        self.assertEqual(next_state.legacy_secret_rule, [2, 5])
        self.assertEqual(next_state.legacy_sequence, 5)

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
        self.assertIn("开始批量领取奖励：本次会连续处理当前及后续解锁的全部可领项", logs)
        self.assertIn("批量领取完成：新领取 2 个，已领取过 0 个，失败 0 个", logs)
        self.assertFalse(any(message.startswith("已领取：") for message in logs))

    def test_claim_worker_drains_rewards_unlocked_one_at_a_time_as_one_batch(self) -> None:
        claimed: list[str] = []
        logs: list[str] = []
        progress_checks = 0

        class FakeClient:
            def __init__(self, cookie: str) -> None:
                self.cookie = cookie

            def get_user_task_progress(self, up_id: int, task_id: str | None = None) -> dict[str, object]:
                return {}

            def get_activity_task_progress(self, task_ids: list[str]) -> dict[str, object]:
                nonlocal progress_checks
                progress_checks += 1
                if not claimed:
                    statuses = {"activity-a": 2, "activity-b": 1}
                elif claimed == ["activity-a"] and progress_checks < 3:
                    # 模拟上一项领取后，B 站需要一次刷新才开放下一项。
                    statuses = {"activity-a": 3, "activity-b": 1}
                elif claimed == ["activity-a"]:
                    statuses = {"activity-a": 3, "activity-b": 2}
                else:
                    statuses = {"activity-a": 3, "activity-b": 3}
                return {
                    "list": [
                        {
                            "task_id": task_id,
                            "task_name": f"奖励 {task_id}",
                            "task_status": statuses[task_id],
                            "indicators": [{"cur_value": 60, "limit": 60}],
                        }
                        for task_id in task_ids
                    ]
                }

            def claim_activity_mission_reward(self, task_id: str) -> dict[str, object]:
                claimed.append(task_id)
                return {}

        original_client = watcher.BilibiliClient
        watcher.BilibiliClient = FakeClient
        try:
            live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), logs.append)
            live_watcher._last_up_id = 100
            live_watcher._activity_task_ids.update({"activity-a", "activity-b"})
            live_watcher._activity_claim_task_ids.update({"activity-a", "activity-b"})
            live_watcher._next_activity_discovery_at = float("inf")

            live_watcher._claim_completed_worker()
        finally:
            watcher.BilibiliClient = original_client

        self.assertEqual(claimed, ["activity-a", "activity-b"])
        self.assertGreaterEqual(progress_checks, 3)
        self.assertEqual(sum(message.startswith("开始批量领取奖励") for message in logs), 1)
        self.assertEqual(sum(message.startswith("批量领取完成") for message in logs), 1)
        self.assertFalse(any(message.startswith("已领取：") for message in logs))

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
            live_watcher._activity_claim_task_ids.add("activity-a")

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
            live_watcher._activity_claim_task_ids.add("activity-a")
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
            live_watcher._activity_claim_task_ids.add("activity-a")

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
            live_watcher._activity_claim_task_ids.add("activity-a")

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
            live_watcher._activity_claim_task_ids.add("activity-a")
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
        today = watcher._bilibili_today()
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

    def test_new_checkpoint_template_queries_parent_and_claims_checkpoint_sid(self) -> None:
        calls: list[list[str]] = []

        class FakeClient:
            def discover_live_activity_tasks(self, room_id: str) -> dict[str, object]:
                return {
                    "tracking_task_ids": ["parent-day-1"],
                    "tasks": [
                        {
                            "task_id": "claim-60",
                            "parent_task_id": "parent-day-1",
                            "task_name": "观看直播60分钟",
                            "award_name": "头像",
                            "group_label": "7月29日",
                            "current": 0,
                            "target": 60,
                            "task_status": 1,
                        },
                        {
                            "task_id": "claim-120",
                            "parent_task_id": "parent-day-1",
                            "task_name": "观看直播120分钟",
                            "award_name": "战令等级直升",
                            "group_label": "7月29日",
                            "current": 0,
                            "target": 120,
                            "task_status": 1,
                        },
                    ],
                }

            def get_activity_task_progress(self, task_ids: list[str]) -> dict[str, object]:
                calls.append(task_ids)
                return {
                    "tracking_task_ids": ["parent-day-1"],
                    "list": [
                        {
                            "task_id": "claim-60",
                            "parent_task_id": "parent-day-1",
                            "task_name": "观看直播60分钟",
                            "award_name": "头像",
                            "current": 60,
                            "target": 60,
                            "task_status": 3,
                        },
                        {
                            "task_id": "claim-120",
                            "parent_task_id": "parent-day-1",
                            "task_name": "观看直播120分钟",
                            "award_name": "战令等级直升",
                            "current": 120,
                            "target": 120,
                            "task_status": 2,
                        },
                    ],
                }

        live_watcher = LiveWatcher(
            WatchOptions(cookie="a=b", room_id="23612045"),
            lambda _message: None,
        )

        found_claimable = live_watcher._check_activity_task_progress(FakeClient())

        self.assertTrue(found_claimable)
        self.assertEqual(calls, [["parent-day-1"]])
        self.assertEqual(live_watcher._activity_task_ids, {"parent-day-1"})
        self.assertEqual(
            live_watcher._activity_claim_task_ids,
            {"claim-60", "claim-120"},
        )
        self.assertEqual(live_watcher._claimable_task_ids, {"claim-120"})
        self.assertNotIn("parent-day-1", live_watcher._claimable_task_ids)
        self.assertEqual(
            live_watcher._activity_task_meta["claim-120"]["award_name"],
            "战令等级直升",
        )

    def test_new_checkpoint_claim_uses_activity_mission_api(self) -> None:
        calls: list[tuple[str, str]] = []

        class FakeClient:
            def __init__(self, cookie: str) -> None:
                self.cookie = cookie

            def claim_activity_mission_reward(self, task_id: str) -> dict[str, object]:
                calls.append(("activity", task_id))
                return {}

            def claim_user_task_rewards(self, up_id: int, task_id: str | None = None) -> dict[str, object]:
                calls.append(("user", str(task_id)))
                return {}

            def close(self) -> None:
                return None

        original_client = watcher.BilibiliClient
        watcher.BilibiliClient = FakeClient
        try:
            live_watcher = LiveWatcher(
                WatchOptions(cookie="a=b", room_id="23612045"),
                lambda _message: None,
            )
            live_watcher._activity_task_ids.add("parent-day-1")
            live_watcher._activity_claim_task_ids.add("claim-120")
            live_watcher._activity_task_meta["claim-120"] = {
                "group_label": "7月29日",
                "task_name": "观看直播120分钟",
                "award_name": "战令等级直升",
            }

            result = live_watcher._claim_one_task(100, "claim-120")
        finally:
            watcher.BilibiliClient = original_client

        self.assertEqual(calls, [("activity", "claim-120")])
        self.assertIn("战令等级直升", result)

    def test_activity_progress_enrichment_replaces_empty_checkpoint_metadata(self) -> None:
        live_watcher = LiveWatcher(
            WatchOptions(cookie="a=b", room_id="23612045"),
            lambda _message: None,
        )
        live_watcher._activity_task_meta["claim-120"] = {
            "group_label": "7月29日",
            "group_index": 0,
            "task_name": "观看直播120分钟",
            "award_name": "战令等级直升",
        }
        progress = {
            "list": [
                {
                    "task_id": "claim-120",
                    "group_label": "",
                    "task_name": "",
                    "award_name": "",
                    "task_status": 2,
                    "current": 120,
                    "target": 120,
                }
            ]
        }

        live_watcher._enrich_activity_progress(progress)

        task = progress["list"][0]
        self.assertEqual(task["group_label"], "7月29日")
        self.assertEqual(task["group_index"], 0)
        self.assertEqual(task["task_name"], "观看直播120分钟")
        self.assertEqual(task["award_name"], "战令等级直升")

    def test_activity_discovery_is_cached_while_progress_stays_fresh(self) -> None:
        calls = {"discover": 0, "progress": 0}

        class FakeClient:
            def discover_live_activity_tasks(self, room_id: str) -> dict[str, object]:
                calls["discover"] += 1
                return {"tasks": [{"task_id": "activity-a", "task_name": "观看 30 分钟"}]}

            def get_activity_task_progress(self, task_ids: list[str]) -> dict[str, object]:
                calls["progress"] += 1
                return {"list": [{"task_id": "activity-a", "task_status": 1}]}

        live_watcher = LiveWatcher(
            WatchOptions(cookie="a=b", room_id="23612045"),
            lambda _message: None,
        )
        client = FakeClient()

        live_watcher._check_activity_task_progress(client)
        live_watcher._check_activity_task_progress(client)

        self.assertEqual(calls, {"discover": 1, "progress": 2})

    def test_forced_activity_discovery_bypasses_cache(self) -> None:
        calls = {"discover": 0}

        class FakeClient:
            def discover_live_activity_tasks(self, room_id: str) -> dict[str, object]:
                calls["discover"] += 1
                return {"tasks": [{"task_id": "activity-a"}]}

        live_watcher = LiveWatcher(
            WatchOptions(cookie="a=b", room_id="23612045"),
            lambda _message: None,
        )
        client = FakeClient()

        live_watcher._discover_activity_task_ids_if_due(client, announce_progress=False)
        live_watcher._discover_activity_task_ids_if_due(client, announce_progress=False, force=True)

        self.assertEqual(calls["discover"], 2)

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

    def test_watch_start_delay_matches_competitor_one_route_per_second(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _m: None)
        self.assertEqual(live_watcher._watch_start_delay(1), 0.0)
        self.assertEqual(live_watcher._watch_start_delay(5), 4.0)
        self.assertEqual(live_watcher._watch_start_delay(10), 9.0)
        self.assertEqual(live_watcher._watch_start_delay(11), 10.0)
        self.assertEqual(live_watcher._watch_start_delay(42), 41.0)
        self.assertEqual(live_watcher._watch_start_delay(100), 99.0)

    def test_heartbeat_count_appears_in_status_summary(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1", watch_threads=2), lambda _m: None)
        summary_before, _n1, _p1 = live_watcher._watch_status_summary_info()
        self.assertNotIn("累计计时", summary_before)
        for _ in range(3):
            live_watcher._record_heartbeat()
        summary_after, _n2, _p2 = live_watcher._watch_status_summary_info()
        self.assertIn("请求成功 3 次", summary_after)
        self.assertNotIn("累计计时", summary_after)

    def test_status_summary_reports_totalv2_effective_rate(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1", watch_threads=40), lambda _m: None)
        for worker_id in range(1, 41):
            live_watcher._set_watch_status(worker_id, "正常", interval=60)
        live_watcher._record_server_progress(100, 1_000.0)
        live_watcher._record_server_progress(102, 1_120.0)

        summary, _normal, _problem = live_watcher._watch_status_summary_info()

        self.assertIn("40/40 心跳已接受", summary)
        self.assertIn("设置 40 路", summary)
        self.assertIn("B 站实绩约 1.0x", summary)

    def test_high_multiplier_totalv2_rate_is_available_after_twenty_seconds(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1", watch_threads=100), lambda _m: None)
        live_watcher._record_server_progress(100, 1_000.0)
        live_watcher._record_server_progress(110, 1_020.0)

        self.assertAlmostEqual(live_watcher.get_server_credit_rate() or 0.0, 30.0, delta=0.01)

    def test_configured_thread_count_is_never_expanded_in_background(self) -> None:
        live_watcher = RecordingWatcher(
            WatchOptions(cookie="a=b", room_id="1", watch_threads=2)
        )
        room = RoomInfo(room_id=1, live_status=1)
        live_watcher._room = room
        live_watcher._start_watch_threads(room)
        for thread in list(live_watcher._watch_threads):
            thread.join(timeout=3)

        live_watcher._record_server_progress(0, 1_000.0, expect_progress=True)
        live_watcher._record_server_progress(1, 1_060.0, expect_progress=True)

        self.assertEqual(live_watcher.started_workers, [1, 2])
        self.assertEqual(live_watcher._watch_worker_count, 2)
        self.assertEqual(len(live_watcher._watch_threads), 2)

    def test_high_route_count_uses_faster_totalv2_stall_detection(self) -> None:
        hundred = LiveWatcher(WatchOptions(cookie="a=b", room_id="1", watch_threads=100), lambda _m: None)
        ten = LiveWatcher(WatchOptions(cookie="a=b", room_id="1", watch_threads=10), lambda _m: None)

        self.assertEqual(hundred._server_progress_stall_seconds(), 90.0)
        self.assertEqual(ten._server_progress_stall_seconds(), 150.0)

    def test_stalled_totalv2_requests_a_single_session_rebuild(self) -> None:
        logs: list[str] = []
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1", watch_threads=10), logs.append)
        live_watcher._room = RoomInfo(room_id=1, live_status=1)
        live_watcher._heartbeat_count = 10

        with (
            patch.object(watcher, "SERVER_PROGRESS_STALL_SECONDS", 120.0),
            patch.object(watcher, "SERVER_PROGRESS_RECONNECT_COOLDOWN_SECONDS", 120.0),
        ):
            generation = live_watcher._watch_session_generation()
            live_watcher._record_server_progress(140, 1_000.0, expect_progress=True)
            live_watcher._record_server_progress(140, 1_121.0, expect_progress=True)
            live_watcher._record_server_progress(140, 1_180.0, expect_progress=True)

        self.assertTrue(live_watcher._watch_session_needs_reconnect(generation))
        self.assertEqual(live_watcher._watch_reconnect_generation, generation + 1)
        self.assertEqual(sum("正在自动重建观看会话" in message for message in logs), 1)

    def test_generation_rebuild_replaces_the_underlying_http_session(self) -> None:
        created: list[object] = []
        closed: list[int] = []
        started: list[int] = []

        class FakeClient:
            def __init__(self, number: int) -> None:
                self.number = number

            def get_live_play_url(self, room: RoomInfo) -> str:
                return "https://example.com/live.flv"

            def room_entry_action(self, room: RoomInfo) -> dict[str, object]:
                return {}

            def enter_room_heartbeat(self, room: RoomInfo) -> dict[str, object]:
                started.append(self.number)
                return {"heartbeat_interval": 30, "timestamp": 100, "secret_key": "legacy", "secret_rule": [0]}

            def close(self) -> None:
                closed.append(self.number)

        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        room = RoomInfo(room_id=1, live_status=1)
        live_watcher._room = room

        def new_client() -> FakeClient:
            client = FakeClient(len(created) + 1)
            created.append(client)
            return client

        waits = 0

        def wait_once(_interval: int, _generation: int) -> None:
            nonlocal waits
            waits += 1
            if waits == 1:
                live_watcher._watch_reconnect_generation += 1
            else:
                live_watcher._stop.set()

        live_watcher._new_watch_client = new_client  # type: ignore[method-assign]
        live_watcher._watch_start_delay = lambda _worker_id: 0.0  # type: ignore[method-assign]
        live_watcher._stagger_watch_reconnect = lambda _worker_id: None  # type: ignore[method-assign]
        live_watcher._wait_for_watch_interval = wait_once  # type: ignore[method-assign]

        live_watcher._heartbeat_watch_worker(1, room)

        self.assertEqual(len(created), 2)
        self.assertEqual(started, [1, 2])
        self.assertEqual(closed, [1, 2])

    def test_totalv2_advance_resets_stall_timer(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        live_watcher._room = RoomInfo(room_id=1, live_status=1)
        live_watcher._heartbeat_count = 1

        with patch.object(watcher, "SERVER_PROGRESS_STALL_SECONDS", 120.0):
            live_watcher._record_server_progress(140, 1_000.0, expect_progress=True)
            live_watcher._record_server_progress(141, 1_100.0, expect_progress=True)
            live_watcher._record_server_progress(141, 1_190.0, expect_progress=True)

        self.assertEqual(live_watcher._watch_reconnect_generation, 0)
        self.assertEqual(live_watcher._server_progress_advanced_at, 1_100.0)

    def test_totalv2_temporary_rollback_does_not_fake_a_new_advance(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        live_watcher._room = RoomInfo(room_id=1, live_status=1)
        live_watcher._heartbeat_count = 1

        live_watcher._record_server_progress(278, 1_000.0, expect_progress=True)
        live_watcher._record_server_progress(0, 1_100.0, expect_progress=True)
        live_watcher._record_server_progress(278, 1_121.0, expect_progress=True)

        self.assertEqual(live_watcher._server_progress_value, 278)
        self.assertEqual(live_watcher._server_progress_advanced_at, 1_000.0)

    def test_stable_completed_progress_does_not_rebuild_sessions(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        live_watcher._room = RoomInfo(room_id=1, live_status=1)
        live_watcher._heartbeat_count = 1

        with patch.object(watcher, "SERVER_PROGRESS_STALL_SECONDS", 120.0):
            live_watcher._record_server_progress(300, 1_000.0, expect_progress=False)
            live_watcher._record_server_progress(300, 1_500.0, expect_progress=False)

        self.assertEqual(live_watcher._watch_reconnect_generation, 0)

    def test_pending_watch_progress_ignores_non_watch_tasks(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)

        self.assertTrue(live_watcher._has_pending_watch_progress({
            "list": [{"task_name": "观看直播180分钟", "current": 140, "target": 180, "task_status": 1}],
        }))
        self.assertFalse(live_watcher._has_pending_watch_progress({
            "list": [{"task_name": "完成指定互动", "current": 0, "target": 1, "task_status": 1}],
        }))

    def test_totalv2_activity_does_not_depend_on_chinese_watch_keywords(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)

        self.assertTrue(live_watcher._has_pending_watch_progress({
            "tracking_task_ids": ["parent-a"],
            "list": [{"task_id": "parent-a", "task_name": "OWCS Drops", "current": 140, "target": 180, "task_status": 1}],
        }))

    def test_totalv2_mixed_tasks_only_use_live_minutes_for_server_progress(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        progress = {
            "tracking_task_ids": ["follow-parent", "watch-parent", "vote-parent"],
            "list": [
                {
                    "task_id": "follow-parent",
                    "task_name": "关注推荐主播",
                    "current": 24,
                    "target": 24,
                    "task_status": 3,
                },
                {
                    "task_id": "watch-parent",
                    "task_name": "每日观看官方直播间30分钟",
                    "current": 16,
                    "target": 30,
                    "task_status": 1,
                },
                {
                    "task_id": "vote-parent",
                    "task_name": "累计投票",
                    "current": 15,
                    "target": 20,
                    "task_status": 1,
                },
            ],
        }

        self.assertEqual(live_watcher._task_summary_progress_score(progress), 16.0)
        self.assertTrue(live_watcher._has_pending_watch_progress(progress))

    def test_totalv2_pending_non_watch_task_does_not_trigger_watch_stall(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        progress = {
            "tracking_task_ids": ["watch-parent", "share-parent"],
            "list": [
                {
                    "task_id": "watch-parent",
                    "task_name": "观看直播30分钟",
                    "current": 30,
                    "target": 30,
                    "task_status": 2,
                },
                {
                    "task_id": "share-parent",
                    "task_name": "每日分享活动页",
                    "current": 0,
                    "target": 1,
                    "task_status": 1,
                },
            ],
        }

        self.assertEqual(live_watcher._task_summary_progress_score(progress), 30.0)
        self.assertFalse(live_watcher._has_pending_watch_progress(progress))

    def test_totalv2_signature_does_not_change_when_claim_checkpoints_change(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)

        first = {
            "tracking_task_ids": ["parent-a"],
            "list": [{"task_id": "claim-1", "task_name": "观看直播30分钟", "group_label": "8月2日", "current": 10, "target": 30}],
        }
        second = {
            "tracking_task_ids": ["parent-a"],
            "list": [{"task_id": "claim-2", "task_name": "观看直播60分钟", "group_label": "8月2日", "current": 10, "target": 60}],
        }

        self.assertEqual(live_watcher._task_progress_signature(first), live_watcher._task_progress_signature(second))

    def test_server_rate_uses_recent_window_instead_of_startup_burst(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _m: None)
        live_watcher._record_server_progress(100, 0.0)
        live_watcher._record_server_progress(120, 60.0)
        live_watcher._record_server_progress(121, 180.0)
        live_watcher._record_server_progress(122, 240.0)
        live_watcher._record_server_progress(123, 300.0)

        self.assertAlmostEqual(live_watcher.get_server_credit_rate() or 0.0, 1.0, delta=0.01)

    def test_local_watch_estimate_does_not_multiply_heartbeat_intervals(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1", watch_threads=2), lambda _m: None)
        for _ in range(40):
            live_watcher._record_heartbeat(60)
        live_watcher._watch_started_at = time.time() - 90

        self.assertAlmostEqual(live_watcher.get_local_watch_estimate_minutes(), 1.5, delta=0.02)

    def test_local_watch_estimate_freezes_after_stop_and_resets_on_restart(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _m: None)
        live_watcher._record_heartbeat()
        live_watcher._watch_started_at = time.time() - 60
        live_watcher.stop()
        frozen = live_watcher.get_local_watch_estimate_minutes()
        time.sleep(0.01)
        self.assertAlmostEqual(live_watcher.get_local_watch_estimate_minutes(), frozen, delta=0.001)

        with patch("bili_drop_guard.watcher.threading.Thread") as thread_class:
            thread_class.return_value = unittest.mock.MagicMock()
            live_watcher.start()
        self.assertEqual(live_watcher._heartbeat_count, 0)
        self.assertEqual(live_watcher.get_local_watch_estimate_minutes(), 0.0)

    def test_status_summary_logs_only_on_problem_not_when_normal(self) -> None:
        logs: list[str] = []
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1", watch_threads=3), logs.append)
        for worker_id in (1, 2, 3):
            live_watcher._set_watch_status(worker_id, "正常", interval=60)
        live_watcher._log_watch_status_summary(force=False)
        self.assertFalse(any("后台计时状态" in message for message in logs))
        live_watcher._set_watch_status(3, "暂时失败", message="网络抖动")
        live_watcher._log_watch_status_summary(force=False)
        self.assertTrue(any("观看连接" in message for message in logs))

    def test_progress_polling_runs_when_auto_claim_is_disabled(self) -> None:
        live_watcher = LiveWatcher(
            WatchOptions(cookie="a=b", room_id="1", auto_claim=False),
            lambda _message: None,
        )
        calls: list[str] = []

        class FakeClient:
            def check_login(self):
                return LoginInfo(True, uname="tester", mid=1)

            def get_room_info(self, _room_id):
                live_watcher._stop.set()
                return RoomInfo(room_id=1, live_status=1, anchor_uid=2)

            def close(self):
                pass

        live_watcher._start_watch_threads = lambda _room: None  # type: ignore[method-assign]
        live_watcher._check_activity_task_progress = lambda _client: calls.append("activity") or False  # type: ignore[method-assign]
        live_watcher._check_and_claim_task = lambda _client, _up_id: calls.append("generic") or False  # type: ignore[method-assign]
        live_watcher._check_explicit_task_ids = lambda _up_id: calls.append("explicit") or False  # type: ignore[method-assign]
        live_watcher._start_auto_claim_thread = lambda: calls.append("claim")  # type: ignore[method-assign]

        with patch("bili_drop_guard.watcher.BilibiliClient", return_value=FakeClient()):
            live_watcher._run()

        self.assertEqual(calls, ["activity", "generic", "explicit"])

    def test_server_progress_rollback_keeps_last_confirmed_minutes(self) -> None:
        logs: list[str] = []
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), logs.append)

        def progress(current: int) -> dict:
            return {
                "list": [{
                    "task_id": "parent",
                    "task_name": "观看直播60分钟",
                    "group_label": "7月31日",
                    "current": current,
                    "target": 60,
                    "task_status": 1,
                }]
            }

        live_watcher._record_task_progress(progress(30), announce_claimable=False)
        confirmed_summary = live_watcher._last_task_summary
        live_watcher._record_task_progress(progress(29), announce_claimable=False)

        self.assertEqual(live_watcher._last_task_progress_score, 30)
        self.assertEqual(live_watcher._last_task_summary, confirmed_summary)
        self.assertTrue(any("保留上次已确认分钟数" in message for message in logs))

    def test_activity_progress_does_not_call_mission_info_when_totalv2_empty(self) -> None:
        logs: list[str] = []
        mission_calls: list[list[str]] = []

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
                mission_calls.append(list(task_ids))
                raise AssertionError("totalv2 为空时不应再连发 mission/info 兜底")

        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="23612045"), logs.append)

        found_claimable = live_watcher._check_activity_task_progress(FakeClient())

        self.assertFalse(found_claimable)
        self.assertEqual(mission_calls, [])
        self.assertFalse(any("兜底" in message for message in logs))
        self.assertFalse(any("冷却" in message for message in logs))

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
        live_watcher._activity_claim_task_ids.update({"old-claim"})
        live_watcher._activity_task_meta["old-task"] = {"group_label": "5月22日"}
        live_watcher._activity_task_meta["old-claim"] = {"group_label": "5月22日"}
        live_watcher._claimable_task_ids.update({"old-task", "old-claim"})

        task_ids = live_watcher._discover_activity_task_ids(FakeClient(), announce_progress=False)

        self.assertEqual(task_ids, ["new-task"])
        self.assertEqual(live_watcher._activity_task_ids, {"new-task"})
        self.assertEqual(live_watcher._activity_claim_task_ids, {"new-task"})
        self.assertEqual(live_watcher._claimable_task_ids, set())
        self.assertEqual(live_watcher._activity_task_meta["new-task"]["group_label"], "5月25日")
        self.assertTrue(any("活动任务已更新" in message for message in logs))

    def test_activity_discovery_anchors_clock_to_bilibili_server_time(self) -> None:
        class FakeClient:
            def discover_live_activity_tasks(self, room_id: str) -> dict[str, object]:
                return {
                    "server_time": 2_000.0,
                    "tasks": [{"task_id": "task-a", "current": 0, "target": 60}],
                }

        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="23612045"), lambda _message: None)

        with patch("bili_drop_guard.watcher.time.time", return_value=1_000.0):
            live_watcher._discover_activity_task_ids(FakeClient(), announce_progress=False)
            server_now = live_watcher._bilibili_now_timestamp()

        self.assertEqual(live_watcher._bilibili_time_offset_seconds, 1_000.0)
        self.assertEqual(server_now, 2_000.0)

    def test_activity_discovery_logs_bilibili_server_clock_once(self) -> None:
        class FakeClient:
            def discover_live_activity_tasks(self, room_id: str) -> dict[str, object]:
                return {
                    "server_time": datetime(
                        2026,
                        7,
                        30,
                        16,
                        53,
                        tzinfo=timezone.utc,
                    ).timestamp(),
                    "tasks": [{"task_id": "task-a", "current": 0, "target": 60}],
                }

        logs: list[str] = []
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="23612045"), logs.append)

        live_watcher._discover_activity_task_ids(FakeClient(), announce_progress=False)
        live_watcher._discover_activity_task_ids(FakeClient(), announce_progress=False)

        server_time_logs = [message for message in logs if message.startswith("B站时间已同步")]
        self.assertEqual(len(server_time_logs), 1)
        self.assertIn("7月31日 00:53:00", server_time_logs[0])

    def test_activity_discovery_clears_stale_task_ids_when_page_has_no_tasks(self) -> None:
        class FakeClient:
            def discover_live_activity_tasks(self, room_id: str) -> dict[str, object]:
                return {"tasks": []}

            def get_activity_task_progress(self, task_ids: list[str]) -> dict[str, object]:
                raise AssertionError(f"不应继续查询旧任务：{task_ids}")

        logs: list[str] = []
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="23612045"), logs.append)
        live_watcher._activity_task_ids.update({"old-task"})
        live_watcher._activity_claim_task_ids.update({"old-claim"})
        live_watcher._activity_task_meta["old-task"] = {"group_label": "5月22日"}
        live_watcher._activity_task_meta["old-claim"] = {"group_label": "5月22日"}
        live_watcher._claimable_task_ids.update({"old-task", "old-claim"})

        found_claimable = live_watcher._check_activity_task_progress(FakeClient())

        self.assertFalse(found_claimable)
        self.assertEqual(live_watcher._activity_task_ids, set())
        self.assertEqual(live_watcher._activity_claim_task_ids, set())
        self.assertEqual(live_watcher._activity_task_meta, {})
        self.assertEqual(live_watcher._claimable_task_ids, set())
        self.assertTrue(any("已清空旧任务缓存" in message for message in logs))

    def test_activity_discovery_preserves_stale_task_ids_on_parse_error(self) -> None:
        class FakeClient:
            def discover_live_activity_tasks(self, room_id: str) -> dict[str, object]:
                raise RuntimeError("直播页没有找到活动任务 ID，请确认直播间页面有本次掉宝任务")

            def get_activity_task_progress(self, task_ids: list[str]) -> dict[str, object]:
                raise AssertionError(f"不应继续查询旧任务：{task_ids}")

        logs: list[str] = []
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="23612045"), logs.append)
        live_watcher._activity_task_ids.update({"old-task"})
        live_watcher._activity_claim_task_ids.update({"old-claim"})
        live_watcher._activity_task_meta["old-task"] = {"group_label": "5月22日"}
        live_watcher._activity_task_meta["old-claim"] = {"group_label": "5月22日"}
        live_watcher._claimable_task_ids.update({"old-task", "old-claim"})

        found_claimable = live_watcher._check_activity_task_progress(FakeClient())

        self.assertFalse(found_claimable)
        self.assertEqual(live_watcher._activity_task_ids, {"old-task"})
        self.assertEqual(live_watcher._activity_claim_task_ids, {"old-claim"})
        self.assertEqual(live_watcher._activity_task_meta["old-task"]["group_label"], "5月22日")
        self.assertEqual(live_watcher._activity_task_meta["old-claim"]["group_label"], "5月22日")
        self.assertEqual(live_watcher._claimable_task_ids, {"old-task", "old-claim"})
        self.assertFalse(any("已清空旧任务缓存" in message for message in logs))
        self.assertTrue(any("暂时没有读到可跟踪的掉宝任务" in message for message in logs))

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

    def test_activity_status_one_is_not_claimable_even_when_progress_is_full(self) -> None:
        live_watcher = LiveWatcher(
            WatchOptions(cookie="a=b", room_id="1"),
            lambda _message: None,
        )
        progress = {
            "list": [
                {
                    "task_id": "claim-60",
                    "task_name": "观看直播60分钟",
                    "task_status": 1,
                    "current": 60,
                    "target": 60,
                },
                {
                    "task_id": "claim-120",
                    "task_name": "观看直播120分钟",
                    "task_status": 2,
                    "current": 120,
                    "target": 120,
                },
            ]
        }

        claimable = live_watcher._find_claimable_task_refs(progress)

        self.assertEqual(claimable, [("观看直播120分钟", "claim-120")])

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

    def test_task_summary_reports_all_received_watch_steps(self) -> None:
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

        self.assertEqual(summary, "全部奖励已领取：5月23日，共 2 个奖励")

    def test_record_task_progress_deduplicates_unchanged_summary(self) -> None:
        logs: list[str] = []
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), logs.append)
        progress = {"tasks": [{"task_id": "task-a", "name": "观看 30 分钟", "current": 10, "target": 30}]}

        with patch("bili_drop_guard.watcher.time.time", side_effect=[100.0, 1000.0]):
            live_watcher._record_task_progress(progress, announce_claimable=False)
            live_watcher._record_task_progress(progress, announce_claimable=False)

        self.assertEqual(sum(1 for message in logs if message.startswith("掉宝任务：")), 1)

    def test_record_task_progress_deduplicates_unchanged_detected_summary(self) -> None:
        logs: list[str] = []
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), logs.append)
        progress = {
            "tasks": [
                {"task_id": f"task-{index}", "name": f"奖励 {index}", "current": 0, "target": minutes}
                for index, minutes in enumerate((30, 60, 120, 180), start=1)
            ]
        }

        with patch("bili_drop_guard.watcher.time.time", side_effect=[100.0, 100.0, 1000.0, 1000.0]):
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
                {"task_id": f"task-real-{index}", "name": f"同步奖励 {index}", "current": 257, "target": minutes}
                for index, minutes in enumerate((300, 360, 420, 480), start=1)
            ]
        }

        live_watcher._record_task_progress(zero_progress, announce_claimable=False)
        live_watcher._record_task_progress(real_progress, announce_claimable=False)

        task_logs = [message for message in logs if message.startswith("掉宝任务：")]
        self.assertEqual(len(task_logs), 2)
        self.assertIn("共 6 个奖励", task_logs[0])
        self.assertIn("奖励 1（目标 30 分钟）：等待 B 站返回真实进度", task_logs[0])
        self.assertNotIn("0/30 分钟", task_logs[0])
        self.assertIn("257/300 分钟", task_logs[1])
        self.assertNotIn("0/30 分钟", task_logs[1])
        self.assertIn("活动任务已识别，等待 B 站返回真实进度", logs)

    def test_task_summary_focuses_today_activity_group(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        today = watcher._bilibili_today()
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

    def test_task_summary_prefers_current_active_period_over_calendar_day(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        now = time.time()
        today = watcher._bilibili_today()
        previous = today - timedelta(days=1)
        active_label = f"{previous.month}月{previous.day}日"
        upcoming_label = f"{today.month}月{today.day}日"
        progress = {
            "list": [
                {
                    "task_id": "active-task",
                    "award_name": "当前有效奖励",
                    "group_label": active_label,
                    "group_index": 0,
                    "task_status": 1,
                    "current": 10,
                    "target": 60,
                    "active_start": now - 3600,
                    "active_end": now + 3600,
                },
                {
                    "task_id": "future-task",
                    "award_name": "尚未开始奖励",
                    "group_label": upcoming_label,
                    "group_index": 1,
                    "task_status": 1,
                    "current": 0,
                    "target": 60,
                    "active_start": now + 3600,
                    "active_end": now + 7200,
                },
            ]
        }

        summary = live_watcher._summarize_task(progress)

        self.assertIn(f"当前可挂：{active_label}", summary)
        self.assertIn("当前有效奖励", summary)
        self.assertNotIn("尚未开始奖励", summary)

    def test_task_summary_handles_period_boundaries_and_gap(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        progress = {
            "list": [
                {
                    "task_id": "day-1",
                    "award_name": "第一场",
                    "group_label": "7月30日",
                    "group_index": 0,
                    "task_status": 1,
                    "current": 10,
                    "target": 60,
                    "active_start": 100.0,
                    "active_end": 200.0,
                },
                {
                    "task_id": "day-2",
                    "award_name": "第二场",
                    "group_label": "7月31日",
                    "group_index": 1,
                    "task_status": 1,
                    "current": 0,
                    "target": 60,
                    "active_start": 230.0,
                    "active_end": 330.0,
                },
            ]
        }

        with patch("bili_drop_guard.watcher.time.time", return_value=199.0):
            summary = live_watcher._summarize_task(progress)
            self.assertIn("当前可挂：7月30日", summary)
            self.assertIn("B站当前生效", summary)
        with patch("bili_drop_guard.watcher.time.time", return_value=200.0):
            self.assertIn("最近一场：7月30日", live_watcher._summarize_task(progress))
        with patch("bili_drop_guard.watcher.time.time", return_value=215.0):
            self.assertIn("最近一场：7月30日", live_watcher._summarize_task(progress))
        with patch("bili_drop_guard.watcher.time.time", return_value=230.0):
            self.assertIn("当前可挂：7月31日", live_watcher._summarize_task(progress))
        with patch("bili_drop_guard.watcher.time.time", return_value=50.0):
            self.assertIn("下一场：7月30日", live_watcher._summarize_task(progress))

    def test_task_summary_merges_today_groups_split_across_indexes(self) -> None:
        # B 站把同一天的任务拆进多个 EraTasklistPc 组（组数 > 日期 Tab 数），
        # 这些组共用同一个日期标签但 group_index 不同。聚焦今天时必须把它们合并，
        # 否则高档位奖励（在第二个组里）会被整组隐藏。
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        today = watcher._bilibili_today()
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
        today = watcher._bilibili_today()
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

    def test_bilibili_today_uses_utc_plus_eight_across_local_day_boundary(self) -> None:
        utc_time = datetime(2026, 7, 30, 16, 30, tzinfo=timezone.utc)

        self.assertEqual(watcher._bilibili_today(utc_time), date(2026, 7, 31))

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


class LiveWatcherRegressionTest(unittest.TestCase):
    def test_heartbeat_uses_latest_shared_room_snapshot(self) -> None:
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
        stale_room = RoomInfo(room_id=1, live_status=0, message="未开播")
        latest_room = RoomInfo(room_id=1, live_status=1, message="直播中")
        live_watcher._room = latest_room

        resolved = live_watcher._resolve_heartbeat_room(object(), stale_room)  # type: ignore[arg-type]

        self.assertIs(resolved, latest_room)

    def test_invalid_login_stops_before_starting_watch_workers_and_closes_client(self) -> None:
        closed = threading.Event()
        started = []

        class FakeClient:
            def __init__(self, cookie: str) -> None:
                self.cookie = cookie

            def check_login(self) -> LoginInfo:
                return LoginInfo(logged_in=False, message="Cookie 未登录")

            def close(self) -> None:
                closed.set()

        original_client = watcher.BilibiliClient
        watcher.BilibiliClient = FakeClient
        try:
            live_watcher = LiveWatcher(WatchOptions(cookie="expired", room_id="1"), lambda _message: None)
            live_watcher._start_watch_threads = lambda _room=None: started.append(True)  # type: ignore[method-assign]
            live_watcher._run()
        finally:
            watcher.BilibiliClient = original_client

        self.assertEqual(started, [])
        self.assertTrue(closed.is_set())

    def test_claimable_snapshot_is_deduplicated_and_received_snapshot_clears_queue(self) -> None:
        logs: list[str] = []
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), logs.append)
        live_watcher._last_up_id = 100
        claimable = {
            "list": [
                {"task_id": "task-a", "task_name": "奖励 A", "current": 30, "target": 30},
                {"task_id": "task-a", "task_name": "奖励 A", "current": 30, "target": 30},
            ]
        }

        live_watcher._record_task_progress(claimable, announce_claimable=True)
        live_watcher._record_task_progress(claimable, announce_claimable=True)

        self.assertEqual(live_watcher._claimable_task_ids, {"task-a"})
        self.assertEqual(sum("检测到 1 个奖励" in message for message in logs), 1)

        received = {"list": [{"task_id": "task-a", "task_name": "奖励 A", "is_receive": 1}]}
        live_watcher._record_task_progress(received, announce_claimable=True)
        self.assertEqual(live_watcher._claimable_task_ids, set())

    def test_claimable_snapshot_does_not_report_queue_when_auto_claim_is_off(self) -> None:
        logs: list[str] = []
        live_watcher = LiveWatcher(
            WatchOptions(cookie="a=b", room_id="1", auto_claim=False),
            logs.append,
        )
        claimable = {
            "list": [
                {"task_id": "task-a", "task_name": "奖励 A", "current": 30, "target": 30},
            ]
        }

        live_watcher._record_task_progress(claimable, announce_claimable=True)

        self.assertIn("检测到 1 个奖励可以领取；自动领取已关闭，请点击“领取奖励”", logs)
        self.assertFalse(any("正在排队领取" in message for message in logs))

    def test_concurrent_claim_for_same_task_submits_only_once(self) -> None:
        submit_count = 0
        submit_lock = threading.Lock()
        barrier = threading.Barrier(2)

        class FakeClient:
            def __init__(self, cookie: str) -> None:
                self.cookie = cookie

            def claim_user_task_rewards(self, up_id: int, task_id: str | None = None) -> dict[str, object]:
                nonlocal submit_count
                with submit_lock:
                    submit_count += 1
                time.sleep(0.05)
                return {}

            def close(self) -> None:
                return

        original_client = watcher.BilibiliClient
        watcher.BilibiliClient = FakeClient
        try:
            live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="1"), lambda _message: None)
            results: list[str] = []

            def claim() -> None:
                barrier.wait(timeout=1)
                results.append(live_watcher._claim_one_task(100, "task-a"))

            threads = [threading.Thread(target=claim), threading.Thread(target=claim)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)
        finally:
            watcher.BilibiliClient = original_client

        self.assertEqual(submit_count, 1)
        self.assertEqual(len(results), 2)
        self.assertTrue(any("正在领取" in result or "已经领取过" in result for result in results))


if __name__ == "__main__":
    unittest.main()
