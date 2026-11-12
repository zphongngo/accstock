"""Intraday Ichimoku BO bulletin (30m / 60m): every Bảng điện symbol, BO only (no BD), sent after each bar closes.

A bar is evaluated only once it is complete: bar end = start + bar length, capped at the end of its session
(11:30 / 14:45). Slots = bar end + 5 minutes, the last bar of the day (ends 14:45, ATC) at 15:05:
    30m: 09:35 10:05 10:35 11:05 11:35 13:35 14:05 14:35 15:05
    60m: 10:05 11:05 11:35 14:05 15:05
Only symbols whose latest completed bar is the bar of the slot are reported (no stale signal of a suspended stock).
"""
from __future__ import annotations

import html
from datetime import datetime, time, timedelta

import pandas as pd

from app.analysis.daily_signals import MAX_ERRORS_LISTED, SymbolSignal, _line, scan
from app.services.timeframes import SESSIONS, bar_starts, get_tf

BO_TIMEFRAMES = ("30m", "1h")
SLOT_DELAY = timedelta(minutes=5)
LAST_SLOT = time(15, 5)          # the bar ending at 14:45 (ATC) is sent at 15:05
SLOT_GRACE = timedelta(minutes=20)   # a slot missed by more than this (app started late) is skipped


def bo_tf(key: str | None) -> str:
    k = get_tf(key or "1h").key
    if k not in BO_TIMEFRAMES:
        raise ValueError("Chỉ hỗ trợ khung 30m hoặc 60m")
    return k


def tf_label(key: str) -> str:
    return "60m" if key == "1h" else key


def bar_end(start: datetime, minutes: int) -> datetime:
    for s, e in SESSIONS:
        if s <= start.time() < e:
            return min(start + timedelta(minutes=minutes), datetime.combine(start.date(), e))
    return start + timedelta(minutes=minutes)


def slot_of(start: datetime, minutes: int) -> datetime:
    end = bar_end(start, minutes)
    if end.time() >= SESSIONS[-1][1]:
        return datetime.combine(start.date(), LAST_SLOT)
    return end + SLOT_DELAY


def slots(day: datetime, key: str) -> list[tuple[datetime, datetime]]:
    """(send time, bar start) of every slot of one day."""
    m = get_tf(key).minutes
    return [(slot_of(s, m), s) for s in bar_starts(day, m)]


def due_slot(now: datetime, key: str) -> tuple[datetime, datetime] | None:
    """The slot to send now (weekdays, within SLOT_GRACE of its time), as (send time, bar start)."""
    if now.weekday() >= 5:
        return None
    past = [x for x in slots(now, key) if x[0] <= now < x[0] + SLOT_GRACE]
    return past[-1] if past else None


def next_slot(now: datetime, key: str) -> datetime | None:
    day = now
    for _ in range(10):
        if day.weekday() < 5:
            for send, _ in slots(day, key):
                if send > now:
                    return send
        day = datetime.combine(day.date() + timedelta(days=1), time(0))
    return None


def completed(df: pd.DataFrame, minutes: int, now: datetime) -> pd.DataFrame:
    """Drop the bar still in progress (and anything after `now`)."""
    if df.empty:
        return df
    ends = [bar_end(pd.Timestamp(d).to_pydatetime(), minutes) for d in df["date"]]
    return df[[e <= now for e in ends]].reset_index(drop=True)


def scan_bo(stock, symbols: list[str], key: str, now: datetime | None = None,
            bar: datetime | None = None) -> tuple[list[SymbolSignal], dict[str, str], datetime | None, int]:
    """Evaluate the latest completed `key` bar of every symbol. `bar` = the bar start to report (a slot); default =
    the most recent completed bar found. Returns (BO signals sorted by score, errors, bar start, symbols on that bar)."""
    now = now or datetime.now()
    m = get_tf(key).minutes
    results, errors = scan(stock, symbols, key, prepare=lambda df: completed(df, m, now))
    if bar is None and results:
        bar = max(pd.Timestamp(r.date).to_pydatetime() for r in results)
    if bar is None:
        return [], errors, None, 0
    tag = bar.strftime("%Y-%m-%d %H:%M")
    current = [r for r in results if r.date[:16] == tag]
    bo = sorted((r for r in current if r.cloud_bo), key=lambda r: (-r.score, r.symbol))
    return bo, errors, bar, len(current)


def build_bo_message(bo: list[SymbolSignal], key: str, bar: datetime | None, scanned: int,
                     errors: dict[str, str] | None = None) -> str:
    m = get_tf(key).minutes
    when = (f"nến {bar:%H:%M}–{bar_end(bar, m):%H:%M} {bar:%d/%m/%Y}" if bar else "chưa có nến hoàn thành")
    out = [f"🟢 <b>PStock · Ichimoku BO khung {tf_label(key)}</b> · {when}",
           f"Quét {scanned} mã · {len(bo)} mã phá mây · giá: nghìn đồng"]
    if bo:
        out += ["", "🟢 <b>BREAK OUT – giá vượt lên trên mây</b>"] + [_line(r, True) for r in bo]
    else:
        out += ["", "Không có mã nào phá mây Ichimoku ở nến này."]
    if errors:
        out += ["", f"⚠️ {len(errors)} mã không tải được: " + ", ".join(
            html.escape(s) for s in list(errors)[:MAX_ERRORS_LISTED])
            + (" …" if len(errors) > MAX_ERRORS_LISTED else "")]
    out += ["", "<i>Phân tích kỹ thuật tham khảo, không phải khuyến nghị đầu tư.</i>"]
    return "\n".join(out)
