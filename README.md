# Upbit Watch — localhost control panel

A beautiful dark control panel that runs and monitors new-listing detectors across **Upbit,
Binance, Bithumb, and Coinbase** (built from `docs/LOCALHOST_BUILD_PLAN.md`). One FastAPI app
supervises all four detectors and streams live status / listings / logs / price snapshots over
WebSocket, fanning Telegram alerts out to any number of subscribers on manually-assigned delay tiers.

## Run (Windows)

First-time setup (creates `.venv` and installs dependencies):

```bat
setup.bat
```

Then start it either way:

```bat
run.bat                 :: runs in this window (Ctrl+C to stop), opens the dashboard
run-upbit-watch.bat     :: runs in a background window; stop with stop-upbit-watch.bat
```

Or directly:

```bat
.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open **http://localhost:8000**. The detectors auto-arm on launch (`autostart` in `config.json`):
the first run seeds existing markets silently; afterward it alerts only on genuinely new markets.
Dedup (SQLite `state.db`) survives restarts.

## Features
- **Control:** Start / Stop / Restart all four exchange detectors from the top bar, or each one
  independently from the Exchanges panel.
- **Status:** uptime, last poll, latency, markets tracked, polls, errors (live via WebSocket) —
  per exchange.
- **Listings feed + logs:** new markets appear instantly, tagged by exchange, filterable; streaming
  log console.
- **Markets browser:** searchable, exchange-filterable table of all tracked markets.
- **Subscribers:** add/remove any number of Telegram recipients and assign each a delay tier
  (e.g. instant / delayed / free) — no billing, tiers are just a name + seconds you configure.
- **Settings:** per-exchange poll interval + Telegram token/chat ID + tier delays, with a
  "Send test message" button.
- **Phase 0:** on each listing, snapshots Bybit (primary) + Binance (best-effort) prices at
  +0/10/30/60s/5m and charts them. "Simulate listing" exercises the pipeline (SIM-BTC → BTCUSDT).
- **Loop B (Upbit announcements):** polls Upbit's trade-announcement API, classifies new-listing
  notices (both "신규 거래지원 안내" and "마켓 디지털 자산 추가" phrasings; excludes
  delisting/caution), extracts the ticker, and alerts — often *earlier* than the market-list
  engines. Best-effort: Cloudflare may 429 a home IP (handled gracefully — see the Loop B error
  count); tune the interval in Settings.

## Files
`app.py` (FastAPI + WebSocket), `exchanges.py` (exchange registry + parsers), `detector.py`
(generalized new-listing engine), `notice.py` (Upbit Loop B announcement poller), `phase0.py`
(price logger), `notify.py` (Telegram), `broadcast.py` (per-subscriber tiered fan-out),
`subscribers.py` (subscriber CRUD), `storage.py` (schema init/migration), `static/index.html` +
`static/app.js` (dashboard), `config.json`, `.env` (secrets), `tests/` (pytest suite).

## Documentation
Full guides live in [`docs/`](docs/):
- [`SETUP_AND_GUIDE.md`](docs/SETUP_AND_GUIDE.md) — install, Telegram, running, troubleshooting.
- [`HOW_IT_WORKS.md`](docs/HOW_IT_WORKS.md) — architecture, data flow, what each file/function does.
- [`LOCALHOST_BUILD_PLAN.md`](docs/LOCALHOST_BUILD_PLAN.md) — original build plan (historical).
- [`site/`](docs/site/) — the standalone documentation website (open `docs/site/index.html`).

## Reset
```bat
del state.db state.db-shm state.db-wal   :: next Start re-seeds silently from scratch
```

## Deps note
Requires Python 3.11+ on Windows. `setup.bat` creates the virtual environment and installs
`requirements.txt`. To recreate manually:

```bat
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```
