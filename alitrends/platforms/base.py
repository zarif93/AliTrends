"""What every social platform implements. Adding a network = one module with a Platform subclass,
registered in platforms/__init__.py; the bot loop and the panel pick it up from there."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable

import requests

from ..aliexpress import Product
from ..config import Market, Target


class PublishError(RuntimeError):
    pass


@dataclass(frozen=True)
class TokenInfo:
    """Health of one credential, shown on the dashboard and used for expiry warnings."""
    name: str
    ok: bool
    expires_at: datetime | None = None  # None = never expires or unknown
    message: str = ""


# Called by a platform when it rotates a per-target token: (target row id, new token, expiry).
SaveSecret = Callable[[int, str, datetime | None], None]


class Platform:
    name: str = ""
    label: str = ""                     # Hebrew name for the panel
    target_hint: str = ""               # what to type in the target id field
    needs_target_secret: bool = False   # True if each target carries its own token

    def available(self) -> bool:
        """False when the platform's credentials are missing; its targets are then skipped."""
        return True

    def prepare(self, targets: Iterable[Target]) -> None:
        """Called once per cycle before publishing, e.g. to refresh tokens."""

    def publish(self, target: Target, product: Product, text: str, market: Market) -> str:
        """Publish and return the platform's post id, or raise PublishError."""
        raise NotImplementedError

    def followers(self, target: Target) -> int | None:
        return None

    def token_info(self, targets: Iterable[Target]) -> list[TokenInfo]:
        return []

    def resolve_target(self, target_id: str, secret: str | None) -> tuple[str, str]:
        """Validate a new target from the panel; returns (target id, display label)."""
        return target_id, ""


def graph_json(session: requests.Session, method: str, url: str, prefix: str, **kwargs) -> dict:
    """Call a Graph-style API (Facebook, Instagram, Threads) and raise PublishError on any failure."""
    try:
        data = session.request(method, url, timeout=60, **kwargs).json()
    except (requests.RequestException, ValueError) as exc:
        raise PublishError(f"{prefix}: {exc}") from exc
    if isinstance(data, dict) and "error" in data:
        error = data["error"]
        raise PublishError(f"{prefix}: {error.get('message') if isinstance(error, dict) else error}")
    return data
