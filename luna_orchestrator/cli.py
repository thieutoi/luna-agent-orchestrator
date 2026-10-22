"""Beginner-friendly local commands; no network except the explicit telegram command."""
import argparse
import json
import os
import sqlite3
import sys

from .config import configure, load_config
from .core import Orchestrator
from .locking import polling_lock


def main(argv=None):
    parser = argparse.ArgumentParser(prog="luna")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("configure", "doctor", "logs", "recover", "telegram"):
        sub = commands.add_parser(command)
        sub.add_argument("--config", default=os.environ.get("LUNA_CONFIG", "luna-config.json"))
        if command == "recover":
            sub.add_argument("--confirm-stopped", action="store_true")
    commands.add_parser("demo")
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            from .demo import main as demo
            demo()
        elif args.command == "configure":
            configure(args.config)
            print("Configuration created. Set LUNA_TELEGRAM_TOKEN locally; it was not requested or saved.")
        elif args.command == "telegram":
            from .telegram import main as telegram
            return telegram(args.config)
        else:
            if args.command == "recover" and not args.confirm_stopped:
                raise ValueError("Stop and verify ALL executors/workers, then use --confirm-stopped")
            config = load_config(args.config)
            o = Orchestrator(config["db_path"])
            if args.command == "doctor":
                status = o.system_status()
                status["python"] = ".".join(map(str, sys.version_info[:3]))
                status["token_configured"] = bool(os.environ.get("LUNA_TELEGRAM_TOKEN"))
                print(json.dumps(status, indent=2))
            elif args.command == "logs":
                print("\n".join(o.task_events(50)) or "No task events.")
            elif args.command == "recover":
                with polling_lock(config["db_path"]):
                    print(f"Recovered {len(o.recover_interrupted())} interrupted tasks; no tasks were replayed.")
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, sqlite3.Error, RuntimeError):
        print("Command failed. Check local configuration/database; existing files were preserved.", file=sys.stderr)
        return 2
