from __future__ import annotations

import re
import threading
from typing import Callable

from .config import AppConfig
from .watcher import LiveWatcher, WatchOptions, WatchWorkerStatus


LogSink = Callable[[str], None]
ACCOUNT_START_STAGGER_SECONDS = 2.0


def _parse_task_ids(value: str) -> list[str]:
    return [item for item in re.split(r"[\s,，;；]+", (value or "").strip()) if item]


def build_account_options(config: AppConfig) -> list[tuple[str, WatchOptions]]:
    """把配置展开成「每个勾选账号一组 WatchOptions」。active_accounts 为空视为全选。"""
    active = list(config.active_accounts or [])
    pairs: list[tuple[str, WatchOptions]] = []
    for account in config.accounts:
        if active and account.name not in active:
            continue
        if not account.cookie:
            continue
        options = WatchOptions(
            cookie=account.cookie,
            room_id=config.room_id,
            check_interval=config.check_interval,
            auto_claim=config.auto_claim,
            task_ids=_parse_task_ids(config.task_ids),
            watch_threads=config.watch_threads,
        )
        pairs.append((account.name, options))
    return pairs


class MultiAccountWatcher:
    """协调多个账号的并行观看会话。复刻 LiveWatcher 的公开接口供 GUI 直接替换。"""

    def __init__(
        self,
        account_options: list[tuple[str, WatchOptions]],
        log: LogSink,
        *,
        watcher_factory: Callable[..., object] = LiveWatcher,
        stagger_seconds: float = ACCOUNT_START_STAGGER_SECONDS,
    ) -> None:
        self._log = log
        self._stagger_seconds = stagger_seconds
        self._watcher_factory = watcher_factory
        self._stop = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._claim_lock = threading.Lock()
        self._start_thread: threading.Thread | None = None
        self._claim_thread: threading.Thread | None = None
        self._children: list[tuple[str, object]] = []
        for name, options in account_options:
            child = watcher_factory(options, self._make_child_log(name))
            self._children.append((name, child))

    def _make_child_log(self, name: str) -> LogSink:
        return lambda message, _name=name: self._log(f"[{_name}] {message}")

    def start(self) -> None:
        with self._lifecycle_lock:
            if self.running or (self._start_thread is not None and self._start_thread.is_alive()):
                self._log("已经在运行中")
                return
            self._stop.clear()
            self._start_thread = threading.Thread(target=self._staggered_start, daemon=True)
            self._start_thread.start()

    def _staggered_start(self) -> None:
        for index, (name, child) in enumerate(self._children):
            if index > 0 and self._stagger_seconds > 0:
                self._stop.wait(self._stagger_seconds)
            # 「检查是否已停止」和「启动子账号」必须原子完成：否则 stop() 可能恰好插在
            # 两者之间，而 LiveWatcher.start() 会清掉它自己的停止标志，导致这一路计时
            # 永远停不下来（线程泄漏、持续打 B 站接口）。用 _lifecycle_lock 串起来，
            # 并保证只在锁外做错峰等待，避免长时间持锁。
            with self._lifecycle_lock:
                if self._stop.is_set():
                    return
                child.start()

    def _await_start_for_test(self) -> None:
        if self._start_thread is not None:
            self._start_thread.join(timeout=5)

    def stop(self) -> None:
        # 持锁设置停止标志并停掉所有子账号，与 _staggered_start 的「检查+启动」互斥，
        # 确保停止后不会再有账号被启动，已启动的也都会被停掉。
        with self._lifecycle_lock:
            self._stop.set()
            for _name, child in self._children:
                child.stop()
        self._log("已请求停止全部账号")

    @property
    def running(self) -> bool:
        return any(getattr(child, "running", False) for _name, child in self._children)

    def get_watch_status_snapshot(self) -> tuple[list[WatchWorkerStatus], str]:
        rows: list[WatchWorkerStatus] = []
        normal_accounts = 0
        for index, (name, child) in enumerate(self._children, start=1):
            child_summary = ""
            child_state = "启动中"
            child_interval = None
            getter = getattr(child, "get_watch_status_snapshot", None)
            if callable(getter):
                try:
                    child_rows, child_summary = getter()
                    normal = sum(1 for r in child_rows if r.state == "正常")
                    total = len(child_rows)
                    if normal and normal == total:
                        child_state = "正常"
                    elif normal:
                        child_state = "计时中"
                    elif any(r.state == "等待开播" for r in child_rows):
                        child_state = "等待开播"
                    elif any(r.state in {"启动中", "计时中"} for r in child_rows):
                        child_state = "计时中"
                    elif total:
                        child_state = "暂时失败"
                    intervals = [r.interval for r in child_rows if r.interval is not None]
                    child_interval = min(intervals) if intervals else None
                except Exception:
                    # 单账号状态读取异常不应拖垮整体状态展示
                    child_state = "启动中"
                    child_summary = ""
                    child_interval = None
            if child_state == "正常":
                normal_accounts += 1
            detail = child_summary.replace("后台计时状态：", "").strip()
            rows.append(WatchWorkerStatus(
                worker_id=index,
                state=child_state,
                interval=child_interval,
                message=f"{name}：{detail}" if detail else name,
            ))
        total_accounts = len(self._children)
        summary = f"多账号并行：{normal_accounts}/{total_accounts} 账号正常运行"
        return rows, summary

    def claim_completed_tasks(self) -> None:
        with self._claim_lock:
            if self._claim_thread is not None and self._claim_thread.is_alive():
                self._log("领取线程正在运行中")
                return
            self._claim_thread = threading.Thread(target=self._staggered_claim, daemon=True)
            self._claim_thread.start()

    def _staggered_claim(self) -> None:
        for index, (name, child) in enumerate(self._children):
            if self._stop.is_set():
                return
            if index > 0 and self._stagger_seconds > 0:
                self._stop.wait(self._stagger_seconds)
                if self._stop.is_set():
                    return
            self._delegate_to_child(child, name, "claim_completed_tasks", "领取触发")

    def _await_claim_for_test(self) -> None:
        if self._claim_thread is not None:
            self._claim_thread.join(timeout=5)

    def refresh_progress_once(self) -> None:
        for name, child in self._children:
            self._delegate_to_child(child, name, "refresh_progress_once", "刷新进度")

    def rediscover_tasks_once(self) -> None:
        for name, child in self._children:
            self._delegate_to_child(child, name, "rediscover_tasks_once", "重新识别任务")

    def _delegate_to_child(self, child: object, name: str, method: str, action: str) -> None:
        """对单个子账号调用某方法；单账号失败只记日志、不影响其他账号。"""
        fn = getattr(child, method, None)
        if not callable(fn):
            return
        try:
            fn()
        except Exception as exc:
            self._log(f"[{name}] {action}失败：{exc}")
