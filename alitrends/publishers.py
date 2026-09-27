"""Telegram and Facebook publishing. Each publish returns the platform's message/post id or raises."""
from __future__ import annotations

import logging

import requests
import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

log = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com/v22.0"


class PublishError(RuntimeError):
    pass


class TelegramPublisher:
    def __init__(self, token: str, admin_chat_id: str):
        self._bot = telebot.TeleBot(token, threaded=False)
        self._admin = admin_chat_id

    def publish(self, chat_id: str, image_url: str, caption: str, link: str, button: str) -> str:
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton(text=button, url=link))
        try:
            message = self._bot.send_photo(
                chat_id=chat_id, photo=image_url, caption=caption,
                reply_markup=keyboard, disable_notification=True,
            )
        except Exception as exc:  # telebot raises several unrelated exception types
            raise PublishError(f"Telegram {chat_id}: {exc}") from exc
        return str(message.message_id)

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
