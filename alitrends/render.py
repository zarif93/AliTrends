"""Turns a product + AI copy into the final post text for each platform, with live, exact price data."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .aliexpress import Product
from .config import Market

TELEGRAM_CAPTION_LIMIT = 1024


@dataclass(frozen=True)
class PostStyle:
    link_in_button: bool  # Telegram shows the link as a button under the photo
    limit: int            # max characters
    max_hashtags: int


STYLES: dict[str, PostStyle] = {
    "telegram": PostStyle(link_in_button=True, limit=TELEGRAM_CAPTION_LIMIT, max_hashtags=6),
    "facebook": PostStyle(link_in_button=False, limit=60_000, max_hashtags=6),
    # Instagram captions don't make links clickable, but the short link can still be copied.
    "instagram": PostStyle(link_in_button=False, limit=2_200, max_hashtags=8),
    # Threads allows 500 characters and a single topic tag per post.
    "threads": PostStyle(link_in_button=False, limit=500, max_hashtags=1),
    # The first line becomes the pin title; the link goes in the pin's own link field.
    "pinterest": PostStyle(link_in_button=True, limit=500, max_hashtags=5),
}

CURRENCY_SYMBOLS = {"USD": "$", "ILS": "₪", "EUR": "€", "BRL": "R$", "GBP": "£"}

# Short UI strings per language. {old} = original price, {n} = sales count.
LABELS: dict[str, dict[str, str]] = {
    "English":    {"instead": "instead of {old}", "sold": "{n} sold", "button": "🛒 View deal", "link": "👉"},
    "Hebrew":     {"instead": "במקום {old}", "sold": "{n} נמכרו", "button": "🛒 לצפייה בדיל", "link": "👈"},
    "Arabic":     {"instead": "بدلاً من {old}", "sold": "{n} تم بيعها", "button": "🛒 شاهد العرض", "link": "👈"},
    "French":     {"instead": "au lieu de {old}", "sold": "{n} vendus", "button": "🛒 Voir l'offre", "link": "👉"},
    "Spanish":    {"instead": "antes {old}", "sold": "{n} vendidos", "button": "🛒 Ver oferta", "link": "👉"},
    "Portuguese": {"instead": "antes {old}", "sold": "{n} vendidos", "button": "🛒 Ver oferta", "link": "👉"},
}


def money(amount: Decimal, currency: str) -> str:
    symbol = CURRENCY_SYMBOLS.get(currency)
    text = f"{amount:,.2f}"
    return f"{symbol}{text}" if symbol else f"{text} {currency}"


def compact_count(n: int) -> str:
    if n >= 10_000:
        return f"{n // 1000}K+"
    if n >= 1_000:
        return f"{n / 1000:.1f}K+".replace(".0K", "K")
    return f"{n}+"


def button_text(market: Market) -> str:
    return LABELS[market.language]["button"]


def deal_lines(product: Product, market: Market) -> list[str]:
    labels = LABELS[market.language]
    price = f"💰 {money(product.price, product.currency)}"
    if product.discount > 0 and product.original_price > product.price:
        old = money(product.original_price, product.currency)
        price += f"  ({labels['instead'].format(old=old)}, -{product.discount}%)"
    social = []
    if product.rating:
        social.append(f"⭐ {product.rating}/5")
    if product.sales:
        social.append("🔥 " + labels["sold"].format(n=compact_count(product.sales)))
    return [price] + ([" · ".join(social)] if social else [])


def render(product: Product, copy: dict, market: Market, platform: str) -> str:
    """Final post text for a platform: the link goes in a button or inline, within the platform's limits."""
    style = STYLES[platform]
    head = [copy["headline"], "", copy["body"], "", *deal_lines(product, market)]
    link = [] if style.link_in_button else ["", f"{LABELS[market.language]['link']} {product.promotion_link}"]
    tags = (copy.get("hashtags") or [])[: style.max_hashtags]
    tail = ["", " ".join(tags)] if tags else []

    text = "\n".join(head + link + tail)
    if len(text) > style.limit:
        text = _fit(head, link, style.limit)
    return text


def _fit(head: list[str], link: list[str], limit: int) -> str:
    """Drop hashtags first, then shorten the body; the price lines and the link are never cut."""
    text = "\n".join(head + link)
    if len(text) <= limit:
        return text
    body_index = 2
    overflow = len(text) - limit + 1  # room for the ellipsis
    head = head.copy()
    head[body_index] = head[body_index][: max(0, len(head[body_index]) - overflow)].rstrip() + "…"
    text = "\n".join(head + link)
    if len(text) > limit:  # headline and prices alone are too long: hard cut, but keep the link
        suffix = "\n" + "\n".join(link) if link else ""
        text = "\n".join(head)[: max(0, limit - len(suffix))] + suffix
    return text
