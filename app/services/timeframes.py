"""Chart timeframes (5m, 15m, 30m, 1h, 1D, 1W) and helpers shared by the data sources and the charts.

Times are naive Vietnam-local datetimes (UTC+7). Intraday bars are labelled by their start time.
SSI v3 serves 1m/3m/5m/15m/1h/1d, so 30m is built from 15m bars and 1W from 1D bars.
Vietcap serves 1m/1h/1d, so 5m/15m/30m are built from 1m bars.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

import pandas as pd

OHLCV_COLS = ["date", "open", "high", "low", "close", "volume"]

# HOSE/HNX continuous sessions; the 14:30–14:45 ATC and post-close trades fall in the last bucket.
SESSIONS = ((time(9, 0), time(11, 30)), (time(13, 0), time(14, 45)))


@dataclass(frozen=True)
class TF:
    key: str
    label: str
    minutes: int | None       # bar length for intraday, None for 1D / 1W
    ssi: str                  # SSI v3 Timeframe value fetched from the server
    vietcap: str              # Vietcap gap-chart timeFrame fetched from the server
    lookback_days: int | None  # calendar days of history to load (None = HISTORY_DAYS)
    cache_minutes: int | None  # cache TTL (None = CACHE_TTL_MINUTES)

    @property
    def intraday(self) -> bool:
        return self.minutes is not None


TIMEFRAMES: dict[str, TF] = {
    "5m": TF("5m", "5 phút", 5, "5m", "ONE_MINUTE", 45, 2),
    "15m": TF("15m", "15 phút", 15, "15m", "ONE_MINUTE", 100, 3),
    "30m": TF("30m", "30 phút", 30, "15m", "ONE_MINUTE", 180, 5),
    "1h": TF("1h", "60 phút", 60, "1h", "ONE_HOUR", 365, 10),
    "1D": TF("1D", "Ngày", None, "1d", "ONE_DAY", None, None),
    "1W": TF("1W", "Tuần", None, "1d", "ONE_DAY", 365 * 7, None),
}
ALIASES = {"60m": "1h", "1H": "1h", "D": "1D", "1d": "1D", "daily": "1D", "W": "1W", "1w": "1W", "weekly": "1W"}


def get_tf(key: str | None) -> TF:
    key = (key or "1D").strip()
    key = ALIASES.get(key, key)
    if key not in TIMEFRAMES:
        raise ValueError(f"timeframe '{key}' không hỗ trợ, chọn một trong: {', '.join(TIMEFRAMES)}")
    return TIMEFRAMES[key]


def resample(df: pd.DataFrame, tf: TF) -> pd.DataFrame:
    """Aggregate finer bars into `tf` bars. A no-op when the bars are already at `tf`."""
    if df.empty:
        return df[OHLCV_COLS].copy() if set(OHLCV_COLS) <= set(df.columns) else pd.DataFrame(columns=OHLCV_COLS)
    d = df.sort_values("date")
    if tf.key == "1W":
        key = d["date"].dt.to_period("W-SUN").dt.start_time  # Monday of the week
    elif tf.intraday:
        key = d["date"].dt.floor(f"{tf.minutes}min")
    else:
        key = d["date"].dt.normalize()
    out = d.groupby(key).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                             close=("close", "last"), volume=("volume", "sum"))
    out.index.name = "date"
    return out.reset_index()[OHLCV_COLS]


def bar_starts(day: datetime, minutes: int) -> list[datetime]:
    """Start times of the `minutes` bars of one trading day."""
    out = []
    for start, end in SESSIONS:
        t = datetime.combine(day.date(), start)
        stop = datetime.combine(day.date(), end)
        while t < stop:
            out.append(t)
            t += timedelta(minutes=minutes)
    return out


def future_times(last: pd.Timestamp, n: int, tf: TF) -> list[pd.Timestamp]:
    """The next `n` bar times after `last` (weekdays only; public holidays are not skipped)."""
    last = pd.Timestamp(last)
    if tf.key == "1W":
        return [last + pd.Timedelta(weeks=i) for i in range(1, n + 1)]
    if not tf.intraday:
        return list(pd.bdate_range(last + pd.Timedelta(days=1), periods=n))
    out: list[pd.Timestamp] = []
    day = last.normalize()
    while len(out) < n:
        if day.weekday() < 5:
            out += [pd.Timestamp(t) for t in bar_starts(day.to_pydatetime(), tf.minutes) if t > last]
        day += pd.Timedelta(days=1)
    return out[:n]


def fmt_time(ts, tf: TF) -> str:
    """Human-readable bar time: '2026-11-03' (1D/1W) or '2026-11-03 14:15' (intraday)."""
    return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M" if tf.intraday else "%Y-%m-%d")


def chart_time(ts, tf: TF):
    """lightweight-charts time: 'YYYY-MM-DD' for 1D/1W, unix seconds for intraday.

    Intraday times are VN-local wall-clock time encoded as if it were UTC, so the chart axis
    (which renders UTC) shows Vietnam time no matter where the browser runs.
    """
    ts = pd.Timestamp(ts)
    if not tf.intraday:
        return ts.strftime("%Y-%m-%d")
    return int((ts - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1))
