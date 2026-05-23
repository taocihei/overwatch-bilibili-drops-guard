from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Optional

from .bilibili import BilibiliClient, RoomInfo
from .config import MAX_WATCH_THREADS


LogSink = Callable[[str], None]
CLAIM_SUBMIT_DELAY_SECONDS = 3.0
CLAIM_RATE_LIMIT_DELAY_SECONDS = 12.0
CLAIM_RATE_LIMIT_ATTEMPTS = 3


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
        self._activity_task_ids: set[str] = set()
        self._activity_task_meta: dict[str, dict[str, Any]] = {}
        self._claimable_general = False
        self._claim_lock = threading.Lock()
        self._claim_thread: Optional[threading.Thread] = None
        self._last_up_id: int | None = None
        self._watch_status_lock = threading.Lock()
        self._watch_statuses: dict[int, dict[str, Any]] = {}
        self._watch_worker_count = self._normalize_watch_threads(self.options.watch_threads)
        self._last_watch_status_summary = ""
        self._manual_refresh_thread: Optional[threading.Thread] = None
        self._rediscover_thread: Optional[threading.Thread] = None

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
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.log("已请求停止")

    def claim_completed_tasks(self) -> None:
        if self._claim_thread and self._claim_thread.is_alive():
            self.log("领取线程正在运行中")
            return
        self._claim_thread = threading.Thread(target=self._claim_completed_worker, daemon=True)
        self._claim_thread.start()

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

    def _run(self) -> None:
        client = BilibiliClient(self.options.cookie)
        login = client.check_login()
        if login.logged_in:
            self.log(f"账号登录正常：{login.uname}（{login.mid}）")
        else:
            self.log(login.message)
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

                if self.options.auto_claim and room.anchor_uid:
                    found_live_claimable = self._check_and_claim_task(client, room.anchor_uid)
                    found_activity_claimable = self._check_activity_task_progress(client)
                    found_explicit_claimable = self._check_explicit_task_ids(room.anchor_uid)
                    if found_live_claimable or found_activity_claimable or found_explicit_claimable:
                        self._start_auto_claim_thread()
            except Exception as exc:
                self.log(f"守护循环异常：{exc}")

            self._stop.wait(max(10, int(self.options.check_interval or 10)))

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
        return f"后台计时状态：{'，'.join(parts)}{interval_text}", normal_count, failed_count + waiting_count

    def _log_watch_status_summary(self, *, force: bool = False) -> None:
        summary, normal_count, problem_count = self._watch_status_summary_info()
        if not force and normal_count < self._watch_worker_count and problem_count == 0:
            return
        if force or summary != self._last_watch_status_summary:
            self._last_watch_status_summary = summary
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
        self.log(f"房间 {room.room_id}：{room.message}｜{room.title}{anchor}｜人气 {room.online}")

    def _heartbeat_watch_worker(self, worker_id: int, room: RoomInfo | None) -> None:
        client = BilibiliClient(self.options.cookie)
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
                if self._watch_detail_enabled():
                    self.log(f"后台计时 {worker_id} 首次计时请求成功，下一次约 {state.interval} 秒后")
                self._log_watch_status_summary()
                self._stop.wait(state.interval)

                while not self._stop.is_set():
                    state = self._continue_heartbeat_session(client, current_room, sequence, state)
                    sequence += 1
                    self._set_watch_status(worker_id, "正常", interval=state.interval)
                    if self._watch_detail_enabled():
                        self.log(f"后台计时 {worker_id} 计时请求已提交，下一次约 {state.interval} 秒后")
                    self._log_watch_status_summary()
                    self._stop.wait(state.interval)
            except Exception as exc:
                self._set_watch_status(worker_id, "暂时失败", message=self._friendly_error(exc))
                self.log(f"后台计时 {worker_id} 暂时失败：{self._friendly_error(exc)}；稍后重试")
                self._log_watch_status_summary()
                self._stop.wait(15)

    def _resolve_heartbeat_room(self, client: BilibiliClient, room: RoomInfo | None) -> RoomInfo:
        if room and room.room_id:
            return room
        with_room = self._room
        if with_room and with_room.room_id:
            return with_room
        return client.get_room_info(self.options.room_id)

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

    def _extract_web_heartbeat_interval(self, data: dict[str, Any], fallback: int = 60) -> int:
        value = data.get("next_interval") or data.get("heartbeat_interval") or data.get("interval") or fallback
        try:
            return max(10, int(value or fallback or 60))
        except (TypeError, ValueError):
            return max(10, int(fallback or 60))

    def _start_heartbeat_session(self, client: BilibiliClient, room: RoomInfo, fallback: HeartbeatState) -> HeartbeatState:
        data = client.enter_room_heartbeat(room)
        return self._extract_heartbeat_state(data, fallback)

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

        data = client.web_live_heartbeat(room.room_id, state.interval)
        state.interval = self._extract_web_heartbeat_interval(data, state.interval)
        return state

    def _start_auto_claim_thread(self) -> None:
        if self._claim_thread and self._claim_thread.is_alive():
            return
        self._claim_thread = threading.Thread(target=self._claim_completed_worker, daemon=True)
        self._claim_thread.start()

    def _check_and_claim_task(self, client: BilibiliClient, up_id: int) -> bool:
        try:
            progress = client.get_user_task_progress(up_id)
        except Exception as exc:
            self.log(f"掉宝任务进度检查失败：{exc}")
            return False

        return self._record_task_progress(progress, announce_claimable=True)

    def _check_activity_task_progress(self, client: BilibiliClient) -> bool:
        self._discover_activity_task_ids(client, announce_progress=False)
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

    def _remember_activity_progress_source(self, progress: dict[str, Any], queried_task_ids: list[str]) -> None:
        progress_task_ids = self._discover_task_ids(progress)
        task_ids = progress_task_ids or queried_task_ids
        if not task_ids:
            return
        with self._claim_lock:
            self._activity_task_ids.update(task_ids)
            for task_id in task_ids:
                self._activity_task_meta.setdefault(task_id, {})

    def _discover_activity_task_ids(self, client: BilibiliClient, announce_progress: bool = True) -> list[str]:
        try:
            progress = client.discover_live_activity_tasks(self.options.room_id)
        except Exception as exc:
            self.log(f"没有读到活动任务列表，稍后会自动再试：{self._friendly_error(exc)}")
            return []
        task_ids = self._discover_task_ids(progress)
        if not task_ids:
            self.log("当前直播页暂时没有读到可跟踪的掉宝任务，稍后会自动再试")
            return []
        with self._claim_lock:
            current_task_ids = set(task_ids)
            previous_task_ids = set(self._activity_task_ids)
            new_task_ids = [task_id for task_id in task_ids if task_id not in previous_task_ids]
            stale_task_ids = sorted(previous_task_ids - current_task_ids)
            self._activity_task_ids = current_task_ids
            self._known_task_ids.update(task_ids)
            self._claimable_task_ids.difference_update(stale_task_ids)
            next_meta: dict[str, dict[str, Any]] = {}
            for node in self._iter_task_nodes(progress):
                task_id = self._task_id_from_node(node)
                if task_id:
                    next_meta[task_id] = {
                        "group_label": node.get("group_label") or "",
                        "group_index": node.get("group_index"),
                        "task_name": node.get("task_name") or node.get("name") or node.get("title") or "",
                        "award_name": node.get("award_name") or "",
                    }
            self._activity_task_meta = next_meta
        if new_task_ids:
            self.log("已找到本次活动任务，会自动显示本次可挂的奖励进度")
        if stale_task_ids:
            self.log("活动任务已更新，已同步最新任务列表")
        if announce_progress or new_task_ids:
            self._record_task_progress(progress, announce_claimable=False)
        return task_ids

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
                node.setdefault("group_label", meta["group_label"])
            if meta.get("group_index") is not None:
                node.setdefault("group_index", meta["group_index"])
            if meta.get("award_name"):
                node.setdefault("award_name", meta["award_name"])
            if meta.get("task_name"):
                node.setdefault("task_name", meta["task_name"])

    def _record_task_progress(self, progress: dict[str, Any], announce_claimable: bool) -> bool:
        summary = self._summarize_task(progress)
        if summary:
            self.log(f"掉宝任务：\n{summary}")

        discovered_task_ids = self._discover_task_ids(progress)
        if discovered_task_ids:
            with self._claim_lock:
                new_task_ids = [task_id for task_id in discovered_task_ids if task_id not in self._known_task_ids]
                self._known_task_ids.update(discovered_task_ids)
            if new_task_ids:
                self.log("已自动找到任务列表，无需手动填写")

        claimable_tasks = self._find_claimable_task_refs(progress)
        if not claimable_tasks:
            return False

        with self._claim_lock:
            named_tasks: list[str] = []
            for name, task_id in claimable_tasks:
                named_tasks.append(name)
                if task_id:
                    self._claimable_task_ids.add(task_id)
                else:
                    self._claimable_general = True
        if announce_claimable:
            self.log(f"检测到 {len(named_tasks)} 个奖励可以领取，正在排队领取")
        return True

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
        progress = client.get_user_task_progress(up_id, task_id=task_id)
        claimable_tasks = self._find_claimable_task_refs(progress)
        if not claimable_tasks:
            return "手动任务尚未完成"
        with self._claim_lock:
            for _name, found_task_id in claimable_tasks:
                self._claimable_task_ids.add(found_task_id or task_id)
        stop_scan.set()
        return f"手动任务已完成：{', '.join(name for name, _task_id in claimable_tasks)}"

    def _claim_completed_worker(self) -> None:
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
            try:
                BilibiliClient(self.options.cookie).claim_user_task_rewards(up_id)
                with self._claim_lock:
                    self._claimable_general = False
                self.log("已领取：已完成的通用奖励")
            except Exception as exc:
                self.log(f"领取失败：通用奖励：{self._friendly_error(exc)}")
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
            is_activity_task = task_id in self._activity_task_ids
        if already_claimed:
            return f"已跳过：{self._claim_task_label(task_id)} 已经领取过"
        client = BilibiliClient(self.options.cookie)
        label = self._claim_task_label(task_id)
        if is_activity_task:
            self._claim_with_retry(lambda: client.claim_activity_mission_reward(task_id), label)
        else:
            self._claim_with_retry(lambda: client.claim_user_task_rewards(up_id, task_id=task_id), label)
        with self._claim_lock:
            self._claimed_markers.add(marker)
            self._claimable_task_ids.discard(task_id)
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
        return "请求频率过高" in text or "频率" in text or "稍后再试" in text

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
            is_activity_task = task_id in self._activity_task_ids
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
        room = BilibiliClient(self.options.cookie).get_room_info(self.options.room_id)
        self._room = room
        self._last_up_id = room.anchor_uid or self._last_up_id
        if room.room_id:
            self._log_room(room)
        return self._last_up_id

    def _refresh_claimable_tasks(self, up_id: int) -> None:
        self.log("领取前刷新任务进度")
        client = BilibiliClient(self.options.cookie)
        try:
            progress = client.get_user_task_progress(up_id)
        except Exception as exc:
            self.log(f"领取前刷新任务进度失败：{exc}")
        else:
            self._record_task_progress(progress, announce_claimable=False)
        self._check_activity_task_progress(client)

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
            hidden_note = f"，已隐藏其他日期 {hidden_count} 个任务" if hidden_count else ""
            header = f"当前可挂：{group_label}，共 {len(text_parts)} 个奖励{hidden_note}"
            return "\n".join([header, *text_parts])
        return "\n".join(text_parts)

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

        if len(step_nodes) < 3 or len(set(base_names)) != 1:
            return ""

        step_nodes.sort(key=lambda item: item[2])
        max_current = max(current_value for _node, current_value, _target in step_nodes)
        max_target = max(target for _node, _current, target in step_nodes)
        header_parts: list[str] = []
        if group_label:
            hidden_note = f"，已隐藏其他日期 {hidden_count} 个任务" if hidden_count else ""
            header_parts.append(f"当前可挂：{group_label}，共 {len(step_nodes)} 个奖励{hidden_note}")
        header_parts.append(f"{base_names[0]}（当前：{self._format_progress_value(max_current)} 分钟）")

        lines = [*header_parts]
        for node, current_value, target_value in step_nodes:
            bar = self._task_progress_bar(current_value, target_value, max_target=max_target)
            target_text = self._format_progress_value(target_value).rjust(4)
            state = self._task_step_state_text(node, current_value, target_value)
            lines.append(f"  {bar} {target_text} 分钟  {state}")
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
        chosen_key = self._today_task_group_key(groups)
        if chosen_key is None:
            active_keys = [key for key, group_nodes in groups.items() if any(self._task_has_visible_activity(node) for node in group_nodes)]
            if active_keys:
                chosen_key = sorted(active_keys)[0]
        if chosen_key is None:
            unfinished_keys = [key for key, group_nodes in groups.items() if any(not self._node_received(node) for node in group_nodes)]
            chosen_key = sorted(unfinished_keys or groups.keys())[0]

        focused = [*groups[chosen_key], *ungrouped]
        hidden_count = max(0, len(nodes) - len(focused))
        return focused, chosen_key[1], hidden_count

    def _today_task_group_key(self, groups: dict[tuple[int, str], list[dict[str, Any]]]) -> tuple[int, str] | None:
        today = date.today()
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
        if self._node_claimable(node):
            return True
        current, _target = self._task_progress_values(node)
        try:
            return float(current) > 0
        except (TypeError, ValueError):
            return False

    def _skip_task_summary_node(self, node: dict[str, Any]) -> bool:
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
        for index, node in enumerate(sorted(self._iter_task_nodes(progress), key=self._task_sort_key)):
            if not self._node_claimable(node):
                continue
            name = self._task_display_name(node)
            if name == "任务":
                name = str(node.get("id") or f"任务{index + 1}")
            claimable_refs.append((name, self._task_id_from_node(node)))
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
        if activity_status == 2:
            return True
        if activity_status == 3:
            return False
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
        current = self._first_present(node, ("current", "progress", "now", "finish"))
        target = self._first_present(node, ("target", "total", "require", "max"))
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
