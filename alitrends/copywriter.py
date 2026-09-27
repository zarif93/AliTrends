"""AI-written marketing copy. Prices and links are never written by the model — see render.py."""
from __future__ import annotations

import json
import logging
import re

from openai import OpenAI, OpenAIError

from .aliexpress import Product
from .config import Market
from .storage import Storage

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a senior social-media copywriter for a deals channel.
You write short, natural, native-sounding posts that make people want to check out a product.
You never invent facts: no made-up specs, prices, discounts, shipping times or guarantees.
You never mention AliExpress, affiliate links, or that the text was generated.
You always answer with a single JSON object and nothing else."""

USER_PROMPT = """Write a post in {language} about this product.

Product title (may be messy, keyword-stuffed): {title}
Category: {category}

Return JSON with exactly these keys:
- "headline": one punchy line, max 10 words, 1 fitting emoji at the start. Lead with the main benefit or the problem it solves.
- "body": 2-3 short sentences, max 45 words total. Second person. Benefit-focused (problem -> solution -> result).
  Name the product once, in a short natural form (not the raw title). No prices, no numbers about discounts, no links, no hashtags.
- "hashtags": 5 relevant hashtags in {language} (no spaces inside a tag, each starting with #), mixing broad and specific.

Address the reader in a gender-neutral way (in Hebrew and Arabic use the plural form, e.g. "אתם").
Write like a real person from the {language}-speaking audience, not a translation. Avoid clichés like "Buy now!" and "Don't miss out"."""

_NON_WORD = re.compile(r"[^\w]")


class CopyError(RuntimeError):
    pass


class Copywriter:
    def __init__(self, api_key: str, model: str, storage: Storage):
        self._client = OpenAI(api_key=api_key, max_retries=3, timeout=60)
        self._model = model
        self._storage = storage

    def copy_for(self, product: Product, market: Market) -> dict:
        """Cached per product+language, so each post is written once and reused across channels."""
        cached = self._storage.get_copy(product.product_id, market.language)
        if cached:
            return cached
        content = self._generate(product, market)
        self._storage.save_copy(product.product_id, market.language, content)
        return content

    def _generate(self, product: Product, market: Market) -> dict:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                response_format={"type": "json_object"},
                temperature=0.8,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": USER_PROMPT.format(
                        language=market.language, title=product.title, category=product.category)},
                ],
            )
            data = json.loads(response.choices[0].message.content or "{}")
        except (OpenAIError, json.JSONDecodeError) as exc:
            raise CopyError(f"Copy generation failed for {product.product_id}: {exc}") from exc
        return _validate(data)


def _validate(data: dict) -> dict:
    headline = str(data.get("headline") or "").strip()
    body = str(data.get("body") or "").strip()
    raw_tags = data.get("hashtags") or []
    if isinstance(raw_tags, str):
        raw_tags = raw_tags.split()
    hashtags = []
    for item in raw_tags:
        item = str(item).strip()
        # "#a #b" or "#a#b" packed into one item -> split; a single "Two Words" tag -> Two_Words
        parts = item.replace("#", " #").split() if item.count("#") > 1 else [item.replace(" ", "_")]
        for part in parts:
            tag = "#" + _NON_WORD.sub("", part.lstrip("#").replace("-", "_"))
            if len(tag) > 2 and tag not in hashtags:
                hashtags.append(tag)

    if not headline or not body:
        raise CopyError(f"Model returned incomplete copy: {data!r}")
    return {"headline": headline, "body": body, "hashtags": hashtags[:6]}
