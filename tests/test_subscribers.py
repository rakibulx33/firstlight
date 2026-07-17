import pytest

import storage
import subscribers as subs


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "state.db")
    storage.init_schema(path)
    return path


def test_add_list_update_delete(db):
    row = subs.add_subscriber(db, "111", name="Alice", tier="instant")
    assert row["chat_id"] == "111"
    assert row["tier"] == "instant"
    assert row["enabled"] == 1

    rows = subs.list_subscribers(db)
    assert len(rows) == 1

    updated = subs.update_subscriber(db, row["id"], tier="free", enabled=0)
    assert updated["tier"] == "free"
    assert updated["enabled"] == 0

    assert subs.remove_subscriber(db, row["id"]) is True
    assert subs.list_subscribers(db) == []
    assert subs.remove_subscriber(db, row["id"]) is False


def test_add_rejects_duplicate_chat_id(db):
    subs.add_subscriber(db, "111", tier="free")
    with pytest.raises(ValueError):
        subs.add_subscriber(db, "111", tier="instant")


def test_add_rejects_empty_chat_id(db):
    with pytest.raises(ValueError):
        subs.add_subscriber(db, "  ", tier="free")


def test_ensure_legacy_migrated_seeds_once_from_env_chat_id(db):
    subs.ensure_legacy_migrated(db, "999", tier="instant")
    rows = subs.list_subscribers(db)
    assert len(rows) == 1
    assert rows[0]["chat_id"] == "999"
    assert rows[0]["tier"] == "instant"

    # Second boot: subscribers already exist -> no-op even if called again.
    subs.ensure_legacy_migrated(db, "999", tier="instant")
    assert len(subs.list_subscribers(db)) == 1


def test_ensure_legacy_migrated_noop_without_chat_id(db):
    subs.ensure_legacy_migrated(db, None)
    subs.ensure_legacy_migrated(db, "")
    assert subs.list_subscribers(db) == []
