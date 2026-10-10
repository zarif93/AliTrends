"""Shabbat and Yom Tov quiet window, always evaluated in Israel time regardless of the server's timezone.

A quiet window covers a run of consecutive rest days (Shabbat and the Yom Tov days kept in Israel). It starts
`stop_before` minutes before sunset on the eve of the first rest day and ends `resume_after` minutes after
sunset on the last one. Sunset is computed for Jerusalem (NOAA solar formula, accurate to about a minute),
and the Hebrew calendar comes from pyluach, so no network or zmanim service is needed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from pyluach.dates import HebrewDate

ISRAEL = ZoneInfo("Asia/Jerusalem")
LATITUDE, LONGITUDE = 31.7683, 35.2137  # Jerusalem

STOP_BEFORE_MINUTES = 60   # before sunset on the eve (Jerusalem candle lighting is 40 minutes before)
RESUME_AFTER_MINUTES = 60  # after sunset at the end (havdalah in Israel is ~35-40 minutes after)

SATURDAY = 5
# Yom Tov days in Israel as (pyluach month, day); pyluach numbers months from Nisan = 1, so Tishrei = 7.
YOM_TOV = {
    (7, 1): "ראש השנה", (7, 2): "ראש השנה", (7, 10): "יום כיפור", (7, 15): "סוכות",
    (7, 22): "שמיני עצרת", (1, 15): "פסח", (1, 21): "שביעי של פסח", (3, 6): "שבועות",
}


@dataclass(frozen=True)
class QuietWindow:
    start: datetime
    end: datetime
    name: str  # "שבת", or the holiday's name (Yom Tov that falls on Shabbat is named for the holiday)


def now_israel() -> datetime:
    return datetime.now(ISRAEL)


def holiday(day: date) -> str | None:
    hebrew = HebrewDate.from_pydate(day)
    return YOM_TOV.get((hebrew.month, hebrew.day))


def is_rest_day(day: date) -> bool:
    return day.weekday() == SATURDAY or holiday(day) is not None


def sunset(day: date) -> datetime:
    """Sunset in Jerusalem on `day`, as an aware Israel-time datetime."""
    n = day.timetuple().tm_yday
    gamma = 2 * math.pi / 365 * (n - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                       - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma) - 0.006758 * math.cos(2 * gamma)
            + 0.000907 * math.sin(2 * gamma) - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
    lat = math.radians(LATITUDE)
    hour_angle = math.degrees(math.acos(
        math.cos(math.radians(90.833)) / (math.cos(lat) * math.cos(decl)) - math.tan(lat) * math.tan(decl)))
    minutes_utc = 720 - 4 * (LONGITUDE - hour_angle) - eqtime
    moment = datetime.combine(day, time(0), tzinfo=timezone.utc) + timedelta(minutes=minutes_utc)
    return moment.astimezone(ISRAEL)


def quiet_window(moment: datetime, stop_before: int = STOP_BEFORE_MINUTES,
                 resume_after: int = RESUME_AFTER_MINUTES) -> QuietWindow | None:
    """The quiet window `moment` falls in, or None when posting is allowed."""
    local = moment.astimezone(ISRAEL)
    today = local.date()
    if is_rest_day(today):
        first = today
    elif is_rest_day(today + timedelta(days=1)) and local >= sunset(today) - timedelta(minutes=stop_before):
        first = today + timedelta(days=1)
    else:
        return None
    while is_rest_day(first - timedelta(days=1)):
        first -= timedelta(days=1)
    last = first
    while is_rest_day(last + timedelta(days=1)):
        last += timedelta(days=1)
    start = sunset(first - timedelta(days=1)) - timedelta(minutes=stop_before)
    end = sunset(last) + timedelta(minutes=resume_after)
    if not start <= local < end:
        return None
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    names = [h for h in map(holiday, days) if h]
    return QuietWindow(start, end, names[0] if names else "שבת")


def is_shabbat(moment: datetime, stop_before: int = STOP_BEFORE_MINUTES,
               resume_after: int = RESUME_AFTER_MINUTES) -> bool:
    return quiet_window(moment, stop_before, resume_after) is not None
