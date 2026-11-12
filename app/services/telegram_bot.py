"""Telegram bot commands, answered while the app runs: /ck <symbol> (technical summary), /help.

Long polling (`getUpdates`, 10 s) inside the FastAPI lifespan, like the schedulers. Only chats listed in
TELEGRAM_CHAT_ID get an answer; anything else is ignored. Messages older than STALE_SECONDS when the app starts
(sent while it was down) are skipped. Turn it off with TELEGRAM_COMMANDS=false.

Telegram allows ONE getUpdates consumer per bot token: if the same token runs in two places (e.g. the Windows
app and the Linux app, or a webhook set by another tool), Telegram answers 409 Conflict and only one of them gets
the commands.
"""
from __future__ import annotations

import asyncio
import html
import logging
import time

from app.analysis.stock_brief import HELP, SYMBOL_RE, build_ck_message, parse_command
from app.core.config import get_settings
from app.services.telegram_service import TelegramError, TelegramService

log = logging.getLogger(__name__)
POLL_TIMEOUT = 10
STALE_SECONDS = 300
ERROR_PAUSE = 30


def answer_ck(symbol: str) -> str:
    from app.analysis.stock_scanner import analyze_dataframe
    from app.api.deps import get_board_service, get_stock_service

    df, _ = get_stock_service().get_ohlcv(symbol, refresh=True, timeframe="1D")
    a = analyze_dataframe(symbol, df, "1D")
    from app.services.priceboard_service import avg_volume_20

    try:
        quote = get_board_service().quotes([symbol]).get(symbol)
    except Exception:  # noqa: BLE001  (the summary is still useful without the live quote)
        quote = None
    if quote:   # %V20p as on the Bảng điện bulletin: today's volume / average of the 20 sessions before today
        avg = avg_volume_20(df)
        quote = {**quote, "vol_ratio": round(quote["volume"] / avg, 4) if avg and quote.get("volume") else None}
    return build_ck_message(a, quote)


def reply_for(text: str, ck=answer_ck) -> str | None:
    """The answer to one message text, or None when it isn't a command of this bot."""
    parsed = parse_command(text)
    if not parsed:
        return None
    cmd, args = parsed
    if cmd in ("start", "help"):
        return HELP
    if cmd != "ck":
        return None
    if not args:
        return HELP
    sym = args[0]
    if not SYMBOL_RE.match(sym):
        return f"Mã không hợp lệ: {html.escape(sym[:20])}\n{HELP}"
    try:
        return ck(sym)
    except Exception as e:  # noqa: BLE001
        log.warning("/ck %s: %s", sym, e)
        return f"Không lấy được dữ liệu mã <b>{sym}</b>. Kiểm tra lại mã chứng khoán."


def handle_update(update: dict, allowed: set[str], started: float, tg: TelegramService, ck=answer_ck) -> bool:
    """Answer one update if it is a command from an allowed chat. Returns True when a reply was sent."""
    msg = update.get("message") or update.get("channel_post") or {}
    chat = str((msg.get("chat") or {}).get("id", ""))
    if not msg.get("text") or chat not in allowed or msg.get("date", 0) < started - STALE_SECONDS:
        return False
    answer = reply_for(msg["text"], ck)
    if not answer:
        return False
    tg.reply(chat, answer, reply_to=msg.get("message_id"))
    return True


async def run_telegram_bot(stop: asyncio.Event) -> None:
    s = get_settings()
    tg = TelegramService()
    if not s.TELEGRAM_COMMANDS or not tg.configured:
        return
    allowed, started, offset, warned = set(tg.chat_ids), time.time(), None, False
    log.info("Telegram bot: trả lời lệnh /ck trong %d chat.", len(allowed))
    while not stop.is_set():
        try:
            updates = await asyncio.to_thread(tg.get_updates, offset, POLL_TIMEOUT)
            warned = False
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    await asyncio.to_thread(handle_update, u, allowed, started, tg)
                except Exception:  # noqa: BLE001  (one bad message must not stop the bot)
                    log.exception("Telegram bot: không trả lời được tin nhắn.")
            continue
        except TelegramError as e:
            if not warned:   # e.g. 409 Conflict: the same token is polled elsewhere, or a webhook is set
                log.warning("Telegram bot: %s (thử lại sau %d giây)", e, ERROR_PAUSE)
                warned = True
        except Exception as e:  # noqa: BLE001  (network down, proxy...)
            if not warned:
                log.warning("Telegram bot: lỗi kết nối %s (thử lại sau %d giây)", e, ERROR_PAUSE)
                warned = True
        try:
            await asyncio.wait_for(stop.wait(), timeout=ERROR_PAUSE)
        except asyncio.TimeoutError:
            pass
