import io
import json
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from luna_orchestrator import Orchestrator, TaskStatus
from luna_orchestrator.cli import main
from luna_orchestrator.config import configure, load_config
from luna_orchestrator.locking import polling_lock
from luna_orchestrator.telegram import TelegramAdapter, TelegramClient, TelegramError


def update(text, number=1, user=123):
    return {"update_id": number, "message": {
        "from": {"id": user, "is_bot": False},
        "chat": {"id": user, "type": "private"}, "text": text}}


class V030Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "state.sqlite3"
        self.o = Orchestrator(self.db)
        self.o.register("echo", str.upper)
        self.client = Mock()

    def adapter(self, **kw):
        return TelegramAdapter(self.o, [123], self.client, **kw)

    def test_poll_checkpoint_survives_restart_and_prevents_reply_replay(self):
        self.client.call.return_value = [update("/status", 7)]
        self.adapter().poll_once()
        restored = TelegramAdapter(Orchestrator(self.db), [123], self.client)
        self.assertEqual(restored.offset, 8)
        self.client.reset_mock()
        self.client.call.return_value = [update("/status", 7)]
        restored.poll_once()
        self.client.call.assert_called_once()
        self.assertEqual(self.client.call.call_args.kwargs["offset"], 8)

    def test_approve_and_reject_integration_persist_actor_audit(self):
        a = self.o.submit("echo", "PRIVATE", sensitive=True)
        b = self.o.submit("echo", "PRIVATE", sensitive=True)
        self.client.call.return_value = [update(f"/approve {a.id}", 1), update(f"/reject {b.id}", 2)]
        self.adapter().poll_once()
        restored = Orchestrator(self.db)
        self.assertEqual(restored.tasks[a.id].status, TaskStatus.PENDING)
        self.assertEqual(restored.tasks[b.id].status, TaskStatus.REJECTED)
        self.assertEqual(restored.tasks[a.id].attempts, 0)
        self.assertIn(f"{a.id}:approved:telegram:123", restored.audit)
        self.assertIn(f"{b.id}:rejected:telegram:123", restored.audit)

    def test_failed_reply_does_not_repeat_decision_on_restart(self):
        task = self.o.submit("echo", "x", sensitive=True)
        self.client.call.side_effect = [[update(f"/approve {task.id}", 3)], TelegramError("offline")]
        with self.assertRaises(TelegramError):
            self.adapter().poll_once()
        restored = self.adapter()
        self.assertEqual(restored.offset, 4)
        self.assertEqual(task.status, TaskStatus.PENDING)
        self.client.call.side_effect = None
        self.client.call.return_value = [update(f"/approve {task.id}", 3)]
        self.client.reset_mock()
        restored.poll_once()
        self.client.call.assert_called_once()

    def test_checkpoint_write_failure_prevents_reply(self):
        self.client.call.return_value = [update("/status", 2)]
        adapter = self.adapter()
        with patch.object(self.o._store, "checkpoint", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                adapter.poll_once()
        self.client.call.assert_called_once()

    def test_monotonic_checkpoint_and_validation(self):
        self.assertEqual(self.o.checkpoint("x", 8), 8)
        self.assertEqual(self.o.checkpoint("x", 3), 8)
        self.assertEqual(Orchestrator(self.db).checkpoint("x"), 8)
        for invalid in (-1, True, "2"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.o.checkpoint("x", invalid)

    def test_bot_cursor_namespaces_are_independent(self):
        self.o.checkpoint("bot-a", 9)
        self.assertIsNone(self.o.checkpoint("bot-b"))

    def test_rate_limit_silently_drops_and_window_recovers(self):
        now = [0]
        adapter = self.adapter(rate_limit=2, clock=lambda: now[0])
        self.assertIsNotNone(adapter.handle_update(update("/status")))
        self.assertIsNotNone(adapter.handle_update(update("/status")))
        self.assertIsNone(adapter.handle_update(update("/status")))
        now[0] = 60
        self.assertIsNotNone(adapter.handle_update(update("/status")))
        self.assertEqual(len(adapter._requests[123]), 1)

    def test_unauthorized_spam_allocates_no_user_buckets_or_replies(self):
        adapter = self.adapter()
        for user in range(1000, 1100):
            self.assertIsNone(adapter.handle_update(update("/logs", user=user)))
        self.assertEqual(adapter._requests, {})

    def test_old_decision_has_no_side_effect(self):
        task = self.o.submit("echo", "x", sensitive=True)
        message = update(f"/approve {task.id}")
        message["message"]["date"] = int(time.time()) - 400
        self.assertIn("expired", self.adapter().handle_update(message))
        self.assertEqual(task.status, TaskStatus.AWAITING_APPROVAL)

    def test_system_and_logs_omit_payload_result_and_paths(self):
        task = self.o.submit("echo", "PRIVATE")
        self.o.execute(task.id)
        adapter = self.adapter()
        for command in ("/system", "/logs"):
            reply = adapter.handle_update(update(command))
            self.assertNotIn("PRIVATE", reply)
            self.assertNotIn(str(self.root), reply)
        self.assertIn("completed", adapter.handle_update(update("/logs")))

    def test_network_reconnect_backoff_then_success(self):
        adapter = self.adapter()
        stop = Event()
        waits = []
        def wait(delay):
            waits.append(delay)
            if delay == 0.2:
                stop.set()
        with patch.object(adapter, "poll_once", side_effect=[TelegramError("offline"), TelegramError("offline"), None]):
            adapter.run(stop, wait=wait)
        self.assertEqual(waits, [1, 2, 0.2])

    def test_reconnect_exhaustion_is_bounded(self):
        adapter = self.adapter()
        waits = []
        with patch.object(adapter, "poll_once", side_effect=TelegramError("offline")) as poll:
            with self.assertRaises(TelegramError):
                adapter.run(max_failures=3, wait=waits.append)
        self.assertEqual(waits, [1, 2])
        self.assertEqual(poll.call_count, 3)

    def test_permanent_errors_do_not_reconnect(self):
        adapter = self.adapter()
        waits = []
        with patch.object(adapter, "poll_once", side_effect=TelegramError("bad auth", retryable=False)):
            with self.assertRaises(TelegramError):
                adapter.run(wait=waits.append)
        self.assertEqual(waits, [])

    def test_retry_after_is_honored_without_real_sleep(self):
        adapter = self.adapter()
        stop = Event()
        waits = []
        def wait(delay):
            waits.append(delay)
            stop.set()
        with patch.object(adapter, "poll_once", side_effect=TelegramError("limited", retry_after=17)):
            adapter.run(stop, wait=wait)
        self.assertEqual(waits, [17])

    def test_http429_parsed_and401_fatal_without_secret_in_error(self):
        client = TelegramClient("0:TEST")
        for code in (429, 401, 409):
            body = io.BytesIO(b'{"parameters":{"retry_after":9}}')
            error = HTTPError("bot0:TEST", code, "private", {}, body)
            with patch.object(client._opener, "open", side_effect=error):
                with self.assertRaises(TelegramError) as ctx:
                    client.call("getUpdates")
            self.assertNotIn("TEST", str(ctx.exception))
            self.assertEqual(ctx.exception.retryable, code == 429)
            if code == 429:
                self.assertEqual(ctx.exception.retry_after, 9)

    def test_local_poll_lock_refuses_duplicate_and_releases(self):
        with polling_lock(self.db):
            with self.assertRaises(RuntimeError):
                with polling_lock(self.db):
                    self.fail("duplicate acquired")
        with polling_lock(self.db):
            pass

    def test_wizard_preserves_existing_and_never_requests_token(self):
        config = self.root / "luna-config.json"
        prompts = []
        values = iter(["123", "tasks.sqlite3", ""])
        def ask(prompt):
            prompts.append(prompt)
            return next(values)
        configure(config, ask=ask)
        self.assertNotIn("token", " ".join(prompts).lower())
        loaded = load_config(config, environ={})
        self.assertEqual(loaded["allowed_user_ids"], [123])
        self.assertEqual(loaded["db_path"], str(self.root / "tasks.sqlite3"))
        with self.assertRaises(ValueError):
            configure(config, ask=ask)

    def test_config_rejects_secrets_bad_ids_and_unknown_fields(self):
        file = self.root / "config.json"
        for content in ([], {"allowed_user_ids": [True]}, {"allowed_user_ids": [123], "token": "do-not-store"}):
            file.write_text(json.dumps(content))
            with self.subTest(content=content), self.assertRaises(ValueError):
                load_config(file, environ={})

    def test_environment_only_config_remains_compatible(self):
        config = load_config(self.root / "missing.json", environ={"LUNA_TELEGRAM_USER_IDS": "123,456"})
        self.assertEqual(config["allowed_user_ids"], [123,456])

    def test_doctor_and_recovery_cli_no_network_or_secret(self):
        config = self.root / "luna-config.json"
        configure(config, ask=Mock(side_effect=["123", str(self.db), "20"]))
        output, errors = io.StringIO(), io.StringIO()
        with patch.dict("os.environ", {"LUNA_TELEGRAM_TOKEN": "0:TEST"}, clear=True):
            with redirect_stdout(output), redirect_stderr(errors):
                self.assertEqual(main(["doctor", "--config", str(config)]), 0)
                self.assertEqual(main(["recover", "--config", str(config)]), 2)
                self.assertEqual(main(["recover", "--config", str(config), "--confirm-stopped"]), 0)
        self.assertNotIn("TEST", output.getvalue() + errors.getvalue())
        self.assertIn('"token_configured": true', output.getvalue())

    def test_restart_keeps_running_claim_until_manual_recovery(self):
        task = self.o.submit("echo", "x")
        self.o._save(replace(task, status=TaskStatus.RUNNING, attempts=1), "started", asdict(task))
        restored = Orchestrator(self.db)
        restored.register("echo", str.upper)
        with self.assertRaises(ValueError):
            restored.execute(task.id)
        self.assertEqual(restored.recover_interrupted(), [task.id])
        self.assertEqual(Orchestrator(self.db).tasks[task.id].status, TaskStatus.FAILED)

    def test_expired_cursor_allows_randomized_lower_telegram_ids(self):
        self.o.checkpoint("telegram", 999999)
        self.o.checkpoint("telegram:updated", int(time.time()) - 90000)
        self.client.call.return_value = [update("/status", 4)]
        adapter = self.adapter()
        adapter.poll_once()
        self.assertIsNone(self.client.call.call_args_list[0].kwargs["offset"])
        self.assertEqual(adapter.offset, 5)
        self.assertEqual(Orchestrator(self.db).checkpoint("telegram"), 5)

    def test_stop_before_start_does_not_poll(self):
        stop = Event()
        stop.set()
        adapter = self.adapter()
        with patch.object(adapter, "poll_once") as poll:
            adapter.run(stop)
            poll.assert_not_called()

    def test_recovery_cli_refuses_live_local_poller(self):
        config = self.root / "luna-config.json"
        configure(config, ask=Mock(side_effect=["123", str(self.db), "20"]))
        with patch.dict("os.environ", {}, clear=True):
            with polling_lock(self.db), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["recover", "--config", str(config), "--confirm-stopped"]), 2)

    def test_invalid_limits_fail_closed(self):
        for kwargs in ({"rate_limit": 0}, {"rate_limit": True}, {"window_seconds": float("nan")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.adapter(**kwargs)

    def test_actor_validation_does_not_change_task(self):
        task = self.o.submit("echo", "x", sensitive=True)
        for actor in (True, -1, "123"):
            with self.subTest(actor=actor), self.assertRaises(ValueError):
                self.o.approve(task.id, actor_id=actor)
        self.assertEqual(task.status, TaskStatus.AWAITING_APPROVAL)

    def test_new_database_permissions_and_existing_content_preserved(self):
        import os, stat
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.db.stat().st_mode), 0o600)
        task = self.o.submit("echo", "x")
        restored = Orchestrator(self.db)
        self.assertEqual(restored.tasks[task.id].payload, "x")
