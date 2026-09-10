from __future__ import annotations

import gc
import queue
import threading
import time
import unittest
import weakref
from unittest.mock import MagicMock, patch

from bili_drop_guard import gui
from bili_drop_guard import cookie_capture
from bili_drop_guard.bilibili import BilibiliClient, LoginInfo, RoomInfo
from bili_drop_guard.config import AccountProfile, AppConfig
from bili_drop_guard.multi_account import MultiAccountWatcher
from bili_drop_guard.sponsor import SponsorOrder, SponsorUnavailable
from bili_drop_guard.watcher import LiveWatcher, WatchOptions
from tests.test_gui_notifications import FakeLogText, FakeText, FakeVar


class AuditRegressionTests(unittest.TestCase):
    def test_app_close_signals_cookie_capture_without_blocking_tk(self):
        app = object.__new__(gui.App)
        app._cookie_capture_cancel = threading.Event()
        app.watcher = None
        app._save_runtime_settings_on_close = MagicMock()
        with patch.object(gui.tk.Tk, 'destroy') as destroy:
            app.destroy()
        self.assertTrue(app._cookie_capture_cancel.is_set())
        self.assertTrue(app._closing)
        destroy.assert_called_once()

    def test_multi_account_stop_reports_late_child_after_wait_budget(self):
        coordinator = MultiAccountWatcher([], lambda _msg: None)
        first, last = MagicMock(), MagicMock()
        first.wait_for_stop.return_value = True
        last.wait_for_stop.return_value = False
        coordinator._children = [('A', first), ('B', last)]
        with patch('bili_drop_guard.multi_account.time.monotonic', side_effect=[0, 10, 10]):
            self.assertFalse(coordinator.stop())
        last.wait_for_stop.assert_called_once_with(timeout=0.0)

    def test_cancel_capture_closes_browser_without_starting_fallback(self):
        cancel = threading.Event()
        driver = MagicMock()
        attached = MagicMock()
        driver.get.side_effect = lambda _url: cancel.set()
        with (patch.object(cookie_capture, '_launch_browser_for_attach', return_value=attached),
              patch.object(cookie_capture, '_load_webdriver_class', return_value=MagicMock(return_value=driver)) as factory,
              patch.object(cookie_capture, '_close_attached_browser') as close,
              patch.object(cookie_capture, 'open_bilibili_login_page') as fallback):
            with self.assertRaises(cookie_capture.CaptureCancelled):
                cookie_capture.capture_bilibili_cookie(cancel_event=cancel)
        driver.quit.assert_called_once()
        close.assert_called_once_with(attached)
        factory.assert_called_once()
        fallback.assert_not_called()

    def test_cancel_before_capture_does_not_open_a_browser(self):
        cancel = threading.Event()
        cancel.set()
        with patch.object(cookie_capture, '_launch_browser_for_attach') as launch:
            with self.assertRaises(cookie_capture.CaptureCancelled):
                cookie_capture.capture_bilibili_cookie(cancel_event=cancel)
        launch.assert_not_called()

    def test_cookie_result_does_not_restore_a_deleted_account(self):
        app = object.__new__(gui.App)
        app.config_data = AppConfig(accounts=[AccountProfile('B', 'old-B')])
        app.editing_account_name = 'B'
        app.account_name_var = FakeVar('B')
        app.cookie_text = FakeText('old-B')
        app._log = MagicMock()
        with patch.object(gui, 'save_config') as save:
            app._apply_captured_cookie({'account': 'A', 'previous_cookie': 'old-A', 'cookie': 'new-A'})
        save.assert_not_called()
        self.assertEqual(app.cookie_text.value, 'old-B')

    def test_cookie_capture_preserves_unchanged_draft_name_for_same_editor(self):
        app = object.__new__(gui.App)
        app.config_data = AppConfig(accounts=[AccountProfile('A', 'old-A')])
        app.editing_account_name = 'A'
        app.account_name_var = FakeVar('renamed-A')
        app.cookie_text = FakeText('old-A')
        app._refresh_cookie_placeholder = MagicMock()
        app._save_account = MagicMock()
        app._apply_captured_cookie({'account': 'A', 'draft_name': 'renamed-A',
                                    'previous_cookie': 'old-A', 'cookie': 'new-A'})
        app._save_account.assert_called_once()
        self.assertEqual(app.cookie_text.value, 'new-A')
        self.assertEqual(app.account_name_var.get(), 'renamed-A')

    def test_cookie_result_updates_origin_account_without_overwriting_current_editor(self):
        app = object.__new__(gui.App)
        app.config_data = AppConfig(accounts=[AccountProfile('A', 'old-A'), AccountProfile('B', 'old-B')])
        app.editing_account_name = 'B'
        app.account_name_var = FakeVar('B')
        app.cookie_text = FakeText('old-B')
        app._log = MagicMock()
        app._refresh_account_selector = MagicMock()
        with patch.object(gui, 'save_config') as save:
            app._apply_captured_cookie({'account': 'A', 'previous_cookie': 'old-A', 'cookie': 'new-A'})
        self.assertEqual(app.cookie_text.value, 'old-B')
        self.assertEqual([(a.name, a.cookie) for a in save.call_args.args[0].accounts], [('A', 'new-A'), ('B', 'old-B')])

    def test_empty_generic_response_does_not_hide_activity_failure(self):
        watcher = LiveWatcher(WatchOptions(cookie='a=b', room_id='1'), lambda _m: None)
        client = MagicMock()
        client.discover_live_activity_tasks.side_effect = RuntimeError('changed page')
        client.get_user_task_progress.return_value = {'list': []}
        for _ in range(3):
            watcher._poll_task_features(client, 2)
        self.assertTrue(watcher.task_monitor_degraded)
        self.assertFalse(watcher._stop.is_set())

    def test_login_transport_failure_is_retryable(self):
        with patch('bili_drop_guard.bilibili.requests.Session') as session:
            session.return_value.get.side_effect = TimeoutError('offline')
            client = BilibiliClient('a=b')
            with self.assertRaisesRegex(RuntimeError, '登录状态检查失败'):
                client.check_login()
            client.close()

    def test_watcher_retries_login_and_starts_after_network_recovers(self):
        watcher = LiveWatcher(WatchOptions(cookie='a=b', room_id='1'), lambda _m: None)
        client = MagicMock()
        client.check_login.side_effect = [RuntimeError('offline'), LoginInfo(True, uname='tester', mid=1)]
        client.get_room_info.return_value = RoomInfo(room_id=1, live_status=1, anchor_uid=2)
        watcher._start_watch_threads = MagicMock(side_effect=lambda _room: watcher._stop.set())
        watcher._ensure_task_monitor_started = MagicMock()
        with patch('bili_drop_guard.watcher.BilibiliClient', return_value=client), patch.object(watcher._stop, 'wait', return_value=False):
            watcher._run()
        self.assertEqual(client.check_login.call_count, 2)
        watcher._start_watch_threads.assert_called_once()

    def test_stop_checks_all_threads_even_when_join_budget_expires(self):
        watcher = LiveWatcher(WatchOptions(cookie='a=b', room_id='1'), lambda _m: None)
        watcher._thread = MagicMock()
        watcher._thread.is_alive.return_value = False
        watcher._claim_thread = MagicMock()
        watcher._claim_thread.is_alive.return_value = True
        with patch('bili_drop_guard.watcher.time.monotonic', side_effect=[0, 10]):
            self.assertFalse(watcher.wait_for_stop(1))

    def test_zero_credit_never_claims_a_verified_live_time_cap(self):
        watcher = LiveWatcher(WatchOptions(cookie='a=b', room_id='1', watch_threads=100), lambda _m: None)
        for i in range(1, 101):
            watcher._set_watch_status(i, '正常', interval=60)
        watcher._record_server_progress(140, 1000)
        watcher._record_server_progress(140, 1090)
        self.assertNotIn('已追平', watcher.get_watch_status_snapshot()[1])

    def sponsor_app(self):
        app = object.__new__(gui.App)
        app._sponsor_cache_lock = threading.RLock()
        app._sponsor_order_cache = {}
        app._sponsor_order_inflight = {}
        app._sponsor_order_errors = {}
        app._sponsor_install_id = 'test-install-1234'
        app._sponsor_checkout_intent_id = 'test-intent-1234'
        app._persist_sponsor_order_cache = MagicMock()
        return app

    def test_cache_sweeps_expired_amounts_and_limits_live_entries(self):
        app = self.sponsor_app()
        clients = [MagicMock() for _ in range(10)]
        for i, client in enumerate(clients):
            app._sponsor_order_cache[str(i)] = (time.time() - gui.SPONSOR_ORDER_CACHE_TTL_SECONDS - 1, client, SponsorOrder('expired-order'), b'png')
        app._cache_sponsor_order('10.00', MagicMock(), SponsorOrder('new-order', expires_in_seconds=600), b'png')
        self.assertEqual(list(app._sponsor_order_cache), ['10.00'])
        for client in clients:
            client.close.assert_called_once()
        for i in range(40):
            app._cache_sponsor_order(f'{i}.01', MagicMock(), SponsorOrder(f'order-{i}', expires_in_seconds=600), b'png')
        self.assertLessEqual(len(app._sponsor_order_cache), 16)

    def test_failed_orders_release_clients_and_cache_only_error_text(self):
        app = self.sponsor_app()
        refs = []

        class Client:
            closed = 0

            def __init__(self):
                refs.append(weakref.ref(self))

            def create_order(self, *_a, **_kw):
                raise SponsorUnavailable('offline')

            def close(self):
                Client.closed += 1

        with patch.object(gui.SponsorClient, 'from_environment', side_effect=Client), patch.object(gui.time, 'sleep'):
            for i in range(10):
                with self.assertRaises(SponsorUnavailable):
                    app._get_or_create_sponsor_order(f'{i + 1}.00')
        gc.collect()
        self.assertEqual(Client.closed, 10)
        self.assertTrue(all(ref() is None for ref in refs))
        self.assertTrue(all(isinstance(value, str) for value in app._sponsor_order_errors.values()))

    def test_hidden_logs_do_not_rewrite_visible_history(self):
        app = object.__new__(gui.App)
        app.log_entries = [('task', 'x' * 200 + '\n')] * 1900
        app.log_view_var = FakeVar('task')
        app.log_text = FakeLogText()
        app.auto_scroll_var = FakeVar(False)
        app.after = MagicMock()
        app._render_log_text()
        with patch.object(app.log_text, 'insert', wraps=app.log_text.insert) as insert:
            for _ in range(100):
                app._log('房间 1：直播中')
        self.assertEqual(insert.call_count, 0)

    def test_log_drain_yields_without_emptying_a_large_queue(self):
        app = object.__new__(gui.App)
        app.log_queue = queue.Queue()
        for _ in range(1000):
            app.log_queue.put('ordinary message')
        app._log = MagicMock()
        app._notify_from_message = MagicMock()
        app.after = MagicMock()
        app._drain_logs()
        self.assertGreater(app.log_queue.qsize(), 0)
        self.assertGreater(app._log.call_count, 0)

    def test_visible_logs_are_inserted_once_per_drain_and_only_append_new_text(self):
        app = object.__new__(gui.App)
        app.log_entries = [('task', 'previous history\n')]
        app.log_view_var = FakeVar('task')
        app.log_text = FakeLogText()
        app.auto_scroll_var = FakeVar(False)
        app._notify_from_message = MagicMock()
        app.after = MagicMock()
        app.log_queue = queue.Queue()
        for _ in range(100):
            app.log_queue.put('ordinary message')
        app._render_log_text()
        with (patch.object(app.log_text, 'insert', wraps=app.log_text.insert) as insert,
              patch.object(app.log_text, 'delete', wraps=app.log_text.delete) as delete):
            app._drain_logs()
        self.assertEqual(insert.call_count, 1)
        delete.assert_not_called()
        self.assertNotIn('previous history', insert.call_args.args[1])
        self.assertTrue(app.log_text.value.startswith('previous history\n'))

    def test_ready_amount_does_not_wait_for_another_slow_amount(self):
        app = self.sponsor_app()
        app.preview_mode = False
        app._sponsor_warm_started = False
        app._sponsor_warm_ready = threading.Event()
        app._sponsor_prefetch_ready = threading.Event()
        blocked = threading.Event()
        release = threading.Event()

        def create(amount, **_kw):
            if amount == '100.00':
                blocked.set()
                release.wait(2)
            app._cache_sponsor_order(amount, MagicMock(), SponsorOrder('ready-order', expires_in_seconds=600), b'png')

        app._create_sponsor_order_network = create
        slow_batch = MagicMock()
        slow_batch.reserve_orders.side_effect = lambda *_a, **_k: (blocked.set(), release.wait(2), (_ for _ in ()).throw(SponsorUnavailable('batch failed')))[-1]
        try:
            with patch.object(gui.SponsorClient, 'from_environment', return_value=slow_batch):
                app._warm_sponsor_service()
                self.assertTrue(blocked.wait(1))
                deadline = time.monotonic() + 0.5
                while app._cached_sponsor_order('5.00') is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertIsNotNone(app._cached_sponsor_order('5.00'))
        finally:
            release.set()
            self.assertTrue(app._sponsor_prefetch_ready.wait(3))
