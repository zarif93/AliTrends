"""SQLite persistence: cached AI copy and a log of what was published where."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

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
    platform     TEXT NOT NULL,         -- "telegram" | "facebook"
    product_id   TEXT NOT NULL,
    external_id  TEXT,
    price        TEXT,
    currency     TEXT,
    published_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_publications_channel ON publications (channel_key, published_at);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Storage:
    def __init__(self, path: str):
        self._con = sqlite3.connect(path)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.executescript(SCHEMA)

    def close(self) -> None:
        self._con.close()

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
                (product_id, language, json.dumps(content, ensure_ascii=False), _now().isoformat()),
            )

    # --- publication log -----------------------------------------------------

    def record_publication(self, channel_key: str, platform: str, product_id: str,
                           external_id: str | None, price: str, currency: str) -> None:
        with self._con:
            self._con.execute(
                "INSERT INTO publications (channel_key, platform, product_id, external_id, price, currency, published_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (channel_key, platform, product_id, external_id, price, currency, _now().isoformat()),
            )

    def recently_published(self, channel_key: str, days: int) -> set[str]:
        since = (_now() - timedelta(days=days)).isoformat()
        rows = self._con.execute(
            "SELECT DISTINCT product_id FROM publications WHERE channel_key = ? AND published_at >= ?",
            (channel_key, since),
        )
        return {r[0] for r in rows}

    def stats_since(self, since: datetime) -> dict[str, int]:
        rows = self._con.execute(
            "SELECT platform, COUNT(*) FROM publications WHERE published_at >= ? GROUP BY platform",
            (since.isoformat(),),
        )
        return dict(rows.fetchall())
