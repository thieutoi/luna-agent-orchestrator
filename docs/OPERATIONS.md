# Persistence, polling and recovery

## SQLite

Schema v3 upgrades v1/v2 transactionally and adds monotonic consumer checkpoints.
Tasks, approval state, attempts and audit history are preserved. Older application
versions cannot open v3: back up and stop old executors/pollers before upgrading.
Do not mix versions against one database.

Task state and audit remain one transaction; conditional writes reject stale claims.
Only parent supervisors publish results. Telegram decisions record actor ID in the
same event, e.g. TASK_ID:approved:telegram:123. IDs are personal data; restrict access.

## Polling delivery contract

An OS lock permits one local poller per database and releases on process exit.
The file stays on disk; existence is not proof of a live lock. This does not
coordinate different databases/hosts or protect a bot token globally.
409 conflicts stop the adapter; investigate before restarting.

Offsets are namespaced by public bot ID, never its secret token. Processed IDs,
including unauthorized/rate-limited messages, are checkpointed before reply.
Restart resumes the cursor. Failed reply delivery is not replayed.
A crash between decision and checkpoint may redeliver a guarded decision;
state checks reject repeated approve/reject. Delivery is not exactly once.

Telegram retains updates at most 24h and can randomize IDs after a week idle.
After more than 24h without a processed update, obsolete offsets are reset so
lower new IDs are not suppressed. Tasks/audit remain intact. Old dated approval
commands are still rejected.

Rate limits are per authorized user, in RAM; restart resets them. Default 20/minute,
configurable 1-100. At most 100 allowed users. Unauthorized IDs allocate no buckets.
Excess commands are silently dropped and checkpointed. This is not distributed
anti-DDoS protection.

## Diagnose locally

    luna doctor --config luna-config.json
    luna logs --config luna-config.json

Doctor may create/migrate the configured DB. It prints state counts and token
presence, never paths, IDs, payloads/results or credentials. It does not certify
network access, correct token, live operation or absence of orphan workers.

## Crash recovery

1. Stop the bot and ALL executors/supervisors using the database on every machine.
2. Find and stop/verify surviving worker processes; a parent crash can orphan them.
3. Inspect external side effects, which recovery/retry cannot roll back.
4. Make a protected SQLite-aware backup.
5. Run luna recover --config luna-config.json --confirm-stopped.
6. Inspect logs/states before restarting trusted executors.

Recovery requires the operator assertion and rejects a concurrent local poller lock.
The flag does not detect arbitrary orphan workers. It marks interrupted running
tasks failed with unknown outcome; never auto-retries/recreates them.
Completed/rejected/timed_out states stay terminal.

For consistent backups, stop writers and use sqlite3.Connection.backup() from
trusted local Python or the sqlite3 CLI backup command. Do not simply copy a live
database and assume its journal/transaction is included. Restore only while all
processes are stopped, into a protected path with compatible application version.
Backups contain plaintext task/actor data.

## Release checklist

- Inspect tests and real matrix CI, plus installed CLI smoke tests.
- Owner tests a separate real bot privately; never disclose its token.
- Review configuration/backup permissions and supported workload limits.
- Review license, CHANGELOG and maintainer security contact.
- Tag/release/PyPI publication requires explicit owner approval. The candidate is
  not published and the wizard installs no unattended service.
