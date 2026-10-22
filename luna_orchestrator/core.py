"""Task routing, bounded retries and optional durable state."""

from dataclasses import asdict, dataclass, replace
from enum import Enum
import math
import time
from threading import RLock
from typing import Callable, Dict, List
from uuid import uuid4

from .storage import SQLiteStore
from .execution import IsolationError, run_isolated


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"


@dataclass
class Task:
    id: str
    worker: str
    payload: str
    sensitive: bool
    status: TaskStatus
    result: str = ""
    max_retries: int = 0
    attempts: int = 0
    timeout_seconds: float | None = None


class Orchestrator:
    def __init__(self, db_path=None) -> None:
        self.workers: Dict[str, Callable[[str], str]] = {}
        self.tasks: Dict[str, Task] = {}
        self.audit: List[str] = []
        self._state_lock = RLock()
        self._store = SQLiteStore(db_path) if db_path is not None else None
        self.refresh()

    def refresh(self):
        """Reload durable state; preserve existing Task references."""
        with self._state_lock:
            if self._store:
                records, audit = self._store.load()
                for record in records:
                    record["status"] = TaskStatus(record["status"])
                    task = Task(**record)
                    if task.id in self.tasks:
                        self.tasks[task.id].__dict__.update(task.__dict__)
                    else:
                        self.tasks[task.id] = task
                self.audit[:] = audit

    def _save(self, task, event, previous=None):
        full_event = f"{task.id}:{event}"
        if previous is not None and asdict(self.tasks[task.id]) != previous:
            raise ValueError("Task changed while execution was in progress")
        if self._store:
            self._store.save(asdict(task), previous, full_event)
        if task.id in self.tasks:
            self.tasks[task.id].__dict__.update(task.__dict__)
        else:
            self.tasks[task.id] = task
        self.audit.append(full_event)
        return self.tasks[task.id]

    def register(self, name: str, handler: Callable[[str], str]) -> None:
        if not name or not callable(handler):
            raise ValueError("Worker name and callable handler are required")
        with self._state_lock:
            self.workers[name] = handler

    def submit(self, worker: str, payload: str, *, sensitive: bool = False,
               max_retries: int = 0, timeout_seconds: float | None = None) -> Task:
        if type(max_retries) is not int or not 0 <= max_retries <= 10:
            raise ValueError("max_retries must be an integer between 0 and 10")
        if timeout_seconds is not None:
            if (type(timeout_seconds) not in (int, float)
                    or not 0 < timeout_seconds <= 86400 or not math.isfinite(timeout_seconds)):
                raise ValueError("timeout_seconds must be in (0, 86400], or None")
            timeout_seconds = float(timeout_seconds)
        with self._state_lock:
            if worker not in self.workers:
                raise ValueError("Unknown worker")
            task = Task(
                id=str(uuid4()), worker=worker, payload=payload, sensitive=sensitive,
                status=TaskStatus.AWAITING_APPROVAL if sensitive else TaskStatus.PENDING,
                max_retries=max_retries, timeout_seconds=timeout_seconds,
            )
            return self._save(task, "submitted")

    def _decide(self, task_id, status, event):
        with self._state_lock:
            self.refresh()
            task = self.tasks[task_id]
            if task.status != TaskStatus.AWAITING_APPROVAL:
                raise ValueError("Task is not awaiting approval")
            self._save(replace(task, status=status), event, asdict(task))

    def approve(self, task_id: str) -> None:
        self._decide(task_id, TaskStatus.PENDING, "approved")

    def reject(self, task_id: str) -> None:
        self._decide(task_id, TaskStatus.REJECTED, "rejected")

    def execute(self, task_id: str) -> Task:
        with self._state_lock:
            self.refresh()
            task = self.tasks[task_id]
            if task.status != TaskStatus.PENDING:
                raise ValueError("Task is not executable")
            # Workers are code, not persisted data. Re-register after restart.
            if task.worker not in self.workers:
                raise ValueError("Worker must be registered before execution")
            handler = self.workers[task.worker]
            task = self._save(replace(task, status=TaskStatus.RUNNING, attempts=task.attempts + 1),
                              "started", asdict(task))
            payload = task.payload
            # Own an immutable snapshot: refresh() may mutate the public Task.
            owned = replace(task)
            deadline = (time.monotonic() + task.timeout_seconds
                        if task.timeout_seconds is not None else None)
        while True:
            try:
                if deadline is not None:
                    try:
                        state, result = run_isolated(handler, payload, deadline)
                    except Exception:
                        # Supervision/cleanup errors cannot prove worker death.
                        # Never treat them as retryable handler exceptions.
                        raise IsolationError(
                            "Isolated supervision failed; task remains running"
                        ) from None
                    status = TaskStatus(state)
                else:
                    result = str(handler(payload))
                    status = TaskStatus.COMPLETED
            except IsolationError:
                raise
            except Exception as exc:
                try:
                    result = f"{type(exc).__name__}: {exc}"
                except Exception:
                    result = type(exc).__name__
                status = TaskStatus.FAILED
            with self._state_lock:
                previous = asdict(owned)
                if deadline is not None and time.monotonic() >= deadline:
                    status, result = TaskStatus.TIMED_OUT, "Task execution deadline exceeded"
                if status == TaskStatus.FAILED and owned.attempts <= owned.max_retries:
                    task = self._save(replace(owned, attempts=owned.attempts + 1, result=result),
                                      "retrying", previous)
                    owned = replace(task)
                    continue
                return self._save(replace(owned, status=status, result=result),
                                  status.value, previous)

    def recover_interrupted(self):
        """Maintenance only: stop all executors first. Never replay side effects."""
        with self._state_lock:
            self.refresh()
            recovered = []
            for task in list(self.tasks.values()):
                if task.status == TaskStatus.RUNNING:
                    self._save(replace(task, status=TaskStatus.FAILED,
                                       result="Interrupted execution; outcome unknown"),
                               "interrupted", asdict(task))
                    recovered.append(task.id)
            return recovered
