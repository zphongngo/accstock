import numpy as np
import pandas as pd

from app.analysis.breakout import compute_breakout
from app.analysis.ichimoku import PRESETS, compute_ichimoku, future_cloud, get_params, ichimoku_signals, time_cycles
from app.analysis.macd import compute_macd
from app.analysis.rsi import compute_rsi
from app.analysis.stock_scanner import analyze_dataframe, chart_payload, enrich, score_series
from tests.conftest import make_ohlcv

DISPLACEMENT = 26


def test_ichimoku_lines(ohlcv):
    e = compute_ichimoku(ohlcv)
    i = 100
    w9 = ohlcv.iloc[i - 8: i + 1]
    assert np.isclose(e["tenkan"].iloc[i], (w9["high"].max() + w9["low"].min()) / 2)
    w17 = ohlcv.iloc[i - 16: i + 1]  # Trịnh Phát: Kijun 17
    assert np.isclose(e["kijun"].iloc[i], (w17["high"].max() + w17["low"].min()) / 2)
    w33 = ohlcv.iloc[i - 32: i + 1]  # Senkou B 33
    assert np.isclose(e["span_b_raw"].iloc[i], (w33["high"].max() + w33["low"].min()) / 2)
    w129 = ohlcv.iloc[i + 100 - 128: i + 101]
    assert np.isclose(e["kijun_129"].iloc[i + 100], (w129["high"].max() + w129["low"].min()) / 2)
    # senkou_a on bar i equals raw span A from DISPLACEMENT bars earlier
    assert np.isclose(e["senkou_a"].iloc[i], e["span_a_raw"].iloc[i - DISPLACEMENT])
    assert np.isclose(e["chikou"].iloc[i], ohlcv["close"].iloc[i + DISPLACEMENT])
    assert "kijun_65" in e and "kijun_129" in e


def test_presets():
    p = get_params()
    assert (p.tenkan, p.kijun, p.displacement, p.senkou_b, p.long_kijun) == (9, 17, 26, 33, (65, 129))
    assert PRESETS["classic"]["kijun"] == 26
    assert p.time_cycles == (9, 17, 26, 33, 42, 65, 76, 129, 172, 200, 257)


def test_future_cloud_and_cycles(ohlcv):
    e = compute_ichimoku(ohlcv)
    fc = future_cloud(e)
    assert len(fc) == DISPLACEMENT
    assert pd.Timestamp(fc[0]["date"]) > e["date"].iloc[-1]
    tc = time_cycles(e)
    assert tc["from_high"]["bars_since"] >= 0 and tc["from_low"]["bars_since"] >= 0


def test_time_cycle_hit():
    df = make_ohlcv(200)
    # force a clear top exactly 26 bars before the last bar
    idx = len(df) - 1 - 26
    df.loc[idx, "high"] = df["high"].max() * 1.5
    tc = time_cycles(compute_ichimoku(df))
    assert tc["from_high"]["bars_since"] == 26
    assert 26 in tc["from_high"]["cycle_hit"]
    assert tc["from_high"]["next_cycle"] == 33  # mốc tiếp theo trong dãy số học Trịnh Phát
    assert tc["from_high"]["next_cycle_date"] is not None


def test_signals_uptrend():
    df = make_ohlcv(300, trend=0.004)
    s = ichimoku_signals(compute_ichimoku(df))
    assert s["price_vs_cloud"] == "above_cloud"


def test_macd_matches_ema(ohlcv):
    e = compute_macd(ohlcv)
    ema12 = ohlcv["close"].ewm(span=12, adjust=False).mean()
    ema26 = ohlcv["close"].ewm(span=26, adjust=False).mean()
    assert np.isclose(e["macd"].iloc[-1], (ema12 - ema26).iloc[-1])


def test_rsi_bounds(ohlcv):
    r = compute_rsi(ohlcv)["rsi"].dropna()
    assert ((r >= 0) & (r <= 100)).all()
    up = pd.DataFrame({"close": np.arange(1, 50, dtype=float)})
    assert compute_rsi(up)["rsi"].iloc[-1] == 100


def test_breakout_detected():
    df = make_ohlcv(120)
    last = df.index[-1]
    df.loc[last, "close"] = df["high"].iloc[-21:-1].max() * 1.05
    df.loc[last, "high"] = df.loc[last, "close"]
    df.loc[last, "volume"] = df["volume"].iloc[-21:-1].mean() * 3
    e = compute_breakout(df)
    assert bool(e["breakout"].iloc[-1])
    assert e["vol_ratio"].iloc[-1] >= 1.5


def test_analyze_and_chart(ohlcv):
    a = analyze_dataframe("fpt", ohlcv)
    assert a["symbol"] == "FPT" and -100 <= a["score"] <= 100
    assert a["volume"] == int(ohlcv["volume"].iloc[-1])            # KLGD of the latest bar, in shares
    assert a["rating"] in {"Strong Bullish", "Bullish", "Neutral", "Bearish", "Strong Bearish"}
    c = chart_payload(ohlcv, bars=200)
    assert len(c["candles"]) == 200
    assert len(c["senkou_a"]) > 200  # includes projected cloud
    assert c["chikou"][-1]["value"] == float(ohlcv["close"].iloc[-1])
    assert set(c["long_kijun"]) == {"65", "129"} and c["params"]["kijun"] == 17


def test_score_series_matches_point_in_time_score():
    # trending up, trending down, choppy, plus volume spikes to trigger breakouts/breakdowns
    for seed, trend in ((1, 0.004), (2, -0.004), (3, 0.0), (4, 0.001)):
        df = make_ohlcv(260, seed=seed, trend=trend)
        df.loc[df.index[::23], "volume"] *= 4
        s = score_series(enrich(df))
        for i in range(60, len(df), 3):
            assert s.iloc[i] == analyze_dataframe("X", df.iloc[: i + 1])["score"], (seed, i)


def test_chart_has_score_series(ohlcv):
    c = chart_payload(ohlcv, bars=200)
    assert len(c["score"]) == 200 and all(-100 <= x["value"] <= 100 for x in c["score"])
    assert c["score"][-1]["value"] == analyze_dataframe("X", ohlcv)["score"]


def test_custom_ichimoku_params(ohlcv):
    from app.analysis.ichimoku import custom_params
    base = get_params()
    assert custom_params() == base                                   # nothing given = app setup
    p = custom_params(tenkan=7, kijun=22, senkou_a=30, senkou_b=44, chikou=20)
    assert (p.tenkan, p.kijun, p.displacement, p.senkou_b, p.lag) == (7, 22, 30, 44, 20)
    assert custom_params(senkou_a=30).lag == base.displacement       # moving the cloud keeps the Chikou
    e = compute_ichimoku(ohlcv, p)
    assert np.isclose(e["chikou"].iloc[100], ohlcv["close"].iloc[120])
    assert np.isclose(e["senkou_b"].iloc[100], e["span_b_raw"].iloc[70])
    c = chart_payload(ohlcv, bars=200, p=p)
    assert c["params"]["chikou"] == 20 and c["params"]["senkou_a"] == 30 and c["default_params"]["kijun"] == 17
    assert c["chikou"][-1]["time"] == c["candles"][-21]["time"]
    a = analyze_dataframe("X", ohlcv, p=p)
    assert a["ichimoku"]["params"]["tenkan"] == 7
    assert c["score"][-1]["value"] == a["score"]                     # score invariant holds for custom params too
    for i in range(80, len(ohlcv), 7):
        assert score_series(enrich(ohlcv, p), p).iloc[i] == analyze_dataframe("X", ohlcv.iloc[: i + 1], p=p)["score"]
