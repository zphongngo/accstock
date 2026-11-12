"""Send messages through the Telegram Bot API (token + chat IDs from telegram.env)."""
from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4000  # Telegram limit is 4096 characters per message


class TelegramError(RuntimeError):
    pass


class TelegramService:
    def __init__(self) -> None:
        self.s = get_settings()

    @property
    def chat_ids(self) -> list[str]:
        return [c.strip() for c in self.s.TELEGRAM_CHAT_ID.split(",") if c.strip()]

    @property
    def configured(self) -> bool:
        return bool(self.s.TELEGRAM_BOT_TOKEN and self.chat_ids)

    def _call(self, method: str, _timeout: float = 30, **payload) -> dict:
        if not self.s.TELEGRAM_BOT_TOKEN:
            raise TelegramError("TELEGRAM_BOT_TOKEN chưa được đặt trong telegram.env")
        r = httpx.post(API.format(token=self.s.TELEGRAM_BOT_TOKEN, method=method), json=payload, timeout=_timeout)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if not body.get("ok"):
            # Never include the URL: it contains the bot token.
            raise TelegramError(f"Telegram {method} lỗi {r.status_code}: {body.get('description', r.text[:200])}")
        return body["result"]

    def get_updates(self, offset: int | None = None, timeout: int = 0) -> list[dict]:
        """Incoming messages (long polling when `timeout` > 0). `offset` = last update_id + 1 confirms the others."""
        payload = {"timeout": timeout, "allowed_updates": ["message", "channel_post"]}
        if offset is not None:
            payload["offset"] = offset
        return self._call("getUpdates", _timeout=timeout + 15, **payload)

    def reply(self, chat_id: str, text: str, reply_to: int | None = None) -> int:
        """Answer in one chat (bot commands), split like `send`."""
        sent = 0
        for part in split_message(text):
            extra = {"reply_to_message_id": reply_to, "allow_sending_without_reply": True} if reply_to else {}
            self._call("sendMessage", chat_id=chat_id, text=part, parse_mode="HTML",
                       disable_web_page_preview=True, **extra)
            sent += 1
        return sent

    def send(self, text: str) -> int:
        """Send `text` (HTML) to every chat ID, split into chunks <= MAX_LEN. Returns messages sent."""
        if not self.chat_ids:
            raise TelegramError("TELEGRAM_CHAT_ID chưa được điền trong telegram.env")
        sent = 0
        for chat in self.chat_ids:
            for part in split_message(text):
                self._call("sendMessage", chat_id=chat, text=part, parse_mode="HTML",
                           disable_web_page_preview=True)
                sent += 1
        return sent


def split_message(text: str, limit: int = MAX_LEN) -> list[str]:
    """Split on line boundaries so HTML tags (kept within one line) are never cut."""
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # a single overlong line: hard split
            parts.append(line[:limit])
            line = line[limit:]
        if cur and len(cur) + 1 + len(line) > limit:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts
