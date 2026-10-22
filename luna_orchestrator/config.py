"""Local non-secret configuration. Bot tokens are environment-only."""
import json
import os
from pathlib import Path


def validate(config, base):
    if not isinstance(config, dict) or set(config) - {"allowed_user_ids", "db_path", "rate_limit"}:
        raise ValueError("Unknown configuration fields; never store tokens in config")
    ids = config.get("allowed_user_ids")
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 100
            or any(type(i) is not int or i <= 0 for i in ids)):
        raise ValueError("Set 1-100 positive integer user IDs")
    rate = config.get("rate_limit", 20)
    if type(rate) is not int or not 1 <= rate <= 100:
        raise ValueError("rate_limit must be between 1 and 100")
    path = config.get("db_path", "luna.sqlite3")
    if not isinstance(path, str) or not path.strip() or path == ":memory:":
        raise ValueError("Set a file-backed database path")
    return {"allowed_user_ids": sorted(set(ids)), "rate_limit": rate,
            "db_path": str((base / path).resolve())}


def load_config(path="luna-config.json", *, environ=None):
    env = os.environ if environ is None else environ
    file = Path(path).resolve()
    config = json.loads(file.read_text(encoding="utf-8-sig")) if file.exists() else {}
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object")
    if "LUNA_TELEGRAM_USER_IDS" in env:
        config["allowed_user_ids"] = [int(value.strip()) for value in env["LUNA_TELEGRAM_USER_IDS"].split(",")]
    if "LUNA_DB_PATH" in env:
        config["db_path"] = env["LUNA_DB_PATH"]
    return validate(config, file.parent)


def configure(path="luna-config.json", *, ask=input):
    file = Path(path).resolve()
    if file.exists():
        raise ValueError("Configuration exists; edit it locally rather than overwrite")
    ids = [int(value.strip()) for value in ask("Allowed Telegram user IDs (comma separated): ").split(",")]
    db = ask("SQLite filename [luna.sqlite3]: ").strip() or "luna.sqlite3"
    rate = int(ask("Commands per user per minute [20]: ").strip() or "20")
    data = {"allowed_user_ids": ids, "db_path": db, "rate_limit": rate}
    validate(data, file.parent)
    # Exclusive creation preserves existing work; restrictive POSIX permissions.
    descriptor = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")
    return file
