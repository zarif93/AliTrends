from datetime import date, datetime, timedelta, timezone

import pytest

from alitrends.schedule import ISRAEL, holiday, is_shabbat, quiet_window, sunset


def il(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=ISRAEL)


@pytest.mark.parametrize("day,expected", [
    (date(2026, 6, 21), "19:47"),   # longest day
    (date(2026, 12, 21), "16:39"),  # shortest day
])
def test_sunset_in_jerusalem(day, expected):
    assert sunset(day).strftime("%H:%M") == expected


def test_holidays_kept_in_israel():
    assert holiday(date(2026, 9, 12)) == "ראש השנה"
    assert holiday(date(2026, 9, 21)) == "יום כיפור"
    assert holiday(date(2026, 10, 3)) == "שמיני עצרת"
    assert holiday(date(2027, 4, 22)) == "פסח"
    assert holiday(date(2026, 9, 27)) is None  # chol hamoed is a weekday
    assert holiday(date(2027, 4, 23)) is None  # second day of Pesach is kept only abroad


@pytest.mark.parametrize("moment,expected", [
    (il(2026, 12, 25, 14, 30), False),  # winter Friday: sunset 16:41, stop at 15:41
    (il(2026, 12, 25, 15, 45), True),
    (il(2026, 12, 26, 17, 30), True),
    (il(2026, 12, 26, 17, 50), False),  # winter Saturday: sunset 16:41, back at 17:41 (not 21:30)
    (il(2026, 6, 26, 18, 30), False),   # summer Friday: sunset 19:49, still posting
    (il(2026, 6, 27, 20, 40), True),    # summer Saturday night, before sunset + 60
    (il(2026, 9, 20, 17, 0), False),    # Sunday before Yom Kippur: sunset 18:40
    (il(2026, 9, 20, 17, 45), True),    # erev Yom Kippur
    (il(2026, 9, 21, 12, 0), True),     # Yom Kippur on a Monday
    (il(2026, 9, 22, 10, 0), False),
])
def test_window(moment, expected):
    assert is_shabbat(moment) is expected


def test_rosh_hashana_after_shabbat_is_one_window():
    window = quiet_window(il(2026, 9, 12, 23, 0))  # Saturday night, still Rosh Hashana
    assert window.name == "ראש השנה"
    assert window.start == sunset(date(2026, 9, 11)) - timedelta(minutes=60)
    assert window.end == sunset(date(2026, 9, 13)) + timedelta(minutes=60)


def test_margins_are_configurable():
    eve = sunset(date(2026, 12, 25))
    assert not is_shabbat(eve - timedelta(minutes=30), stop_before=20)
    assert is_shabbat(eve - timedelta(minutes=10), stop_before=20)
    end = sunset(date(2026, 12, 26))
    assert is_shabbat(end + timedelta(minutes=70), resume_after=72)
    assert not is_shabbat(end + timedelta(minutes=73), resume_after=72)


def test_server_timezone_does_not_matter():
    # 16:00 UTC on a winter Friday is 18:00 in Israel, after sunset.
    assert is_shabbat(datetime(2026, 12, 25, 16, 0, tzinfo=timezone.utc))
