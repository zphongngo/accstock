"""SSI market data over the official SDK (`pip install ssi-sdk`, API v3).

Docs: https://developers.ssi.com.vn/docs/sdk/python/service-classes

SSI allows one active session per API key, so this service owns the single `Auth` that both
the REST client (`Data`) and the realtime stream (`RealtimeService`) use.
Market data needs no OTP.
"""
from __future__ import annotations

import logging
import threading
from datetime import date

import pandas as pd

from app.core.config import get_settings
from app.services.timeframes import OHLCV_COLS, TF, get_tf, resample

log = logging.getLogger(__name__)


class SSIError(RuntimeError):
    pass


class SSIService:
    def __init__(self) -> None:
        self.s = get_settings()
        self._lock = threading.RLock()
        self._auth = None
        self._data = None

    @property
    def configured(self) -> bool:
        return bool(self.s.SSI_CONSUMER_ID and self.s.SSI_CONSUMER_SECRET)

    def _config(self):
        from ssi_sdk import Config

        return Config(
            client_id=self.s.SSI_CLIENT_ID,
            api_key=self.s.SSI_CONSUMER_ID,
            api_secret=self.s.SSI_CONSUMER_SECRET,
            private_key=self.s.SSI_PRIVATE_KEY,
            api_url=self.s.SSI_API_URL,
            streaming_url=self.s.SSI_STREAMING_URL,
            rate_limit_per_second=self.s.SSI_RATE_LIMIT_PER_SECOND,
            log_level=self.s.SSI_LOG_LEVEL,
        )

    @property
    def auth(self):
        """The shared, authenticated `ssi_sdk.Auth` (created on first use)."""
        with self._lock:
            if self._auth is None:
                if not self.configured:
                    raise SSIError("SSI_CONSUMER_ID / SSI_CONSUMER_SECRET chưa được đặt trong .env")
                from ssi_sdk import Auth, Data

                auth = Auth(self._config())
                try:
                    auth.authenticate()
                except Exception as e:  # noqa: BLE001
                    auth.close()
                    raise SSIError(f"SSI đăng nhập thất bại: {e}") from e
                self._auth, self._data = auth, Data(auth)
            return self._auth

    def _reset(self) -> None:
        with self._lock:
            if self._auth is not None:
                try:
                    self._auth.close()
                except Exception:  # noqa: BLE001
                    pass
            self._auth = self._data = None

    def close(self) -> None:
        self._reset()

    def _fetch(self, symbol: str, timeframe: str, start: date, end: date) -> list:
        from ssi_sdk.enums import Timeframe
        from ssi_sdk.exceptions import AuthenticationError

        for attempt in (1, 2):
            self.auth  # noqa: B018 - make sure we are logged in
            try:
                with self._lock:
                    return self._data.market_data.get_ohlc_all(
                        symbol, Timeframe(timeframe), start.strftime("%Y/%m/%d"),
                        end.strftime("%Y/%m/%d"), ascending=True,
                    )
            except AuthenticationError as e:
                # Session taken over / expired: log in again once.
                log.warning("SSI auth error (%s), re-authenticating", e)
                self._reset()
                if attempt == 2:
                    raise SSIError(f"SSI đăng nhập thất bại: {e}") from e
        return []

    def get_ohlcv(self, symbol: str, start: date, end: date, timeframe: str | TF = "1D") -> pd.DataFrame:
        tf = timeframe if isinstance(timeframe, TF) else get_tf(timeframe)
        bars = self._fetch(symbol.upper(), tf.ssi, start, end)
        # 30m <- 15m and 1W <- 1D; for the other timeframes this is a no-op.
        return resample(self.normalize(bars, intraday=tf.intraday), tf)

    # Backwards-compatible name used by scripts.
    def get_daily_ohlcv(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        return self.get_ohlcv(symbol, start, end, "1D")

    @staticmethod
    def normalize(bars, intraday: bool = False) -> pd.DataFrame:
        """`list[OHLCData]` (or dicts with the same field names) -> date/open/high/low/close/volume."""
        if not bars:
            return pd.DataFrame(columns=OHLCV_COLS)
        rows = [b if isinstance(b, dict) else vars(b) for b in bars]
        df = pd.DataFrame(rows)
        df = pd.DataFrame({
            "date": pd.to_datetime(df["trading_date"], format="mixed"),
            "open": pd.to_numeric(df["open_price"], errors="coerce"),
            "high": pd.to_numeric(df["high_price"], errors="coerce"),
            "low": pd.to_numeric(df["low_price"], errors="coerce"),
            "close": pd.to_numeric(df["close_price"], errors="coerce"),
            "volume": pd.to_numeric(df["volume"], errors="coerce"),
        })
        if not intraday:
            df["date"] = df["date"].dt.normalize()
        return df.dropna().drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
