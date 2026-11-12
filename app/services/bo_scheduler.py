"""Auto-send of the intraday Ichimoku BO bulletin (Cổ phiếu tab: "Gửi BO" / 30m-60m / "Tự động").

Runs inside the FastAPI lifespan, like the board scheduler. On weekdays with a session (`trading_today`), at each
slot of the chosen timeframe (see `intraday_bo.slots`) it scans every board symbol and sends the BO list to
Telegram when there is at least one. The last handled slot is kept in data/bo_alert_state.json, so a restart
doesn't resend it. Settings: `auto_bo`, `bo_tf` in data/board_settings.json.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time as monotonic
from datetime import date, datetime
from pathlib import Path

from app.services import board_settings

log = logging.getLogger(__name__)
_POLL_SECONDS = 10
_RETRY_SECONDS = 120


def _state_path() -> Path:
    return board_settings.settings_path().with_name("bo_alert_state.json")


def last_slot() -> str | None:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8")).get("last_slot")
    except (OSError, ValueError, AttributeError):
        return None


def _save_slot(slot: str) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"last_slot": slot}), encoding="utf-8")
    tmp.replace(p)


def run_once(key: str, bar: datetime, now: datetime) -> tuple[int, int]:
    """Scan + send one slot. Returns (number of BO, messages sent)."""
    from app.analysis.daily_signals import board_symbols
    from app.analysis.intraday_bo import build_bo_message, scan_bo
    from app.api.deps import get_board_service, get_stock_service
    from app.services.telegram_service import TelegramService

    tg = TelegramService()
    if not tg.configured:
        raise RuntimeError("Telegram chưa cấu hình (telegram.env)")
    symbols = board_symbols(get_board_service(), board_settings.load()["watch"])
    bo, errors, bar, _ = scan_bo(get_stock_service(), symbols, key, now=now, bar=bar)
    if not bo:
        return 0, 0
    return len(bo), tg.send(build_bo_message(bo, key, bar, len(symbols), errors))


async def run_bo_scheduler(stop: asyncio.Event) -> None:
    from app.analysis.intraday_bo import due_slot, tf_label
    from app.api.deps import get_board_service

    attempts: dict[str, float] = {}
    market: tuple[date, bool] | None = None
    while not stop.is_set():
        try:
            cfg = board_settings.load()
            now = datetime.now()
            due = due_slot(now, cfg["bo_tf"]) if cfg["auto_bo"] else None
            if due:
                send_at, bar = due
                slot = f"{cfg['bo_tf']}@{send_at:%Y-%m-%dT%H:%M}"
                if slot != last_slot() and monotonic.monotonic() - attempts.get(slot, 0) >= _RETRY_SECONDS:
                    attempts[slot] = monotonic.monotonic()
                    if market is None or market[0] != now.date():
                        market = (now.date(), await asyncio.to_thread(get_board_service().trading_today))
                    if not market[1]:
                        log.info("BO %s: hôm nay không có phiên, bỏ qua.", tf_label(cfg["bo_tf"]))
                        _save_slot(slot)
                    else:
                        n, sent = await asyncio.to_thread(run_once, cfg["bo_tf"], bar, now)
                        _save_slot(slot)
                        log.info("BO %s nến %s: %d mã phá mây, đã gửi %d tin.", tf_label(cfg["bo_tf"]),
                                 bar.strftime("%H:%M"), n, sent)
        except Exception:  # noqa: BLE001  (retried after _RETRY_SECONDS while the slot is still due)
            market = None if market and market[0] != datetime.now().date() else market
            log.exception("Lỗi khi gửi BO tự động.")
        try:
            await asyncio.wait_for(stop.wait(), timeout=_POLL_SECONDS)
        except asyncio.TimeoutError:
            pass
