from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from alitrends.aliexpress import Product
from alitrends.config import MARKETS, Target
from alitrends.platforms.base import PublishError
from alitrends.platforms.pinterest import PinterestPlatform
from alitrends.render import render

PRODUCT = Product("1", "Wireless earbuds", "https://cdn/x.jpg", "https://s.click/e/x", Decimal("10"),
                  Decimal("20"), "USD", 50, 4.8, 500, 7.0, "cat")
MARKET = MARKETS["English"]
COPY = {"headline": "🎧 Silence, anywhere", "body": "All-day battery.", "hashtags": ["#a", "#b"]}


class Response:
    def __init__(self, data, status=200):
        self.data, self.status_code, self.content = data, status, b"x"

    def json(self):
        return self.data


class FakeSession:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for (m, suffix), value in self.routes.items():
            if m == method and url.endswith(suffix):
                return value if isinstance(value, Response) else Response(value)
        raise AssertionError(f"unexpected {method} {url}")


class Memory(dict):
    def get(self, key):
        return super().get(key)

    def set(self, key, value):
        self[key] = value


def future(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def connected(routes, **state):
    kv = Memory({"pinterest.refresh_token": "r1", "pinterest.access_token": "a1",
                 "pinterest.access_expires_at": future(20), "pinterest.username": "shop", **state})
    session = FakeSession(routes)
    return PinterestPlatform("app", "secret", kv, session=session), kv, session


def test_not_available_until_connected():
    pin = PinterestPlatform("app", "secret", Memory(), session=FakeSession({}))
    assert pin.configured and not pin.available() and pin.connected_as() is None
    assert not PinterestPlatform(None, None, Memory()).configured


def test_publish_splits_title_and_description():
    pin, _, session = connected({("POST", "/pins"): {"id": "p1"}})
    text = render(PRODUCT, COPY, MARKET, "pinterest")
    assert pin.publish(Target(1, 1, "pinterest", "b1"), PRODUCT, text, MARKET) == "p1"
    body = session.calls[0][2]["json"]
    assert body["title"] == "🎧 Silence, anywhere"
    assert body["description"].startswith("All-day battery.") and "https://s.click" not in body["description"]
    assert body["link"] == "https://s.click/e/x" and body["board_id"] == "b1"
    assert body["media_source"]["url"].endswith("_960x960.jpg")
    assert session.calls[0][2]["headers"]["Authorization"] == "Bearer a1"


def test_expiring_access_token_is_refreshed_and_rotated_refresh_token_saved():
    pin, kv, session = connected({
        ("POST", "/oauth/token"): {"access_token": "a2", "expires_in": 2592000, "refresh_token": "r2"},
        ("POST", "/pins"): {"id": "p2"},
    }, **{"pinterest.access_expires_at": future(1)})
    pin.publish(Target(1, 1, "pinterest", "b1"), PRODUCT, "t\nd", MARKET)
    assert kv["pinterest.access_token"] == "a2" and kv["pinterest.refresh_token"] == "r2"
    assert session.calls[0][2]["data"] == {"grant_type": "refresh_token", "refresh_token": "r1"}
    assert session.calls[0][2]["auth"] == ("app", "secret")


def test_api_error_becomes_publish_error():
    pin, _, _ = connected({("POST", "/pins"): Response({"code": 3, "message": "Board not found"}, 404)})
    with pytest.raises(PublishError, match="Board not found"):
        pin.publish(Target(1, 1, "pinterest", "b1"), PRODUCT, "t\nd", MARKET)


def test_connect_stores_tokens_and_username():
    kv = Memory()
    session = FakeSession({
        ("POST", "/oauth/token"): {"access_token": "a1", "refresh_token": "r1", "expires_in": 2592000,
                                   "refresh_token_expires_in": 31536000},
        ("GET", "/user_account"): {"username": "shop"},
    })
    pin = PinterestPlatform("app", "secret", kv, session=session)
    assert pin.connect("code123", "https://panel/pinterest/callback") == "shop"
    assert pin.connected_as() == "shop" and pin.available()
    assert kv["pinterest.refresh_expires_at"]
    pin.disconnect()
    assert pin.connected_as() is None


def test_resolve_board_by_name():
    pin, _, _ = connected({("GET", "/boards"): {"items": [{"id": "111", "name": "Deals IL"}], "bookmark": None}})
    assert pin.resolve_target("deals il", None) == ("111", "Deals IL")
    with pytest.raises(PublishError, match="Deals IL"):
        pin.resolve_target("nope", None)


def test_render_fits_pinterest():
    text = render(PRODUCT, {**COPY, "body": "word " * 300}, MARKET, "pinterest")
    assert len(text) <= 500 and "https://s.click" not in text
