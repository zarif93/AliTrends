import re

import pytest

from alitrends.config import Secrets
from alitrends.panel import auth, create_app
from alitrends.platforms.base import Platform, PublishError
from alitrends.storage import Storage


class FakeTelegram:
    def __init__(self):
        self.codes = []

    def send_admin_strict(self, text):
        self.codes.append(re.search(r"\d{6}", text).group())

    def notify_admin(self, text, silent=False):
        pass


class FakeTelegramPlatform(Platform):
    name, label = "telegram", "טלגרם"

    def resolve_target(self, target_id, secret):
        if target_id == "bad":
            raise PublishError("chat not found")
        return "-100" + target_id, "My channel"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PANEL_COOKIE_SECURE", "0")
    secrets = Secrets("k", "s", "default_tid", "ai", "bot", "admin", None, str(tmp_path / "t.db"), str(tmp_path))
    storage = Storage(secrets.db_path)
    auth.set_credentials(storage, "admin", "correct horse battery")
    telegram = FakeTelegram()
    app = create_app(secrets, telegram=telegram, platforms={"telegram": FakeTelegramPlatform()})
    app.config["TESTING"] = True
    client = app.test_client()
    yield client, storage, telegram
    storage.close()


def csrf(client):
    client.get("/login")
    with client.session_transaction() as sess:
        return sess["csrf"]


def login(client, telegram, password="correct horse battery"):
    token = csrf(client)
    r = client.post("/login", data={"_csrf": token, "username": "admin", "password": password})
    if r.status_code != 302:
        return r
    client.get("/login/code")  # the pending-login session gets a fresh CSRF token here
    return client.post("/login/code", data={"_csrf": session_csrf(client), "code": telegram.codes[-1]})


def session_csrf(client):
    with client.session_transaction() as sess:
        return sess["csrf"]


def test_pages_require_login(env):
    client, _, _ = env
    r = client.get("/channels")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert client.get("/jobs/1.json").status_code == 401


def test_login_with_telegram_code(env):
    client, storage, telegram = env
    r = login(client, telegram)
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    assert client.get("/").status_code == 200
    assert "מצב הבוט" in client.get("/").get_data(as_text=True)


def test_wrong_password_and_lockout(env):
    client, storage, telegram = env
    for _ in range(5):
        assert login(client, telegram, password="nope").status_code == 401
    assert login(client, telegram).status_code == 429  # even the right password is refused for now
    assert telegram.codes == []


def test_wrong_code_then_restart(env):
    client, _, telegram = env
    token = csrf(client)
    client.post("/login", data={"_csrf": token, "username": "admin", "password": "correct horse battery"})
    for _ in range(4):
        with client.session_transaction() as sess:
            sess["csrf"] = "t"
        assert client.post("/login/code", data={"_csrf": "t", "code": "000000" if telegram.codes[-1] != "000000" else "111111"}).status_code == 200
    with client.session_transaction() as sess:
        sess["csrf"] = "t"
    r = client.post("/login/code", data={"_csrf": "t", "code": "000000" if telegram.codes[-1] != "000000" else "111111"})
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert client.get("/").status_code == 302


def test_post_without_csrf_is_rejected(env):
    client, _, telegram = env
    login(client, telegram)
    assert client.post("/pause", data={"paused": "1"}).status_code == 400


def test_password_change_logs_out_other_sessions(env):
    client, storage, telegram = env
    login(client, telegram)
    auth.set_credentials(storage, "admin", "another long password")  # e.g. from the CLI
    assert client.get("/").status_code == 302


def test_channel_and_target_flow(env):
    client, storage, telegram = env
    login(client, telegram)
    token = session_csrf(client)
    r = client.post("/channels/new", data={"_csrf": token, "language": "Hebrew", "category": "main"})
    assert r.status_code == 302
    cid = storage.channels()[0].id

    r = client.post(f"/channels/{cid}/targets", data={"_csrf": token, "platform": "telegram", "target_id": "bad"})
    assert storage.channel(cid).targets == ()
    client.post(f"/channels/{cid}/targets", data={"_csrf": token, "platform": "telegram", "target_id": "55"})
    target = storage.channel(cid).targets[0]
    assert (target.target_id, target.label) == ("-10055", "My channel")

    r = client.post(f"/channels/{cid}", data={"_csrf": token, "enabled": "1", "tracking_id": "bad id!",
                                              "every_n_cycles": "1"}, follow_redirects=True)
    assert "Tracking ID" in r.get_data(as_text=True) and storage.channel(cid).tracking_id is None
    client.post(f"/channels/{cid}", data={"_csrf": token, "enabled": "1", "tracking_id": "he_main",
                                          "every_n_cycles": "3", "active_from": "08:00", "active_to": "22:00"})
    channel = storage.channel(cid)
    assert (channel.tracking_id, channel.every_n_cycles, channel.active_to) == ("he_main", 3, "22:00")

    r = client.post(f"/channels/{cid}/preview", headers={"X-CSRF-Token": token})
    job_id = r.get_json()["job_id"]
    assert client.get(f"/jobs/{job_id}.json").get_json()["status"] == "pending"

    for page in ("/", "/channels", f"/channels/{cid}", "/settings", "/blacklist", "/manual", "/commissions",
                 "/followers", "/logs", "/audit", "/account"):
        assert client.get(page).status_code == 200, page


def test_settings_validation(env):
    client, storage, telegram = env
    login(client, telegram)
    token = session_csrf(client)
    page = client.get("/settings").get_data(as_text=True)
    form = {"_csrf": token}
    for name, value in re.findall(r'name="([^"]+)" value="([^"]*)"', page):
        if name != "_csrf":
            form[name] = value
    form["min_sales"] = "250"
    form["prompt.Hebrew"] = "טון קליל"
    form.pop("shabbat_enabled")  # an unchecked box is simply absent from the form
    client.post("/settings", data=form)
    tuning = storage.tuning()
    assert tuning.min_sales == 250 and tuning.prompt_extra == {"Hebrew": "טון קליל"}
    assert tuning.shabbat_enabled is False

    form["daily_report_hour"] = "99"
    client.post("/settings", data=form)
    assert storage.tuning().daily_report_hour != 99


def test_manual_post_queues_job(env):
    client, storage, telegram = env
    login(client, telegram)
    token = session_csrf(client)
    cid = storage.create_channel("Hebrew", "main")
    storage.create_target(cid, "telegram", "-1")
    client.post("/manual", data={"_csrf": token, "url": "https://www.aliexpress.com/item/1005001234567.html",
                                 "channel_ids": [str(cid)], "when": "2030-01-01T10:00"})
    job = storage.jobs(("manual_post",))[0]
    assert job["payload"] == {"url": "https://www.aliexpress.com/item/1005001234567.html",
                              "product_id": "1005001234567", "channel_ids": [cid]}
    assert job["run_at"].startswith("2030-01-01T08:00")  # 10:00 Israel (winter) = 08:00 UTC


def test_audit_restore_from_panel(env):
    client, storage, telegram = env
    login(client, telegram)
    token = session_csrf(client)
    cid = storage.create_channel("Hebrew", "main")
    storage.delete_channel(cid)
    entry = storage.audit_log()[0]
    client.post(f"/audit/{entry['id']}/restore", data={"_csrf": token})
    assert storage.channel(cid) is not None


def test_pinterest_connect_flow(tmp_path, monkeypatch):
    from urllib.parse import parse_qs, urlparse

    from alitrends.platforms.pinterest import PinterestPlatform

    class Memory(dict):
        def set(self, key, value):
            self[key] = value

    class FakePin(PinterestPlatform):
        def connect(self, code, redirect_uri):
            self.seen = (code, redirect_uri)
            self._set("refresh_token", "r")
            self._set("username", "shop")
            return "shop"

    monkeypatch.setenv("PANEL_COOKIE_SECURE", "0")
    secrets = Secrets("k", "s", "t", "ai", "bot", "admin", None, str(tmp_path / "t.db"), str(tmp_path))
    storage = Storage(secrets.db_path)
    auth.set_credentials(storage, "admin", "correct horse battery")
    telegram = FakeTelegram()
    pin = FakePin("app", "secret", Memory())
    client = create_app(secrets, telegram=telegram, platforms={"pinterest": pin}).test_client()
    login(client, telegram)

    assert "localhost/pinterest/callback" in client.get("/settings").get_data(as_text=True)
    r = client.get("/pinterest/connect")
    query = parse_qs(urlparse(r.headers["Location"]).query)
    assert r.headers["Location"].startswith("https://www.pinterest.com/oauth/") and query["client_id"] == ["app"]

    client.get("/pinterest/callback?code=c&state=wrong")
    assert pin.connected_as() is None                      # state mismatch is refused

    r = client.get("/pinterest/connect")
    state = parse_qs(urlparse(r.headers["Location"]).query)["state"][0]
    client.get(f"/pinterest/callback?code=c&state={state}")
    assert pin.connected_as() == "shop" and pin.seen == ("c", "http://localhost/pinterest/callback")
    storage.close()
