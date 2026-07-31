from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from .bilibili import BilibiliClient, RoomInfo, make_session_buvid, make_session_device_uuid
from .config import MAX_WATCH_THREADS


LogSink = Callable[[str], None]
CLAIM_SUBMIT_DELAY_SECONDS = 3.0
CLAIM_RATE_LIMIT_DELAY_SECONDS = 12.0
CLAIM_RATE_LIMIT_ATTEMPTS = 3
WATCH_START_BATCH_SIZE = 1
ACTIVITY_DISCOVERY_SUCCESS_TTL_SECONDS = 300.0
ACTIVITY_DISCOVERY_RETRY_TTL_SECONDS = 60.0
BILIBILI_TIMEZONE = timezone(timedelta(hours=8))


def _bilibili_today(now: datetime | None = None):
    """Return the calendar day used by Bilibili activities (UTC+8)."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(BILIBILI_TIMEZONE).date()


@dataclass
class WatchOptions:
    cookie: str
    room_id: str
    check_interval: int = 10
    auto_claim: bool = True
    task_ids: list[str] | None = None
    watch_threads: int = 1


@dataclass
class HeartbeatState:
    interval: int = 60
    ets: int = 0
    secret_key: str = ""
    secret_rule: list[int] | None = None


@dataclass
class WatchWorkerStatus:
    worker_id: int
    state: str
    interval: int | None
    message: str


class LiveWatcher:
    def __init__(self, options: WatchOptions, log: LogSink) -> None:
        self.options = options
        self.log = log
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._watch_threads: list[threading.Thread] = []
        self._room: RoomInfo | None = None
        self._claimed_markers: set[str] = set()
        self._claimable_task_ids: set[str] = set()
        self._known_task_ids: set[str] = set()
        # totalv2 必须查询父 task_id；mission/receive 必须提交 checkpoint sid。
        self._activity_task_ids: set[str] = set()
        self._activity_claim_task_ids: set[str] = set()
        self._activity_task_meta: dict[str, dict[str, Any]] = {}
        self._bilibili_time_offset_seconds: float | None = None
        self._claimable_general = False
        self._claim_lock = threading.Lock()
        self._claim_run_lock = threading.Lock()
        self._claim_thread: Optional[threading.Thread] = None
        self._claiming_markers: set[str] = set()
        self._general_claim_suppressed = False
        self._last_up_id: int | None = None
        self._watch_status_lock = threading.Lock()
        self._watch_statuses: dict[int, dict[str, Any]] = {}
        self._watch_worker_count = self._normalize_watch_threads(self.options.watch_threads)
        self._last_watch_status_summary = ""
        self._last_watch_status_log_at = 0.0
        self._heartbeat_count = 0
        self._watch_started_at = 0.0
        self._watch_stopped_at = 0.0
        self._last_detected_log_at = 0.0
        self._last_room_log_key = ""
        self._last_room_log_at = 0.0
        self._last_task_summary = ""
        self._last_task_summary_at = 0.0
        self._last_task_progress_score = 0.0
        self._last_task_progress_signature: tuple[str, ...] = ()
        self._last_task_waiting_log_at = 0.0
        self._manual_refresh_thread: Optional[threading.Thread] = None
        self._rediscover_thread: Optional[threading.Thread] = None
        self._next_activity_discovery_at = 0.0

    @property
    def running(self) -> bool:
        return bool(
            (self._thread is not None and self._thread.is_alive())
            or any(thread.is_alive() for thread in self._watch_threads)
        )

    def start(self) -> None:
        if self.running:
            self.log("已经在运行中")
            return
        self._stop.clear()
        with self._watch_status_lock:
            self._heartbeat_count = 0
            self._watch_started_at = 0.0
            self._watch_stopped_at = 0.0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._watch_status_lock:
            if self._watch_started_at and not self._watch_stopped_at:
                self._watch_stopped_at = time.time()
        self._stop.set()
        self.log("已请求停止")

    def claim_completed_tasks(self) -> None:
        if not self._start_claim_thread(log_if_running=True):
            return

    def refresh_progress_once(self) -> None:
        if self._manual_refresh_thread and self._manual_refresh_thread.is_alive():
            self.log("手动刷新正在进行中")
            return
        up_id = self._last_up_id
        if not up_id:
            self.log("尚未开始挂宝，暂时无法刷新进度")
            return
        self._manual_refresh_thread = threading.Thread(
            target=self._manual_refresh_worker, args=(up_id,), daemon=True
        )
        self._manual_refresh_thread.start()

    def _manual_refresh_worker(self, up_id: int) -> None:
        self.log("手动刷新进度")
        try:
            self._refresh_claimable_tasks(up_id)
        except Exception as exc:
            self.log(f"手动刷新失败：{self._friendly_error(exc)}")

    def rediscover_tasks_once(self) -> None:
        if self._rediscover_thread and self._rediscover_thread.is_alive():
            self.log("重新识别任务正在进行中")
            return
        self._rediscover_thread = threading.Thread(
            target=self._rediscover_tasks_worker, daemon=True
        )
        self._rediscover_thread.start()

    def _rediscover_tasks_worker(self) -> None:
        self.log("重新识别任务列表")
        with self._claim_lock:
            self._activity_task_ids.clear()
            self._activity_claim_task_ids.clear()
            self._activity_task_meta.clear()
            self._claimable_task_ids.clear()
        client: BilibiliClient | None = None
        try:
            client = BilibiliClient(self.options.cookie)
            task_ids = self._discover_activity_task_ids_if_due(
                client,
                announce_progress=True,
                force=True,
            )
            if task_ids:
                self._check_activity_task_progress(client)
        except Exception as exc:
            self.log(f"重新识别任务失败：{self._friendly_error(exc)}")
        finally:
            if client is not None:
                self._close_client(client)

    def _run(self) -> None:
        client: BilibiliClient | None = None
        try:
            client = BilibiliClient(self.options.cookie)
            try:
                login = client.check_login()
            except Exception as exc:
                self.log(f"登录状态检查失败：{self._friendly_error(exc)}")
            else:
                if not login.logged_in:
                    self.log(login.message)
                    return
                self.log(f"账号登录正常：{login.uname}（{login.mid}）")

            watch_started = False
            while not self._stop.is_set():
                try:
                    room = client.get_room_info(self.options.room_id)
                    self._room = room
                    self._last_up_id = room.anchor_uid or self._last_up_id
                    self._log_room(room)
                    if room.room_id and not watch_started:
                        self._start_watch_threads(room)
                        watch_started = True

                    if room.anchor_uid:
                        # 任务发现和 B 站真实分钟数必须始终刷新；auto_claim 只控制是否提交领取。
                        found_activity_claimable = self._check_activity_task_progress(client)
                        with self._claim_lock:
                            has_activity_tasks = bool(self._activity_task_ids)
                        found_live_claimable = (
                            False
                            if has_activity_tasks
                            else self._check_and_claim_task(client, room.anchor_uid)
                        )
                        found_explicit_claimable = self._check_explicit_task_ids(room.anchor_uid)
                        if self.options.auto_claim and (
                            found_live_claimable or found_activity_claimable or found_explicit_claimable
                        ):
                            self._start_auto_claim_thread()
                except Exception as exc:
                    self.log(f"守护循环异常：{exc}")

                self._stop.wait(max(10, int(self.options.check_interval or 10)))
        except Exception as exc:
            self.log(f"守护启动失败：{self._friendly_error(exc)}")
        finally:
            if client is not None:
                self._close_client(client)
            self.log("守护已停止")

    def _start_watch_threads(self, room: RoomInfo | None = None) -> None:
        worker_count = self._normalize_watch_threads(self.options.watch_threads)
        self._watch_worker_count = worker_count
        self._watch_threads = []
        with self._watch_status_lock:
            self._watch_statuses = {
                worker_id: {"state": "启动中", "updated_at": time.time(), "interval": None, "message": ""}
                for worker_id in range(1, worker_count + 1)
            }
            self._last_watch_status_summary = ""
        target_room = room or self._room
        for worker_id in range(1, worker_count + 1):
            thread = threading.Thread(target=self._heartbeat_watch_worker, args=(worker_id, target_room), daemon=True)
            self._watch_threads.append(thread)
            thread.start()
        self.log(f"已启动 {worker_count} 路后台计时，不会打开直播间浏览器窗口")
        self._log_watch_status_summary(force=True)

    def _watch_detail_enabled(self) -> bool:
        return self._watch_worker_count <= 5

    def _normalize_watch_threads(self, value: int) -> int:
        try:
            number = int(value or 1)
        except (TypeError, ValueError):
            number = 1
        return min(max(number, 1), MAX_WATCH_THREADS)

    def _set_watch_status(self, worker_id: int, state: str, *, interval: int | None = None, message: str = "") -> None:
        with self._watch_status_lock:
            self._watch_statuses[worker_id] = {
                "state": state,
                "updated_at": time.time(),
                "interval": interval,
                "message": message,
            }

    def _watch_status_summary_info(self) -> tuple[str, int, int]:
        with self._watch_status_lock:
            worker_count = self._watch_worker_count
            statuses = [self._watch_statuses.get(worker_id, {"state": "启动中"}) for worker_id in range(1, worker_count + 1)]
            heartbeat_count = self._heartbeat_count

        normal_count = sum(1 for status in statuses if status.get("state") == "正常")
        starting_count = sum(1 for status in statuses if status.get("state") in {"启动中", "计时中"})
        waiting_count = sum(1 for status in statuses if status.get("state") == "等待开播")
        failed_count = sum(1 for status in statuses if status.get("state") == "暂时失败")
        intervals = [
            int(status["interval"])
            for status in statuses
            if status.get("state") == "正常" and status.get("interval") is not None
        ]

        parts = [f"{normal_count}/{worker_count} 正常"]
        if starting_count:
            parts.append(f"{starting_count} 路启动中")
        if waiting_count:
            parts.append(f"{waiting_count} 路等待开播")
        if failed_count:
            parts.append(f"{failed_count} 路稍后重试")
        interval_text = ""
        if intervals:
            min_interval = min(intervals)
            max_interval = max(intervals)
            if min_interval == max_interval:
                interval_text = f"，下一次约 {min_interval} 秒后"
            else:
                interval_text = f"，下一次约 {min_interval}-{max_interval} 秒后"
        heartbeat_text = f"，心跳成功 {heartbeat_count} 次" if heartbeat_count else ""
        return f"观看连接：{'，'.join(parts)}{interval_text}{heartbeat_text}", normal_count, failed_count + waiting_count

    def _log_watch_status_summary(self, *, force: bool = False) -> None:
        summary, _normal_count, problem_count = self._watch_status_summary_info()
        # 后台计时状态实时显示在右侧「后台计时状态」卡；运行日志只在出问题（有路重试/等待）时提醒，
        # 正常运行不刷屏，让运行日志专注任务进度和领取结果。
        if not force and problem_count == 0:
            return
        now = time.time()
        if force or (summary != self._last_watch_status_summary and now - self._last_watch_status_log_at >= 15):
            self._last_watch_status_summary = summary
            self._last_watch_status_log_at = now
            self.log(summary)

    def get_watch_status_snapshot(self) -> tuple[list["WatchWorkerStatus"], str]:
        with self._watch_status_lock:
            worker_count = self._watch_worker_count
            statuses = [
                (worker_id, dict(self._watch_statuses.get(worker_id, {"state": "启动中"})))
                for worker_id in range(1, worker_count + 1)
            ]
        rows: list[WatchWorkerStatus] = []
        for worker_id, status in statuses:
            interval_value = status.get("interval")
            try:
                interval = int(interval_value) if interval_value is not None else None
            except (TypeError, ValueError):
                interval = None
            rows.append(
                WatchWorkerStatus(
                    worker_id=worker_id,
                    state=str(status.get("state") or "启动中"),
                    interval=interval,
                    message=str(status.get("message") or ""),
                )
            )
        summary, _normal, _problem = self._watch_status_summary_info()
        return rows, summary

    def _log_room(self, room: RoomInfo) -> None:
        if not room.room_id:
            self.log(room.message)
            return
        anchor = f"｜主播 {room.anchor}" if room.anchor else ""
        message = f"房间 {room.room_id}：{room.message}｜{room.title}{anchor}｜人气 {room.online}"
        key = f"{room.room_id}|{room.live_status}|{room.title}|{room.anchor}"
        now = time.time()
        if key != self._last_room_log_key or now - self._last_room_log_at >= 60:
            self._last_room_log_key = key
            self._last_room_log_at = now
            self.log(message)

    def _watch_start_delay(self, worker_id: int) -> float:
        """错峰启动：默认每秒只起 1 路，避免大量心跳同时进入后被合并或限频。"""
        if worker_id <= 1:
            return 0.0
        return float((worker_id - 1) // WATCH_START_BATCH_SIZE)

    def _record_heartbeat(self, interval: int | float | None = None) -> None:
        """Record connection health only; accepted HTTP heartbeats are not credited minutes."""
        with self._watch_status_lock:
            if not self._watch_started_at:
                self._watch_started_at = time.time()
            self._heartbeat_count += 1

    def get_local_watch_estimate_minutes(self) -> float:
        """Actual local wall-clock time since the first successful watch heartbeat."""
        return self._watch_elapsed_minutes()

    def _heartbeat_watch_worker(self, worker_id: int, room: RoomInfo | None) -> None:
        # 先确定本会话的设备身份并构造客户端，让每个 worker 都有独立 cookie 和 buvid。
        try:
            client = BilibiliClient(
                self.options.cookie,
                session_buvid=make_session_buvid(),
                session_device_uuid=make_session_device_uuid(),
            )
        except Exception as exc:
            self._set_watch_status(worker_id, "暂时失败", message=self._friendly_error(exc))
            self.log(f"后台计时 {worker_id} 启动失败：{self._friendly_error(exc)}")
            return
        try:
            # 分批错峰启动，避免短时间内大量心跳被 B 站频控拦截（详见 _watch_start_delay）。
            start_delay = self._watch_start_delay(worker_id)
            if start_delay > 0:
                self._stop.wait(start_delay)
            if self._stop.is_set():
                return
            state = HeartbeatState()
            current_room = room
            while not self._stop.is_set():
                try:
                    current_room = self._resolve_heartbeat_room(client, current_room)
                    if not current_room.room_id:
                        self._set_watch_status(worker_id, "暂时失败", message=current_room.message)
                        self.log(f"后台计时 {worker_id} 暂停：{current_room.message}")
                        self._log_watch_status_summary()
                        self._stop.wait(20)
                        continue
                    if current_room.live_status != 1:
                        self._set_watch_status(worker_id, "等待开播", message=f"房间 {current_room.room_id} 当前未开播")
                        if self._watch_detail_enabled():
                            self.log(f"后台计时 {worker_id} 等待开播：房间 {current_room.room_id} 当前未开播")
                        self._log_watch_status_summary()
                        self._stop.wait(30)
                        continue

                    self._set_watch_status(worker_id, "计时中", message=f"已进入房间 {current_room.room_id}")
                    if self._watch_detail_enabled():
                        self.log(f"后台计时 {worker_id} 已进入房间 {current_room.room_id}，正在提交观看计时")

                    sequence = 1
                    state = self._start_heartbeat_session(client, current_room, state)
                    self._set_watch_status(worker_id, "正常", interval=state.interval, message="首次计时请求已提交")
                    self._record_heartbeat(state.interval)
                    if self._watch_detail_enabled():
                        self.log(f"后台计时 {worker_id} 首次计时请求成功，下一次约 {state.interval} 秒后")
                    self._log_watch_status_summary()
                    self._stop.wait(state.interval)

                    while not self._stop.is_set():
                        latest_room = self._latest_room_snapshot(current_room)
                        if latest_room.live_status != 1:
                            current_room = latest_room
                            break
                        current_room = latest_room
                        state = self._continue_heartbeat_session(client, current_room, sequence, state)
                        sequence += 1
                        self._set_watch_status(worker_id, "正常", interval=state.interval)
                        self._record_heartbeat(state.interval)
                        if self._watch_detail_enabled():
                            self.log(f"后台计时 {worker_id} 计时请求已提交，下一次约 {state.interval} 秒后")
                        self._log_watch_status_summary()
                        self._stop.wait(state.interval)
                except Exception as exc:
                    self._set_watch_status(worker_id, "暂时失败", message=self._friendly_error(exc))
                    if self._watch_detail_enabled():
                        self.log(f"后台计时 {worker_id} 暂时失败：{self._friendly_error(exc)}；稍后重试")
                    self._log_watch_status_summary()
                    self._stop.wait(15)
        finally:
            self._close_client(client)

    def _resolve_heartbeat_room(self, client: BilibiliClient, room: RoomInfo | None) -> RoomInfo:
        latest = self._latest_room_snapshot(room)
        if latest and latest.room_id:
            return latest
        return client.get_room_info(self.options.room_id)

    def _latest_room_snapshot(self, fallback: RoomInfo | None) -> RoomInfo:
        latest = self._room
        if latest and latest.room_id and (fallback is None or latest.room_id == fallback.room_id):
            return latest
        return fallback or RoomInfo(room_id=0)

    def _extract_heartbeat_state(self, data: dict[str, Any], fallback: HeartbeatState | None = None) -> HeartbeatState:
        fallback = fallback or HeartbeatState()
        raw_rule = data.get("secret_rule") or data.get("secretRule") or fallback.secret_rule or []
        if isinstance(raw_rule, str):
            try:
                raw_rule = [int(item) for item in raw_rule.replace("[", "").replace("]", "").split(",") if item.strip()]
            except ValueError:
                raw_rule = fallback.secret_rule or []
        elif isinstance(raw_rule, list):
            raw_rule = [int(item) for item in raw_rule if str(item).strip()]
        else:
            raw_rule = fallback.secret_rule or []

        interval = data.get("heartbeat_interval") or data.get("interval") or data.get("time") or fallback.interval
        ets = data.get("timestamp") or data.get("ets") or fallback.ets or int(time.time())
        return HeartbeatState(
            interval=max(10, int(interval or fallback.interval or 60)),
            ets=int(ets or fallback.ets or time.time()),
            secret_key=str(data.get("secret_key") or data.get("secretKey") or fallback.secret_key or ""),
            secret_rule=raw_rule,
        )

    def _start_heartbeat_session(self, client: BilibiliClient, room: RoomInfo, fallback: HeartbeatState) -> HeartbeatState:
        # 先注册"进入直播间"动作，B 站才会把后续 x25Kn 心跳算到当前会话上。
        # 缺这一步是 v0.4.1 多路计时仍然只算一路的关键原因。
        # 失败不致命：网络抖动/代理偶尔断时跳过，让 x25Kn 心跳继续跑。
        # 不在这里写日志，否则 20 路每次失败都会刷屏；用全局静默计数代替。
        try:
            client.room_entry_action(room)
        except Exception:
            self._record_room_entry_failure()
        data = client.enter_room_heartbeat(room)
        return self._extract_heartbeat_state(data, fallback)

    def _record_room_entry_failure(self) -> None:
        # 每 N 次失败汇总写一条日志，避免 20 路同时报错刷屏。
        with self._watch_status_lock:
            count = getattr(self, "_room_entry_fail_count", 0) + 1
            self._room_entry_fail_count = count
        if count == 1 or count % 50 == 0:
            self.log(f"上报进入直播间累计失败 {count} 次（不影响心跳，可能是代理抖动）")

    def _continue_heartbeat_session(
        self,
        client: BilibiliClient,
        room: RoomInfo,
        sequence: int,
        state: HeartbeatState,
    ) -> HeartbeatState:
        if state.secret_key and state.secret_rule:
            data = client.in_room_heartbeat(
                room,
                sequence=sequence,
                interval=state.interval,
                ets=state.ets,
                secret_key=state.secret_key,
                secret_rule=state.secret_rule,
            )
            return self._extract_heartbeat_state(data, state)

        raise RuntimeError("x25Kn 会话缺少签名参数，需要重新建立计时会话")

    def _start_auto_claim_thread(self) -> None:
        self._start_claim_thread(log_if_running=False)

    def _start_claim_thread(self, *, log_if_running: bool) -> bool:
        with self._claim_run_lock:
            if self._claim_thread and self._claim_thread.is_alive():
                if log_if_running:
                    self.log("领取线程正在运行中")
                return False
            self._claim_thread = threading.Thread(target=self._claim_completed_worker, daemon=True)
            self._claim_thread.start()
            return True

    def _check_and_claim_task(self, client: BilibiliClient, up_id: int) -> bool:
        try:
            progress = client.get_user_task_progress(up_id)
        except Exception as exc:
            self.log(f"掉宝任务进度检查失败：{exc}")
            return False

        return self._record_task_progress(progress, announce_claimable=True)

    def _check_activity_task_progress(self, client: BilibiliClient) -> bool:
        self._discover_activity_task_ids_if_due(client, announce_progress=False)
        with self._claim_lock:
            if self._activity_task_ids:
                task_ids = self._merge_task_ids([], self._activity_task_ids)
            else:
                task_ids = self._merge_task_ids(self.options.task_ids or [], set())
        if not task_ids:
            return False
        try:
            progress = client.get_activity_task_progress(task_ids)
        except Exception as exc:
            self.log(f"活动任务进度检查失败：{exc}")
            return False
        self._enrich_activity_progress(progress)
        self._remember_activity_progress_source(progress, task_ids)
        return self._record_task_progress(progress, announce_claimable=True)

    def _discover_activity_task_ids_if_due(
        self,
        client: BilibiliClient,
        *,
        announce_progress: bool,
        force: bool = False,
    ) -> list[str]:
        """按 TTL 刷新公共活动配置，避免每个进度周期重复下载直播页。"""
        now = time.monotonic()
        with self._claim_lock:
            if not force and now < self._next_activity_discovery_at:
                return sorted(self._activity_task_ids)
            # 先占住刷新窗口，防止手动刷新与守护循环同时抓取同一直播页。
            self._next_activity_discovery_at = now + ACTIVITY_DISCOVERY_RETRY_TTL_SECONDS

        task_ids = self._discover_activity_task_ids(client, announce_progress=announce_progress)
        ttl = (
            ACTIVITY_DISCOVERY_SUCCESS_TTL_SECONDS
            if task_ids
            else ACTIVITY_DISCOVERY_RETRY_TTL_SECONDS
        )
        with self._claim_lock:
            self._next_activity_discovery_at = time.monotonic() + ttl
        return task_ids

    @staticmethod
    def _close_client(client: object) -> None:
        close = getattr(client, "close", None)
        if not callable(close):
            return
        try:
            close()
        except Exception:
            pass

    def _remember_activity_progress_source(self, progress: dict[str, Any], queried_task_ids: list[str]) -> None:
        tracking_task_ids = self._activity_tracking_task_ids(progress) or queried_task_ids
        claim_task_ids = self._discover_task_ids(progress)
        if not tracking_task_ids and not claim_task_ids:
            return
        with self._claim_lock:
            self._activity_task_ids.update(tracking_task_ids)
            self._activity_claim_task_ids.update(claim_task_ids)
            for task_id in claim_task_ids:
                self._activity_task_meta.setdefault(task_id, {})

    def _discover_activity_task_ids(self, client: BilibiliClient, announce_progress: bool = True) -> list[str]:
        try:
            progress = client.discover_live_activity_tasks(self.options.room_id)
        except Exception as exc:
            if self._is_no_activity_task_error(exc):
                self.log(f"当前直播页暂时没有读到可跟踪的掉宝任务：{self._friendly_error(exc)}")
            else:
                self.log(f"没有读到活动任务列表，稍后会自动再试：{self._friendly_error(exc)}")
            return []
        claim_task_ids = self._discover_task_ids(progress)
        task_ids = self._activity_tracking_task_ids(progress) or claim_task_ids
        if not task_ids:
            self._clear_activity_task_cache()
            self.log("当前直播页暂时没有读到可跟踪的掉宝任务，稍后会自动再试")
            return []
        try:
            server_time = float(progress.get("server_time"))
        except (TypeError, ValueError):
            server_time = None
        if server_time is not None:
            first_server_time_sync = self._bilibili_time_offset_seconds is None
            self._bilibili_time_offset_seconds = server_time - time.time()
            if first_server_time_sync:
                server_datetime = datetime.fromtimestamp(server_time, tz=BILIBILI_TIMEZONE)
                self.log(
                    "B站时间已同步："
                    f"{server_datetime.month}月{server_datetime.day}日 "
                    f"{server_datetime:%H:%M:%S}，任务日期按活动有效统计时间判断"
                )
        with self._claim_lock:
            current_task_ids = set(task_ids)
            previous_task_ids = set(self._activity_task_ids)
            new_task_ids = [task_id for task_id in task_ids if task_id not in previous_task_ids]
            stale_task_ids = sorted(previous_task_ids - current_task_ids)
            current_claim_task_ids = set(claim_task_ids)
            previous_claim_task_ids = set(self._activity_claim_task_ids)
            stale_claim_task_ids = previous_claim_task_ids - current_claim_task_ids
            self._activity_task_ids = current_task_ids
            self._activity_claim_task_ids = current_claim_task_ids
            self._known_task_ids.update(current_claim_task_ids)
            self._claimable_task_ids.difference_update(set(stale_task_ids) | stale_claim_task_ids)
            next_meta: dict[str, dict[str, Any]] = {}
            for node in self._iter_task_nodes(progress):
                task_id = self._task_id_from_node(node)
                if task_id:
                    next_meta[task_id] = {
                        "group_label": node.get("group_label") or "",
                        "group_index": node.get("group_index"),
                        "task_name": node.get("task_name") or node.get("name") or node.get("title") or "",
                        "award_name": node.get("award_name") or "",
                        "active_start": node.get("active_start"),
                        "active_end": node.get("active_end"),
                    }
            self._activity_task_meta = next_meta
        if new_task_ids:
            self.log("已找到本次活动任务，会自动显示本次可挂的奖励进度")
        if stale_task_ids:
            self.log("活动任务已更新，已同步最新任务列表")
        # 页面 bootstrap 只用于识别拓扑和文案；可领取状态以 totalv2 的实时响应为准，
        # 避免旧 HTML 中的 status=2 把过期奖励提前塞进领取队列。
        return task_ids

    def _clear_activity_task_cache(self) -> None:
        with self._claim_lock:
            stale_task_ids = (
                set(self._activity_task_ids)
                | set(self._activity_claim_task_ids)
                | set(self._activity_task_meta)
            )
            if not stale_task_ids:
                return
            self._activity_task_ids.clear()
            self._activity_claim_task_ids.clear()
            self._activity_task_meta.clear()
            self._claimable_task_ids.difference_update(stale_task_ids)
            self._last_task_summary = ""
            self._last_task_progress_score = 0.0
            self._last_task_progress_signature = ()
        self.log("当前直播页没有本次活动任务，已清空旧任务缓存")

    def _is_no_activity_task_error(self, exc: Exception) -> bool:
        text = str(exc)
        return (
            "直播间号格式不正确" in text
            or "直播页没有找到活动任务" in text
            or "直播间页面有本次掉宝任务" in text
        )

    def _enrich_activity_progress(self, progress: dict[str, Any]) -> None:
        with self._claim_lock:
            meta_by_task_id = {task_id: dict(meta) for task_id, meta in self._activity_task_meta.items()}
        if not meta_by_task_id:
            return
        for node in self._iter_task_nodes(progress):
            task_id = self._task_id_from_node(node)
            meta = meta_by_task_id.get(task_id)
            if not meta:
                continue
            if meta.get("group_label"):
                if not node.get("group_label"):
                    node["group_label"] = meta["group_label"]
            if meta.get("group_index") is not None:
                if node.get("group_index") is None:
                    node["group_index"] = meta["group_index"]
            if meta.get("award_name"):
                if not node.get("award_name"):
                    node["award_name"] = meta["award_name"]
            if meta.get("task_name"):
                if not node.get("task_name"):
                    node["task_name"] = meta["task_name"]
            if meta.get("active_start") is not None and node.get("active_start") is None:
                node["active_start"] = meta["active_start"]
            if meta.get("active_end") is not None and node.get("active_end") is None:
                node["active_end"] = meta["active_end"]

    def _record_task_progress(self, progress: dict[str, Any], announce_claimable: bool) -> bool:
        summary = self._summarize_task(progress)
        now = time.time()
        claimable_tasks = self._find_claimable_task_refs(progress)
        task_nodes = self._iter_task_nodes(progress)
        if summary:
            progress_score = self._task_summary_progress_score(progress)
            progress_signature = self._task_progress_signature(progress)
            if progress_signature != self._last_task_progress_signature:
                self._last_task_progress_signature = progress_signature
                self._last_task_progress_score = 0.0
            progress_went_backwards = (
                bool(progress_signature)
                and self._last_task_progress_score > 0
                and progress_score < self._last_task_progress_score
                and not claimable_tasks
            )
            if progress_went_backwards:
                self._log_task_waiting_progress("B 站进度暂时延迟，继续保留上次已确认分钟数")
            elif self._should_defer_zero_task_summary(progress, claimable_tasks, progress_score, now):
                detected_summary = self._summarize_detected_tasks(progress)
                if detected_summary and detected_summary != self._last_task_summary:
                    self._last_task_summary = detected_summary
                    self._last_task_summary_at = now
                    self._last_detected_log_at = now
                    self._log_task_waiting_progress("活动任务已识别，等待 B 站返回真实进度")
                    self.log(f"掉宝任务：\n{detected_summary}")
            elif summary != self._last_task_summary:
                self._last_task_summary = summary
                self._last_task_summary_at = now
                self._last_task_progress_score = progress_score
                self.log(f"掉宝任务：\n{summary}")

        discovered_task_ids = self._discover_task_ids(progress)
        if discovered_task_ids:
            with self._claim_lock:
                new_task_ids = [task_id for task_id in discovered_task_ids if task_id not in self._known_task_ids]
                self._known_task_ids.update(discovered_task_ids)
            if new_task_ids:
                self.log("已自动找到任务列表，无需手动填写")

        with self._claim_lock:
            previous_claimable_ids = set(self._claimable_task_ids)
            previous_claimable_general = self._claimable_general
            claimable_ids = {task_id for _name, task_id in claimable_tasks if task_id}
            if self._last_up_id:
                claimable_ids = {
                    task_id
                    for task_id in claimable_ids
                    if f"{self._last_up_id}:{task_id}" not in self._claimed_markers
                }
            # 只清理由本次响应明确出现的任务，避免一个接口的空/局部响应误删
            # 另一个接口刚识别出的可领取任务。
            self._claimable_task_ids.difference_update(set(discovered_task_ids) - claimable_ids)
            self._claimable_task_ids.update(claimable_ids)

            general_nodes = [node for node in task_nodes if not self._task_id_from_node(node)]
            general_claimable = any(self._node_claimable(node) for node in general_nodes)
            if general_nodes and not general_claimable:
                self._claimable_general = False
                self._general_claim_suppressed = False
            elif general_claimable and not self._general_claim_suppressed:
                self._claimable_general = True

            pending_named_tasks = [
                name for name, task_id in claimable_tasks
                if (task_id and task_id in claimable_ids)
                or (not task_id and self._claimable_general)
            ]
            newly_claimable_names = [
                name for name, task_id in claimable_tasks
                if (task_id and task_id in claimable_ids and task_id not in previous_claimable_ids)
                or (not task_id and self._claimable_general and not previous_claimable_general)
            ]

        if not pending_named_tasks:
            return False

        if announce_claimable and newly_claimable_names:
            self.log(f"检测到 {len(newly_claimable_names)} 个奖励可以领取，正在排队领取")
        return True

    def _log_task_waiting_progress(self, message: str, *, min_interval: float = 30.0) -> None:
        now = time.time()
        if now - self._last_task_waiting_log_at < min_interval:
            return
        self._last_task_waiting_log_at = now
        self.log(message)

    def _should_defer_zero_task_summary(
        self,
        progress: dict[str, Any],
        claimable_tasks: list[tuple[str, str]],
        progress_score: float,
        now: float,
    ) -> bool:
        """B 站分钟数接口经常空返回；只要还没有当前分钟数、也没有可领取任务，
        就先展示已识别任务，等待接口返回真实进度。"""
        if claimable_tasks or progress_score > 0:
            return False
        return self._task_summary_visible_count(progress) >= 1

    def _task_summary_visible_count(self, progress: dict[str, Any]) -> int:
        nodes = sorted(self._iter_task_nodes(progress), key=self._task_sort_key)
        nodes, _group_label, _hidden_count = self._focus_task_nodes(nodes)
        return sum(1 for node in nodes if not self._skip_task_summary_node(node))

    def _task_summary_progress_score(self, progress: dict[str, Any]) -> float:
        score = 0.0
        nodes = sorted(self._iter_task_nodes(progress), key=self._task_sort_key)
        nodes, _group_label, _hidden_count = self._focus_task_nodes(nodes)
        for node in nodes:
            if self._skip_task_summary_node(node):
                continue
            current, _target = self._task_progress_values(node)
            try:
                score = max(score, float(current))
            except (TypeError, ValueError):
                continue
        return score

    def _task_progress_signature(self, progress: dict[str, Any]) -> tuple[str, ...]:
        nodes = sorted(self._iter_task_nodes(progress), key=self._task_sort_key)
        nodes, group_label, _hidden_count = self._focus_task_nodes(nodes)
        identities = [
            self._task_id_from_node(node) or self._task_display_name(node)
            for node in nodes
            if not self._skip_task_summary_node(node)
        ]
        return tuple([group_label, *identities])

    def _watch_elapsed_minutes(self) -> float:
        """本地首个成功心跳后的实际墙钟时长（分钟），停止后冻结。"""
        with self._watch_status_lock:
            started_at = self._watch_started_at
            stopped_at = self._watch_stopped_at
        if not started_at:
            return 0.0
        ended_at = stopped_at or time.time()
        return max(0.0, (ended_at - started_at) / 60.0)

    def _local_task_status_text(self, node: dict[str, Any], target_value: float) -> str:
        if self._node_received(node):
            return "✓ 已领取"
        if self._node_claimable(node):
            return "✓ 已完成，待领取"
        if target_value <= 0:
            return "等待 B 站返回目标分钟数"
        return "等待 B 站返回真实进度"

    def _summarize_detected_tasks(self, progress: dict[str, Any]) -> str:
        nodes = sorted(self._iter_task_nodes(progress), key=self._task_sort_key)
        nodes, group_label, hidden_count = self._focus_task_nodes(nodes)
        lines: list[str] = []
        for node in nodes:
            if self._skip_task_summary_node(node):
                continue
            name = self._task_display_name(node)
            _current, target = self._task_progress_values(node)
            try:
                target_value = float(target)
            except (TypeError, ValueError):
                target_value = 0.0
            status = self._local_task_status_text(node, target_value)
            if target_value > 0:
                lines.append(f"{name}（目标 {self._format_progress_value(target_value)} 分钟）：{status}")
            else:
                lines.append(f"{name}：{status}")
        if not lines:
            return ""
        header_main = "任务已识别，等待 B 站返回真实进度"
        if group_label:
            hidden_note = f"，已隐藏其他日期 {hidden_count} 个任务" if hidden_count else ""
            header = f"{header_main}，{group_label}，共 {len(lines)} 个奖励{hidden_note}"
        else:
            header = f"{header_main}，共 {len(lines)} 个奖励"
        return "\n".join([header, *lines])

    def _check_explicit_task_ids(self, up_id: int) -> bool:
        with self._claim_lock:
            task_ids = self._merge_task_ids(self.options.task_ids or [], set())
        if not task_ids:
            return False
        with self._claim_lock:
            if self._claimable_task_ids:
                self.log(f"已有 {len(self._claimable_task_ids)} 个任务完成，等待点击领取奖励，暂停新的并发检查")
                return True

        stop_scan = threading.Event()
        self.log(f"正在检查你手动填写的 {len(task_ids)} 个任务")
        found_claimable = False
        for task_id in task_ids:
            if stop_scan.is_set():
                self.log("已识别到完成任务，停止剩余任务检查，等待点击领取奖励")
                break
            try:
                result = self._check_one_explicit_task(up_id, task_id, stop_scan)
            except Exception as exc:
                self.log(f"手动任务检查失败：{self._friendly_error(exc)}")
                continue
            if result:
                self.log(result)
                if stop_scan.is_set():
                    found_claimable = True
        return found_claimable

    def _check_one_explicit_task(self, up_id: int, task_id: str, stop_scan: threading.Event) -> str:
        if stop_scan.is_set():
            return ""
        client = BilibiliClient(self.options.cookie)
        try:
            progress = client.get_user_task_progress(up_id, task_id=task_id)
            claimable_tasks = self._find_claimable_task_refs(progress)
            if not claimable_tasks:
                with self._claim_lock:
                    self._claimable_task_ids.discard(task_id)
                return "手动任务尚未完成"
            with self._claim_lock:
                for _name, found_task_id in claimable_tasks:
                    self._claimable_task_ids.add(found_task_id or task_id)
            stop_scan.set()
            return f"手动任务已完成：{', '.join(name for name, _task_id in claimable_tasks)}"
        finally:
            self._close_client(client)

    def _claim_completed_worker(self) -> None:
        try:
            self._claim_completed_worker_impl()
        except Exception as exc:
            self.log(f"领取失败：{self._friendly_error(exc)}")

    def _claim_completed_worker_impl(self) -> None:
        up_id = self._resolve_up_id()
        if not up_id:
            self.log("缺少主播 UID，暂时无法领取")
            return
        self._refresh_claimable_tasks(up_id)
        with self._claim_lock:
            task_ids = sorted(self._claimable_task_ids)
            claim_general = self._claimable_general
        if not task_ids and not claim_general:
            self.log("已刷新任务进度，但仍未检测到可领取任务；如果 B 站页面显示已完成，请稍后再点领取")
            return
        self.log("开始领取奖励：会按顺序一个一个领取，避免太快导致失败")

        if claim_general and not task_ids:
            client = BilibiliClient(self.options.cookie)
            try:
                client.claim_user_task_rewards(up_id)
                with self._claim_lock:
                    self._claimable_general = False
                    self._general_claim_suppressed = True
                self.log("已领取：已完成的通用奖励")
            except Exception as exc:
                self.log(f"领取失败：通用奖励：{self._friendly_error(exc)}")
            finally:
                self._close_client(client)
            return

        for index, task_id in enumerate(task_ids):
            if self._stop.is_set():
                self.log("已停止领取，剩余奖励下次可继续领取")
                break
            label = self._claim_task_label(task_id)
            self.log(f"正在领取：{label}")
            try:
                self.log(self._claim_one_task(up_id, task_id))
            except Exception as exc:
                self.log(f"领取失败：{label}：{self._friendly_error(exc)}")
            if index < len(task_ids) - 1:
                self._wait_between_claims(CLAIM_SUBMIT_DELAY_SECONDS)

    def _claim_one_task(self, up_id: int, task_id: str) -> str:
        marker = f"{up_id}:{task_id}"
        with self._claim_lock:
            already_claimed = marker in self._claimed_markers
            is_activity_task = task_id in self._activity_claim_task_ids
            already_claiming = marker in self._claiming_markers
            if already_claimed:
                self._claimable_task_ids.discard(task_id)
            elif not already_claiming:
                self._claiming_markers.add(marker)
        if already_claimed:
            return f"已跳过：{self._claim_task_label(task_id)} 已经领取过"
        if already_claiming:
            return f"已跳过：{self._claim_task_label(task_id)} 正在领取"
        label = self._claim_task_label(task_id)
        already_received = False
        client: BilibiliClient | None = None
        try:
            client = BilibiliClient(self.options.cookie)
            if is_activity_task:
                self._claim_with_retry(lambda: client.claim_activity_mission_reward(task_id), label)
            else:
                self._claim_with_retry(lambda: client.claim_user_task_rewards(up_id, task_id=task_id), label)
        except Exception as exc:
            # B 站对已领取的奖励返回“任务奖励已经领取”(code 202031)。totalv2 偶尔仍把
            # 这种奖励报成“可领取”，于是我们会重复发起领取。这不是失败：奖励已经到手，
            # 当成成功并标记已领，避免反复重试、避免把“已领取”误报成“领取失败”。
            if not self._is_already_claimed_error(exc):
                raise
            already_received = True
        finally:
            if client is not None:
                self._close_client(client)
            with self._claim_lock:
                self._claiming_markers.discard(marker)
        with self._claim_lock:
            self._claimed_markers.add(marker)
            self._claimable_task_ids.discard(task_id)
        if already_received:
            return f"已领取：{label}（此前已领取）"
        return f"已领取：{label}"

    def _claim_with_retry(self, submit: Callable[[], Any], label: str) -> None:
        for attempt in range(1, CLAIM_RATE_LIMIT_ATTEMPTS + 1):
            try:
                submit()
                return
            except Exception as exc:
                if not self._is_rate_limited_error(exc) or attempt >= CLAIM_RATE_LIMIT_ATTEMPTS:
                    raise
                self.log(f"B 站提示操作太快，{int(CLAIM_RATE_LIMIT_DELAY_SECONDS)} 秒后自动重试：{label}")
                self._wait_between_claims(CLAIM_RATE_LIMIT_DELAY_SECONDS)
                if self._stop.is_set():
                    raise RuntimeError("已停止，未继续领取")

    def _is_rate_limited_error(self, exc: Exception) -> bool:
        text = str(exc)
        return "请求频率过高" in text or "频率" in text or "稍后再试" in text or "操作太快" in text

    def _is_already_claimed_error(self, exc: Exception) -> bool:
        # 只认 B 站“已经领取/重复领取”这类明确文案；不要用裸“已领取”，
        # 否则像“请确认是否已领取”这种失败文案会被误判成已领取而丢弃任务。
        text = str(exc)
        return "已经领取" in text or "重复领取" in text

    def _friendly_error(self, exc: Exception) -> str:
        text = str(exc)
        if "csrf" in text.lower() or "bili_jct" in text:
            return "登录信息已过期或不完整，请重新获取 Cookie 后再试"
        if self._is_rate_limited_error(exc):
            return "B 站提示操作太快，请稍后再试"
        if "未登录" in text or "登录" in text:
            return "登录状态失效，请重新获取 Cookie"
        if "timeout" in text.lower() or "timed out" in text.lower() or "超时" in text:
            return "网络超时，程序稍后会自动重试"
        return text

    def _wait_between_claims(self, seconds: float) -> None:
        if seconds <= 0:
            return
        self._stop.wait(seconds)

    def _claim_task_label(self, task_id: str) -> str:
        with self._claim_lock:
            meta = dict(self._activity_task_meta.get(task_id) or {})
            is_activity_task = task_id in self._activity_claim_task_ids
        if not is_activity_task:
            return "手动填写的任务"
        parts = [
            str(meta.get("group_label") or "").strip(),
            str(meta.get("task_name") or "").strip(),
            str(meta.get("award_name") or "").strip(),
        ]
        label = "｜".join(part for part in parts if part)
        return label or "活动任务"

    def _resolve_up_id(self) -> int | None:
        if self._last_up_id:
            return self._last_up_id
        client = BilibiliClient(self.options.cookie)
        try:
            room = client.get_room_info(self.options.room_id)
        finally:
            self._close_client(client)
        self._room = room
        self._last_up_id = room.anchor_uid or self._last_up_id
        if room.room_id:
            self._log_room(room)
        return self._last_up_id

    def _refresh_claimable_tasks(self, up_id: int) -> None:
        self.log("领取前刷新任务进度")
        client = BilibiliClient(self.options.cookie)
        try:
            try:
                progress = client.get_user_task_progress(up_id)
            except Exception as exc:
                self.log(f"领取前刷新任务进度失败：{exc}")
            else:
                self._record_task_progress(progress, announce_claimable=False)
            self._check_activity_task_progress(client)
        finally:
            self._close_client(client)

        with self._claim_lock:
            task_ids = self._merge_task_ids(self.options.task_ids or [], set())
        for task_id in task_ids:
            try:
                self._check_one_explicit_task(up_id, task_id, threading.Event())
            except Exception as exc:
                self.log(f"领取前检查手动任务失败：{self._friendly_error(exc)}")

    def _summarize_task(self, progress: dict[str, Any]) -> str:
        text_parts: list[str] = []
        nodes = sorted(self._iter_task_nodes(progress), key=self._task_sort_key)
        nodes, group_label, hidden_count = self._focus_task_nodes(nodes)
        received_summary = self._summarize_all_received_tasks(nodes, group_label, hidden_count)
        if received_summary:
            return received_summary
        compact_summary = self._summarize_task_steps(nodes, group_label, hidden_count)
        if compact_summary:
            return compact_summary
        for node in nodes:
            if self._skip_task_summary_node(node):
                continue
            name = self._task_display_name(node)
            current, target = self._task_progress_values(node)
            status_text = self._task_status_text(node, current, target)
            if current is not None and target is not None:
                text_parts.append(f"{name}：{self._format_progress_value(current)}/{self._format_progress_value(target)} 分钟，{status_text}")
            elif status_text:
                text_parts.append(f"{name}：{status_text}")
        text_parts = [part for part in text_parts if part]
        if not text_parts:
            return ""
        if group_label:
            header = self._task_group_summary_header(nodes, group_label, len(text_parts), hidden_count)
            return "\n".join([header, *text_parts])
        return "\n".join(text_parts)

    def _summarize_all_received_tasks(self, nodes: list[dict[str, Any]], group_label: str, hidden_count: int) -> str:
        visible_nodes = [node for node in nodes if not self._skip_empty_placeholder_node(node)]
        if not visible_nodes or any(not self._node_received(node) for node in visible_nodes):
            return ""
        hidden_note = f"，已隐藏其他日期 {hidden_count} 个任务" if hidden_count else ""
        if group_label:
            display_label = self._task_group_display_label(nodes, group_label)
            return f"全部奖励已领取：{display_label}，共 {len(visible_nodes)} 个奖励{hidden_note}"
        return f"全部奖励已领取：共 {len(visible_nodes)} 个奖励"

    def _summarize_task_steps(self, nodes: list[dict[str, Any]], group_label: str, hidden_count: int) -> str:
        step_nodes: list[tuple[dict[str, Any], float, float]] = []
        base_names: list[str] = []
        for node in nodes:
            if self._skip_task_summary_node(node):
                continue
            current, target = self._task_progress_values(node)
            try:
                current_value = float(current)
                target_value = float(target)
            except (TypeError, ValueError):
                return ""
            if target_value <= 0:
                return ""
            base_name = self._task_base_name(node)
            if not base_name:
                return ""
            step_nodes.append((node, current_value, target_value))
            base_names.append(base_name)

        if len(step_nodes) < 2 or len(set(base_names)) != 1:
            return ""

        step_nodes.sort(key=lambda item: item[2])
        max_current = max(current_value for _node, current_value, _target in step_nodes)
        max_target = max(target for _node, _current, target in step_nodes)
        header_parts: list[str] = []
        if group_label:
            header_parts.append(
                self._task_group_summary_header(nodes, group_label, len(step_nodes), hidden_count)
            )
        header_parts.append(f"{base_names[0]}（当前：{self._format_progress_value(max_current)} 分钟）")

        lines = [*header_parts]
        for node, current_value, target_value in step_nodes:
            bar = self._task_progress_bar(current_value, target_value, max_target=max_target)
            target_text = self._format_progress_value(target_value).rjust(4)
            state = self._task_step_state_text(node, current_value, target_value)
            award = str(node.get("award_name") or "").strip()
            award_text = f"  {award}" if award else ""
            lines.append(f"  {bar} {target_text} 分钟  {state}{award_text}")
        return "\n".join(lines)

    def _task_base_name(self, node: dict[str, Any]) -> str:
        raw_name = str(node.get("name") or node.get("task_name") or node.get("title") or "").strip()
        if not raw_name:
            return ""
        name = re.sub(r"\s*\d+(?:\.\d+)?\s*分钟\s*$", "", raw_name).strip()
        return name or raw_name

    def _task_progress_bar(self, current: float, target: float, *, max_target: float, width: int = 20) -> str:
        if target <= 0 or max_target <= 0:
            return "░" * width
        ratio = min(max(current / target, 0.0), 1.0)
        filled = int(round(width * ratio))
        return "█" * filled + "░" * (width - filled)

    def _task_step_state_text(self, node: dict[str, Any], current: float, target: float) -> str:
        if self._node_received(node):
            return "✓ 已领取"
        if self._node_claimable(node):
            return "✓ 待领取"
        remaining = max(0.0, target - current)
        if remaining <= 0:
            return "✓ 已完成"
        return f"还差 {self._format_progress_value(remaining)} 分钟"

    def _focus_task_nodes(self, nodes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str, int]:
        groups: dict[tuple[int, str], list[dict[str, Any]]] = {}
        ungrouped: list[dict[str, Any]] = []
        for node in nodes:
            key = self._task_group_key(node)
            if key is None:
                ungrouped.append(node)
            else:
                groups.setdefault(key, []).append(node)
        if not groups:
            return nodes, "", 0

        chosen_key: tuple[int, str] | None = None
        chosen_key = self._active_task_group_key(groups)
        if chosen_key is None:
            chosen_key = self._nearest_period_task_group_key(groups)
        if chosen_key is None:
            chosen_key = self._today_task_group_key(groups)
        if chosen_key is None:
            active_keys = [key for key, group_nodes in groups.items() if any(self._task_has_visible_activity(node) for node in group_nodes)]
            if active_keys:
                chosen_key = sorted(active_keys)[0]
        if chosen_key is None:
            unfinished_keys = [key for key, group_nodes in groups.items() if any(not self._node_received(node) for node in group_nodes)]
            chosen_key = sorted(unfinished_keys or groups.keys())[0]

        # B 站可能把同一天的任务拆进多个 EraTasklistPc 组（组数 > 日期 Tab 数时，
        # 后面的组会复用最后一个日期标签）。这些组共用同一个日期标签但 group_index
        # 不同，必须按标签合并，否则高档位奖励所在的那个组会被整组当成“其他日期”隐藏。
        chosen_label = chosen_key[1]
        focused_nodes = [
            node
            for key, group_nodes in groups.items()
            if key[1] == chosen_label
            for node in group_nodes
        ]
        focused = [*focused_nodes, *ungrouped]
        hidden_count = max(0, len(nodes) - len(focused))
        return focused, chosen_label, hidden_count

    def _active_task_group_key(
        self,
        groups: dict[tuple[int, str], list[dict[str, Any]]],
    ) -> tuple[int, str] | None:
        now = self._bilibili_now_timestamp()
        active: list[tuple[float, tuple[int, str]]] = []
        for key, nodes in groups.items():
            for node in nodes:
                try:
                    start = float(node.get("active_start"))
                    end = float(node.get("active_end"))
                except (TypeError, ValueError):
                    continue
                if start <= now < end:
                    active.append((start, key))
                    break
        if not active:
            return None
        return max(active, key=lambda item: item[0])[1]

    def _nearest_period_task_group_key(
        self,
        groups: dict[tuple[int, str], list[dict[str, Any]]],
    ) -> tuple[int, str] | None:
        now = self._bilibili_now_timestamp()
        past: list[tuple[float, tuple[int, str]]] = []
        future: list[tuple[float, tuple[int, str]]] = []
        for key, nodes in groups.items():
            periods: list[tuple[float, float]] = []
            for node in nodes:
                try:
                    periods.append((float(node.get("active_start")), float(node.get("active_end"))))
                except (TypeError, ValueError):
                    continue
            if not periods:
                continue
            start = min(period[0] for period in periods)
            end = max(period[1] for period in periods)
            if end <= now:
                past.append((end, key))
            elif start > now:
                future.append((start, key))
        if past:
            return max(past, key=lambda item: item[0])[1]
        if future:
            return min(future, key=lambda item: item[0])[1]
        return None

    def _task_group_summary_header(
        self,
        nodes: list[dict[str, Any]],
        group_label: str,
        reward_count: int,
        hidden_count: int,
    ) -> str:
        now = self._bilibili_now_timestamp()
        periods: list[tuple[float, float]] = []
        for node in nodes:
            try:
                periods.append((float(node.get("active_start")), float(node.get("active_end"))))
            except (TypeError, ValueError):
                continue
        if periods and all(end <= now for _start, end in periods):
            prefix = "最近一场"
        elif periods and all(start > now for start, _end in periods):
            prefix = "下一场"
        else:
            prefix = "当前可挂"
        hidden_note = f"，已隐藏其他日期 {hidden_count} 个任务" if hidden_count else ""
        display_label = self._task_group_display_label(nodes, group_label)
        return f"{prefix}：{display_label}，共 {reward_count} 个奖励{hidden_note}"

    def _task_group_display_label(self, nodes: list[dict[str, Any]], group_label: str) -> str:
        now = self._bilibili_now_timestamp()
        periods: list[tuple[float, float]] = []
        for node in nodes:
            try:
                periods.append((float(node.get("active_start")), float(node.get("active_end"))))
            except (TypeError, ValueError):
                continue
        active_periods = [period for period in periods if period[0] <= now < period[1]]
        if active_periods:
            end = max(period[1] for period in active_periods)
            end_datetime = datetime.fromtimestamp(end, tz=BILIBILI_TIMEZONE)
            return (
                f"{group_label}（B站当前生效，有效至 "
                f"{end_datetime.month}月{end_datetime.day}日 {end_datetime:%H:%M}）"
            )
        return group_label

    def _today_task_group_key(self, groups: dict[tuple[int, str], list[dict[str, Any]]]) -> tuple[int, str] | None:
        server_now = datetime.fromtimestamp(self._bilibili_now_timestamp(), tz=timezone.utc)
        today = _bilibili_today(server_now)
        today_labels = {
            f"{today.month}月{today.day}日",
            f"{today.month:02d}月{today.day}日",
            f"{today.month}月{today.day:02d}日",
            f"{today.month:02d}月{today.day:02d}日",
        }
        for key in groups:
            if key[1].strip() in today_labels:
                return key
        return None

    def _bilibili_now_timestamp(self) -> float:
        local_now = time.time()
        if self._bilibili_time_offset_seconds is None:
            return local_now
        return local_now + self._bilibili_time_offset_seconds

    def _task_group_key(self, node: dict[str, Any]) -> tuple[int, str] | None:
        group_label = str(node.get("group_label") or "").strip()
        group_index = node.get("group_index")
        if not group_label and group_index is None:
            return None
        try:
            sort_index = int(group_index)
        except (TypeError, ValueError):
            sort_index = 999
        label = group_label or f"第 {sort_index + 1} 天"
        return sort_index, label

    def _task_has_visible_activity(self, node: dict[str, Any]) -> bool:
        if self._node_received(node):
            return False
        if self._node_claimable(node):
            return True
        current, _target = self._task_progress_values(node)
        try:
            return float(current) > 0
        except (TypeError, ValueError):
            return False

    def _skip_task_summary_node(self, node: dict[str, Any]) -> bool:
        if self._node_received(node):
            return True
        return self._skip_empty_placeholder_node(node)

    def _skip_empty_placeholder_node(self, node: dict[str, Any]) -> bool:
        current, target = self._task_progress_values(node)
        try:
            target_value = float(target)
        except (TypeError, ValueError):
            target_value = None
        name = self._task_display_name(node)
        return name == "任务" and target_value is not None and target_value <= 0 and not self._node_claimable(node)

    def _task_status_text(self, node: dict[str, Any], current: Any, target: Any) -> str:
        if self._node_received(node):
            return "已领取"
        if self._node_claimable(node):
            return "已完成，待领取"
        try:
            current_value = float(current)
            target_value = float(target)
        except (TypeError, ValueError):
            return "等待进度更新"
        if target_value <= 0:
            return "等待进度更新"
        remaining = max(0.0, target_value - current_value)
        if remaining <= 0:
            return "已完成，等待 B 站刷新领取按钮"
        return f"还差 {self._format_progress_value(remaining)} 分钟"

    def _format_progress_value(self, value: Any) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if number.is_integer():
            return str(int(number))
        return f"{number:.1f}".rstrip("0").rstrip(".")

    def _task_display_name(self, node: dict[str, Any]) -> str:
        name = str(node.get("name") or node.get("task_name") or node.get("title") or node.get("task_id") or "任务")
        group_label = str(node.get("group_label") or "").strip()
        award_name = str(node.get("award_name") or "").strip()
        parts = [part for part in (group_label, name, award_name) if part]
        return "｜".join(parts) if parts else name

    def _find_claimable_tasks(self, progress: dict[str, Any]) -> list[str]:
        return [name for name, _task_id in self._find_claimable_task_refs(progress)]

    def _find_claimable_task_refs(self, progress: dict[str, Any]) -> list[tuple[str, str]]:
        claimable_refs: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for index, node in enumerate(sorted(self._iter_task_nodes(progress), key=self._task_sort_key)):
            if not self._node_claimable(node):
                continue
            name = self._task_display_name(node)
            if name == "任务":
                name = str(node.get("id") or f"任务{index + 1}")
            task_id = self._task_id_from_node(node)
            key = ("id", task_id) if task_id else ("name", name)
            if key in seen:
                continue
            seen.add(key)
            claimable_refs.append((name, task_id))
        return claimable_refs

    def _task_sort_key(self, node: dict[str, Any]) -> tuple[int, float, str]:
        group_index = node.get("group_index")
        try:
            group_value = int(group_index)
        except (TypeError, ValueError):
            group_value = 999
        _current, target = self._task_progress_values(node)
        try:
            target_value = float(target)
        except (TypeError, ValueError):
            target_value = 999999
        return group_value, target_value, self._task_display_name(node)

    def _discover_task_ids(self, progress: dict[str, Any]) -> list[str]:
        task_ids: list[str] = []
        seen: set[str] = set()
        for node in self._iter_task_nodes(progress):
            task_id = self._task_id_from_node(node)
            if task_id and task_id not in seen:
                task_ids.append(task_id)
                seen.add(task_id)
        return task_ids

    def _activity_tracking_task_ids(self, progress: dict[str, Any]) -> list[str]:
        raw_task_ids = progress.get("tracking_task_ids")
        if not isinstance(raw_task_ids, (list, tuple, set)):
            return []
        task_ids: list[str] = []
        seen: set[str] = set()
        for value in raw_task_ids:
            task_id = str(value or "").strip()
            if not task_id or task_id in seen:
                continue
            seen.add(task_id)
            task_ids.append(task_id)
        return task_ids

    def _task_id_from_node(self, node: dict[str, Any]) -> str:
        value = node.get("task_id") or node.get("taskId") or node.get("taskid") or node.get("id")
        if value is None:
            return ""
        text = str(value).strip()
        return text if text else ""

    def _merge_task_ids(self, configured: list[str], discovered: set[str]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for task_id in [*configured, *sorted(discovered)]:
            if not task_id or task_id in seen:
                continue
            merged.append(task_id)
            seen.add(task_id)
        return merged

    def _iter_task_nodes(self, value: Any) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        if isinstance(value, dict):
            keys = set(value)
            taskish_keys = {
                "task_id",
                "taskId",
                "taskid",
                "id",
                "task_name",
                "name",
                "title",
                "status",
                "state",
                "task_status",
                "receive_status",
                "can_receive",
                "is_receive",
                "progress",
                "current",
                "target",
                "total",
            }
            is_taskish = bool(keys & taskish_keys)
            if is_taskish:
                nodes.append(value)
            for key, child in value.items():
                if is_taskish and key in {"check_points", "checkpoints", "indicators", "list"}:
                    continue
                nodes.extend(self._iter_task_nodes(child))
        elif isinstance(value, list):
            for child in value:
                nodes.extend(self._iter_task_nodes(child))
        return nodes

    def _node_claimable(self, node: dict[str, Any]) -> bool:
        activity_status = self._activity_status(node)
        if activity_status is not None:
            return activity_status == 2
        if self._node_received(node) or self._node_unclaimable(node):
            return False
        if self._truthy(node.get("can_receive")) or self._truthy(node.get("claimable")):
            return True
        if self._truthy(node.get("is_finish")) and not self._truthy(node.get("is_receive")):
            return True
        if self._status_claimable(node.get("receive_status")):
            return True
        if self._status_claimable(node.get("reward_status")):
            return True
        if self._status_finished(node.get("status") or node.get("state")) and not self._truthy(node.get("is_receive")):
            return True
        return self._progress_full(node) and not self._truthy(node.get("is_receive"))

    def _node_received(self, node: dict[str, Any]) -> bool:
        activity_status = self._activity_status(node)
        if activity_status == 3:
            return True
        if activity_status == 2:
            return False
        if self._truthy(node.get("is_receive")) or self._truthy(node.get("received")):
            return True
        for key in ("receive_status", "reward_status", "status", "state"):
            if self._status_received(node.get(key)):
                return True
        return False

    def _node_unclaimable(self, node: dict[str, Any]) -> bool:
        if self._falsey(node.get("can_receive")) or self._falsey(node.get("claimable")):
            return True
        for key in ("receive_status", "reward_status"):
            if self._status_unclaimable(node.get(key)):
                return True
        return False

    def _truthy(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value == 1
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "can_receive", "claimable", "finish", "finished"}
        return False

    def _falsey(self, value: Any) -> bool:
        if isinstance(value, bool):
            return not value
        if isinstance(value, (int, float)):
            return value == 0
        if isinstance(value, str):
            return value.strip().lower() in {"0", "false", "no", "cannot_receive", "unclaimable", "not_receive"}
        return False

    def _status_claimable(self, value: Any) -> bool:
        if isinstance(value, (int, float)):
            return int(value) == 1
        if isinstance(value, str):
            return value.strip().lower() in {"1", "can_receive", "claimable", "unreceived", "wait_receive"}
        return False

    def _status_received(self, value: Any) -> bool:
        if isinstance(value, (int, float)):
            return int(value) in {2, 3}
        if isinstance(value, str):
            return value.strip().lower() in {"2", "3", "received", "claimed", "already_receive", "already_received", "done_received"}
        return False

    def _status_unclaimable(self, value: Any) -> bool:
        if isinstance(value, (int, float)):
            return int(value) in {0, -1}
        if isinstance(value, str):
            return value.strip().lower() in {"0", "-1", "cannot_receive", "unclaimable", "not_receive", "expired"}
        return False

    def _status_finished(self, value: Any) -> bool:
        if isinstance(value, (int, float)):
            return int(value) == 1
        if isinstance(value, str):
            return value.strip().lower() in {"complete", "completed", "done", "finish", "finished", "success"}
        return False

    def _progress_full(self, node: dict[str, Any]) -> bool:
        current, target = self._task_progress_values(node)
        try:
            return current is not None and target is not None and float(current) >= float(target) > 0
        except (TypeError, ValueError):
            return False

    def _task_progress_values(self, node: dict[str, Any]) -> tuple[Any, Any]:
        current = self._first_present(
            node,
            (
                "current",
                "progress",
                "now",
                "finish",
                "cur_value",
                "curValue",
                "current_value",
                "currentValue",
                "finished",
                "done",
                "num",
                "count",
            ),
        )
        target = self._first_present(
            node,
            (
                "target",
                "total",
                "require",
                "max",
                "limit",
                "target_value",
                "targetValue",
                "require_value",
                "requireValue",
                "need",
                "goal",
            ),
        )
        if current is not None or target is not None:
            return current, target
        for key in ("indicators", "list"):
            first = self._first_dict(node.get(key))
            if first:
                return first.get("cur_value"), first.get("limit")
        for key in ("check_points", "checkpoints"):
            checkpoint = self._first_dict(node.get(key))
            first = self._first_dict(checkpoint.get("list") if checkpoint else None)
            if first:
                return first.get("cur_value"), first.get("limit")
        return None, None

    def _first_present(self, node: dict[str, Any], keys: tuple[str, ...]) -> Any:
        for key in keys:
            if key in node and node.get(key) is not None:
                return node.get(key)
        return None

    def _activity_status(self, node: dict[str, Any]) -> int | None:
        if "task_status" in node:
            value = node.get("task_status")
        elif "taskStatus" in node:
            value = node.get("taskStatus")
        elif "status" in node and (
            "award_sid" in node
            or "awardsid" in node
            or "ztasksid" in node
            or "sid" in node and ("alias" in node or "award_name" in node or "awardname" in node)
        ):
            value = node.get("status")
        else:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _first_dict(self, value: Any) -> dict[str, Any]:
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    return item
        if isinstance(value, dict):
            return value
        return {}
