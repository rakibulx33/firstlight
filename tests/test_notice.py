from notice import parse_notice


def test_default_listing_detection():
    t = "에스피엑스6900(SPX) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)"
    assert parse_notice(t) == ("SPX", True)


def test_default_exclusion():
    assert parse_notice("OXT(OXT) 거래지원 종료 안내")[1] is False


def test_custom_keywords():
    ticker, is_listing = parse_notice(
        "FOO(FOO) brand new coin", listing_kw=["brand new"], exclude_kw=["delist"]
    )
    assert (ticker, is_listing) == ("FOO", True)
    assert parse_notice("FOO(FOO) brand new delist", listing_kw=["brand new"], exclude_kw=["delist"])[1] is False
