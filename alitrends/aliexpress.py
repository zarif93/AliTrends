"""Minimal, typed client for the AliExpress Affiliate API (api-sg.aliexpress.com/sync)."""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

import requests

log = logging.getLogger(__name__)

API_URL = "https://api-sg.aliexpress.com/sync"
MIN_CALL_INTERVAL = 1.2  # seconds; the API bans bursts ("ApiCallLimit") for ~1s


class AliExpressError(RuntimeError):
    pass


class RateLimited(AliExpressError):
    pass


@dataclass(frozen=True)
class Product:
    product_id: str
    title: str
    image_url: str
    promotion_link: str
    price: Decimal
    original_price: Decimal
    currency: str
    discount: int          # percent, 0-100
    rating: float | None   # 0-5 stars, derived from evaluate_rate
    sales: int             # recent sales volume
    commission_rate: float # percent
    category: str
    hot: bool = False      # came from hotproduct.query

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "Product | None":
        """Parse one API product; returns None when essential fields are missing or malformed."""
        try:
            price = _decimal(raw.get("target_sale_price") or raw.get("target_app_sale_price"))
            original = _decimal(raw.get("target_original_price")) or price
            link = raw.get("promotion_link") or ""
            image = raw.get("product_main_image_url") or ""
            if not (price and link and image):
                return None
            rate = _percent(raw.get("evaluate_rate"))
            return cls(
                product_id=str(raw["product_id"]),
                title=(raw.get("product_title") or "").strip(),
                image_url=image,
                promotion_link=link,
                price=price,
                original_price=max(original, price),
                currency=raw.get("target_sale_price_currency") or "USD",
                discount=int(_percent(raw.get("discount")) or 0),
                rating=round(rate * 5 / 100, 1) if rate else None,
                sales=int(raw.get("lastest_volume") or 0),
                commission_rate=_percent(raw.get("hot_product_commission_rate") or raw.get("commission_rate")) or 0.0,
                category=raw.get("second_level_category_name") or raw.get("first_level_category_name") or "",
            )
        except (KeyError, ValueError, TypeError, InvalidOperation):
            log.debug("Skipping malformed product %r", raw.get("product_id"))
            return None


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value).replace(",", ""))


def _percent(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(str(value).strip().rstrip("%"))


def sign(params: dict[str, str], secret: str) -> str:
    """HMAC-SHA256 over the key-sorted concatenation of key+value pairs (AliExpress 'sync' signing)."""
    payload = "".join(f"{k}{params[k]}" for k in sorted(params))
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest().upper()


class AliExpressClient:
    def __init__(self, app_key: str, app_secret: str, tracking_id: str,
                 session: requests.Session | None = None, retries: int = 3, timeout: float = 30):
        self._app_key = app_key
        self._secret = app_secret
        self.tracking_id = tracking_id
        self._session = session or requests.Session()
        self._retries = retries
        self._timeout = timeout
        self._last_call = 0.0

    def _throttle(self) -> None:
        wait = self._last_call + MIN_CALL_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        """Call an API method and return its `resp_result.result` (or the raw response body)."""
        body = {k: str(v) for k, v in params.items() if v is not None}
        last_error: Exception | None = None

        for attempt in range(1, self._retries + 1):
            signed = {
                "app_key": self._app_key,
                "method": method,
                "timestamp": str(int(time.time() * 1000)),
                "sign_method": "sha256",
                **body,
            }
            signed["sign"] = sign(signed, self._secret)
            self._throttle()
            try:
                response = self._session.post(API_URL, data=signed, timeout=self._timeout)
                response.raise_for_status()
                return self._unwrap(method, response.json())
            except (requests.RequestException, ValueError, RateLimited) as exc:
                last_error = exc
                log.warning("AliExpress %s failed (attempt %d/%d): %s", method, attempt, self._retries, exc)
                time.sleep(2 ** attempt)

        raise AliExpressError(f"{method} failed after {self._retries} attempts: {last_error}")

    @staticmethod
    def _unwrap(method: str, data: dict[str, Any]) -> dict[str, Any]:
        if "error_response" in data:
            err = data["error_response"]
            error = RateLimited if err.get("code") == "ApiCallLimit" else AliExpressError
            raise error(f"{method}: {err.get('code')} - {err.get('msg')}")
        key = method.replace(".", "_") + "_response"
        result = data.get(key, {}).get("resp_result", {})
        if result and str(result.get("resp_code")) not in ("200", "None"):
            raise AliExpressError(f"{method}: {result.get('resp_code')} - {result.get('resp_msg')}")
        return result.get("result") or {}

    def short_link(self, url: str) -> str:
        """Convert a long promotion link into a short s.click one; falls back to the original link."""
        try:
            result = self.call("aliexpress.affiliate.link.generate", promotion_link_type=0,
                               source_values=url, tracking_id=self.tracking_id)
            links = (result.get("promotion_links") or {}).get("promotion_link") or []
            return (links[0].get("promotion_link") if links else None) or url
        except AliExpressError as exc:
            log.warning("Short link failed, using the long one: %s", exc)
            return url

    def search_products(self, **kwargs: Any) -> list[Product]:
        """Regular catalogue search (aliexpress.affiliate.product.query)."""
        return self._query("aliexpress.affiliate.product.query", hot=False, **kwargs)

    def hot_products(self, **kwargs: Any) -> list[Product]:
        """Hot products with boosted commission (Advanced API permission required)."""
        return self._query("aliexpress.affiliate.hotproduct.query", hot=True, **kwargs)

    def _query(self, method: str, *, hot: bool, category_ids: Iterable[str] = (), keywords: str | None = None,
               language: str = "EN", currency: str = "USD", country: str = "US",
               sort: str = "LAST_VOLUME_DESC", page: int = 1, page_size: int = 50) -> list[Product]:
        result = self.call(
            method,
            category_ids=",".join(category_ids) or None,
            keywords=keywords,
            target_language=language,
            target_currency=currency,
            ship_to_country=country,
            sort=sort,
            page_no=page,
            page_size=page_size,
            tracking_id=self.tracking_id,
        )
        raw_products = (result.get("products") or {}).get("product") or []
        products = [p for p in map(Product.from_api, raw_products) if p]
        return [replace(p, hot=True) for p in products] if hot else products
