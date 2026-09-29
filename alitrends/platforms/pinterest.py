"""Pinterest (API v5): one connected account, one target per board.

The account is connected once from the panel (OAuth). Tokens live in the database: the access token
lasts about 30 days and is refreshed automatically with the refresh token. PINTEREST_APP_ID and
PINTEREST_APP_SECRET (from developers.pinterest.com) stay in .env.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable
from urllib.parse import urlencode

import requests

from ..aliexpress import Product
from ..config import Market, Target
from .base import KeyValue, Platform, PublishError, TokenInfo
from .telegram import jpeg_url

log = logging.getLogger(__name__)

API_URL = "https://api.pinterest.com/v5"
AUTHORIZE_URL = "https://www.pinterest.com/oauth/"
SCOPES = "boards:read,pins:read,pins:write,user_accounts:read"
REFRESH_WHEN_LEFT = timedelta(days=2)
TITLE_LIMIT = 100
DESCRIPTION_LIMIT = 500

KEYS = ("access_token", "access_expires_at", "refresh_token", "refresh_expires_at", "username")


def _parse(stamp: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp) if stamp else None
    except ValueError:
        return None


class PinterestPlatform(Platform):
    name = "pinterest"
    label = "פינטרסט"
    target_hint = "שם הלוח (Board) או המזהה שלו. קודם מחברים חשבון פינטרסט בדף ההגדרות"

    def __init__(self, app_id: str | None, app_secret: str | None, kv: KeyValue,
                 session: requests.Session | None = None):
        self.app_id = app_id
        self.app_secret = app_secret
        self._kv = kv
        self._session = session or requests.Session()

    # --- account connection (OAuth) --------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.app_secret)

    def _get(self, key: str) -> str | None:
        return self._kv.get(f"pinterest.{key}")

    def _set(self, key: str, value: str | None) -> None:
        self._kv.set(f"pinterest.{key}", value or "")

    def connected_as(self) -> str | None:
        return (self._get("username") or "?") if self._get("refresh_token") else None

    def available(self) -> bool:
        return self.configured and bool(self._get("refresh_token"))

    def authorize_url(self, redirect_uri: str, state: str) -> str:
        return AUTHORIZE_URL + "?" + urlencode({
            "client_id": self.app_id, "redirect_uri": redirect_uri, "response_type": "code",
            "scope": SCOPES, "state": state})

    def connect(self, code: str, redirect_uri: str) -> str:
        """Exchange the OAuth code for tokens, store them, return the account's username."""
        self._store_tokens(self._token_request({"grant_type": "authorization_code", "code": code,
                                                "redirect_uri": redirect_uri}))
        username = self._request("GET", "/user_account").get("username", "")
        self._set("username", username)
        return username

    def disconnect(self) -> None:
        for key in KEYS:
            self._set(key, None)

    def _token_request(self, data: dict) -> dict:
        if not self.configured:
            raise PublishError("Pinterest: PINTEREST_APP_ID / PINTEREST_APP_SECRET are not set")
        try:
            response = self._session.post(f"{API_URL}/oauth/token", data=data,
                                          auth=(self.app_id, self.app_secret), timeout=30)
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise PublishError(f"Pinterest token: {exc}") from exc
        if response.status_code >= 400 or "access_token" not in body:
            raise PublishError(f"Pinterest token: {body.get('message') or body}")
        return body

    def _store_tokens(self, body: dict) -> None:
        now = datetime.now(timezone.utc)
        self._set("access_token", body["access_token"])
        self._set("access_expires_at", (now + timedelta(seconds=int(body.get("expires_in") or 2592000))).isoformat())
        if body.get("refresh_token"):  # present on connect, and on refresh when Pinterest rotates it
            self._set("refresh_token", body["refresh_token"])
            if body.get("refresh_token_expires_in"):
                self._set("refresh_expires_at",
                          (now + timedelta(seconds=int(body["refresh_token_expires_in"]))).isoformat())

    def _access_token(self) -> str:
        token = self._get("access_token")
        expires = _parse(self._get("access_expires_at"))
        if token and expires and expires - datetime.now(timezone.utc) > REFRESH_WHEN_LEFT:
            return token
        refresh = self._get("refresh_token")
        if not refresh:
            raise PublishError("Pinterest: no account connected (connect it in the panel settings)")
        self._store_tokens(self._token_request({"grant_type": "refresh_token", "refresh_token": refresh}))
        log.info("Pinterest: access token refreshed")
        return self._get("access_token") or ""

    def _request(self, method: str, path: str, **kwargs) -> dict:
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        try:
            response = self._session.request(method, f"{API_URL}{path}", headers=headers, timeout=60, **kwargs)
            body = response.json() if response.content else {}
        except (requests.RequestException, ValueError) as exc:
            raise PublishError(f"Pinterest {path}: {exc}") from exc
        if response.status_code >= 400:
            message = body.get("message") if isinstance(body, dict) else body
            raise PublishError(f"Pinterest {path}: {message or response.status_code}")
        return body

    # --- Platform ----------------------------------------------------------------

    def prepare(self, targets: Iterable[Target]) -> None:
        self._access_token()  # refresh early so an expired token shows up once per cycle, not per pin

    def publish(self, target: Target, product: Product, text: str, market: Market) -> str:
        # render() puts the headline on the first line; it becomes the pin title, the rest its description.
        title, _, rest = text.partition("\n")
        pin = self._request("POST", "/pins", json={
            "board_id": target.target_id,
            "title": title.strip()[:TITLE_LIMIT],
            "description": rest.strip()[:DESCRIPTION_LIMIT],
            "link": product.promotion_link,
            "alt_text": product.title[:500],
            "media_source": {"source_type": "image_url", "url": jpeg_url(product.image_url)},
        })
        if not pin.get("id"):
            raise PublishError(f"Pinterest {target.target_id}: unexpected response {pin}")
        return str(pin["id"])

    def followers(self, target: Target) -> int | None:
        return self._request("GET", f"/boards/{target.target_id}").get("follower_count")

    def token_info(self, targets: Iterable[Target]) -> list[TokenInfo]:
        name = f"פינטרסט @{self._get('username') or '?'}"
        if not self.available():
            return [TokenInfo("פינטרסט", False, message="לא מחובר")]
        try:
            self._request("GET", "/user_account")
        except PublishError as exc:
            return [TokenInfo(name, False, _parse(self._get("refresh_expires_at")), str(exc))]
        # The access token renews itself; what eventually needs a reconnect is the refresh token.
        return [TokenInfo(name, True, _parse(self._get("refresh_expires_at")))]

    def boards(self) -> list[dict]:
        boards, bookmark = [], None
        for _ in range(10):
            params = {"page_size": 100, **({"bookmark": bookmark} if bookmark else {})}
            page = self._request("GET", "/boards", params=params)
            boards.extend(page.get("items", []))
            bookmark = page.get("bookmark")
            if not bookmark:
                break
        return boards

    def resolve_target(self, target_id: str, secret: str | None) -> tuple[str, str]:
        if not self.available():
            raise PublishError("קודם צריך לחבר חשבון פינטרסט בדף ההגדרות")
        boards = self.boards()
        wanted = target_id.strip().lower()
        for board in boards:
            if wanted in (str(board.get("id")), (board.get("name") or "").lower()):
                return str(board["id"]), board.get("name", "")
        known = ", ".join(b.get("name", "") for b in boards) or "אין"
        raise PublishError(f"לא מצאתי את הלוח {target_id}. לוחות זמינים: {known}")
