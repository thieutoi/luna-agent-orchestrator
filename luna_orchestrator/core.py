"""Simple deterministic task routing with explicit human approval."""

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List
from uuid import uuid4


class TaskStatus(str, Enum):
    PENDING = "pending"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Task:
    id: str
    worker: str
    payload: str
    sensitive: bool
    status: TaskStatus
    result: str = ""


class Orchestrator:
    def __init__(self) -> None:
        self.workers: Dict[str, Callable[[str], str]] = {}
        self.tasks: Dict[str, Task] = {}
        self.audit: List[str] = []

    def register(self, name: str, handler: Callable[[str], str]) -> None:
        if not name or not callable(handler):
            raise ValueError("Worker name and callable handler are required")
        self.workers[name] = handler

    def submit(self, worker: str, payload: str, *, sensitive: bool = False) -> Task:
        if worker not in self.workers:
            raise ValueError("Unknown worker")
        task = Task(
            id=str(uuid4()),
            worker=worker,
            payload=payload,
            sensitive=sensitive,
            status=TaskStatus.AWAITING_APPROVAL if sensitive else TaskStatus.PENDING,
        )
        self.tasks[task.id] = task
        self.audit.append(f"{task.id}:submitted")
        return task

    def approve(self, task_id: str) -> None:
        task = self.tasks[task_id]
        if task.status != TaskStatus.AWAITING_APPROVAL:
            raise ValueError("Task is not awaiting approval")
        task.status = TaskStatus.PENDING
        self.audit.append(f"{task.id}:approved")

    def execute(self, task_id: str) -> Task:
        task = self.tasks[task_id]
        if task.status != TaskStatus.PENDING:
            raise ValueError("Task is not executable")
        try:
            task.result = str(self.workers[task.worker](task.payload))
            task.status = TaskStatus.COMPLETED
        except Exception as exc:
            task.result = f"{type(exc).__name__}: {exc}"
            task.status = TaskStatus.FAILED
        self.audit.append(f"{task.id}:{task.status.value}")
        return task
