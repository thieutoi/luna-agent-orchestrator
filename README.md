# Luna Agent Orchestrator

Local-first Python task orchestration with SQLite, approvals, Telegram controls
and optional process-isolated execution deadlines.

**v0.3.0 is an unreleased development candidate.** Python 3.10+; no third-party
runtime dependencies, bundled LLM provider or production bot.

## Quick Start (target: about 10 minutes)

Until the candidate is merged, use its feature branch:

    git clone https://github.com/thieutoi/luna-agent-orchestrator.git
    cd luna-agent-orchestrator
    git checkout feature/v0.3.0

Windows PowerShell, without changing execution policy:

    py -3 -m venv .venv
    .venv\Scripts\python.exe -m pip install .
    .venv\Scripts\luna.exe demo
    .venv\Scripts\luna.exe configure
    .venv\Scripts\luna.exe doctor

Linux:

    python3 -m venv .venv
    .venv/bin/python -m pip install .
    .venv/bin/luna demo
    .venv/bin/luna configure
    .venv/bin/luna doctor

Expected demo: HELLO LUNA, completed. The wizard asks only for numeric Telegram user
IDs, SQLite filename and command limit. It creates ignored local luna-config.json
and refuses to overwrite files. It never requests or saves a token.
Doctor's token_configured=false is normal before bot setup. The 10-minute goal
assumes Python/Git/venv/internet are available; it was not timed with a beginner.
pip may download setuptools as a build backend.

[Telegram Windows/Linux setup](docs/TELEGRAM.md), [operations/recovery](docs/OPERATIONS.md),
[fake-ID configuration example](examples/luna-config.example.json).
Install from source; do not guess a PyPI package name for this unpublished version.

## Commands

| CLI | Purpose |
|---|---|
| luna demo | Offline echo, no token/network |
| luna configure | Create non-secret local configuration |
| luna doctor | Python/storage/task states and token presence |
| luna logs | Latest 50 audit events |
| luna recover --confirm-stopped | Mark interrupted tasks failed, never replay |
| luna telegram | Explicitly start the real bot |

Use --config PATH with configure/doctor/logs/recover/telegram, or LUNA_CONFIG.
python -m luna_orchestrator supports the same commands without installation.
Existing demo and telegram module entry points remain supported.

| Telegram | Purpose |
|---|---|
| /start | Help |
| /status | Task count |
| /system | Storage and task counts by state |
| /tasks | Latest 50 IDs/states |
| /logs | Latest 20 audit events |
| /approve TASK_ID | Awaiting approval to pending, no execution |
| /reject TASK_ID | Awaiting approval to terminal rejected |

Only allowlisted numeric IDs in matching private chats are accepted.
Defaults: 20 commands/user/minute, silently dropping excess; 100 users maximum.
Allowed users share management of all tasks: this is not multi-tenant.
Replies omit paths, payloads, results and credentials.

## Embed Luna

    from luna_orchestrator import Orchestrator

    def main():
        o = Orchestrator("luna.sqlite3")
        o.register("echo", str.upper)
        task = o.submit("echo", "hello", sensitive=True,
                        timeout_seconds=5, max_retries=1)
        o.approve(task.id)
        print(o.execute(task.id).result)

    if __name__ == "__main__":
        main()

Orchestrator() uses RAM; a database path persists task/audit changes atomically.
Register trusted handlers after restart; functions are never deserialized.
Call refresh() to read changes by another instance. Telegram only controls tasks;
it never accepts arbitrary code/payloads or executes handlers. Embed a separate
trusted application to submit and run real work.

## Retry and deadlines

max_retries accepts 0-10 extra attempts, default 0, immediately after handler errors.
Use idempotent handlers: side effects cannot be undone. Approval covers the budget.

timeout_seconds accepts finite seconds in (0,86400]. A spawned process gets one
monotonic budget for startup/retries. Deadline causes terminate/join, then kill/join
if needed. Confirmed worker death yields terminal timed_out, never retried.
Late results cannot overwrite terminal/recovered state. Supervision uncertainty
keeps the claim running, blocking replay. Cleanup and OS/SQLite scheduling may
extend caller wall time past deadline.

Default None keeps inline execution without a deadline. Process handlers must be
importable/picklable top-level callables, not closures/lambdas. Guard startup as above.
Descendant subprocess trees are unsupported. This is not a sandbox.

## Testing / release preparation

    python -m compileall -q luna_orchestrator tests
    python -m unittest discover -s tests -v
    python -m luna_orchestrator.demo

CI installs the package and tests installed CLI/compile/tests/demo on Python
3.10/3.11/3.12, Ubuntu/Windows. Telegram integration tests use fake transports.
See [v0.3.0 QA](validation/V030.vi.md), [CHANGELOG](CHANGELOG.md),
[security](SECURITY.md) and [contributing](CONTRIBUTING.md).
Validation reports are historical evidence, not live deployment claims.
No tag, PyPI upload, service installation or release is automatic.

MIT license.
