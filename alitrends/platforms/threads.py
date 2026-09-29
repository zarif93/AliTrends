"""Threads (graph.threads.net). Each Threads account has its own long-lived token, stored on the target.

Long-lived tokens last 60 days and can be refreshed once they are a day old; the bot refreshes them
about weekly and saves the new token back to the database.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Iterable

import requests

from ..aliexpress import Product
from ..config import Market, Target
from .base import Platform, PublishError, SaveSecret, TokenInfo, graph_json
from .telegram import jpeg_url

log = logging.getLogger(__name__)

THREADS_URL = "https://graph.threads.net/v1.0"
REFRESH_URL = "https://graph.threads.net/refresh_access_token"
TOKEN_LIFETIME = timedelta(days=60)
REFRESH_WHEN_LEFT = timedelta(days=53)  # i.e. about a week after the last refresh


def _parse(stamp: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp) if stamp else None
    except ValueError:
        return None


class ThreadsPlatform(Platform):
    name = "threads"
    label = "Threads"
    target_hint = "הדביקו טוקן Threads ארוך-טווח; המזהה והשם יתמלאו לבד"
    needs_target_secret = True

    def __init__(self, save_secret: SaveSecret, session: requests.Session | None = None, sleep=time.sleep):
        self._save_secret = save_secret
        self._session = session or requests.Session()
        self._sleep = sleep

    def _secret(self, target: Target) -> str:
        if not target.secret:
            raise PublishError(f"Threads {target.target_id}: no token set for this target")
        return target.secret

    def prepare(self, targets: Iterable[Target]) -> None:
        now = datetime.now(timezone.utc)
        for target in targets:
            if not target.secret:
                continue
            expires = _parse(target.secret_expires_at)
            if expires and expires - now > REFRESH_WHEN_LEFT:
                continue
            try:
                self.refresh(target)
            except PublishError as exc:
                # A token younger than 24h can't be refreshed yet; that's fine, try again next cycle.
                log.warning("%s", exc)

    def refresh(self, target: Target) -> None:
        data = graph_json(self._session, "GET", REFRESH_URL, f"Threads {target.target_id} token refresh",
                          params={"grant_type": "th_refresh_token", "access_token": self._secret(target)})
        token = data.get("access_token")
        if not token:
            raise PublishError(f"Threads {target.target_id} token refresh: unexpected response {data}")
        lifetime = timedelta(seconds=int(data.get("expires_in") or TOKEN_LIFETIME.total_seconds()))
        self._save_secret(target.id, token, datetime.now(timezone.utc) + lifetime)
        log.info("Threads %s: token refreshed", target.target_id)

    def publish(self, target: Target, product: Product, text: str, market: Market) -> str:
        user_id, token = target.target_id, self._secret(target)
        prefix = f"Threads {user_id}"
        container = graph_json(self._session, "POST", f"{THREADS_URL}/{user_id}/threads", prefix, data={
            "media_type": "IMAGE", "image_url": jpeg_url(product.image_url), "text": text, "access_token": token})
        creation_id = container.get("id")
        if not creation_id:
            raise PublishError(f"{prefix}: no container id in {container}")
        self._wait_ready(creation_id, token, prefix)
        published = graph_json(self._session, "POST", f"{THREADS_URL}/{user_id}/threads_publish", prefix,
                               data={"creation_id": creation_id, "access_token": token})
        if not published.get("id"):
            raise PublishError(f"{prefix}: unexpected response {published}")
        return str(published["id"])

    def _wait_ready(self, creation_id: str, token: str, prefix: str, attempts: int = 12) -> None:
        for _ in range(attempts):
            data = graph_json(self._session, "GET", f"{THREADS_URL}/{creation_id}", prefix,
                              params={"fields": "status,error_message", "access_token": token})
            status = data.get("status")
            if status in (None, "FINISHED", "PUBLISHED"):
                return
            if status in ("ERROR", "EXPIRED"):
                raise PublishError(f"{prefix}: container {status}: {data.get('error_message', '')}")
            self._sleep(5)
        raise PublishError(f"{prefix}: container not ready in time")

    def followers(self, target: Target) -> int | None:
        data = graph_json(self._session, "GET", f"{THREADS_URL}/{target.target_id}/threads_insights",
                          "Threads API", params={"metric": "followers_count", "access_token": self._secret(target)})
        for metric in data.get("data", []):
            if metric.get("name") == "followers_count":
                return (metric.get("total_value") or {}).get("value")
        return None

    def token_info(self, targets: Iterable[Target]) -> list[TokenInfo]:
        infos = []
        for target in targets:
            name = f"Threads {target.label or target.target_id}"
            if not target.secret:
                infos.append(TokenInfo(name, False, message="אין טוקן"))
                continue
            try:
                graph_json(self._session, "GET", f"{THREADS_URL}/me", "Threads API",
                           params={"fields": "id", "access_token": target.secret})
                infos.append(TokenInfo(name, True, _parse(target.secret_expires_at)))
            except PublishError as exc:
                infos.append(TokenInfo(name, False, _parse(target.secret_expires_at), str(exc)))
        return infos

    def resolve_target(self, target_id: str, secret: str | None) -> tuple[str, str]:
        if not secret:
            raise PublishError("ל-Threads צריך טוקן")
        me = graph_json(self._session, "GET", f"{THREADS_URL}/me", "Threads API",
                        params={"fields": "id,username", "access_token": secret})
        if target_id and target_id not in (me.get("id"), "me"):
            raise PublishError(f"הטוקן שייך לחשבון {me.get('id')}, לא ל-{target_id}")
        return str(me["id"]), f"@{me.get('username', '')}"
