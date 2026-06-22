from phase0 import Phase0, OFFSETS


def test_offsets_default_and_override(tmp_path):
    db = str(tmp_path / "s.db")
    assert Phase0(db)._offsets() == OFFSETS
    assert Phase0(db, config={"phase0_offsets": [0, 5, 15]})._offsets() == [0, 5, 15]


def test_sources_default_and_override(tmp_path):
    db = str(tmp_path / "s.db")
    assert Phase0(db)._sources() == {"bybit": True, "binance": True}
    p = Phase0(db, config={"phase0_sources": {"bybit": True, "binance": False}})
    assert p._sources()["binance"] is False


def test_offsets_fall_back_on_bad_input(tmp_path):
    db = str(tmp_path / "s.db")
    # empty list, all-negative, and non-list garbage all fall back to OFFSETS
    assert Phase0(db, config={"phase0_offsets": []})._offsets() == OFFSETS
    assert Phase0(db, config={"phase0_offsets": [-5, -1]})._offsets() == OFFSETS
    assert Phase0(db, config={"phase0_offsets": "nope"})._offsets() == OFFSETS
    # mixed valid/invalid keeps the valid, deduped + sorted
    assert Phase0(db, config={"phase0_offsets": [30, 0, 30, 10]})._offsets() == [0, 10, 30]


def test_sources_default_when_missing_or_partial(tmp_path):
    db = str(tmp_path / "s.db")
    # missing dict -> both default True
    assert Phase0(db, config={"phase0_sources": {}})._sources() == {"bybit": True, "binance": True}
    # partial dict -> unspecified key defaults True
    assert Phase0(db, config={"phase0_sources": {"binance": False}})._sources() == {
        "bybit": True,
        "binance": False,
    }
