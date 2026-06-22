"""DetectorEngine — async Upbit new-listing detector.

Core logic preserved from LOCALHOST_BUILD_PLAN.md:
  * poll https://api.upbit.com/v1/market/all at a configurable interval
  * seed existing markets SILENTLY on the first ever run
  * afterward alert + log ONLY genuinely new markets
  * SQLite dedup that survives restarts

Refactored into a supervised engine the FastAPI control panel can start/stop/restart,
emitting live events (status / listing / log) onto an EventBus for WebSocket fan-out.
"""
import asyncio
import sqlite3
import time
from collections import deque
from datetime import datetime, timezone

import aiohttp

from alerts import alert_allowed

UPBIT_MARKET_URL = "https://api.upbit.com/v1/market/all"


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
    def __init__(self, db_path, config, notifier=None, phase0=None, bus=None):
        self.db_path = db_path
        self.config = config            # dict; reads "poll_interval"
        self.notifier = notifier        # notify.Telegram | None
        self.phase0 = phase0            # phase0.Phase0 | None
        self.bus = bus or EventBus()
        self.logs: deque = deque(maxlen=500)
        self._task: asyncio.Task | None = None
        self._running = False
        self.started_at: float | None = None
        self.last_poll_ts: str | None = None
        self.last_latency_ms: float | None = None
        self.markets_count = 0
        self.poll_count = 0
        self.error_count = 0
        self.last_error: str | None = None
        self.notice_poller = None       # Loop B (set by app.py); started/stopped with Loop A
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
            db.execute("CREATE TABLE IF NOT EXISTS seen(market TEXT PRIMARY KEY, ts TEXT)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS listings("
                "market TEXT PRIMARY KEY, english TEXT, korean TEXT, detected_at TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS snapshots("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, market TEXT, source TEXT,"
                " t_offset INTEGER, price REAL, ts TEXT)"
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
            "running": self._running,
            "started_at": self.started_at,
            "uptime_s": uptime,
            "last_poll_ts": self.last_poll_ts,
            "last_latency_ms": self.last_latency_ms,
            "markets_count": self.markets_count,
            "poll_count": self.poll_count,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "poll_interval": float(self.config.get("poll_interval", 1.0)),
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
        self.log("info", "Detector started")
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
        self.log("info", "Detector stopped")
        self.publish_status()

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    # ---- main loop -----------------------------------------------------
    async def _run(self) -> None:
        with self._conn() as db:
            seen = {r[0] for r in db.execute("SELECT market FROM seen")}
        first_run = not seen
        try:
            async with aiohttp.ClientSession() as session:
                while self._running:
                    interval = float(self.config.get("poll_interval", 1.0))
                    t0 = time.perf_counter()
                    try:
                        async with session.get(
                            UPBIT_MARKET_URL, timeout=aiohttp.ClientTimeout(total=5)
                        ) as r:
                            data = await r.json()
                        self.last_latency_ms = round((time.perf_counter() - t0) * 1000, 1)
                        self.last_poll_ts = utcnow_iso()
                        self.poll_count += 1
                        self._error_alerted = False
                        current = {m["market"] for m in data}
                        self.markets_count = len(current)
                        now = utcnow_iso()
                        if first_run:
                            with self._conn() as db:
                                db.executemany(
                                    "INSERT OR IGNORE INTO seen VALUES(?,?)",
                                    [(m, now) for m in current],
                                )
                                db.commit()
                            first_run = False
                            seen = current
                            self.log(
                                "info",
                                f"Seeded {len(current)} existing markets silently (first run)",
                            )
                        else:
                            new = current - seen
                            if new:
                                for mk in sorted(new):
                                    info = next((x for x in data if x["market"] == mk), {})
                                    await self._handle_new(mk, info, now)
                                seen = current
                        self.publish_status()
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001 - poll must never die
                        self.error_count += 1
                        self.last_error = str(e)
                        self.log("error", f"poll error: {e}")
                        if not self._error_alerted and self.notifier and alert_allowed(self.config, "error"):
                            self._error_alerted = True
                            try:
                                await self.notifier.send(f"⚠️ Upbit Watch — Loop A error: {e}")
                            except Exception:  # noqa: BLE001
                                pass
                        self.publish_status()
                    await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    async def _handle_new(self, market: str, info: dict, now: str) -> None:
        english = info.get("english_name", "")
        korean = info.get("korean_name", "")
        with self._conn() as db:
            db.execute("INSERT OR IGNORE INTO seen VALUES(?,?)", (market, now))
            db.execute(
                "INSERT OR IGNORE INTO listings VALUES(?,?,?,?)",
                (market, english, korean, now),
            )
            db.commit()
        self.log("alert", f"NEW MARKET {market} {english} ({korean})")
        self.bus.publish(
            {
                "type": "listing",
                "data": {
                    "market": market,
                    "english": english,
                    "korean": korean,
                    "detected_at": now,
                },
            }
        )
        if self.notifier and alert_allowed(self.config, "listing"):
            res = await self.notifier.send(
                f"\U0001F6A8 UPBIT NEW MARKET: {market}\n{english} ({korean})\n{now}"
            )
            if not res.get("ok"):
                self.log("error", f"Telegram alert failed: {res.get('error', res)}")
        if self.phase0:
            self.phase0.schedule(market, english)

    async def simulate_listing(self, market: str = "SIM-BTC", english: str = "Simulated Bitcoin",
                               korean: str = "시뮬레이션") -> None:
        """Dev-only: force a fake new listing to exercise the alert + Phase 0 path.

        Default maps to base BTC -> BTCUSDT so Phase 0 captures a real Bybit price.
        """
        self.log("info", f"Simulating new listing: {market}")
        await self._handle_new(market, {"english_name": english, "korean_name": korean}, utcnow_iso())
