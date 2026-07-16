"""CoinListingFeed -- optional fast-path signal source from the third-party
coinlisting.pro WebSocket feed, layered on top of the exchange DetectorEngines.

SCHEMA CAVEAT: at write time only the service's marketing/pricing page was
available -- no docs or example payloads. `parse_message()` is a best-effort,
defensive guess at common field names (tried under several plausible aliases);
anything that doesn't parse is logged and skipped rather than crashing the feed.
Verify field names against a real message once connected and adjust `parse_message`
if they differ -- that's the one function that should need changing.

Routing: per the service's own guidance, the Seoul colo carries Upbit listings and
the Tokyo colo carries everything else (Binance/Bithumb/Coinbase and other
non-Upbit venues), so both are connected simultaneously and messages are routed by
each message's own `exchange` field, not by which socket they arrived on.

Dedup: a signal here goes through DetectorEngine.handle_signal(), which checks the
same `seen` table the poll loop uses -- so if the REST poller already caught a
listing first, the fast feed's copy of the same event is silently ignored (and
vice versa). Whichever source sees it first fires the alert.
"""
import asyncio
import json
from datetime import datetime, timezone

import websockets

TOKYO_URL = "wss://tokyo.coinlisting.pro/listings"
SEOUL_URL = "wss://seoul.coinlisting.pro/listings"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _first(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def parse_message(msg: dict) -> dict | None:
    """Best-effort normalize a coinlisting.pro message into
    {"exchange", "market", "base", "display_en", "display_kr"}.
    Returns None if the message doesn't look like a listing event (e.g. a
    heartbeat/ack/subscription-confirmation frame)."""
    if not isinstance(msg, dict):
        return None
    exchange = _first(msg, "exchange", "venue", "source", "ex")
    market = _first(msg, "market", "symbol", "ticker", "pair")
    if not exchange or not market:
        return None
    exchange = str(exchange).strip().lower()
    market = str(market).strip()
    base = _first(msg, "base", "baseAsset", "base_asset", "ticker_base")
    if not base:
        base = market.split("-")[-1] if "-" in market else market
    return {
        "exchange": exchange,
        "market": market,
        "base": str(base).strip().upper(),
        "display_en": str(_first(msg, "name", "title", "english_name") or ""),
        "display_kr": str(_first(msg, "korean_name", "name_kr") or ""),
    }


class CoinListingFeed:
    def __init__(self, engines: dict, config: dict, bus=None, logger=None, api_key: str | None = None):
        self.engines = engines  # {exchange_id: DetectorEngine}
        self.config = config
        self.bus = bus
        self.logger = logger
        self.api_key = api_key or None
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self.message_count = 0
        self.listing_count = 0
        self.error_count = 0
        self.last_message_ts: str | None = None
        self.last_error: str | None = None
        self.connected: dict[str, bool] = {"tokyo": False, "seoul": False}

    def configured(self) -> bool:
        return bool(self.api_key)

    def _log(self, level: str, msg: str) -> None:
        if self.logger:
            self.logger(level, msg)

    def status(self) -> dict:
        return {
            "configured": self.configured(),
            "running": self._running,
            "connected": dict(self.connected),
            "message_count": self.message_count,
            "listing_count": self.listing_count,
            "error_count": self.error_count,
            "last_message_ts": self.last_message_ts,
            "last_error": self.last_error,
        }

    async def start(self) -> None:
        if self._running or not self.configured():
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._run("tokyo", TOKYO_URL)),
            asyncio.create_task(self._run("seoul", SEOUL_URL)),
        ]
        self._log("info", "Fast feed (coinlisting.pro) started")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._tasks = []
        self.connected = {"tokyo": False, "seoul": False}
        self._log("info", "Fast feed (coinlisting.pro) stopped")

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def _run(self, colo: str, url: str) -> None:
        backoff = 1.0
        while self._running:
            try:
                async with websockets.connect(
                    f"{url}?key={self.api_key}", open_timeout=10, ping_interval=20
                ) as ws:
                    self.connected[colo] = True
                    self._log("info", f"Fast feed connected ({colo})")
                    backoff = 1.0
                    async for raw in ws:
                        if not self._running:
                            break
                        await self._handle_raw(colo, raw)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - feed must never die
                self.error_count += 1
                self.last_error = f"{colo}: {e}"
                self._log("error", f"Fast feed ({colo}) error: {e}")
            finally:
                self.connected[colo] = False
            if not self._running:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _handle_raw(self, colo: str, raw) -> None:
        self.message_count += 1
        self.last_message_ts = utcnow_iso()
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            return
        info = parse_message(msg)
        if not info:
            return
        engine = self.engines.get(info["exchange"])
        if not engine:
            self._log("info", f"Fast feed ({colo}): unrecognized exchange {info['exchange']!r}, skipped")
            return
        self.listing_count += 1
        self._log("info", f"Fast feed ({colo}) signal: [{info['exchange']}] {info['market']}")
        await engine.handle_signal(info["market"], info)
