"""Turns a product + AI copy into the final post text for each platform, with live, exact price data."""
from __future__ import annotations

from decimal import Decimal

from .aliexpress import Product
from .config import Market

TELEGRAM_CAPTION_LIMIT = 1024

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
    """platform: "telegram" (link lives in a button, 1024-char limit) or "facebook" (link inline)."""
    head = [copy["headline"], "", copy["body"], "", *deal_lines(product, market)]
    tail: list[str] = []
    if platform == "facebook":
        tail += ["", f"{LABELS[market.language]['link']} {product.promotion_link}"]
    if copy.get("hashtags"):
        tail += ["", " ".join(copy["hashtags"])]

    text = "\n".join(head + tail)
    if platform == "telegram" and len(text) > TELEGRAM_CAPTION_LIMIT:
        text = _fit(head, tail, TELEGRAM_CAPTION_LIMIT)
    return text


def _fit(head: list[str], tail: list[str], limit: int) -> str:
    """Drop hashtags first, then shorten the body; the price lines are never cut."""
    text = "\n".join(head)
    if len(text) <= limit:
        return text
    body_index = 2
    overflow = len(text) - limit + 1
    head = head.copy()
    head[body_index] = head[body_index][: max(0, len(head[body_index]) - overflow)].rstrip() + "…"
    return "\n".join(head)[:limit]
