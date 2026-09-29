from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

import alitrends.runner as runner
from alitrends.aliexpress import Product
from alitrends.config import Tuning
from alitrends.platforms.base import Platform, PublishError
from alitrends.runner import Bot, CycleReport
from alitrends.storage import Storage

PRODUCT = Product("p1", "Nice thing", "https://cdn/x.jpg", "https://s.click/e/x", Decimal("10"), Decimal("20"),
                  "USD", 50, 4.8, 500, 7.0, "cat")
COPY = {"headline": "🔥 Wow", "body": "Body text.", "hashtags": ["#a", "#b"]}


class FakePlatform(Platform):
    def __init__(self, name, fail=False):
        self.name, self.label, self.fail = name, name, fail
        self.posts, self.prepared = [], []

    def prepare(self, targets):
        self.prepared.append([t.target_id for t in targets])

    def publish(self, target, product, text, market):
        if self.fail:
            raise PublishError(f"{self.name} down")
        self.posts.append((target.target_id, product.product_id, text))
        return f"{self.name}-{len(self.posts)}"


class FakeSource:
    def __init__(self, product=PRODUCT):
        self.product = product
        self.picks = []

    def pick(self, market, category, channel_key, tracking_id=None):
        self.picks.append((channel_key, tracking_id))
        return self.product

    def with_link(self, product, tracking_id=None):
        return replace(product, promotion_link=f"https://s.click/{tracking_id or 'default'}")


class FakeCopywriter:
    def copy_for(self, product, market):
        return COPY


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def notify_admin(self, text, silent=False):
        self.sent.append(text)


class FakeClient:
    def product_detail(self, product_id, **kwargs):
        return replace(PRODUCT, product_id=product_id) if product_id != "missing" else None


@pytest.fixture
def bot(tmp_path, monkeypatch):
    b = Bot.__new__(Bot)
    b.dry_run = False
    b.storage = Storage(str(tmp_path / "t.db"))
    b.tuning = Tuning(post_delay_seconds=0, shabbat_enabled=False)
    b.platforms = {"telegram": FakePlatform("telegram"), "facebook": FakePlatform("facebook", fail=True),
                   "threads": FakePlatform("threads")}
    b.source = FakeSource()
    b.copywriter = FakeCopywriter()
    b.telegram = FakeTelegram()
    b.client = FakeClient()
    b._in_jobs = False
    b._last_heartbeat = b._last_daily_check = 0.0
    b.reload = lambda: setattr(b, "channels", b.storage.channels(only_enabled=True))
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    yield b
    b.storage.close()


def add_channel(bot, language="Hebrew", category="main", **fields):
    cid = bot.storage.create_channel(language, category, **fields)
    bot.storage.create_target(cid, "telegram", f"tg-{cid}")
    bot.storage.create_target(cid, "facebook", f"fb-{cid}")
    off = bot.storage.create_target(cid, "threads", f"th-{cid}")
    bot.storage.update_target(off, enabled=False)
    return cid


def test_failure_on_one_platform_does_not_stop_others(bot):
    add_channel(bot)
    bot.reload()
    report = bot.run_cycle()
    assert report.published == 1 and len(report.errors) == 1
    assert bot.platforms["telegram"].posts[0][0] == "tg-1"
    assert bot.platforms["threads"].posts == []                  # disabled target
    assert bot.storage.stats_since(datetime(2000, 1, 1, tzinfo=timezone.utc)) == {"telegram": 1}
    assert bot.storage.recent_errors()[0]["message"] == "facebook down"
    assert bot.platforms["telegram"].prepared == [["tg-1"]]


def test_frequency_and_active_hours_skip(bot):
    add_channel(bot, every_n_cycles=2)          # id 1: due on odd cycles only (cycle 1 + id 1 = 2)
    add_channel(bot, category="Toys & Kids", active_from="00:00", active_to="00:01")
    bot.reload()
    first = bot.run_cycle()
    second = bot.run_cycle()
    assert (first.published, first.off_hours, first.not_due) == (1, 1, 0)
    assert (second.published, second.off_hours, second.not_due) == (0, 1, 1)


def test_paused_mid_cycle_stops(bot):
    add_channel(bot)
    add_channel(bot, category="Toys & Kids")
    bot.reload()
    bot.storage.set_settings({"paused": True})
    assert bot.run_cycle().published == 0


def test_tracking_id_is_passed_to_sourcing(bot):
    add_channel(bot, tracking_id="hebrew_main")
    bot.reload()
    bot.run_cycle()
    assert bot.source.picks == [("Hebrew/main", "hebrew_main")]


def test_preview_job_does_not_publish(bot):
    cid = add_channel(bot)
    job = bot.storage.add_job("preview", {"channel_id": cid})
    bot._run_jobs()
    result = bot.storage.job(job)
    assert result["status"] == "done"
    assert [p["platform"] for p in result["result"]["posts"]] == ["telegram", "facebook", "threads"]
    assert "Wow" in result["result"]["posts"][0]["text"]
    assert bot.platforms["telegram"].posts == []


def test_post_now_job(bot):
    cid = add_channel(bot)
    job = bot.storage.add_job("post_channel", {"channel_id": cid})
    bot._run_jobs()
    assert bot.storage.job(job)["result"]["published"] == 1


def test_manual_post_uses_channel_tracking(bot):
    a = add_channel(bot, tracking_id="aaa")
    b = add_channel(bot, language="English")
    job = bot.storage.add_job("manual_post", {"url": "x", "product_id": "42", "channel_ids": [a, b]})
    bot._run_jobs()
    result = bot.storage.job(job)
    assert result["status"] == "done"
    assert result["result"]["channels"]["Hebrew/main"]["published"] == 1
    links = [p[2] for p in bot.platforms["telegram"].posts]
    assert len(links) == 2
    assert bot.platforms["telegram"].posts[0][1] == "42"


def test_manual_post_all_failed(bot):
    a = add_channel(bot)
    job = bot.storage.add_job("manual_post", {"url": "x", "product_id": "missing", "channel_ids": [a]})
    bot._run_jobs()
    assert bot.storage.job(job)["status"] == "failed"


def test_publishing_jobs_wait_for_shabbat_end(bot, monkeypatch):
    cid = add_channel(bot)
    bot.tuning = Tuning(shabbat_enabled=True)
    monkeypatch.setattr(runner, "is_shabbat", lambda moment: True)
    post = bot.storage.add_job("post_channel", {"channel_id": cid})
    preview = bot.storage.add_job("preview", {"channel_id": cid})
    bot._run_jobs()
    assert bot.storage.job(post)["status"] == "pending"
    assert bot.storage.job(preview)["status"] == "done"


def test_no_live_targets_is_skipped(bot):
    cid = bot.storage.create_channel("Hebrew", "main")
    bot.reload()
    report = CycleReport()
    assert bot._process(bot.storage.channel(cid), report) is False and report.skipped == 1
