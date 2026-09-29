"""The posting loop: one product per channel per cycle, isolated failures, Shabbat-aware.

Channels and settings are re-read from the database at the start of every cycle, so changes made in
the panel apply from the next cycle. While sleeping, the bot keeps a heartbeat, runs jobs queued by
the panel (previews, "post now", scheduled manual posts) and the once-a-day tasks.
"""
from __future__ import annotations

import argparse
import gzip
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from typing import Any

from .aliexpress import AliExpressClient, AliExpressError, Product, parse_order, product_id_from_url
from .config import Channel, Secrets, Target, Tuning, legacy_channels_from_env, legacy_tuning_from_env
from .copywriter import Copywriter, CopyError
from .platforms import PLATFORM_CLASSES, Platform, PublishError, TelegramPublisher, build_platforms, platform_label
from .render import render
from .schedule import is_shabbat, now_israel, resume_time
from .sourcing import ProductSource
from .storage import Storage

log = logging.getLogger("alitrends")

TICK_SECONDS = 5            # how often a sleeping bot checks for panel jobs
HEARTBEAT_SECONDS = 30
DAILY_CHECK_SECONDS = 60
ORDER_STATUSES = ("Payment Completed", "Buyer Confirmed Receipt")
MAX_BACKUP_BYTES = 45 * 1024 * 1024  # Telegram bots may send documents up to 50 MB


@dataclass
class CycleReport:
    published: int = 0
    skipped: int = 0
    not_due: int = 0
    off_hours: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        text = f"✅ Cycle done: {self.published} published, {self.skipped} skipped, {len(self.errors)} errors"
        if self.errors:
            text += "\n\n" + "\n".join(f"• {e}" for e in self.errors[:15])
        return text


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_seeded(storage: Storage) -> None:
    """First start after the upgrade: copy channels and tuning from the old .env into the database."""
    created = storage.seed_from_env(legacy_channels_from_env(), legacy_tuning_from_env())
    if created:
        log.info("Imported %d channels from .env into the database", created)
    scrubbed = storage.scrub_secrets(name for name, cls in PLATFORM_CLASSES.items() if cls.needs_target_secret)
    if scrubbed:
        log.warning("Removed %d stray tokens from targets that don't use one", scrubbed)


class Bot:
    def __init__(self, secrets: Secrets, *, dry_run: bool = False):
        self.secrets = secrets
        self.dry_run = dry_run
        self.storage = Storage(secrets.db_path)
        ensure_seeded(self.storage)
        self.client = AliExpressClient(secrets.ali_app_key, secrets.ali_app_secret, secrets.ali_tracking_id)
        self.telegram = TelegramPublisher(secrets.telegram_token, secrets.admin_chat_id)
        self.platforms: dict[str, Platform] = build_platforms(secrets, self._save_secret, self.telegram)
        self.source = ProductSource(self.client, self.storage, pool_ttl=Tuning.pool_ttl_seconds,
                                    cooldown_days=Tuning.repost_cooldown_days)
        self.copywriter = Copywriter(secrets.openai_api_key, Tuning.openai_model, self.storage)
        self.tuning = Tuning()
        self.channels: list[Channel] = []
        self._in_jobs = False
        self._last_heartbeat = 0.0
        self._last_daily_check = 0.0

    # --- config --------------------------------------------------------------

    def reload(self) -> None:
        self.tuning = self.storage.tuning()
        self.channels = self.storage.channels(only_enabled=True)
        self.source.configure(self.tuning)
        self.copywriter.configure(self.tuning.openai_model, dict(self.tuning.prompt_extra))

    def _save_secret(self, target_row_id: int, token: str, expires_at: datetime | None) -> None:
        self.storage.update_target(target_row_id, audit=False, secret=token,
                                   secret_expires_at=expires_at.isoformat() if expires_at else None)

    # --- loop ----------------------------------------------------------------

    def run_forever(self, *, once: bool = False, limit: int | None = None, only: str | None = None) -> None:
        self.storage.fail_interrupted_jobs()
        self.reload()
        log.info("Starting: %d channels, dry_run=%s", len(self.channels), self.dry_run)
        if not self.dry_run:
            # Several of these in a row mean the service keeps crashing and systemd keeps restarting it.
            self.telegram.notify_admin(f"🟢 AliTrends עלה ({len(self.channels)} ערוצים)", silent=True)
            self.storage.add_job("refresh_status", {})  # fill the dashboard's token/follower panel
        while True:
            self.reload()
            if self.tuning.paused and not once:
                self._set_state("paused")
                self._sleep(60)
                continue
            try:
                self._set_state("running")
                report = self.run_cycle(limit=limit, only=only)
                log.info(report.summary())
                self._record_cycle(report)
                if report.errors and not self.dry_run:
                    self.telegram.notify_admin(report.summary(), silent=True)
            except Exception as exc:  # last line of defence: log, alert, keep the bot alive
                log.exception("Cycle crashed")
                self.storage.record_error("cycle", f"Cycle crashed: {exc!r}")
                if not self.dry_run:
                    self.telegram.notify_admin(f"❌ Cycle crashed: {exc!r}")
                if once:
                    raise
                self._sleep(300)
                continue
            if once:
                return
            log.info("Sleeping %ds until next cycle", self.tuning.cycle_sleep_seconds)
            self._set_state("sleeping", next_cycle_at=_utcnow() + timedelta(seconds=self.tuning.cycle_sleep_seconds))
            self._sleep(self.tuning.cycle_sleep_seconds)

    def _set_state(self, state: str, **extra: Any) -> None:
        if self.dry_run:
            return
        self.storage.set_meta_json("bot_state", {"state": state, "since": _utcnow().isoformat(),
                                                 **{k: str(v) for k, v in extra.items()}})
        self._heartbeat(force=True)

    def _record_cycle(self, report: CycleReport) -> None:
        if self.dry_run:
            return
        self.storage.set_meta_json("last_cycle", {
            "finished_at": _utcnow().isoformat(), "published": report.published, "skipped": report.skipped,
            "not_due": report.not_due, "off_hours": report.off_hours, "errors": len(report.errors)})

    def _heartbeat(self, force: bool = False) -> None:
        if self.dry_run or (not force and time.monotonic() - self._last_heartbeat < HEARTBEAT_SECONDS):
            return
        self.storage.set_meta("heartbeat", _utcnow().isoformat())
        self._last_heartbeat = time.monotonic()

    def _sleep(self, seconds: float) -> None:
        """Sleep in short slices, staying responsive to panel jobs and the daily tasks."""
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            time.sleep(min(left, TICK_SECONDS))
            self._tick()

    def _tick(self) -> None:
        self._heartbeat()
        self._run_jobs()
        if time.monotonic() - self._last_daily_check >= DAILY_CHECK_SECONDS:
            self._last_daily_check = time.monotonic()
            self._maybe_daily_tasks()

    def _shabbat_now(self) -> bool:
        return self.tuning.shabbat_enabled and is_shabbat(now_israel())

    def run_cycle(self, limit: int | None = None, only: str | None = None) -> CycleReport:
        report = CycleReport()
        cycle_no = int(self.storage.get_meta("cycle_no") or 0) + 1
        if not self.dry_run:
            self.storage.set_meta("cycle_no", str(cycle_no))

        channels = [c for c in self.channels if not only or only.lower() in c.key.lower()]
        channels = channels[:limit] if limit else channels
        self._prepare_platforms(channels, report)

        posted_any = False
        for channel in channels:
            if self.storage.tuning().paused and not self.dry_run:
                log.info("Paused from the panel, stopping the cycle")
                break
            if not channel.is_due(cycle_no):
                report.not_due += 1
                continue
            self._wait_out_shabbat()
            if not channel.in_active_hours(_utcnow()):
                report.off_hours += 1
                continue
            if posted_any and not self.dry_run:
                self._sleep(self.tuning.post_delay_seconds)
            posted_any |= self._process(channel, report)
            self._tick()
        return report

    def _prepare_platforms(self, channels: list[Channel], report: CycleReport) -> None:
        if self.dry_run:
            return
        by_platform: dict[str, list[Target]] = {}
        for channel in channels:
            for target in channel.live_targets:
                by_platform.setdefault(target.platform, []).append(target)
        for name, targets in by_platform.items():
            platform = self.platforms.get(name)
            if not platform or not platform.available():
                continue
            try:
                platform.prepare(targets)
            except PublishError as exc:
                self._error(report, f"{platform.label} tokens", str(exc))

    def _wait_out_shabbat(self) -> None:
        if not self._shabbat_now():
            return
        until = resume_time(now_israel())
        log.info("Shabbat: pausing until %s", until.isoformat())
        self._set_state("shabbat", next_cycle_at=until)
        if not self.dry_run:
            self.telegram.notify_admin("עוצרים לכבוד שבת 🕯️", silent=False)
        while now_israel() < until:
            self._sleep(min(600, max(1, (until - now_israel()).total_seconds())))
        self._set_state("running")
        if not self.dry_run:
            self.telegram.notify_admin("שבת יצאה, שבוע טוב! ✨", silent=False)

    def _error(self, report: CycleReport | None, source: str, message: str) -> None:
        log.error("%s: %s", source, message)
        if report is not None:
            report.errors.append(f"{source}: {message}")
        if not self.dry_run:
            self.storage.record_error(source, message)

    # --- one channel ---------------------------------------------------------

    def _usable(self, target: Target) -> bool:
        platform = self.platforms.get(target.platform)
        return bool(platform and platform.available())

    def _process(self, channel: Channel, report: CycleReport, product: Product | None = None) -> bool:
        market = channel.market
        targets = [t for t in channel.live_targets if self._usable(t)]
        if not targets:
            report.skipped += 1
            return False
        product = product or self.source.pick(market, channel.category, channel.key, channel.tracking_id)
        if not product:
            log.warning("%s: no fresh product available", channel.key)
            report.skipped += 1
            return False

        try:
            copy = self.copywriter.copy_for(product, market)
        except CopyError as exc:
            self._error(report, channel.key, str(exc))
            return False

        posted = False
        for target in targets:
            platform = self.platforms[target.platform]
            text = render(product, copy, market, target.platform)
            if self.dry_run:
                print(f"\n===== [{target.platform}] {channel.key} -> {product.product_id}\n{text}\n")
                posted = True
                continue
            try:
                external_id = platform.publish(target, product, text, market)
            except PublishError as exc:
                self._error(report, f"{channel.key} [{platform.label}]", str(exc))
                continue
            self.storage.record_publication(channel.key, target.platform, product.product_id, external_id,
                                            str(product.price), product.currency)
            report.published += 1
            posted = True
            log.info("%s [%s] posted %s (%s %s)", channel.key, target.platform, product.product_id,
                     product.price, product.currency)
        return posted

    # --- jobs from the panel --------------------------------------------------

    def _run_jobs(self) -> None:
        if self.dry_run or self._in_jobs:
            return
        self._in_jobs = True
        try:
            for job in self.storage.claim_due_jobs(allow_publishing=not self._shabbat_now()):
                log.info("Job %d: %s %s", job["id"], job["kind"], job["payload"])
                try:
                    result = getattr(self, f"_job_{job['kind']}")(job["payload"])
                    self.storage.finish_job(job["id"], ok=True, result=result)
                except Exception as exc:
                    log.exception("Job %d failed", job["id"])
                    self.storage.finish_job(job["id"], ok=False, result={"error": str(exc)})
        finally:
            self._in_jobs = False

    def _channel_or_fail(self, channel_id: int) -> Channel:
        channel = self.storage.channel(int(channel_id))
        if not channel:
            raise RuntimeError("הערוץ לא נמצא")
        return channel

    def _job_preview(self, payload: dict) -> dict:
        channel = self._channel_or_fail(payload["channel_id"])
        product = self.source.pick(channel.market, channel.category, channel.key, channel.tracking_id)
        if not product:
            raise RuntimeError("לא נמצא מוצר מתאים (אולי כולם פורסמו לאחרונה או נחסמו)")
        copy = self.copywriter.copy_for(product, channel.market)
        platforms = sorted({t.platform for t in channel.targets} or {"telegram"}, key=list(PLATFORM_CLASSES).index)
        return {
            "product": _product_dict(product),
            "posts": [{"platform": p, "label": platform_label(p), "text": render(product, copy, channel.market, p)}
                      for p in platforms],
        }

    def _publish_now(self, channel: Channel, product: Product | None = None) -> dict:
        report = CycleReport()
        self._prepare_platforms([channel], report)
        if not self._process(channel, report, product):
            raise RuntimeError("; ".join(report.errors) or "לא פורסם: אין יעדים פעילים או מוצר מתאים")
        return {"published": report.published, "errors": report.errors}

    def _job_post_channel(self, payload: dict) -> dict:
        return self._publish_now(self._channel_or_fail(payload["channel_id"]))

    def _job_manual_post(self, payload: dict) -> dict:
        product_id = payload.get("product_id") or product_id_from_url(payload["url"])
        if not product_id:
            raise RuntimeError("לא הצלחתי לזהות מוצר בקישור")
        results: dict[str, Any] = {}
        failures = 0
        for channel_id in payload["channel_ids"]:
            channel = self.storage.channel(int(channel_id))
            if not channel:
                continue
            market = channel.market
            try:
                product = self.client.product_detail(product_id, language=market.api_language,
                                                     currency=market.currency, country=market.country)
                if not product:
                    raise RuntimeError("המוצר לא זמין במדינה של הערוץ")
                product = self.source.with_link(product, channel.tracking_id)
                results[channel.key] = self._publish_now(channel, product)
            except (AliExpressError, RuntimeError) as exc:
                failures += 1
                results[channel.key] = {"error": str(exc)}
                self._error(None, f"{channel.key} manual post", str(exc))
        if failures and failures == len(payload["channel_ids"]):
            raise RuntimeError("; ".join(f"{k}: {v['error']}" for k, v in results.items()))
        return {"product_id": product_id, "channels": results}

    def _job_sync_commissions(self, payload: dict) -> dict:
        return {"orders": self._sync_commissions()}

    def _job_refresh_status(self, payload: dict) -> dict:
        return self._refresh_status()

    # --- daily tasks -----------------------------------------------------------

    def _maybe_daily_tasks(self) -> None:
        now = now_israel()
        if self.dry_run or now.hour < self.tuning.daily_report_hour:
            return
        today = now.date().isoformat()
        tasks = [("daily_report_date", lambda: self.telegram.notify_admin(self.daily_report_text(now), silent=True)),
                 ("status_date", self._daily_status),
                 ("commissions_date", self._sync_commissions)]
        if self.tuning.backup_enabled:
            tasks.append(("backup_date", self._backup))
        for key, task in tasks:
            if self.storage.get_meta(key) == today:
                continue
            try:
                task()
            except Exception as exc:
                log.exception("Daily task %s failed", key)
                self.storage.record_error(f"daily {key}", str(exc))
            finally:
                # Marked done even on failure, so a broken token doesn't retry every minute.
                self.storage.set_meta(key, today)

    def daily_report_text(self, now: datetime) -> str:
        since = now - timedelta(hours=24)
        by_platform = self.storage.stats_since(since)
        by_language = self.storage.stats_by_language_since(since)
        total = sum(by_platform.values())
        parts = [f"{platform_label(p)} {by_platform[p]}" for p in PLATFORM_CLASSES if by_platform.get(p)]
        lines = [
            f"{'📊' if total else '⚠️'} סיכום יומי {now:%d/%m}",
            f"פורסמו ב-24 השעות האחרונות: {total}" + (f" ({' · '.join(parts)})" if parts else ""),
        ]
        if by_language:
            lines.append("לפי שפה: " + ", ".join(f"{lang} {n}" for lang, n in by_language.items()))
        lines.append(f"שגיאות: {self.storage.errors_since(since)}")
        if not total:
            lines.append("לא פורסם כלום. כדאי לבדוק את הלוג בפאנל או: journalctl -u alitrends -n 100")
        return "\n".join(lines)

    def _daily_status(self) -> None:
        status = self._refresh_status()
        warn_before = timedelta(days=self.tuning.token_warning_days)
        problems = []
        for token in status["tokens"]:
            expires = datetime.fromisoformat(token["expires_at"]) if token["expires_at"] else None
            if not token["ok"]:
                problems.append(f"❌ {token['name']}: {token['message']}")
            elif expires and expires - _utcnow() < warn_before:
                days = max(0, (expires - _utcnow()).days)
                problems.append(f"⏳ {token['name']}: פג בעוד {days} ימים ({expires:%d/%m/%Y})")
        if problems:
            self.telegram.notify_admin("🔑 בעיות בטוקנים:\n" + "\n".join(problems))

    def _refresh_status(self) -> dict:
        """Token health and follower counts, stored for the dashboard."""
        channels = self.storage.channels()
        by_platform: dict[str, list[Target]] = {}
        for channel in channels:
            for target in channel.targets:
                by_platform.setdefault(target.platform, []).append(target)

        tokens = []
        for name, platform in self.platforms.items():
            if name != "telegram" and name not in by_platform:
                continue
            for info in platform.token_info(by_platform.get(name, [])):
                tokens.append({"name": info.name, "ok": info.ok, "message": info.message,
                               "expires_at": info.expires_at.isoformat() if info.expires_at else None})
        self.storage.set_meta_json("token_status", {"checked_at": _utcnow().isoformat(), "tokens": tokens})

        day = now_israel().date().isoformat()
        counted = 0
        for name, targets in by_platform.items():
            platform = self.platforms.get(name)
            if not platform or not platform.available():
                continue
            for target in {t.target_id: t for t in targets if t.enabled}.values():
                try:
                    count = platform.followers(target)
                except Exception as exc:
                    log.warning("Followers %s %s: %s", name, target.target_id, exc)
                    continue
                if count is not None:
                    self.storage.record_followers(day, name, target.target_id, int(count))
                    counted += 1
        return {"tokens": tokens, "followers": counted}

    def _sync_commissions(self, days: int = 30) -> int:
        end = datetime.now()
        start = end - timedelta(days=days)
        orders = []
        for status in ORDER_STATUSES:
            try:
                raw = self.client.orders(start, end, status)
            except AliExpressError as exc:
                self.storage.record_error("commissions", str(exc))
                raise
            orders.extend(o for o in map(parse_order, raw) if o)
        self.storage.upsert_orders(orders)
        self.storage.set_meta("commissions_synced_at", _utcnow().isoformat())
        log.info("Commissions: %d orders synced", len(orders))
        return len(orders)

    def _backup(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "alitrends.db")
            self.storage.backup_to(path)
            with open(path, "rb") as fh:
                data = gzip.compress(fh.read())
        name = f"alitrends-{now_israel():%Y-%m-%d}.db.gz"
        if len(data) > MAX_BACKUP_BYTES:
            self.telegram.notify_admin(f"⚠️ הגיבוי גדול מדי לטלגרם ({len(data) // 1_000_000}MB)")
            return
        self.telegram.send_admin_document(data, name, f"💾 גיבוי יומי ({len(data) // 1024}KB)")


def _product_dict(product: Product) -> dict:
    return {"id": product.product_id, "title": product.title, "image": product.image_url,
            "link": product.promotion_link, "price": str(product.price), "currency": product.currency,
            "discount": product.discount, "rating": product.rating, "sales": product.sales,
            "hot": product.hot, "commission": product.commission_rate}


def setup_logging(log_dir: str, verbose: bool, filename: str = "alitrends.log") -> None:
    os.makedirs(log_dir, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(os.path.join(log_dir, filename), maxBytes=5_000_000,
                                       backupCount=5, encoding="utf-8")
    console = logging.StreamHandler()
    for handler in (file_handler, console):
        handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers[:] = [file_handler, console]
    for noisy in ("urllib3", "httpx", "openai", "TeleBot", "waitress"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="AliTrends deals publisher")
    parser.add_argument("--dry-run", action="store_true", help="print posts instead of publishing")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    parser.add_argument("--limit", type=int, help="only process the first N channels")
    parser.add_argument("--only", help='only channels whose key contains this text, e.g. "Hebrew/main"')
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    secrets = Secrets.from_env()
    setup_logging(secrets.log_dir, args.verbose)
    Bot(secrets, dry_run=args.dry_run).run_forever(once=args.once, limit=args.limit, only=args.only)
