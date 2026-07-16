from exchanges import EXCHANGES, parse_binance, parse_bithumb, parse_coinbase, parse_upbit


def test_parse_upbit():
    data = [
        {"market": "KRW-BTC", "korean_name": "비트코인", "english_name": "Bitcoin"},
        {"market": "KRW-XYZ", "korean_name": "엑스코인", "english_name": "XYZ Coin"},
    ]
    out = parse_upbit(data)
    assert out == [
        {"market": "KRW-BTC", "base": "BTC", "display_en": "Bitcoin", "display_kr": "비트코인"},
        {"market": "KRW-XYZ", "base": "XYZ", "display_en": "XYZ Coin", "display_kr": "엑스코인"},
    ]


def test_parse_upbit_skips_missing_market():
    assert parse_upbit([{"korean_name": "no market field"}]) == []


def test_parse_binance():
    data = {"symbols": [
        {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT", "status": "TRADING"},
        {"symbol": "ETHBTC", "baseAsset": "ETH", "quoteAsset": "BTC", "status": "TRADING"},
    ]}
    out = parse_binance(data)
    assert out == [
        {"market": "BTCUSDT", "base": "BTC", "display_en": "BTCUSDT", "display_kr": ""},
        {"market": "ETHBTC", "base": "ETH", "display_en": "ETHBTC", "display_kr": ""},
    ]


def test_parse_binance_skips_incomplete_entries():
    assert parse_binance({"symbols": [{"symbol": "BTCUSDT"}]}) == []  # missing baseAsset
    assert parse_binance({}) == []
    assert parse_binance(None) == []


def test_parse_bithumb_upbit_shaped():
    data = [{"market": "KRW-BTC", "korean_name": "비트코인", "english_name": "Bitcoin"}]
    assert parse_bithumb(data) == [
        {"market": "KRW-BTC", "base": "BTC", "display_en": "Bitcoin", "display_kr": "비트코인"}
    ]


def test_parse_bithumb_degrades_gracefully_on_unexpected_shape():
    # Shape not independently verified against live Bithumb traffic -- must not raise
    # even if fields are missing/renamed, since a bad exchange response must never
    # crash the poll loop (see DetectorEngine._run's per-poll try/except).
    out = parse_bithumb([{"symbol": "KRW-ETH"}, {"unexpected": "junk"}, {}])
    assert out == [{"market": "KRW-ETH", "base": "ETH", "display_en": "", "display_kr": ""}]


def test_parse_coinbase():
    data = [
        {"id": "BTC-USD", "base_currency": "BTC", "quote_currency": "USD", "status": "online"},
    ]
    assert parse_coinbase(data) == [
        {"market": "BTC-USD", "base": "BTC", "display_en": "BTC-USD", "display_kr": ""}
    ]


def test_parse_coinbase_skips_incomplete_entries():
    assert parse_coinbase([{"id": "BTC-USD"}]) == []  # missing base_currency
    assert parse_coinbase(None) == []


def test_exchanges_registry_has_all_four():
    assert set(EXCHANGES.keys()) == {"upbit", "binance", "bithumb", "coinbase"}
    for ex_id, spec in EXCHANGES.items():
        assert spec["market_url"].startswith("https://")
        assert callable(spec["parse"])
        assert spec["poll_interval_key"]
        assert spec["default_poll_interval"] > 0
