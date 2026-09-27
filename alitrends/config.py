"""Static configuration (markets, categories) and runtime settings loaded from the environment."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Market:
    """How a content language maps onto AliExpress: shipping country, currency and title language."""
    language: str      # human name used in prompts and env keys, e.g. "Hebrew"
    api_language: str  # AliExpress target_language
    country: str       # AliExpress ship_to_country (affects availability and link validity)
    currency: str      # AliExpress target_currency
    rtl: bool = False
    min_discount: int = 15  # EU listings rarely show a strike-through price, so EU markets use 0


MARKETS: dict[str, Market] = {
    "English": Market("English", "EN", "US", "USD"),
    "Arabic": Market("Arabic", "AR", "SA", "USD", rtl=True),
    "Portuguese": Market("Portuguese", "PT", "BR", "BRL"),
    "French": Market("French", "FR", "FR", "EUR", min_discount=0),
    "Spanish": Market("Spanish", "ES", "ES", "EUR", min_discount=0),
    "Hebrew": Market("Hebrew", "HE", "IL", "ILS", rtl=True),
}

MAIN = "main"

# Channel category -> AliExpress first-level category ids (from aliexpress.affiliate.category.get).
CATEGORIES: dict[str, tuple[str, ...]] = {
    "Electronics & Technology": ("44", "509", "7", "202192403"),
    "Fashion & Accessories": ("3", "200000345", "200000343", "200000297", "322", "36", "1511", "1524"),
    "Home & Living": ("15", "6", "39", "1503"),
    "Sports & Outdoor": ("18", "201768104"),
    "Toys & Kids": ("26", "1501"),
    "Automotive & Motorcycle": ("34", "201355758"),
    "Beauty & Health": ("66", "200165144"),
    "Office & Education": ("21",),
    "Security & Tools": ("30", "1420", "13"),
}

CHANNEL_CATEGORIES: tuple[str, ...] = (MAIN, *CATEGORIES)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_channel(key: str) -> str | None:
    """Channel ids live in env keys like "Hebrew main"; missing or "false" means the channel is off."""
    value = (os.getenv(key) or "").strip()
    return None if value.lower() in ("", "false") else value


@dataclass(frozen=True)
class Channel:
    language: str
    category: str               # MAIN or a key of CATEGORIES
    telegram_id: str | None
    facebook_page_id: str | None

    @property
    def key(self) -> str:
        return f"{self.language}/{self.category}"

    @property
    def market(self) -> Market:
        return MARKETS[self.language]


@dataclass(frozen=True)
class Settings:
    ali_app_key: str
    ali_app_secret: str
    ali_tracking_id: str
    openai_api_key: str
    openai_model: str
    telegram_token: str
    admin_chat_id: str
    facebook_user_token: str | None
    db_path: str
    log_dir: str
    post_delay_seconds: int
    cycle_sleep_seconds: int
    repost_cooldown_days: int
    pool_ttl_seconds: int
    shabbat_enabled: bool
    daily_report_hour: int  # Israel time
    channels: tuple[Channel, ...] = field(default_factory=tuple)

    @classmethod
    def from_env(cls, env_file: str | Path | None = BASE_DIR / ".env") -> "Settings":
        load_dotenv(env_file)

        def required(name: str) -> str:
            value = os.getenv(name)
            if not value:
                raise RuntimeError(f"Missing required environment variable: {name}")
            return value

        channels = tuple(
            Channel(
                language=lang,
                category=cat,
                telegram_id=_env_channel(f"{lang} {cat}"),
                facebook_page_id=_env_channel(f"{lang} Facebook {cat}"),
            )
            for lang in MARKETS
            for cat in CHANNEL_CATEGORIES
        )

        return cls(
            ali_app_key=required("ALI_APP_KEY"),
            ali_app_secret=required("ALI_APP_SECRET"),
            ali_tracking_id=required("ALI_TRACKING_ID"),
            openai_api_key=required("AI_API"),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            telegram_token=required("BOT_TOKEN"),
            admin_chat_id=os.getenv("ADMIN_CHAT_ID", "7902249875"),
            facebook_user_token=os.getenv("FACE_TOKEN") or None,
            db_path=os.getenv("DB_PATH", str(BASE_DIR / "alitrends.db")),
            log_dir=os.getenv("LOG_DIR", str(BASE_DIR / "logs")),
            post_delay_seconds=int(os.getenv("POST_DELAY_SECONDS", "30")),
            cycle_sleep_seconds=int(os.getenv("CYCLE_SLEEP_SECONDS", "4900")),
            repost_cooldown_days=int(os.getenv("REPOST_COOLDOWN_DAYS", "21")),
            pool_ttl_seconds=int(os.getenv("POOL_TTL_SECONDS", str(6 * 3600))),
            shabbat_enabled=_env_bool("SHABBAT_ENABLED", True),
            daily_report_hour=int(os.getenv("DAILY_REPORT_HOUR", "21")),
            channels=tuple(c for c in channels if c.telegram_id or c.facebook_page_id),
        )
