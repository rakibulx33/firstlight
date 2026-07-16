"""Fan a single alert out to every enabled subscriber, each delayed by their tier.

Fire-and-forget: `fanout()` schedules one asyncio task per subscriber and returns
immediately -- a slow (high-delay-tier) subscriber never blocks the poll loop,
other subscribers, or Phase 0 scheduling. Gating (alert_on_listing/notice/error +
quiet hours, via alerts.alert_allowed) is the caller's job, applied once before
calling fanout -- tiers only control delay, never whether an event alerts at all.
"""
import asyncio


async def _send_one(telegram, sub: dict, delay: float, text: str, log) -> None:
    if delay > 0:
        await asyncio.sleep(delay)
    res = await telegram.send(text, chat_id=sub["chat_id"])
    if not res.get("ok"):
        who = sub.get("name") or sub["chat_id"]
        log("error", f"Telegram alert to {who} failed: {res.get('error', res)}")


def fanout(telegram, subscribers: list[dict], tiers: dict, text: str, log) -> list[asyncio.Task]:
    tasks = []
    for sub in subscribers:
        if not sub.get("enabled"):
            continue
        delay = float(tiers.get(sub.get("tier"), 0.0))  # unknown tier -> deliver immediately
        tasks.append(asyncio.create_task(_send_one(telegram, sub, delay, text, log)))
    return tasks
