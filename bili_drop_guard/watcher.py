from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .bilibili import BilibiliClient, RoomInfo
from .browser_watch import BrowserWatchOptions, BrowserWatchSession


LogSink = Callable[[str], None]


@dataclass
class WatchOptions:
    cookie: str
    room_id: str
    check_interval: int = 60
    auto_claim: bool = True
    task_ids: list[str] | None = None
    watch_threads: int = 1


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
        self._claimable_general = False
        self._claim_lock = threading.Lock()
        self._claim_thread: Optional[threading.Thread] = None
        self._last_up_id: int | None = None

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

    def _run(self) -> None:
        client = BilibiliClient(self.options.cookie)
        login = client.check_login()
        if login.logged_in:
            self.log(f"账号登录正常：{login.uname}（{login.mid}）")
        else:
            self.log(login.message)
        self._start_watch_threads()

        while not self._stop.is_set():
            try:
                room = client.get_room_info(self.options.room_id)
                self._room = room
                self._last_up_id = room.anchor_uid or self._last_up_id
                self._log_room(room)

                if self.options.auto_claim and room.anchor_uid:
                    found_claimable = self._check_and_claim_task(client, room.anchor_uid)
                    found_explicit_claimable = self._check_explicit_task_ids(room.anchor_uid)
                    if found_claimable or found_explicit_claimable:
                        self._start_auto_claim_thread()
            except Exception as exc:
                self.log(f"守护循环异常：{exc}")

            self._stop.wait(max(10, int(self.options.check_interval or 60)))

        self.log("守护已停止")

    def _start_watch_threads(self) -> None:
        worker_count = max(1, int(self.options.watch_threads or 1))
        self._watch_threads = []
        thread = threading.Thread(target=self._browser_watch_worker, args=(worker_count,), daemon=True)
        self._watch_threads.append(thread)
        thread.start()
        self.log(f"已启动浏览器观看管理线程，准备打开 {worker_count} 个直播窗口用于并行累计观看时长")

    def _log_room(self, room: RoomInfo) -> None:
        if not room.room_id:
            self.log(room.message)
            return
        anchor = f"｜主播 {room.anchor}" if room.anchor else ""
        self.log(f"房间 {room.room_id}：{room.message}｜{room.title}{anchor}｜人气 {room.online}")

    def _browser_watch_worker(self, window_count: int) -> None:
        try:
            session = BrowserWatchSession(
                BrowserWatchOptions(
                    cookie=self.options.cookie,
                    room_id=self.options.room_id,
                    window_count=window_count,
                    refresh_interval=self.options.check_interval,
                ),
                self.log,
            )
            session.run(self._stop)
        except Exception as exc:
            self.log(f"浏览器观看启动失败：{exc}")

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

    def _record_task_progress(self, progress: dict[str, Any], announce_claimable: bool) -> bool:
        summary = self._summarize_task(progress)
        if summary:
            self.log(f"掉宝任务：{summary}")

        discovered_task_ids = self._discover_task_ids(progress)
        if discovered_task_ids:
            with self._claim_lock:
                new_task_ids = [task_id for task_id in discovered_task_ids if task_id not in self._known_task_ids]
                self._known_task_ids.update(discovered_task_ids)
            if new_task_ids:
                self.log(f"已自动识别任务 ID：{', '.join(new_task_ids[:8])}")

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
            self.log(f"检测到任务已完成，准备领取奖励：{', '.join(named_tasks)}")
        return True

    def _check_explicit_task_ids(self, up_id: int) -> bool:
        with self._claim_lock:
            task_ids = self._merge_task_ids(self.options.task_ids or [], self._known_task_ids)
        if not task_ids:
            return False
        with self._claim_lock:
            if self._claimable_task_ids:
                self.log(f"已有 {len(self._claimable_task_ids)} 个任务完成，等待点击领取奖励，暂停新的并发检查")
                return True

        stop_scan = threading.Event()
        self.log(f"开始检查指定任务：{len(task_ids)} 个任务；多线程只用于观看时长，不用于任务检查")
        found_claimable = False
        for task_id in task_ids:
            if stop_scan.is_set():
                self.log("已识别到完成任务，停止剩余任务检查，等待点击领取奖励")
                break
            try:
                result = self._check_one_explicit_task(up_id, task_id, stop_scan)
            except Exception as exc:
                self.log(f"任务 {task_id} 检查失败：{exc}")
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
            return f"任务 {task_id} 尚未完成"
        with self._claim_lock:
            for _name, found_task_id in claimable_tasks:
                self._claimable_task_ids.add(found_task_id or task_id)
        stop_scan.set()
        return f"任务 {task_id} 已完成：{', '.join(name for name, _task_id in claimable_tasks)}"

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
        self.log("开始领取奖励：领奖固定使用 1 个线程提交请求")

        if claim_general and not task_ids:
            try:
                BilibiliClient(self.options.cookie).claim_user_task_rewards(up_id)
                with self._claim_lock:
                    self._claimable_general = False
                self.log("通用任务奖励领取请求已提交")
            except Exception as exc:
                self.log(f"通用任务奖励领取失败：{exc}")
            return

        for task_id in task_ids:
            try:
                self.log(self._claim_one_task(up_id, task_id))
            except Exception as exc:
                self.log(f"任务 {task_id} 领取失败：{exc}")

    def _claim_one_task(self, up_id: int, task_id: str) -> str:
        marker = f"{up_id}:{task_id}"
        with self._claim_lock:
            if marker in self._claimed_markers:
                return f"任务 {task_id} 已提交过领取，跳过"
        BilibiliClient(self.options.cookie).claim_user_task_rewards(up_id, task_id=task_id)
        with self._claim_lock:
            self._claimed_markers.add(marker)
            self._claimable_task_ids.discard(task_id)
        return f"任务 {task_id} 领取请求已提交"

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
            return
        self._record_task_progress(progress, announce_claimable=False)

        with self._claim_lock:
            task_ids = self._merge_task_ids(self.options.task_ids or [], self._known_task_ids)
        for task_id in task_ids:
            try:
                self._check_one_explicit_task(up_id, task_id, threading.Event())
            except Exception as exc:
                self.log(f"领取前检查任务 {task_id} 失败：{exc}")

    def _summarize_task(self, progress: dict[str, Any]) -> str:
        text_parts: list[str] = []
        for node in self._iter_task_nodes(progress):
            name = str(node.get("name") or node.get("task_name") or node.get("title") or node.get("task_id") or "任务")
            current = node.get("current") or node.get("progress") or node.get("now") or node.get("finish")
            target = node.get("target") or node.get("total") or node.get("require") or node.get("max")
            status = node.get("receive_status") or node.get("status") or node.get("state")
            if current is not None and target is not None:
                text_parts.append(f"{name} {current}/{target} 状态={status}")
            elif status is not None:
                text_parts.append(f"{name} 状态={status}")
        return "；".join(part for part in text_parts if part)[:240]

    def _find_claimable_tasks(self, progress: dict[str, Any]) -> list[str]:
        return [name for name, _task_id in self._find_claimable_task_refs(progress)]

    def _find_claimable_task_refs(self, progress: dict[str, Any]) -> list[tuple[str, str]]:
        claimable_refs: list[tuple[str, str]] = []
        for index, node in enumerate(self._iter_task_nodes(progress)):
            if not self._node_claimable(node):
                continue
            name = str(
                node.get("name")
                or node.get("task_name")
                or node.get("title")
                or node.get("task_id")
                or node.get("id")
                or f"任务{index + 1}"
            )
            claimable_refs.append((name, self._task_id_from_node(node)))
        return claimable_refs

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
                "receive_status",
                "can_receive",
                "is_receive",
                "progress",
                "current",
                "target",
                "total",
            }
            if keys & taskish_keys:
                nodes.append(value)
            for child in value.values():
                nodes.extend(self._iter_task_nodes(child))
        elif isinstance(value, list):
            for child in value:
                nodes.extend(self._iter_task_nodes(child))
        return nodes

    def _node_claimable(self, node: dict[str, Any]) -> bool:
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
        current = node.get("current") or node.get("progress") or node.get("now") or node.get("finish")
        target = node.get("target") or node.get("total") or node.get("require") or node.get("max")
        try:
            return current is not None and target is not None and float(current) >= float(target) > 0
        except (TypeError, ValueError):
            return False
