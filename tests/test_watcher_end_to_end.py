from __future__ import annotations

import threading
import time
import unittest

from bili_drop_guard import watcher as watcher_module
from bili_drop_guard.bilibili import RoomInfo
from bili_drop_guard.watcher import LiveWatcher, WatchOptions


class FakeClient:
    """模拟 BilibiliClient 一整轮：心跳成功 → 检测到完成 → 领奖。"""

    def __init__(
        self,
        cookie: str = "a=b",
        *,
        session_buvid: str | None = None,
        session_device_uuid: str | None = None,
    ) -> None:
        self.cookie = cookie
        self.session_buvid = session_buvid
        self.session_device_uuid = session_device_uuid
        self.heartbeat_calls: list[str] = []
        self.claim_calls: list[tuple[int, str | None]] = []
        self._claim_step = {"current": 0, "total": 30}

    def check_login(self):  # noqa: D401
        from bili_drop_guard.bilibili import LoginInfo

        return LoginInfo(logged_in=True, uname="测试账号", mid=999, message="ok")

    def get_room_info(self, room_id: str) -> RoomInfo:
        return RoomInfo(
            room_id=int(room_id) if str(room_id).isdigit() else 23612045,
            title="测试直播",
            live_status=1,
            online=100,
            anchor="测试主播",
            anchor_uid=12345,
            message="直播中",
        )

    def enter_room_heartbeat(self, room: RoomInfo) -> dict:
        self.heartbeat_calls.append(f"enter:{room.room_id}")
        return {"heartbeat_interval": 30, "timestamp": 100, "secret_key": "k", "secret_rule": [0]}

    def in_room_heartbeat(self, room: RoomInfo, sequence: int, interval: int, ets: int, secret_key: str, secret_rule: list[int]) -> dict:
        self.heartbeat_calls.append(f"in:{room.room_id}:{sequence}")
        return {"heartbeat_interval": 30, "timestamp": 200}

    def get_user_task_progress(self, up_id: int, task_id: str | None = None) -> dict:
        # 第一次进度未满；之后已满且可领取
        step = self._claim_step
        step["current"] = min(step["total"], step["current"] + step["total"])
        return {
            "list": [
                {
                    "task_id": "live-task-1",
                    "task_name": "观看 30 分钟",
                    "current": step["current"],
                    "target": step["total"],
                }
            ]
        }

    def discover_live_activity_tasks(self, room_id: str) -> dict:
        return {"tasks": []}

    def get_activity_task_progress(self, task_ids: list[str]) -> dict:
        return {"list": []}

    def claim_user_task_rewards(self, up_id: int, task_id: str | None = None) -> dict:
        self.claim_calls.append((up_id, task_id))
        return {}


class WatcherEndToEndTest(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_client = watcher_module.BilibiliClient
        self._orig_submit = watcher_module.CLAIM_SUBMIT_DELAY_SECONDS
        self._orig_rate = watcher_module.CLAIM_RATE_LIMIT_DELAY_SECONDS
        watcher_module.CLAIM_SUBMIT_DELAY_SECONDS = 0
        watcher_module.CLAIM_RATE_LIMIT_DELAY_SECONDS = 0
        self._client_holder: dict[str, FakeClient] = {}

        def factory(cookie: str, *, session_buvid: str | None = None, session_device_uuid: str | None = None) -> FakeClient:
            client = self._client_holder.get("instance")
            if client is None:
                client = FakeClient(cookie, session_buvid=session_buvid, session_device_uuid=session_device_uuid)
                self._client_holder["instance"] = client
            return client

        watcher_module.BilibiliClient = factory

    def tearDown(self) -> None:
        watcher_module.BilibiliClient = self._orig_client
        watcher_module.CLAIM_SUBMIT_DELAY_SECONDS = self._orig_submit
        watcher_module.CLAIM_RATE_LIMIT_DELAY_SECONDS = self._orig_rate

    def test_heartbeat_workers_start_and_report_normal_status(self) -> None:
        logs: list[str] = []
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="23612045", watch_threads=3, check_interval=10), logs.append)
        room = RoomInfo(room_id=23612045, live_status=1, anchor_uid=12345, message="直播中")

        live_watcher._start_watch_threads(room)
        # 给心跳 worker 一点时间跑首轮
        deadline = time.time() + 3
        while time.time() < deadline:
            snapshot, _summary = live_watcher.get_watch_status_snapshot()
            if all(row.state == "正常" for row in snapshot):
                break
            time.sleep(0.05)
        live_watcher._stop.set()
        for thread in live_watcher._watch_threads:
            thread.join(timeout=2)

        snapshot, summary = live_watcher.get_watch_status_snapshot()
        self.assertEqual(len(snapshot), 3)
        self.assertTrue(all(row.state == "正常" for row in snapshot), f"snapshot={snapshot}")
        self.assertIn("3/3 正常", summary)
        client = self._client_holder["instance"]
        self.assertTrue(any(call.startswith("enter:") for call in client.heartbeat_calls))

    def test_claim_flow_submits_to_bilibili_when_task_completed(self) -> None:
        logs: list[str] = []
        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="23612045", watch_threads=1), logs.append)
        live_watcher._last_up_id = 12345
        live_watcher._claimable_task_ids.add("live-task-1")

        live_watcher._claim_completed_worker()

        client = self._client_holder["instance"]
        self.assertEqual(client.claim_calls, [(12345, "live-task-1")])
        self.assertTrue(any("开始领取奖励" in message for message in logs))
        self.assertTrue(any("已领取：" in message for message in logs))

    def test_rate_limit_triggers_retry_then_success(self) -> None:
        logs: list[str] = []

        class RateLimitedClient(FakeClient):
            def __init__(
                self,
                cookie: str = "a=b",
                *,
                session_buvid: str | None = None,
                session_device_uuid: str | None = None,
            ) -> None:
                super().__init__(cookie, session_buvid=session_buvid, session_device_uuid=session_device_uuid)
                self._attempts = 0

            def claim_user_task_rewards(self, up_id: int, task_id: str | None = None) -> dict:
                self._attempts += 1
                if self._attempts == 1:
                    raise RuntimeError("请求频率过高，请稍后再试")
                return super().claim_user_task_rewards(up_id, task_id)

        def factory(cookie: str, *, session_buvid: str | None = None, session_device_uuid: str | None = None) -> RateLimitedClient:
            client = self._client_holder.get("instance")
            if client is None:
                client = RateLimitedClient(cookie, session_buvid=session_buvid, session_device_uuid=session_device_uuid)
                self._client_holder["instance"] = client
            return client

        watcher_module.BilibiliClient = factory

        live_watcher = LiveWatcher(WatchOptions(cookie="a=b", room_id="23612045"), logs.append)
        live_watcher._last_up_id = 12345
        live_watcher._claimable_task_ids.add("live-task-1")
        live_watcher._wait_between_claims = lambda _seconds: None  # type: ignore[method-assign]

        live_watcher._claim_completed_worker()

        client = self._client_holder["instance"]
        self.assertEqual(len(client.claim_calls), 1)  # final success call
        self.assertEqual(client._attempts, 2)


if __name__ == "__main__":
    unittest.main()
