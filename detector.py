"""DetectorEngine — async new-listing detector, generalized across exchanges.

Core logic preserved from the original Upbit-only build:
  * poll a market-list endpoint at a configurable interval
  * seed existing markets SILENTLY on the first ever run
  * afterward alert + log ONLY genuinely new markets
  * SQLite dedup that survives restarts

One instance per exchange (see exchanges.EXCHANGES / app.py) shares this same
class -- only the URL, response parser, headers, and poll-interval config key
differ per exchange, so the loop/dedup/EventBus/Telegram/Phase0 wiring isn't
duplicated per exchange. `seen`/`listings` are keyed on `(exchange, market)` so
multiple exchanges can be dedup'd in the same tables without collisions (see
storage.py).

Refactored into a supervised engine the FastAPI control panel can start/stop/restart,
emitting live events (status / listing / log) onto an EventBus for WebSocket fan-out.
"""
import asyncio
import sqlite3
import time
from collections import deque
from datetime import datetime, timezone

import aiohttp

import subscribers
from alerts import alert_allowed
from broadcast import fanout
from exchanges import UPBIT_MARKET_URL, parse_upbit


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventBus:
    """Minimal pub/sub: each subscriber gets an asyncio.Queue of events."""

    def __init__(self):
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, event: dict) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass


class DetectorEngine:
    def __init__(
        self,
        db_path,
        config,
        notifier=None,
        phase0=None,
        bus=None,
        exchange: str = "upbit",
        label: str | None = None,
        market_url: str | None = None,
        parse_fn=None,
        headers: dict | None = None,
        poll_interval_key: str = "poll_interval",
        default_poll_interval: float = 1.0,
    ):
        self.db_path = db_path
        self.config = config            # dict; reads config[poll_interval_key]
        self.notifier = notifier        # notify.Telegram | None
        self.phase0 = phase0            # phase0.Phase0 | None
        self.bus = bus or EventBus()
        self.exchange = exchange
        self.label = label or exchange.title()
        self.market_url = market_url or UPBIT_MARKET_URL
        self.parse_fn = parse_fn or parse_upbit
        self.headers = headers
        self.poll_interval_key = poll_interval_key
        self.default_poll_interval = default_poll_interval
        self.logs: deque = deque(maxlen=500)
        self._task: asyncio.Task | None = None
        self._notify_tasks: set[asyncio.Task] = set()
        self._running = False
        self.started_at: float | None = None
        self.last_poll_ts: str | None = None
        self.last_latency_ms: float | None = None
        self.markets_count = 0
        self.poll_count = 0
        self.error_count = 0
        self.last_error: str | None = None
        self.notice_poller = None       # Loop B (Upbit only, set by app.py)
        self._error_alerted = False
        self._init_db()

    # ---- storage -------------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=3000")  # wait out brief locks instead of erroring
        return c

    def _init_db(self) -> None:
        with self._conn() as db:
            db.execute("PRAGMA journal_mode=WAL")  # concurrent readers + 1 writer, no lock storms
            db.execute(
                "CREATE TABLE IF NOT EXISTS seen("
                "exchange TEXT NOT NULL, market TEXT NOT NULL, ts TEXT,"
                " PRIMARY KEY(exchange, market))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS listings("
                "exchange TEXT NOT NULL, market TEXT NOT NULL, base TEXT, english TEXT, korean TEXT,"
                " detected_at TEXT, PRIMARY KEY(exchange, market))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS snapshots("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, exchange TEXT NOT NULL DEFAULT 'upbit',"
                " market TEXT, source TEXT, t_offset INTEGER, price REAL, ts TEXT)"
            )
            db.commit()

    # ---- observability -------------------------------------------------
    def log(self, level: str, msg: str) -> None:
        entry = {"ts": utcnow_iso(), "level": level, "msg": msg}
        self.logs.append(entry)
        self.bus.publish({"type": "log", "data": entry})
        print(f"[{entry['ts']}] {level.upper()}: {msg}", flush=True)

    def status(self) -> dict:
        uptime = round(time.time() - self.started_at, 1) if self.started_at else None
        return {
            "exchange": self.exchange,
            "label": self.label,
            "running": self._running,
            "started_at": self.started_at,
            "uptime_s": uptime,
            "last_poll_ts": self.last_poll_ts,
            "last_latency_ms": self.last_latency_ms,
            "markets_count": self.markets_count,
            "poll_count": self.poll_count,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "poll_interval": float(self.config.get(self.poll_interval_key, self.default_poll_interval)),
            "loop_b": self.notice_poller.status() if self.notice_poller else None,
        }

    def publish_status(self) -> None:
        self.bus.publish({"type": "status", "data": self.status()})

    # ---- lifecycle -----------------------------------------------------
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.started_at = time.time()
        self.error_count = 0
        self.last_error = None
        self._task = asyncio.create_task(self._run())
        if self.notice_poller:
            await self.notice_poller.start()
        self.log("info", f"{self.label} detector started")
        self.publish_status()

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self.notice_poller:
            await self.notice_poller.stop()
        self.started_at = None
        self.log("info", f"{self.label} detector stopped")
        self.publish_status()

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    # ---- main loop -----------------------------------------------------
    async def _run(self) -> None:
        with self._conn() as db:
            seen = {r[0] for r in db.execute("SELECT market FROM seen WHERE exchange=?", (self.exchange,))}
        first_run = not seen
        try:
            session_kwargs = {"headers": self.headers} if self.headers else {}
            async with aiohttp.ClientSession(**session_kwargs) as session:
                while self._running:
                    interval = float(self.config.get(self.poll_interval_key, self.default_poll_interval))
                    t0 = time.perf_counter()
                    try:
                        async with session.get(
                            self.market_url, timeout=aiohttp.ClientTimeout(total=8)
                        ) as r:
                            data = await r.json()
                        self.last_latency_ms = round((time.perf_counter() - t0) * 1000, 1)
                        self.last_poll_ts = utcnow_iso()
                        self.poll_count += 1
                        self._error_alerted = False
                        infos = self.parse_fn(data)
                        by_market = {i["market"]: i for i in infos}
                        current = set(by_market)
                        self.markets_count = len(current)
                        now = utcnow_iso()
                        if first_run:
                            with self._conn() as db:
                                db.executemany(
                                    "INSERT OR IGNORE INTO seen(exchange, market, ts) VALUES(?,?,?)",
                                    [(self.exchange, m, now) for m in current],
                                )
                                db.commit()
                            first_run = False
                            seen = current
                            self.log(
                                "info",
                                f"{self.label} seeded {len(current)} existing markets silently (first run)",
                            )
                        else:
                            new = current - seen
                            if new:
                                for mk in sorted(new):
                                    await self._handle_new(mk, by_market.get(mk, {}), now)
                                seen = current
                        self.publish_status()
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001 - poll must never die
                        self.error_count += 1
                        self.last_error = str(e)
                        self.log("error", f"{self.label} poll error: {e}")
                        if not self._error_alerted and self.notifier and alert_allowed(self.config, "error"):
                            self._error_alerted = True
                            self._notify(f"⚠️ {self.label} — poll error: {e}")
                        self.publish_status()
                    await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    def _notify(self, text: str) -> None:
        """Fire-and-forget broadcast to every enabled subscriber, tiered by delay."""
        subs = subscribers.list_subscribers(self.db_path)
        tiers = self.config.get("subscriber_tiers") or {}
        tasks = fanout(self.notifier, subs, tiers, text, self.log)
        self._notify_tasks.update(tasks)
        for t in tasks:
            t.add_done_callback(self._notify_tasks.discard)

    async def _handle_new(self, market: str, info: dict, now: str) -> None:
        base = info.get("base") or market
        english = info.get("display_en", "")
        korean = info.get("display_kr", "")
        with self._conn() as db:
            db.execute(
                "INSERT OR IGNORE INTO seen(exchange, market, ts) VALUES(?,?,?)",
                (self.exchange, market, now),
            )
            db.execute(
                "INSERT OR IGNORE INTO listings(exchange, market, base, english, korean, detected_at)"
                " VALUES(?,?,?,?,?,?)",
                (self.exchange, market, base, english, korean, now),
            )
            db.commit()
        self.log("alert", f"NEW LISTING [{self.label}] {market} {english} ({korean})")
        self.bus.publish(
            {
                "type": "listing",
                "data": {
                    "exchange": self.exchange,
                    "market": market,
                    "base": base,
                    "english": english,
                    "korean": korean,
                    "detected_at": now,
                },
            }
        )
        if self.notifier and alert_allowed(self.config, "listing"):
            self._notify(f"\U0001F6A8 {self.label} NEW LISTING: {market}\n{english} ({korean})\n{now}")
        if self.phase0:
            self.phase0.schedule(market, base, display=english, exchange=self.exchange)

    async def simulate_listing(self, market: str = "SIM-BTC", english: str = "Simulated Bitcoin",
                               korean: str = "시뮬레이션") -> None:
        """Dev-only: force a fake new listing to exercise the alert + Phase 0 path.

        Default maps to base BTC -> BTCUSDT so Phase 0 captures a real Bybit price.
        """
        self.log("info", f"Simulating new listing on {self.label}: {market}")
        base = market.split("-")[-1] if "-" in market else market
        await self._handle_new(market, {"base": base, "display_en": english, "display_kr": korean}, utcnow_iso())
