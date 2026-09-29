from types import SimpleNamespace

import pytest
from telebot.apihelper import ApiTelegramException

from alitrends.platforms.base import PublishError
from alitrends.platforms.telegram import TelegramPublisher, jpeg_url


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
