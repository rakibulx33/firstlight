"""Loop B — Upbit announcement/notice poller.

Polls the (undocumented, Cloudflare-fronted) announcements endpoint filtered to the
trade category. A new-listing announcement looks like:

    "에스피엑스6900(SPX) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)"

i.e.  <korean name>(TICKER) 신규 거래지원 안내 (<markets> 마켓)

This often fires at/just before the market/all change (Loop A), so it's an earlier signal.
Best-effort from a home IP (Cloudflare may 429); errors are logged, never fatal.

Behaviour mirrors Loop A: seed existing notice ids SILENTLY on first run, then alert only
on genuinely new listing announcements. Dedup via the `notices` table (survives restarts).
"""
import asyncio
import re
import sqlite3
import time
from datetime import datetime, timezone

import aiohttp

from alerts import alert_allowed

ANNOUNCE_URL = (
    "https://api-manager.upbit.com/api/v1/announcements"
    "?os=web&page=1&per_page=30&category=trade"
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://upbit.com/",
}
TICKER_RE = re.compile(r"\(([A-Z0-9]{2,15})\)")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Upbit phrases new listings two ways: "신규 거래지원 안내" and "… 마켓 디지털 자산 추가".
LISTING_KW = ("거래지원", "디지털 자산 추가", "신규 상장", "마켓 추가")
# Exclude delisting / caution notices that also carry a ticker.
EXCLUDE_KW = ("종료", "폐지", "유의")


def parse_notice(title: str, listing_kw=LISTING_KW, exclude_kw=EXCLUDE_KW) -> tuple[str | None, bool]:
    """Return (ticker, is_listing) from an announcement title."""
    title = title or ""
    m = TICKER_RE.search(title)
    ticker = m.group(1) if m else None
    is_listing = (
        bool(ticker)
        and any(k in title for k in listing_kw)
        and not any(k in title for k in exclude_kw)
    )
    return ticker, is_listing


class NoticePoller:
    def __init__(self, db_path, config, notifier=None, bus=None, logger=None, on_tick=None):
        self.db_path = db_path
        self.config = config          # reads "poll_interval_notice"
        self.notifier = notifier
        self.bus = bus
        self.logger = logger          # callable(level, msg)
        self.on_tick = on_tick        # callable() -> push unified status
        self._task: asyncio.Task | None = None
        self._running = False
        self.last_poll_ts: str | None = None
        self.last_latency_ms: float | None = None
        self.poll_count = 0
        self.error_count = 0
        self.last_error: str | None = None
        self.notice_count = 0
        self.listing_count = 0
        self._error_alerted = False
        self._init_db()

    def _log(self, level, msg):
        if self.logger:
            self.logger(level, msg)

    def _conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=3000")
        return c

    def _init_db(self):
        with self._conn() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS notices("
                "id INTEGER PRIMARY KEY, title TEXT, ticker TEXT, category TEXT,"
                " is_listing INTEGER, listed_at TEXT, detected_at TEXT)"
            )
            db.commit()
            self.notice_count = db.execute("SELECT COUNT(*) FROM notices").fetchone()[0]
            self.listing_count = db.execute(
                "SELECT COUNT(*) FROM notices WHERE is_listing=1"
            ).fetchone()[0]

    def poll_interval(self) -> float:
        return float(self.config.get("poll_interval_notice", 5.0))

    def _keywords(self):
        nk = self.config.get("notice_keywords") or {}
        return (nk.get("listing") or list(LISTING_KW), nk.get("exclude") or list(EXCLUDE_KW))

    def status(self) -> dict:
        return {
            "running": self._running,
            "last_poll_ts": self.last_poll_ts,
            "last_latency_ms": self.last_latency_ms,
            "poll_count": self.poll_count,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "notice_count": self.notice_count,
            "listing_count": self.listing_count,
            "poll_interval": self.poll_interval(),
        }

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())
        self._log("info", "Loop B (notices) started")

    async def stop(self):
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
        self._log("info", "Loop B (notices) stopped")

    async def _run(self):
        with self._conn() as db:
            seen = {r[0] for r in db.execute("SELECT id FROM notices")}
        first_run = not seen
        try:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                while self._running:
                    t0 = time.perf_counter()
                    try:
                        async with session.get(
                            ANNOUNCE_URL, timeout=aiohttp.ClientTimeout(total=8)
                        ) as r:
                            if r.status != 200:
                                raise RuntimeError(f"HTTP {r.status} (Cloudflare/429?)")
                            payload = await r.json()
                        self.last_latency_ms = round((time.perf_counter() - t0) * 1000, 1)
                        self.last_poll_ts = utcnow_iso()
                        self.poll_count += 1
                        self._error_alerted = False
                        notices = (payload.get("data") or {}).get("notices") or []
                        new = [n for n in notices if n["id"] not in seen]
                        if first_run:
                            for n in notices:
                                self._store(n)
                                seen.add(n["id"])
                            first_run = False
                            self._log(
                                "info",
                                f"Loop B seeded {len(notices)} existing announcements silently",
                            )
                        else:
                            for n in sorted(new, key=lambda x: x["id"]):
                                await self._handle(n)
                                seen.add(n["id"])
                        if self.on_tick:
                            self.on_tick()
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001
                        self.error_count += 1
                        self.last_error = str(e)
                        self._log("error", f"Loop B poll error: {e}")
                        if not self._error_alerted and self.notifier and alert_allowed(self.config, "error"):
                            self._error_alerted = True
                            try:
                                await self.notifier.send(f"⚠️ Upbit Watch — Loop B error: {e}")
                            except Exception:  # noqa: BLE001
                                pass
                        if self.on_tick:
                            self.on_tick()
                    await asyncio.sleep(self.poll_interval())
        except asyncio.CancelledError:
            pass

    def _store(self, n: dict) -> dict:
        listing_kw, exclude_kw = self._keywords()
        ticker, is_listing = parse_notice(n.get("title", ""), listing_kw, exclude_kw)
        row = {
            "id": n["id"],
            "title": n.get("title", ""),
            "ticker": ticker,
            "category": n.get("category", ""),
            "is_listing": 1 if is_listing else 0,
            "listed_at": n.get("listed_at", ""),
            "detected_at": utcnow_iso(),
        }
        with self._conn() as db:
            db.execute(
                "INSERT OR IGNORE INTO notices(id,title,ticker,category,is_listing,listed_at,detected_at)"
                " VALUES(:id,:title,:ticker,:category,:is_listing,:listed_at,:detected_at)",
                row,
            )
            db.commit()
            self.notice_count = db.execute("SELECT COUNT(*) FROM notices").fetchone()[0]
            self.listing_count = db.execute(
                "SELECT COUNT(*) FROM notices WHERE is_listing=1"
            ).fetchone()[0]
        return row

    async def _handle(self, n: dict):
        row = self._store(n)
        listing = bool(row["is_listing"])
        level = "alert" if listing else "info"
        tag = "LISTING ANNOUNCEMENT" if listing else "notice"
        self._log(level, f"Loop B {tag}: {row['title']}")
        if self.bus:
            self.bus.publish({"type": "notice", "data": row})
        if listing and self.notifier and alert_allowed(self.config, "notice"):
            link = f"https://upbit.com/service_center/notice?id={row['id']}"
            res = await self.notifier.send(
                f"\U0001F4E2 UPBIT LISTING ANNOUNCEMENT: {row['ticker']}\n"
                f"{row['title']}\n{link}"
            )
            if not res.get("ok"):
                self._log("error", f"Telegram announcement alert failed: {res.get('error', res)}")
