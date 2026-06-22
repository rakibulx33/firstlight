# LOCALHOST_BUILD_PLAN.md

How to build and run the Upbit listing bot **on my own machine** (HP Victus, Windows 11 + WSL2) — before spending a cent on a VPS. Companion to `UPBIT_LISTING_BOT_PLAN.md` (strategy, phases, risk rules live there).

---

## What runs well locally vs not (honest scope)

| Stage | Local OK? | Why |
|---|---|---|
| **Phase 0 — backtest/observe** | ✅ **Yes, do it all locally for $0** | Latency doesn't matter — I'm just logging listings + price snapshots to measure the pattern. |
| **Phase 1-3 — dev & testing** | ✅ Yes | Build, debug, paper-trade against testnet all from the laptop. |
| **Live alerts I act on** | ⚠️ Marginal | RTT from Bangladesh to Upbit (Seoul) is ~150-250ms + residential jitter. Fine to learn on, not to win on. |
| **Phase 4 — live auto-trade** | ❌ No | Need always-on + a static IP to whitelist exchange keys + low RTT. → Seoul VM. |

> **Rule: build + validate everything locally, deploy to a Seoul VM only when going live.** The multi-week Phase 0 backtest is the perfect local job — keep the laptop plugged in and awake, collect data for $0, *then* decide on a VPS.

---

## Target environment: WSL2 (Ubuntu 24.04)

Use the WSL2 Ubuntu I already have, not native Windows Python. Reasons: clean Linux async networking, and it's the **same OS as the eventual Ubuntu VPS** — so the code I build locally deploys unchanged. That's the whole point.

---

## Step 1 — WSL2 + Python

```bash
wsl                       # drop into Ubuntu 24.04
sudo apt update && sudo apt install -y python3-venv python3-pip tmux
python3 --version         # expect 3.12.x on 24.04
```

## Step 2 — Project + virtualenv

```bash
mkdir -p ~/upbit-bot && cd ~/upbit-bot
python3 -m venv .venv && source .venv/bin/activate
pip install aiohttp python-telegram-bot python-dotenv
```

`requirements.txt`:
```
aiohttp
python-telegram-bot
python-dotenv
# Phase 3-4: ccxt   (or pybit / python-binance)
```

## Step 3 — Telegram bot + `.env`

1. Message **@BotFather** → `/newbot` → copy the token.
2. Message your new bot once, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` to grab your `chat.id`.

`.env` (add to `.gitignore`):
```
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=987654321
```

## Step 4 — Minimal detector (Loop A) — copy-runnable

`detector.py`:
```python
import asyncio, aiohttp, sqlite3, os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
MARKET_URL = "https://api.upbit.com/v1/market/all"
POLL = 1.0  # seconds — 1/s is well within Upbit's per-IP limit

db = sqlite3.connect("state.db")
db.execute("CREATE TABLE IF NOT EXISTS seen(market TEXT PRIMARY KEY, ts TEXT)")
db.commit()

async def alert(session, text):
    await session.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text},
    )

async def main():
    seen = {r[0] for r in db.execute("SELECT market FROM seen")}
    first_run = not seen                      # seed silently on first ever run
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(MARKET_URL, timeout=5) as r:
                    data = await r.json()
                current = {m["market"] for m in data}
                new = current - seen
                now = datetime.now(timezone.utc).isoformat()
                if first_run:
                    db.executemany("INSERT OR IGNORE INTO seen VALUES(?,?)",
                                   [(m, now) for m in current])
                    db.commit(); first_run = False
                elif new:
                    for mk in new:
                        info = next(x for x in data if x["market"] == mk)
                        await alert(session,
                            f"🚨 UPBIT NEW MARKET: {mk}\n"
                            f"{info.get('english_name','')} "
                            f"({info.get('korean_name','')})\n{now}")
                        db.execute("INSERT OR IGNORE INTO seen VALUES(?,?)", (mk, now))
                        print("NEW:", mk, now)
                    db.commit()
                seen = current
            except Exception as e:
                print("poll error:", e)
            await asyncio.sleep(POLL)

if __name__ == "__main__":
    asyncio.run(main())
```

This seeds existing markets silently on first launch, then alerts **only** on genuinely new markets, with SQLite dedup that survives restarts.

## Step 5 — Run it

```bash
source .venv/bin/activate
python detector.py        # first run seeds silently; leave it running
```

You should see no alerts until Upbit actually adds a market — then a Telegram ping in ~1-2s.

## Step 6 — Keep it alive locally

Pick one:

- **tmux (simplest):** `tmux new -s bot` → run it → detach with `Ctrl-b d`. Reattach: `tmux attach -t bot`.
- **PM2 (I've used it for n8n):** `pm2 start detector.py --name upbit --interpreter ./.venv/bin/python` then `pm2 save`.
- **WSL systemd:** enable in `/etc/wsl.conf` (`[boot]\nsystemd=true`), `wsl --shutdown`, then a normal `systemd` unit — closest to the production setup.

⚠️ **Laptop sleep/hibernate kills the bot and WSL.** For the Phase 0 backtest: plug in, set Windows power to "never sleep," and ideally disable WSL auto-shutdown. If keeping the laptop awake for weeks is impractical, *that's the cue* to move Phase 0 onto the free Oracle Seoul box.

---

## Phase 0 local workflow (the main reason to run locally)

Extend `detector.py` to log instead of just alert: on each new market, snapshot the Binance/Bybit price at **+0s / +10s / +30s / +60s / +5m** into a `listings` table. Run 2-3 weeks. Then measure: for *my* latency, how big is the entry window and the pump? That dataset decides whether live trading is worth a VPS — all collected for $0.

---

## Local-specific gotchas

- **Notice endpoint + residential IP:** the undocumented Upbit notice API is behind Cloudflare and may challenge/429 a home IP. `market/all` (Loop A) is unaffected — keep it primary; treat Loop B as best-effort locally.
- **No static IP:** can't IP-whitelist Binance/Bybit keys from a dynamic home connection — another reason **live trading = VPS**. Doesn't affect Phase 0-1.
- **Timezone:** log everything in **UTC**; Upbit announcement times are **KST (UTC+9)**.
- **Outbound only:** no port-forwarding/firewall changes needed — the bot only makes outbound requests.

---

## Bridge to production (when to leave localhost)

**Move to a Seoul VM when:** I start acting on alerts for real, or wire up auto-trade.

**What changes:** almost nothing — same WSL/Ubuntu code copies straight to an Oracle Cloud **Seoul** Always Free ARM box (or a ~$5-12/mo Tokyo/Seoul VPS). Add: a `systemd` service, the 10-min keep-alive cron (Oracle reclaims idle instances ~7 days), a static IP for exchange-key whitelisting, and trade-only API keys (no withdrawal). Building on WSL2 Ubuntu now means zero rewrite later.

---

*Next: I can extend `detector.py` with the Phase 0 price-snapshot logger, or add the Loop B notice poller once you've captured the live endpoint from dev-tools.*
