"""Additional final-QA regressions; no live Telegram traffic."""
import threading
import unittest
from unittest.mock import Mock, patch

from luna_orchestrator import Orchestrator, TaskStatus
from luna_orchestrator.telegram import TelegramAdapter, TelegramClient, TelegramError


class FinalQATests(unittest.TestCase):
    def test_allowlist_validates_before_deduplication(self):
        for ids in ([1, True], [1, 1.0], [123, 123.0]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                TelegramAdapter(Orchestrator(), ids, Mock())

    def test_telegram_mismatched_chat_does_not_read_or_approve(self):
        o = Mock()
        adapter = TelegramAdapter(o, [123], Mock())
        update = {"message": {
            "from": {"id": 123, "is_bot": False},
            "chat": {"id": 456, "type": "private"}, "text": "/approve task",
        }}
        self.assertIsNone(adapter.handle_update(update))
        self.assertEqual(o.mock_calls, [])

    def test_network_timeout_is_sanitized_and_http_timeout_is_set(self):
        client = TelegramClient("0:TEST")
        with patch.object(client._opener, "open", side_effect=TimeoutError("bot0:TEST")) as opened:
            with self.assertRaises(TelegramError) as ctx:
                client.call("getUpdates")
        self.assertEqual(opened.call_args.kwargs["timeout"], 40)
        self.assertNotIn("TEST", str(ctx.exception))

    def test_legacy_inline_handler_without_timeout_stays_claimed(self):
        # Legacy inline mode is explicitly selected by timeout_seconds=None.
        entered, release = threading.Event(), threading.Event()
        o, errors = Orchestrator(), []
        def handler(_):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test synchronization timeout")
            return "finished"
        o.register("blocking", handler)
        task = o.submit("blocking", "x", max_retries=2)
        def execute():
            try:
                o.execute(task.id)
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=execute)
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            self.assertEqual(task.status, TaskStatus.RUNNING)
            self.assertEqual(task.attempts, 1)
            with self.assertRaises(ValueError):
                o.execute(task.id)
        finally:
            release.set()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(task.status, TaskStatus.COMPLETED)
