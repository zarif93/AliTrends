"""Static configuration (markets, categories), secrets from the environment, and the runtime model.

Secrets (API keys, tokens) live in `.env`. Everything an admin may change at runtime — channels, their
targets and the tuning knobs — lives in the database (see storage.py) and is edited from the panel.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from datetime import datetime, time
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Market:
    """How a content language maps onto AliExpress: shipping country, currency and title language."""
    language: str      # human name used in prompts and env keys, e.g. "Hebrew"
    api_language: str  # AliExpress target_language
    country: str       # AliExpress ship_to_country (affects availability and link validity)
    currency: str      # AliExpress target_currency
    timezone: str      # where the audience lives; active hours are evaluated here
    rtl: bool = False
    min_discount: int = 15  # EU listings rarely show a strike-through price, so EU markets use 0


MARKETS: dict[str, Market] = {
    "English": Market("English", "EN", "US", "USD", "America/New_York"),
    "Arabic": Market("Arabic", "AR", "SA", "USD", "Asia/Riyadh", rtl=True),
    "Portuguese": Market("Portuguese", "PT", "BR", "BRL", "America/Sao_Paulo"),
    "French": Market("French", "FR", "FR", "EUR", "Europe/Paris", min_discount=0),
    "Spanish": Market("Spanish", "ES", "ES", "EUR", "Europe/Madrid", min_discount=0),
    "Hebrew": Market("Hebrew", "HE", "IL", "ILS", "Asia/Jerusalem", rtl=True),
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
    """Legacy channel ids live in env keys like "Hebrew main"; missing or "false" means off."""
    value = (os.getenv(key) or "").strip()
    return None if value.lower() in ("", "false") else value


# --- secrets -----------------------------------------------------------------

@dataclass(frozen=True)
class Secrets:
    ali_app_key: str
    ali_app_secret: str
    ali_tracking_id: str
    openai_api_key: str
    telegram_token: str
    admin_chat_id: str
    facebook_user_token: str | None
    db_path: str
    log_dir: str
    pinterest_app_id: str | None = None
    pinterest_app_secret: str | None = None

    @classmethod
    def from_env(cls, env_file: str | Path | None = BASE_DIR / ".env") -> "Secrets":
        load_dotenv(env_file)

        def required(name: str) -> str:
            value = os.getenv(name)
            if not value:
                raise RuntimeError(f"Missing required environment variable: {name}")
            return value

        return cls(
            ali_app_key=required("ALI_APP_KEY"),
            ali_app_secret=required("ALI_APP_SECRET"),
            ali_tracking_id=required("ALI_TRACKING_ID"),
            openai_api_key=required("AI_API"),
            telegram_token=required("BOT_TOKEN"),
            admin_chat_id=os.getenv("ADMIN_CHAT_ID", "7902249875"),
            facebook_user_token=os.getenv("FACE_TOKEN") or None,
            db_path=os.getenv("DB_PATH", str(BASE_DIR / "alitrends.db")),
            log_dir=os.getenv("LOG_DIR", str(BASE_DIR / "logs")),
            pinterest_app_id=os.getenv("PINTEREST_APP_ID") or None,
            pinterest_app_secret=os.getenv("PINTEREST_APP_SECRET") or None,
        )


# --- runtime tuning (DB-backed, edited from the panel) -------------------------

@dataclass(frozen=True)
class Tuning:
    paused: bool = False
    openai_model: str = "gpt-4o-mini"
    post_delay_seconds: int = 30
    cycle_sleep_seconds: int = 4900
    repost_cooldown_days: int = 21
    pool_ttl_seconds: int = 6 * 3600
    shabbat_enabled: bool = True
    daily_report_hour: int = 21          # Israel time
    min_rating: float = 4.5
    min_sales: int = 100
    hot_products_enabled: bool = True
    backup_enabled: bool = True
    watchdog_minutes: int = 45
    token_warning_days: int = 7
    # Per language: minimum discount (%) and extra copywriting instructions.
    min_discount: Mapping[str, int] = field(default_factory=lambda: {m.language: m.min_discount for m in MARKETS.values()})
    prompt_extra: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, values: Mapping[str, Any]) -> "Tuning":
        """Build from the settings table: plain keys, plus "min_discount.<Language>" / "prompt.<Language>"."""
        default = cls()
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            if f.name in ("min_discount", "prompt_extra") or f.name not in values:
                continue
            kind = type(getattr(default, f.name))
            try:
                kwargs[f.name] = _coerce(values[f.name], kind)
            except (TypeError, ValueError):
                continue  # a bad stored value falls back to the default rather than crashing the bot
        discounts = dict(default.min_discount)
        prompts: dict[str, str] = {}
        for language in MARKETS:
            if f"min_discount.{language}" in values:
                try:
                    discounts[language] = int(values[f"min_discount.{language}"])
                except (TypeError, ValueError):
                    pass
            text = str(values.get(f"prompt.{language}") or "").strip()
            if text:
                prompts[language] = text
        return cls(**kwargs, min_discount=discounts, prompt_extra=prompts)


def _coerce(value: Any, kind: type) -> Any:
    if kind is bool:
        return value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "on")
    return kind(value)


@dataclass(frozen=True)
class SettingField:
    key: str
    label: str
    kind: type
    help: str = ""
    minimum: float | None = None
    maximum: float | None = None
    group: str = "general"


# Drives the settings form in the panel and validation of submitted values.
SETTING_FIELDS: tuple[SettingField, ...] = (
    SettingField("post_delay_seconds", "המתנה בין פוסטים (שניות)", int, "בין ערוץ לערוץ באותו סבב", 0, 3600),
    SettingField("cycle_sleep_seconds", "המתנה בין סבבים (שניות)", int, "4900 = כשעה ו-20 דקות", 60, 86400),
    SettingField("shabbat_enabled", "עצירה בשבת", bool, "שישי 14:00 עד מוצ\"ש 21:30 שעון ישראל"),
    SettingField("daily_report_hour", "שעת הדוח היומי (שעון ישראל)", int, "גם גיבוי, מנויים ועמלות", 0, 23),
    SettingField("openai_model", "מודל OpenAI", str, "למשל gpt-4o-mini"),
    SettingField("hot_products_enabled", "מוצרים חמים", bool, "מוצרים עם עמלה מוגדלת קודם", group="filters"),
    SettingField("min_rating", "דירוג מינימלי", float, "מתוך 5", 0, 5, group="filters"),
    SettingField("min_sales", "מכירות מינימליות", int, "", 0, 1_000_000, group="filters"),
    SettingField("repost_cooldown_days", "ימים לפני פרסום חוזר של אותו מוצר", int, "", 0, 365, group="filters"),
    SettingField("pool_ttl_seconds", "רענון מאגר מוצרים (שניות)", int, "21600 = 6 שעות", 300, 86400, group="filters"),
    SettingField("backup_enabled", "גיבוי יומי לטלגרם", bool, group="alerts"),
    SettingField("watchdog_minutes", "התראה אם הבוט לא מגיב (דקות)", int, "", 10, 1440, group="alerts"),
    SettingField("token_warning_days", "התראה לפני פקיעת טוקן (ימים)", int, "", 1, 60, group="alerts"),
)

SETTING_KEYS = {f.key for f in SETTING_FIELDS} | {"paused"}


def parse_setting(spec: SettingField, raw: str | None) -> Any:
    """Validate a submitted form value; raises ValueError with a Hebrew message."""
    if spec.kind is bool:
        return raw in ("1", "on", "true")
    raw = (raw or "").strip()
    if not raw:
        raise ValueError(f"{spec.label}: חובה למלא")
    try:
        value = spec.kind(raw)
    except ValueError:
        raise ValueError(f"{spec.label}: ערך לא תקין") from None
    if spec.minimum is not None and value < spec.minimum:
        raise ValueError(f"{spec.label}: מינימום {spec.minimum:g}")
    if spec.maximum is not None and value > spec.maximum:
        raise ValueError(f"{spec.label}: מקסימום {spec.maximum:g}")
    return value


def legacy_tuning_from_env() -> dict[str, Any]:
    """Tuning values the old .env may still carry; imported into the DB once."""
    mapping = {
        "OPENAI_MODEL": ("openai_model", str),
        "POST_DELAY_SECONDS": ("post_delay_seconds", int),
        "CYCLE_SLEEP_SECONDS": ("cycle_sleep_seconds", int),
        "REPOST_COOLDOWN_DAYS": ("repost_cooldown_days", int),
        "POOL_TTL_SECONDS": ("pool_ttl_seconds", int),
        "DAILY_REPORT_HOUR": ("daily_report_hour", int),
    }
    out: dict[str, Any] = {}
    for env, (key, kind) in mapping.items():
        if os.getenv(env):
            try:
                out[key] = kind(os.environ[env])
            except ValueError:
                pass
    if os.getenv("SHABBAT_ENABLED") is not None:
        out["shabbat_enabled"] = _env_bool("SHABBAT_ENABLED", True)
    return out


# --- channels ----------------------------------------------------------------

@dataclass(frozen=True)
class Target:
    """One destination of a channel on one platform, e.g. a Telegram chat or a Facebook page."""
    id: int
    channel_id: int
    platform: str          # key of platforms.PLATFORMS
    target_id: str         # chat id / page id / Instagram business id / Threads user id
    enabled: bool = True
    label: str = ""
    secret: str | None = None             # per-target token (Threads)
    secret_expires_at: str | None = None  # ISO timestamp, when known


@dataclass(frozen=True)
class Channel:
    id: int
    language: str
    category: str               # MAIN or a key of CATEGORIES
    enabled: bool = True
    tracking_id: str | None = None
    every_n_cycles: int = 1
    active_from: str | None = None  # "HH:MM" in the market's timezone; None = all day
    active_to: str | None = None
    targets: tuple[Target, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.language}/{self.category}"

    @property
    def market(self) -> Market:
        return MARKETS[self.language]

    @property
    def live_targets(self) -> tuple[Target, ...]:
        return tuple(t for t in self.targets if t.enabled)

    def is_due(self, cycle_no: int) -> bool:
        """Every n-th cycle, offset by id so channels with the same n don't all post in the same cycle."""
        n = max(1, self.every_n_cycles)
        return (cycle_no + self.id) % n == 0

    def in_active_hours(self, moment: datetime) -> bool:
        if not (self.active_from and self.active_to):
            return True
        local = moment.astimezone(ZoneInfo(self.market.timezone)).time()
        start, end = parse_hhmm(self.active_from), parse_hhmm(self.active_to)
        if start <= end:
            return start <= local < end
        return local >= start or local < end  # window over midnight, e.g. 20:00-02:00


def parse_hhmm(text: str) -> time:
    hours, minutes = text.strip().split(":")
    return time(int(hours), int(minutes))


def legacy_channels_from_env() -> list[tuple[str, str, str | None, str | None]]:
    """(language, category, telegram id, facebook page id) from the old env keys, for a one-time import."""
    found = []
    for lang in MARKETS:
        for cat in CHANNEL_CATEGORIES:
            tg = _env_channel(f"{lang} {cat}")
            fb = _env_channel(f"{lang} Facebook {cat}")
            if tg or fb:
                found.append((lang, cat, tg, fb))
    return found
