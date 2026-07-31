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


COOKIE_WITH_BUVID = (
    "SESSDATA=abc; bili_jct=xyz; buvid3=SHARED-BUVID-FROM-COOKIE; "
    "buvid4=SHARED-BUVID4; buvid_fp=SHARED-FP; LIVE_BUVID=SHARED-LIVE; "
    "_uuid=SHARED-UUID; b_lsid=SHARED-LSID; b_nut=1"
)


class MakeSessionIdentityTest(unittest.TestCase):
    def test_make_session_buvid_returns_live_buvid_shape(self) -> None:
        self.assertRegex(make_session_buvid(), r"^AUTO\d{16}$")

    def test_make_session_buvid_is_unique_each_call(self) -> None:
        values = {make_session_buvid() for _ in range(50)}
        self.assertEqual(len(values), 50)

    def test_make_session_device_uuid_is_unique_each_call(self) -> None:
        values = {make_session_device_uuid() for _ in range(50)}
        self.assertEqual(len(values), 50)
        for value in values:
            self.assertEqual(len(value), 36)
            self.assertEqual(value.count("-"), 4)


class BilibiliClientSessionIdentityTest(unittest.TestCase):
    def test_explicit_session_buvid_only_overrides_heartbeat_body_identity(self) -> None:
        client = BilibiliClient(COOKIE_WITH_BUVID, session_buvid="MY-SESSION-BUVID")
        self.addCleanup(client.close)

        self.assertEqual(client.buvid, "MY-SESSION-BUVID")
        self.assertEqual(
            client.session.cookies.get("LIVE_BUVID", domain=".bilibili.com"),
            "SHARED-LIVE",
        )

    def test_explicit_session_device_uuid_overrides_default(self) -> None:
        client = BilibiliClient(
            COOKIE_WITH_BUVID,
            session_device_uuid="my-device-uuid-1234",
        )
        self.addCleanup(client.close)

        self.assertEqual(client.device_uuid, "my-device-uuid-1234")

    def test_without_overrides_generates_live_buvid(self) -> None:
        client = BilibiliClient(COOKIE_WITH_BUVID)
        self.addCleanup(client.close)

        self.assertRegex(client.buvid, r"^AUTO\d{16}$")
        self.assertEqual(client.session.cookies.get("LIVE_BUVID", domain=".bilibili.com"), "SHARED-LIVE")

    def test_session_identity_does_not_rewrite_cookie_buvid3(self) -> None:
        client = BilibiliClient(COOKIE_WITH_BUVID, session_buvid="MY-FRESH-SESSION-BUVID")
        self.addCleanup(client.close)

        self.assertEqual(
            client.session.cookies.get("buvid3", domain=".bilibili.com"),
            "SHARED-BUVID-FROM-COOKIE",
        )

    def test_session_identity_preserves_authenticated_device_cookie_set(self) -> None:
        client = BilibiliClient(
            COOKIE_WITH_BUVID,
            session_buvid="MY-FRESH-SESSION-BUVID",
            session_device_uuid="device-1",
        )
        self.addCleanup(client.close)

        for name, original in {
            "buvid4": "SHARED-BUVID4",
            "buvid_fp": "SHARED-FP",
            "_uuid": "SHARED-UUID",
            "b_lsid": "SHARED-LSID",
            "b_nut": "1",
        }.items():
            self.assertEqual(
                client.session.cookies.get(name, domain=".bilibili.com"),
                original,
            )
        self.assertEqual(
            client.session.cookies.get("LIVE_BUVID", domain=".bilibili.com"),
            "SHARED-LIVE",
        )


class WatcherHeartbeatWorkerUsesUniqueSessionIdentityTest(unittest.TestCase):
    def test_each_worker_creates_client_with_distinct_x25_identity(self) -> None:
        captured_buvids: list[str] = []
        captured_device_uuids: list[str] = []
        closed_clients: list[str] = []
        original_client = watcher_module.BilibiliClient

        class RecordingClient:
            def __init__(
                self,
                cookie_header: str,
                *,
                session_buvid: str | None = None,
                session_device_uuid: str | None = None,
            ) -> None:
                self.cookie_header = cookie_header
                self._buvid = session_buvid or "fallback"
                self._device_uuid = session_device_uuid or "fallback-device"
                captured_buvids.append(self._buvid)
                captured_device_uuids.append(self._device_uuid)

            def close(self) -> None:
                closed_clients.append(self._buvid)

        watcher_module.BilibiliClient = RecordingClient
        try:
            live_watcher = LiveWatcher(
                WatchOptions(cookie=COOKIE_WITH_BUVID, room_id="1", watch_threads=5),
                lambda _message: None,
            )
            live_watcher._stop.set()
            for worker_id in range(1, 6):
                live_watcher._heartbeat_watch_worker(
                    worker_id,
                    RoomInfo(room_id=1, live_status=1),
                )
        finally:
            watcher_module.BilibiliClient = original_client

        self.assertEqual(len(captured_buvids), 5)
        self.assertEqual(len(set(captured_buvids)), 5)
        self.assertEqual(len(set(captured_device_uuids)), 5)
        self.assertCountEqual(closed_clients, captured_buvids)
        for buvid in captured_buvids:
            self.assertRegex(buvid, r"^AUTO\d{16}$")


if __name__ == "__main__":
    unittest.main()
