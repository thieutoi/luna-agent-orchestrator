import unittest

from luna_orchestrator import Orchestrator, TaskStatus


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.orchestrator = Orchestrator()
        self.orchestrator.register("echo", lambda value: value.upper())

    def test_normal_task(self):
        task = self.orchestrator.submit("echo", "hello")
        result = self.orchestrator.execute(task.id)
        self.assertEqual(result.status, TaskStatus.COMPLETED)
        self.assertEqual(result.result, "HELLO")

    def test_sensitive_task_needs_approval(self):
        task = self.orchestrator.submit("echo", "secret", sensitive=True)
        with self.assertRaises(ValueError):
            self.orchestrator.execute(task.id)
        self.orchestrator.approve(task.id)
        self.assertEqual(self.orchestrator.execute(task.id).status, TaskStatus.COMPLETED)

    def test_unknown_worker(self):
        with self.assertRaises(ValueError):
            self.orchestrator.submit("missing", "hello")

    def test_failure_recorded(self):
        def fail(_):
            raise RuntimeError("boom")
        self.orchestrator.register("fail", fail)
        task = self.orchestrator.submit("fail", "x")
        self.assertEqual(self.orchestrator.execute(task.id).status, TaskStatus.FAILED)


if __name__ == "__main__":
    unittest.main()
