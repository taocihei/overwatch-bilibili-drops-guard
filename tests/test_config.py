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

        self.assertEqual(loaded.check_interval, 60)
        self.assertFalse(loaded.auto_claim)
        self.assertEqual(loaded.watch_threads, config.MAX_WATCH_WINDOWS)
        self.assertEqual(loaded.cookie, "")


if __name__ == "__main__":
    unittest.main()
