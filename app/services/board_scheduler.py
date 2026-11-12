"""Interval-based Telegram bulletin scheduler for the price board."""
from __future__ import annotations

import asyncio
import json
import logging
import time as monotonic_time
from datetime import date, datetime, time, timedelta
from pathlib import Path

from app.api.deps import get_board_service
from app.services import board_settings
from app.services.telegram_service import TelegramService

log = logging.getLogger(__name__)
_POLL_SECONDS = 5
_RETRY_SECONDS = 60


def _minutes(value: str) -> int:
    parsed = time.fromisoformat(value)
    return parsed.hour * 60 + parsed.minute


def slot_minutes(settings: dict) -> list[int]:
    """Every slot of a session day as minutes from midnight, given the window and the interval."""
    start, end = _minutes(settings["board_start"]), _minutes(settings["board_end"])
    interval = settings["board_interval"]
    return [] if interval < 1 else list(range(start, end + 1, interval))


def _fmt(minutes_of_day: int) -> str:
    return f"{minutes_of_day // 60:02d}:{minutes_of_day % 60:02d}"


def scheduled_slot(now: datetime, settings: dict) -> str | None:
    """Return the due slot for a weekday within the configured time window."""
    if now.weekday() >= 5:
        return None

    start, end = _minutes(settings["board_start"]), _minutes(settings["board_end"])
    current = now.hour * 60 + now.minute
    if current < start or current > end:
        return None

    slot = start + ((current - start) // settings["board_interval"]) * settings["board_interval"]
    return f"{now.date().isoformat()}T{_fmt(slot)}"


def next_slot(now: datetime, settings: dict) -> dict | None:
    """The next bulletin the scheduler will try to send: {"date", "time", "in_minutes"}.
    Weekdays only (a market holiday still skips that day, see `trading_today`)."""
    todays = [s for s in slot_minutes(settings) if now.date().weekday() < 5
              and s >= now.hour * 60 + now.minute]
    if todays:
        slot = todays[0]
        return {"date": now.date().isoformat(), "time": _fmt(slot),
                "in_minutes": slot - (now.hour * 60 + now.minute)}
    day = now.date()
    for _ in range(14):
        day += timedelta(days=1)
        if day.weekday() >= 5:
            continue
        slots = slot_minutes(settings)
        if not slots:
            return None
        return {"date": day.isoformat(), "time": _fmt(slots[0]),
                "in_minutes": (day - now.date()).days * 1440 + slots[0] - (now.hour * 60 + now.minute)}
    return None


def schedule_info(now: datetime | None = None) -> dict:
    """What the Bảng điện panel shows next to the interval fields, from the persisted settings."""
    config = board_settings.load()
    now = now or datetime.now()
    per_day = len(slot_minutes(config))
    return {"auto_board": config["auto_board"] and per_day > 0, "threshold": config["threshold"],
            "board_start": config["board_start"], "board_end": config["board_end"],
            "board_interval": config["board_interval"], "slots_per_day": per_day,
            "next": next_slot(now, config) if config["auto_board"] else None,
            "last_sent_slot": _last_sent_slot()}


def _state_path() -> Path:
    return board_settings.settings_path().with_name("board_alert_state.json")


def _last_sent_slot() -> str | None:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8")).get("last_sent_slot")
    except (OSError, ValueError, AttributeError):
        return None


def _save_last_sent_slot(slot: str) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"last_sent_slot": slot}), encoding="utf-8")
    tmp.replace(path)


def _send_bulletin(settings: dict) -> tuple[int, int, int, int]:
    telegram = TelegramService()
    if not telegram.configured:
        raise RuntimeError("Telegram chưa cấu hình: điền TELEGRAM_BOT_TOKEN và TELEGRAM_CHAT_ID trong telegram.env")

    messages, strong, weak = get_board_service().movers_message(settings["threshold"], settings["watch"])
    sent = sum(telegram.send(message) for message in messages)
    return sent, strong, weak, len(telegram.chat_ids)


async def run_board_scheduler(stop: asyncio.Event) -> None:
    """Check the persisted board settings and send each due weekday slot once."""
    last_attempt: dict[str, float] = {}
    market_day: date | None = None
    market_is_open: bool | None = None
    market_checked_at = 0.0
    while not stop.is_set():
        try:
            config = board_settings.load()
            now = datetime.now()
            slot = scheduled_slot(now, config) if config["auto_board"] else None
            if market_day != now.date():
                market_day, market_is_open, market_checked_at = now.date(), None, 0.0
            if slot and slot != _last_sent_slot():
                now = monotonic_time.monotonic()
                if market_is_open is None and now - market_checked_at >= _RETRY_SECONDS:
                    market_checked_at = now
                    try:
                        market_is_open = await asyncio.to_thread(get_board_service().trading_today)
                        if not market_is_open:
                            log.info("Hôm nay không có phiên giao dịch; bỏ qua bản tin Telegram.")
                    except Exception:
                        log.exception("Không kiểm tra được ngày giao dịch; tạm hoãn bản tin %s.", slot)
                attempted_at = last_attempt.get(slot, 0)
                if market_is_open and now - attempted_at >= _RETRY_SECONDS:
                    last_attempt[slot] = now
                    try:
                        sent, strong, weak, chats = await asyncio.to_thread(_send_bulletin, config)
                        _save_last_sent_slot(slot)
                        log.info("Đã gửi bản tin bảng điện: %d mạnh, %d yếu, %d tin tới %d chat.",
                                 strong, weak, sent, chats)
                    except Exception:
                        log.exception("Không gửi được bản tin bảng điện tại lịch %s.", slot)
        except Exception:
            log.exception("Lỗi khi kiểm tra lịch gửi Telegram của Bảng điện.")
        try:
            await asyncio.wait_for(stop.wait(), timeout=_POLL_SECONDS)
        except asyncio.TimeoutError:
            pass
