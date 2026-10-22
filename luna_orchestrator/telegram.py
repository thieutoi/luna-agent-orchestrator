"""Private-chat Telegram control adapter with a fail-closed user allowlist."""

import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .core import Orchestrator


class TelegramError(RuntimeError):
    """Sanitized transport failure; never include the token-bearing URL."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TelegramClient:
    def __init__(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
            raise ValueError("Invalid Telegram token format")
        self._token = token
        self._opener = build_opener(_NoRedirect())

    def call(self, method, **params):
        if method not in {"getUpdates", "sendMessage"}:
            raise ValueError("Unsupported Telegram method")
        request = Request(
            f"https://api.telegram.org/bot{self._token}/{method}",
            data=json.dumps(params).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with self._opener.open(request, timeout=40) as response:
                data = json.load(response)
            if not isinstance(data, dict) or data.get("ok") is not True:
                raise TelegramError("Telegram API rejected the request")
            return data["result"]
        except (HTTPError, URLError, OSError, ValueError, KeyError):
            raise TelegramError("Telegram request failed; check network and bot configuration") from None


class TelegramAdapter:
    def __init__(self, orchestrator, allowed_user_ids, client):
        ids = tuple(allowed_user_ids)
        # Validate before deduplication: True == 1 and 1.0 == 1 in Python.
        if not ids or any(type(i) is not int or i <= 0 for i in ids):
            raise ValueError("A nonempty allowlist of positive integer user IDs is required")
        self.orchestrator = orchestrator
        self.allowed_user_ids = frozenset(ids)
        self.client = client
        self.offset = None

    def handle_update(self, update):
        """Ignore unauthorized/malformed updates without reading task state."""
        if not isinstance(update, dict):
            return None
        message = update.get("message")
        if not isinstance(message, dict):
            return None
        sender, chat = message.get("from"), message.get("chat")
        if not isinstance(sender, dict) or not isinstance(chat, dict):
            return None
        user_id = sender.get("id")
        if (type(user_id) is not int or user_id not in self.allowed_user_ids
                or sender.get("is_bot") is not False or chat.get("type") != "private"
                or type(chat.get("id")) is not int or chat["id"] != user_id):
            return None
        text = message.get("text")
        if not isinstance(text, str) or not text.startswith("/"):
            return None
        parts = text.split()
        command = parts[0]
        # Addressed commands are ignored in private-chat mode.
        if command == "/start" and len(parts) == 1:
            return "Luna: /status, /tasks, /approve TASK_ID, /reject TASK_ID"
        o = self.orchestrator
        if command in {"/approve", "/reject"}:
            if len(parts) != 2:
                return f"Usage: {command} TASK_ID"
            try:
                (o.approve if command == "/approve" else o.reject)(parts[1])
            except (KeyError, ValueError):
                return "Task missing or not awaiting approval."
            return "Approved." if command == "/approve" else "Rejected."
        if command in {"/status", "/tasks"} and len(parts) == 1:
            with o._state_lock:
                o.refresh()
                tasks = list(o.tasks.values())
                if command == "/status":
                    return f"Luna ready. Tasks: {len(tasks)}"
                # IDs and states only: never expose payloads, worker names or results.
                lines = [f"{t.id} {t.status.value}" for t in tasks[-50:]]
                return ("Latest 50 tasks:\n" + "\n".join(lines)) if lines else "No tasks."
        return "Unknown command or invalid arguments. Use /start."

    def poll_once(self):
        updates = self.client.call("getUpdates", offset=self.offset, timeout=25,
                                   allowed_updates=["message"])
        if not isinstance(updates, list):
            raise TelegramError("Invalid Telegram updates response")
        for update in updates:
            if not isinstance(update, dict) or type(update.get("update_id")) is not int:
                continue
            update_id = update["update_id"]
            if self.offset is not None and update_id < self.offset:
                continue
            reply = self.handle_update(update)
            if reply is not None:
                self.client.call("sendMessage", chat_id=update["message"]["chat"]["id"],
                                 text=reply)
            self.offset = update_id + 1


def main():
    try:
        token = os.environ["LUNA_TELEGRAM_TOKEN"]
        raw_ids = os.environ["LUNA_TELEGRAM_USER_IDS"].split(",")
        ids = [int(value.strip()) for value in raw_ids]
        o = Orchestrator(os.environ.get("LUNA_DB_PATH", "luna.sqlite3"))
        adapter = TelegramAdapter(o, ids, TelegramClient(token))
    except (KeyError, ValueError):
        print("Set LUNA_TELEGRAM_TOKEN and LUNA_TELEGRAM_USER_IDS locally.", file=sys.stderr)
        return 2
    try:
        while True:
            adapter.poll_once()
    except KeyboardInterrupt:
        return 0
    except TelegramError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
