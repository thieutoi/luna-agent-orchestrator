# Luna Agent Orchestrator

An open-source, local-first Python task orchestrator for AI-agent workflows. Built to make task routing, approvals, and audit trails easy to understand and extend.

> Early-stage prototype. No model providers or Telegram bot are connected yet.

## Features

- Register named workers and route tasks to them
- Human approval required before tasks marked sensitive can execute
- In-memory task state and audit events
- Standard-library-only Python implementation

## Quick start

Requires Python 3.10+.

```bash
python -m luna_orchestrator.demo
python -m unittest discover -s tests -v
```

## Roadmap

- SQLite persistence and recovery
- Telegram command adapter with authorization
- Optional LLM adapters, rate limits, retries, and job scheduling
- Documentation, contribution guide, and security review

## Safety

Do not commit tokens, credentials, personal data, or production workflow secrets. This project is independent from any private business automation repository.

## License

MIT. See [LICENSE](LICENSE).

## Execution state

A task is marked `running` before its handler is called. Concurrent or nested
execution of the same running task is rejected, as is execution of completed or
failed tasks. This is an in-process guard; it does not provide persistence or
cross-process coordination.
