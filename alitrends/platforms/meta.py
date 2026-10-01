"""Facebook pages and Instagram business accounts, both driven by the one user token in FACE_TOKEN.

The user token lists the pages it manages (/me/accounts); each page has its own token, and an Instagram
business account linked to a page is published with that page's token.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Iterable

import requests

from ..aliexpress import Product
from ..config import Market, Target
from .base import Platform, PublishError, TokenInfo, graph_json
from .telegram import jpeg_url

log = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com/v22.0"


class MetaAccounts:
    def __init__(self, user_token: str | None, session: requests.Session | None = None):
        self.user_token = user_token
        self.session = session or requests.Session()
        self.page_tokens: dict[str, str] = {}
        self.page_names: dict[str, str] = {}
        self.instagram: dict[str, tuple[str, str]] = {}  # ig id -> (page token, username)
        self._loaded = False

    def refresh(self) -> None:
        """Page tokens come from the user token; refreshed each cycle so expiry shows up quickly."""
        if not self.user_token:
            raise PublishError("Facebook: FACE_TOKEN is not set")
        data = graph_json(self.session, "GET", f"{GRAPH_URL}/me/accounts", "Facebook API", params={
            "fields": "access_token,name,id,instagram_business_account{id,username}",
            "limit": 100, "access_token": self.user_token,
        })
        self.page_tokens, self.page_names, self.instagram = {}, {}, {}
        for page in data.get("data", []):
            self.page_tokens[page["id"]] = page["access_token"]
            self.page_names[page["id"]] = page.get("name", "")
            ig = page.get("instagram_business_account")
            if ig:
                self.instagram[ig["id"]] = (page["access_token"], ig.get("username", ""))
        self._loaded = True
        log.info("Meta: %d page tokens, %d Instagram accounts", len(self.page_tokens), len(self.instagram))

    def ensure(self) -> None:
        if not self._loaded:
            self.refresh()

    def token_info(self) -> TokenInfo:
        name = "פייסבוק/אינסטגרם (FACE_TOKEN)"
        if not self.user_token:
            return TokenInfo(name, False, message="לא מוגדר")
        # Validity is decided by a real call: debug_token refuses some token types (system users, page
        # tokens) when asked with the token itself, which says nothing about whether the token works.
        try:
            me = graph_json(self.session, "GET", f"{GRAPH_URL}/me", "Facebook API",
                            params={"fields": "id,name", "access_token": self.user_token})
        except PublishError as exc:
            return TokenInfo(name, False, message=str(exc))
        expires, message = None, me.get("name", "")
        try:
            data = graph_json(self.session, "GET", f"{GRAPH_URL}/debug_token", "Facebook API", params={
                "input_token": self.user_token, "access_token": self.user_token}).get("data", {})
            if data.get("expires_at"):  # 0 = never expires
                expires = datetime.fromtimestamp(data["expires_at"], timezone.utc)
            else:
                message += " · ללא תאריך תפוגה"
        except PublishError:
            message += " · תאריך תפוגה לא ידוע"
        return TokenInfo(name, True, expires, message=message)


class FacebookPlatform(Platform):
    name = "facebook"
    label = "פייסבוק"
    target_hint = "מזהה העמוד (Page ID). העמוד חייב להיות מנוהל על ידי בעל FACE_TOKEN"

    def __init__(self, accounts: MetaAccounts):
        self.accounts = accounts

    def available(self) -> bool:
        return bool(self.accounts.user_token)

    def prepare(self, targets: Iterable[Target]) -> None:
        self.accounts.refresh()

    def _token(self, page_id: str) -> str:
        self.accounts.ensure()
        token = self.accounts.page_tokens.get(page_id)
        if not token:
            raise PublishError(f"Facebook {page_id}: no page token (is the page connected to FACE_TOKEN?)")
        return token

    def publish(self, target: Target, product: Product, text: str, market: Market) -> str:
        page_id = target.target_id
        data = graph_json(self.accounts.session, "POST", f"{GRAPH_URL}/{page_id}/photos", f"Facebook {page_id}",
                          data={"message": text, "url": product.image_url, "access_token": self._token(page_id)})
        post_id = data.get("post_id") or data.get("id")
        if not post_id:
            raise PublishError(f"Facebook {page_id}: unexpected response {data}")
        return str(post_id)

    def followers(self, target: Target) -> int | None:
        data = graph_json(self.accounts.session, "GET", f"{GRAPH_URL}/{target.target_id}", "Facebook API",
                          params={"fields": "followers_count", "access_token": self._token(target.target_id)})
        return data.get("followers_count")

    def token_info(self, targets: Iterable[Target]) -> list[TokenInfo]:
        return [self.accounts.token_info()]

    def resolve_target(self, target_id: str, secret: str | None) -> tuple[str, str]:
        self.accounts.refresh()
        if target_id not in self.accounts.page_tokens:
            known = ", ".join(f"{n} ({i})" for i, n in self.accounts.page_names.items()) or "אין"
            raise PublishError(f"העמוד {target_id} לא מנוהל על ידי FACE_TOKEN. עמודים זמינים: {known}")
        return target_id, self.accounts.page_names.get(target_id, "")


class InstagramPlatform(Platform):
    name = "instagram"
    label = "אינסטגרם"
    target_hint = "מזהה חשבון אינסטגרם עסקי או @username. החשבון חייב להיות מחובר לעמוד פייסבוק של FACE_TOKEN"

    def __init__(self, accounts: MetaAccounts, sleep=time.sleep):
        self.accounts = accounts
        self._sleep = sleep

    def available(self) -> bool:
        return bool(self.accounts.user_token)

    def prepare(self, targets: Iterable[Target]) -> None:
        self.accounts.refresh()  # cheap, and FacebookPlatform may not have run this cycle

    def _token(self, ig_id: str) -> str:
        self.accounts.ensure()
        entry = self.accounts.instagram.get(ig_id)
        if not entry:
            raise PublishError(f"Instagram {ig_id}: account not linked to a page of FACE_TOKEN")
        return entry[0]

    def publish(self, target: Target, product: Product, text: str, market: Market) -> str:
        ig_id = target.target_id
        token = self._token(ig_id)
        prefix = f"Instagram {ig_id}"
        session = self.accounts.session
        # Instagram only accepts JPEG, so use the CDN's resized JPEG variant.
        container = graph_json(session, "POST", f"{GRAPH_URL}/{ig_id}/media", prefix,
                               data={"image_url": jpeg_url(product.image_url), "caption": text,
                                     "access_token": token})
        creation_id = container.get("id")
        if not creation_id:
            raise PublishError(f"{prefix}: no container id in {container}")
        self._wait_ready(creation_id, token, prefix)
        published = graph_json(session, "POST", f"{GRAPH_URL}/{ig_id}/media_publish", prefix,
                               data={"creation_id": creation_id, "access_token": token})
        if not published.get("id"):
            raise PublishError(f"{prefix}: unexpected response {published}")
        return str(published["id"])

    def _wait_ready(self, creation_id: str, token: str, prefix: str, attempts: int = 10) -> None:
        for _ in range(attempts):
            status = graph_json(self.accounts.session, "GET", f"{GRAPH_URL}/{creation_id}", prefix,
                                params={"fields": "status_code", "access_token": token}).get("status_code")
            if status in (None, "FINISHED", "PUBLISHED"):
                return
            if status in ("ERROR", "EXPIRED"):
                raise PublishError(f"{prefix}: media container {status}")
            self._sleep(3)
        raise PublishError(f"{prefix}: media container not ready in time")

    def followers(self, target: Target) -> int | None:
        data = graph_json(self.accounts.session, "GET", f"{GRAPH_URL}/{target.target_id}", "Instagram API",
                          params={"fields": "followers_count", "access_token": self._token(target.target_id)})
        return data.get("followers_count")

    def token_info(self, targets: Iterable[Target]) -> list[TokenInfo]:
        return []  # same token as Facebook, reported there

    def resolve_target(self, target_id: str, secret: str | None) -> tuple[str, str]:
        self.accounts.refresh()
        wanted = target_id.lstrip("@").lower()
        for ig_id, (_, username) in self.accounts.instagram.items():
            if target_id == ig_id or wanted == username.lower():
                return ig_id, f"@{username}"
        known = ", ".join(f"@{u} ({i})" for i, (_, u) in self.accounts.instagram.items()) or "אין"
        raise PublishError(f"חשבון האינסטגרם {target_id} לא מחובר לעמוד של FACE_TOKEN. זמינים: {known}")
