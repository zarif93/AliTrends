"""Single-admin login: username + password, then a one-time code sent to the admin's Telegram chat.

Sessions are signed cookies. Each carries the current "session epoch"; bumping the epoch (password
change, "log out everywhere") invalidates every existing session.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone

from flask import abort, current_app, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from ..storage import Storage

CODE_TTL_SECONDS = 300
CODE_MAX_TRIES = 5
LOCK_WINDOW = timedelta(minutes=15)
MAX_FAILURES_PER_IP = 5
MAX_FAILURES_TOTAL = 20
MIN_PASSWORD_LENGTH = 5

PUBLIC_ENDPOINTS = {"login", "login_code", "static"}


def set_credentials(storage: Storage, username: str, password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"הסיסמה חייבת להיות באורך {MIN_PASSWORD_LENGTH} תווים לפחות")
    storage.set_meta("admin_username", username.strip())
    storage.set_meta("admin_password_hash", generate_password_hash(password))
    bump_epoch(storage)


def credentials_configured(storage: Storage) -> bool:
    return bool(storage.get_meta("admin_password_hash"))


def check_credentials(storage: Storage, username: str, password: str) -> bool:
    stored_user = storage.get_meta("admin_username") or ""
    stored_hash = storage.get_meta("admin_password_hash")
    if not stored_hash:
        return False
    user_ok = hmac.compare_digest(stored_user.encode(), username.strip().encode())
    return check_password_hash(stored_hash, password) and user_ok


def bump_epoch(storage: Storage) -> str:
    epoch = secrets.token_hex(8)
    storage.set_meta("session_epoch", epoch)
    return epoch


def locked_out(storage: Storage, ip: str) -> bool:
    since = datetime.now(timezone.utc) - LOCK_WINDOW
    return (storage.login_failures(since, ip) >= MAX_FAILURES_PER_IP
            or storage.login_failures(since) >= MAX_FAILURES_TOTAL)


def _code_hash(code: str) -> str:
    return hmac.new(current_app.secret_key.encode(), code.encode(), hashlib.sha256).hexdigest()


def start_code_challenge(username: str) -> str:
    """Put a pending login in the session and return the code to send."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    session.clear()
    session["pending"] = {"user": username, "code": _code_hash(code),
                          "expires": time.time() + CODE_TTL_SECONDS, "tries": 0}
    return code


def verify_code(storage: Storage, code: str) -> str:
    """Returns "ok", "retry" (wrong code, may try again) or "restart" (expired / too many tries)."""
    pending = session.get("pending")
    if not pending or time.time() > pending["expires"]:
        session.pop("pending", None)
        return "restart"
    pending["tries"] += 1
    session["pending"] = pending
    if hmac.compare_digest(pending["code"], _code_hash(code.strip())):
        session.clear()
        session.permanent = True
        session["user"] = pending["user"]
        session["epoch"] = storage.get_meta("session_epoch") or bump_epoch(storage)
        session["csrf"] = secrets.token_hex(16)
        return "ok"
    if pending["tries"] >= CODE_MAX_TRIES:
        session.pop("pending", None)
        return "restart"
    return "retry"


def is_logged_in(storage: Storage) -> bool:
    return bool(session.get("user")) and session.get("epoch") == storage.get_meta("session_epoch")


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def check_csrf() -> None:
    if request.method != "POST":
        return
    sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token") or ""
    if not session.get("csrf") or not hmac.compare_digest(sent, session["csrf"]):
        abort(400, "CSRF token missing or invalid — רעננו את הדף ונסו שוב")


def guard() -> object | None:
    """before_request hook: CSRF on every POST, login on every non-public page."""
    check_csrf()
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if not is_logged_in(g.storage):
        session.pop("user", None)
        if request.path.endswith(".json"):
            abort(401)
        return redirect(url_for("login", next=request.full_path if request.method == "GET" else None))
    return None

