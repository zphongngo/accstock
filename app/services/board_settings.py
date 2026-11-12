"""Board settings shared by the browser and scheduled Telegram alerts.

Stored in data/board_settings.json (gitignored), written atomically.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import time
from pathlib import Path

from app.core.config import get_settings

DEFAULTS = {
    "watch": [],
    "threshold": 3.0,
    "auto_board": True,
    "auto_daily": True,
    "board_start": "09:18",
    "board_end": "15:10",
    "board_interval": 30,
    "auto_bo": False,      # intraday Ichimoku BO bulletin (Cổ phiếu tab), off until the user ticks it
    "bo_tf": "1h",         # its timeframe: "30m" or "1h"
}
BO_TFS = ("30m", "1h")
SWITCHES = ("auto_board", "auto_daily")
SCHEDULE_FIELDS = ("board_start", "board_end", "board_interval")
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,10}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}$")
_lock = threading.Lock()


def settings_path() -> Path:
    return Path(get_settings().DATA_DIR) / "board_settings.json"


def clean_watch(symbols) -> list[str]:
    out = [str(s).strip().upper() for s in (symbols or [])]
    return list(dict.fromkeys(s for s in out if SYMBOL_RE.match(s)))[:100]


def _valid_time(value) -> bool:
    return isinstance(value, str) and TIME_RE.fullmatch(value) is not None


def _minutes(value: str) -> int:
    parsed = time.fromisoformat(value)
    return parsed.hour * 60 + parsed.minute


def load() -> dict:
    try:
        d = json.loads(settings_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  (missing / corrupt: defaults)
        d = {}
    thr = d.get("threshold", DEFAULTS["threshold"])
    try:
        thr = float(thr)
    except (TypeError, ValueError):
        thr = DEFAULTS["threshold"]
    if not 0 < thr <= 15:
        thr = DEFAULTS["threshold"]
    start = d.get("board_start", DEFAULTS["board_start"])
    end = d.get("board_end", DEFAULTS["board_end"])
    interval = d.get("board_interval", DEFAULTS["board_interval"])
    try:
        valid_schedule = (_valid_time(start) and _valid_time(end) and _minutes(start) < _minutes(end)
                          and isinstance(interval, int) and not isinstance(interval, bool)
                          and 1 <= interval <= 720)
    except ValueError:
        valid_schedule = False
    if not valid_schedule:
        start, end, interval = (DEFAULTS[k] for k in SCHEDULE_FIELDS)
    out = {"watch": clean_watch(d.get("watch")), "threshold": thr,
           "board_start": start, "board_end": end, "board_interval": interval}
    for k in SWITCHES:   # anything but an explicit false keeps the job on
        out[k] = d.get(k, DEFAULTS[k]) is not False
    out["auto_bo"] = d.get("auto_bo") is True
    out["bo_tf"] = d.get("bo_tf") if d.get("bo_tf") in BO_TFS else DEFAULTS["bo_tf"]
    return out


def save(watch=None, threshold: float | None = None, auto_board: bool | None = None,
         auto_daily: bool | None = None, board_start: str | None = None,
         board_end: str | None = None, board_interval: int | None = None, auto_bo: bool | None = None,
         bo_tf: str | None = None) -> dict:
    with _lock:
        cur = load()
        if watch is not None:
            cur["watch"] = clean_watch(watch)
        if threshold is not None:
            cur["threshold"] = float(threshold)
        if auto_board is not None:
            cur["auto_board"] = bool(auto_board)
        if auto_daily is not None:
            cur["auto_daily"] = bool(auto_daily)
        if board_start is not None:
            cur["board_start"] = board_start
        if board_end is not None:
            cur["board_end"] = board_end
        if board_interval is not None:
            cur["board_interval"] = board_interval
        if auto_bo is not None:
            cur["auto_bo"] = bool(auto_bo)
        if bo_tf is not None:
            bo_tf = {"60m": "1h"}.get(bo_tf, bo_tf)
            if bo_tf not in BO_TFS:
                raise ValueError("Khung BO chỉ được là 30m hoặc 60m.")
            cur["bo_tf"] = bo_tf
        if (not _valid_time(cur["board_start"]) or not _valid_time(cur["board_end"])
                or _minutes(cur["board_start"]) >= _minutes(cur["board_end"])
                or not isinstance(cur["board_interval"], int) or isinstance(cur["board_interval"], bool)
                or not 1 <= cur["board_interval"] <= 720):
            raise ValueError("Khung giờ gửi phải hợp lệ và giờ bắt đầu phải trước giờ kết thúc; interval từ 1 đến 720 phút.")
        p = settings_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
        return cur
