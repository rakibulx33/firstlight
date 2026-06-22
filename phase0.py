"""Phase 0 price-snapshot logger.

On each new listing, snapshot the coin's spot price at +0/10/30/60s/5m.
Primary source: Bybit (reachable). Best-effort: Binance (currently geo-blocked from
this machine -> recorded as NULL, no crash). Rows land in the `snapshots` table.

Upbit market like "KRW-XYZ" / "SIM-BTC" -> base = last segment -> "<BASE>USDT".
"""
import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import aiohttp

OFFSETS = [0, 10, 30, 60, 300]  # seconds after detection


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Phase0:
    def __init__(self, db_path, bus=None, logger=None, config=None):
        self.db_path = db_path
        self.bus = bus
        self.logger = logger            # callable(level, msg) | None
        self.config = config or {}
        self._tasks: set[asyncio.Task] = set()

    def _log(self, level, msg):
        if self.logger:
            self.logger(level, msg)

    def _offsets(self) -> list[int]:
        offs = self.config.get("phase0_offsets") or OFFSETS
        try:
            return sorted({int(x) for x in offs if int(x) >= 0}) or list(OFFSETS)
        except (TypeError, ValueError):
            return list(OFFSETS)

    def _sources(self) -> dict:
        src = self.config.get("phase0_sources") or {}
        return {"bybit": bool(src.get("bybit", True)), "binance": bool(src.get("binance", True))}

    def schedule(
        self,
        market: str,
        english: str = "",
        base_ts: datetime | None = None,
        offsets: list[int] | None = None,
    ) -> None:
        """Arm price-snapshot collection for `market`.

        base_ts: detection time the offsets are measured from (default: now).
        offsets: which offsets to capture (default: all). Used by resume_pending to
        capture only the offsets that are still in the future after a restart.
        """
        base = market.split("-")[-1]
        symbol = f"{base}USDT"
        offs = self._offsets() if offsets is None else list(offsets)
        t = asyncio.create_task(self._collect(market, symbol, base_ts, offs))
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        self._log("info", f"Phase0 scheduled for {market} ({symbol}) at +{offs}s")

    def resume_pending(self) -> None:
        """Re-arm Phase 0 after a process restart.

        For every listing detected within the last OFFSETS[-1] seconds, capture any
        snapshot offsets that (a) weren't recorded yet and (b) are still in the future.
        Offsets whose time already passed while the process was down are unrecoverable
        (the ticker APIs only give *current* price) and are left as gaps.
        """
        horizon = self._offsets()[-1]
        now = datetime.now(timezone.utc)
        resumed = 0
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            listings = db.execute("SELECT market, detected_at FROM listings").fetchall()
            for r in listings:
                try:
                    det = datetime.fromisoformat(r["detected_at"])
                except (ValueError, TypeError):
                    continue
                elapsed = (now - det).total_seconds()
                if elapsed < 0 or elapsed > horizon:
                    continue  # nothing left to capture for this listing
                done = {
                    row[0]
                    for row in db.execute(
                        "SELECT DISTINCT t_offset FROM snapshots WHERE market=?", (r["market"],)
                    )
                }
                remaining = [
                    o for o in self._offsets() if o not in done and (det + timedelta(seconds=o)) > now
                ]
                if remaining:
                    self.schedule(r["market"], base_ts=det, offsets=remaining)
                    resumed += 1
        if resumed:
            self._log("info", f"Phase0 resumed {resumed} pending collection(s) after restart")

    async def _collect(
        self,
        market: str,
        symbol: str,
        base_ts: datetime | None,
        offsets: list[int],
    ) -> None:
        base_dt = base_ts or datetime.now(timezone.utc)
        async with aiohttp.ClientSession() as session:
            for off in offsets:
                wait = (base_dt + timedelta(seconds=off) - datetime.now(timezone.utc)).total_seconds()
                if wait > 0:
                    await asyncio.sleep(wait)
                ts = utcnow_iso()
                src = self._sources()
                bybit = await self._bybit(session, symbol) if src["bybit"] else None
                binance = await self._binance(session, symbol) if src["binance"] else None
                self._save(market, "bybit", off, bybit, ts)
                self._save(market, "binance", off, binance, ts)
                self._log("info", f"Phase0 {market} +{off}s  bybit={bybit}  binance={binance}")
                if self.bus:
                    self.bus.publish(
                        {
                            "type": "snapshot",
                            "data": {
                                "market": market,
                                "t_offset": off,
                                "bybit": bybit,
                                "binance": binance,
                                "ts": ts,
                            },
                        }
                    )

    async def _bybit(self, session, symbol):
        url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as r:
                d = await r.json()
                lst = (d.get("result") or {}).get("list") or []
                if lst:
                    return float(lst[0]["lastPrice"])
        except Exception:  # noqa: BLE001
            pass
        return None

    async def _binance(self, session, symbol):
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as r:
                d = await r.json()
                if "price" in d:
                    return float(d["price"])
        except Exception:  # noqa: BLE001
            pass
        return None

    def _save(self, market, source, off, price, ts):
        with sqlite3.connect(self.db_path) as db:
            db.execute("PRAGMA busy_timeout=3000")
            db.execute(
                "INSERT INTO snapshots(market,source,t_offset,price,ts) VALUES(?,?,?,?,?)",
                (market, source, off, price, ts),
            )
            db.commit()
