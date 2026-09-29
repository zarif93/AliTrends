"""`python -m alitrends.panel [serve|set-password]`.

serve         run the panel (waitress) and the bot watchdog
set-password  create or replace the admin username and password
"""
from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

from waitress import serve

from ..config import Secrets
from ..platforms import TelegramPublisher
from ..runner import ensure_seeded, setup_logging
from ..storage import Storage
from . import auth
from .app import create_app

log = logging.getLogger("alitrends.panel")

WATCHDOG_INTERVAL = 60


def watchdog(secrets: Secrets, telegram: TelegramPublisher) -> None:
    """Alert the admin when the bot's heartbeat goes stale, and again when it comes back."""
    storage = Storage(secrets.db_path)
    while True:
        try:
            beat = storage.get_meta("heartbeat")
            if beat:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(beat)
                limit = timedelta(minutes=storage.tuning().watchdog_minutes)
                alerted = storage.get_meta("watchdog_alerted") == "1"
                if age > limit and not alerted:
                    minutes = int(age.total_seconds() // 60)
                    telegram.notify_admin(f"🚨 הבוט לא מגיב כבר {minutes} דקות.\n"
                                          f"בדקו: systemctl status alitrends / journalctl -u alitrends -n 100")
                    storage.set_meta("watchdog_alerted", "1")
                elif age <= limit and alerted:
                    telegram.notify_admin("✅ הבוט חזר לפעול")
                    storage.set_meta("watchdog_alerted", "0")
        except Exception:
            log.exception("Watchdog check failed")
        time.sleep(WATCHDOG_INTERVAL)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="AliTrends admin panel")
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("serve", help="run the panel (default)")
    run.add_argument("--host", default=os.getenv("PANEL_HOST", "127.0.0.1"))
    run.add_argument("--port", type=int, default=int(os.getenv("PANEL_PORT", "8080")))
    sub.add_parser("set-password", help="set the admin username and password")
    args = parser.parse_args(argv)

    secrets = Secrets.from_env()
    storage = Storage(secrets.db_path)
    ensure_seeded(storage)

    if args.command == "set-password":
        username = input("Username: ").strip()
        password = getpass.getpass("Password (5+ characters): ")
        if password != getpass.getpass("Repeat password: "):
            sys.exit("Passwords do not match")
        try:
            auth.set_credentials(storage, username, password)
        except ValueError as exc:
            sys.exit(str(exc))
        print("Saved. All existing panel sessions were logged out.")
        return

    host = getattr(args, "host", os.getenv("PANEL_HOST", "127.0.0.1"))
    port = getattr(args, "port", int(os.getenv("PANEL_PORT", "8080")))
    setup_logging(secrets.log_dir, verbose=False, filename="panel.log")
    if not auth.credentials_configured(storage):
        log.warning("No admin password yet: run `python -m alitrends.panel set-password`")
    storage.close()

    telegram = TelegramPublisher(secrets.telegram_token, secrets.admin_chat_id)
    threading.Thread(target=watchdog, args=(secrets, telegram), daemon=True, name="watchdog").start()
    log.info("Panel listening on http://%s:%d", host, port)
    serve(create_app(secrets, telegram=telegram), host=host, port=port, threads=4, ident="AliTrends")


if __name__ == "__main__":
    main()
