from __future__ import annotations

import ctypes
import traceback

from bili_drop_guard.config import APP_DIR
from bili_drop_guard.gui import main


def _run() -> None:
    try:
        main()
    except Exception as exc:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        log_path = APP_DIR / "crash.log"
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        log_path.write_text(detail, encoding="utf-8")
        try:
            ctypes.windll.user32.MessageBoxW(
                None,
                f"程序启动失败，详情已写入：\n{log_path}\n\n{exc}",
                "守望先锋 B 站直播挂宝",
                0x10,
            )
        except Exception:
            pass
        raise


if __name__ == "__main__":
    _run()
