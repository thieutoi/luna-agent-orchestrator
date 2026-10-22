"""Transactional SQLite snapshots and audit events (standard library only)."""

import json
import os
import sqlite3
from contextlib import closing


class SQLiteStore:
    def __init__(self, path):
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("Use a file-backed SQLite database")
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass  # Preserve existing database and owner-managed permissions.
        else:
            os.close(descriptor)
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3):
                raise ValueError("Unsupported database schema")
            db.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY, event TEXT NOT NULL)")
            if version < 2:
                for task_id, encoded in db.execute("SELECT id, data FROM tasks").fetchall():
                    data = json.loads(encoded)
                    data.setdefault("timeout_seconds", None)
                    db.execute("UPDATE tasks SET data=? WHERE id=?",
                               (json.dumps(data, sort_keys=True), task_id))
            db.execute("CREATE TABLE IF NOT EXISTS checkpoints (name TEXT PRIMARY KEY, value INTEGER NOT NULL)")
            db.execute("PRAGMA user_version = 3")

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def load(self):
        with closing(self.connect()) as db:
            # Keep tasks and audit from the same database snapshot.
            db.execute("BEGIN")
            tasks = [json.loads(row[0]) for row in db.execute("SELECT data FROM tasks ORDER BY rowid")]
            audit = [row[0] for row in db.execute("SELECT event FROM audit ORDER BY seq")]
            return tasks, audit

    def save(self, data, previous, event):
        encoded = json.dumps(data, sort_keys=True)
        with closing(self.connect()) as db, db:
            if previous is None:
                db.execute("INSERT INTO tasks(id, data) VALUES (?, ?)", (data["id"], encoded))
            else:
                result = db.execute(
                    "UPDATE tasks SET data=? WHERE id=? AND data=?",
                    (encoded, data["id"], json.dumps(previous, sort_keys=True)),
                )
                if result.rowcount != 1:
                    raise ValueError("Task changed in another orchestrator; refresh and retry")
            db.execute("INSERT INTO audit(event) VALUES (?)", (event,))

    def checkpoint(self, name, value=None):
        with closing(self.connect()) as db, db:
            if value is not None:
                db.execute(
                    "INSERT INTO checkpoints(name,value) VALUES (?,?) "
                    "ON CONFLICT(name) DO UPDATE SET value=MAX(value,excluded.value)",
                    (name, value),
                )
            row = db.execute("SELECT value FROM checkpoints WHERE name=?", (name,)).fetchone()
            return row[0] if row else None

    def reset_checkpoint(self, name):
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM checkpoints WHERE name IN (?,?)", (name, name + ":updated"))
