"""Registry of social platforms. To add a network: write a Platform subclass in its own module and
add it to PLATFORM_CLASSES and build_platforms(); give it a PostStyle in render.STYLES."""
from __future__ import annotations

from ..config import Secrets
from .base import Platform, PublishError, SaveSecret, TokenInfo
from .meta import FacebookPlatform, InstagramPlatform, MetaAccounts
from .telegram import TelegramPlatform, TelegramPublisher
from .threads import ThreadsPlatform

# Order here is the display order in the panel and reports.
PLATFORM_CLASSES: dict[str, type[Platform]] = {
    cls.name: cls for cls in (TelegramPlatform, FacebookPlatform, InstagramPlatform, ThreadsPlatform)
}


def platform_label(name: str) -> str:
    cls = PLATFORM_CLASSES.get(name)
    return cls.label if cls else name


def build_platforms(secrets: Secrets, save_secret: SaveSecret,
                    telegram: TelegramPublisher | None = None) -> dict[str, Platform]:
    telegram = telegram or TelegramPublisher(secrets.telegram_token, secrets.admin_chat_id)
    meta = MetaAccounts(secrets.facebook_user_token)
    return {
        "telegram": TelegramPlatform(telegram),
        "facebook": FacebookPlatform(meta),
        "instagram": InstagramPlatform(meta),
        "threads": ThreadsPlatform(save_secret),
    }


__all__ = ["PLATFORM_CLASSES", "Platform", "PublishError", "TokenInfo", "TelegramPublisher",
           "build_platforms", "platform_label"]
