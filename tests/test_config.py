from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bili_drop_guard import config


class ConfigTest(unittest.TestCase):
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
        self.assertEqual(loaded.watch_threads, config.MAX_WATCH_WINDOWS)
        self.assertEqual(loaded.cookie, "")
        self.assertEqual(loaded.room_id, config.DEFAULT_ROOM_ID)

    def test_default_config_uses_overwatch_room_url(self) -> None:
        self.assertEqual(config.AppConfig().room_id, config.DEFAULT_ROOM_ID)

    def test_sanitize_config_stores_room_url_as_number(self) -> None:
        room_url = "https://live.bilibili.com/23612045?live_from=82002&spm_id_from=333.788.top_right_bar_window_dynamic.content.click"

        sanitized = config.sanitize_config(config.AppConfig(room_id=room_url))

        self.assertEqual(sanitized.room_id, "23612045")


if __name__ == "__main__":
    unittest.main()
