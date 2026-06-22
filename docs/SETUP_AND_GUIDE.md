# Upbit Watch — Setup & User Guide

A localhost control panel that detects **new Upbit listings** the moment they appear and pings
your Telegram. Runs natively on **Windows** for $0, and the same Python code deploys unchanged to a
Seoul VPS later. Companion to `LOCALHOST_BUILD_PLAN.md`.

---

## 1. What it does

Two independent detectors run together and feed one dashboard:

| | Source | Speed | Reliability |
|---|---|---|---|
| **Loop A** | `api.upbit.com/v1/market/all` (the tradable-market list) | polls every **1 s** | **Primary** — rock solid |
| **Loop B** | `api-manager.upbit.com/.../announcements` (the notice feed) | polls every **8 s** | **Early-warning** — best-effort (Cloudflare may 429 a home IP) |

Plus **Phase 0**: on each detected listing it snapshots the coin's price on Bybit (primary) and
Binance (best-effort) at **+0 / 10 / 30 / 60 s / 5 m** and charts it — the dataset that tells you
how big the entry window/pump is for *your* latency.

Both detectors **seed silently on first run** (record what already exists, send nothing), then alert
**only** on genuinely new items. De-duplication lives in SQLite (`state.db`) and **survives restarts**.

---

## 2. Prerequisites

- **Windows 10/11** with **Python 3.11+** — check in a terminal: `py --version`
  (get it from [python.org](https://www.python.org/downloads/) and tick "Add python.exe to PATH").
- A **Telegram bot token + chat ID** (see §4)
- Outbound internet only (no port-forwarding). Upbit + Bybit must be reachable; Binance is optional.

---

## 3. Setup from scratch

Double-click **`setup.bat`** (or run it from a terminal). It creates the virtual environment and
installs the dependencies:

```bat
setup.bat
```

That is equivalent to:

```bat
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
:: (fastapi, uvicorn[standard], aiohttp, python-dotenv)
```

---

## 4. Telegram setup

1. In Telegram, message **@BotFather** → `/newbot` → copy the **token** (`123456:ABC...`).
2. Send your new bot any message, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy your numeric **chat id**.
3. Put them in `.env` (in the project folder):
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_CHAT_ID=987654321
   ```
   …or just paste them into the dashboard **Settings → Telegram** and click **Save**.
4. Verify with **Settings → Send test message** (you should get a ✅ message instantly).

`.env` is git-ignored — never commit it.

---

## 5. Running

Easiest — double-click one of these:

| Script | What it does |
|---|---|
| **`run.bat`** | Runs in the current window (Ctrl+C to stop) and opens the dashboard. |
| **`run-upbit-watch.bat`** | Starts the server in a minimized background window, then opens the dashboard. |
| **`stop-upbit-watch.bat`** | Stops the background server started above. |

Or run uvicorn directly:

```bat
.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Then open **http://localhost:8000** in your browser.

**Autostart:** with `"autostart": true` in `config.json` (the default), both detectors arm
themselves the instant the app launches — so after a reboot you just relaunch and it's already
watching. Set it to `false` if you'd rather press **Start** yourself.

> ⚠️ **PC sleep/hibernate pauses the bot.** For long Phase 0 runs: plug in and set Windows power to
> "never sleep". If keeping it awake for weeks is impractical, that's the cue to move to a free
> Oracle Cloud Seoul box.

---

## 6. Using the dashboard

**Top bar** — `Start` / `Stop` / `Restart` control *both* loops together. The green dot pulses when
running; the `live` pill shows the WebSocket is connected; the gear opens **Settings**.

**Status card (left)** — uptime, last poll time, poll latency, markets tracked, total polls, errors.
Below it, a **Loop B** panel shows announcements seen, listings detected, and Loop B errors.

**Tabs**

- **Live** — the new-market feed (Loop A) on the left, a streaming log console on the right. A new
  listing flashes in and raises a toast.
- **Announce** — the announcement feed (Loop B). Listing notices are highlighted amber with the
  ticker badge + a `listing` tag; delisting/caution notices are shown muted. Click any card to open
  the official Upbit notice.
- **Markets** — searchable table of every tracked market (type e.g. `KRW-` to filter).
- **Phase 0** — the price-snapshot chart. Pick a market, **Refresh**, or hit **Simulate listing** to
  fire a fake `SIM-BTC → BTCUSDT` through the whole pipeline and watch the chart fill.

**Settings (drawer)** — Loop A poll interval, Loop B poll interval, Telegram token/chat id, and the
**Send test message** button.

---

## 7. Alerts you'll receive

- **Loop A (market live):**
  `🚨 UPBIT NEW MARKET: KRW-XXX` + name + UTC timestamp
- **Loop B (announcement):**
  `📢 UPBIT LISTING ANNOUNCEMENT: XXX` + the Korean title + a link to the notice

Loop B usually fires first; Loop A confirms the market is actually tradable.

---

## 8. Configuration reference

`config.json`
| Key | Default | Meaning |
|---|---|---|
| `poll_interval` | `1.0` | Loop A seconds between polls (1/s is within Upbit's limit) |
| `poll_interval_notice` | `8.0` | Loop B seconds between polls (keep ≥3 s to avoid Cloudflare 429) |
| `autostart` | `true` | Arm both loops automatically when the app launches |

`.env` — `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

---

## 9. API reference (all under `http://localhost:8000`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/start` `/api/stop` `/api/restart` | control both loops |
| GET | `/api/status` | full status (incl. `loop_b`) |
| GET | `/api/listings` | detected markets (Loop A) |
| GET | `/api/notices` | announcements (Loop B) |
| GET | `/api/markets` | all tracked markets |
| GET | `/api/logs` | recent log lines |
| GET/PUT | `/api/settings` | read/update intervals + Telegram |
| POST | `/api/telegram/test` | send a test Telegram message |
| POST | `/api/simulate` | dev: fire a fake listing (SIM-BTC) |
| GET | `/api/listings/{market}/snapshots` | Phase 0 price points |
| WS | `/ws` | live status / listing / notice / log / snapshot events |

Interactive docs: `http://localhost:8000/docs`.

---

## 10. Troubleshooting

- **Loop B error count climbing / `HTTP 429`** — the announcement API is Cloudflare-fronted and
  rate-limits residential IPs. Raise **Loop B poll interval** in Settings (e.g. 10–15 s). Loop A is
  unaffected and remains your reliable signal.
- **Phase 0 Binance values are blank** — Binance is intermittently blocked from this network; Bybit
  values still fill in. This is expected and non-fatal.
- **Browser can't reach localhost:8000** — make sure the server window is still running and that it
  bound to `127.0.0.1:8000`.
- **Want a clean slate** — `del state.db state.db-shm state.db-wal` (removes the WAL sidecar files
  too); the next Start re-seeds silently from scratch.
- **Bot went quiet after closing the laptop** — the PC slept and paused the process. Relaunch
  (autostart re-arms it).
- **`'py' is not recognized`** — Python isn't on PATH. Reinstall from python.org with "Add to PATH"
  ticked, or substitute the full path to `python.exe` in `setup.bat`.

---

## 11. Going live (leaving localhost)

Move to a Seoul VM when you start acting on alerts for real or wire auto-trade. Almost nothing
changes — the same Python code copies straight to an Oracle Cloud **Seoul** Always-Free ARM box
(or a ~$5–12/mo Tokyo/Seoul VPS). Add: a `systemd` service (instead of the `.bat` launcher), a
keep-alive cron, a static IP for exchange-key whitelisting, and trade-only API keys (no withdrawal).
The code is OS-agnostic, so there's zero rewrite later.
