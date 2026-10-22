# Security Policy

This project is an early prototype and is not suitable for executing untrusted tasks in production.

Do not report security vulnerabilities in public issues. Contact the maintainer privately through their GitHub profile until a dedicated security reporting channel is configured.

Never commit tokens, API keys, personal data, or secrets. The human approval mechanism is a programmatic gate, not authentication or sandboxing.

## Trust boundary

Only trusted application code should access an Orchestrator instance. Task objects,
worker registration, the task dictionary and audit list are mutable Python objects;
they are not an authorization boundary. Call approve only from a trusted caller.
Approval does not authenticate a human or bind a cryptographic snapshot of a payload.
Registered handlers execute with the Python process permissions, without a sandbox.
Do not pass credentials in payloads or exception messages: results store handler
exception text verbatim. Audit events are in memory by default, or persisted in SQLite when configured.
Trusted application code and anyone with database write access can change them.


## SQLite and Telegram

SQLite files contain plaintext payloads, results and audit events. Restrict file
permissions, keep the database on trusted local storage, and protect backups.
The database is not encrypted. Do not place bot tokens or credentials in tasks.
Never copy a live database file without SQLite-aware backup procedures.

Telegram authentication relies on HTTPS updates fetched directly from Telegram,
then checks numeric sender ID and private-chat identity before reading task data.
Do not pass untrusted JSON to handle_update as if it were an authenticated webhook.
Keep tokens in the local environment. Network exception text and Telegram API error
descriptions are not exposed in CLI errors, since request URLs contain the token.
All allowlisted users can approve/reject any awaiting task. Decisions currently
record the action but not the Telegram actor ID.

SQLite conditional writes protect normal concurrent execution, not malicious
database edits. Recovery must run only with all executors stopped. Retries can
repeat side effects; opt in only for handlers whose operations are idempotent.
Directly mutating public Task/dictionary objects is unsupported and can bypass
in-process controls. Use orchestrator APIs for transitions.


## Deadline-mode isolation

A configured task deadline uses a spawned process which the supervisor terminates
and, if necessary, kills. This is process lifecycle isolation, not a security
sandbox: the child inherits the account's filesystem/network access and environment.
Use only trusted importable handlers. Do not execute untrusted pickles, malicious
code or handlers that launch their own child processes.

Forced termination skips handler finally blocks and cannot reverse external side
effects. Timeout never retries automatically. If process death is uncertain, state
remains running and cannot be replayed. After supervisor failure, stop all surviving
workers before maintenance recovery; startup deliberately does not replay tasks.
Temporary JSON result files live in a private OS temporary directory and are removed
after worker exit; task payloads/results must still exclude secrets.
