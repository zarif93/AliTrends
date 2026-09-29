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


def rank_key(p: Product) -> tuple[bool, float]:
    """Hot products (boosted commission) come first; score orders within each group."""
    return (p.hot, score(p))


class ProductSource:
    HOT_BACKOFF_SECONDS = 6 * 3600  # after a hotproduct.query failure, use only product.query for a while

    def __init__(self, client: AliExpressClient, storage: Storage, *, pool_ttl: int,
                 cooldown_days: int, pages: int = 2, bar: QualityBar = QualityBar(), top_k: int = 15,
                 min_candidates: int = 30):
        self._client = client
        self._storage = storage
        self._ttl = pool_ttl
        self._cooldown_days = cooldown_days
        self._pages = pages
        self._bar = bar
        self._top_k = top_k
        self._min_candidates = min_candidates
        self._hot_disabled_until = 0.0
        self._pools: dict[tuple[str, str], tuple[float, list[Product]]] = {}

    def pool(self, market: Market, category: str) -> list[Product]:
        """Filtered candidates for a market+category, hot first, cached for `pool_ttl` seconds."""
        if category == MAIN:
            merged: dict[str, Product] = {}
            for cat in CATEGORIES:
                for p in self.pool(market, cat):
                    merged.setdefault(p.product_id, p)
            return sorted(merged.values(), key=rank_key, reverse=True)

        key = (market.language, category)
        cached = self._pools.get(key)
        if cached and time.monotonic() - cached[0] < self._ttl:
            return cached[1]

        products: dict[str, Product] = {}
        hot_count = 0
        if time.monotonic() >= self._hot_disabled_until:
            self._collect(market, category, products, hot=True)
            hot_count = len(products)
        if len(products) < self._min_candidates:
            self._collect(market, category, products, hot=False)

        ranked = sorted(products.values(), key=rank_key, reverse=True)
        if ranked or not cached:
            self._pools[key] = (time.monotonic(), ranked)
        else:
            log.warning("Empty refresh for %s/%s, keeping previous pool", market.language, category)
            ranked = cached[1]
        log.info("Pool %s/%s: %d candidates (%d hot)", market.language, category, len(ranked), hot_count)
        return ranked

    def _collect(self, market: Market, category: str, into: dict[str, Product], *, hot: bool) -> None:
        """Add passing products from up to `pages` pages; products already collected (e.g. hot) win."""
        fetch = self._client.hot_products if hot else self._client.search_products
        for page in range(1, self._pages + 1):
            try:
                batch = fetch(category_ids=CATEGORIES[category], language=market.api_language,
                              currency=market.currency, country=market.country, page=page)
            except AliExpressError as exc:
                if hot:
                    self._hot_disabled_until = time.monotonic() + self.HOT_BACKOFF_SECONDS
                    log.warning("Hot products unavailable (%s); using product.query only for %dh",
                                exc, self.HOT_BACKOFF_SECONDS // 3600)
                else:
                    log.error("Fetching %s/%s page %d failed: %s", market.language, category, page, exc)
                return
            for p in batch:
                if passes(p, self._bar, market):
                    into.setdefault(p.product_id, p)
            if not batch:
                return

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
