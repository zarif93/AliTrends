from datetime import datetime, timezone

import alitrends.runner as runner
from alitrends.config import Tuning
from alitrends.runner import Bot
from alitrends.schedule import ISRAEL
from alitrends.storage import Storage


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def notify_admin(self, text, silent=False):
        self.sent.append(text)


def make_bot(tmp_path):
    bot = Bot.__new__(Bot)  # skip building real API clients
    bot.tuning = Tuning(daily_report_hour=21, backup_enabled=False)
    bot.dry_run = False
    bot.storage = Storage(str(tmp_path / "t.db"))
    bot.telegram = FakeTelegram()
    bot._daily_status = lambda: None
    bot._sync_commissions = lambda: 0
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
    bot.storage.record_publication("Hebrew/main", "threads", "p", "t", "1", "ILS")
    bot.storage.record_error("x", "boom")
    bot.storage.record_error("y", "bang")
    text = bot.daily_report_text(datetime.now(ISRAEL))
    assert "פורסמו ב-24 השעות האחרונות: 3" in text and "טלגרם 1 · פייסבוק 1 · Threads 1" in text
    assert "Hebrew 3" in text and "שגיאות: 2" in text


def test_empty_day_warns(tmp_path):
    text = make_bot(tmp_path).daily_report_text(datetime.now(ISRAEL))
    assert text.startswith("⚠️") and "journalctl" in text


def test_report_sent_once_per_day(tmp_path, monkeypatch):
    bot = make_bot(tmp_path)

    def at(day, hour, minute):
        monkeypatch.setattr(runner, "now_israel", lambda: datetime(2026, 9, day, hour, minute, tzinfo=ISRAEL))

    at(27, 20, 59)
    bot._maybe_daily_tasks()
    assert bot.telegram.sent == []            # before the report hour

    at(27, 21, 5)
    bot._maybe_daily_tasks()
    bot._maybe_daily_tasks()
    assert len(bot.telegram.sent) == 1        # once, not twice

    at(28, 21, 0)
    bot._maybe_daily_tasks()
    assert len(bot.telegram.sent) == 2        # again the next day


def test_failing_daily_task_does_not_block_others(tmp_path, monkeypatch):
    bot = make_bot(tmp_path)
    monkeypatch.setattr(runner, "now_israel", lambda: datetime(2026, 9, 27, 22, 0, tzinfo=ISRAEL))

    def broken():
        raise RuntimeError("API down")

    bot._daily_status = broken
    bot._maybe_daily_tasks()
    assert len(bot.telegram.sent) == 1        # the report still went out
    assert bot.storage.recent_errors()[0]["message"] == "API down"
    assert bot.storage.get_meta("status_date") == "2026-09-27"  # not retried every minute
