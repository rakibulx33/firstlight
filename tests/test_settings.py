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
