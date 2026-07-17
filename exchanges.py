"""Exchange registry + response parsers for multi-exchange new-listing detection.

Each `parse_*(data)` is a pure function: raw JSON from that exchange's public
market-list endpoint in, a normalized list of listing records out —
`{"market": <exchange-native id>, "base": <base asset ticker>,
  "display_en": str, "display_kr": str}`.

`DetectorEngine` (detector.py) is exchange-agnostic; it just calls whichever
`parse_fn` its instance was built with. Adding a fifth exchange means adding
one parser + one EXCHANGES entry, not a new engine class.
"""
UPBIT_MARKET_URL = "https://api.upbit.com/v1/market/all"
BINANCE_MARKET_URL = "https://api.binance.com/api/v3/exchangeInfo"
BITHUMB_MARKET_URL = "https://api.bithumb.com/v1/market/all"
COINBASE_MARKET_URL = "https://api.exchange.coinbase.com/products"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def parse_upbit(data: list) -> list[dict]:
    out = []
    for m in data or []:
        market = m.get("market")
        if not market:
            continue
        out.append(
            {
                "market": market,
                "base": market.split("-")[-1],
                "display_en": m.get("english_name", ""),
                "display_kr": m.get("korean_name", ""),
            }
        )
    return out


def parse_binance(data: dict) -> list[dict]:
    out = []
    for s in (data or {}).get("symbols") or []:
        symbol = s.get("symbol")
        base = s.get("baseAsset")
        if not symbol or not base:
            continue
        out.append({"market": symbol, "base": base, "display_en": symbol, "display_kr": ""})
    return out


def parse_bithumb(data: list) -> list[dict]:
    """Bithumb's `/v1/market/all` is modeled on Upbit's API and is assumed to share
    its shape (`market`/`english_name`/`korean_name`). Not independently verified
    against live traffic at write time — degrade gracefully instead of raising if
    a field is missing so a shape mismatch shows up as a logged warning, not a
    dead poll loop (see DetectorEngine._run's per-poll try/except)."""
    out = []
    for m in data or []:
        market = m.get("market") or m.get("symbol")
        if not market:
            continue
        base = market.split("-")[-1] if "-" in market else market
        out.append(
            {
                "market": market,
                "base": base,
                "display_en": m.get("english_name", ""),
                "display_kr": m.get("korean_name", ""),
            }
        )
    return out


def parse_coinbase(data: list) -> list[dict]:
    out = []
    for p in data or []:
        market = p.get("id")
        base = p.get("base_currency")
        if not market or not base:
            continue
        out.append({"market": market, "base": base, "display_en": market, "display_kr": ""})
    return out


EXCHANGES = {
    "upbit": {
        "label": "Upbit",
        "market_url": UPBIT_MARKET_URL,
        "headers": None,
        "parse": parse_upbit,
        "poll_interval_key": "poll_interval",
        "default_poll_interval": 1.0,
    },
    "binance": {
        "label": "Binance",
        "market_url": BINANCE_MARKET_URL,
        "headers": {"User-Agent": BROWSER_UA},
        "parse": parse_binance,
        "poll_interval_key": "poll_interval_binance",
        "default_poll_interval": 5.0,
    },
    "bithumb": {
        "label": "Bithumb",
        "market_url": BITHUMB_MARKET_URL,
        "headers": {"User-Agent": BROWSER_UA},
        "parse": parse_bithumb,
        "poll_interval_key": "poll_interval_bithumb",
        "default_poll_interval": 5.0,
    },
    "coinbase": {
        "label": "Coinbase",
        "market_url": COINBASE_MARKET_URL,
        "headers": {"User-Agent": BROWSER_UA},
        "parse": parse_coinbase,
        "poll_interval_key": "poll_interval_coinbase",
        "default_poll_interval": 5.0,
    },
}
