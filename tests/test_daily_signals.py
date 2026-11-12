import numpy as np
import pandas as pd

from app.analysis.daily_signals import SymbolSignal, build_message, evaluate
from app.analysis.ichimoku import compute_ichimoku
from app.services.telegram_service import split_message
from tests.conftest import make_ohlcv


def _with_last_close(df: pd.DataFrame, close: float) -> pd.DataFrame:
    df = df.copy()
    i = df.index[-1]
    df.loc[i, ["open", "close"]] = close
    df.loc[i, "high"] = close * 1.005
    df.loc[i, "low"] = close * 0.995
    return df


def _cloud(df):
    e = compute_ichimoku(df)
    return e["cloud_top"].iloc[-1], e["cloud_bottom"].iloc[-1], e


def test_cloud_breakout_and_breakdown_flags():
    # Flat market so the cloud is narrow and the last bar decides the break.
    rng = np.random.default_rng(0)
    n = 200
    close = 50000 + rng.normal(0, 50, n)
    df = pd.DataFrame({"date": pd.bdate_range("2026-01-01", periods=n), "open": close, "high": close + 60,
                       "low": close - 60, "close": close, "volume": 1e6})
    top, bot, _ = _cloud(df.iloc[:-1])
    df.loc[df.index[-2], "close"] = (top + bot) / 2           # previous close inside the cloud
    up = _with_last_close(df, top * 1.03)
    e = compute_ichimoku(up)
    assert bool(e["cloud_breakout"].iloc[-1]) and not bool(e["cloud_breakdown"].iloc[-1])
    down = _with_last_close(df, bot * 0.97)
    e = compute_ichimoku(down)
    assert bool(e["cloud_breakdown"].iloc[-1]) and not bool(e["cloud_breakout"].iloc[-1])
    # already above the cloud on the previous bar -> no new break
    stay = up.copy()
    stay.loc[stay.index[-2], "close"] = top * 1.02
    assert not bool(compute_ichimoku(stay)["cloud_breakout"].iloc[-1])


def test_evaluate_exposes_signals():
    s = evaluate("fpt", make_ohlcv(300))
    assert s.symbol == "FPT" and set(s.checks) == {"tk", "chikou", "future", "k129"}
    assert isinstance(s.cloud_bo, bool) and isinstance(s.vol_bd, bool)


def _sig(sym, **kw):
    base = dict(symbol=sym, date="2026-11-04", close=27500, change_pct=3.2, score=45, rating="Bullish",
                vol_ratio=2.1, cloud_bo=False, cloud_bd=False, vol_bo=False, vol_bd=False,
                checks={"tk": True, "chikou": False, "future": None, "k129": True})
    base.update(kw)
    return SymbolSignal(**base)


def test_build_message_sections():
    msg = build_message([_sig("HPG", cloud_bo=True, vol_bo=True), _sig("MWG", cloud_bd=True, score=-60),
                         _sig("FPT")], {"BAD": "no <data>"}, "04/11/2026", scanned=4)
    assert "Quét 4 mã · 🟢 1 BO · 🔴 1 BD" in msg
    assert "<b>HPG</b> 27.50 (+3.20%)" in msg and "<b>KL 2.10x MA20</b>" in msg
    bo_part, bd_part = msg.split("BREAK DOWN")
    # labels are HTML-escaped: Telegram's HTML parse mode rejects a bare "<Kijun"
    assert "Tenkan&gt;Kijun ✅" in bo_part and "Chikou trên giá ❌" in bo_part and "Trên Kijun 129 ✅" in bo_part
    assert "Mây TL" not in bo_part.split("HPG")[1]  # unknown checks are omitted
    # BD labels follow the BD direction: a bullish condition shows as "Tenkan<Kijun ❌"
    assert "MWG" in bd_part and "Tenkan&lt;Kijun ❌" in bd_part and "Chikou dưới giá ✅" in bd_part
    assert "🟢 BO <b>HPG</b>" in msg  # price/volume section
    assert "no &lt;data&gt;" in msg   # error text is HTML-escaped
    assert "FPT" not in msg


def test_build_message_no_signal():
    msg = build_message([_sig("FPT")], {}, "04/11/2026")
    assert "Không có mã nào phá mây" in msg and "BREAK OUT" not in msg


def test_split_message_keeps_lines_whole():
    text = "\n".join(f"<b>line {i}</b> " + "x" * 90 for i in range(200))
    parts = split_message(text, limit=1000)
    assert all(len(p) <= 1000 for p in parts) and "\n".join(parts) == text
    assert all(p.count("<b>") == p.count("</b>") for p in parts)


def test_bo_and_bd_sections_sorted_by_score_high_to_low():
    res = [_sig("AAA", cloud_bd=True, score=-60), _sig("BBB", cloud_bd=True, score=-20),
           _sig("CCC", cloud_bo=True, score=30), _sig("DDD", cloud_bo=True, score=70)]
    msg = build_message(res, {}, "10/11/2026")
    assert msg.index("DDD") < msg.index("CCC") < msg.index("BBB") < msg.index("AAA")


def test_long_error_list_is_capped():
    from app.analysis.daily_signals import MAX_ERRORS_LISTED

    errors = {f"S{i:03d}": "boom" for i in range(MAX_ERRORS_LISTED + 5)}
    msg = build_message([], errors, "10/11/2026")
    assert "S000" in msg and f"S{MAX_ERRORS_LISTED:03d}" not in msg and "… và 5 mã khác" in msg


def test_scan_dedupes_cuts_at_date_and_collects_errors():
    from datetime import date

    from app.analysis.daily_signals import scan

    calls = []

    class Stock:
        def get_ohlcv(self, sym, refresh=False, timeframe="1D"):
            calls.append(sym)
            if sym == "BAD":
                raise RuntimeError("no data")
            df = make_ohlcv(200)
            df["date"] = pd.bdate_range(end="2026-11-10", periods=len(df))
            return df, "fake"

    results, errors = scan(Stock(), ["FPT", "BAD", "FPT", "HPG"], until=date(2026, 11, 9))
    assert sorted(calls) == ["BAD", "FPT", "HPG"]
    assert [r.symbol for r in results] == ["FPT", "HPG"] and set(errors) == {"BAD"}
    assert all(r.date[:10] == "2026-11-09" for r in results)
