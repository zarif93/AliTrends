from datetime import datetime, timezone

import pytest

from alitrends.schedule import ISRAEL, is_shabbat, resume_time


def il(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=ISRAEL)


@pytest.mark.parametrize("moment,expected", [
    (il(2026, 9, 25, 13, 59), False),  # Friday before the window
    (il(2026, 9, 25, 14, 0), True),    # Friday, window starts
    (il(2026, 9, 26, 12, 0), True),    # Saturday
    (il(2026, 9, 26, 21, 29), True),
    (il(2026, 9, 26, 21, 30), False),  # Saturday night, window ends
    (il(2026, 9, 27, 10, 0), False),   # Sunday
])
def test_window(moment, expected):
    assert is_shabbat(moment) is expected


def test_server_timezone_does_not_matter():
    # 12:00 UTC on Friday is 15:00 in Israel (summer time) -> inside the window.
    assert is_shabbat(datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc))


def test_resume_time_from_friday_and_saturday():
    assert resume_time(il(2026, 9, 25, 16, 0)) == il(2026, 9, 26, 21, 30)
    assert resume_time(il(2026, 9, 26, 9, 0)) == il(2026, 9, 26, 21, 30)
