import json
import importlib
from fastapi.testclient import TestClient


def _client(tmp_path, monkeypatch):
    # Isolate config.json, .env, state.db into a temp dir before importing app.
    monkeypatch.setenv("UPBIT_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"poll_interval": 1.0}))
    (tmp_path / "static").mkdir(exist_ok=True)  # StaticFiles mount validates this dir at import
    import app as app_module
    importlib.reload(app_module)
    return TestClient(app_module.app), app_module


def test_get_settings_fills_defaults_and_hides_token(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    r = client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["poll_interval"] == 1.0
    assert body["phase0_offsets"] == [0, 10, 30, 60, 300]
    assert body["phase0_sources"] == {"bybit": True, "binance": True}
    assert body["alert_on_listing"] is True
    assert body["quiet_hours"]["enabled"] is False
    assert "telegram_token" not in body
    assert body["telegram_token_set"] is False
    assert body["poll_interval_binance"] == 5.0
    assert body["poll_interval_bithumb"] == 5.0
    assert body["poll_interval_coinbase"] == 5.0
    assert body["subscriber_tiers"] == {"instant": 0, "delayed": 30, "free": 120}


def test_put_updates_and_validates(tmp_path, monkeypatch):
    client, app_module = _client(tmp_path, monkeypatch)
    r = client.put("/api/settings", json={
        "poll_interval": 2.5,
        "phase0_offsets": [0, 5, 15],
        "phase0_sources": {"bybit": True, "binance": False},
        "alert_on_notice": False,
        "quiet_hours": {"enabled": True, "start": "22:00", "end": "06:30"},
    })
    assert r.status_code == 200
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["poll_interval"] == 2.5
    assert saved["phase0_offsets"] == [0, 5, 15]
    assert saved["phase0_sources"]["binance"] is False
    assert saved["alert_on_notice"] is False
    assert saved["quiet_hours"] == {"enabled": True, "start": "22:00", "end": "06:30"}


def test_put_rejects_bad_values_without_crashing(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    r = client.put("/api/settings", json={
        "poll_interval": 0.01,            # below floor -> clamped to 0.2
        "phase0_offsets": "nope",         # wrong type -> ignored
        "quiet_hours": {"enabled": True, "start": "25:99", "end": "07:00"},  # bad time -> ignored
    })
    assert r.status_code == 200
    body = r.json()
    assert body["poll_interval"] == 0.2
    assert body["phase0_offsets"] == [0, 10, 30, 60, 300]  # unchanged default


def test_put_updates_exchange_poll_intervals_and_tiers(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    r = client.put("/api/settings", json={
        "poll_interval_binance": 0.1,     # below floor -> clamped to 0.5
        "poll_interval_bithumb": 7.0,
        "subscriber_tiers": {"instant": -5, "delayed": "30", "": 10},  # negative clamped, str coerced, blank name dropped
    })
    assert r.status_code == 200
    body = r.json()
    assert body["poll_interval_binance"] == 0.5
    assert body["poll_interval_bithumb"] == 7.0
    assert body["subscriber_tiers"] == {"instant": 0.0, "delayed": 30.0}


def test_put_ignores_empty_or_invalid_tiers(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    r = client.put("/api/settings", json={"subscriber_tiers": "nope"})
    assert r.status_code == 200
    assert r.json()["subscriber_tiers"] == {"instant": 0, "delayed": 30, "free": 120}


def test_exchanges_endpoint_lists_all_four(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    r = client.get("/api/exchanges")
    assert r.status_code == 200
    ids = {row["exchange"] for row in r.json()}
    assert ids == {"upbit", "binance", "bithumb", "coinbase"}


def test_exchange_control_unknown_id_404s(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    assert client.post("/api/exchanges/nope/start").status_code == 404


def test_subscribers_crud(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    r = client.post("/api/subscribers", json={"chat_id": "42", "name": "Alice", "tier": "instant"})
    assert r.status_code == 200
    sub_id = r.json()["id"]

    assert client.post("/api/subscribers", json={"chat_id": "x", "tier": "not-a-real-tier"}).status_code == 400

    r = client.put(f"/api/subscribers/{sub_id}", json={"tier": "free"})
    assert r.status_code == 200
    assert r.json()["tier"] == "free"

    assert client.put("/api/subscribers/999999", json={"tier": "free"}).status_code == 404

    r = client.get("/api/subscribers")
    assert len(r.json()) == 1

    assert client.delete(f"/api/subscribers/{sub_id}").status_code == 200
    assert client.delete(f"/api/subscribers/{sub_id}").status_code == 404
