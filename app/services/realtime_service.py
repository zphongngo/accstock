"""Realtime 1-minute bars from SSI StreamingService (ssi-sdk `Stream`).

Docs: https://developers.ssi.com.vn/docs/sdk/python/service-classes (6. Streaming realtime)

- One WebSocket (`Stream(auth)`) shared by the whole app, opened on first use. It reuses the
  `Auth` owned by `SSIService` (SSI allows one session per API key). Market data needs no OTP.
- `watch(symbol)` subscribes `subscribe_symbol_ohlcv([symbol], interval=Timeframe.MINUTE_1)`.
- `on_data` receives `IntervalMessage` bars (one per minute, re-sent while the minute is still
  open). Today's bars are kept in memory and rolled up into 5m/15m/30m/1h/1D/1W by
  `StockService`.
- The SDK reconnects and re-subscribes by itself; when it gives up (`failed`) the stream is
  rebuilt on the next `watch()`.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from app.core.config import get_settings
from app.services.ssi_service import SSIService
from app.services.timeframes import OHLCV_COLS

log = logging.getLogger(__name__)
VN_TZ = timezone(timedelta(hours=7))
MAX_SYMBOLS = 50


def _now_vn() -> datetime:
    return datetime.now(VN_TZ).replace(tzinfo=None)


def parse_bar_time(interval_time, trading_time="") -> datetime | None:
    """Start time of a streamed bar as naive VN time.

    Accepts epoch seconds/milliseconds, 'YYYY/MM/DD HH:MM:SS', ISO strings or a bare 'HH:MM:SS'
    (taken as today). Seconds are dropped so the value is the minute the bar belongs to.
    """
    for raw in (interval_time, trading_time):
        if raw in (None, ""):
            continue
        try:
            text = str(raw).strip()
            if text.replace(".", "", 1).isdigit():
                v = float(text)
                v = v / 1000 if v > 1e11 else v
                dt = datetime.fromtimestamp(v, VN_TZ).replace(tzinfo=None)
            elif len(text) <= 8 and text.count(":") >= 1:
                dt = datetime.combine(_now_vn().date(), datetime.strptime(text, "%H:%M:%S" if text.count(":") == 2 else "%H:%M").time())
            else:
                ts = pd.Timestamp(text.replace("/", "-"))
                if ts.tzinfo is not None:
                    ts = ts.tz_convert(VN_TZ).tz_localize(None)
                dt = ts.to_pydatetime()
            return dt.replace(second=0, microsecond=0)
        except (ValueError, OverflowError):
            continue
    return None


class RealtimeService:
    def __init__(self, ssi: SSIService) -> None:
        self.s = get_settings()
        self.ssi = ssi
        self._lock = threading.RLock()
        self._stream = None
        self._state = "idle"           # idle | connecting | connected | reconnected | disconnected | failed | error
        self._error: str | None = None
        self._symbols: set[str] = set()
        self._bars: dict[str, dict[datetime, dict]] = {}
        self._day: date = _now_vn().date()
        self._last_msg: float | None = None
        self._msg_count = 0

    @property
    def enabled(self) -> bool:
        return bool(self.s.SSI_STREAM_ENABLED and self.ssi.configured)

    # ---------- connection ----------
    def _on_connection(self, event) -> None:
        self._state = str(getattr(event.state, "value", event.state))
        err = getattr(event, "error", None)
        self._error = f"{type(err).__name__}: {err}" if err else None
        log.info("SSI stream %s (reconnects=%s)%s", self._state, getattr(event, "reconnect_count", 0),
                 f" — {self._error}" if self._error else "")

    def _on_data(self, msg) -> None:
        # Keep it short: runs on the SDK's socket thread.
        from ssi_sdk.models import IntervalMessage

        if not isinstance(msg, IntervalMessage) or not msg.symbol:
            return
        t = parse_bar_time(msg.interval_time, msg.trading_time)
        if t is None or not msg.close:
            return
        bar = {"date": t, "open": float(msg.open or msg.close), "high": float(msg.high or msg.close),
               "low": float(msg.low or msg.close), "close": float(msg.close), "volume": float(msg.volume or 0)}
        with self._lock:
            if t.date() != self._day:           # new trading day: drop yesterday's bars
                if t.date() > self._day:
                    self._day, self._bars = t.date(), {}
                else:
                    return
            self._bars.setdefault(msg.symbol.upper(), {})[t] = bar
            self._last_msg, self._msg_count = time.time(), self._msg_count + 1

    def _start(self) -> None:
        from ssi_sdk import Stream

        self._state, self._error = "connecting", None
        stream = Stream(self.ssi.auth)
        stream.streaming.on_data = self._on_data
        stream.streaming.on_connection = self._on_connection
        stream.streaming.connect()  # returns once connected; the SDK keeps the socket alive in its own thread
        self._stream = stream
        if self._state == "connecting":
            self._state = "connected"
        if self._symbols:
            self._subscribe(sorted(self._symbols))

    def _subscribe(self, symbols: list[str]) -> None:
        from ssi_sdk.enums import Timeframe

        self._stream.streaming.subscribe_symbol_ohlcv(symbols, interval=Timeframe.MINUTE_1)

    def watch(self, symbol: str) -> bool:
        """Make sure `symbol` is streamed. Returns False if streaming is off or unavailable."""
        if not self.enabled:
            return False
        symbol = symbol.upper().strip()
        with self._lock:
            try:
                if self._stream is not None and self._state == "failed":
                    self.stop()
                if self._stream is None:
                    self._start()
                if symbol not in self._symbols:
                    if len(self._symbols) >= MAX_SYMBOLS:
                        return False
                    self._subscribe([symbol])
                    self._symbols.add(symbol)
                return True
            except Exception as e:  # noqa: BLE001
                log.warning("SSI stream unavailable: %s", e)
                self._state, self._error, self._stream = "error", str(e), None
                return False

    def stop(self) -> None:
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.streaming.disconnect()
                except Exception:  # noqa: BLE001
                    pass
            self._stream, self._state = None, "idle"

    # ---------- data ----------
    def bars_1m(self, symbol: str) -> pd.DataFrame:
        """Today's streamed 1-minute bars for `symbol` (empty if none)."""
        with self._lock:
            rows = list(self._bars.get(symbol.upper(), {}).values())
        if not rows:
            return pd.DataFrame(columns=OHLCV_COLS)
        return pd.DataFrame(rows)[OHLCV_COLS].sort_values("date").reset_index(drop=True)

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": self.enabled,
                "state": self._state,
                "error": self._error,
                "symbols": sorted(self._symbols),
                "messages": self._msg_count,
                "last_message_age_s": round(time.time() - self._last_msg, 1) if self._last_msg else None,
            }
