# AliTrends

Publishes curated AliExpress affiliate deals to Telegram channels, Facebook pages, Instagram business
accounts, Threads and Pinterest boards, in six languages (English, Arabic, Portuguese, French, Spanish, Hebrew), and comes
with a private admin panel for managing it all.

## How it works

```
AliExpress Affiliate API ─► sourcing ─► copywriter (OpenAI) ─► render ─► Telegram / Facebook / Instagram / Threads
  product.query              filter +     headline, body,        live price,
  hotproduct.query           score +      hashtags (cached       rating, sales,
  link.generate              de-dupe      per product+lang)      short link
```

1. **Sourcing** (`alitrends/sourcing.py`): for each market (language → ship-to country + currency) and
   category, fetch hot products first, then best sellers; keep only ones that pass the rating / sales /
   discount thresholds and aren't blacklisted; score by discount, demand, rating and commission.
2. **Picking**: rank-weighted random choice among the top 15 the channel hasn't posted recently. The
   link is shortened under the channel's own tracking id (if it has one) so earnings split per channel.
3. **Copy** (`alitrends/copywriter.py`): the model writes only the headline, body and hashtags, as JSON,
   plus any per-language instructions set in the panel. It never writes prices or links.
4. **Render** (`alitrends/render.py`): adds localized price / discount / rating / sales lines within each
   platform's limits (Telegram 1024 with a link button, Threads 500, Instagram 2200, Facebook inline link).
5. **Publish** (`alitrends/platforms/`): each target is isolated; one failure never stops the cycle.
6. **Shabbat** (`alitrends/schedule.py`): no posting from Friday 14:00 to Saturday 21:30 **Israel time**,
   checked before every post (manual and scheduled posts wait too).

### Channels, targets and settings

A **channel** is a language + category (e.g. Hebrew / main). Each channel has **targets**: a Telegram
chat, a Facebook page, an Instagram account, a Threads account. Every target of a channel gets the same
product in a cycle. Per channel you can set a tracking id, "every N cycles" and active hours (in the
audience's timezone).

Channels, targets and tuning live in the SQLite database and are edited from the panel; the bot re-reads
them every cycle. Secrets stay in `.env`. On the first start after upgrading, channels and tuning found in
the old `.env` keys (`"Hebrew main"`, `"Hebrew Facebook main"`, `POST_DELAY_SECONDS`, …) are imported once.

### Pinterest

Create an app at developers.pinterest.com (needs a business account), add the redirect URI shown in the
panel's settings page (`https://<server>.<tailnet>.ts.net/pinterest/callback`), put `PINTEREST_APP_ID`
and `PINTEREST_APP_SECRET` in `.env`, restart both services, then click "connect" in the settings page.
Each target is a board (by name or id). The headline becomes the pin title and the product link is the
pin's link. New Pinterest apps start with trial access; request standard access so pins are public.

### Adding a social network

Write a `Platform` subclass in `alitrends/platforms/<name>.py` (publish, and optionally prepare /
followers / token_info / resolve_target), register it in `alitrends/platforms/__init__.py`, and give it a
`PostStyle` in `render.STYLES`. The panel, reports and stats pick it up automatically.

## The admin panel

`python -m alitrends.panel` serves the panel (default `127.0.0.1:8080`). It has:

- **Dashboard**: bot state and heartbeat, next cycle, posts in the last 24h and per day, token health with
  expiry dates, recent errors and posts, global pause / resume.
- **Channels**: add / edit / disable / delete channels and their targets; preview the next post; post now.
- **Manual post**: paste an AliExpress link, pick channels, post now or at a scheduled time.
- **Settings**: timing, product filters, alerts, per-language minimum discount and AI instructions.
- **Blacklist**: products and keywords that are never posted.
- **Commissions**: affiliate orders per tracking id (synced daily; needs `order.list` access).
- **Followers**: daily follower counts per target, with a chart.
- **Logs**, **Audit log** (every change, with one-click restore), **Account** (password, log out everywhere).

Security: username + password (hashed), then a one-time code sent to the admin Telegram chat; lockout after
repeated failures; CSRF protection; sessions expire after 12 hours. The panel should only be reachable
through Tailscale (below), never directly from the internet.

The panel doesn't publish by itself: previews, "post now", manual posts, commission syncs and status
checks are queued as jobs that the bot picks up within seconds.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then fill in the keys
python -m alitrends.panel set-password
```

## Running

```bash
python main.py --dry-run --once --only "Hebrew/main"   # print the posts, publish nothing
python main.py --once                                 # one real cycle, then exit
python main.py                                        # run forever
python -m alitrends.panel                             # the admin panel
```

| Flag | Meaning |
|---|---|
| `--dry-run` | print posts instead of publishing (still calls AliExpress and OpenAI) |
| `--once` | run a single cycle and exit |
| `--only TEXT` | only channels whose key contains TEXT, e.g. `Hebrew/`, `/main` |
| `--limit N` | only the first N channels |
| `-v` | debug logging |

Logs go to `logs/alitrends.log` and `logs/panel.log` (rotated). Data lives in `alitrends.db` (SQLite).

## Deploying on Ubuntu

### 1. Code and dependencies

```bash
cd /root/ali/AliTrends
git pull
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m alitrends.panel set-password
```

### 2. Services

Copy the unit files from `deploy/` (adjust the paths if the project lives elsewhere):

```bash
cp deploy/alitrends.service deploy/alitrends-panel.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now alitrends alitrends-panel
systemctl restart alitrends            # pick up the new code
journalctl -u alitrends -f             # bot log
journalctl -u alitrends-panel -f       # panel log
```

### 3. Private access with Tailscale

The panel listens on `127.0.0.1:8080` only. Tailscale publishes it over HTTPS to your own devices:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up                  # log in with the same account as on your phone/computer
tailscale serve --bg 8080     # https://<server-name>.<tailnet>.ts.net -> 127.0.0.1:8080
tailscale serve status
```

If `tailscale serve` asks to enable HTTPS certificates, follow the link it prints (one click in the
Tailscale admin console). Install the Tailscale app on your phone and computer, log in with the same
account, and open the `https://…ts.net` address. Nothing is opened on the server's public IP.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```
