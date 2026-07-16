# How Upbit Watch Works — Architecture & Feature Internals

This explains **how every feature actually works under the hood** — the components, the data
flow, and what each file/function does. For install & day-to-day use, see `SETUP_AND_GUIDE.md`.

---

## 1. Big picture

Upbit Watch is **one FastAPI process** that supervises **five independent async detection loops**
(one `DetectorEngine` per exchange — Upbit, Binance, Bithumb, Coinbase — plus Upbit's Loop B
announcement poller) and pushes everything live to a browser dashboard over a WebSocket. Every
alert fans out to a list of Telegram **subscribers**, each on a manually-assigned delay tier.

```
                          ┌─────────────────────────────────── app.py (FastAPI) ───────────────────────────────────┐
                          │                                                                                        │
  Upbit market/all  ──▶  │  DetectorEngine(upbit)   ─┐                                                             │
  Binance exchangeInfo ─▶ │  DetectorEngine(binance) ─┤                                                             │
  Bithumb market/all ──▶  │  DetectorEngine(bithumb) ─┼──▶  EventBus (async pub/sub) ──▶  /ws  ──▶ Browser dashboard│
  Coinbase products ───▶  │  DetectorEngine(coinbase)─┤            │                                   │  (static/*)│
                          │  Loop B: NoticePoller  ────┤            ├──▶ broadcast.fanout() ──▶ every enabled       │
  Upbit announcements ─▶  │  (Upbit only, every 3s)    │            │      subscriber, delayed by tier (notify.py)  │
                          │  Phase 0: Phase0 ──────────┘            └──▶ SQLite state.db (WAL)                     │
  Bybit / Binance   ──▶  │  (on each new listing)                                                                  │
                          │                                                                                        │
                          │  REST: /api/start /stop /restart /status /exchanges /listings /subscribers ...        │
                          └────────────────────────────────────────────────────────────────────────────────────────┘
```

Three facts make the whole thing reliable:

1. **Seed-silent + dedup.** On first run each loop records everything that *already* exists and
   sends nothing. After that it alerts **only** on genuinely new items. The "already seen" set
   lives in SQLite (keyed per exchange), so it **survives restarts** (no false alert storm after
   a reboot).
2. **One `DetectorEngine`, one class, many exchanges.** Only the URL, response parser, headers, and
   poll-interval config key differ per exchange (`exchanges.py`'s `EXCHANGES` registry) — the
   loop/dedup/EventBus/Telegram/Phase0 wiring is written once. `Start`/`Stop`/`Restart` (and
   autostart) control all four exchange engines together; each is an independent `asyncio.Task`
   that can never crash the process (every poll is wrapped in try/except). Per-exchange
   start/stop is also available (`/api/exchanges/{id}/start|stop|restart`).
3. **Fan-out is non-blocking.** `broadcast.fanout()` fires one `asyncio.Task` per enabled
   subscriber with that subscriber's tier delay — a slow (high-delay-tier) subscriber never blocks
   the poll loop, other subscribers, or Phase 0 scheduling.

Optionally, `coinlisting.py`'s `CoinListingFeed` layers a third-party push (WebSocket) signal
source on top of the four poll-based engines — see §4.10.

---

## 2. Components (which file does what)

| File | Role | Key pieces |
|---|---|---|
| `app.py` | FastAPI app: REST + WebSocket, wires everything, autostart | `lifespan`, `/api/*`, `/ws`, `persist_env` |
| `exchanges.py` | Exchange registry + response parsers | `EXCHANGES`, `parse_upbit/binance/bithumb/coinbase` |
| `detector.py` | Generalized new-listing engine (one instance per exchange) + shared `EventBus` | `DetectorEngine`, `EventBus` |
| `notice.py` | **Loop B** announcement poller (Upbit only) | `NoticePoller`, `parse_notice` |
| `phase0.py` | Price-snapshot logger after a listing | `Phase0` |
| `notify.py` | Telegram sender (raw Bot API) | `Telegram` |
| `coinlisting.py` | Optional 3rd-party fast-feed WebSocket client | `CoinListingFeed`, `parse_message` |
| `broadcast.py` | Per-subscriber, per-tier-delay fan-out | `fanout()` |
| `subscribers.py` | Telegram subscriber CRUD + legacy chat_id migration | `list/add/update/remove_subscriber`, `ensure_legacy_migrated` |
| `storage.py` | Schema init + migration to the multi-exchange/subscriber schema | `init_schema` |
| `static/index.html` + `static/app.js` | The dashboard (Tailwind + Alpine.js + Chart.js + WebSocket) | one self-contained page |
| `config.json` | Tunables: poll intervals (per exchange), autostart, subscriber tiers | — |
| `.env` | Secrets: Telegram token + legacy chat id | — |
| `state.db` | SQLite store (WAL): `seen`, `listings`, `notices`, `snapshots`, `subscribers` | — |

---

## 3. The core data flow — "a new coin is detected"

This is the **alert fan-out**, the heart of the system:

```
new market/announcement detected
        │
        ├─▶ write to SQLite (seen/listings or notices)   ← dedup memory, survives restart (per exchange)
        ├─▶ engine.log(...)        ─▶ EventBus "log"      ─▶ dashboard Logs console
        ├─▶ EventBus "listing"/"notice"                   ─▶ dashboard feed + toast
        ├─▶ broadcast.fanout(...)  ─▶ Telegram.send(...)  ─▶ every enabled subscriber, delayed by tier
        └─▶ Phase0.schedule(market, base) (new-listing engines only) ─▶ price snapshots at +0/10/30/60s/5m
```

Everything is decoupled through the **EventBus**: detectors `publish()` events, the WebSocket
endpoint `subscribe()`s and forwards them. Detectors never know about the browser.

---

## 4. Feature internals

### 4.1 New-listing detection, generalized (`exchanges.py` + `detector.py` → `DetectorEngine`)
One `DetectorEngine` instance is constructed per exchange from `exchanges.EXCHANGES` (Upbit,
Binance, Bithumb, Coinbase), passing in that exchange's `market_url`, response `parse_fn`,
`headers`, and `poll_interval` config key — the loop/dedup/alert logic below is written once and
shared by all four:
- `_run()` opens one `aiohttp` session and loops every `poll_interval` (Upbit default **1s**;
  Binance/Bithumb/Coinbase default **5s** — their market-list endpoints are heavier and unproven
  at 1s):
  - GET the exchange's market-list URL, measure latency, run it through `parse_fn` to get a
    normalized `{market, base, display_en, display_kr}` per entry.
  - **First ever run** (no `seen` rows for this exchange): bulk-insert every current market into
    `seen` and log "Seeded N markets silently" — **no alerts**.
  - **Afterwards:** `new = current_markets − seen`. For each new market call `_handle_new()`.
- `_handle_new()` = the fan-out: insert into `seen` + `listings` (tagged with `exchange`), log an
  `alert`, publish a `listing` event (`exchange` in the payload), `broadcast.fanout(...)` the
  Telegram alert to every enabled subscriber, and `phase0.schedule(market, base, exchange=...)`.
- Every poll calls `publish_status()` so the dashboard's uptime/latency/markets tick live — each
  engine's `status()` includes its own `exchange`/`label`, since all four publish `status` events
  on the same shared `EventBus`.
- The whole poll body is wrapped in try/except: a network blip increments `error_count` and is
  logged, but the loop keeps going.
- `simulate_listing()` is a dev hook (`/api/exchanges/{id}/simulate`, or `/api/simulate` as the
  Upbit alias) that forces a fake `SIM-BTC` through `_handle_new()` to exercise the entire
  pipeline (it maps to `BTCUSDT` so Phase 0 gets real prices).
- Bithumb's parser (`parse_bithumb`) assumes the same response shape as Upbit's (Bithumb's public
  API is modeled on Upbit's) but degrades gracefully — logs and skips malformed entries rather
  than raising — since this hasn't been independently verified against live traffic.

### 4.2 Loop B — announcement detection (`notice.py` → `NoticePoller`)
- Polls `https://api-manager.upbit.com/api/v1/announcements?...&category=trade` every
  `poll_interval_notice` (default **3s**) with a **browser `User-Agent`** (the endpoint is behind
  Cloudflare).
- Dedup key is the announcement **`id`**. Same seed-silent-then-alert logic, stored in `notices`.
- `parse_notice(title)` does the classification:
  - **Ticker** = first `(UPPERCASE)` group in the title, e.g. `에스피엑스6900(SPX) …` → `SPX`.
  - **is_listing** = has a ticker **and** the title contains a listing phrase
    (`거래지원`, `디지털 자산 추가`, `신규 상장`, `마켓 추가`) **and** does *not* contain
    `종료`/`폐지`/`유의` (delisting / caution). So `OXT 거래지원 종료` and `KERNEL 거래 유의` are
    correctly **excluded**.
- `_handle()` publishes a `notice` event and, **only for listings**, sends a Telegram alert with a
  link to the official notice.
- Cloudflare `429`s are caught, counted (`error_count`), logged, and the loop continues — Loop A
  stays your reliable signal. `on_tick` (= `engine.publish_status`) refreshes the unified status
  after each notice poll.
- Loop B usually fires **before** Loop A (announced before tradable), so it's the early warning.

### 4.3 Phase 0 — price snapshots (`phase0.py` → `Phase0`)
- `schedule(market, base, exchange=...)` takes the base asset ticker directly (e.g. `"BTC"` →
  `"BTCUSDT"`) rather than deriving it from the market id — that derivation only works for
  Upbit-shaped ids (`KRW-XXX`); Binance ids are already bare symbols (`BTCUSDT`) and Coinbase ids
  are `BTC-USD`, so `DetectorEngine` passes in the already-parsed base asset from `exchanges.py`.
- `_collect()` waits to each offset **+0 / 10 / 30 / 60 s / 5 m** after detection and, at each
  point, fetches the price from **Bybit** (primary) and **Binance** (best-effort) and writes both
  to the `snapshots` table (tagged with `exchange`); also publishes a `snapshot` event for the
  live chart.
- If a source fails (e.g. Binance geo-blocked), that value is stored `NULL` — no crash, the chart
  just shows a gap.
- Purpose: measure how big/fast the post-listing move is **for your latency**, to decide if a VPS
  is worth it.

### 4.4 EventBus + WebSocket (`detector.py` `EventBus`, `app.py` `/ws`)
- `EventBus` is a tiny pub/sub: `subscribe()` returns an `asyncio.Queue`; `publish(event)` does a
  non-blocking `put_nowait` into every subscriber's queue (drops if a slow client's queue is full,
  so one stuck browser can't stall the detectors).
- `/ws` accepts the socket, subscribes, sends an initial `status`, then forwards every event until
  the client disconnects (then it unsubscribes).
- **Event types:** `status`, `log`, `listing`, `notice`, `snapshot`. The dashboard switches on
  `ev.type` to update the right panel.

### 4.5 Telegram + multi-subscriber fan-out (`notify.py`, `broadcast.py`, `subscribers.py`)
- `Telegram.send(text, chat_id=None)` POSTs to the raw Bot API `sendMessage` via `aiohttp`,
  targeting an explicit `chat_id` or falling back to the legacy single `self.chat_id` (used by
  `/api/telegram/test`). One bot token; many recipients.
- **Subscribers** (`subscribers.py`) are rows in the `subscribers` table: `chat_id`, `name`,
  `tier`, `enabled`. No billing — a tier is assigned manually from the Subscribers tab.
  `ensure_legacy_migrated()` seeds the pre-upgrade `.env` `TELEGRAM_CHAT_ID` as the first
  subscriber on first boot, so upgrading doesn't silently stop delivering.
- **Tiers** (`config.json` `subscriber_tiers`, e.g. `{"instant": 0, "delayed": 30, "free": 120}`)
  are just a name → delay-in-seconds mapping, editable from Settings → Tiers.
- `broadcast.fanout(telegram, subscribers, tiers, text, log)` fires one `asyncio.Task` per enabled
  subscriber, each `await`ing its tier's delay before sending — fire-and-forget from the caller's
  side, so a delayed-tier subscriber never blocks detection or other subscribers. Gating
  (`alert_on_listing`/`notice`/`error` + quiet hours, via `alert_allowed()`) happens **once**
  before calling `fanout`; tiers only control delay, never whether an event alerts at all.

### 4.6 Control & lifecycle (`app.py` `lifespan`, `DetectorEngine.start/stop/restart`)
- `start()` launches the engine's poll task and, for the Upbit engine only, `notice_poller.start()`
  too; `stop()` cancels cleanly; `restart()` = stop then start.
- The top-bar `/api/start /stop /restart` control **all four exchange engines together**;
  `/api/exchanges/{id}/start|stop|restart` controls one exchange independently (used by the
  Exchanges sidebar panel).
- **Autostart:** the FastAPI `lifespan` hook starts every engine on boot when `config.autostart`
  is true (default) and stops them all on shutdown — so a reboot + relaunch comes up already
  watching, no manual Start. `storage.init_schema(DB)` runs first, migrating a pre-existing
  single-exchange `state.db` to the multi-exchange schema exactly once.

### 4.7 Persistence & dedup (`state.db`, WAL)
- Four tables (see §5). `seen`/`notices` are the dedup memory; `listings`/`snapshots` are the
  results.
- The DB opens in **WAL mode** with a 3s `busy_timeout`, so the three concurrent writers (Loop A
  1/s, Loop B, Phase 0) never hit "database is locked".
- Because dedup lives on disk, **restarts don't re-alert** — verified: kill the process, relaunch,
  and it does *not* re-seed or re-fire on the 752 existing markets.

### 4.8 Settings (`app.py` `/api/settings`, `persist_env`)
- GET returns current intervals + whether Telegram is configured (never the token).
- PUT updates `poll_interval` / `poll_interval_notice` (written to `config.json`) and Telegram
  creds (written to `.env` via `persist_env`, which upserts keys without clobbering the file).

### 4.9 Dashboard (`static/index.html`)
- One self-contained page: **Tailwind** (CDN) for styling, **Alpine.js** for reactivity,
  **Chart.js** for the Phase 0 chart, **Lucide** for icons. No build step.
- On load it fetches initial state via REST, then opens `/ws` and updates reactively on each
  event (auto-reconnecting if the socket drops).
- Tabs: **Live** (listings feed + log console), **Announce** (Loop B feed, listings highlighted),
  **Markets** (searchable table), **Phase 0** (price chart + Simulate button), **Subscribers**.
  The status sidebar shows Loop A metrics, a Loop B sub-panel, an Exchanges panel, and a Fast Feed
  panel. Settings is a slide-over drawer.

### 4.10 Fast feed — optional 3rd-party WebSocket signal (`coinlisting.py` → `CoinListingFeed`)
- Connects to **both** `wss://tokyo.coinlisting.pro/listings` and
  `wss://seoul.coinlisting.pro/listings` simultaneously (per the provider's own routing guidance:
  Seoul carries Upbit listings, Tokyo carries everything else), only if `COINLISTING_API_KEY` is
  set in `.env` (`configured()` mirrors `Telegram.configured()`'s pattern — never stored in
  `config.json`, so it can't end up committed to git).
- **Schema caveat:** the exact message shape wasn't documented anywhere available at integration
  time (only the provider's marketing/pricing page) — `parse_message()` is a best-effort, defensive
  guess at common field names (`exchange`/`venue`/`source`, `market`/`symbol`/`ticker`/`pair`,
  etc.), tried under several aliases. A message that doesn't parse is skipped, not fatal. **Verify
  against a real message and adjust `parse_message()`'s field-name guesses if they're wrong** — the
  Logs tab will show "Fast feed signal: [...]" lines for every recognized listing, so a feed that's
  connected but never producing signals is the tell that the guessed field names need correcting.
- Each parsed message is routed by its own `exchange` field to the matching `DetectorEngine`
  (`engines[exchange]`) and goes through `DetectorEngine.handle_signal()` — the same dedup (`seen`
  table) and alert/broadcast/Phase0 path a poll-detected listing uses, so whichever source (this
  feed or the exchange's own poller) notices a listing first fires the alert; the other is a no-op.
- Reconnects with exponential backoff (capped at 30s) per socket independently; a parse error or
  dropped connection is logged and counted, never fatal to the process.
- Controlled the same way as the exchange engines: `/api/fastfeed`, `/api/fastfeed/start|stop|restart`,
  and included in the top-bar `/api/start|stop|restart` (all-together) calls.

---

## 5. Storage reference (`state.db`)

| Table | Columns | Purpose |
|---|---|---|
| `seen` | `exchange, market, ts` (PK: `exchange, market`) | Dedup — every market ever observed, per exchange |
| `listings` | `exchange, market, base, english, korean, detected_at` (PK: `exchange, market`) | Detected new markets |
| `notices` | `id, title, ticker, category, is_listing, listed_at, detected_at` | Loop B announcements (Upbit only) |
| `snapshots` | `id, exchange, market, source, t_offset, price, ts` | Phase 0 price points (Bybit/Binance) |
| `subscribers` | `id, chat_id, name, tier, enabled, created_at, updated_at` | Telegram recipients + their delay tier |

`storage.init_schema()` migrates a pre-existing single-exchange `state.db` (no `exchange` column)
to this schema once, tagging all prior rows `exchange='upbit'` and backfilling `base` from the
Upbit-shaped `market` id.

---

## 6. Event reference (WebSocket `/ws`)

| `type` | Emitted when | Payload (`data`) |
|---|---|---|
| `status` | each poll, start/stop, **per exchange** | full status incl. `exchange`, `label`, and (Upbit only) nested `loop_b` |
| `log` | any log line | `{ts, level, msg}` (`level`: info/alert/error) |
| `listing` | any exchange finds a new market | `{exchange, market, base, english, korean, detected_at}` |
| `notice` | Loop B finds a new announcement | `{id, title, ticker, is_listing, listed_at, ...}` |
| `snapshot` | a Phase 0 price point is captured | `{exchange, market, t_offset, bybit, binance, ts}` |

Every exchange's `DetectorEngine` publishes `status` on the same bus — the dashboard routes
non-Upbit `status` events into a separate `exchangeStatuses` map instead of the main status bar
(see `static/app.js` `onEvent`).

---

## 7. API ↔ UI map

| Endpoint | Used by |
|---|---|
| `POST /api/start /stop /restart` | top-bar control buttons (all exchanges) |
| `GET /api/status` | status sidebar (Upbit, + initial load) |
| `GET /api/exchanges` | Exchanges sidebar panel (initial load) |
| `POST /api/exchanges/{id}/start\|stop\|restart` | Exchanges panel per-exchange buttons |
| `POST /api/exchanges/{id}/simulate` | Exchanges panel simulate button |
| `GET /api/fastfeed` | Fast Feed sidebar panel (initial load) |
| `POST /api/fastfeed/start\|stop\|restart` | Fast Feed panel buttons |
| `GET /api/listings?exchange=` | Live tab feed |
| `GET /api/notices` | Announce tab feed |
| `GET /api/markets?exchange=` | Markets tab table |
| `GET /api/logs` | Logs console |
| `GET/PUT /api/settings` | Settings drawer (incl. Tiers section) |
| `GET/POST /api/subscribers`, `PUT/DELETE /api/subscribers/{id}` | Subscribers tab |
| `POST /api/telegram/test` | "Send test message" button |
| `POST /api/simulate` | "Simulate listing" button (Phase 0; Upbit alias for `/api/exchanges/upbit/simulate`) |
| `GET /api/snapshots/markets`, `GET /api/listings/{market}/snapshots?exchange=` | Phase 0 chart |
| `WS /ws` | all live updates |

---

## 8. End-to-end: what happens when Upbit lists a new coin

1. **Loop B** sees the Upbit announcement first (e.g. `신규 거래지원 안내 (XYZ)`), classifies it
   as a listing, extracts `XYZ`, stores it, and fans out `📢 LISTING ANNOUNCEMENT: XYZ` to every
   enabled subscriber (delayed per their tier).
2. Seconds later **Upbit's `DetectorEngine`** sees `KRW-XYZ` appear in `market/all` (and,
   independently, the Binance/Bithumb/Coinbase engines may catch the same coin listing on their
   own exchanges), stores it, fans out `🚨 NEW LISTING: KRW-XYZ`, and triggers **Phase 0**.
3. **Phase 0** snapshots `XYZUSDT` on Bybit/Binance at +0/10/30/60s/5m → the chart fills in.
4. The dashboard shows it all live: cards flash into the feeds (tagged with which exchange),
   a toast pops, logs stream, the chart draws.

---

## 9. Failure modes & resilience

| Situation | Behaviour |
|---|---|
| Exchange/network blip (any of the 4) | poll error logged + counted for that exchange only; its loop continues, others unaffected |
| Cloudflare `429` on Loop B | logged + counted; the other loops unaffected; raise Loop B interval in Settings |
| Binance geo-blocked (Phase 0 price source) | Phase 0 stores `NULL` for Binance; Bybit still recorded |
| Bithumb/Coinbase response shape drifts from what's assumed | parser logs a warning and skips the malformed entry rather than crashing the poll loop |
| A subscriber's delayed-tier alert is slow to send | fire-and-forget `asyncio.Task` per subscriber — never blocks detection or other subscribers |
| Fast feed (coinlisting.pro) drops or won't connect | reconnects with backoff (capped 30s) per socket; other detection sources unaffected |
| Fast feed message doesn't match the guessed schema | `parse_message` returns `None`, message is skipped — no crash, but also no alert from that source (verify field names against a real message) |
| Process restart / reboot | dedup persists (WAL, per exchange) → no re-seed, no false alerts; `state.db` migrated once if upgrading; autostart re-arms |
| PC sleep | process pauses → bot pauses; relaunch (autostart) resumes |
| Slow/stuck browser | its event queue drops overflow; detectors never block |

---

*Generated alongside the graphify knowledge graph (`graphify-out/graph.html`), which maps these
same components as 13 communities with `REST API`, `DetectorEngine`, and `NoticePoller` as the
most-connected hubs.*
