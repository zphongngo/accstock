"""Combine all indicators, score a symbol, and scan a watchlist."""
from __future__ import annotations

import logging
import math

import pandas as pd

from app.analysis.breakout import breakout_signals, compute_breakout
from app.analysis.ichimoku import IchiParams, compute_ichimoku, future_cloud, get_params, ichimoku_signals, params_dict
from app.analysis.macd import compute_macd, macd_signals
from app.analysis.rsi import compute_rsi, rsi_signals
from app.services.timeframes import chart_time, fmt_time, get_tf

log = logging.getLogger(__name__)
MIN_BARS = 60  # Kijun 129 / chu kỳ dài sẽ trống nếu dữ liệu ít hơn


def enrich(df: pd.DataFrame, p: IchiParams | None = None) -> pd.DataFrame:
    df = df.sort_values("date").reset_index(drop=True)
    df = compute_ichimoku(df, p)
    for fn in (compute_macd, compute_rsi, compute_breakout):
        df = fn(df)
    return df


def score_signals(ich: dict, macd: dict, rsi: dict, brk: dict) -> tuple[int, list[str]]:
    score, notes = 0, []

    def add(points: int, note: str):
        nonlocal score
        score += points
        notes.append(f"{'+' if points >= 0 else ''}{points} {note}")

    pos = ich["price_vs_cloud"]
    if pos == "above_cloud":
        add(20, "Giá trên mây Ichimoku")
    elif pos == "below_cloud":
        add(-20, "Giá dưới mây Ichimoku")
    pr = ich["params"]
    if ich["tenkan_above_kijun"] is True:
        add(10, f"Tenkan {pr['tenkan']} > Kijun {pr['kijun']}")
    elif ich["tenkan_above_kijun"] is False:
        add(-10, f"Tenkan {pr['tenkan']} < Kijun {pr['kijun']}")
    if ich["tk_cross"] == "bullish":
        add(5, "Tenkan cắt lên Kijun")
    elif ich["tk_cross"] == "bearish":
        add(-5, "Tenkan cắt xuống Kijun")
    d = pr.get("chikou", pr["displacement"])
    if ich["chikou"] == "above_price":
        add(10, f"Chikou trên giá {d} phiên trước")
    elif ich["chikou"] == "below_price":
        add(-10, f"Chikou dưới giá {d} phiên trước")
    if ich["future_cloud"] == "bullish":
        add(5, "Mây tương lai tăng")
    elif ich["future_cloud"] == "bearish":
        add(-5, "Mây tương lai giảm")
    for n, lk in ich["long_kijun"].items():
        if lk["price_above"] is True:
            add(5, f"Giá trên Kijun {n}")
        elif lk["price_above"] is False:
            add(-5, f"Giá dưới Kijun {n}")

    add(10 if macd["macd"] > macd["signal"] else -10,
        "MACD trên Signal" if macd["macd"] > macd["signal"] else "MACD dưới Signal")
    add(5 if macd["above_zero"] else -5, "MACD > 0" if macd["above_zero"] else "MACD < 0")
    if macd["cross"] == "bullish":
        add(5, "MACD cắt lên")
    elif macd["cross"] == "bearish":
        add(-5, "MACD cắt xuống")

    zone = rsi["zone"]
    if zone == "bullish":
        add(10, f"RSI {rsi['rsi']} vùng tích cực")
    elif zone == "bearish":
        add(-5, f"RSI {rsi['rsi']} dưới 50")
    elif zone == "overbought":
        add(0, f"RSI {rsi['rsi']} quá mua – rủi ro điều chỉnh")
    elif zone == "oversold":
        add(-10, f"RSI {rsi['rsi']} quá bán")

    if brk["breakout_today"]:
        add(20, f"Breakout hôm nay, KL = {brk['vol_ratio']}x MA20")
    elif brk["breakout_last_5d"]:
        add(10, "Có breakout trong 5 phiên gần nhất")
    if brk["breakdown_today"]:
        add(-20, "Breakdown hỗ trợ 20 phiên với KL lớn")

    return max(-100, min(100, score)), notes


def score_series(e: pd.DataFrame, p: IchiParams | None = None) -> pd.Series:
    """Score of every bar of an `enrich()`-ed frame — the same rules as `score_signals`, vectorised.

    Every input is causal (rolling / EWM / positive shifts), so the value at bar i equals the
    score `analyze_dataframe` would give using data up to bar i only.
    """
    p = p or get_params()
    close, zero = e["close"], pd.Series(0, index=e.index)

    def pm(cond_pos, cond_neg, pts_pos, pts_neg=None):
        pts_neg = -pts_pos if pts_neg is None else pts_neg
        return zero.mask(cond_pos, pts_pos).mask(cond_neg & ~cond_pos, pts_neg)

    sa, sb = e["senkou_a"], e["senkou_b"]
    cloud_ok = sa.notna() & sb.notna()
    s = pm(cloud_ok & (close > pd.concat([sa, sb], axis=1).max(axis=1)),
           cloud_ok & (close < pd.concat([sa, sb], axis=1).min(axis=1)), 20)

    tk, kj = e["tenkan"], e["kijun"]
    s += pm(kj.notna() & (tk > kj), kj.notna() & ~(tk > kj), 10)
    tk_prev, kj_prev = tk.shift(1), kj.shift(1)
    s += pm((tk_prev <= kj_prev) & (tk > kj), (tk_prev >= kj_prev) & (tk < kj), 5)

    past = close.shift(p.lag)
    s += pm(past.notna() & (close > past), past.notna() & ~(close > past), 10)

    fa, fb = e["span_a_raw"], e["span_b_raw"]
    fut_ok = fa.notna() & fb.notna()
    s += pm(fut_ok & (fa >= fb), fut_ok & (fa < fb), 5)

    for n in p.long_kijun:
        lk = e[f"kijun_{n}"]
        s += pm(lk.notna() & (close > lk), lk.notna() & ~(close > lk), 5)

    m, sig = e["macd"], e["macd_signal"]
    s += zero.mask(m > sig, 10).mask(~(m > sig), -10)
    s += zero.mask(m > 0, 5).mask(~(m > 0), -5)
    s += pm((m.shift(1) <= sig.shift(1)) & (m > sig), (m.shift(1) >= sig.shift(1)) & (m < sig), 5)

    r = e["rsi"]
    s += zero.mask(r >= 70, 0).mask((r <= 30) & ~(r >= 70), -10) \
        .mask((r >= 50) & (r < 70), 10).mask(~(r >= 70) & ~(r <= 30) & ~(r >= 50), -5)

    bo, bd = e["breakout"].astype(bool), e["breakdown"].astype(bool)
    bo_5 = bo.astype(int).rolling(5, min_periods=1).max().astype(bool)
    s += zero.mask(bo, 20).mask(~bo & bo_5, 10)
    s += zero.mask(bd, -20)
    return s.clip(-100, 100)


def rating(score: int) -> str:
    if score >= 60:
        return "Strong Bullish"
    if score >= 25:
        return "Bullish"
    if score > -25:
        return "Neutral"
    if score > -60:
        return "Bearish"
    return "Strong Bearish"


def analyze_dataframe(symbol: str, df: pd.DataFrame, timeframe: str = "1D", p: IchiParams | None = None) -> dict:
    tf = get_tf(timeframe)
    if len(df) < MIN_BARS:
        raise ValueError(f"{symbol}: need at least {MIN_BARS} {tf.key} bars, got {len(df)}")
    e = enrich(df, p)
    last, prev = e.iloc[-1], e.iloc[-2]
    ich, mac, rs, brk = ichimoku_signals(e, p, tf=tf), macd_signals(e), rsi_signals(e), breakout_signals(e)
    score, notes = score_signals(ich, mac, rs, brk)
    return {
        "symbol": symbol.upper(),
        "timeframe": tf.key,
        "date": fmt_time(last["date"], tf),
        "close": float(last["close"]),
        "change_pct": round((float(last["close"]) / float(prev["close"]) - 1) * 100, 2),
        "volume": int(last["volume"]) if pd.notna(last["volume"]) else None,   # shares traded in the latest bar
        "bars": len(e),
        "score": score,
        "rating": rating(score),
        "score_notes": notes,
        "ichimoku": ich,
        "macd": mac,
        "rsi": rs,
        "volume_breakout": brk,
    }


def _clean(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return round(float(v), 4)


def chart_payload(df: pd.DataFrame, bars: int = 250, timeframe: str = "1D", p: IchiParams | None = None) -> dict:
    """Series for the dashboard chart (lightweight-charts format)."""
    p, tf = p or get_params(), get_tf(timeframe)
    e = enrich(df, p)
    fc = future_cloud(e, p, tf)
    e = e.assign(score=score_series(e, p)).tail(bars)
    t = [chart_time(d, tf) for d in e["date"]]

    def series(col):
        return [{"time": d, "value": _clean(v)} for d, v in zip(t, e[col]) if _clean(v) is not None]

    candles = [
        {"time": d, "open": float(o), "high": float(h), "low": float(lo), "close": float(c)}
        for d, o, h, lo, c in zip(t, e["open"], e["high"], e["low"], e["close"])
    ]
    volume = [{"time": d, "value": float(v), "up": bool(c >= o)}
              for d, v, c, o in zip(t, e["volume"], e["close"], e["open"])]
    span_a = series("senkou_a") + [{"time": chart_time(x["date"], tf), "value": x["senkou_a"]} for x in fc]
    span_b = series("senkou_b") + [{"time": chart_time(x["date"], tf), "value": x["senkou_b"]} for x in fc]
    markers = [{"time": d, "type": "breakout"} for d, b in zip(t, e["breakout"]) if b] + \
              [{"time": d, "type": "breakdown"} for d, b in zip(t, e["breakdown"]) if b]
    return {
        "timeframe": tf.key,
        "intraday": tf.intraday,
        "candles": candles,
        "volume": volume,
        "vol_ma20": series("vol_ma20"),
        "tenkan": series("tenkan"),
        "kijun": series("kijun"),
        "senkou_a": span_a,
        "senkou_b": span_b,
        # Chikou = today's close plotted `lag` bars back
        "chikou": [{"time": d, "value": float(v)}
                   for d, v in zip(t[:-p.lag], e["close"].iloc[p.lag:])],
        "long_kijun": {str(n): series(f"kijun_{n}") for n in p.long_kijun},
        "params": params_dict(p),
        "default_params": params_dict(get_params()),
        "macd": series("macd"),
        "macd_signal": series("macd_signal"),
        "macd_hist": series("macd_hist"),
        "rsi": series("rsi"),
        "score": series("score"),
        "markers": sorted(markers, key=lambda m: m["time"]),
    }
