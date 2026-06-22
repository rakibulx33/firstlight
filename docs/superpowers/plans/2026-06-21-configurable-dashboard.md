# Configurable Dashboard + UI/UX Refresh — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Upbit Watch control panel fully user-configurable (detector, alerts, appearance, display) and refine its UI/UX, keeping the dark identity and adding light mode + accent theming.

**Architecture:** Keep the no-build-step, CDN-library FastAPI app. Split the single `static/index.html` into `index.html` + `app.js` + `theme.css`. Server tunables persist to `config.json`/`.env` (one typed, validated config) and affect the running bot; UI preferences persist to browser `localStorage`. Theming uses CSS custom properties as RGB channel triplets so existing Tailwind alpha utilities keep working.

**Tech Stack:** Python 3.12, FastAPI, Alpine.js 3 (CDN), Tailwind (CDN JIT), Chart.js 4, Lucide. Tests: pytest + FastAPI TestClient (`httpx`). Frontend verification: Playwright MCP.

## Global Constraints

- **No build step.** All libraries via CDN; assets served statically. Code must deploy unchanged to an Ubuntu/Seoul VPS.
- **Run Python only as** `.venv/bin/python -m …` (the venv was pip-bootstrapped manually). Never call bare `pip`/`pytest`.
- **Secrets never leak.** `GET /api/settings` returns `telegram_token_set: bool`, never the token; `.env` stays git-ignored.
- **Backward compatible.** A `config.json` missing new keys must load fine — the loader fills defaults.
- **Non-fatal everywhere.** Detector/notice/phase0 loops already wrap polls in try/except; new code must not introduce crashes (a disabled snapshot source records `NULL`, not an error).
- **Timezone:** logs/timestamps are UTC ISO. Quiet-hours compares against **server local time** (`datetime.now()`) — document this in the UI helptext.
- **Commit after every task** with the message shown in the task's final step.

---

## File map

| File | Responsibility | Action |
|---|---|---|
| `app.py` | Typed config (defaults+migration+validation), expanded `/api/settings`, StaticFiles mount, pass config to phase0 | Modify |
| `alerts.py` | `alert_allowed(config, kind, now)` — per-event flags + quiet hours | Create |
| `phase0.py` | Read `phase0_offsets` / `phase0_sources` from config | Modify |
| `notice.py` | `parse_notice` takes keyword lists; poller reads `notice_keywords` | Modify |
| `detector.py` | Gate Telegram in `_handle_new` via `alert_allowed` | Modify |
| `config.json` | Expanded server-side schema | Modify (runtime) |
| `static/index.html` | Markup/templates only; FOUC head script; sectioned settings panel | Modify |
| `static/app.js` | Alpine `app()` logic + prefs/theme store + display helpers | Create (extract) |
| `static/theme.css` | CSS-variable themes (dark/light), accents, density, font scale | Create |
| `tests/` | pytest suites | Create |

---

### Task 1: Typed server config + expanded `/api/settings`

**Files:**
- Modify: `app.py` (`load_config`, `api_get_settings`, `api_put_settings`)
- Create: `tests/conftest.py`, `tests/test_settings.py`
- Modify: `requirements.txt` (add test deps as comment) + install pytest/httpx into venv

**Interfaces:**
- Consumes: existing `config` dict, `save_config`, `persist_env`, `telegram`.
- Produces: `CONFIG_DEFAULTS` dict; `validate_config_updates(body: dict) -> tuple[dict, dict]` returning `(clean_updates, errors)`; expanded `GET /api/settings` response containing every server tunable below; `PUT /api/settings` applying them.

Server config keys (defaults): `poll_interval=1.0`, `poll_interval_notice=8.0`, `autostart=True`, `phase0_offsets=[0,10,30,60,300]`, `phase0_sources={"bybit":True,"binance":True}`, `notice_keywords={"listing":["거래지원","디지털 자산 추가","신규 상장","마켓 추가"],"exclude":["종료","폐지","유의"]}`, `alert_on_listing=True`, `alert_on_notice=True`, `alert_on_error=False`, `quiet_hours={"enabled":False,"start":"23:00","end":"07:00"}`.

- [ ] **Step 1: Install test tooling**

Run:
```bash
cd ~/upbit-bot && .venv/bin/python -m pip install pytest httpx
```
Append to `requirements.txt`:
```
# dev/test (not needed at runtime): pytest, httpx
```

- [ ] **Step 2: Write the failing tests**

Create `tests/conftest.py`:
```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

Create `tests/test_settings.py`:
```python
import json
import importlib
from fastapi.testclient import TestClient


def _client(tmp_path, monkeypatch):
    # Isolate config.json, .env, state.db into a temp dir before importing app.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"poll_interval": 1.0}))
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd ~/upbit-bot && .venv/bin/python -m pytest tests/test_settings.py -v`
Expected: FAIL (KeyErrors / missing defaults).

- [ ] **Step 4: Implement config defaults + validation in `app.py`**

Replace `load_config` and add a defaults constant + validator near the top (after `ENV_PATH`):
```python
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
```

- [ ] **Step 5: Expand `GET`/`PUT /api/settings`**

Replace `api_get_settings` and `api_put_settings`:
```python
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd ~/upbit-bot && .venv/bin/python -m pytest tests/test_settings.py -v`
Expected: 3 passed.

- [ ] **Step 7: Commit**
```bash
cd ~/upbit-bot && git add app.py tests/ requirements.txt && git commit -m "feat: typed server config with validation + expanded /api/settings"
```

---

### Task 2: Phase 0 configurable offsets + sources

**Files:**
- Modify: `phase0.py` (`__init__`, `schedule`, `_collect`; add `_offsets`/`_sources`)
- Modify: `app.py` (pass `config` into `Phase0`)
- Create: `tests/test_phase0_config.py`

**Interfaces:**
- Consumes: `config["phase0_offsets"]`, `config["phase0_sources"]` (Task 1).
- Produces: `Phase0(db_path, bus=None, logger=None, config=None)`; `Phase0._offsets() -> list[int]`; `Phase0._sources() -> dict`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_phase0_config.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/upbit-bot && .venv/bin/python -m pytest tests/test_phase0_config.py -v`
Expected: FAIL (`__init__` has no `config`, no `_offsets`).

- [ ] **Step 3: Implement**

In `phase0.py`, update `__init__`:
```python
    def __init__(self, db_path, bus=None, logger=None, config=None):
        self.db_path = db_path
        self.bus = bus
        self.logger = logger            # callable(level, msg) | None
        self.config = config or {}
        self._tasks: set[asyncio.Task] = set()
```
Add helpers after `_log`:
```python
    def _offsets(self) -> list[int]:
        offs = self.config.get("phase0_offsets") or OFFSETS
        try:
            return sorted({int(x) for x in offs if int(x) >= 0}) or list(OFFSETS)
        except (TypeError, ValueError):
            return list(OFFSETS)

    def _sources(self) -> dict:
        src = self.config.get("phase0_sources") or {}
        return {"bybit": bool(src.get("bybit", True)), "binance": bool(src.get("binance", True))}
```
In `schedule`, change the default-offsets line:
```python
        offs = self._offsets() if offsets is None else list(offsets)
```
In `_collect`, gate each source (record `None` when disabled so the chart still aligns):
```python
                ts = utcnow_iso()
                src = self._sources()
                bybit = await self._bybit(session, symbol) if src["bybit"] else None
                binance = await self._binance(session, symbol) if src["binance"] else None
```
Also update the `resume_pending` reference to `OFFSETS` to use `self._offsets()`:
```python
                remaining = [
                    o for o in self._offsets() if o not in done and (det + timedelta(seconds=o)) > now
                ]
```

- [ ] **Step 4: Pass config in `app.py`**

Change the `phase0 = Phase0(...)` construction:
```python
phase0 = Phase0(DB, bus=bus, config=config)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd ~/upbit-bot && .venv/bin/python -m pytest tests/test_phase0_config.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**
```bash
cd ~/upbit-bot && git add phase0.py app.py tests/ && git commit -m "feat: configurable Phase 0 offsets and snapshot sources"
```

---

### Task 3: Notice keywords configurable

**Files:**
- Modify: `notice.py` (`parse_notice` signature, `NoticePoller._store`, add `_keywords`)
- Create: `tests/test_notice.py`

**Interfaces:**
- Consumes: `config["notice_keywords"]` (Task 1).
- Produces: `parse_notice(title, listing_kw=LISTING_KW, exclude_kw=EXCLUDE_KW) -> tuple[str|None, bool]`; `NoticePoller._keywords() -> tuple[list, list]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_notice.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/upbit-bot && .venv/bin/python -m pytest tests/test_notice.py -v`
Expected: FAIL (`parse_notice` takes 1 arg).

- [ ] **Step 3: Implement**

In `notice.py`, change `parse_notice`:
```python
def parse_notice(title: str, listing_kw=LISTING_KW, exclude_kw=EXCLUDE_KW) -> tuple[str | None, bool]:
    """Return (ticker, is_listing) from an announcement title."""
    title = title or ""
    m = TICKER_RE.search(title)
    ticker = m.group(1) if m else None
    is_listing = (
        bool(ticker)
        and any(k in title for k in listing_kw)
        and not any(k in title for k in exclude_kw)
    )
    return ticker, is_listing
```
Add a helper in `NoticePoller` (after `poll_interval`):
```python
    def _keywords(self):
        nk = self.config.get("notice_keywords") or {}
        return (nk.get("listing") or list(LISTING_KW), nk.get("exclude") or list(EXCLUDE_KW))
```
In `_store`, use them:
```python
        listing_kw, exclude_kw = self._keywords()
        ticker, is_listing = parse_notice(n.get("title", ""), listing_kw, exclude_kw)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/upbit-bot && .venv/bin/python -m pytest tests/test_notice.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**
```bash
cd ~/upbit-bot && git add notice.py tests/ && git commit -m "feat: configurable Loop B listing/exclude keywords"
```

---

### Task 4: Alert gating — flags + quiet hours

**Files:**
- Create: `alerts.py`
- Modify: `detector.py` (`_handle_new`), `notice.py` (`_handle`)
- Create: `tests/test_alerts.py`

**Interfaces:**
- Consumes: `config` flags + `quiet_hours` (Task 1); engine/poller `self.config`, `self.notifier`.
- Produces: `alerts.alert_allowed(config: dict, kind: str, now: datetime | None = None) -> bool` where `kind ∈ {"listing","notice","error"}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_alerts.py`:
```python
from datetime import datetime
from alerts import alert_allowed


def test_flags():
    assert alert_allowed({"alert_on_listing": True}, "listing") is True
    assert alert_allowed({"alert_on_listing": False}, "listing") is False
    assert alert_allowed({}, "error") is False          # error defaults off
    assert alert_allowed({"alert_on_notice": True}, "notice") is True


def test_quiet_hours_same_day():
    cfg = {"alert_on_listing": True, "quiet_hours": {"enabled": True, "start": "09:00", "end": "17:00"}}
    assert alert_allowed(cfg, "listing", now=datetime(2026, 6, 21, 12, 0)) is False
    assert alert_allowed(cfg, "listing", now=datetime(2026, 6, 21, 20, 0)) is True


def test_quiet_hours_overnight_wrap():
    cfg = {"alert_on_listing": True, "quiet_hours": {"enabled": True, "start": "23:00", "end": "07:00"}}
    assert alert_allowed(cfg, "listing", now=datetime(2026, 6, 21, 2, 0)) is False
    assert alert_allowed(cfg, "listing", now=datetime(2026, 6, 21, 23, 30)) is False
    assert alert_allowed(cfg, "listing", now=datetime(2026, 6, 21, 12, 0)) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/upbit-bot && .venv/bin/python -m pytest tests/test_alerts.py -v`
Expected: FAIL (no `alerts` module).

- [ ] **Step 3: Implement `alerts.py`**
```python
"""Alert gating: per-event flags + quiet hours (server-local time)."""
from datetime import datetime

_DEFAULT_FLAG = {"listing": True, "notice": True, "error": False}


def _in_quiet(qh: dict, now: datetime) -> bool:
    if not qh or not qh.get("enabled"):
        return False
    try:
        sh, sm = (int(x) for x in str(qh["start"]).split(":"))
        eh, em = (int(x) for x in str(qh["end"]).split(":"))
    except (KeyError, ValueError, TypeError):
        return False
    cur = now.hour * 60 + now.minute
    start, end = sh * 60 + sm, eh * 60 + em
    if start == end:
        return False
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end          # overnight wrap


def alert_allowed(config: dict, kind: str, now: datetime | None = None) -> bool:
    if not config.get(f"alert_on_{kind}", _DEFAULT_FLAG.get(kind, False)):
        return False
    return not _in_quiet(config.get("quiet_hours") or {}, now or datetime.now())
```

- [ ] **Step 4: Gate the senders**

In `detector.py`, add `from alerts import alert_allowed` at top, and in `_handle_new` change the notifier block:
```python
        if self.notifier and alert_allowed(self.config, "listing"):
            res = await self.notifier.send(
                f"\U0001F6A8 UPBIT NEW MARKET: {market}\n{english} ({korean})\n{now}"
            )
            if not res.get("ok"):
                self.log("error", f"Telegram alert failed: {res.get('error', res)}")
```
In `notice.py`, add `from alerts import alert_allowed` at top, and in `_handle` change:
```python
        if listing and self.notifier and alert_allowed(self.config, "notice"):
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd ~/upbit-bot && .venv/bin/python -m pytest tests/ -v`
Expected: all green (Tasks 1–4 suites).

- [ ] **Step 6: Commit**
```bash
cd ~/upbit-bot && git add alerts.py detector.py notice.py tests/ && git commit -m "feat: alert gating via per-event flags + quiet hours"
```

---

### Task 5: Split static assets (refactor, no behavior change)

**Files:**
- Create: `static/app.js` (move the inline `function app(){…}` verbatim), `static/theme.css` (move the inline `<style>` block verbatim)
- Modify: `static/index.html` (reference external files), `app.py` (mount StaticFiles)

**Interfaces:**
- Produces: `/static/app.js`, `/static/theme.css` served; `index.html` unchanged in behavior.

- [ ] **Step 1: Mount StaticFiles in `app.py`**

Add import `from fastapi.staticfiles import StaticFiles` and, after `app = FastAPI(...)`:
```python
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
```

- [ ] **Step 2: Extract `theme.css`** — cut the entire contents of the `<style>…</style>` block in `index.html` into `static/theme.css`; in `<head>` replace it with `<link rel="stylesheet" href="/static/theme.css" />`.

- [ ] **Step 3: Extract `app.js`** — cut the entire `<script> function app(){…} </script>` block (the one defining `app()`, not the CDN tags) into `static/app.js` (drop the surrounding `<script>` tags); before `</body>` add `<script src="/static/app.js"></script>` **above** the Alpine CDN tag is not required — keep Alpine's `defer` tag where it is and place `app.js` before it.

- [ ] **Step 4: Verify (Playwright MCP), no behavior change**

Restart the server (`tmux kill-session -t upbit 2>/dev/null; tmux new -d -s upbit ~/upbit-bot/run.sh`), then with Playwright MCP: navigate `http://localhost:8000`, confirm the page renders, the four tabs switch, the `live` pill turns green (WS connected), and Settings drawer opens. Take a screenshot for the dark-mode baseline.

- [ ] **Step 5: Commit**
```bash
cd ~/upbit-bot && git add app.py static/ && git commit -m "refactor: split dashboard into index.html + app.js + theme.css"
```

---

### Task 6: Theme engine — CSS variables, Tailwind repoint, prefs store, FOUC

**Files:**
- Modify: `static/theme.css` (CSS variables for dark/light, accent, density, font scale)
- Modify: `static/index.html` (Tailwind config repoint; FOUC `<head>` script)
- Modify: `static/app.js` (prefs store: `loadPrefs/savePrefs/applyPrefs`)

**Interfaces:**
- Consumes: `localStorage["upbitwatch.prefs"]`.
- Produces: `<html>` carries `data-theme` / `data-accent` / `data-density` / font-scale; `app()` exposes `prefs` + `setPref(key,val)` + `applyPrefs()`.

- [ ] **Step 1: Define theme variables in `theme.css`**

Prepend (channel triplets so Tailwind alpha utilities work):
```css
:root{
  --bg:15 23 42; --surface:19 29 49; --card:30 41 59; --muted:39 51 73;
  --border:51 65 85; --fg:248 250 252; --sub:148 163 184;
  --primary:245 158 11; --accent:139 92 246;
  --ok:34 197 94; --warn:245 158 11; --danger:239 68 68;
}
:root[data-theme="light"]{
  --bg:248 250 252; --surface:255 255 255; --card:255 255 255; --muted:241 245 249;
  --border:226 232 240; --fg:15 23 42; --sub:71 85 105;
  --primary:217 119 6; --accent:124 58 237;
  --ok:22 163 74; --warn:202 138 4; --danger:220 38 38;
}
:root[data-accent="violet"]{--primary:139 92 246}
:root[data-accent="blue"]{--primary:59 130 246}
:root[data-accent="green"]{--primary:34 197 94}
:root[data-accent="rose"]{--primary:244 63 94}
:root[data-density="compact"]{--space-card:0.75rem}
html{font-size:15px}
:root[data-fontscale="sm"] html, html[data-fontscale="sm"]{font-size:14px}
:root[data-fontscale="lg"] html, html[data-fontscale="lg"]{font-size:16px}
```
Then change the existing hardcoded `html,body{background:#0F172A;color:#F8FAFC;…}` rule to:
```css
html,body{background:rgb(var(--bg));color:rgb(var(--fg));font-family:Inter,system-ui,sans-serif}
```
For compact density, append override rules:
```css
:root[data-density="compact"] .p-5{padding:0.85rem}
:root[data-density="compact"] .py-3{padding-top:0.4rem;padding-bottom:0.4rem}
:root[data-density="compact"] .gap-6{gap:1rem}
```

- [ ] **Step 2: Repoint Tailwind colors in `index.html`**

Replace the `colors:{…}` block in the inline `tailwind.config` with:
```js
        colors: {
          bg: 'rgb(var(--bg) / <alpha-value>)', surface: 'rgb(var(--surface) / <alpha-value>)',
          card: 'rgb(var(--card) / <alpha-value>)', muted: 'rgb(var(--muted) / <alpha-value>)',
          border: 'rgb(var(--border) / <alpha-value>)', fg: 'rgb(var(--fg) / <alpha-value>)',
          sub: 'rgb(var(--sub) / <alpha-value>)', primary: 'rgb(var(--primary) / <alpha-value>)',
          accent: 'rgb(var(--accent) / <alpha-value>)', ok: 'rgb(var(--ok) / <alpha-value>)',
          warn: 'rgb(var(--warn) / <alpha-value>)', danger: 'rgb(var(--danger) / <alpha-value>)'
        },
```

- [ ] **Step 3: Add the FOUC-prevention head script**

Immediately after `<head>` opens (before any stylesheet), add:
```html
  <script>
    (function(){
      try{
        var p = JSON.parse(localStorage.getItem('upbitwatch.prefs')||'{}');
        var root = document.documentElement;
        var theme = p.theme||'dark';
        if(theme==='system') theme = matchMedia('(prefers-color-scheme: light)').matches?'light':'dark';
        root.setAttribute('data-theme', theme);
        root.setAttribute('data-accent', p.accent||'amber');
        root.setAttribute('data-density', p.density||'comfortable');
        root.setAttribute('data-fontscale', p.fontScale||'base');
      }catch(e){}
    })();
  </script>
```

- [ ] **Step 4: Add the prefs store to `app.js`**

In the returned object add a `prefs` field and methods:
```js
        prefs: { theme:'dark', accent:'amber', density:'comfortable', fontScale:'base',
                 defaultTab:'live', visibleTabs:['live','announce','markets','phase0'],
                 visibleCards:['status','about'], timeFormat:'local', tablePageSize:200,
                 numberFormat:{decimals:4, grouping:true}, favorites:[],
                 toastDuration:6000, toastSound:false, toastEvents:['listing','notice'] },
        loadPrefs(){ try{ this.prefs = { ...this.prefs, ...JSON.parse(localStorage.getItem('upbitwatch.prefs')||'{}') }; }catch(e){} },
        savePrefs(){ localStorage.setItem('upbitwatch.prefs', JSON.stringify(this.prefs)); },
        setPref(key, val){ this.prefs[key] = val; this.savePrefs(); this.applyPrefs(); },
        applyPrefs(){
          const root = document.documentElement;
          let theme = this.prefs.theme;
          if(theme==='system') theme = matchMedia('(prefers-color-scheme: light)').matches?'light':'dark';
          root.setAttribute('data-theme', theme);
          root.setAttribute('data-accent', this.prefs.accent);
          root.setAttribute('data-density', this.prefs.density);
          root.setAttribute('data-fontscale', this.prefs.fontScale);
          if(this._chart){ this.renderChart(); }   // re-tint chart axes/legend on theme change
        },
```
Call `this.loadPrefs(); this.applyPrefs();` as the first lines of `init()`.

- [ ] **Step 5: Verify (Playwright MCP)**

Reload `localhost:8000`. Confirm dark mode looks identical to the Task 5 baseline (regression). In the browser console run `localStorage.setItem('upbitwatch.prefs', JSON.stringify({theme:'light'})); location.reload();` and confirm light mode renders with **no flash of dark** on load, readable contrast, and the live feed/cards all themed. Then set `{accent:'violet'}` and confirm buttons/active tab recolor. Reset to `{}`.

- [ ] **Step 6: Commit**
```bash
cd ~/upbit-bot && git add static/ && git commit -m "feat: CSS-variable theming (light/dark, accent, density, font scale) with no FOUC"
```

---

### Task 7: Sectioned settings panel

**Files:**
- Modify: `static/index.html` (replace the settings drawer markup with a sectioned panel)
- Modify: `static/app.js` (settings/prefs bindings + save handlers + validation feedback)

**Interfaces:**
- Consumes: `GET/PUT /api/settings` (Task 1), prefs store (Task 6).
- Produces: a panel with sections **Detector / Alerts / Appearance / Display / Telegram / Reset**; `app()` gains `settingsSection` (active rail item) and `saveServerSettings()`.

- [ ] **Step 1: Extend `settings` state and add save handler in `app.js`**

Extend the `settings` object to include the new server keys and add:
```js
        settingsSection: 'detector',
        async saveServerSettings(){
          const s = this.settings;
          const body = {
            poll_interval: parseFloat(s.poll_interval),
            poll_interval_notice: parseFloat(s.poll_interval_notice),
            autostart: !!s.autostart,
            phase0_offsets: (''+s.phase0_offsets_str).split(',').map(x=>parseInt(x.trim(),10)).filter(n=>!isNaN(n)&&n>=0),
            phase0_sources: s.phase0_sources,
            notice_keywords: { listing: s.kw_listing_str.split(',').map(x=>x.trim()).filter(Boolean),
                               exclude: s.kw_exclude_str.split(',').map(x=>x.trim()).filter(Boolean) },
            alert_on_listing: !!s.alert_on_listing, alert_on_notice: !!s.alert_on_notice, alert_on_error: !!s.alert_on_error,
            quiet_hours: s.quiet_hours,
          };
          if(s.telegram_token) body.telegram_token = s.telegram_token;
          if(s.telegram_chat_id) body.telegram_chat_id = s.telegram_chat_id;
          const res = await (await fetch('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
          this.settings = { ...this.settings, ...res, telegram_token:'',
            phase0_offsets_str: res.phase0_offsets.join(', '),
            kw_listing_str: res.notice_keywords.listing.join(', '),
            kw_exclude_str: res.notice_keywords.exclude.join(', ') };
          this.savedFlash = true; setTimeout(()=>this.savedFlash=false, 1600);
        },
```
Add `savedFlash:false` to state. In `loadSettings()`, after merging, populate the string mirrors:
```js
          this.settings.phase0_offsets_str = (s.phase0_offsets||[]).join(', ');
          this.settings.kw_listing_str = (s.notice_keywords?.listing||[]).join(', ');
          this.settings.kw_exclude_str = (s.notice_keywords?.exclude||[]).join(', ');
```

- [ ] **Step 2: Replace the drawer markup in `index.html`**

Replace the inner content of the settings drawer (keep the outer `x-show="settingsOpen"` overlay + the right panel container, widen it to `max-w-2xl`) with: a **left rail** of buttons bound to `settingsSection`, and a **content area** showing one section at a time via `x-show`. Each section follows this pattern (Detector shown in full; build the others the same way):
```html
<div x-show="settingsSection==='detector'" class="space-y-5">
  <div><label class="block text-sm mb-1.5 text-sub">Loop A poll interval (s) <span class="ml-1 text-[10px] px-1.5 py-0.5 rounded bg-primary/15 text-primary">affects the bot</span></label>
    <input x-model="settings.poll_interval" type="number" step="0.1" min="0.2" class="w-full bg-muted/50 border border-border rounded-lg px-3 py-2 text-sm tabnum"/></div>
  <div><label class="block text-sm mb-1.5 text-sub">Loop B poll interval (s)</label>
    <input x-model="settings.poll_interval_notice" type="number" step="0.5" min="1" class="w-full bg-muted/50 border border-border rounded-lg px-3 py-2 text-sm tabnum"/></div>
  <label class="flex items-center gap-2 text-sm"><input type="checkbox" x-model="settings.autostart"/> Autostart on launch</label>
  <div><label class="block text-sm mb-1.5 text-sub">Phase 0 offsets (seconds, comma-separated)</label>
    <input x-model="settings.phase0_offsets_str" placeholder="0, 10, 30, 60, 300" class="w-full bg-muted/50 border border-border rounded-lg px-3 py-2 text-sm tabnum"/></div>
  <div class="flex gap-4 text-sm"><label class="flex items-center gap-2"><input type="checkbox" x-model="settings.phase0_sources.bybit"/> Bybit</label>
    <label class="flex items-center gap-2"><input type="checkbox" x-model="settings.phase0_sources.binance"/> Binance</label></div>
  <div><label class="block text-sm mb-1.5 text-sub">Loop B listing keywords (comma-separated)</label>
    <input x-model="settings.kw_listing_str" class="w-full bg-muted/50 border border-border rounded-lg px-3 py-2 text-sm"/></div>
  <div><label class="block text-sm mb-1.5 text-sub">Loop B exclude keywords (comma-separated)</label>
    <input x-model="settings.kw_exclude_str" class="w-full bg-muted/50 border border-border rounded-lg px-3 py-2 text-sm"/></div>
</div>
```
Build the remaining sections with the same control styling:
- **Alerts:** three checkboxes `settings.alert_on_listing/notice/error`; quiet-hours `settings.quiet_hours.enabled` checkbox + two `time` inputs `quiet_hours.start`/`end` (helptext: "server local time"); local toast controls bound to `prefs` via `@change="setPref('toastDuration', …)"` etc.
- **Appearance:** segmented buttons / selects calling `setPref('theme',v)`, `setPref('accent',v)`, `setPref('density',v)`, `setPref('fontScale',v)`, with current value highlighted (`:class="prefs.theme===v ? 'border-primary text-fg' : …"`).
- **Display:** `setPref('timeFormat', …)` (local/utc/relative), `setPref('tablePageSize', …)`, number-format decimals/grouping, `setPref('defaultTab', …)`, and checkbox lists toggling membership in `prefs.visibleTabs` / `prefs.visibleCards` (use a `toggleInArray(arr,val)` helper added to `app.js`).
- **Telegram:** move the existing token/chat/test controls here unchanged.
- **Reset:** a "Reset appearance & display" button (`localStorage.removeItem('upbitwatch.prefs'); location.reload()`) and a "Reset bot settings to defaults" button (PUT the `CONFIG_DEFAULTS` values; confirm first).

Add a footer in the panel: a **Save** button bound to `saveServerSettings()` (with `x-show="savedFlash"` "Saved ✓") and a Close button. Add `toggleInArray(arr,val){ const i=arr.indexOf(val); i>-1?arr.splice(i,1):arr.push(val); this.savePrefs(); this.applyPrefs(); }` to `app.js`.

- [ ] **Step 3: Verify (Playwright MCP)**

Open Settings. In **Detector**, change Phase 0 offsets to `0, 5, 15`, untick Binance, Save; confirm the "Saved ✓" flash, then check on disk: `cat ~/upbit-bot/config.json` shows `phase0_offsets:[0,5,15]` and `phase0_sources.binance:false`. In **Appearance**, switch to light + violet and confirm instant recolor and persistence across reload. In **Alerts**, enable quiet hours and Save; confirm `config.json` updates.

- [ ] **Step 4: Commit**
```bash
cd ~/upbit-bot && git add static/ && git commit -m "feat: sectioned settings panel (Detector/Alerts/Appearance/Display/Telegram/Reset)"
```

---

### Task 8: Display preferences wiring

**Files:**
- Modify: `static/app.js` (`fmtTime`, number formatting, pagination, favorites, default tab, visibility), `static/index.html` (star buttons, pagination control, conditional tab/card rendering, toast prefs)

**Interfaces:**
- Consumes: `prefs` (Task 6/7).
- Produces: `fmtTime` honoring `prefs.timeFormat`; `fmtNum(v)`; `pagedMarkets` getter; `toggleFavorite(market)`; tab/card visibility driven by prefs.

- [ ] **Step 1: Implement helpers in `app.js`**

Replace `fmtTime` and add helpers:
```js
        fmtTime(iso){ if(!iso) return '—'; try{ const d=new Date(iso);
          if(this.prefs.timeFormat==='utc') return d.toISOString().slice(11,19)+'Z';
          if(this.prefs.timeFormat==='relative'){ const s=Math.round((Date.now()-d)/1000);
            if(s<60) return s+'s ago'; if(s<3600) return Math.round(s/60)+'m ago'; return Math.round(s/3600)+'h ago'; }
          return d.toLocaleTimeString(); }catch(e){ return iso; } },
        fmtNum(v){ if(v==null) return '—'; const n=Number(v);
          return n.toLocaleString(undefined,{minimumFractionDigits:0,maximumFractionDigits:this.prefs.numberFormat.decimals,useGrouping:this.prefs.numberFormat.grouping}); },
        toggleFavorite(market){ this.toggleInArray(this.prefs.favorites, market); },
        get pagedMarkets(){ return this.filteredMarkets.slice(0, this.prefs.tablePageSize); },
```
Change `filteredMarkets` to sort favorites first:
```js
        get filteredMarkets(){ const f=this.marketFilter.trim().toUpperCase();
          let list = f ? this.markets.filter(m=>m.market.includes(f)) : this.markets.slice();
          const fav = this.prefs.favorites;
          return list.sort((a,b)=>(fav.includes(b.market)?1:0)-(fav.includes(a.market)?1:0)); },
```
In `switchTab`/`init`, honor `prefs.defaultTab`: in `init()` after `loadPrefs()` set `this.tab = this.prefs.defaultTab || 'live'`.

- [ ] **Step 2: Wire markup in `index.html`**

- Markets table: iterate `pagedMarkets` instead of `filteredMarkets`; add a leading star cell `<button @click="toggleFavorite(m.market)"><i :data-lucide="prefs.favorites.includes(m.market)?'star':'star'" :class="prefs.favorites.includes(m.market)?'text-primary fill-primary':'text-sub'"></i></button>`; show `pagedMarkets.length + ' / ' + filteredMarkets.length`.
- Tabs `<template x-for="t in tabs">`: add `x-show="prefs.visibleTabs.includes(t.id)"` on the button.
- Sidebar: wrap the Status card in `x-show="prefs.visibleCards.includes('status')"` and About card in `x-show="prefs.visibleCards.includes('about')"`.
- Toast: change the auto-dismiss timeout in `showToast` to `this.prefs.toastDuration`, gate raising a toast on `this.prefs.toastEvents.includes(kind)`, and if `prefs.toastSound` play a short beep via `new Audio` data URI (optional, guard in try/catch).
- Phase 0 chart price labels / any displayed prices: pass through `fmtNum`.

- [ ] **Step 3: Verify (Playwright MCP)**

Set time format to **relative** (Display) — confirm log/listing timestamps switch to "Ns ago". Star a market in the Markets tab — confirm it jumps to the top and the star persists across reload. Hide the "Announce" tab in Display — confirm it disappears from the tab bar. Set table page size to 25 — confirm the count caps.

- [ ] **Step 4: Commit**
```bash
cd ~/upbit-bot && git add static/ && git commit -m "feat: wire display prefs (time/number format, pagination, favorites, tab/card visibility, toast prefs)"
```

---

### Task 9: Visual refinement, accessibility, mobile, pro review

**Files:**
- Modify: `static/index.html`, `static/theme.css`, `static/app.js` (polish only)

- [ ] **Step 1: Pro UI/UX review pass**

Invoke the `ui-ux-pro-max` skill with action **review/improve** on `static/index.html` + `static/theme.css` (project type: dashboard; stack: Tailwind + Alpine). Apply its concrete recommendations on hierarchy, spacing, empty states, and the light-mode palette. Keep the dark identity.

- [ ] **Step 2: Accessibility**

Settings panel: trap focus while open (on open, focus the first control; `keydown` Tab/Shift+Tab cycles within the panel; ESC closes — `@keydown.escape.window="settingsOpen=false"`). Ensure all icon-only buttons have `aria-label` (audit existing ones). Verify focus-visible rings are present on the new controls.

- [ ] **Step 3: Contrast + mobile check (Playwright MCP)**

Resize to 390×844 (mobile): confirm the top-bar controls wrap sensibly, the sidebar stacks above content, and the settings panel is full-width and scrollable. Check WCAG AA contrast for `--sub` text on `--bg`/`--card` in **both** themes (use the browser devtools or a contrast snippet); darken light-mode `--sub` if it fails. Take dark + light screenshots at desktop and mobile widths.

- [ ] **Step 4: Full regression (Playwright MCP)**

Run through: Start/Stop/Restart, switch all tabs, open Settings each section, Simulate listing (Phase 0 chart renders + re-tints on theme switch), toggle theme/accent/density/font scale. Confirm no console errors.

- [ ] **Step 5: Run the backend suite once more**

Run: `cd ~/upbit-bot && .venv/bin/python -m pytest tests/ -v`
Expected: all pass.

- [ ] **Step 6: Commit**
```bash
cd ~/upbit-bot && git add static/ && git commit -m "polish: UX refinement, light-mode contrast, a11y focus trap, mobile layout"
```

---

## Self-review notes (coverage)

- Spec §3 server config → Tasks 1–4. Spec §3 browser prefs → Tasks 6–8.
- Spec §4 settings surface → Task 7. Spec §5 theming → Task 6. Spec §6 backend wiring → Tasks 2–4.
- Spec §7 components: config service (T1), theme/prefs store (T6), settings panel (T7), display helpers (T8), tunable readers (T2/T3). Spec §8 verification distributed across tasks + T9. Spec §9 risks: alpha-utility refit done before features (T6 before T7/T8); light-mode contrast (T9 step 3); config migration (T1 `load_config`).
- Out-of-scope (drag-reorder, symbol map, layout profiles) intentionally absent.
