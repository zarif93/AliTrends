from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
from telebot.apihelper import ApiTelegramException

from alitrends.aliexpress import Product
from alitrends.config import MARKETS, Target
from alitrends.platforms.base import PublishError
from alitrends.platforms.telegram import TelegramPlatform, TelegramPublisher, jpeg_url

PRODUCT = Product("1", "t", "https://cdn/x.jpg", "https://s.click/e/x", Decimal("10"), Decimal("20"), "USD",
                  50, 4.8, 500, 7.0, "cat")


def tg_error(code, description, retry_after=None):
    body = {"ok": False, "error_code": code, "description": description}
    if retry_after is not None:
        body["parameters"] = {"retry_after": retry_after}
    return ApiTelegramException("sendPhoto", None, body)


class FakeBot:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.photos = []

    def send_photo(self, chat_id, photo, **kwargs):
        self.photos.append(photo)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(message_id=outcome)


def publisher(bot, fetched=b"JPEG", sleeps=None):
    return TelegramPublisher("t", "admin", bot=bot, fetch_image=lambda url: fetched,
                             sleep=(sleeps.append if sleeps is not None else lambda s: None))


def publish(pub):
    return pub.publish("-100", "https://cdn/x.jpg", "caption", "https://link", "button")


def test_success_by_url():
    bot = FakeBot(11)
    assert publish(publisher(bot)) == "11"
    assert bot.photos == ["https://cdn/x.jpg"]


def test_rate_limit_waits_and_retries():
    sleeps = []
    bot = FakeBot(tg_error(429, "Too Many Requests: retry after 5", retry_after=5), 12)
    assert publish(publisher(bot, sleeps=sleeps)) == "12"
    assert sleeps == [6]


def test_bad_photo_url_falls_back_to_uploaded_jpeg():
    bot = FakeBot(tg_error(400, "Bad Request: wrong type of the web page content"), 13)
    assert publish(publisher(bot)) == "13"
    assert bot.photos == ["https://cdn/x.jpg", b"JPEG"]


def test_other_errors_are_not_retried():
    bot = FakeBot(tg_error(400, "Bad Request: chat not found"))
    with pytest.raises(PublishError, match="chat not found"):
        publish(publisher(bot))
    assert len(bot.photos) == 1


def test_gives_up_after_repeated_rate_limits():
    bot = FakeBot(*(tg_error(429, "Too Many Requests", retry_after=1) for _ in range(3)))
    with pytest.raises(PublishError):
        publish(publisher(bot))
    assert len(bot.photos) == 3


def test_jpeg_url_suffix():
    assert jpeg_url("https://cdn/kf/S1.jpg") == "https://cdn/kf/S1.jpg_960x960.jpg"
    assert jpeg_url("https://cdn/kf/S1.jpg_960x960.jpg") == "https://cdn/kf/S1.jpg_960x960.jpg"


class FakeVideoBot(FakeBot):
    def __init__(self, video_outcome, *photo_outcomes):
        super().__init__(*photo_outcomes)
        self.video_outcome = video_outcome
        self.videos = []

    def send_video(self, chat_id, video, **kwargs):
        self.videos.append(video)
        if isinstance(self.video_outcome, Exception):
            raise self.video_outcome
        return SimpleNamespace(message_id=self.video_outcome)


def telegram_post(bot, video_url):
    product = replace(PRODUCT, video_url=video_url)
    return TelegramPlatform(publisher(bot)).publish(Target(1, 1, "telegram", "-100"), product, "c", MARKETS["English"])


def test_telegram_posts_video_when_there_is_one():
    bot = FakeVideoBot(21)
    assert telegram_post(bot, "https://video/x.mp4") == "21"
    assert bot.videos == ["https://video/x.mp4"] and bot.photos == []


def test_telegram_falls_back_to_photo_when_video_fails():
    bot = FakeVideoBot(tg_error(400, "Bad Request: wrong file identifier/HTTP URL specified"), 11)
    assert telegram_post(bot, "https://video/x.mp4") == "11"
    assert bot.photos == ["https://cdn/x.jpg"]


def test_telegram_without_video_posts_photo():
    bot = FakeVideoBot(21, 11)
    assert telegram_post(bot, "") == "11"
    assert bot.videos == []
