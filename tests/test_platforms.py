from dataclasses import replace
from decimal import Decimal

import pytest

from alitrends.aliexpress import Product
from alitrends.config import MARKETS, Target
from alitrends.platforms.base import PublishError
from alitrends.platforms.meta import FacebookPlatform, InstagramPlatform, MetaAccounts
from alitrends.platforms.threads import ThreadsPlatform

PRODUCT = Product("1", "t", "https://cdn/x.jpg", "https://s.click/e/x", Decimal("10"), Decimal("20"), "USD",
                  50, 4.8, 500, 7.0, "cat")
MARKET = MARKETS["English"]


class Response:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


class FakeSession:
    """Answers requests by (method, url suffix) from a dict of queues."""

    def __init__(self, routes):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for (m, suffix), queue in self.routes.items():
            if m == method and url.endswith(suffix):
                return Response(queue.pop(0) if len(queue) > 1 else queue[0])
        raise AssertionError(f"unexpected {method} {url}")


ACCOUNTS = {"data": [{"id": "page1", "name": "My Page", "access_token": "ptok",
                      "instagram_business_account": {"id": "ig1", "username": "deals"}}]}


def meta(routes):
    session = FakeSession({("GET", "/me/accounts"): [ACCOUNTS], **routes})
    return MetaAccounts("utok", session), session


COMMENT = {("POST", "/comments"): [{"id": "cm1"}]}


def test_facebook_publish_uses_page_token_and_comments_the_link():
    accounts, session = meta({("POST", "/page1/photos"): [{"post_id": "page1_9"}], **COMMENT})
    fb = FacebookPlatform(accounts)
    assert fb.publish(Target(1, 1, "facebook", "page1"), PRODUCT, "text", MARKET) == "page1_9"
    photo, comment = session.calls[-2], session.calls[-1]
    assert photo[2]["data"]["access_token"] == "ptok"
    assert comment[1].endswith("/page1_9/comments")
    assert comment[2]["data"]["message"] == "🛒 View deal 👉 https://s.click/e/x"


def test_facebook_post_without_link_comment_is_removed():
    accounts, session = meta({("POST", "/page1/photos"): [{"post_id": "page1_9"}],
                              ("POST", "/comments"): [{"error": {"message": "nope"}}],
                              ("DELETE", "/page1_9"): [{"success": True}]})
    with pytest.raises(PublishError, match="post removed"):
        FacebookPlatform(accounts).publish(Target(1, 1, "facebook", "page1"), PRODUCT, "t", MARKET)
    assert session.calls[-1][0] == "DELETE"


def test_facebook_unknown_page():
    accounts, _ = meta({})
    with pytest.raises(PublishError, match="no page token"):
        FacebookPlatform(accounts).publish(Target(1, 1, "facebook", "other"), PRODUCT, "t", MARKET)


def test_instagram_container_then_publish():
    accounts, session = meta({
        ("POST", "/ig1/media"): [{"id": "c1"}],
        ("GET", "/c1"): [{"status_code": "IN_PROGRESS"}, {"status_code": "FINISHED"}],
        ("POST", "/ig1/media_publish"): [{"id": "m1"}],
    })
    ig = InstagramPlatform(accounts, sleep=lambda s: None)
    assert ig.publish(Target(1, 1, "instagram", "ig1"), PRODUCT, "caption", MARKET) == "m1"
    create = next(c for c in session.calls if c[1].endswith("/ig1/media"))
    assert create[2]["data"]["image_url"].endswith("_960x960.jpg")  # Instagram needs JPEG
    assert create[2]["data"]["access_token"] == "ptok"


def test_instagram_container_error():
    accounts, _ = meta({("POST", "/ig1/media"): [{"id": "c1"}], ("GET", "/c1"): [{"status_code": "ERROR"}]})
    with pytest.raises(PublishError, match="ERROR"):
        InstagramPlatform(accounts, sleep=lambda s: None).publish(Target(1, 1, "instagram", "ig1"), PRODUCT, "c", MARKET)


def test_instagram_resolve_by_username():
    accounts, _ = meta({})
    assert InstagramPlatform(accounts).resolve_target("@Deals", None) == ("ig1", "@deals")
    with pytest.raises(PublishError, match="זמינים"):
        InstagramPlatform(accounts).resolve_target("@nope", None)


def test_graph_error_becomes_publish_error():
    accounts, _ = meta({("POST", "/page1/photos"): [{"error": {"message": "Invalid token"}}]})
    with pytest.raises(PublishError, match="Invalid token"):
        FacebookPlatform(accounts).publish(Target(1, 1, "facebook", "page1"), PRODUCT, "t", MARKET)


def test_threads_publish_flow():
    session = FakeSession({
        ("POST", "/u1/threads"): [{"id": "c1"}],
        ("GET", "/c1"): [{"status": "FINISHED"}],
        ("POST", "/u1/threads_publish"): [{"id": "t1"}],
    })
    threads = ThreadsPlatform(lambda *a: None, session=session, sleep=lambda s: None)
    assert threads.publish(Target(1, 1, "threads", "u1", secret="tok"), PRODUCT, "hi", MARKET) == "t1"
    assert session.calls[0][2]["data"]["access_token"] == "tok"


def test_threads_needs_token():
    threads = ThreadsPlatform(lambda *a: None, session=FakeSession({}))
    with pytest.raises(PublishError, match="no token"):
        threads.publish(Target(1, 1, "threads", "u1"), PRODUCT, "hi", MARKET)


def test_threads_refreshes_old_tokens_and_saves():
    saved = []
    session = FakeSession({("GET", "/refresh_access_token"): [{"access_token": "new", "expires_in": 5184000}]})
    threads = ThreadsPlatform(lambda *a: saved.append(a), session=session)
    threads.prepare([
        Target(1, 1, "threads", "u1", secret="old"),                                     # expiry unknown
        Target(2, 1, "threads", "u2", secret="x", secret_expires_at="2999-01-01T00:00:00+00:00"),  # fresh
        Target(3, 1, "threads", "u3"),                                                   # no token
    ])
    assert [(s[0], s[1]) for s in saved] == [(1, "new")]


def test_threads_resolve_fills_id():
    session = FakeSession({("GET", "/me"): [{"id": "777", "username": "shop"}]})
    threads = ThreadsPlatform(lambda *a: None, session=session)
    assert threads.resolve_target("", "tok") == ("777", "@shop")
    with pytest.raises(PublishError):
        threads.resolve_target("123", "tok")


def test_threads_followers():
    session = FakeSession({("GET", "/u1/threads_insights"): [
        {"data": [{"name": "followers_count", "total_value": {"value": 42}}]}]})
    assert ThreadsPlatform(lambda *a: None, session=session).followers(Target(1, 1, "threads", "u1", secret="t")) == 42


def test_facebook_token_valid_even_when_debug_token_refuses():
    accounts, _ = meta({("GET", "/me"): [{"id": "1", "name": "Admin"}],
                        ("GET", "/debug_token"): [{"error": {"message": "(#100) You must provide an app access token"}}]})
    info = accounts.token_info()
    assert info.ok and info.expires_at is None and "לא ידוע" in info.message


def test_facebook_never_expiring_token():
    accounts, _ = meta({("GET", "/me"): [{"id": "1", "name": "Admin"}],
                        ("GET", "/debug_token"): [{"data": {"is_valid": True, "expires_at": 0,
                                                            "data_access_expires_at": 1700000000}}]})
    info = accounts.token_info()
    assert info.ok and info.expires_at is None and "ללא תאריך תפוגה" in info.message


def test_facebook_expiring_token_and_broken_token():
    accounts, _ = meta({("GET", "/me"): [{"id": "1"}],
                        ("GET", "/debug_token"): [{"data": {"is_valid": True, "expires_at": 4102444800}}]})
    assert accounts.token_info().expires_at.year == 2100
    broken, _ = meta({("GET", "/me"): [{"error": {"message": "Error validating access token: Session has expired"}}]})
    info = broken.token_info()
    assert not info.ok and "expired" in info.message


VIDEO_PRODUCT = replace(PRODUCT, video_url="https://video/x.mp4")


def test_facebook_posts_video_when_there_is_one():
    accounts, session = meta({("POST", "/page1/videos"): [{"id": "v1"}], **COMMENT})
    assert FacebookPlatform(accounts).publish(Target(1, 1, "facebook", "page1"), VIDEO_PRODUCT, "t", MARKET) == "v1"
    data = session.calls[-2][2]["data"]
    assert data["file_url"] == "https://video/x.mp4" and data["description"] == "t"


def test_facebook_falls_back_to_photo_when_video_fails():
    accounts, session = meta({("POST", "/page1/videos"): [{"error": {"message": "bad video"}}],
                              ("POST", "/page1/photos"): [{"post_id": "page1_9"}], **COMMENT})
    assert FacebookPlatform(accounts).publish(Target(1, 1, "facebook", "page1"), VIDEO_PRODUCT, "t", MARKET) == "page1_9"


def test_instagram_video_is_a_reel():
    accounts, session = meta({
        ("POST", "/ig1/media"): [{"id": "c1"}],
        ("GET", "/c1"): [{"status_code": "FINISHED"}],
        ("POST", "/ig1/media_publish"): [{"id": "m1"}],
    })
    ig = InstagramPlatform(accounts, sleep=lambda s: None)
    assert ig.publish(Target(1, 1, "instagram", "ig1"), VIDEO_PRODUCT, "caption", MARKET) == "m1"
    create = next(c for c in session.calls if c[1].endswith("/ig1/media"))
    assert create[2]["data"]["media_type"] == "REELS"
    assert create[2]["data"]["video_url"] == "https://video/x.mp4"


def test_instagram_falls_back_to_image_when_reel_fails():
    accounts, session = meta({
        ("POST", "/ig1/media"): [{"id": "c1"}, {"id": "c2"}],
        ("GET", "/c1"): [{"status_code": "ERROR"}],
        ("GET", "/c2"): [{"status_code": "FINISHED"}],
        ("POST", "/ig1/media_publish"): [{"id": "m2"}],
    })
    ig = InstagramPlatform(accounts, sleep=lambda s: None)
    assert ig.publish(Target(1, 1, "instagram", "ig1"), VIDEO_PRODUCT, "caption", MARKET) == "m2"
    creates = [c[2]["data"] for c in session.calls if c[1].endswith("/ig1/media")]
    assert "video_url" in creates[0] and "image_url" in creates[1]


def test_threads_posts_video():
    session = FakeSession({
        ("POST", "/u1/threads"): [{"id": "c1"}],
        ("GET", "/c1"): [{"status": "FINISHED"}],
        ("POST", "/u1/threads_publish"): [{"id": "t1"}],
    })
    threads = ThreadsPlatform(lambda *a: None, session=session, sleep=lambda s: None)
    assert threads.publish(Target(1, 1, "threads", "u1", secret="tok"), VIDEO_PRODUCT, "hi", MARKET) == "t1"
    assert session.calls[0][2]["data"]["media_type"] == "VIDEO"
