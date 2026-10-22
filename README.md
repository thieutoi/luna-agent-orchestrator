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

Retry accepts 0â€“10 additional attempts, runs immediately for handler exceptions
(including result conversion errors), and holds the task in `running` throughout.
There is no delay, backoff, timeout or scheduler. Use retry only for idempotent
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


## Final QA / review gate

The GitHub Actions workflow tests Python 3.10, 3.11 and 3.12 on Ubuntu and Windows,
including compile, unit/regression tests and the local demo.

**Handler execution timeout is not implemented.** A blocked handler stays running;
retries start only after an exception. SQLite connection timeouts, Telegram HTTP
timeouts and the CI job timeout do not cancel an Agent handler. PR #2 remains Draft
until the required timeout behavior is implemented and verified. See
[final QA evidence](validation/FINAL_QA.vi.md) for the tested commit and CI results.
