"""SQLite persistence: channels and settings (edited from the panel), cached AI copy, publication log,
jobs queued by the panel for the bot, audit log, and the numbers behind the dashboard.

Both processes (bot and panel) open the same file; WAL mode lets them read and write concurrently.
A Storage instance must stay on the thread that created it.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .config import Channel, Target, Tuning

SCHEMA = """
CREATE TABLE IF NOT EXISTS copies (
    product_id  TEXT NOT NULL,
    language    TEXT NOT NULL,
    content     TEXT NOT NULL,          -- JSON: {"headline", "body", "hashtags"}
    created_at  TEXT NOT NULL,
    PRIMARY KEY (product_id, language)
);

CREATE TABLE IF NOT EXISTS publications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_key  TEXT NOT NULL,
    platform     TEXT NOT NULL,         -- key of platforms.PLATFORMS
    product_id   TEXT NOT NULL,
    external_id  TEXT,
    price        TEXT,
    currency     TEXT,
    published_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_publications_channel ON publications (channel_key, published_at);
CREATE INDEX IF NOT EXISTS idx_publications_time ON publications (published_at);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    language       TEXT NOT NULL,
    category       TEXT NOT NULL,
    enabled        INTEGER NOT NULL DEFAULT 1,
    tracking_id    TEXT,
    every_n_cycles INTEGER NOT NULL DEFAULT 1,
    active_from    TEXT,
    active_to      TEXT,
    created_at     TEXT NOT NULL,
    UNIQUE (language, category)
);

CREATE TABLE IF NOT EXISTS targets (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id        INTEGER NOT NULL REFERENCES channels (id) ON DELETE CASCADE,
    platform          TEXT NOT NULL,
    target_id         TEXT NOT NULL,
    enabled           INTEGER NOT NULL DEFAULT 1,
    label             TEXT NOT NULL DEFAULT '',
    secret            TEXT,
    secret_expires_at TEXT,
    created_at        TEXT NOT NULL,
    UNIQUE (channel_id, platform, target_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL                 -- JSON
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    action     TEXT NOT NULL,           -- create | update | delete | restore
    entity     TEXT NOT NULL,           -- table name
    entity_key TEXT NOT NULL,
    summary    TEXT NOT NULL,
    before     TEXT,                    -- JSON row, NULL if it did not exist
    after      TEXT
);

CREATE TABLE IF NOT EXISTS blacklist (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL CHECK (kind IN ('product', 'keyword')),
    value      TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE (kind, value)
);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL,          -- JSON
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | failed | cancelled
    run_at      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    finished_at TEXT,
    result      TEXT                    -- JSON
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status, run_at);

CREATE TABLE IF NOT EXISTS errors (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    source  TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS followers (
    day       TEXT NOT NULL,
    platform  TEXT NOT NULL,
    target_id TEXT NOT NULL,
    count     INTEGER NOT NULL,
    PRIMARY KEY (day, platform, target_id)
);

CREATE TABLE IF NOT EXISTS orders (
    order_key    TEXT PRIMARY KEY,      -- sub order id when present, else order id
    order_id     TEXT NOT NULL,
    tracking_id  TEXT NOT NULL,
    product_id   TEXT,
    status       TEXT,
    currency     TEXT,
    paid_amount  REAL,
    commission   REAL,
    created_time TEXT,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    ip TEXT NOT NULL,
    ok INTEGER NOT NULL
);
"""

# Tables whose changes are audited and can be restored, with their primary key column.
AUDITED = {"channels": "id", "targets": "id", "blacklist": "id", "settings": "key"}

JOB_KINDS = ("preview", "post_channel", "manual_post", "sync_commissions", "refresh_status")
PUBLISHING_JOBS = ("post_channel", "manual_post")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime | None = None) -> str:
    return (moment or _now()).astimezone(timezone.utc).isoformat()


class StorageError(ValueError):
    pass


class Storage:
    def __init__(self, path: str):
        self._con = sqlite3.connect(path, timeout=15)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA foreign_keys=ON")
        self._con.execute("PRAGMA busy_timeout=15000")
        self._con.executescript(SCHEMA)

    def close(self) -> None:
        self._con.close()

    def backup_to(self, path: str) -> None:
        """Consistent copy of the whole database, safe while the other process is writing."""
        target = sqlite3.connect(path)
        try:
            self._con.backup(target)
        finally:
            target.close()

    def _rows(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        return [dict(r) for r in self._con.execute(sql, tuple(params)).fetchall()]

    def _one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        row = self._con.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    # --- AI copy cache -------------------------------------------------------

    def get_copy(self, product_id: str, language: str) -> dict | None:
        row = self._con.execute(
            "SELECT content FROM copies WHERE product_id = ? AND language = ?", (product_id, language)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def save_copy(self, product_id: str, language: str, content: dict) -> None:
        with self._con:
            self._con.execute(
                "INSERT OR REPLACE INTO copies (product_id, language, content, created_at) VALUES (?, ?, ?, ?)",
                (product_id, language, json.dumps(content, ensure_ascii=False), _iso()),
            )

    # --- publication log -----------------------------------------------------

    def record_publication(self, channel_key: str, platform: str, product_id: str,
                           external_id: str | None, price: str, currency: str) -> None:
        with self._con:
            self._con.execute(
                "INSERT INTO publications (channel_key, platform, product_id, external_id, price, currency, published_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (channel_key, platform, product_id, external_id, price, currency, _iso()),
            )

    def recently_published(self, channel_key: str, days: int) -> set[str]:
        since = _iso(_now() - timedelta(days=days))
        rows = self._con.execute(
            "SELECT DISTINCT product_id FROM publications WHERE channel_key = ? AND published_at >= ?",
            (channel_key, since),
        )
        return {r[0] for r in rows}

    def stats_since(self, since: datetime) -> dict[str, int]:
        rows = self._con.execute(
            "SELECT platform, COUNT(*) FROM publications WHERE published_at >= ? GROUP BY platform",
            (_iso(since),),
        )
        return {r[0]: r[1] for r in rows}

    def stats_by_language_since(self, since: datetime) -> dict[str, int]:
        """Channel keys are "<Language>/<category>", so the language is the part before the slash."""
        rows = self._con.execute(
            "SELECT substr(channel_key, 1, instr(channel_key, '/') - 1) AS language, COUNT(*)"
            " FROM publications WHERE published_at >= ? GROUP BY language ORDER BY COUNT(*) DESC",
            (_iso(since),),
        )
        return {r[0]: r[1] for r in rows}

    def stats_by_channel_since(self, since: datetime) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for r in self._con.execute(
            "SELECT channel_key, platform, COUNT(*) FROM publications WHERE published_at >= ?"
            " GROUP BY channel_key, platform", (_iso(since),)):
            out.setdefault(r[0], {})[r[1]] = r[2]
        return out

    def daily_counts(self, days: int) -> list[dict]:
        """Posts per UTC day and platform, for the dashboard chart."""
        return self._rows(
            "SELECT substr(published_at, 1, 10) AS day, platform, COUNT(*) AS n FROM publications"
            " WHERE published_at >= ? GROUP BY day, platform ORDER BY day",
            (_iso(_now() - timedelta(days=days)),),
        )

    def recent_publications(self, limit: int = 20) -> list[dict]:
        return self._rows("SELECT * FROM publications ORDER BY id DESC LIMIT ?", (limit,))

    # --- small key/value state ----------------------------------------------

    def get_meta(self, key: str) -> str | None:
        row = self._con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._con:
            self._con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def get_meta_json(self, key: str, default: Any = None) -> Any:
        raw = self.get_meta(key)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except ValueError:
            return default

    def set_meta_json(self, key: str, value: Any) -> None:
        self.set_meta(key, json.dumps(value, ensure_ascii=False, default=str))

    # --- audit ---------------------------------------------------------------

    def _snapshot(self, table: str, key: Any) -> dict | None:
        row = self._one(f"SELECT * FROM {table} WHERE {AUDITED[table]} = ?", (key,))
        if row and table == "channels":
            row["_targets"] = self._rows("SELECT * FROM targets WHERE channel_id = ?", (key,))
        return row

    def _audit(self, action: str, table: str, key: Any, summary: str,
               before: dict | None, after: dict | None) -> None:
        self._con.execute(
            "INSERT INTO audit_log (at, action, entity, entity_key, summary, before, after) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_iso(), action, table, str(key), summary,
             json.dumps(before, ensure_ascii=False) if before is not None else None,
             json.dumps(after, ensure_ascii=False) if after is not None else None),
        )

    def audit_log(self, limit: int = 200) -> list[dict]:
        rows = self._rows("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["before"] = json.loads(r["before"]) if r["before"] else None
            r["after"] = json.loads(r["after"]) if r["after"] else None
        return rows

    def restore(self, audit_id: int) -> str:
        """Put an audited row back to how it was before that change. Returns a summary."""
        entry = self._one("SELECT * FROM audit_log WHERE id = ?", (audit_id,))
        if not entry:
            raise StorageError("רשומה לא נמצאה")
        table, pk = entry["entity"], AUDITED.get(entry["entity"])
        if not pk:
            raise StorageError("אי אפשר לשחזר את הרשומה הזו")
        key: Any = entry["entity_key"]
        if pk == "id":
            key = int(key)
        wanted = json.loads(entry["before"]) if entry["before"] else None
        current = self._snapshot(table, key)
        summary = f"שחזור: {entry['summary']}"
        try:
            with self._con:
                if wanted is None:
                    self._con.execute(f"DELETE FROM {table} WHERE {pk} = ?", (key,))
                else:
                    children = wanted.pop("_targets", None)
                    self._upsert(table, pk, wanted)
                    # Targets come back only with a deleted channel; an edit restore leaves them alone.
                    if children is not None and current is None:
                        self._con.execute("DELETE FROM targets WHERE channel_id = ?", (key,))
                        for child in children:
                            self._upsert("targets", "id", child)
                self._audit("restore", table, key, summary, current, self._snapshot(table, key))
        except sqlite3.IntegrityError as exc:
            raise StorageError(f"השחזור מתנגש בנתונים קיימים ({exc})") from exc
        return summary

    def _upsert(self, table: str, pk: str, row: dict) -> None:
        cols = list(row)
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != pk)
        self._con.execute(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
            f" ON CONFLICT ({pk}) DO UPDATE SET {updates}",
            [row[c] for c in cols],
        )

    # --- settings ------------------------------------------------------------

    def settings(self) -> dict[str, Any]:
        return {r["key"]: json.loads(r["value"]) for r in self._rows("SELECT key, value FROM settings")}

    def tuning(self) -> Tuning:
        return Tuning.from_settings(self.settings())

    def set_settings(self, values: dict[str, Any], *, audit: bool = True) -> list[str]:
        """Write changed keys only; returns the keys that changed."""
        current = self.settings()
        changed = []
        with self._con:
            for key, value in values.items():
                if key in current and current[key] == value:
                    continue
                before = self._snapshot("settings", key)
                self._con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                                  (key, json.dumps(value, ensure_ascii=False)))
                if audit:
                    self._audit("update", "settings", key, f"הגדרה {key}", before, self._snapshot("settings", key))
                changed.append(key)
        return changed

    # --- channels and targets ------------------------------------------------

    def channels(self, *, only_enabled: bool = False) -> list[Channel]:
        where = " WHERE enabled = 1" if only_enabled else ""
        rows = self._rows(f"SELECT * FROM channels{where} ORDER BY language, category = 'main' DESC, category")
        targets: dict[int, list[Target]] = {}
        for t in self._rows("SELECT * FROM targets ORDER BY platform, id"):
            targets.setdefault(t["channel_id"], []).append(_target(t))
        return [_channel(r, targets.get(r["id"], [])) for r in rows]

    def channel(self, channel_id: int) -> Channel | None:
        row = self._one("SELECT * FROM channels WHERE id = ?", (channel_id,))
        if not row:
            return None
        return _channel(row, [_target(t) for t in self._rows(
            "SELECT * FROM targets WHERE channel_id = ? ORDER BY platform, id", (channel_id,))])

    def target(self, target_id: int) -> Target | None:
        row = self._one("SELECT * FROM targets WHERE id = ?", (target_id,))
        return _target(row) if row else None

    def create_channel(self, language: str, category: str, **fields: Any) -> int:
        try:
            with self._con:
                cur = self._con.execute(
                    "INSERT INTO channels (language, category, enabled, tracking_id, every_n_cycles, active_from,"
                    " active_to, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (language, category, int(fields.get("enabled", True)), fields.get("tracking_id"),
                     int(fields.get("every_n_cycles", 1)), fields.get("active_from"), fields.get("active_to"), _iso()),
                )
                new_id = cur.lastrowid
                self._audit("create", "channels", new_id, f"ערוץ {language}/{category} נוסף",
                            None, self._snapshot("channels", new_id))
        except sqlite3.IntegrityError:
            raise StorageError(f"כבר קיים ערוץ {language}/{category}") from None
        return int(new_id)

    def update_channel(self, channel_id: int, **fields: Any) -> None:
        allowed = {"enabled", "tracking_id", "every_n_cycles", "active_from", "active_to"}
        self._update("channels", channel_id, {k: v for k, v in fields.items() if k in allowed}, "ערוץ")

    def delete_channel(self, channel_id: int) -> None:
        before = self._snapshot("channels", channel_id)
        if not before:
            return
        with self._con:
            self._con.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
            self._audit("delete", "channels", channel_id,
                        f"ערוץ {before['language']}/{before['category']} נמחק", before, None)

    def create_target(self, channel_id: int, platform: str, target_id: str, *, label: str = "",
                      secret: str | None = None, enabled: bool = True) -> int:
        try:
            with self._con:
                cur = self._con.execute(
                    "INSERT INTO targets (channel_id, platform, target_id, enabled, label, secret, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (channel_id, platform, target_id.strip(), int(enabled), label, secret or None, _iso()),
                )
                new_id = cur.lastrowid
                self._audit("create", "targets", new_id, f"יעד {platform} {target_id} נוסף",
                            None, self._snapshot("targets", new_id))
        except sqlite3.IntegrityError:
            raise StorageError("היעד הזה כבר קיים בערוץ") from None
        return int(new_id)

    def update_target(self, target_id: int, *, audit: bool = True, **fields: Any) -> None:
        allowed = {"target_id", "enabled", "label", "secret", "secret_expires_at"}
        self._update("targets", target_id, {k: v for k, v in fields.items() if k in allowed}, "יעד", audit=audit)

    def delete_target(self, target_id: int) -> None:
        before = self._snapshot("targets", target_id)
        if not before:
            return
        with self._con:
            self._con.execute("DELETE FROM targets WHERE id = ?", (target_id,))
            self._audit("delete", "targets", target_id,
                        f"יעד {before['platform']} {before['target_id']} נמחק", before, None)

    def _update(self, table: str, row_id: int, values: dict[str, Any], noun: str, *, audit: bool = True) -> None:
        if not values:
            return
        before = self._snapshot(table, row_id)
        if not before:
            raise StorageError(f"{noun} לא נמצא")
        values = {k: (int(v) if isinstance(v, bool) else v) for k, v in values.items()}
        if all(before.get(k) == v for k, v in values.items()):
            return
        try:
            with self._con:
                self._con.execute(
                    f"UPDATE {table} SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ?",
                    [*values.values(), row_id],
                )
                if audit:
                    name = (f"{before['language']}/{before['category']}" if table == "channels"
                            else f"{before['platform']} {before['target_id']}")
                    changed = ", ".join(k for k in values if before.get(k) != values[k])
                    self._audit("update", table, row_id, f"{noun} {name}: {changed}",
                                before, self._snapshot(table, row_id))
        except sqlite3.IntegrityError:
            raise StorageError("הערך מתנגש ביעד קיים") from None

    def seed_from_env(self, channels: list[tuple[str, str, str | None, str | None]],
                      tuning: dict[str, Any]) -> int:
        """One-time import of the old .env configuration. Returns how many channels were created."""
        if self.get_meta("seeded_from_env"):
            return 0
        created = 0
        if not self._one("SELECT id FROM channels LIMIT 1"):
            for language, category, telegram_id, facebook_id in channels:
                channel_id = self.create_channel(language, category)
                if telegram_id:
                    self.create_target(channel_id, "telegram", telegram_id)
                if facebook_id:
                    self.create_target(channel_id, "facebook", facebook_id)
                created += 1
            if tuning and not self.settings():
                self.set_settings(tuning)
        self.set_meta("seeded_from_env", _iso())
        return created

    # --- blacklist -----------------------------------------------------------

    def blacklist(self) -> list[dict]:
        return self._rows("SELECT * FROM blacklist ORDER BY kind, value")

    def blacklist_sets(self) -> tuple[set[str], list[str]]:
        ids, words = set(), []
        for r in self._rows("SELECT kind, value FROM blacklist"):
            if r["kind"] == "product":
                ids.add(r["value"])
            else:
                words.append(r["value"].lower())
        return ids, words

    def add_blacklist(self, kind: str, value: str, note: str = "") -> int:
        value = value.strip()
        if kind not in ("product", "keyword") or not value:
            raise StorageError("ערך לא תקין")
        try:
            with self._con:
                cur = self._con.execute(
                    "INSERT INTO blacklist (kind, value, note, created_at) VALUES (?, ?, ?, ?)",
                    (kind, value, note, _iso()))
                self._audit("create", "blacklist", cur.lastrowid, f"רשימה שחורה: {value} נוסף",
                            None, self._snapshot("blacklist", cur.lastrowid))
        except sqlite3.IntegrityError:
            raise StorageError("כבר ברשימה") from None
        return int(cur.lastrowid)

    def remove_blacklist(self, item_id: int) -> None:
        before = self._snapshot("blacklist", item_id)
        if not before:
            return
        with self._con:
            self._con.execute("DELETE FROM blacklist WHERE id = ?", (item_id,))
            self._audit("delete", "blacklist", item_id, f"רשימה שחורה: {before['value']} הוסר", before, None)

    # --- jobs (panel -> bot) --------------------------------------------------

    def add_job(self, kind: str, payload: dict, run_at: datetime | None = None) -> int:
        if kind not in JOB_KINDS:
            raise StorageError(f"unknown job kind {kind}")
        with self._con:
            cur = self._con.execute(
                "INSERT INTO jobs (kind, payload, run_at, created_at) VALUES (?, ?, ?, ?)",
                (kind, json.dumps(payload, ensure_ascii=False), _iso(run_at), _iso()))
        return int(cur.lastrowid)

    def claim_due_jobs(self, *, allow_publishing: bool) -> list[dict]:
        """Mark due jobs as running and return them (oldest first)."""
        kinds = JOB_KINDS if allow_publishing else tuple(k for k in JOB_KINDS if k not in PUBLISHING_JOBS)
        with self._con:
            rows = self._rows(
                f"SELECT * FROM jobs WHERE status = 'pending' AND run_at <= ? AND kind IN"
                f" ({', '.join('?' for _ in kinds)}) ORDER BY run_at, id", (_iso(), *kinds))
            for r in rows:
                self._con.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (r["id"],))
        for r in rows:
            r["payload"] = json.loads(r["payload"])
        return rows

    def finish_job(self, job_id: int, *, ok: bool, result: Any) -> None:
        with self._con:
            self._con.execute(
                "UPDATE jobs SET status = ?, finished_at = ?, result = ? WHERE id = ?",
                ("done" if ok else "failed", _iso(), json.dumps(result, ensure_ascii=False, default=str), job_id))

    def fail_interrupted_jobs(self) -> None:
        with self._con:
            self._con.execute(
                "UPDATE jobs SET status = 'failed', finished_at = ?, result = ? WHERE status = 'running'",
                (_iso(), json.dumps({"error": "הבוט הופעל מחדש באמצע העבודה"})))

    def cancel_job(self, job_id: int) -> bool:
        with self._con:
            cur = self._con.execute("UPDATE jobs SET status = 'cancelled', finished_at = ? WHERE id = ?"
                                    " AND status = 'pending'", (_iso(), job_id))
        return cur.rowcount > 0

    def job(self, job_id: int) -> dict | None:
        row = self._one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if row:
            row["payload"] = json.loads(row["payload"])
            row["result"] = json.loads(row["result"]) if row["result"] else None
        return row

    def jobs(self, kinds: Iterable[str] = JOB_KINDS, limit: int = 50) -> list[dict]:
        kinds = tuple(kinds)
        rows = self._rows(f"SELECT * FROM jobs WHERE kind IN ({', '.join('?' for _ in kinds)})"
                          " ORDER BY id DESC LIMIT ?", (*kinds, limit))
        for r in rows:
            r["payload"] = json.loads(r["payload"])
            r["result"] = json.loads(r["result"]) if r["result"] else None
        return rows

    # --- errors ----------------------------------------------------------------

    def record_error(self, source: str, message: str) -> None:
        with self._con:
            self._con.execute("INSERT INTO errors (at, source, message) VALUES (?, ?, ?)",
                              (_iso(), source, message[:2000]))
            # Keep the table small; the log files hold the full history.
            self._con.execute("DELETE FROM errors WHERE id <= (SELECT MAX(id) FROM errors) - 1000")

    def recent_errors(self, limit: int = 20) -> list[dict]:
        return self._rows("SELECT * FROM errors ORDER BY id DESC LIMIT ?", (limit,))

    def errors_since(self, since: datetime) -> int:
        return self._con.execute("SELECT COUNT(*) FROM errors WHERE at >= ?", (_iso(since),)).fetchone()[0]

    # --- followers ------------------------------------------------------------

    def record_followers(self, day: str, platform: str, target_id: str, count: int) -> None:
        with self._con:
            self._con.execute("INSERT OR REPLACE INTO followers (day, platform, target_id, count) VALUES (?, ?, ?, ?)",
                              (day, platform, target_id, count))

    def follower_history(self, days: int = 90) -> list[dict]:
        since = (_now() - timedelta(days=days)).date().isoformat()
        return self._rows("SELECT * FROM followers WHERE day >= ? ORDER BY day", (since,))

    # --- affiliate orders -----------------------------------------------------

    def upsert_orders(self, orders: list[dict]) -> int:
        with self._con:
            for o in orders:
                self._con.execute(
                    "INSERT INTO orders (order_key, order_id, tracking_id, product_id, status, currency, paid_amount,"
                    " commission, created_time, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT (order_key) DO UPDATE SET status = excluded.status, paid_amount = excluded.paid_amount,"
                    " commission = excluded.commission, currency = excluded.currency, updated_at = excluded.updated_at",
                    (o["order_key"], o["order_id"], o["tracking_id"], o.get("product_id"), o.get("status"),
                     o.get("currency"), o.get("paid_amount"), o.get("commission"), o.get("created_time"), _iso()))
        return len(orders)

    def commission_by_tracking(self, since: datetime) -> list[dict]:
        return self._rows(
            "SELECT tracking_id, currency, COUNT(*) AS orders, SUM(paid_amount) AS sales, SUM(commission) AS commission"
            " FROM orders WHERE created_time >= ? GROUP BY tracking_id, currency ORDER BY commission DESC",
            (since.strftime("%Y-%m-%d"),))

    def recent_orders(self, limit: int = 50) -> list[dict]:
        return self._rows("SELECT * FROM orders ORDER BY created_time DESC LIMIT ?", (limit,))

    # --- login attempts -------------------------------------------------------

    def record_login(self, ip: str, ok: bool) -> None:
        with self._con:
            self._con.execute("INSERT INTO login_attempts (at, ip, ok) VALUES (?, ?, ?)", (_iso(), ip, int(ok)))
            self._con.execute("DELETE FROM login_attempts WHERE at < ?", (_iso(_now() - timedelta(days=30)),))

    def login_failures(self, since: datetime, ip: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM login_attempts WHERE ok = 0 AND at >= ?"
        params: list[Any] = [_iso(since)]
        if ip is not None:
            sql += " AND ip = ?"
            params.append(ip)
        return self._con.execute(sql, params).fetchone()[0]


class DbKeyValue:
    """Meta-table access that is safe from any thread: each call opens its own short connection."""

    def __init__(self, path: str):
        self._path = path

    def get(self, key: str) -> str | None:
        storage = Storage(self._path)
        try:
            return storage.get_meta(key)
        finally:
            storage.close()

    def set(self, key: str, value: str) -> None:
        storage = Storage(self._path)
        try:
            storage.set_meta(key, value)
        finally:
            storage.close()


def _target(row: dict) -> Target:
    return Target(id=row["id"], channel_id=row["channel_id"], platform=row["platform"], target_id=row["target_id"],
                  enabled=bool(row["enabled"]), label=row["label"] or "", secret=row["secret"],
                  secret_expires_at=row["secret_expires_at"])


def _channel(row: dict, targets: list[Target]) -> Channel:
    return Channel(id=row["id"], language=row["language"], category=row["category"], enabled=bool(row["enabled"]),
                   tracking_id=row["tracking_id"] or None, every_n_cycles=row["every_n_cycles"] or 1,
                   active_from=row["active_from"] or None, active_to=row["active_to"] or None,
                   targets=tuple(targets))
