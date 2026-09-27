from datetime import datetime, timezone

import alitrends.runner as runner
from alitrends.runner import Bot
from alitrends.schedule import ISRAEL
from alitrends.storage import Storage


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def notify_admin(self, text, silent=False):
        self.sent.append(text)


class FakeSettings:
    daily_report_hour = 21


def make_bot(tmp_path):
    bot = Bot.__new__(Bot)  # skip building real API clients
    bot.settings = FakeSettings()
    bot.dry_run = False
    bot.storage = Storage(str(tmp_path / "t.db"))
    bot.telegram = FakeTelegram()
    bot._errors_since_report = 2
    return bot


def test_language_stats(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    for key in ("Hebrew/main", "Hebrew/main", "English/Toys & Kids"):
        s.record_publication(key, "telegram", "p", "m", "1", "USD")
    since = datetime(2000, 1, 1, tzinfo=timezone.utc)
    assert s.stats_by_language_since(since) == {"Hebrew": 2, "English": 1}
    s.close()


def test_report_text(tmp_path):
    bot = make_bot(tmp_path)
    bot.storage.record_publication("Hebrew/main", "telegram", "p", "m", "1", "ILS")
    bot.storage.record_publication("Hebrew/main", "facebook", "p", "f", "1", "ILS")
    text = bot.daily_report_text(datetime.now(ISRAEL))
    assert "פורסמו ב-24 השעות האחרונות: 2" in text and "טלגרם 1 · פייסבוק 1" in text
    assert "Hebrew 2" in text and "שגיאות: 2" in text


def test_empty_day_warns(tmp_path):
    text = make_bot(tmp_path).daily_report_text(datetime.now(ISRAEL))
    assert text.startswith("⚠️") and "journalctl" in text


def test_report_sent_once_per_day(tmp_path, monkeypatch):
    bot = make_bot(tmp_path)

    def at(day, hour, minute):
        monkeypatch.setattr(runner, "now_israel", lambda: datetime(2026, 9, day, hour, minute, tzinfo=ISRAEL))

    at(27, 20, 59)
    bot._maybe_daily_report()
    assert bot.telegram.sent == []            # before the report hour

    at(27, 21, 5)
    bot._maybe_daily_report()
    bot._maybe_daily_report()
    assert len(bot.telegram.sent) == 1        # once, not twice
    assert bot._errors_since_report == 0

    at(28, 21, 0)
    bot._maybe_daily_report()
    assert len(bot.telegram.sent) == 2        # again the next day
