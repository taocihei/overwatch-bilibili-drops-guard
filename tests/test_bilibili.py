from __future__ import annotations

import unittest

import requests

from bili_drop_guard.bilibili import _decode_json_response, normalize_room_id


class BilibiliRoomTest(unittest.TestCase):
    def test_normalize_room_id_accepts_number(self) -> None:
        self.assertEqual(normalize_room_id(" 123456 "), "123456")

    def test_normalize_room_id_accepts_live_url(self) -> None:
        self.assertEqual(normalize_room_id("https://live.bilibili.com/123456?spm_id_from=333"), "123456")

    def test_normalize_room_id_accepts_blanc_url(self) -> None:
        self.assertEqual(normalize_room_id("https://live.bilibili.com/blanc/123456"), "123456")

    def test_normalize_room_id_rejects_unrelated_text(self) -> None:
        self.assertEqual(normalize_room_id("房间：123456"), "")

    def test_decode_json_response_reports_html_response(self) -> None:
        response = requests.Response()
        response.status_code = 200
        response._content = b"<html>blocked</html>"
        response.headers["Content-Type"] = "text/html"

        with self.assertRaisesRegex(RuntimeError, "未返回 JSON"):
            _decode_json_response(response)

    def test_decode_json_response_reports_http_status(self) -> None:
        response = requests.Response()
        response.status_code = 412
        response.url = "https://api.bilibili.com/test"
        response._content = "风控".encode("utf-8")
        response.headers["Content-Type"] = "text/plain"

        with self.assertRaisesRegex(RuntimeError, "HTTP 412"):
            _decode_json_response(response)


if __name__ == "__main__":
    unittest.main()
