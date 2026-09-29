from datetime import datetime, timezone

import pytest

from alitrends.config import SETTING_FIELDS, Channel, Tuning, parse_setting


def ch(**kw):
    return Channel(id=kw.pop("id", 1), language=kw.pop("language", "Hebrew"), category="main", **kw)


def test_every_n_cycles_spreads_channels():
    a, b = ch(id=1, every_n_cycles=2), ch(id=2, every_n_cycles=2)
    assert [a.is_due(n) for n in range(1, 5)] == [True, False, True, False]
    assert [b.is_due(n) for n in range(1, 5)] == [False, True, False, True]
    assert all(ch(every_n_cycles=1).is_due(n) for n in range(5))


@pytest.mark.parametrize("utc_hour,expected", [(5, False), (6, True), (19, True), (20, False)])
def test_active_hours_in_market_timezone(utc_hour, expected):
    # Hebrew market = Asia/Jerusalem, UTC+3 in September: 09:00-23:00 local = 06:00-20:00 UTC.
    channel = ch(active_from="09:00", active_to="23:00")
    assert channel.in_active_hours(datetime(2026, 9, 21, utc_hour, 0, tzinfo=timezone.utc)) is expected


def test_active_hours_over_midnight():
    channel = ch(language="Portuguese", active_from="20:00", active_to="02:00")  # Sao Paulo, UTC-3
    assert channel.in_active_hours(datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc))      # 22:00 local
    assert channel.in_active_hours(datetime(2026, 9, 21, 4, 30, tzinfo=timezone.utc))     # 01:30 local
    assert not channel.in_active_hours(datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc))  # 12:00 local


def test_no_hours_means_always():
    assert ch().in_active_hours(datetime.now(timezone.utc))


def test_tuning_from_settings_ignores_bad_values():
    t = Tuning.from_settings({"min_sales": "abc", "post_delay_seconds": "15", "shabbat_enabled": "false",
                              "min_discount.Hebrew": 30, "prompt.English": "  be funny ", "unknown": 1})
    assert t.min_sales == 100 and t.post_delay_seconds == 15 and t.shabbat_enabled is False
    assert t.min_discount["Hebrew"] == 30 and t.min_discount["French"] == 0
    assert t.prompt_extra == {"English": "be funny"}


def test_parse_setting_validates_ranges():
    spec = next(f for f in SETTING_FIELDS if f.key == "daily_report_hour")
    assert parse_setting(spec, "21") == 21
    with pytest.raises(ValueError):
        parse_setting(spec, "25")
    with pytest.raises(ValueError):
        parse_setting(spec, "")
    flag = next(f for f in SETTING_FIELDS if f.key == "shabbat_enabled")
    assert parse_setting(flag, None) is False and parse_setting(flag, "1") is True
