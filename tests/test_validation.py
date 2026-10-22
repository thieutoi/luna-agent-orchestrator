import threading
import unittest
from luna_orchestrator import Orchestrator, TaskStatus


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.o = Orchestrator()
        self.calls = []
        self.o.register("echo", lambda value: self.calls.append(value) or value.upper())

    def test_registration_and_payload(self):
        t = self.o.submit("echo", "hello")
        self.assertEqual(t.status, TaskStatus.PENDING)
        self.assertEqual(self.o.execute(t.id).result, "HELLO")
        self.assertEqual(self.calls, ["hello"])

    def test_invalid_registration(self):
        for name, handler in [("", str), ("bad", None)]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.o.register(name, handler)

    def test_sensitive_gate_has_no_side_effects(self):
        t = self.o.submit("echo", "private", sensitive=True)
        with self.assertRaises(ValueError):
            self.o.execute(t.id)
        self.assertEqual(self.calls, [])
        self.assertEqual(t.status, TaskStatus.AWAITING_APPROVAL)
        self.assertEqual(self.o.audit, [f"{t.id}:submitted"])
        self.o.approve(t.id)
        self.o.execute(t.id)
        self.assertEqual(self.calls, ["private"])
        self.assertEqual(self.o.audit, [f"{t.id}:submitted", f"{t.id}:approved", f"{t.id}:completed"])

    def test_completed_cannot_run_again(self):
        t = self.o.submit("echo", "x")
        self.o.execute(t.id)
        with self.assertRaises(ValueError):
            self.o.execute(t.id)
        self.assertEqual(self.calls, ["x"])

    def test_failed_cannot_run_again(self):
        def fail(value):
            self.calls.append(value)
            raise RuntimeError("boom")
        self.o.register("fail", fail)
        t = self.o.submit("fail", "x")
        self.o.execute(t.id)
        self.assertEqual(t.status, TaskStatus.FAILED)
        self.assertEqual(t.result, "RuntimeError: boom")
        with self.assertRaises(ValueError):
            self.o.execute(t.id)
        self.assertEqual(self.calls, ["x"])
        self.assertEqual(self.o.audit[-1], f"{t.id}:failed")

    def test_missing_worker_no_task_created(self):
        with self.assertRaises(ValueError):
            self.o.submit("missing", "x")
        self.assertEqual(self.o.tasks, {})
        self.assertEqual(self.o.audit, [])

    def test_missing_task(self):
        for operation in (self.o.approve, self.o.execute):
            with self.subTest(operation=operation.__name__), self.assertRaises(KeyError):
                operation("missing")

    def test_invalid_and_repeated_approval(self):
        t = self.o.submit("echo", "x")
        with self.assertRaises(ValueError):
            self.o.approve(t.id)
        t = self.o.submit("echo", "x", sensitive=True)
        self.o.approve(t.id)
        with self.assertRaises(ValueError):
            self.o.approve(t.id)
        self.o.execute(t.id)
        with self.assertRaises(ValueError):
            self.o.approve(t.id)

    def test_result_conversion_failure_recorded(self):
        class BadString:
            def __str__(self):
                raise TypeError("cannot stringify")
        self.o.register("bad", lambda _: BadString())
        t = self.o.submit("bad", "x")
        self.o.execute(t.id)
        self.assertEqual(t.status, TaskStatus.FAILED)
        self.assertEqual(t.result, "TypeError: cannot stringify")

    def test_reentrant_execution_blocked(self):
        def handler(value):
            self.calls.append(value)
            if len(self.calls) == 1:
                with self.assertRaises(ValueError):
                    self.o.execute(t.id)
            return value
        self.o.register("nested", handler)
        t = self.o.submit("nested", "x")
        self.o.execute(t.id)
        self.assertEqual(self.calls, ["x"])
        self.assertEqual(t.status, TaskStatus.COMPLETED)

    def test_concurrent_execution_blocked(self):
        entered, release = threading.Event(), threading.Event()
        errors = []
        def handler(value):
            self.calls.append(value)
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test synchronization timeout")
            return value
        self.o.register("blocking", handler)
        t = self.o.submit("blocking", "x")
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
                self.o.execute(t.id)
        finally:
            release.set()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.calls, ["x"])
        self.assertEqual(t.status, TaskStatus.COMPLETED)
