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

    def test_empty_active_means_all_accounts(self) -> None:
        pairs = build_account_options(self._config([]))
        names = [name for name, _opts in pairs]
        self.assertEqual(names, ["主号", "小号"])

    def test_only_selected_accounts_included(self) -> None:
        pairs = build_account_options(self._config(["小号"]))
        names = [name for name, _opts in pairs]
        self.assertEqual(names, ["小号"])

    def test_each_option_uses_account_cookie_and_shared_settings(self) -> None:
        pairs = build_account_options(self._config(["主号"]))
        _name, opts = pairs[0]
        self.assertEqual(opts.cookie, "SESSDATA=a")
        self.assertEqual(opts.room_id, "23612045")
        self.assertEqual(opts.watch_threads, 2)


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
