import asyncio

from coinlisting import CoinListingFeed, parse_message
from detector import DetectorEngine
from exchanges import parse_upbit


def test_parse_message_common_field_names():
    msg = {"exchange": "Binance", "symbol": "BTCUSDT", "baseAsset": "BTC", "name": "Bitcoin"}
    assert parse_message(msg) == {
        "exchange": "binance",
        "market": "BTCUSDT",
        "base": "BTC",
        "display_en": "Bitcoin",
        "display_kr": "",
    }


def test_parse_message_alternate_field_names():
    msg = {"venue": "upbit", "market": "KRW-XYZ", "title": "XYZ Coin", "korean_name": "엑스코인"}
    out = parse_message(msg)
    assert out["exchange"] == "upbit"
    assert out["market"] == "KRW-XYZ"
    assert out["base"] == "XYZ"  # derived from market since no explicit base field
    assert out["display_en"] == "XYZ Coin"
    assert out["display_kr"] == "엑스코인"


def test_parse_message_missing_required_fields_returns_none():
    assert parse_message({"symbol": "BTCUSDT"}) is None  # no exchange
    assert parse_message({"exchange": "binance"}) is None  # no market
    assert parse_message({}) is None
    assert parse_message("not a dict") is None


def test_parse_message_masked_ticker_survives():
    # Trial mode masks ticker names -- must not crash, just pass the masked value through.
    msg = {"exchange": "upbit", "market": "***", "name": "***"}
    out = parse_message(msg)
    assert out["market"] == "***"
    assert out["display_en"] == "***"


def test_configured_and_status():
    feed = CoinListingFeed({}, {}, api_key=None)
    assert feed.configured() is False
    assert feed.status()["configured"] is False

    feed2 = CoinListingFeed({}, {}, api_key="dummy-test-key")
    assert feed2.configured() is True
    assert feed2.status()["running"] is False


def test_start_noop_without_key():
    async def run():
        feed = CoinListingFeed({}, {}, api_key=None)
        await feed.start()
        assert feed._running is False
        assert feed._tasks == []

    asyncio.run(run())


def test_handle_raw_routes_recognized_exchange_and_skips_unknown(tmp_path):
    async def run():
        db = str(tmp_path / "s.db")
        engine = DetectorEngine(db, {}, exchange="upbit", parse_fn=parse_upbit)
        logs = []
        feed = CoinListingFeed({"upbit": engine}, {}, logger=lambda lvl, msg: logs.append((lvl, msg)))

        await feed._handle_raw("seoul", '{"exchange":"upbit","market":"KRW-ZZZ","name":"ZZZ Coin"}')
        assert feed.message_count == 1
        assert feed.listing_count == 1
        with engine._conn() as conn:
            row = conn.execute(
                "SELECT * FROM listings WHERE exchange='upbit' AND market='KRW-ZZZ'"
            ).fetchone()
        assert row is not None
        assert row["base"] == "ZZZ"

        # Unrecognized exchange is skipped, not routed anywhere, doesn't crash.
        await feed._handle_raw("tokyo", '{"exchange":"kraken","market":"XYZ-USD"}')
        assert feed.listing_count == 1  # unchanged

        # Malformed JSON doesn't crash the feed either.
        await feed._handle_raw("tokyo", "not json")
        assert feed.message_count == 3

    asyncio.run(run())


def test_handle_signal_dedups_against_seen(tmp_path):
    async def run():
        db = str(tmp_path / "s.db")
        engine = DetectorEngine(db, {}, exchange="upbit", parse_fn=parse_upbit)
        info = {"base": "ABC", "display_en": "ABC Coin", "display_kr": ""}

        await engine.handle_signal("KRW-ABC", info)
        with engine._conn() as conn:
            count1 = conn.execute("SELECT COUNT(*) FROM listings WHERE market='KRW-ABC'").fetchone()[0]
        assert count1 == 1

        # Second signal for the same market is a no-op (already in `seen`).
        await engine.handle_signal("KRW-ABC", info)
        with engine._conn() as conn:
            count2 = conn.execute("SELECT COUNT(*) FROM listings WHERE market='KRW-ABC'").fetchone()[0]
        assert count2 == 1

    asyncio.run(run())
