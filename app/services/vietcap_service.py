"""Vietcap (VCI) public chart API client — OHLCV for all chart timeframes, used as fallback."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
import pandas as pd

from app.core.config import get_settings
from app.services.timeframes import OHLCV_COLS, TF, get_tf, resample

HEADERS = {
    "Content-Type": "application/json",
    "Referer": "https://trading.vietcap.com.vn/",
    "Origin": "https://trading.vietcap.com.vn",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
}
VN_TZ = timezone(timedelta(hours=7))
BARS_PER_DAY = {"ONE_MINUTE": 280, "ONE_HOUR": 6, "ONE_DAY": 1}


class VietcapService:
    def __init__(self) -> None:
        self.s = get_settings()
        # One pooled, thread-safe client: reusing the TLS connection is ~3x faster than a new one per request
        # behind the corporate proxy (a full scan of the board = ~270 requests).
        self._client = httpx.Client(headers=HEADERS, timeout=60)

    def get_ohlcv(self, symbol: str, start: date, end: date, timeframe: str | TF = "1D") -> pd.DataFrame:
        tf = timeframe if isinstance(timeframe, TF) else get_tf(timeframe)
        to_ts = int(datetime.combine(end + timedelta(days=1), datetime.min.time(), VN_TZ).timestamp())
        days = max((end - start).days, 1)
        count_back = int(days * 5 / 7 * BARS_PER_DAY[tf.vietcap]) + 50
        r = self._client.post(
            self.s.VIETCAP_URL,
            json={"timeFrame": tf.vietcap, "symbols": [symbol.upper()], "to": to_ts, "countBack": count_back},
        )
        r.raise_for_status()
        df = self.normalize(r.json(), intraday=tf.intraday)
        if not df.empty:
            df = df[(df["date"] >= pd.Timestamp(start)) & (df["date"] < pd.Timestamp(end + timedelta(days=1)))]
        return resample(df, tf)

    def close(self) -> None:
        self._client.close()

    def get_daily_ohlcv(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        return self.get_ohlcv(symbol, start, end, "1D")

    @staticmethod
    def normalize(payload, intraday: bool = False) -> pd.DataFrame:
        item = payload[0] if isinstance(payload, list) and payload else payload
        if not item or not item.get("t"):
            return pd.DataFrame(columns=OHLCV_COLS)
        ts = [int(float(x)) for x in item["t"]]
        if intraday:
            dates = [pd.Timestamp(datetime.fromtimestamp(t, VN_TZ).replace(tzinfo=None)) for t in ts]
        else:
            dates = [pd.Timestamp(datetime.fromtimestamp(t, VN_TZ).date()) for t in ts]
        df = pd.DataFrame(
            {"date": dates, "open": item["o"], "high": item["h"], "low": item["l"],
             "close": item["c"], "volume": item["v"]}
        )
        for c in OHLCV_COLS[1:]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.dropna().drop_duplicates("date").sort_values("date").reset_index(drop=True)
