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
exception text verbatim. Audit events currently remain in memory and can be changed
by application code.
