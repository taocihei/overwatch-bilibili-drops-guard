from __future__ import annotations

import unittest

from bili_drop_guard import notifier


class NotifierTest(unittest.TestCase):
    def test_empty_url_does_not_send(self) -> None:
        self.assertFalse(notifier.send_notification("", "标题", "内容"))

    def test_send_notification_posts_json_payload(self) -> None:
        calls: list[tuple[str, dict[str, object], int]] = []

        class Response:
            def raise_for_status(self) -> None:
                return None

        def fake_post(url: str, json: dict[str, object], timeout: int) -> Response:
            calls.append((url, json, timeout))
            return Response()

        original_post = notifier.requests.post
        notifier.requests.post = fake_post  # type: ignore[assignment]
        try:
            result = notifier.send_notification("https://example.com/hook", "标题", "已领取：奖励", "info")
        finally:
            notifier.requests.post = original_post  # type: ignore[assignment]

        self.assertTrue(result)
        self.assertEqual(calls[0][0], "https://example.com/hook")
        self.assertEqual(calls[0][1]["title"], "标题")
        self.assertEqual(calls[0][1]["message"], "已领取：奖励")
        self.assertEqual(calls[0][1]["level"], "info")
        self.assertEqual(calls[0][1]["source"], "OverwatchBiliDrops")
        self.assertEqual(calls[0][2], 8)


if __name__ == "__main__":
    unittest.main()
