"""Choosing which product to post: fetch candidate pools from the API, filter, score, de-duplicate."""
from __future__ import annotations

import logging
import math
import random
import time
from dataclasses import dataclass, replace

from .aliexpress import AliExpressClient, AliExpressError, Product
from .config import CATEGORIES, MAIN, Market
from .storage import Storage

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class QualityBar:
    min_rating: float = 4.5
    min_sales: int = 100


def score(p: Product) -> float:
    """Higher is better: real discounts, proven demand, happy buyers, and what pays us."""
    rating = p.rating or 4.5
    return (
        p.discount * 1.0                       # 0..90
        + math.log10(max(p.sales, 1)) * 12     # 100 sales -> 24, 10k -> 48
        + (rating - 4.5) * 40                  # 4.5 -> 0, 5.0 -> 20
        + p.commission_rate * 1.5              # 7% -> ~10
    )


def passes(p: Product, bar: QualityBar, market: Market) -> bool:
    return (
        p.sales >= bar.min_sales
        and p.discount >= market.min_discount
        and (p.rating is None or p.rating >= bar.min_rating)
    )


class ProductSource:
    def __init__(self, client: AliExpressClient, storage: Storage, *, pool_ttl: int,
                 cooldown_days: int, pages: int = 2, bar: QualityBar = QualityBar(), top_k: int = 15):
        self._client = client
        self._storage = storage
        self._ttl = pool_ttl
        self._cooldown_days = cooldown_days
        self._pages = pages
        self._bar = bar
        self._top_k = top_k
        self._pools: dict[tuple[str, str], tuple[float, list[Product]]] = {}

    def pool(self, market: Market, category: str) -> list[Product]:
        """Scored, filtered candidates for a market+category, cached for `pool_ttl` seconds."""
        if category == MAIN:
            merged = {p.product_id: p for cat in CATEGORIES for p in self.pool(market, cat)}
            return sorted(merged.values(), key=score, reverse=True)

        key = (market.language, category)
        cached = self._pools.get(key)
        if cached and time.monotonic() - cached[0] < self._ttl:
            return cached[1]

        products: dict[str, Product] = {}
        for page in range(1, self._pages + 1):
            try:
                batch = self._client.search_products(
                    category_ids=CATEGORIES[category], language=market.api_language,
                    currency=market.currency, country=market.country, page=page,
                )
            except AliExpressError as exc:
                log.error("Fetching %s/%s page %d failed: %s", market.language, category, page, exc)
                break
            products.update((p.product_id, p) for p in batch if passes(p, self._bar, market))
            if not batch:
                break

        ranked = sorted(products.values(), key=score, reverse=True)
        if ranked or not cached:
            self._pools[key] = (time.monotonic(), ranked)
        else:
            log.warning("Empty refresh for %s/%s, keeping previous pool", market.language, category)
            ranked = cached[1]
        log.info("Pool %s/%s: %d candidates", market.language, category, len(ranked))
        return ranked

    def pick(self, market: Market, category: str, channel_key: str) -> Product | None:
        """Pick a fresh product for a channel: weighted-random among the best not posted recently."""
        seen = self._storage.recently_published(channel_key, self._cooldown_days)
        fresh = [p for p in self.pool(market, category) if p.product_id not in seen][: self._top_k]
        if not fresh:
            return None
        # Rank-weighted so the best deals win most of the time without the channel feeling repetitive.
        weights = [1 / (rank + 2) for rank in range(len(fresh))]
        product = random.choices(fresh, weights=weights, k=1)[0]
        return replace(product, promotion_link=self._client.short_link(product.promotion_link))
