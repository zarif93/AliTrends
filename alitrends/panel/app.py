"""The admin panel: a small Flask app over the same SQLite database the bot uses.

The panel never publishes by itself. Anything that needs the bot's API clients (preview, "post now",
manual posts, commission sync, status refresh) is queued as a job that the bot picks up within seconds.
"""
from __future__ import annotations

import hmac
import os
import re
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for

from ..aliexpress import product_id_from_url
from ..config import (CHANNEL_CATEGORIES, MAIN, MARKETS, SETTING_FIELDS, Secrets, parse_hhmm,
                      parse_setting)
from ..platforms import PLATFORM_CLASSES, Platform, PublishError, TelegramPublisher, build_platforms, platform_label
from ..schedule import ISRAEL, is_shabbat, now_israel
from ..storage import Storage, StorageError
from . import auth

TRACKING_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
LOG_FILES = ("alitrends.log", "panel.log")
LOG_TAIL_BYTES = 2_000_000

CATEGORY_LABELS = {
    MAIN: "ראשי (כל הקטגוריות)",
    "Electronics & Technology": "אלקטרוניקה וטכנולוגיה",
    "Fashion & Accessories": "אופנה ואקססוריז",
    "Home & Living": "בית ומגורים",
    "Sports & Outdoor": "ספורט ושטח",
    "Toys & Kids": "צעצועים וילדים",
    "Automotive & Motorcycle": "רכב ואופנועים",
    "Beauty & Health": "יופי ובריאות",
    "Office & Education": "משרד ולימודים",
    "Security & Tools": "אבטחה וכלי עבודה",
}
LANGUAGE_LABELS = {"English": "אנגלית", "Arabic": "ערבית", "Portuguese": "פורטוגזית", "French": "צרפתית",
                   "Spanish": "ספרדית", "Hebrew": "עברית"}
STATE_LABELS = {"running": "מפרסם", "sleeping": "ממתין לסבב הבא", "paused": "מושהה", "shabbat": "שבת"}
JOB_LABELS = {"preview": "תצוגה מקדימה", "post_channel": "פרסום עכשיו", "manual_post": "פרסום ידני",
              "sync_commissions": "סנכרון עמלות", "refresh_status": "בדיקת טוקנים ומנויים"}
JOB_STATUS_LABELS = {"pending": "ממתין", "running": "רץ", "done": "הושלם", "failed": "נכשל", "cancelled": "בוטל"}


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def create_app(secrets_: Secrets, *, telegram: TelegramPublisher | None = None,
               platforms: dict[str, Platform] | None = None) -> Flask:
    app = Flask(__name__)
    db_path = secrets_.db_path

    boot = Storage(db_path)
    secret_key = boot.get_meta("panel_secret_key")
    if not secret_key:
        secret_key = secrets.token_hex(32)
        boot.set_meta("panel_secret_key", secret_key)
    boot.close()

    app.config.update(
        SECRET_KEY=secret_key,
        SESSION_COOKIE_NAME="alitrends_panel",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("PANEL_COOKIE_SECURE", "1") != "0",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=1_000_000,
    )
    telegram = telegram or TelegramPublisher(secrets_.telegram_token, secrets_.admin_chat_id)
    # The panel only uses platforms to validate new targets; rotated tokens are saved by the bot.
    platforms = platforms or build_platforms(secrets_, lambda *_: None, telegram)
    app.extensions["alitrends"] = {"secrets": secrets_, "telegram": telegram, "platforms": platforms}

    # --- request plumbing ----------------------------------------------------

    @app.before_request
    def _open_storage():
        g.storage = Storage(db_path)
        return auth.guard()

    @app.teardown_request
    def _close_storage(_exc):
        storage = g.pop("storage", None)
        if storage:
            storage.close()

    @app.after_request
    def _security_headers(response):
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' https: data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'")
        response.headers["Cache-Control"] = "no-store"
        return response

    # Globals (unlike context) are also visible inside imported macros.
    app.jinja_env.globals.update(csrf_token=auth.csrf_token, platform_label=platform_label)

    @app.context_processor
    def _template_globals():
        return {
            "PLATFORMS": PLATFORM_CLASSES,
            "LANGUAGE_LABELS": LANGUAGE_LABELS,
            "CATEGORY_LABELS": CATEGORY_LABELS,
            "JOB_LABELS": JOB_LABELS,
            "JOB_STATUS_LABELS": JOB_STATUS_LABELS,
            "logged_in": bool(session.get("user")),
        }

    @app.template_filter("il")
    def _il(value: str | datetime | None, fmt: str = "%d/%m %H:%M") -> str:
        moment = value if isinstance(value, datetime) else _parse_time(value)
        return moment.astimezone(ISRAEL).strftime(fmt) if moment else "—"

    @app.template_filter("ago")
    def _ago(value: str | None) -> str:
        moment = _parse_time(value)
        if not moment:
            return "אף פעם"
        seconds = int((datetime.now(timezone.utc) - moment).total_seconds())
        if seconds < 0:
            seconds = -seconds
            prefix = "בעוד"
        else:
            prefix = "לפני"
        if seconds < 60:
            return f"{prefix} {seconds} שניות"
        if seconds < 3600:
            return f"{prefix} {seconds // 60} דקות"
        if seconds < 86400:
            return f"{prefix} {seconds // 3600} שעות"
        return f"{prefix} {seconds // 86400} ימים"

    @app.template_filter("channel_label")
    def _channel_label(key: str) -> str:
        language, _, category = key.partition("/")
        return f"{LANGUAGE_LABELS.get(language, language)} · {CATEGORY_LABELS.get(category, category)}"

    @app.errorhandler(400)
    def _bad_request(error):
        return render_template("error.html", message=getattr(error, "description", "בקשה לא תקינה")), 400

    def storage() -> Storage:
        return g.storage

    def back(default: str = "dashboard", **kwargs):
        target = request.form.get("next") or request.args.get("next")
        if target and target.startswith("/") and not target.startswith("//"):
            return redirect(target)
        return redirect(url_for(default, **kwargs))

    # --- auth ------------------------------------------------------------------

    @app.route("/login", methods=["GET", "POST"])
    def login():
        s = storage()
        if not auth.credentials_configured(s):
            return render_template("login.html", not_configured=True)
        if request.method == "POST":
            ip = request.remote_addr or "?"
            if auth.locked_out(s, ip):
                flash("יותר מדי ניסיונות כושלים. נסו שוב בעוד 15 דקות.", "error")
                return render_template("login.html"), 429
            username = request.form.get("username", "")
            if not auth.check_credentials(s, username, request.form.get("password", "")):
                s.record_login(ip, False)
                flash("שם משתמש או סיסמה שגויים", "error")
                return render_template("login.html"), 401
            code = auth.start_code_challenge(username.strip())
            try:
                telegram.send_admin_strict(f"🔐 קוד כניסה לפאנל AliTrends: {code}\n"
                                           f"תקף ל-5 דקות. אם לא אתם ניסיתם להיכנס, החליפו סיסמה.")
            except PublishError:
                session.clear()
                flash("לא הצלחתי לשלוח קוד לטלגרם. בדקו את BOT_TOKEN ו-ADMIN_CHAT_ID.", "error")
                return render_template("login.html"), 503
            session["next"] = request.args.get("next") or ""
            return redirect(url_for("login_code"))
        return render_template("login.html")

    @app.route("/login/code", methods=["GET", "POST"])
    def login_code():
        if not session.get("pending"):
            return redirect(url_for("login"))
        if request.method == "POST":
            s = storage()
            ip = request.remote_addr or "?"
            next_url = session.get("next") or ""
            result = auth.verify_code(s, request.form.get("code", ""))
            if result == "ok":
                s.record_login(ip, True)
                s.set_meta_json("last_login", {"at": datetime.now(timezone.utc).isoformat(), "ip": ip})
                if next_url.startswith("/") and not next_url.startswith("//"):
                    return redirect(next_url)
                return redirect(url_for("dashboard"))
            s.record_login(ip, False)
            if result == "restart":
                flash("הקוד פג או שהיו יותר מדי ניסיונות. התחברו מחדש.", "error")
                return redirect(url_for("login"))
            flash("קוד שגוי", "error")
        return render_template("login_code.html")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/account", methods=["GET", "POST"])
    def account():
        s = storage()
        if request.method == "POST":
            action = request.form.get("action")
            if action == "logout_all":
                session["epoch"] = auth.bump_epoch(s)
                flash("כל שאר החיבורים נותקו", "ok")
                return redirect(url_for("account"))
            if not auth.check_credentials(s, session["user"], request.form.get("current", "")):
                flash("הסיסמה הנוכחית שגויה", "error")
            elif request.form.get("new") != request.form.get("confirm"):
                flash("הסיסמאות החדשות לא תואמות", "error")
            else:
                try:
                    username = request.form.get("username") or session["user"]
                    auth.set_credentials(s, username, request.form.get("new", ""))
                    session["user"] = username.strip()
                    session["epoch"] = s.get_meta("session_epoch")
                    flash("הסיסמה עודכנה. חיבורים אחרים נותקו.", "ok")
                except ValueError as exc:
                    flash(str(exc), "error")
            return redirect(url_for("account"))
        return render_template("account.html", last_login=s.get_meta_json("last_login"))

    # --- dashboard ---------------------------------------------------------------

    @app.get("/")
    def dashboard():
        s = storage()
        tuning = s.tuning()
        now = datetime.now(timezone.utc)
        heartbeat = s.get_meta("heartbeat")
        beat = _parse_time(heartbeat)
        alive = bool(beat and now - beat < timedelta(minutes=tuning.watchdog_minutes))
        day_ago = now - timedelta(hours=24)
        by_platform = s.stats_since(day_ago)
        daily = s.daily_counts(14)
        chart = _daily_chart(daily)
        channels = s.channels()
        tokens = s.get_meta_json("token_status", {})
        for token in tokens.get("tokens", []):
            expires = _parse_time(token.get("expires_at"))
            token["expiring"] = bool(expires and expires - now < timedelta(days=tuning.token_warning_days))
        return render_template(
            "dashboard.html",
            tuning=tuning, state=s.get_meta_json("bot_state", {}), last_cycle=s.get_meta_json("last_cycle"),
            heartbeat=heartbeat, alive=alive, state_labels=STATE_LABELS,
            by_platform=by_platform, by_language=s.stats_by_language_since(day_ago),
            total=sum(by_platform.values()), errors_24h=s.errors_since(day_ago),
            errors=s.recent_errors(8), publications=s.recent_publications(10),
            tokens=tokens, chart=chart, shabbat=is_shabbat(now_israel()),
            channel_count=len(channels), live_channels=sum(1 for c in channels if c.enabled and c.live_targets),
            pending_jobs=[j for j in s.jobs(limit=20) if j["status"] in ("pending", "running")],
            now=now,
        )

    @app.post("/pause")
    def pause():
        paused = request.form.get("paused") == "1"
        storage().set_settings({"paused": paused})
        flash("הבוט הושהה. הוא יסיים את הפוסט הנוכחי ויעצור." if paused else "הבוט ממשיך לפרסם", "ok")
        return back()

    @app.post("/status/refresh")
    def refresh_status():
        storage().add_job("refresh_status", {})
        flash("נשלח לבוט. הנתונים יתעדכנו בעוד דקה בערך.", "ok")
        return back()

    # --- channels ------------------------------------------------------------------

    @app.get("/channels")
    def channels():
        s = storage()
        by_language: dict[str, list] = defaultdict(list)
        for channel in s.channels():
            by_language[channel.language].append(channel)
        week = s.stats_by_channel_since(datetime.now(timezone.utc) - timedelta(days=7))
        return render_template("channels.html", by_language=dict(by_language), week=week)

    @app.route("/channels/new", methods=["GET", "POST"])
    def channel_new():
        s = storage()
        existing = {(c.language, c.category) for c in s.channels()}
        if request.method == "POST":
            language, category = request.form.get("language"), request.form.get("category")
            if language not in MARKETS or category not in CHANNEL_CATEGORIES:
                flash("שפה או קטגוריה לא תקינות", "error")
            else:
                try:
                    channel_id = s.create_channel(language, category, enabled=True)
                    flash("הערוץ נוצר. עכשיו הוסיפו לו יעדים (טלגרם, פייסבוק...)", "ok")
                    return redirect(url_for("channel_edit", channel_id=channel_id))
                except StorageError as exc:
                    flash(str(exc), "error")
        return render_template("channel_new.html", markets=MARKETS, categories=CHANNEL_CATEGORIES,
                               existing=existing)

    def _channel(channel_id: int):
        channel = storage().channel(channel_id)
        if not channel:
            abort(404)
        return channel

    @app.route("/channels/<int:channel_id>", methods=["GET", "POST"])
    def channel_edit(channel_id: int):
        s = storage()
        channel = _channel(channel_id)
        if request.method == "POST":
            try:
                s.update_channel(channel_id, **_channel_form())
                flash("נשמר. ייכנס לתוקף מהסבב הבא.", "ok")
            except (ValueError, StorageError) as exc:
                flash(str(exc), "error")
            return redirect(url_for("channel_edit", channel_id=channel_id))
        jobs = [j for j in s.jobs(("preview", "post_channel"), limit=100)
                if j["payload"].get("channel_id") == channel_id][:5]
        return render_template("channel_edit.html", channel=channel, jobs=jobs,
                               week=s.stats_by_channel_since(datetime.now(timezone.utc) - timedelta(days=7))
                               .get(channel.key, {}),
                               default_tracking=secrets_.ali_tracking_id)

    def _channel_form() -> dict[str, Any]:
        form = request.form
        tracking = form.get("tracking_id", "").strip() or None
        if tracking and not TRACKING_ID.match(tracking):
            raise ValueError("Tracking ID יכול להכיל רק אותיות באנגלית, ספרות, _ ו--")
        try:
            every = int(form.get("every_n_cycles") or 1)
        except ValueError:
            raise ValueError("תדירות לא תקינה") from None
        if not 1 <= every <= 48:
            raise ValueError("תדירות: בין 1 ל-48 סבבים")
        start, end = form.get("active_from", "").strip() or None, form.get("active_to", "").strip() or None
        if bool(start) != bool(end):
            raise ValueError("שעות פעילות: צריך למלא גם התחלה וגם סוף, או להשאיר את שניהם ריקים")
        if start and end:
            try:
                parse_hhmm(start), parse_hhmm(end)
            except ValueError:
                raise ValueError("שעות פעילות לא תקינות") from None
            if start == end:
                raise ValueError("שעת ההתחלה והסיום זהות")
        return {"enabled": form.get("enabled") == "1", "tracking_id": tracking, "every_n_cycles": every,
                "active_from": start, "active_to": end}

    @app.post("/channels/<int:channel_id>/toggle")
    def channel_toggle(channel_id: int):
        channel = _channel(channel_id)
        storage().update_channel(channel_id, enabled=not channel.enabled)
        flash(f"{_channel_label(channel.key)}: {'כובה' if channel.enabled else 'הופעל'}", "ok")
        return back("channels")

    @app.post("/channels/<int:channel_id>/delete")
    def channel_delete(channel_id: int):
        channel = _channel(channel_id)
        storage().delete_channel(channel_id)
        flash(f"הערוץ {_channel_label(channel.key)} נמחק. אפשר לשחזר מיומן השינויים.", "ok")
        return redirect(url_for("channels"))

    @app.post("/channels/<int:channel_id>/targets")
    def target_add(channel_id: int):
        s = storage()
        _channel(channel_id)
        platform_name = request.form.get("platform", "")
        platform = platforms.get(platform_name)
        target_id = request.form.get("target_id", "").strip()
        secret = request.form.get("secret", "").strip() or None
        label = request.form.get("label", "").strip()
        if not platform:
            flash("רשת לא מוכרת", "error")
            return redirect(url_for("channel_edit", channel_id=channel_id))
        if platform.needs_target_secret and not secret:
            flash(f"ל-{platform.label} צריך טוקן", "error")
            return redirect(url_for("channel_edit", channel_id=channel_id))
        if request.form.get("skip_check") != "1":
            try:
                target_id, found_label = platform.resolve_target(target_id, secret)
                label = label or found_label
            except PublishError as exc:
                flash(f"הבדיקה נכשלה: {exc}. אפשר לסמן 'שמור בלי בדיקה'.", "error")
                return redirect(url_for("channel_edit", channel_id=channel_id))
        if not target_id:
            flash("חסר מזהה יעד", "error")
            return redirect(url_for("channel_edit", channel_id=channel_id))
        try:
            s.create_target(channel_id, platform_name, target_id, label=label, secret=secret)
            flash(f"נוסף יעד {platform.label} {label or target_id}", "ok")
        except StorageError as exc:
            flash(str(exc), "error")
        return redirect(url_for("channel_edit", channel_id=channel_id))

    def _target(target_id: int):
        target = storage().target(target_id)
        if not target:
            abort(404)
        return target

    @app.post("/targets/<int:target_id>/toggle")
    def target_toggle(target_id: int):
        target = _target(target_id)
        storage().update_target(target_id, enabled=not target.enabled)
        return redirect(url_for("channel_edit", channel_id=target.channel_id))

    @app.post("/targets/<int:target_id>/secret")
    def target_secret(target_id: int):
        target = _target(target_id)
        secret = request.form.get("secret", "").strip()
        if not secret:
            flash("הטוקן ריק", "error")
        else:
            storage().update_target(target_id, secret=secret, secret_expires_at=None)
            flash("הטוקן עודכן", "ok")
        return redirect(url_for("channel_edit", channel_id=target.channel_id))

    @app.post("/targets/<int:target_id>/delete")
    def target_delete(target_id: int):
        target = _target(target_id)
        storage().delete_target(target_id)
        flash("היעד נמחק", "ok")
        return redirect(url_for("channel_edit", channel_id=target.channel_id))

    @app.post("/channels/<int:channel_id>/preview")
    def channel_preview(channel_id: int):
        _channel(channel_id)
        return jsonify(job_id=storage().add_job("preview", {"channel_id": channel_id}))

    @app.post("/channels/<int:channel_id>/post-now")
    def channel_post_now(channel_id: int):
        _channel(channel_id)
        return jsonify(job_id=storage().add_job("post_channel", {"channel_id": channel_id}))

    @app.get("/jobs/<int:job_id>.json")
    def job_json(job_id: int):
        job = storage().job(job_id)
        if not job:
            abort(404)
        heartbeat = _parse_time(storage().get_meta("heartbeat"))
        bot_alive = bool(heartbeat and datetime.now(timezone.utc) - heartbeat < timedelta(minutes=5))
        return jsonify(status=job["status"], status_label=JOB_STATUS_LABELS[job["status"]],
                       result=job["result"], kind=job["kind"], bot_alive=bot_alive)

    @app.post("/jobs/<int:job_id>/cancel")
    def job_cancel(job_id: int):
        flash("בוטל" if storage().cancel_job(job_id) else "כבר לא ניתן לבטל", "ok")
        return back("manual")

    # --- settings ------------------------------------------------------------------

    @app.route("/settings", methods=["GET", "POST"])
    def settings_page():
        s = storage()
        if request.method == "POST":
            values: dict[str, Any] = {}
            errors = []
            for spec in SETTING_FIELDS:
                try:
                    values[spec.key] = parse_setting(spec, request.form.get(spec.key))
                except ValueError as exc:
                    errors.append(str(exc))
            for language in MARKETS:
                raw = request.form.get(f"min_discount.{language}", "").strip()
                try:
                    value = int(raw)
                    if not 0 <= value <= 90:
                        raise ValueError
                    values[f"min_discount.{language}"] = value
                except ValueError:
                    errors.append(f"הנחה מינימלית ל{LANGUAGE_LABELS[language]}: מספר בין 0 ל-90")
                prompt = request.form.get(f"prompt.{language}", "").strip()
                if len(prompt) > 1500:
                    errors.append(f"הנחיות ל{LANGUAGE_LABELS[language]}: עד 1500 תווים")
                values[f"prompt.{language}"] = prompt
            if errors:
                for error in errors:
                    flash(error, "error")
            else:
                changed = s.set_settings(values)
                flash(f"נשמרו {len(changed)} שינויים. ייכנסו לתוקף מהסבב הבא." if changed else "אין שינויים", "ok")
            return redirect(url_for("settings_page"))
        tuning = s.tuning()
        groups: dict[str, list] = defaultdict(list)
        for spec in SETTING_FIELDS:
            groups[spec.group].append(spec)
        pin = platforms.get("pinterest")
        pinterest = {"configured": pin.configured, "user": pin.connected_as(),
                     "redirect_uri": _public_url(url_for("pinterest_callback"))} if pin else None
        return render_template("settings.html", tuning=tuning, groups=groups, markets=MARKETS,
                               value=lambda key: getattr(tuning, key), pinterest=pinterest)

    # --- connected accounts (OAuth) ------------------------------------------------------

    def _public_url(path: str) -> str:
        """The panel's address as the browser sees it (behind `tailscale serve` that's https://….ts.net)."""
        base = os.getenv("PANEL_PUBLIC_URL", "").rstrip("/")
        if not base:
            host = request.headers.get("X-Forwarded-Host") or request.host
            local = host.split(":")[0] in ("127.0.0.1", "localhost")
            base = f"{'http' if local else 'https'}://{host}"
        return base + path

    def _pinterest():
        pin = platforms.get("pinterest")
        if not pin or not pin.configured:
            abort(400, "חסרים PINTEREST_APP_ID ו-PINTEREST_APP_SECRET בקובץ .env")
        return pin

    @app.get("/pinterest/connect")
    def pinterest_connect():
        pin = _pinterest()
        session["pinterest_state"] = secrets.token_urlsafe(16)
        return redirect(pin.authorize_url(_public_url(url_for("pinterest_callback")), session["pinterest_state"]))

    @app.get("/pinterest/callback")
    def pinterest_callback():
        pin = _pinterest()
        expected = session.pop("pinterest_state", None) or ""
        if request.args.get("error"):
            flash(f"פינטרסט סירב: {request.args.get('error_description') or request.args['error']}", "error")
        elif not expected or not hmac.compare_digest(expected, request.args.get("state", "")):
            flash("החיבור לא הושלם (בקשה לא תקינה או ישנה). נסו שוב.", "error")
        else:
            try:
                username = pin.connect(request.args.get("code", ""), _public_url(url_for("pinterest_callback")))
                flash(f"חשבון הפינטרסט @{username} חובר. עכשיו אפשר להוסיף לוחות כיעדים בערוצים.", "ok")
            except PublishError as exc:
                flash(f"החיבור נכשל: {exc}", "error")
        return redirect(url_for("settings_page") + "#pinterest")

    @app.post("/pinterest/disconnect")
    def pinterest_disconnect():
        _pinterest().disconnect()
        flash("חשבון הפינטרסט נותק", "ok")
        return redirect(url_for("settings_page") + "#pinterest")

    # --- blacklist -------------------------------------------------------------------

    @app.route("/blacklist", methods=["GET", "POST"])
    def blacklist():
        s = storage()
        if request.method == "POST":
            kind, value = request.form.get("kind", ""), request.form.get("value", "").strip()
            if kind == "product" and not value.isdigit():
                value = product_id_from_url(value) or ""
                if not value:
                    flash("לא זיהיתי מזהה מוצר בקישור", "error")
                    return redirect(url_for("blacklist"))
            try:
                s.add_blacklist(kind, value, request.form.get("note", "").strip())
                flash("נוסף לרשימה השחורה", "ok")
            except StorageError as exc:
                flash(str(exc), "error")
            return redirect(url_for("blacklist"))
        return render_template("blacklist.html", items=s.blacklist())

    @app.post("/blacklist/<int:item_id>/delete")
    def blacklist_delete(item_id: int):
        storage().remove_blacklist(item_id)
        flash("הוסר", "ok")
        return redirect(url_for("blacklist"))

    # --- manual posts --------------------------------------------------------------------

    @app.route("/manual", methods=["GET", "POST"])
    def manual():
        s = storage()
        channels = s.channels()
        if request.method == "POST":
            url = request.form.get("url", "").strip()
            channel_ids = [int(c) for c in request.form.getlist("channel_ids") if c.isdigit()]
            when = request.form.get("when", "").strip()
            if not url or not channel_ids:
                flash("צריך קישור ולפחות ערוץ אחד", "error")
                return redirect(url_for("manual"))
            run_at = None
            if when:
                try:
                    run_at = datetime.fromisoformat(when).replace(tzinfo=ISRAEL)
                except ValueError:
                    flash("תאריך לא תקין", "error")
                    return redirect(url_for("manual"))
            product_id = url if url.isdigit() else product_id_from_url(url)
            if not product_id:
                flash("לא זיהיתי מוצר בקישור. הדביקו קישור לדף מוצר באליאקספרס.", "error")
                return redirect(url_for("manual"))
            s.add_job("manual_post", {"url": url, "product_id": product_id, "channel_ids": channel_ids}, run_at)
            flash("תוזמן" if run_at else "נשלח לבוט. יפורסם תוך כמה שניות.", "ok")
            return redirect(url_for("manual"))
        keys = {c.id: c.key for c in channels}
        return render_template("manual.html", channels=channels, jobs=s.jobs(("manual_post",), limit=50),
                               keys=keys, shabbat=is_shabbat(now_israel()))

    # --- commissions --------------------------------------------------------------------

    @app.route("/commissions", methods=["GET", "POST"])
    def commissions():
        s = storage()
        if request.method == "POST":
            s.add_job("sync_commissions", {})
            flash("הסנכרון נשלח לבוט", "ok")
            return redirect(url_for("commissions"))
        days = request.args.get("days", 30, type=int)
        since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))
        channels_by_tracking: dict[str, list[str]] = defaultdict(list)
        for channel in s.channels():
            channels_by_tracking[channel.tracking_id or secrets_.ali_tracking_id].append(channel.key)
        return render_template("commissions.html", rows=s.commission_by_tracking(since), days=days,
                               orders=s.recent_orders(50), channels_by_tracking=channels_by_tracking,
                               default_tracking=secrets_.ali_tracking_id,
                               synced_at=s.get_meta("commissions_synced_at"))

    # --- followers -----------------------------------------------------------------------

    @app.get("/followers")
    def followers():
        s = storage()
        names: dict[tuple[str, str], list[str]] = defaultdict(list)
        labels: dict[tuple[str, str], str] = {}
        for channel in s.channels():
            for target in channel.targets:
                names[(target.platform, target.target_id)].append(channel.key)
                labels[(target.platform, target.target_id)] = target.label
        series: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
        for row in s.follower_history(90):
            series[(row["platform"], row["target_id"])].append((row["day"], row["count"]))
        rows = []
        for key, points in series.items():
            counts = dict(points)
            latest_day, latest = points[-1]
            day = datetime.fromisoformat(latest_day)

            def delta(days: int) -> int | None:
                before = counts.get((day - timedelta(days=days)).date().isoformat())
                return latest - before if before is not None else None

            rows.append({"platform": key[0], "target_id": key[1], "label": labels.get(key, ""),
                         "channels": names.get(key, []), "latest": latest, "day": latest_day,
                         "d7": delta(7), "d30": delta(30)})
        rows.sort(key=lambda r: -r["latest"])
        days = sorted({d for pts in series.values() for d, _ in pts})
        short = {d: datetime.fromisoformat(d).strftime("%d/%m") for d in days}
        chart = {"labels": [short[d] for d in days],
                 "series": [{"name": f"{platform_label(k[0])} {labels.get(k) or k[1]}",
                             "points": {short[d]: n for d, n in v}} for k, v in series.items()]}
        return render_template("followers.html", rows=rows, chart=chart)

    # --- logs -------------------------------------------------------------------------------

    @app.get("/logs")
    def logs():
        name = request.args.get("file", LOG_FILES[0])
        if name not in LOG_FILES:
            abort(404)
        level = request.args.get("level", "")
        query = request.args.get("q", "").strip().lower()
        path = os.path.join(secrets_.log_dir, name)
        lines: list[str] = []
        if os.path.exists(path):
            with open(path, "rb") as fh:
                fh.seek(max(0, os.path.getsize(path) - LOG_TAIL_BYTES))
                lines = fh.read().decode("utf-8", "replace").splitlines()[1:]
        levels = {"warning": (" WARNING ", " ERROR ", " CRITICAL "), "error": (" ERROR ", " CRITICAL ")}.get(level)
        if levels:
            lines = [ln for ln in lines if any(tag in ln for tag in levels)]
        if query:
            lines = [ln for ln in lines if query in ln.lower()]
        return render_template("logs.html", lines=lines[-500:][::-1], files=LOG_FILES, current=name,
                               level=level, q=request.args.get("q", ""))

    # --- audit --------------------------------------------------------------------------------

    @app.get("/audit")
    def audit():
        entries = storage().audit_log(200)
        for entry in entries:
            entry["changes"] = _diff(entry["before"], entry["after"])
        return render_template("audit.html", entries=entries)

    @app.post("/audit/<int:audit_id>/restore")
    def audit_restore(audit_id: int):
        try:
            flash(storage().restore(audit_id), "ok")
        except StorageError as exc:
            flash(str(exc), "error")
        return redirect(url_for("audit"))

    return app


def _diff(before: dict | None, after: dict | None) -> list[tuple[str, Any, Any]]:
    hidden = {"created_at", "_targets", "id"}
    keys = sorted((set(before or {}) | set(after or {})) - hidden)
    out = []
    for key in keys:
        old, new = (before or {}).get(key), (after or {}).get(key)
        if old != new:
            if key == "secret":
                old, new = ("••••" if old else None), ("••••" if new else None)
            out.append((key, old, new))
    return out


def _daily_chart(rows: list[dict]) -> dict:
    days = sorted({r["day"] for r in rows})
    per_platform: dict[str, dict[str, int]] = defaultdict(dict)
    for r in rows:
        per_platform[r["platform"]][r["day"]] = r["n"]
    return {"labels": [datetime.fromisoformat(d).strftime("%d/%m") for d in days],
            "series": [{"name": platform_label(p), "values": [per_platform[p].get(d, 0) for d in days]}
                       for p in PLATFORM_CLASSES if p in per_platform]}


def _channel_label(key: str) -> str:
    language, _, category = key.partition("/")
    return f"{LANGUAGE_LABELS.get(language, language)} · {CATEGORY_LABELS.get(category, category)}"

