"""Telegram and Facebook publishing. Each publish returns the platform's message/post id or raises."""
from __future__ import annotations

import logging
import time

import requests
import telebot
from telebot.apihelper import ApiTelegramException
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

log = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com/v22.0"


class PublishError(RuntimeError):
    pass


def _retry_after(exc: ApiTelegramException) -> int:
    params = (exc.result_json or {}).get("parameters") or {}
    return int(params.get("retry_after") or 5)


# Telegram's replies when it can't use a photo URL (AliExpress often serves WebP or huge originals).
_BAD_PHOTO_HINTS = ("web page content", "http url", "image_process", "photo_invalid", "failed to get")
MAX_RATE_LIMIT_WAIT = 60


def jpeg_url(image_url: str) -> str:
    """AliExpress CDN serves a resized JPEG when the name gets a size suffix (and Accept asks for JPEG)."""
    return image_url if "_960x960" in image_url else image_url + "_960x960.jpg"


def download_jpeg(image_url: str, session: requests.Session | None = None) -> bytes:
    response = (session or requests).get(jpeg_url(image_url), headers={"Accept": "image/jpeg"}, timeout=30)
    response.raise_for_status()
    if not response.headers.get("content-type", "").startswith("image/"):
        raise ValueError(f"not an image: {response.headers.get('content-type')}")
    return response.content


class TelegramPublisher:
    def __init__(self, token: str, admin_chat_id: str, bot: telebot.TeleBot | None = None,
                 fetch_image=download_jpeg, sleep=time.sleep):
        self._bot = bot or telebot.TeleBot(token, threaded=False)
        self._admin = admin_chat_id
        self._fetch_image = fetch_image
        self._sleep = sleep

    def publish(self, chat_id: str, image_url: str, caption: str, link: str, button: str) -> str:
        """Send by URL; on a rate limit wait and retry, on a rejected photo upload a JPEG ourselves."""
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton(text=button, url=link))
        photo: str | bytes = image_url

        for attempt in range(3):
            try:
                message = self._bot.send_photo(
                    chat_id=chat_id, photo=photo, caption=caption,
                    reply_markup=keyboard, disable_notification=True,
                )
                return str(message.message_id)
            except ApiTelegramException as exc:
                description = (exc.description or "").lower()
                if exc.error_code == 429 and attempt < 2:
                    wait = min(_retry_after(exc), MAX_RATE_LIMIT_WAIT)
                    log.warning("Telegram %s rate limited, waiting %ss", chat_id, wait)
                    self._sleep(wait + 1)
                    continue
                if exc.error_code == 400 and isinstance(photo, str) and any(h in description for h in _BAD_PHOTO_HINTS):
                    log.warning("Telegram %s rejected photo URL (%s), uploading JPEG", chat_id, exc.description)
                    try:
                        photo = self._fetch_image(image_url)
                    except (requests.RequestException, ValueError) as fetch_exc:
                        raise PublishError(f"Telegram {chat_id}: photo download failed: {fetch_exc}") from exc
                    continue
                raise PublishError(f"Telegram {chat_id}: {exc}") from exc
            except Exception as exc:  # network errors and other telebot exception types
                raise PublishError(f"Telegram {chat_id}: {exc}") from exc
        raise PublishError(f"Telegram {chat_id}: gave up after retries")

    def notify_admin(self, text: str, silent: bool = False) -> None:
        """Best effort — an admin alert must never take the bot down."""
        try:
            self._bot.send_message(self._admin, text[:4000], disable_notification=silent)
        except Exception as exc:
            log.error("Admin notification failed: %s", exc)


class FacebookPublisher:
    def __init__(self, user_token: str, session: requests.Session | None = None):
        self._user_token = user_token
        self._session = session or requests.Session()
        self._page_tokens: dict[str, str] = {}

    def refresh_tokens(self) -> None:
        """Page tokens come from the user token; refreshed each cycle so expiry shows up quickly."""
        data = self._get(f"{GRAPH_URL}/me/accounts",
                         {"fields": "access_token,name,id", "limit": 100, "access_token": self._user_token})
        self._page_tokens = {page["id"]: page["access_token"] for page in data.get("data", [])}
        log.info("Facebook: %d page tokens loaded", len(self._page_tokens))

    def publish(self, page_id: str, image_url: str, message: str) -> str:
        token = self._page_tokens.get(page_id)
        if not token:
            raise PublishError(f"Facebook {page_id}: no page token (is the page connected to FACE_TOKEN?)")
        try:
            response = self._session.post(
                f"{GRAPH_URL}/{page_id}/photos",
                data={"message": message, "url": image_url, "access_token": token},
                timeout=60,
            )
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise PublishError(f"Facebook {page_id}: {exc}") from exc
        if "error" in data:
            raise PublishError(f"Facebook {page_id}: {data['error'].get('message')}")
        post_id = data.get("post_id") or data.get("id")
        if not post_id:
            raise PublishError(f"Facebook {page_id}: unexpected response {data}")
        return str(post_id)

    def _get(self, url: str, params: dict) -> dict:
        try:
            data = self._session.get(url, params=params, timeout=30).json()
        except (requests.RequestException, ValueError) as exc:
            raise PublishError(f"Facebook API: {exc}") from exc
        if "error" in data:
            raise PublishError(f"Facebook API: {data['error'].get('message')}")
        return data
