# Luna Agent Orchestrator

An open-source, local-first Python task orchestrator for AI-agent workflows. Built to make task routing, approvals, and audit trails easy to understand and extend.

> Early-stage prototype. Optional SQLite storage and Telegram control adapter; no model provider is bundled.

## Features

- Register named workers and route tasks to them
- Human approval required before tasks marked sensitive can execute
- In-memory or SQLite task state and audit events
- Optional private-chat Telegram approval controls with a user-ID allowlist
- Opt-in bounded retries and explicit rejection
- Standard-library-only Python implementation

## Quick start

Requires Python 3.10+.

```bash
python -m luna_orchestrator.demo
python -m unittest discover -s tests -v
```

## Roadmap

- Versioned database migrations, retention and backup tooling
- Approval actor records and durable Telegram update checkpoints
- Optional LLM adapters, rate limits, delayed retry backoff and job scheduling
- Documentation, contribution guide, and security review

## Safety

Do not commit tokens, credentials, personal data, or production workflow secrets. This project is independent from any private business automation repository.

## License

MIT. See [LICENSE](LICENSE).

## Execution state

A task is marked `running` before its handler is called. Concurrent or nested
execution of the same running task is rejected, as is execution of completed or
failed tasks. Without SQLite this guard applies only within one process. With
SQLite, conditional database updates also prevent competing task claims.


## SQLite persistence and retries

Existing `Orchestrator()` usage remains in memory. Pass a file path to persist
task records and audit events atomically:

```python
from luna_orchestrator import Orchestrator

o = Orchestrator("luna.sqlite3")
o.register("echo", str.upper)
task = o.submit("echo", "hello", sensitive=True, max_retries=2)
o.approve(task.id)
o.execute(task.id)  # At most 3 attempts total; defaults to 1 attempt.

restored = Orchestrator("luna.sqlite3")
assert restored.tasks[task.id].result == "HELLO"
```

Handlers are trusted Python code and must be registered again after restart.
Payloads, results, approval/rejection states, attempt counts and audit events are
restored; functions and credentials are never serialized. Call `refresh()` when
reading a long-lived instance after another instance changes the database.
SQLite uses parameterized queries and conditional updates to claim each task.
A schema version prevents silently opening a newer unsupported database.

Retry accepts 0Ã¢â‚¬â€œ10 additional attempts, runs immediately for handler exceptions
(including result conversion errors), and holds the task in `running` throughout.
There is no retry delay, backoff or scheduler. Optional task deadlines are described below. Use retry only for idempotent
handlers: side effects can happen before an exception. A sensitive task's approval
covers its configured retry budget. Exhausted tasks remain failed and cannot replay.

After a crash, `running` tasks remain blocked rather than automatically replaying.
Stop **all executors** before calling `recover_interrupted()` in maintenance code;
it marks them failed with an unknown outcome. Inspect external effects before
submitting any replacement. This is not an exactly-once side-effect guarantee.

## Telegram Bot Adapter

The adapter uses the official [Telegram Bot API](https://core.telegram.org/bots/api)
via Python's standard-library HTTPS client; no additional package is needed.

Set these environment variables locally (never commit their values):

- `LUNA_TELEGRAM_TOKEN`: bot token from BotFather.
- `LUNA_TELEGRAM_USER_IDS`: comma-separated numeric Telegram user IDs allowed to
  control this orchestrator. Empty/invalid allowlists prevent startup.
- `LUNA_DB_PATH`: optional SQLite file path; defaults to `luna.sqlite3`.

Run:

```bash
python -m luna_orchestrator.telegram
```

Only allowlisted, non-bot senders in private chats are accepted. Groups, channel
posts, edited messages and unauthenticated webhook input are not supported.
Use one polling process per bot and remove any webhook before polling.

Commands:

| Command | Behavior |
|---|---|
| `/start` | Show supported commands |
| `/status` | Show total task count |
| `/tasks` | Show IDs and states for the latest 50 tasks |
| `/approve TASK_ID` | Change awaiting approval to pending |
| `/reject TASK_ID` | Change awaiting approval to rejected (terminal) |

Approval does **not** execute a task. A separate trusted application registers
handlers, submits tasks and calls `execute()`; this control adapter never accepts
arbitrary code or payloads via Telegram. Allowlisted users share access to all tasks
in this database; this is not a multi-tenant service. Replies omit payloads/results.

Polling offsets are held in memory. After restart, Telegram may redeliver updates;
approval/rejection state guards reject repeated decisions. Delivery is not
transactional with Telegram replies, so a decision may succeed even if its reply
fails. Transport/API failures stop the adapter with a sanitized error; an operator
may restart it. No live bot is automatically started by the demo or unit tests.


## Agent execution deadlines

Pass `timeout_seconds` to `submit()` to isolate that task's handler in a fresh
`multiprocessing` **spawn process**. Accepted values are finite seconds in
`(0, 86400]`. The default `None` retains legacy inline execution with no deadline.

```python
from luna_orchestrator import Orchestrator

def main():
    o = Orchestrator("luna.sqlite3")
    o.register("echo", str.upper)
    task = o.submit("echo", "hello", timeout_seconds=5, max_retries=1)
    o.execute(task.id)

if __name__ == "__main__":
    main()
```

Isolated handlers must be picklable, importable top-level callables. Lambdas,
closures, interactive-session functions and handlers depending on mutable parent
memory are not supported in deadline mode. Use importable modules and protect
application startup with `if __name__ == "__main__"` on all platforms.
Legacy inline handlers keep their existing behavior.

A monotonic deadline covers the **whole execution**, including process startup,
result handling and every retry. On expiry the parent terminates the worker,
waits up to 2 seconds, escalates to kill and waits up to another 2 seconds.
Only after confirmed exit may it persist the terminal `timed_out` state and audit
event. This stops the actual worker process and its threads, not just the caller's
wait. OS scheduling and cleanup/SQLite commits can extend the caller's wall time.

Timeout is terminal and is **never retried**, even when retry budget remains.
Failed attempts may retry only after their worker exits and before the shared
deadline expires. The worker never receives the orchestrator or a SQLite
connection; only the parent publishes results. Late results and stale state
snapshots cannot turn a timed-out/recovered task into a successful task.
If death cannot be confirmed or process supervision/startup fails,
`IsolationError` leaves the task `running`, blocking
replay. A failed terminal-state database write also leaves the durable claim
`running` and requires maintenance.

Process isolation is not a sandbox. Handlers must be trusted and must not launch
descendant processes; killing an arbitrary subprocess tree is not supported.
External requests/effects already performed cannot be undone. If the orchestrator
itself crashes, a child may survive: stop and verify all executor/worker processes
before `recover_interrupted()`. Restart does not automatically replay running tasks
or persist a usable monotonic deadline across machine restarts.
SQLite schema v1 is transactionally upgraded to v2; old records default to
`timeout_seconds=None`. Older application versions cannot open the v2 database.

## QA

CI tests Python 3.10, 3.11 and 3.12 on Ubuntu and Windows. See
[timeout QA evidence](validation/TIMEOUT_QA.vi.md) for scope and actual run results.
Earlier FINAL_QA.vi.md describes the historical timeout blocker before this fix.
