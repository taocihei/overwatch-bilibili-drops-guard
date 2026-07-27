from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bili_drop_guard import config
from bili_drop_guard.config import AppConfig, AccountProfile, sanitize_config


class ConfigTest(unittest.TestCase):
    def test_load_config_ignores_future_unknown_fields(self) -> None:
        original_path = config.CONFIG_PATH
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                config.CONFIG_PATH = Path(temp_dir) / "config.json"
                config.CONFIG_PATH.write_text(
                    json.dumps({"check_interval": 42, "future_option": {"enabled": True}}),
                    encoding="utf-8",
                )

                loaded = config.load_config()
            finally:
                config.CONFIG_PATH = original_path

        self.assertEqual(loaded.check_interval, 42)

    def test_save_config_atomically_replaces_existing_file(self) -> None:
        original_path = config.CONFIG_PATH
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                config.CONFIG_PATH = Path(temp_dir) / "config.json"
                config.CONFIG_PATH.write_text('{"room_id":"old"}', encoding="utf-8")

                config.save_config(AppConfig(room_id="123456", check_interval=35))

                saved = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
                temporary_files = list(config.CONFIG_PATH.parent.glob(".config.json.*.tmp"))
            finally:
                config.CONFIG_PATH = original_path

        self.assertEqual(saved["room_id"], "123456")
        self.assertEqual(saved["check_interval"], 35)
        self.assertEqual(temporary_files, [])

    def test_save_config_keeps_old_file_when_replace_fails(self) -> None:
        original_path = config.CONFIG_PATH
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                config.CONFIG_PATH = Path(temp_dir) / "config.json"
                original_content = '{"room_id":"old"}'
                config.CONFIG_PATH.write_text(original_content, encoding="utf-8")

                with patch("bili_drop_guard.config.os.replace", side_effect=OSError("locked")):
                    with self.assertRaisesRegex(OSError, "locked"):
                        config.save_config(AppConfig(room_id="123456"))

                current_content = config.CONFIG_PATH.read_text(encoding="utf-8")
                temporary_files = list(config.CONFIG_PATH.parent.glob(".config.json.*.tmp"))
            finally:
                config.CONFIG_PATH = original_path

        self.assertEqual(current_content, original_content)
        self.assertEqual(temporary_files, [])

    def test_load_config_clears_legacy_default_task_ids(self) -> None:
        original_path = config.CONFIG_PATH
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                config.CONFIG_PATH = Path(temp_dir) / "config.json"
                config.CONFIG_PATH.write_text(
                    json.dumps({"task_ids": config.LEGACY_DEFAULT_TASK_IDS}, ensure_ascii=False),
                    encoding="utf-8",
                )

                loaded = config.load_config()
            finally:
                config.CONFIG_PATH = original_path
        self.assertEqual(loaded.task_ids, "")

    def test_load_config_sanitizes_bad_values(self) -> None:
        original_path = config.CONFIG_PATH
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                config.CONFIG_PATH = Path(temp_dir) / "config.json"
                config.CONFIG_PATH.write_text(
                    json.dumps(
                        {
                            "check_interval": "oops",
                            "auto_claim": "false",
                            "watch_threads": 9999,
                            "cookie": None,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                loaded = config.load_config()
            finally:
                config.CONFIG_PATH = original_path

        self.assertEqual(loaded.check_interval, config.DEFAULT_CHECK_INTERVAL)
        self.assertFalse(loaded.auto_claim)
        self.assertEqual(loaded.watch_threads, config.MAX_WATCH_THREADS)
        self.assertEqual(loaded.cookie, "")
        self.assertEqual(loaded.room_id, config.DEFAULT_ROOM_ID)

    def test_load_config_migrates_cookie_to_account_profile(self) -> None:
        original_path = config.CONFIG_PATH
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                config.CONFIG_PATH = Path(temp_dir) / "config.json"
                config.CONFIG_PATH.write_text(
                    json.dumps({"cookie": "SESSDATA=a;bili_jct=b", "account_name": "主账号", "config_version": 2}, ensure_ascii=False),
                    encoding="utf-8",
                )

                loaded = config.load_config()
            finally:
                config.CONFIG_PATH = original_path

        self.assertEqual(loaded.account_name, "主账号")
        self.assertEqual(loaded.cookie, "SESSDATA=a;bili_jct=b")
        self.assertEqual([(item.name, item.cookie) for item in loaded.accounts], [("主账号", "SESSDATA=a;bili_jct=b")])
        self.assertEqual(loaded.config_version, config.CONFIG_VERSION)

    def test_load_config_migrates_missing_active_accounts_to_all_accounts(self) -> None:
        original_path = config.CONFIG_PATH
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                config.CONFIG_PATH = Path(temp_dir) / "config.json"
                config.CONFIG_PATH.write_text(
                    json.dumps(
                        {
                            "accounts": [
                                {"name": "主账号", "cookie": "SESSDATA=a"},
                                {"name": "小号", "cookie": "SESSDATA=b"},
                            ],
                            "account_name": "主账号",
                            "config_version": config.CONFIG_VERSION,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                loaded = config.load_config()
            finally:
                config.CONFIG_PATH = original_path

        self.assertEqual(loaded.active_accounts, ["主账号", "小号"])

    def test_sanitize_config_uses_selected_account_cookie(self) -> None:
        sanitized = config.sanitize_config(
            config.AppConfig(
                cookie="old-cookie",
                account_name="小号",
                accounts=[
                    config.AccountProfile(name="主账号", cookie="main-cookie"),
                    config.AccountProfile(name="小号", cookie="alt-cookie"),
                ],
            )
        )

        self.assertEqual(sanitized.cookie, "alt-cookie")
        self.assertEqual(sanitized.account_name, "小号")

    def test_sanitize_config_keeps_notify_url(self) -> None:
        sanitized = config.sanitize_config(config.AppConfig(notify_url=" https://example.com/hook "))

        self.assertEqual(sanitized.notify_url, "https://example.com/hook")

    def test_default_config_uses_overwatch_room_url(self) -> None:
        self.assertEqual(config.AppConfig().room_id, config.DEFAULT_ROOM_ID)

    def test_legacy_default_interval_migrates_to_ten_seconds_once(self) -> None:
        original_path = config.CONFIG_PATH
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                config.CONFIG_PATH = Path(temp_dir) / "config.json"
                config.CONFIG_PATH.write_text(
                    json.dumps({"check_interval": config.LEGACY_DEFAULT_CHECK_INTERVAL}, ensure_ascii=False),
                    encoding="utf-8",
                )

                loaded = config.load_config()
            finally:
                config.CONFIG_PATH = original_path

        self.assertEqual(loaded.check_interval, config.DEFAULT_CHECK_INTERVAL)

    def test_saved_new_config_can_keep_sixty_second_interval(self) -> None:
        original_path = config.CONFIG_PATH
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                config.CONFIG_PATH = Path(temp_dir) / "config.json"
                config.CONFIG_PATH.write_text(
                    json.dumps(
                        {"check_interval": config.LEGACY_DEFAULT_CHECK_INTERVAL, "config_version": config.CONFIG_VERSION},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                loaded = config.load_config()
            finally:
                config.CONFIG_PATH = original_path

        self.assertEqual(loaded.check_interval, config.LEGACY_DEFAULT_CHECK_INTERVAL)

    def test_sanitize_config_stores_room_url_as_number(self) -> None:
        room_url = "https://live.bilibili.com/23612045?live_from=82002&spm_id_from=333.788.top_right_bar_window_dynamic.content.click"

        sanitized = config.sanitize_config(config.AppConfig(room_id=room_url))

        self.assertEqual(sanitized.room_id, "23612045")

    def test_sanitize_config_normalizes_task_id_separators(self) -> None:
        sanitized = config.sanitize_config(config.AppConfig(task_ids="task-a，task-b, task-c；task-d\n task-e"))

        self.assertEqual(sanitized.task_ids, "task-a,task-b,task-c,task-d,task-e")
        self.assertEqual(config.parse_task_ids(sanitized.task_ids), ["task-a", "task-b", "task-c", "task-d", "task-e"])

    def test_active_accounts_defaults_to_empty(self) -> None:
        cfg = AppConfig()
        self.assertEqual(cfg.active_accounts, [])

    def test_sanitize_keeps_only_known_active_accounts(self) -> None:
        cfg = AppConfig(
            cookie="SESSDATA=a",
            account_name="主号",
            accounts=[AccountProfile(name="主号", cookie="SESSDATA=a"),
                      AccountProfile(name="小号", cookie="SESSDATA=b")],
            active_accounts=["小号", "不存在的号"],
        )
        cleaned = sanitize_config(cfg)
        # 只保留确实存在的账号名，过滤无效项
        self.assertEqual(cleaned.active_accounts, ["小号"])


if __name__ == "__main__":
    unittest.main()
