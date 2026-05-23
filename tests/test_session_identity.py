from __future__ import annotations

import unittest

from bili_drop_guard import watcher as watcher_module
from bili_drop_guard.bilibili import (
    BilibiliClient,
    RoomInfo,
    make_session_buvid,
    make_session_device_uuid,
)
from bili_drop_guard.watcher import LiveWatcher, WatchOptions


COOKIE_WITH_BUVID = "SESSDATA=abc; bili_jct=xyz; buvid3=SHARED-BUVID-FROM-COOKIE"


class MakeSessionIdentityTest(unittest.TestCase):
    def test_make_session_buvid_returns_uuid_with_infoc_suffix(self) -> None:
        buvid = make_session_buvid()
        self.assertTrue(buvid.endswith("infoc"), f"unexpected format: {buvid}")
        prefix = buvid[: -len("infoc")]
        # uuid string with 4 dashes
        self.assertEqual(prefix.count("-"), 4)
        self.assertEqual(prefix, prefix.upper())

    def test_make_session_buvid_is_unique_each_call(self) -> None:
        values = {make_session_buvid() for _ in range(50)}
        self.assertEqual(len(values), 50)

    def test_make_session_device_uuid_is_unique_each_call(self) -> None:
        values = {make_session_device_uuid() for _ in range(50)}
        self.assertEqual(len(values), 50)


class BilibiliClientSessionIdentityTest(unittest.TestCase):
    def test_explicit_session_buvid_overrides_cookie_buvid(self) -> None:
        client = BilibiliClient(COOKIE_WITH_BUVID, session_buvid="MY-SESSION-BUVID")

        self.assertEqual(client._buvid, "MY-SESSION-BUVID")

    def test_explicit_session_device_uuid_overrides_default(self) -> None:
        client = BilibiliClient(
            COOKIE_WITH_BUVID,
            session_device_uuid="my-device-uuid-1234",
        )

        self.assertEqual(client._device_uuid, "my-device-uuid-1234")

    def test_without_overrides_falls_back_to_cookie_buvid(self) -> None:
        client = BilibiliClient(COOKIE_WITH_BUVID)

        self.assertEqual(client._buvid, "SHARED-BUVID-FROM-COOKIE")

    def test_session_buvid_also_overrides_cookie_buvid3_in_http_session(self) -> None:
        client = BilibiliClient(COOKIE_WITH_BUVID, session_buvid="MY-FRESH-SESSION-BUVID")

        # 关键：HTTP request 用的 cookie 里 buvid3 也得是 session 独立的，B 站去重多半看 cookie。
        self.assertEqual(client.session.cookies.get("buvid3", domain=".bilibili.com"), "MY-FRESH-SESSION-BUVID")

    def test_without_session_buvid_cookie_buvid3_keeps_original(self) -> None:
        client = BilibiliClient(COOKIE_WITH_BUVID)

        self.assertEqual(client.session.cookies.get("buvid3", domain=".bilibili.com"), "SHARED-BUVID-FROM-COOKIE")


class WatcherHeartbeatWorkerUsesUniqueSessionIdentityTest(unittest.TestCase):
    def test_each_worker_creates_client_with_distinct_buvid(self) -> None:
        captured_buvids: list[str] = []
        captured_device_uuids: list[str] = []
        orig_client_cls = watcher_module.BilibiliClient

        class RecordingClient:
            def __init__(self, cookie_header: str, *, session_buvid: str | None = None, session_device_uuid: str | None = None) -> None:
                self.cookie_header = cookie_header
                self._buvid = session_buvid or "fallback"
                self._device_uuid = session_device_uuid or "fallback-device"
                captured_buvids.append(self._buvid)
                captured_device_uuids.append(self._device_uuid)

            def check_login(self):
                from bili_drop_guard.bilibili import LoginInfo
                return LoginInfo(False, message="skip")

        watcher_module.BilibiliClient = RecordingClient
        try:
            live_watcher = LiveWatcher(
                WatchOptions(cookie=COOKIE_WITH_BUVID, room_id="1", watch_threads=5),
                lambda _m: None,
            )
            live_watcher._stop.set()  # so worker loop exits immediately
            for worker_id in range(1, 6):
                live_watcher._heartbeat_watch_worker(worker_id, RoomInfo(room_id=1, live_status=1))
        finally:
            watcher_module.BilibiliClient = orig_client_cls

        # 5 workers ran → 5 clients created → 5 distinct buvids and device_uuids
        self.assertEqual(len(captured_buvids), 5)
        self.assertEqual(len(set(captured_buvids)), 5, f"buvids not unique: {captured_buvids}")
        self.assertEqual(len(set(captured_device_uuids)), 5, f"device_uuids not unique: {captured_device_uuids}")
        # And NONE should equal the cookie's shared buvid3
        for buvid in captured_buvids:
            self.assertNotEqual(buvid, "SHARED-BUVID-FROM-COOKIE")
            self.assertTrue(buvid.endswith("infoc"))


if __name__ == "__main__":
    unittest.main()
