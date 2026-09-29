"""Registry of social platforms. To add a network: write a Platform subclass in its own module and
add it to PLATFORM_CLASSES and build_platforms(); give it a PostStyle in render.STYLES."""
from __future__ import annotations

from ..config import Secrets
from ..storage import DbKeyValue
from .base import KeyValue, Platform, PublishError, SaveSecret, TokenInfo
from .meta import FacebookPlatform, InstagramPlatform, MetaAccounts
from .pinterest import PinterestPlatform
from .telegram import TelegramPlatform, TelegramPublisher
from .threads import ThreadsPlatform

# Order here is the display order in the panel and reports.
PLATFORM_CLASSES: dict[str, type[Platform]] = {
    cls.name: cls for cls in (TelegramPlatform, FacebookPlatform, InstagramPlatform, ThreadsPlatform,
                                PinterestPlatform)
}


def platform_label(name: str) -> str:
    cls = PLATFORM_CLASSES.get(name)
    return cls.label if cls else name


def build_platforms(secrets: Secrets, save_secret: SaveSecret, telegram: TelegramPublisher | None = None,
                    kv: KeyValue | None = None) -> dict[str, Platform]:
    telegram = telegram or TelegramPublisher(secrets.telegram_token, secrets.admin_chat_id)
    meta = MetaAccounts(secrets.facebook_user_token)
    return {
        "telegram": TelegramPlatform(telegram),
        "facebook": FacebookPlatform(meta),
        "instagram": InstagramPlatform(meta),
        "threads": ThreadsPlatform(save_secret),
        "pinterest": PinterestPlatform(secrets.pinterest_app_id, secrets.pinterest_app_secret,
                                       kv or DbKeyValue(secrets.db_path)),
    }


__all__ = ["PLATFORM_CLASSES", "Platform", "PublishError", "TokenInfo", "TelegramPublisher",
           "build_platforms", "platform_label"]
