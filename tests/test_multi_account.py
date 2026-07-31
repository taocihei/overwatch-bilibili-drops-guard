from __future__ import annotations

import unittest

from bili_drop_guard.config import AppConfig, AccountProfile
from bili_drop_guard.multi_account import build_account_options, MultiAccountWatcher
from bili_drop_guard.watcher import WatchOptions, WatchWorkerStatus


class BuildAccountOptionsTest(unittest.TestCase):
    def _config(self, active: list[str]) -> AppConfig:
        return AppConfig(
            cookie="SESSDATA=a",
            account_name="主号",
            accounts=[AccountProfile(name="主号", cookie="SESSDATA=a"),
                      AccountProfile(name="小号", cookie="SESSDATA=b")],
            room_id="23612045",
            watch_threads=2,
            active_accounts=active,
            task_ids="",
        )

    def test_empty_active_means_no_accounts(self) -> None:
        pairs = build_account_options(self._config([]))
        names = [name for name, _opts in pairs]
        self.assertEqual(names, [])

    def test_only_selected_accounts_included(self) -> None:
        pairs = build_account_options(self._config(["小号"]))
        names = [name for name, _opts in pairs]
        self.assertEqual(names, ["小号"])

    def test_two_active_accounts_are_both_included(self) -> None:
        pairs = build_account_options(self._config(["主号", "小号"]))
        names = [name for name, _opts in pairs]
        self.assertEqual(names, ["主号", "小号"])

    def test_same_bilibili_uid_selected_twice_builds_one_option(self) -> None:
        cfg = self._config(["主号", "小号"])
        cfg.accounts = [
            AccountProfile(name="主号", cookie="DedeUserID=42; SESSDATA=a; bili_jct=x; buvid3=one"),
            AccountProfile(name="小号", cookie="buvid3=two; bili_jct=y; SESSDATA=b; DedeUserID=42"),
        ]
        duplicates: list[tuple[str, str]] = []

        pairs = build_account_options(cfg, lambda duplicate, kept: duplicates.append((duplicate, kept)))

        self.assertEqual([name for name, _opts in pairs], ["主号"])
        self.assertEqual(duplicates, [("小号", "主号")])

    def test_distinct_bilibili_uids_are_kept(self) -> None:
        cfg = self._config(["主号", "小号"])
        cfg.accounts = [
            AccountProfile(name="主号", cookie="DedeUserID=41; SESSDATA=a"),
            AccountProfile(name="小号", cookie="DedeUserID=42; SESSDATA=a"),
        ]
        self.assertEqual(
            [name for name, _opts in build_account_options(cfg)],
            ["主号", "小号"],
        )

    def test_each_option_uses_account_cookie_and_shared_settings(self) -> None:
        pairs = build_account_options(self._config(["主号"]))
        _name, opts = pairs[0]
        self.assertEqual(opts.cookie, "SESSDATA=a")
        self.assertEqual(opts.room_id, "23612045")
        self.assertEqual(opts.watch_threads, 2)

    def test_task_ids_accept_chinese_commas_and_other_separators(self) -> None:
        cfg = self._config(["主号"])
        cfg.task_ids = "task-a，task-b, task-c；task-d\n task-e"

        pairs = build_account_options(cfg)
        _name, opts = pairs[0]

        self.assertEqual(opts.task_ids, ["task-a", "task-b", "task-c", "task-d", "task-e"])


class FakeWatcher:
    instances: list["FakeWatcher"] = []

    def __init__(self, options, log):
        self.options = options
        self.log = log
        self.started = False
        self.stopped = False
        FakeWatcher.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    @property
    def running(self):
        return self.started and not self.stopped


class MultiAccountWatcherLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        FakeWatcher.instances = []

    def _pairs(self):
        return [("主号", WatchOptions(cookie="a", room_id="1")),
                ("小号", WatchOptions(cookie="b", room_id="1"))]

    def test_creates_one_watcher_per_account(self) -> None:
        mw = MultiAccountWatcher(self._pairs(), log=lambda _m: None,
                                 watcher_factory=FakeWatcher, stagger_seconds=0)
        mw.start()
        mw._await_start_for_test()
        self.assertEqual(len(FakeWatcher.instances), 2)
        self.assertTrue(all(w.started for w in FakeWatcher.instances))

    def test_constructor_defensively_collapses_duplicate_accounts(self) -> None:
        logs: list[str] = []
        pairs = [
            ("主号", WatchOptions(cookie="DedeUserID=42; SESSDATA=a", room_id="1")),
            ("别名", WatchOptions(cookie="DedeUserID=42; SESSDATA=b", room_id="1")),
        ]
        MultiAccountWatcher(
            pairs,
            log=logs.append,
            watcher_factory=FakeWatcher,
            stagger_seconds=0,
        )

        self.assertEqual(len(FakeWatcher.instances), 1)
        self.assertTrue(any("别名" in message and "合并" in message for message in logs))

    def test_running_true_when_any_child_running(self) -> None:
        mw = MultiAccountWatcher(self._pairs(), log=lambda _m: None,
                                 watcher_factory=FakeWatcher, stagger_seconds=0)
        self.assertFalse(mw.running)
        mw.start()
        mw._await_start_for_test()
        self.assertTrue(mw.running)

    def test_stop_stops_all_children(self) -> None:
        mw = MultiAccountWatcher(self._pairs(), log=lambda _m: None,
                                 watcher_factory=FakeWatcher, stagger_seconds=0)
        mw.start()
        mw._await_start_for_test()
        mw.stop()
        self.assertTrue(all(w.stopped for w in FakeWatcher.instances))
        self.assertFalse(mw.running)

    def test_stop_waits_for_child_cleanup_with_shared_budget(self) -> None:
        waited: list[float] = []

        class WaitingWatcher(FakeWatcher):
            def wait_for_stop(self, timeout: float) -> bool:
                waited.append(timeout)
                return True

        mw = MultiAccountWatcher(
            self._pairs(),
            log=lambda _m: None,
            watcher_factory=WaitingWatcher,
            stagger_seconds=0,
        )
        mw.start()
        mw._await_start_for_test()

        mw.stop()

        self.assertEqual(len(waited), 2)
        self.assertTrue(all(0 < timeout <= 2.0 for timeout in waited))


class MultiAccountWatcherLogTest(unittest.TestCase):
    def test_child_logs_are_prefixed_with_account_name(self) -> None:
        logs: list[str] = []

        class LoggingWatcher:
            def __init__(self, options, log):
                self.log = log
                self.running = False
            def start(self):
                self.running = True
                self.log("首次计时请求已提交")
            def stop(self):
                self.running = False

        pairs = [("主号", WatchOptions(cookie="a", room_id="1"))]
        mw = MultiAccountWatcher(pairs, log=logs.append,
                                 watcher_factory=LoggingWatcher, stagger_seconds=0)
        mw.start()
        mw._await_start_for_test()
        self.assertIn("[主号] 首次计时请求已提交", logs)


class MultiAccountStatusTest(unittest.TestCase):
    def test_summary_exposes_child_totalv2_rate(self) -> None:
        rates = iter([1.0, 2.0])

        class StatusWatcher:
            def __init__(self, options, log):
                self.running = True
                self.rate = next(rates)
            def start(self): ...
            def stop(self): self.running = False
            def get_server_credit_rate(self): return self.rate
            def get_watch_status_snapshot(self):
                return ([WatchWorkerStatus(worker_id=1, state="正常", interval=60, message="")], "")

        mw = MultiAccountWatcher(
            [("主号", WatchOptions(cookie="a", room_id="1")),
             ("小号", WatchOptions(cookie="b", room_id="1"))],
            log=lambda _m: None,
            watcher_factory=StatusWatcher,
            stagger_seconds=0,
        )

        _rows, summary = mw.get_watch_status_snapshot()

        self.assertIn("2/2 心跳已接受", summary)
        self.assertIn("目标 2x", summary)
        self.assertIn("各账号实绩约 1.0x-2.0x", summary)

    def test_summary_uses_requested_route_target(self) -> None:
        class StatusWatcher:
            def __init__(self, options, log):
                self.running = True
            def start(self): ...
            def stop(self): self.running = False
            def get_server_credit_rate(self): return 6.5
            def get_watch_status_snapshot(self):
                return ([WatchWorkerStatus(worker_id=1, state="正常", interval=60, message="")], "")

        mw = MultiAccountWatcher(
            [("主号", WatchOptions(cookie="a", room_id="1", watch_threads=100))],
            log=lambda _m: None,
            watcher_factory=StatusWatcher,
            stagger_seconds=0,
        )

        _rows, summary = mw.get_watch_status_snapshot()

        self.assertIn("目标 100x", summary)
        self.assertIn("B 站实绩约 6.5x", summary)

    def test_snapshot_has_one_row_per_account_with_name(self) -> None:
        class StatusWatcher:
            def __init__(self, options, log, summary):
                self.running = True
                self._summary = summary
            def start(self): ...
            def stop(self): self.running = False
            def get_watch_status_snapshot(self):
                return ([WatchWorkerStatus(worker_id=1, state="正常", interval=60, message="")], self._summary)

        pairs = [("主号", WatchOptions(cookie="a", room_id="1")),
                 ("小号", WatchOptions(cookie="b", room_id="1"))]
        # 用偏函数给两个账号不同 summary
        summaries = iter(["后台计时状态：1/1 正常", "后台计时状态：0/1 正常，1 路等待开播"])
        mw = MultiAccountWatcher(
            pairs, log=lambda _m: None,
            watcher_factory=lambda options, log: StatusWatcher(options, log, next(summaries)),
            stagger_seconds=0,
        )
        rows, summary = mw.get_watch_status_snapshot()
        self.assertEqual(len(rows), 2)
        self.assertEqual([r.worker_id for r in rows], [1, 2])
        self.assertTrue(any("主号" in r.message for r in rows))
        self.assertTrue(any("小号" in r.message for r in rows))
        self.assertIn("账号", summary)

    def test_local_watch_estimate_uses_wall_clock_max_not_account_sum(self) -> None:
        values = iter([3.0, 5.0])

        class EstimateWatcher:
            def __init__(self, options, log):
                self.value = next(values)
                self.running = False

            def get_local_watch_estimate_minutes(self):
                return self.value

        mw = MultiAccountWatcher(
            [("主号", WatchOptions(cookie="a", room_id="1")),
             ("小号", WatchOptions(cookie="b", room_id="1"))],
            log=lambda _m: None,
            watcher_factory=EstimateWatcher,
            stagger_seconds=0,
        )

        self.assertEqual(mw.get_local_watch_estimate_minutes(), 5.0)


class MultiAccountDelegationTest(unittest.TestCase):
    def test_claim_triggers_all_children(self) -> None:
        claimed: list[str] = []
        refreshed: list[str] = []
        rediscovered: list[str] = []

        def make(name):
            class W:
                def __init__(self, options, log):
                    self.running = True
                def start(self): ...
                def stop(self): self.running = False
                def claim_completed_tasks(self): claimed.append(name)
                def refresh_progress_once(self): refreshed.append(name)
                def rediscover_tasks_once(self): rediscovered.append(name)
            return W

        pairs = [("主号", WatchOptions(cookie="a", room_id="1")),
                 ("小号", WatchOptions(cookie="b", room_id="1"))]
        names = iter(["主号", "小号"])
        mw = MultiAccountWatcher(
            pairs, log=lambda _m: None,
            watcher_factory=lambda options, log: make(next(names))(options, log),
            stagger_seconds=0,
        )
        mw.claim_completed_tasks()
        mw._await_claim_for_test()
        mw.refresh_progress_once()
        mw.rediscover_tasks_once()
        self.assertEqual(sorted(claimed), ["主号", "小号"])
        self.assertEqual(sorted(refreshed), ["主号", "小号"])
        self.assertEqual(sorted(rediscovered), ["主号", "小号"])

    def test_one_child_failure_does_not_block_others(self) -> None:
        # 单账号刷新抛异常时，其余账号仍应被调用（失败隔离）。
        refreshed: list[str] = []

        def make(name, boom):
            class W:
                def __init__(self, options, log):
                    self.running = True
                def start(self): ...
                def stop(self): self.running = False
                def refresh_progress_once(self):
                    if boom:
                        raise RuntimeError("网络炸了")
                    refreshed.append(name)
            return W

        pairs = [("坏号", WatchOptions(cookie="a", room_id="1")),
                 ("好号", WatchOptions(cookie="b", room_id="1"))]
        specs = iter([("坏号", True), ("好号", False)])
        logs: list[str] = []
        mw = MultiAccountWatcher(
            pairs, log=logs.append,
            watcher_factory=lambda options, log: make(*next(specs))(options, log),
            stagger_seconds=0,
        )
        mw.refresh_progress_once()
        self.assertEqual(refreshed, ["好号"])
        self.assertTrue(any("坏号" in m and "失败" in m for m in logs))


class MultiAccountEdgeCaseTest(unittest.TestCase):
    def test_snapshot_handles_child_without_snapshot_method(self) -> None:
        mw = MultiAccountWatcher(
            [("旧版账号", WatchOptions(cookie="a", room_id="1"))],
            log=lambda _m: None,
            watcher_factory=FakeWatcher,
            stagger_seconds=0,
        )

        rows, summary = mw.get_watch_status_snapshot()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].state, "启动中")
        self.assertIn("旧版账号", rows[0].message)
        self.assertIn("0/1", summary)

    def test_empty_accounts_running_false_and_zero_summary(self) -> None:
        mw = MultiAccountWatcher([], log=lambda _m: None, stagger_seconds=0)
        self.assertFalse(mw.running)
        rows, summary = mw.get_watch_status_snapshot()
        self.assertEqual(rows, [])
        self.assertIn("0/0", summary)

    def test_snapshot_isolates_failing_child(self) -> None:
        # 某账号状态读取抛异常时，聚合快照不应崩溃，其余账号照常显示。
        class GoodWatcher:
            def __init__(self, options, log):
                self.running = True
            def start(self): ...
            def stop(self): self.running = False
            def get_watch_status_snapshot(self):
                return ([WatchWorkerStatus(worker_id=1, state="正常", interval=60, message="")],
                        "后台计时状态：1/1 正常")

        class BadWatcher(GoodWatcher):
            def get_watch_status_snapshot(self):
                raise RuntimeError("boom")

        pairs = [("坏号", WatchOptions(cookie="a", room_id="1")),
                 ("好号", WatchOptions(cookie="b", room_id="1"))]
        factories = iter([BadWatcher, GoodWatcher])
        mw = MultiAccountWatcher(
            pairs, log=lambda _m: None,
            watcher_factory=lambda options, log: next(factories)(options, log),
            stagger_seconds=0,
        )
        rows, summary = mw.get_watch_status_snapshot()
        self.assertEqual(len(rows), 2)

    def test_staggered_start_does_not_start_after_stop(self) -> None:
        # 已请求停止后，错峰启动不应再拉起任何账号。
        FakeWatcher.instances = []
        mw = MultiAccountWatcher(
            [("主号", WatchOptions(cookie="a", room_id="1")),
             ("小号", WatchOptions(cookie="b", room_id="1"))],
            log=lambda _m: None, watcher_factory=FakeWatcher, stagger_seconds=0,
        )
        mw.stop()
        mw._staggered_start()
        self.assertTrue(all(not w.started for w in FakeWatcher.instances))

    def test_stop_aborts_staggered_claim(self) -> None:
        # 已请求停止后，错峰领取不应再对子账号发起领取。
        claimed: list[str] = []

        def make(name):
            class W:
                def __init__(self, options, log):
                    self.running = True
                def start(self): ...
                def stop(self): self.running = False
                def claim_completed_tasks(self): claimed.append(name)
            return W

        pairs = [("主号", WatchOptions(cookie="a", room_id="1")),
                 ("小号", WatchOptions(cookie="b", room_id="1"))]
        names = iter(["主号", "小号"])
        mw = MultiAccountWatcher(
            pairs, log=lambda _m: None,
            watcher_factory=lambda options, log: make(next(names))(options, log),
            stagger_seconds=0,
        )
        mw.stop()  # 先请求停止
        mw._staggered_claim()  # 直接同步调用，验证停止后不领取
        self.assertEqual(claimed, [])
