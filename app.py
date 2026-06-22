"""Upbit Watch — integrated control panel.

One FastAPI app that owns a single DetectorEngine, supervises it (start/stop/restart),
serves the dark dashboard, and streams status/listings/logs/snapshots over WebSocket.

Run:  uvicorn app:app --host 127.0.0.1 --port 8000
"""
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from detector import DetectorEngine, EventBus
from notice import NoticePoller
from notify import Telegram
from phase0 import Phase0

BASE = Path(os.getenv("UPBIT_HOME") or Path(__file__).parent)
DB = str(BASE / "state.db")
CONFIG_PATH = BASE / "config.json"
ENV_PATH = BASE / ".env"

CONFIG_DEFAULTS = {
    "poll_interval": 1.0,
    "poll_interval_notice": 8.0,
    "autostart": True,
    "phase0_offsets": [0, 10, 30, 60, 300],
    "phase0_sources": {"bybit": True, "binance": True},
    "notice_keywords": {
        "listing": ["거래지원", "디지털 자산 추가", "신규 상장", "마켓 추가"],
        "exclude": ["종료", "폐지", "유의"],
    },
    "alert_on_listing": True,
    "alert_on_notice": True,
    "alert_on_error": False,
    "quiet_hours": {"enabled": False, "start": "23:00", "end": "07:00"},
}


def load_config() -> dict:
    cfg = dict(CONFIG_DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text()))
        except Exception:  # noqa: BLE001
            pass
    return cfg


def _valid_hhmm(s) -> bool:
    try:
        h, m = str(s).split(":")
        return 0 <= int(h) <= 23 and 0 <= int(m) <= 59
    except Exception:  # noqa: BLE001
        return False


def validate_config_updates(body: dict) -> dict:
    """Return only the well-formed, sanitized server-config updates from `body`."""
    out: dict = {}
    if "poll_interval" in body:
        try:
            out["poll_interval"] = max(0.2, float(body["poll_interval"]))
        except (TypeError, ValueError):
            pass
    if "poll_interval_notice" in body:
        try:
            out["poll_interval_notice"] = max(1.0, float(body["poll_interval_notice"]))
        except (TypeError, ValueError):
            pass
    if "autostart" in body:
        out["autostart"] = bool(body["autostart"])
    if "phase0_offsets" in body and isinstance(body["phase0_offsets"], list):
        try:
            offs = sorted({int(x) for x in body["phase0_offsets"] if int(x) >= 0})
            if offs:
                out["phase0_offsets"] = offs
        except (TypeError, ValueError):
            pass
    if "phase0_sources" in body and isinstance(body["phase0_sources"], dict):
        src = {}
        for k in ("bybit", "binance"):
            if k in body["phase0_sources"]:
                src[k] = bool(body["phase0_sources"][k])
        if src:
            out["phase0_sources"] = {**CONFIG_DEFAULTS["phase0_sources"], **src}
    if "notice_keywords" in body and isinstance(body["notice_keywords"], dict):
        nk = {}
        for k in ("listing", "exclude"):
            v = body["notice_keywords"].get(k)
            if isinstance(v, list):
                cleaned = [str(s).strip() for s in v if str(s).strip()]
                if cleaned:
                    nk[k] = cleaned
        if nk:
            out["notice_keywords"] = {**CONFIG_DEFAULTS["notice_keywords"], **nk}
    for flag in ("alert_on_listing", "alert_on_notice", "alert_on_error"):
        if flag in body:
            out[flag] = bool(body[flag])
    if "quiet_hours" in body and isinstance(body["quiet_hours"], dict):
        qh = body["quiet_hours"]
        if _valid_hhmm(qh.get("start", "x")) and _valid_hhmm(qh.get("end", "x")):
            out["quiet_hours"] = {
                "enabled": bool(qh.get("enabled", False)),
                "start": qh["start"],
                "end": qh["end"],
            }
    return out


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def persist_env(key: str, value: str) -> None:
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    for i, ln in enumerate(lines):
        if ln.startswith(key + "="):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n")


load_dotenv(ENV_PATH)
config = load_config()
bus = EventBus()
telegram = Telegram(os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID"))
phase0 = Phase0(DB, bus=bus, config=config)
engine = DetectorEngine(DB, config, notifier=telegram, phase0=phase0, bus=bus)
phase0.logger = engine.log  # route Phase 0 logs through the engine log/bus
# Loop B — announcement poller, started/stopped alongside Loop A by the engine.
notice_poller = NoticePoller(
    DB, config, notifier=telegram, bus=bus, logger=engine.log, on_tick=engine.publish_status
)
engine.notice_poller = notice_poller


@asynccontextmanager
async def lifespan(app):
    # Auto-arm on launch so a reboot + relaunch comes up watching (no manual Start).
    if config.get("autostart", True):
        await engine.start()
    # Re-arm any Phase 0 collections interrupted by the restart (capture remaining offsets).
    phase0.resume_pending()
    yield
    await engine.stop()


app = FastAPI(title="Upbit Watch", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return (BASE / "static" / "index.html").read_text(encoding="utf-8")


@app.post("/api/start")
async def api_start():
    await engine.start()
    return {"ok": True, "status": engine.status()}


@app.post("/api/stop")
async def api_stop():
    await engine.stop()
    return {"ok": True, "status": engine.status()}


@app.post("/api/restart")
async def api_restart():
    await engine.restart()
    return {"ok": True, "status": engine.status()}


@app.get("/api/status")
async def api_status():
    return engine.status()


@app.get("/api/listings")
async def api_listings():
    with engine._conn() as db:
        return [
            dict(r)
            for r in db.execute(
                "SELECT * FROM listings ORDER BY detected_at DESC LIMIT 200"
            )
        ]


@app.get("/api/markets")
async def api_markets():
    with engine._conn() as db:
        return [
            dict(r)
            for r in db.execute("SELECT market, ts FROM seen ORDER BY market")
        ]


@app.get("/api/notices")
async def api_notices():
    with engine._conn() as db:
        return [
            dict(r)
            for r in db.execute("SELECT * FROM notices ORDER BY id DESC LIMIT 200")
        ]


@app.get("/api/logs")
async def api_logs():
    return list(engine.logs)


@app.get("/api/settings")
async def api_get_settings():
    return {
        "poll_interval": float(config.get("poll_interval", 1.0)),
        "poll_interval_notice": float(config.get("poll_interval_notice", 8.0)),
        "autostart": bool(config.get("autostart", True)),
        "phase0_offsets": list(config.get("phase0_offsets", CONFIG_DEFAULTS["phase0_offsets"])),
        "phase0_sources": dict(config.get("phase0_sources", CONFIG_DEFAULTS["phase0_sources"])),
        "notice_keywords": dict(config.get("notice_keywords", CONFIG_DEFAULTS["notice_keywords"])),
        "alert_on_listing": bool(config.get("alert_on_listing", True)),
        "alert_on_notice": bool(config.get("alert_on_notice", True)),
        "alert_on_error": bool(config.get("alert_on_error", False)),
        "quiet_hours": dict(config.get("quiet_hours", CONFIG_DEFAULTS["quiet_hours"])),
        "telegram_chat_id": telegram.chat_id or "",
        "telegram_token_set": bool(telegram.token),
        "telegram_configured": telegram.configured(),
    }


@app.put("/api/settings")
async def api_put_settings(request: Request):
    body = await request.json()
    updates = validate_config_updates(body)
    if updates:
        config.update(updates)
        save_config(config)
    token = (body.get("telegram_token") or "").strip()
    if token:
        telegram.token = token
        persist_env("TELEGRAM_BOT_TOKEN", token)
    chat_id = str(body.get("telegram_chat_id") or "").strip()
    if chat_id:
        telegram.chat_id = chat_id
        persist_env("TELEGRAM_CHAT_ID", chat_id)
    engine.log("info", "Settings updated")
    return await api_get_settings()


@app.post("/api/telegram/test")
async def api_telegram_test():
    res = await telegram.send("✅ Upbit Watch test message — control panel is wired up.")
    ok = bool(res.get("ok"))
    engine.log("info" if ok else "error", f"Telegram test: {'sent' if ok else res}")
    return {"ok": ok, "result": res}


@app.post("/api/simulate")
async def api_simulate():
    """Dev-only: fire a fake new listing to exercise alert + Phase 0 (maps to BTCUSDT)."""
    await engine.simulate_listing()
    return {"ok": True}


@app.get("/api/snapshots/markets")
async def api_snapshot_markets():
    """Distinct markets that have Phase 0 snapshots, most-recent first."""
    with engine._conn() as db:
        return [
            r["market"]
            for r in db.execute(
                "SELECT market, MAX(ts) AS last_ts FROM snapshots "
                "GROUP BY market ORDER BY last_ts DESC"
            )
        ]


@app.get("/api/listings/{market}/snapshots")
async def api_snapshots(market: str):
    with engine._conn() as db:
        return [
            dict(r)
            for r in db.execute(
                "SELECT source,t_offset,price,ts FROM snapshots WHERE market=? ORDER BY t_offset",
                (market,),
            )
        ]


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    q = bus.subscribe()
    try:
        await websocket.send_json({"type": "status", "data": engine.status()})
        while True:
            event = await q.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - client gone / send race
        pass
    finally:
        bus.unsubscribe(q)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
