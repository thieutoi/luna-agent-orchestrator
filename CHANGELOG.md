# Changelog

## 0.3.0 — unreleased development candidate

- Installable package and luna CLI; standard-library-only runtime.
- Non-secret configuration wizard, local doctor/logs/recover commands.
- Bounded network/5xx reconnect, 429 retry_after, fatal auth/conflict errors.
- SQLite v3 durable per-bot offsets, idle-offset reset, local OS poller lock.
- Strict user-ID allowlist, per-user throttling, stale-decision protection.
- Telegram /system and /logs; actor IDs in atomic approval/rejection events.
- Windows/Linux installation, Telegram setup and recovery/security guides.
- Simulated integration tests and installed CLI/package checks in matrix CI.
- No live deployment, PyPI upload, tag or release.

## 0.2.0 — merged development milestone

- SQLite persistence, atomic audit and manual interrupted-task recovery.
- Telegram approval controls and strict allowlist.
- Bounded retry and spawned-worker task deadlines.
- Python 3.10/3.11/3.12 CI on Ubuntu and Windows.

## 0.1.0 — initial prototype

- Named workers, in-memory routing, sensitive-task approval and audit.
