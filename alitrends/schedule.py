"""Shabbat quiet window, always evaluated in Israel time regardless of the server's timezone.

The window is deliberately conservative so it covers the whole year without an external zmanim source:
earliest candle lighting (winter) is ~16:00 and latest havdalah (summer) is ~20:50 in Israel.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

ISRAEL = ZoneInfo("Asia/Jerusalem")

FRIDAY, SATURDAY = 4, 5
STOP_AT = time(14, 0)     # Friday
RESUME_AT = time(21, 30)  # Saturday night


def now_israel() -> datetime:
    return datetime.now(ISRAEL)


def is_shabbat(moment: datetime) -> bool:
    local = moment.astimezone(ISRAEL)
    if local.weekday() == FRIDAY:
        return local.time() >= STOP_AT
    if local.weekday() == SATURDAY:
        return local.time() < RESUME_AT
    return False


def resume_time(moment: datetime) -> datetime:
    """When posting may resume, for a moment inside the Shabbat window."""
    local = moment.astimezone(ISRAEL)
    days_to_saturday = (SATURDAY - local.weekday()) % 7
    saturday = (local + timedelta(days=days_to_saturday)).date()
    return datetime.combine(saturday, RESUME_AT, tzinfo=ISRAEL)
