import asyncio

from broadcast import fanout


class FakeTelegram:
    def __init__(self):
        self.calls = []

    async def send(self, text, chat_id=None):
        self.calls.append((chat_id, text))
        return {"ok": True}


def _logs():
    entries = []
    return entries, (lambda level, msg: entries.append((level, msg)))


def test_fanout_delivers_instant_before_delayed():
    async def run():
        telegram = FakeTelegram()
        subs = [
            {"chat_id": "slow", "tier": "delayed", "enabled": True},
            {"chat_id": "fast", "tier": "instant", "enabled": True},
        ]
        tiers = {"instant": 0, "delayed": 0.05}
        order = []

        async def send(text, chat_id=None):
            order.append(chat_id)
            return {"ok": True}

        telegram.send = send
        entries, log = _logs()
        tasks = fanout(telegram, subs, tiers, "hello", log)
        assert len(tasks) == 2
        await asyncio.gather(*tasks)
        assert order == ["fast", "slow"]

    asyncio.run(run())


def test_fanout_skips_disabled_subscribers():
    async def run():
        telegram = FakeTelegram()
        subs = [{"chat_id": "off", "tier": "instant", "enabled": False}]
        entries, log = _logs()
        tasks = fanout(telegram, subs, {"instant": 0}, "hello", log)
        assert tasks == []
        await asyncio.sleep(0)
        assert telegram.calls == []

    asyncio.run(run())


def test_fanout_unknown_tier_defaults_to_instant_delivery():
    async def run():
        telegram = FakeTelegram()
        subs = [{"chat_id": "x", "tier": "nonexistent", "enabled": True}]
        entries, log = _logs()
        tasks = fanout(telegram, subs, {"instant": 0}, "hello", log)
        await asyncio.gather(*tasks)
        assert telegram.calls == [("x", "hello")]

    asyncio.run(run())


def test_fanout_logs_failed_delivery():
    async def run():
        class FailingTelegram:
            async def send(self, text, chat_id=None):
                return {"ok": False, "error": "boom"}

        subs = [{"chat_id": "x", "name": "Alice", "tier": "instant", "enabled": True}]
        entries, log = _logs()
        tasks = fanout(FailingTelegram(), subs, {"instant": 0}, "hello", log)
        await asyncio.gather(*tasks)
        assert len(entries) == 1
        assert entries[0][0] == "error"
        assert "Alice" in entries[0][1]

    asyncio.run(run())
