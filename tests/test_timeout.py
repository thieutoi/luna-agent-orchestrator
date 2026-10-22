"""Real spawned-worker tests and deterministic state/race regressions."""
from contextlib import closing
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import sqlite3
import signal
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from luna_orchestrator import Orchestrator, TaskStatus
from luna_orchestrator.execution import IsolationError


def heartbeat(path):
    target = Path(path)
    target.write_text(str(os.getpid()), encoding="utf-8")
    while True:
        with target.open("a") as stream:
            stream.write(".")
        time.sleep(0.05)


def ignore_termination(path):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    return heartbeat(path)


def retry_then_succeed(path):
    target = Path(path)
    if not target.exists():
        target.write_text("first", encoding="utf-8")
        raise RuntimeError("transient")
    return "recovered"


def retry_then_block(path):
    target = Path(path)
    if not target.exists():
        target.write_text("first", encoding="utf-8")
        raise RuntimeError("transient")
    return heartbeat(path)


class TimeoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "tasks.sqlite3"
        self.o = Orchestrator(self.db)

    def test_timeout_kills_real_worker_and_persists_terminal_state(self):
        marker = self.root / "heartbeat"
        self.o.register("heartbeat", heartbeat)
        t = self.o.submit("heartbeat", str(marker), timeout_seconds=3, max_retries=3)
        started = time.monotonic()
        result = self.o.execute(t.id)
        self.assertEqual(result.status, TaskStatus.TIMED_OUT)
        self.assertLess(time.monotonic() - started, 10)
        self.assertTrue(marker.exists(), "worker must actually start, not only time out at spawn")
        before = marker.read_text()
        time.sleep(0.2)
        self.assertEqual(marker.read_text(), before, "handler must stop producing side effects")
        self.assertEqual(t.attempts, 1, "timeout must not retry")
        restored = Orchestrator(self.db)
        self.assertEqual(restored.tasks[t.id].status, TaskStatus.TIMED_OUT)
        self.assertEqual(restored.tasks[t.id].timeout_seconds, 3)
        self.assertEqual(restored.audit, [f"{t.id}:submitted", f"{t.id}:started", f"{t.id}:timed_out"])
        restored.register("heartbeat", heartbeat)
        for op in (restored.execute, restored.approve, restored.reject):
            with self.subTest(op=op.__name__), self.assertRaises(ValueError):
                op(t.id)
        self.assertEqual(restored.recover_interrupted(), [])

    def test_isolated_success(self):
        self.o.register("echo", str.upper)
        t = self.o.submit("echo", "hello", timeout_seconds=10)
        self.assertEqual(self.o.execute(t.id).result, "HELLO")
        self.assertEqual(t.status, TaskStatus.COMPLETED)

    def test_isolated_retry_finishes_before_shared_deadline(self):
        self.o.register("flaky", retry_then_succeed)
        t = self.o.submit("flaky", str(self.root / "retry"), max_retries=2, timeout_seconds=10)
        self.assertEqual(self.o.execute(t.id).result, "recovered")
        self.assertEqual(t.attempts, 2)
        self.assertEqual(Orchestrator(self.db).tasks[t.id].status, TaskStatus.COMPLETED)

    def test_retry_uses_one_task_budget_and_never_retries_timeout(self):
        self.o.register("flaky", retry_then_block)
        marker = self.root / "retry"
        t = self.o.submit("flaky", str(marker), max_retries=5, timeout_seconds=3)
        start = time.monotonic()
        self.o.execute(t.id)
        self.assertEqual(t.status, TaskStatus.TIMED_OUT)
        self.assertEqual(t.attempts, 2)
        self.assertLess(time.monotonic() - start, 10)
        before = marker.read_text()
        time.sleep(0.2)
        self.assertEqual(marker.read_text(), before)

    def test_competing_instance_cannot_run_during_timeout_worker(self):
        marker = self.root / "race"
        self.o.register("block", heartbeat)
        t = self.o.submit("block", str(marker), timeout_seconds=3)
        errors = []
        def execute():
            try:
                self.o.execute(t.id)
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=execute)
        thread.start()
        try:
            until = time.monotonic() + 10
            while not marker.exists() and thread.is_alive() and time.monotonic() < until:
                time.sleep(0.02)
            self.assertTrue(marker.exists())
            other = Orchestrator(self.db)
            other.register("block", heartbeat)
            with self.assertRaises(ValueError):
                other.execute(t.id)
        finally:
            thread.join(15)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(t.status, TaskStatus.TIMED_OUT)
        self.assertEqual(t.attempts, 1)

    def test_unconfirmed_termination_keeps_claim_and_does_not_retry(self):
        self.o.register("echo", str.upper)
        t = self.o.submit("echo", "hello", max_retries=5, timeout_seconds=1)
        with patch("luna_orchestrator.core.run_isolated", side_effect=IsolationError("not stopped")):
            with self.assertRaises(IsolationError):
                self.o.execute(t.id)
        self.assertEqual(t.status, TaskStatus.RUNNING)
        self.assertEqual(t.attempts, 1)
        with self.assertRaises(ValueError):
            Orchestrator(self.db).execute(t.id)

    def test_late_result_cannot_overwrite_recovery_even_after_refresh(self):
        self.o.register("echo", str.upper)
        t = self.o.submit("echo", "x", timeout_seconds=1)
        def race(*_):
            Orchestrator(self.db).recover_interrupted()
            self.o.refresh()
            return "completed", "late"
        with patch("luna_orchestrator.core.run_isolated", side_effect=race):
            with self.assertRaises(ValueError):
                self.o.execute(t.id)
        restored = Orchestrator(self.db)
        self.assertEqual(restored.tasks[t.id].status, TaskStatus.FAILED)
        self.assertNotEqual(restored.tasks[t.id].result, "late")
        self.assertNotIn(f"{t.id}:completed", restored.audit)

    def test_deadline_wins_even_if_runner_returns_success_late(self):
        self.o.register("echo", str.upper)
        t = self.o.submit("echo", "x", timeout_seconds=1)
        with patch("luna_orchestrator.core.run_isolated", return_value=("completed", "late")):
            with patch("luna_orchestrator.core.time.monotonic", side_effect=[10, 12]):
                self.o.execute(t.id)
        self.assertEqual(t.status, TaskStatus.TIMED_OUT)
        self.assertNotEqual(t.result, "late")

    def test_timeout_audit_write_failure_rolls_back_and_blocks_replay(self):
        self.o.register("echo", str.upper)
        t = self.o.submit("echo", "x", timeout_seconds=1)
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("CREATE TRIGGER fail_timeout BEFORE INSERT ON audit "
                       "WHEN NEW.event LIKE '%:timed_out' "
                       "BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with patch("luna_orchestrator.core.run_isolated", return_value=("timed_out", "expired")):
            with self.assertRaises(sqlite3.IntegrityError):
                self.o.execute(t.id)
        restored = Orchestrator(self.db)
        self.assertEqual(restored.tasks[t.id].status, TaskStatus.RUNNING)
        self.assertEqual(restored.audit[-1], f"{t.id}:started")
        with self.assertRaises(ValueError):
            restored.execute(t.id)

    def test_v1_database_migrates_without_breaking_conditional_writes(self):
        self.o.register("echo", str.upper)
        t = self.o.submit("echo", "x", sensitive=True)
        data = asdict(t)
        del data["timeout_seconds"]
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE tasks SET data=? WHERE id=?", (json.dumps(data), t.id))
            db.execute("PRAGMA user_version=1")
        restored = Orchestrator(self.db)
        self.assertIsNone(restored.tasks[t.id].timeout_seconds)
        restored.approve(t.id)
        restored.register("echo", str.upper)
        self.assertEqual(restored.execute(t.id).status, TaskStatus.COMPLETED)

    def test_timeout_validation(self):
        self.o.register("echo", str.upper)
        for seconds in (0, -1, True, "1", float("inf"), float("nan"), 86401, 10**1000):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                self.o.submit("echo", "x", timeout_seconds=seconds)

    def test_restart_does_not_replay_running_timed_task(self):
        self.o.register("echo", str.upper)
        t = self.o.submit("echo", "x", timeout_seconds=1)
        self.o._save(replace(t, status=TaskStatus.RUNNING, attempts=1), "started", asdict(t))
        restored = Orchestrator(self.db)
        restored.register("echo", str.upper)
        with self.assertRaises(ValueError):
            restored.execute(t.id)
        self.assertEqual(restored.recover_interrupted(), [t.id])
        self.assertEqual(Orchestrator(self.db).tasks[t.id].status, TaskStatus.FAILED)

    def test_worker_ignoring_sigterm_is_still_killed(self):
        marker = self.root / "ignore-term"
        self.o.register("stubborn", ignore_termination)
        t = self.o.submit("stubborn", str(marker), timeout_seconds=3)
        self.o.execute(t.id)
        self.assertEqual(t.status, TaskStatus.TIMED_OUT)
        self.assertTrue(marker.exists())
        before = marker.read_text()
        time.sleep(0.2)
        self.assertEqual(marker.read_text(), before)

    def test_stop_escalation_and_unconfirmed_death(self):
        from unittest.mock import Mock
        from luna_orchestrator.execution import _stop
        process = Mock()
        process.is_alive.return_value = True
        with self.assertRaises(IsolationError):
            _stop(process)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(process.join.call_count, 2)

    def test_supervisor_os_error_never_retries_a_potentially_live_worker(self):
        self.o.register("echo", str.upper)
        t = self.o.submit("echo", "x", timeout_seconds=1, max_retries=5)
        with patch("luna_orchestrator.core.run_isolated", side_effect=OSError("kill failed")) as runner:
            with self.assertRaises(IsolationError):
                self.o.execute(t.id)
        runner.assert_called_once()
        self.assertEqual(t.status, TaskStatus.RUNNING)
        self.assertEqual(t.attempts, 1)
        self.assertEqual(Orchestrator(self.db).tasks[t.id].status, TaskStatus.RUNNING)
