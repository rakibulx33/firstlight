"""Telegram notifier — raw Bot API sendMessage via aiohttp (as in the doc).

Token/chat_id are mutable so the Settings panel can update them at runtime.
"""
import aiohttp


class Telegram:
    def __init__(self, token: str | None = None, chat_id: str | None = None):
        self.token = token or None
        self.chat_id = chat_id or None

    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    async def send(self, text: str, chat_id: str | None = None) -> dict:
        """Send to an explicit `chat_id`, or `self.chat_id` (legacy single-recipient
        Settings field, used by the "Send test message" button) if omitted."""
        target = chat_id or self.chat_id
        if not self.token or not target:
            return {"ok": False, "error": "Telegram not configured (set token + chat id in Settings)"}
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    url,
                    json={"chat_id": target, "text": text},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    return await r.json()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
