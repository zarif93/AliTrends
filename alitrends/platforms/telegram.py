"""Telegram: channel posts (photo + caption + link button) and admin messages."""
from __future__ import annotations

import logging
import time
from typing import Iterable

import requests
import telebot
from telebot.apihelper import ApiTelegramException
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..aliexpress import Product
from ..config import Market, Target
from ..render import button_text
from .base import Platform, PublishError, TokenInfo

log = logging.getLogger(__name__)


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
    """Low-level Bot API client: channel photo posts with retries, and messages to the admin chat."""

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

    def send_admin_strict(self, text: str) -> None:
        """For login codes: the caller must know if the message did not go out."""
        try:
            self._bot.send_message(self._admin, text)
        except Exception as exc:
            raise PublishError(f"Telegram admin: {exc}") from exc

    def send_admin_document(self, data: bytes, filename: str, caption: str = "") -> None:
        try:
            self._bot.send_document(self._admin, (filename, data), caption=caption[:1000],
                                    disable_notification=True)
        except Exception as exc:
            raise PublishError(f"Telegram backup: {exc}") from exc

    def member_count(self, chat_id: str) -> int:
        return int(self._bot.get_chat_member_count(chat_id))

    def me(self) -> str:
        return self._bot.get_me().username


class TelegramPlatform(Platform):
    name = "telegram"
    label = "טלגרם"
    target_hint = "מזהה צ'אט, למשל -1001234567890 או @channelname (הבוט חייב להיות מנהל בערוץ)"

    def __init__(self, client: TelegramPublisher):
        self.client = client

    def publish(self, target: Target, product: Product, text: str, market: Market) -> str:
        return self.client.publish(target.target_id, product.image_url, text, product.promotion_link,
                                   button_text(market))

    def followers(self, target: Target) -> int | None:
        return self.client.member_count(target.target_id)

    def token_info(self, targets: Iterable[Target]) -> list[TokenInfo]:
        try:
            return [TokenInfo("טלגרם (BOT_TOKEN)", True, message=f"@{self.client.me()}")]
        except Exception as exc:
            return [TokenInfo("טלגרם (BOT_TOKEN)", False, message=str(exc))]

    def resolve_target(self, target_id: str, secret: str | None) -> tuple[str, str]:
        try:
            chat = self.client._bot.get_chat(target_id)
        except Exception as exc:
            raise PublishError(f"טלגרם לא מכיר את {target_id}: {exc}") from exc
        return str(chat.id), chat.title or chat.username or ""
