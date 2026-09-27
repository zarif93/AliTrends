from decimal import Decimal

from alitrends.aliexpress import Product
from alitrends.config import MARKETS
from alitrends.render import TELEGRAM_CAPTION_LIMIT, compact_count, money, render

PRODUCT = Product("1", "t", "img", "https://s.click.aliexpress.com/e/x", Decimal("24.90"), Decimal("51.00"),
                  "ILS", 51, 4.9, 12500, 7.0, "Earphones")
COPY = {"headline": "🎧 שקט מושלם", "body": "גוף הפוסט.", "hashtags": ["#אוזניות", "#דילים"]}


def test_money_and_counts():
    assert money(Decimal("1234.5"), "ILS") == "₪1,234.50"
    assert money(Decimal("3"), "XYZ") == "3.00 XYZ"
    assert compact_count(12500) == "12K+" and compact_count(1500) == "1.5K+" and compact_count(1000) == "1K+"


def test_telegram_has_prices_but_no_link():
    text = render(PRODUCT, COPY, MARKETS["Hebrew"], "telegram")
    assert "₪24.90" in text and "במקום ₪51.00" in text and "-51%" in text
    assert "s.click" not in text
    assert "#אוזניות" in text


def test_facebook_includes_link():
    assert PRODUCT.promotion_link in render(PRODUCT, COPY, MARKETS["English"], "facebook")


def test_no_discount_hides_old_price():
    p = Product(**{**PRODUCT.__dict__, "discount": 0, "original_price": PRODUCT.price})
    assert "במקום" not in render(p, COPY, MARKETS["Hebrew"], "telegram")


def test_long_caption_is_trimmed_but_keeps_price():
    long_copy = {**COPY, "body": "מילה " * 400}
    text = render(PRODUCT, long_copy, MARKETS["Hebrew"], "telegram")
    assert len(text) <= TELEGRAM_CAPTION_LIMIT
    assert "₪24.90" in text and text.count("…") == 1


def test_hashtags_are_sanitised():
    from alitrends.copywriter import _validate
    out = _validate({"headline": "h", "body": "b", "hashtags": ["#Maison-Propre", "L'aspirateur", "#été", "#été", "#"]})
    assert out["hashtags"] == ["#Maison_Propre", "#Laspirateur", "#été"]


def test_packed_hashtags_are_split():
    from alitrends.copywriter import _validate
    out = _validate({"headline": "h", "body": "b", "hashtags": ["#fitness#musculation #WOD", "Maison Propre"]})
    assert out["hashtags"] == ["#fitness", "#musculation", "#WOD", "#Maison_Propre"]
