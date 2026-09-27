"""The posting loop: one product per channel per cycle, isolated failures, Shabbat-aware."""
from __future__ import annotations

import argparse
import logging
import os
import time
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler

from .aliexpress import AliExpressClient
from .config import Channel, Settings
from .copywriter import Copywriter, CopyError
from .publishers import FacebookPublisher, PublishError, TelegramPublisher
from .render import button_text, render
from .schedule import is_shabbat, now_israel, resume_time
from .sourcing import ProductSource
from .storage import Storage

log = logging.getLogger("alitrends")


@dataclass
class CycleReport:
    published: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        text = f"✅ Cycle done: {self.published} published, {self.skipped} skipped, {len(self.errors)} errors"
        if self.errors:
            text += "\n\n" + "\n".join(f"• {e}" for e in self.errors[:15])
        return text


class Bot:
    def __init__(self, settings: Settings, *, dry_run: bool = False):
        self.settings = settings
        self.dry_run = dry_run
        self.storage = Storage(settings.db_path)
        self.source = ProductSource(
            AliExpressClient(settings.ali_app_key, settings.ali_app_secret, settings.ali_tracking_id),
            self.storage,
            pool_ttl=settings.pool_ttl_seconds,
            cooldown_days=settings.repost_cooldown_days,
        )
        self.copywriter = Copywriter(settings.openai_api_key, settings.openai_model, self.storage)
        self.telegram = TelegramPublisher(settings.telegram_token, settings.admin_chat_id)
        self.facebook = FacebookPublisher(settings.facebook_user_token) if settings.facebook_user_token else None

    # --- loop ----------------------------------------------------------------

    def run_forever(self, *, once: bool = False, limit: int | None = None, only: str | None = None) -> None:
        log.info("Starting: %d channels, dry_run=%s", len(self.settings.channels), self.dry_run)
        while True:
            try:
                report = self.run_cycle(limit=limit, only=only)
                log.info(report.summary())
                if report.errors and not self.dry_run:
                    self.telegram.notify_admin(report.summary(), silent=True)
            except Exception as exc:  # last line of defence: log, alert, keep the bot alive
                log.exception("Cycle crashed")
                if not self.dry_run:
                    self.telegram.notify_admin(f"❌ Cycle crashed: {exc!r}")
                if once:
                    raise
                time.sleep(300)
                continue
            if once:
                return
            log.info("Sleeping %ds until next cycle", self.settings.cycle_sleep_seconds)
            time.sleep(self.settings.cycle_sleep_seconds)

    def run_cycle(self, limit: int | None = None, only: str | None = None) -> CycleReport:
        report = CycleReport()
        if self.facebook and not self.dry_run:
            try:
                self.facebook.refresh_tokens()
            except PublishError as exc:
                report.errors.append(f"Facebook tokens: {exc}")

        channels = [c for c in self.settings.channels if not only or only.lower() in c.key.lower()]
        channels = channels[:limit] if limit else channels
        for index, channel in enumerate(channels):
            self._wait_out_shabbat()
            posted = self._process(channel, report)
            if posted and index < len(channels) - 1 and not self.dry_run:
                time.sleep(self.settings.post_delay_seconds)
        return report

    def _wait_out_shabbat(self) -> None:
        if not self.settings.shabbat_enabled or not is_shabbat(now_israel()):
            return
        until = resume_time(now_israel())
        log.info("Shabbat: pausing until %s", until.isoformat())
        if not self.dry_run:
            self.telegram.notify_admin("עוצרים לכבוד שבת 🕯️", silent=False)
        while now_israel() < until:
            time.sleep(min(600, max(1, (until - now_israel()).total_seconds())))
        if not self.dry_run:
            self.telegram.notify_admin("שבת יצאה, שבוע טוב! ✨", silent=False)

    # --- one channel ---------------------------------------------------------

    def _process(self, channel: Channel, report: CycleReport) -> bool:
        market = channel.market
        product = self.source.pick(market, channel.category, channel.key)
        if not product:
            log.warning("%s: no fresh product available", channel.key)
            report.skipped += 1
            return False

        try:
            copy = self.copywriter.copy_for(product, market)
        except CopyError as exc:
            report.errors.append(f"{channel.key}: {exc}")
            log.error("%s: %s", channel.key, exc)
            return False

        posted = False
        targets = (("telegram", channel.telegram_id), ("facebook", channel.facebook_page_id))
        for platform, target in targets:
            if not target:
                continue
            if platform == "facebook" and not self.facebook:
                continue
            text = render(product, copy, market, platform)
            if self.dry_run:
                print(f"\n===== [{platform}] {channel.key} -> {product.product_id}\n{text}\n")
                posted = True
                continue
            try:
                if platform == "telegram":
                    external_id = self.telegram.publish(target, product.image_url, text,
                                                        product.promotion_link, button_text(market))
                else:
                    external_id = self.facebook.publish(target, product.image_url, text)
            except PublishError as exc:
                report.errors.append(str(exc))
                log.error("%s", exc)
                continue
            self.storage.record_publication(channel.key, platform, product.product_id, external_id,
                                            str(product.price), product.currency)
            report.published += 1
            posted = True
            log.info("%s [%s] posted %s (%s %s)", channel.key, platform, product.product_id,
                     product.price, product.currency)
        return posted


def setup_logging(log_dir: str, verbose: bool) -> None:
    os.makedirs(log_dir, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(os.path.join(log_dir, "alitrends.log"), maxBytes=5_000_000,
                                       backupCount=5, encoding="utf-8")
    console = logging.StreamHandler()
    for handler in (file_handler, console):
        handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers[:] = [file_handler, console]
    for noisy in ("urllib3", "httpx", "openai", "TeleBot"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="AliTrends deals publisher")
    parser.add_argument("--dry-run", action="store_true", help="print posts instead of publishing")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    parser.add_argument("--limit", type=int, help="only process the first N channels")
    parser.add_argument("--only", help='only channels whose key contains this text, e.g. "Hebrew/main"')
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    setup_logging(settings.log_dir, args.verbose)
    Bot(settings, dry_run=args.dry_run).run_forever(once=args.once, limit=args.limit, only=args.only)
