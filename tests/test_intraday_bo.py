from datetime import datetime

import numpy as np
import pandas as pd

from app.analysis import intraday_bo as ib


def _hm(xs):
    return [x.strftime("%H:%M") for x in xs]


def test_slots_follow_bar_closes():
    day = datetime(2026, 11, 11)
    assert _hm(s for s, _ in ib.slots(day, "30m")) == [
        "09:35", "10:05", "10:35", "11:05", "11:35", "13:35", "14:05", "14:35", "15:05"]
    assert _hm(s for s, _ in ib.slots(day, "1h")) == ["10:05", "11:05", "11:35", "14:05", "15:05"]
    assert _hm(b for _, b in ib.slots(day, "1h")) == ["09:00", "10:00", "11:00", "13:00", "14:00"]


def test_due_slot_weekdays_and_grace():
    assert ib.due_slot(datetime(2026, 11, 11, 10, 4), "1h") is None
    send, bar = ib.due_slot(datetime(2026, 11, 11, 10, 6), "1h")
    assert (send.strftime("%H:%M"), bar.strftime("%H:%M")) == ("10:05", "09:00")
    assert ib.due_slot(datetime(2026, 11, 11, 10, 30), "1h") is None          # missed by > 20 min
    assert ib.due_slot(datetime(2026, 11, 14, 10, 6), "1h") is None           # Saturday
    assert ib.next_slot(datetime(2026, 11, 13, 16, 0), "30m") == datetime(2026, 11, 16, 9, 35)


def test_completed_drops_the_bar_in_progress():
    df = pd.DataFrame({"date": pd.to_datetime(["2026-11-11 09:00", "2026-11-11 10:00", "2026-11-11 11:00"]),
                       "close": [1, 2, 3]})
    assert len(ib.completed(df, 60, datetime(2026, 11, 11, 10, 5))) == 1
    assert len(ib.completed(df, 60, datetime(2026, 11, 11, 11, 30))) == 3     # 11:00 bar ends at 11:30


def _bars(n, end, minutes=60, breakout=False):
    starts = [t for d in pd.bdate_range(end=end, periods=60) for t in ib.bar_starts(d.to_pydatetime(), minutes)]
    starts = [t for t in starts if t <= pd.Timestamp(end)][-n:]
    rng = np.random.default_rng(1)
    close = 50000 + rng.normal(0, 30, len(starts))
    if breakout:
        close[-3] = 49992       # inside the (flat) cloud ...
        close[-2:] = 56000      # ... then breaks out on the last completed bar (the very last one is still open)
    return pd.DataFrame({"date": starts, "open": close, "high": close + 50, "low": close - 50, "close": close,
                         "volume": 1e5})


def test_scan_bo_reports_only_the_slot_bar_and_only_bo():
    class Stock:
        def get_ohlcv(self, sym, refresh=False, timeframe="1D"):
            if sym == "OLD":     # suspended: last bar is yesterday's
                return _bars(300, "2026-11-10 14:00", breakout=True), "fake"
            return _bars(300, "2026-11-11 10:00", breakout=(sym == "UP")), "fake"

    bar = datetime(2026, 11, 11, 9, 0)
    bo, errors, got, n = ib.scan_bo(Stock(), ["UP", "FLAT", "OLD"], "1h", now=datetime(2026, 11, 11, 10, 5), bar=bar)
    assert got == bar and n == 2 and not errors          # 10:00 bar still open -> dropped; OLD is stale
    assert [r.symbol for r in bo] == ["UP"]
    msg = ib.build_bo_message(bo, "1h", got, 3)
    assert "khung 60m" in msg and "09:00–10:00 11/11/2026" in msg and "BREAK DOWN" not in msg
