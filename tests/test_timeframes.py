from datetime import datetime, timezone

import pandas as pd

from app.services.realtime_service import parse_bar_time
from app.services.stock_service import StockService
from app.services.timeframes import chart_time, future_times, get_tf, resample


def _bars(times, step=1):
    n = len(times)
    return pd.DataFrame({"date": pd.to_datetime(times), "open": range(10, 10 + n), "high": range(20, 20 + n),
                         "low": range(1, 1 + n), "close": range(11, 11 + n), "volume": [step] * n})


def test_get_tf_aliases():
    assert get_tf("60m").key == "1h" and get_tf(None).key == "1D" and get_tf("weekly").key == "1W"


def test_resample_weekly_labels_monday():
    df = _bars(["2026-10-26", "2026-10-28", "2026-10-30", "2026-11-02", "2026-11-03"])
    w = resample(df, get_tf("1W"))
    assert w["date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-10-26", "2026-11-02"]
    assert w["open"].tolist() == [10, 13] and w["close"].tolist() == [13, 15] and w["volume"].tolist() == [3, 2]


def test_resample_5m_from_1m():
    df = _bars(["2026-11-03 09:00", "2026-11-03 09:01", "2026-11-03 09:04", "2026-11-03 09:05"])
    r = resample(df, get_tf("5m"))
    assert len(r) == 2 and r["high"].tolist() == [22, 23] and r["low"].tolist() == [1, 4]


def test_future_times_respect_sessions():
    tf = get_tf("30m")
    nxt = future_times(pd.Timestamp("2026-11-03 14:30"), 3, tf)  # Tuesday, last bar of the day
    assert [t.strftime("%a %H:%M") for t in nxt] == ["Wed 09:00", "Wed 09:30", "Wed 10:00"]
    lunch = future_times(pd.Timestamp("2026-11-03 11:00"), 1, tf)
    assert lunch[0].strftime("%H:%M") == "13:00"
    fri = future_times(pd.Timestamp("2026-11-06 14:00"), 1, get_tf("1h"))
    assert fri[0].strftime("%a %H:%M") == "Mon 09:00"
    assert len(future_times(pd.Timestamp("2026-11-02"), 26, get_tf("1W"))) == 26


def test_chart_time_intraday_is_vn_wallclock():
    t = chart_time(pd.Timestamp("2026-11-03 09:15"), get_tf("15m"))
    assert datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M") == "09:15"
    assert chart_time(pd.Timestamp("2026-11-03"), get_tf("1D")) == "2026-11-03"


def test_parse_bar_time_formats():
    assert parse_bar_time("2026/11/03 09:15:00") == datetime(2026, 11, 3, 9, 15)
    assert parse_bar_time("2026-11-03T09:15:30") == datetime(2026, 11, 3, 9, 15)
    ts = int(pd.Timestamp("2026-11-03 02:15", tz="UTC").timestamp())  # 09:15 VN
    assert parse_bar_time(ts) == datetime(2026, 11, 3, 9, 15)
    assert parse_bar_time(ts * 1000) == datetime(2026, 11, 3, 9, 15)
    assert parse_bar_time("09:15:00").strftime("%H:%M") == "09:15"
    assert parse_bar_time("", "") is None


def test_on_data_and_live_merge():
    from ssi_sdk.models import IntervalMessage

    svc = StockService()
    rt = svc.realtime
    rt._day = datetime(2026, 11, 3).date()
    for minute, close in ((10, 100), (11, 102), (14, 99), (15, 101)):
        rt._on_data(IntervalMessage(interval_time=f"2026/11/03 10:{minute:02d}:00", symbol="FPT",
                                    open=close, high=close + 1, low=close - 1, close=close, volume=10))
    rt._on_data({"method": "subscribe"})  # acks are ignored
    assert len(rt.bars_1m("FPT")) == 4

    rest_5m = _bars(["2026-11-03 10:00", "2026-11-03 10:05", "2026-11-03 10:10"])
    out, live = svc._apply_live(rest_5m, "FPT", get_tf("5m"))
    # 10:05 bucket started before the first streamed minute (10:10) -> REST bar kept
    assert live and out["date"].dt.strftime("%H:%M").tolist() == ["10:00", "10:05", "10:10", "10:15"]
    assert out["close"].tolist()[1:] == [12, 99, 101] and out["volume"].iloc[2] == 30

    rest_1d = _bars(["2026-10-30", "2026-11-03"])
    d, live = svc._apply_live(rest_1d, "FPT", get_tf("1D"))
    assert live and d["close"].iloc[-1] == 101 and d["high"].iloc[-1] == 103 and d["low"].iloc[-1] == 2
