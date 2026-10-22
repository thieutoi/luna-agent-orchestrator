import json
from contextlib import closing
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import URLError

from luna_orchestrator import Orchestrator, TaskStatus
from luna_orchestrator.telegram import TelegramAdapter, TelegramClient, TelegramError


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.o = self.open()
        self.o.register("echo", str.upper)

    def open(self):
        return Orchestrator(self.path)

    def test_restart_restores_tasks_audit_and_approval(self):
        t = self.o.submit("echo", "hello", sensitive=True, max_retries=2)
        other = self.open()
        self.assertEqual(asdict(other.tasks[t.id]), asdict(t))
        self.assertEqual(other.audit, self.o.audit)
        with self.assertRaises(ValueError):
            other.execute(t.id)
        other.approve(t.id)
        with self.assertRaises(ValueError):
            other.execute(t.id)  # Handlers are intentionally not deserialized.
        other.register("echo", str.upper)
        other.execute(t.id)
        restored = self.open()
        self.assertEqual(restored.tasks[t.id].result, "HELLO")
        self.assertEqual(restored.tasks[t.id].attempts, 1)
        self.assertEqual(restored.audit, other.audit)
        with self.assertRaises(ValueError):
            restored.execute(t.id)

    def test_rejection_persists_and_is_terminal(self):
        t = self.o.submit("echo", "x", sensitive=True)
        self.o.reject(t.id)
        other = self.open()
        self.assertEqual(other.tasks[t.id].status, TaskStatus.REJECTED)
        for operation in (other.approve, other.reject, other.execute):
            with self.subTest(op=operation.__name__), self.assertRaises(ValueError):
                operation(t.id)

    def test_cannot_reject_normal_task(self):
        t = self.o.submit("echo", "x")
        with self.assertRaises(ValueError):
            self.o.reject(t.id)

    def test_failed_persists(self):
        def fail(_):
            raise RuntimeError("boom")
        self.o.register("fail", fail)
        t = self.o.submit("fail", "x")
        self.o.execute(t.id)
        self.assertEqual(self.open().tasks[t.id].status, TaskStatus.FAILED)

    def test_atomic_claim_across_instances(self):
        entered, release = threading.Event(), threading.Event()
        calls, errors = [], []
        def handler(value):
            calls.append(value)
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test timed out")
            return value
        self.o.register("block", handler)
        t = self.o.submit("block", "x")
        other = self.open()
        other.register("block", handler)
        def run():
            try:
                self.o.execute(t.id)
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            with self.assertRaises(ValueError):
                other.execute(t.id)
        finally:
            release.set()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(calls, ["x"])
        self.assertEqual(self.open().tasks[t.id].status, TaskStatus.COMPLETED)

    def test_compare_and_swap_rejects_stale_write(self):
        t = self.o.submit("echo", "x", sensitive=True)
        old = asdict(t)
        self.o.approve(t.id)
        with self.assertRaises(ValueError):
            self.o._store.save(asdict(replace(t, status=TaskStatus.REJECTED)), old, "bad")
        self.assertNotIn("bad", self.open().audit)

    def test_write_failure_leaves_memory_unchanged(self):
        t = self.o.submit("echo", "x", sensitive=True)
        audit = list(self.o.audit)
        with patch.object(self.o._store, "save", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.o.approve(t.id)
        self.assertEqual(t.status, TaskStatus.AWAITING_APPROVAL)
        self.assertEqual(self.o.audit, audit)

    def test_interrupted_tasks_require_explicit_recovery(self):
        t = self.o.submit("echo", "x")
        self.o._save(replace(t, status=TaskStatus.RUNNING, attempts=1), "started", asdict(t))
        other = self.open()
        self.assertEqual(other.tasks[t.id].status, TaskStatus.RUNNING)
        self.assertEqual(other.recover_interrupted(), [t.id])
        self.assertEqual(other.recover_interrupted(), [])
        self.assertEqual(self.open().tasks[t.id].status, TaskStatus.FAILED)

    def test_real_process_crash_leaves_durable_running_state(self):
        t = self.o.submit("echo", "x")
        code = (
            "import os,sys; from luna_orchestrator import Orchestrator; "
            "o=Orchestrator(sys.argv[1]); "
            "o.register('echo', lambda _: os._exit(0)); o.execute(sys.argv[2])"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, str(self.path), t.id],
            capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        restored = self.open()
        self.assertEqual(restored.tasks[t.id].status, TaskStatus.RUNNING)
        self.assertEqual(restored.tasks[t.id].attempts, 1)
        self.assertEqual(restored.recover_interrupted(), [t.id])
        with self.assertRaises(ValueError):
            restored.execute(t.id)

    def test_audit_failure_rolls_back_task_transaction(self):
        t = self.o.submit("echo", "x", sensitive=True)
        import sqlite3
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "CREATE TRIGGER reject_event BEFORE INSERT ON audit "
                "BEGIN SELECT RAISE(ABORT, 'test failure'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.o.approve(t.id)
        restored = self.open()
        self.assertEqual(restored.tasks[t.id].status, TaskStatus.AWAITING_APPROVAL)
        self.assertEqual(restored.audit, [f"{t.id}:submitted"])

    def test_payload_is_data_not_sql(self):
        payload = "'; DROP TABLE tasks; --"
        t = self.o.submit("echo", payload)
        self.assertEqual(self.open().tasks[t.id].payload, payload)

    def test_retry_success_and_durable_attempts(self):
        calls = []
        def flaky(value):
            calls.append(value)
            if len(calls) < 3:
                raise RuntimeError("transient")
            return "done"
        self.o.register("flaky", flaky)
        t = self.o.submit("flaky", "x", sensitive=True, max_retries=2)
        with self.assertRaises(ValueError):
            self.o.execute(t.id)
        self.assertEqual(calls, [])
        self.o.approve(t.id)
        self.o.execute(t.id)
        self.assertEqual(t.status, TaskStatus.COMPLETED)
        self.assertEqual(t.attempts, 3)
        self.assertEqual(self.open().tasks[t.id].attempts, 3)
        self.assertEqual(self.o.audit.count(f"{t.id}:retrying"), 2)

    def test_retry_exhaustion_is_terminal(self):
        self.o.register("bad", lambda _: 1 / 0)
        t = self.o.submit("bad", "x", max_retries=1)
        self.o.execute(t.id)
        self.assertEqual(t.status, TaskStatus.FAILED)
        self.assertEqual(t.attempts, 2)
        with self.assertRaises(ValueError):
            self.o.execute(t.id)

    def test_retry_bounds(self):
        for count in (-1, 11, True, 1.5, "2"):
            with self.subTest(count=count), self.assertRaises(ValueError):
                self.o.submit("echo", "x", max_retries=count)

    def test_nested_different_task_does_not_deadlock(self):
        inner = self.o.submit("echo", "inner")
        self.o.register("nested", lambda _: self.o.execute(inner.id).result)
        outer = self.o.submit("nested", "outer")
        self.assertEqual(self.o.execute(outer.id).result, "INNER")

    def test_exception_with_broken_string_is_recorded(self):
        class BrokenError(Exception):
            def __str__(self):
                raise ValueError("bad str")
        def fail(_):
            raise BrokenError()
        self.o.register("fail", fail)
        t = self.o.submit("fail", "x")
        self.o.execute(t.id)
        self.assertEqual(t.status, TaskStatus.FAILED)
        self.assertEqual(t.result, "BrokenError")


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.o = Orchestrator()
        self.o.register("echo", str.upper)
        self.client = Mock()
        self.adapter = TelegramAdapter(self.o, [123], self.client)

    def update(self, text, user=123, chat_type="private"):
        return {"update_id": 5, "message": {
            "from": {"id": user, "is_bot": False},
            "chat": {"id": user, "type": chat_type}, "text": text}}

    def test_empty_or_invalid_allowlist_denied(self):
        for ids in ([], ["123"], [True], [-1]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                TelegramAdapter(self.o, ids, self.client)

    def test_unauthorized_commands_have_no_side_effects(self):
        t = self.o.submit("echo", "PRIVATE", sensitive=True)
        before = list(self.o.audit)
        for cmd in ("/start", "/status", "/tasks", f"/approve {t.id}", f"/reject {t.id}"):
            for user in (999, "123", True):
                with self.subTest(cmd=cmd, user=user):
                    self.assertIsNone(self.adapter.handle_update(self.update(cmd, user)))
        self.assertEqual(t.status, TaskStatus.AWAITING_APPROVAL)
        self.assertEqual(self.o.audit, before)

    def test_groups_and_malformed_updates_ignored(self):
        for update in (None, {}, {"message": []}, self.update("/tasks", chat_type="group"),
                       {"message": {"from": None, "chat": None}}):
            with self.subTest(update=update):
                self.assertIsNone(self.adapter.handle_update(update))
        update = self.update("/tasks")
        update["message"]["from"]["is_bot"] = True
        self.assertIsNone(self.adapter.handle_update(update))

    def test_start_status_tasks_without_private_data(self):
        t = self.o.submit("echo", "PRIVATE")
        self.assertIn("/approve", self.adapter.handle_update(self.update("/start")))
        self.assertIn("Tasks: 1", self.adapter.handle_update(self.update("/status")))
        result = self.adapter.handle_update(self.update("/tasks"))
        self.assertIn(t.id, result)
        self.assertNotIn("PRIVATE", result)

    def test_approve_does_not_execute(self):
        t = self.o.submit("echo", "private", sensitive=True)
        self.assertEqual(self.adapter.handle_update(self.update(f"/approve {t.id}")), "Approved.")
        self.assertEqual(t.status, TaskStatus.PENDING)
        self.assertEqual(t.attempts, 0)
        self.assertIn("not awaiting", self.adapter.handle_update(self.update(f"/approve {t.id}")))

    def test_reject(self):
        t = self.o.submit("echo", "private", sensitive=True)
        self.assertEqual(self.adapter.handle_update(self.update(f"/reject {t.id}")), "Rejected.")
        with self.assertRaises(ValueError):
            self.o.execute(t.id)

    def test_invalid_command_arguments(self):
        for text in ("/approve", "/reject a b", "/status extra", "/unknown"):
            self.assertIsInstance(self.adapter.handle_update(self.update(text)), str)

    def test_polling_sends_only_authorized_replies_and_advances_offset(self):
        unauthorized = self.update("/tasks", 999)
        unauthorized["update_id"] = 4
        self.client.call.return_value = [unauthorized, self.update("/start")]
        self.adapter.poll_once()
        self.assertEqual(self.adapter.offset, 6)
        calls = self.client.call.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1].kwargs["chat_id"], 123)
        self.adapter.poll_once()  # Duplicate updates ignored.
        self.assertEqual(self.client.call.call_count, 3)

    def test_transport_errors_do_not_leak_token_url(self):
        client = TelegramClient("0:TEST")
        with patch.object(client._opener, "open", side_effect=URLError("bot0:TEST")):
            with self.assertRaises(TelegramError) as ctx:
                client.call("getUpdates")
        self.assertNotIn("TEST", str(ctx.exception))

    def test_http_request_and_api_error(self):
        client = TelegramClient("0:TEST")
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps({"ok": True, "result": []}).encode()
        with patch.object(client._opener, "open", return_value=response) as opened:
            self.assertEqual(client.call("getUpdates", offset=7), [])
            req = opened.call_args.args[0]
            self.assertEqual(req.get_method(), "POST")
            self.assertEqual(json.loads(req.data)["offset"], 7)
        response.read.return_value = b'{"ok": false, "description": "secret"}'
        with patch.object(client._opener, "open", return_value=response):
            with self.assertRaises(TelegramError) as ctx:
                client.call("getUpdates")
        self.assertNotIn("secret", str(ctx.exception))
