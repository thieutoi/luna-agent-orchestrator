"""Spawn isolation for trusted handlers with a task-wide monotonic deadline."""
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import time


class IsolationError(RuntimeError):
    """Worker death could not be confirmed; keep the task claimed."""


def _entry(handler, payload, output):
    try:
        result = str(handler(payload))
        status = "completed"
    except BaseException as exc:
        try:
            result = f"{type(exc).__name__}: {exc}"
        except BaseException:
            result = type(exc).__name__
        status = "failed"
    # Only the parent writes SQLite. Publish a complete result atomically.
    destination = Path(output)
    staging = destination.with_suffix(".tmp")
    staging.write_text(json.dumps([status, result]), encoding="utf-8")
    os.replace(staging, destination)


def _stop(process):
    if process.is_alive():
        process.terminate()
        process.join(2)
    if process.is_alive():
        process.kill()
        process.join(2)
    if process.is_alive():
        raise IsolationError("Worker death not confirmed; task remains running")


def run_isolated(handler, payload, deadline):
    """Return only after worker exit. Timeout includes spawn/startup overhead."""
    if time.monotonic() >= deadline:
        return "timed_out", "Task execution deadline exceeded"
    ctx = multiprocessing.get_context("spawn")
    with tempfile.TemporaryDirectory(prefix="luna-worker-") as directory:
        output = Path(directory) / "result.json"
        process = ctx.Process(target=_entry, args=(handler, payload, str(output)), daemon=True)
        try:
            process.start()
            process.join(max(0, deadline - time.monotonic()))
            if process.is_alive() or time.monotonic() >= deadline:
                _stop(process)
                return "timed_out", "Task execution deadline exceeded"
            if process.exitcode != 0 or not output.exists():
                return "failed", "Isolated worker exited without a result"
            return tuple(json.loads(output.read_text(encoding="utf-8")))
        finally:
            # Also stop the worker on parent-side errors/KeyboardInterrupt.
            if process.pid is not None:
                _stop(process)
                process.close()
