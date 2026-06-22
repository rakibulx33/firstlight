# How Upbit Watch Works — Architecture & Feature Internals

This explains **how every feature actually works under the hood** — the components, the data
flow, and what each file/function does. For install & day-to-day use, see `SETUP_AND_GUIDE.md`.

---

## 1. Big picture

Upbit Watch is **one FastAPI process** that supervises **two independent async detection loops**
and pushes everything live to a browser dashboard over a WebSocket.

```
                          ┌─────────────────────────── app.py (FastAPI) ───────────────────────────┐
                          │                                                                          │
  Upbit market/all  ──▶  │  Loop A: DetectorEngine ─┐                                                │
  (every 1s)             │                          │                                                │
                          │  Loop B: NoticePoller  ──┼──▶  EventBus (async pub/sub) ──▶  /ws  ──▶ Browser dashboard
  Upbit announcements ─▶  │  (every 8s)              │            │                                   │   (static/index.html)
  (every 8s)             │                          │            ├──▶ Telegram (notify.py)           │
                          │  Phase 0: Phase0 ────────┘            └──▶ SQLite state.db (WAL)          │
  Bybit / Binance   ──▶  │  (on each new listing)                                                     │
                          │                                                                          │
                          │  REST: /api/start /stop /restart /status /listings /notices /markets ... │
                          └──────────────────────────────────────────────────────────────────────────┘
```

Two facts make the whole thing reliable:

1. **Seed-silent + dedup.** On first run each loop records everything that *already* exists and
   sends nothing. After that it alerts **only** on genuinely new items. The "already seen" set
   lives in SQLite, so it **survives restarts** (no false alert storm after a reboot).
2. **One supervisor, two loops.** `Start`/`Stop`/`Restart` (and autostart) control both loops
   together; each loop is an independent `asyncio.Task` that can never crash the process (every
   poll is wrapped in try/except).

---

## 2. Components (which file does what)

| File | Role | Key pieces |
|---|---|---|
| `app.py` | FastAPI app: REST + WebSocket, wires everything, autostart | `lifespan`, `/api/*`, `/ws`, `persist_env` |
| `detector.py` | **Loop A** engine + the shared `EventBus` | `DetectorEngine`, `EventBus` |
| `notice.py` | **Loop B** announcement poller | `NoticePoller`, `parse_notice` |
| `phase0.py` | Price-snapshot logger after a listing | `Phase0` |
| `notify.py` | Telegram sender (raw Bot API) | `Telegram` |
| `static/index.html` | The dashboard (Tailwind + Alpine.js + Chart.js + WebSocket) | one self-contained page |
| `config.json` | Tunables: poll intervals, autostart | — |
| `.env` | Secrets: Telegram token + chat id | — |
| `state.db` | SQLite store (WAL): `seen`, `listings`, `notices`, `snapshots` | — |

These were the 13 communities graphify found — they line up 1:1 with these responsibilities.

---

## 3. The core data flow — "a new coin is detected"

This is the **alert fan-out**, the heart of the system:

```
new market/announcement detected
        │
        ├─▶ write to SQLite (seen/listings or notices)   ← dedup memory, survives restart
        ├─▶ engine.log(...)        ─▶ EventBus "log"      ─▶ dashboard Logs console
        ├─▶ EventBus "listing"/"notice"                   ─▶ dashboard feed + toast
        ├─▶ Telegram.send(...)                            ─▶ your phone
        └─▶ Phase0.schedule(market) (Loop A only)         ─▶ price snapshots at +0/10/30/60s/5m
```

Everything is decoupled through the **EventBus**: detectors `publish()` events, the WebSocket
endpoint `subscribe()`s and forwards them. Detectors never know about the browser.

---

## 4. Feature internals

### 4.1 Loop A — market detection (`detector.py` → `DetectorEngine`)
- `_run()` opens one `aiohttp` session and loops every `poll_interval` (default **1s**):
  - GET `https://api.upbit.com/v1/market/all`, measure latency, count markets.
  - **First ever run** (the `seen` table is empty): bulk-insert every current market into `seen`
    and log "Seeded N markets silently" — **no alerts**.
  - **Afterwards:** `new = current_markets − seen`. For each new market call `_handle_new()`.
- `_handle_new()` = the fan-out: insert into `seen` + `listings`, log an `alert`, publish a
  `listing` event, send the Telegram alert, and `phase0.schedule(market)`.
- Every poll calls `publish_status()` so the dashboard's uptime/latency/markets tick live.
- The whole poll body is wrapped in try/except: a network blip increments `error_count` and is
  logged, but the loop keeps going.
- `simulate_listing()` is a dev hook (`/api/simulate`) that forces a fake `SIM-BTC` through
  `_handle_new()` to exercise the entire pipeline (it maps to `BTCUSDT` so Phase 0 gets real prices).

### 4.2 Loop B — announcement detection (`notice.py` → `NoticePoller`)
- Polls `https://api-manager.upbit.com/api/v1/announcements?...&category=trade` every
  `poll_interval_notice` (default **8s**) with a **browser `User-Agent`** (the endpoint is behind
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
- `schedule(market)` derives the symbol: `KRW-XXX` → base `XXX` → `XXXUSDT`, and spawns an async
  task.
- `_collect()` waits to each offset **+0 / 10 / 30 / 60 s / 5 m** after detection and, at each
  point, fetches the price from **Bybit** (primary) and **Binance** (best-effort) and writes both
  to the `snapshots` table; also publishes a `snapshot` event for the live chart.
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

### 4.5 Telegram (`notify.py` → `Telegram`)
- `send(text)` POSTs to the raw Bot API `sendMessage` via `aiohttp`. `configured()` guards against
  missing creds (returns an error dict instead of throwing).
- Token/chat-id are **mutable at runtime** so the Settings drawer can update them without a restart.
- `/api/telegram/test` sends a one-off message so you can confirm delivery.

### 4.6 Control & lifecycle (`app.py` `lifespan`, `DetectorEngine.start/stop/restart`)
- `start()` launches the Loop A task **and** `notice_poller.start()`; `stop()` cancels both
  cleanly; `restart()` = stop then start. So all control paths cover both loops.
- **Autostart:** the FastAPI `lifespan` hook calls `engine.start()` on boot when
  `config.autostart` is true (default) and `engine.stop()` on shutdown — so a reboot + relaunch
  comes up already watching, no manual Start.

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
  **Markets** (searchable table), **Phase 0** (price chart + Simulate button). The status sidebar
  shows Loop A metrics and a Loop B sub-panel. Settings is a slide-over drawer.

---

## 5. Storage reference (`state.db`)

| Table | Columns | Purpose |
|---|---|---|
| `seen` | `market, ts` | Loop A dedup — every market ever observed |
| `listings` | `market, english, korean, detected_at` | Loop A detected new markets |
| `notices` | `id, title, ticker, category, is_listing, listed_at, detected_at` | Loop B announcements |
| `snapshots` | `id, market, source, t_offset, price, ts` | Phase 0 price points (Bybit/Binance) |

---

## 6. Event reference (WebSocket `/ws`)

| `type` | Emitted when | Payload (`data`) |
|---|---|---|
| `status` | each poll, start/stop | full status incl. nested `loop_b` |
| `log` | any log line | `{ts, level, msg}` (`level`: info/alert/error) |
| `listing` | Loop A finds a new market | `{market, english, korean, detected_at}` |
| `notice` | Loop B finds a new announcement | `{id, title, ticker, is_listing, listed_at, ...}` |
| `snapshot` | a Phase 0 price point is captured | `{market, t_offset, bybit, binance, ts}` |

---

## 7. API ↔ UI map

| Endpoint | Used by |
|---|---|
| `POST /api/start /stop /restart` | top-bar control buttons |
| `GET /api/status` | status sidebar (+ initial load) |
| `GET /api/listings` | Live tab feed |
| `GET /api/notices` | Announce tab feed |
| `GET /api/markets` | Markets tab table |
| `GET /api/logs` | Logs console |
| `GET/PUT /api/settings` | Settings drawer |
| `POST /api/telegram/test` | "Send test message" button |
| `POST /api/simulate` | "Simulate listing" button (Phase 0) |
| `GET /api/listings/{market}/snapshots` | Phase 0 chart |
| `WS /ws` | all live updates |

---

## 8. End-to-end: what happens when Upbit lists a new coin

1. **Loop B** sees the announcement first (e.g. `신규 거래지원 안내 (XYZ)`), classifies it as a
   listing, extracts `XYZ`, stores it, and pings Telegram `📢 LISTING ANNOUNCEMENT: XYZ`.
2. Seconds later **Loop A** sees `KRW-XYZ` appear in `market/all`, stores it, pings Telegram
   `🚨 NEW MARKET: KRW-XYZ`, and triggers **Phase 0**.
3. **Phase 0** snapshots `XYZUSDT` on Bybit/Binance at +0/10/30/60s/5m → the chart fills in.
4. The dashboard shows it all live: cards flash into the feeds, a toast pops, logs stream, the
   chart draws.

---

## 9. Failure modes & resilience

| Situation | Behaviour |
|---|---|
| Upbit/network blip | poll error logged + counted; loop continues |
| Cloudflare `429` on Loop B | logged + counted; Loop A unaffected; raise Loop B interval in Settings |
| Binance geo-blocked | Phase 0 stores `NULL` for Binance; Bybit still recorded |
| Process restart / reboot | dedup persists (WAL) → no re-seed, no false alerts; autostart re-arms |
| Laptop sleep | WSL suspends → bot pauses; relaunch (autostart) resumes |
| Slow/stuck browser | its event queue drops overflow; detectors never block |

---

*Generated alongside the graphify knowledge graph (`graphify-out/graph.html`), which maps these
same components as 13 communities with `REST API`, `DetectorEngine`, and `NoticePoller` as the
most-connected hubs.*
