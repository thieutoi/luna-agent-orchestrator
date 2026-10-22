"""Private-chat Telegram control, durable polling and bounded network recovery."""
from collections import deque
import json
import logging
import math
import os
import re
import sys
import time
from http.client import HTTPException
from threading import Event
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .core import Orchestrator

LOG = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    """Sanitized failure: the token-bearing URL must never enter logs."""
    def __init__(self, message, *, retryable=True, retry_after=None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


def _api_failure(code, parameters=None):
    delay = (parameters or {}).get("retry_after") if isinstance(parameters, (dict, type(None))) else None
    if type(delay) not in (int, float) or not 0 <= delay <= 3600 or not math.isfinite(delay):
        delay = None
    return TelegramError(f"Telegram request rejected (code {code if type(code) is int else 'unknown'})",
                         retryable=code == 429 or (type(code) is int and code >= 500),
                         retry_after=delay)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TelegramClient:
    def __init__(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
            raise ValueError("Invalid Telegram token format")
        self._token = token
        self.bot_id = token.split(":", 1)[0]
        self._opener = build_opener(_NoRedirect())

    def call(self, method, **params):
        if method not in {"getUpdates", "sendMessage"}:
            raise ValueError("Unsupported Telegram method")
        request = Request(
            f"https://api.telegram.org/bot{self._token}/{method}",
            data=json.dumps({k: v for k, v in params.items() if v is not None}).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with self._opener.open(request, timeout=40) as response:
                data = json.load(response)
            if not isinstance(data, dict):
                raise TelegramError("Invalid Telegram response", retryable=False)
            if data.get("ok") is not True:
                raise _api_failure(data.get("error_code"), data.get("parameters"))
            return data["result"]
        except HTTPError as exc:
            parameters = None
            try:
                if exc.code == 429:
                    parameters = json.loads(exc.read(65536)).get("parameters")
            except (ValueError, AttributeError, OSError, HTTPException):
                pass
            finally:
                exc.close()
            raise _api_failure(exc.code, parameters) from None
        except (URLError, OSError, HTTPException):
            raise TelegramError("Telegram network failure") from None
        except (ValueError, KeyError):
            raise TelegramError("Invalid Telegram response", retryable=False) from None


class TelegramAdapter:
    def __init__(self, orchestrator, allowed_user_ids, client, *,
                 cursor_key="telegram", rate_limit=20, window_seconds=60, clock=time.monotonic):
        ids = tuple(allowed_user_ids)
        if not ids or len(ids) > 100 or any(type(i) is not int or i <= 0 for i in ids):
            raise ValueError("Allowlist requires 1-100 positive integer user IDs")
        if type(rate_limit) is not int or not 1 <= rate_limit <= 100:
            raise ValueError("rate_limit must be between 1 and 100")
        if (type(window_seconds) not in (int, float) or not 1 <= window_seconds <= 3600):
            raise ValueError("window_seconds must be between 1 and 3600")
        self.orchestrator = orchestrator
        self.allowed_user_ids = frozenset(ids)
        self.client = client
        self.cursor_key = cursor_key
        self.offset = orchestrator.checkpoint(cursor_key)
        self._last_update = orchestrator.checkpoint(cursor_key + ":updated")
        self.rate_limit, self.window_seconds = rate_limit, window_seconds
        self._clock, self._requests = clock, {}

    def handle_update(self, update):
        if not isinstance(update, dict) or not isinstance(update.get("message"), dict):
            return None
        message = update["message"]
        sender, chat = message.get("from"), message.get("chat")
        if not isinstance(sender, dict) or not isinstance(chat, dict):
            return None
        user_id = sender.get("id")
        if (type(user_id) is not int or user_id not in self.allowed_user_ids
                or sender.get("is_bot") is not False or chat.get("type") != "private"
                or type(chat.get("id")) is not int or chat["id"] != user_id):
            return None
        text = message.get("text")
        if not isinstance(text, str) or len(text) > 4096 or not text.startswith("/"):
            return None
        # Authorized users only: bounded queues, silent drops to avoid reply storms.
        now = self._clock()
        requests = self._requests.setdefault(user_id, deque())
        while requests and requests[0] <= now - self.window_seconds:
            requests.popleft()
        if len(requests) >= self.rate_limit:
            return None
        requests.append(now)
        parts = text.split()
        command = parts[0]
        if command == "/start" and len(parts) == 1:
            return "Luna: /status, /system, /tasks, /logs, /approve TASK_ID, /reject TASK_ID"
        o = self.orchestrator
        if command in {"/approve", "/reject"}:
            if len(parts) != 2:
                return f"Usage: {command} TASK_ID"
            # Old queued decisions should not authorize current work.
            date = message.get("date")
            if date is not None and (type(date) is not int or abs(time.time() - date) > 300):
                return "Decision expired. Send a new command."
            try:
                (o.approve if command == "/approve" else o.reject)(parts[1], actor_id=user_id)
            except (KeyError, ValueError):
                return "Task missing or not awaiting approval."
            return "Approved." if command == "/approve" else "Rejected."
        if command == "/system" and len(parts) == 1:
            status = o.system_status()
            return f"Storage: {status['storage']}; tasks: {status['tasks']}\n" + json.dumps(status["states"])
        if command == "/logs" and len(parts) == 1:
            events = o.task_events()
            return "\n".join(events) if events else "No task events."
        if command in {"/status", "/tasks"} and len(parts) == 1:
            with o._state_lock:
                o.refresh()
                tasks = list(o.tasks.values())
                if command == "/status":
                    return f"Luna ready. Tasks: {len(tasks)}"
                lines = [f"{t.id} {t.status.value}" for t in tasks[-50:]]
                return ("Latest 50 tasks:\n" + "\n".join(lines)) if lines else "No tasks."
        return "Unknown command or invalid arguments. Use /start."

    def poll_once(self):
        # Telegram retains updates for at most 24h; IDs may randomize after a week.
        # Do not let an obsolete high offset suppress future messages forever.
        if self._last_update is not None and time.time() - self._last_update > 86400:
            self.orchestrator.reset_checkpoint(self.cursor_key)
            self.offset, self._last_update = None, None
        updates = self.client.call("getUpdates", offset=self.offset, timeout=25,
                                   allowed_updates=["message"])
        if not isinstance(updates, list):
            raise TelegramError("Invalid Telegram updates response", retryable=False)
        for update in updates:
            if (not isinstance(update, dict) or type(update.get("update_id")) is not int
                    or update["update_id"] < 0):
                continue
            update_id = update["update_id"]
            if self.offset is not None and update_id < self.offset:
                continue
            reply = self.handle_update(update)
            # Save before reply: failed delivery must not replay decisions/replies.
            # A crash between a decision and checkpoint may repeat a guarded decision.
            self.offset = self.orchestrator.checkpoint(self.cursor_key, update_id + 1)
            self._last_update = self.orchestrator.checkpoint(self.cursor_key + ":updated", int(time.time()))
            if reply is not None:
                self.client.call("sendMessage", chat_id=update["message"]["chat"]["id"], text=reply)

    def run(self, stop=None, *, max_failures=6, wait=None):
        if type(max_failures) is not int or not 1 <= max_failures <= 20:
            raise ValueError("max_failures must be between 1 and 20")
        stop = stop if stop is not None else Event()
        wait = wait if wait is not None else stop.wait
        failures = 0
        while not stop.is_set():
            try:
                self.poll_once()
                failures = 0
                wait(0.2)
            except TelegramError as exc:
                failures += 1
                if not exc.retryable or failures >= max_failures:
                    raise
                delay = max(min(2 ** (failures - 1), 30), exc.retry_after or 0)
                LOG.warning("Telegram disconnected; retrying after %.1f seconds", delay)
                wait(delay)


def main(config_path=None):
    from .config import load_config
    from .locking import polling_lock
    import sqlite3
    try:
        config = load_config(config_path or os.environ.get("LUNA_CONFIG", "luna-config.json"), environ=os.environ)
        client = TelegramClient(os.environ["LUNA_TELEGRAM_TOKEN"])
        with polling_lock(config["db_path"]):
            o = Orchestrator(config["db_path"])
            adapter = TelegramAdapter(o, config["allowed_user_ids"], client,
                                      cursor_key=f"telegram:{client.bot_id}",
                                      rate_limit=config["rate_limit"])
            logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
            adapter.run()
    except KeyboardInterrupt:
        return 0
    except TelegramError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (KeyError, ValueError, OSError, sqlite3.Error, RuntimeError):
        print("Luna bot stopped. Check local configuration, network, webhook and poller ownership.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
