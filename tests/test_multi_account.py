from __future__ import annotations

import unittest

from bili_drop_guard.config import AppConfig, AccountProfile
from bili_drop_guard.multi_account import build_account_options


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
