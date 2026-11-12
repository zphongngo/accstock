"""ACC Stock Strength Monitor V1 (Requirements/Stock_Strength_Monitor_V1.md): scores and ranks every stock on the
board against VN30, for Swing and Position trading.

  Strength (0–100) = 30% Relative Strength + 25% Trend + 20% Momentum + 15% Volume & Money Flow + 10% Ichimoku
  Swing    = 0.30 RS_Swing    + 0.70 Other      RS_Swing    = 0.40 P1M + 0.35 P3M + 0.25 P6M
  Position = 0.30 RS_Position + 0.70 Other      RS_Position = 0.15 P3M + 0.35 P6M + 0.50 P12M
  Combined = (Swing + Position) / 2             Other = (0.25 Trend + 0.20 Mom + 0.15 Flow + 0.10 Ichi) / 0.70
  P_N = percentile (0–100) of the excess return vs VN30 over N sessions, across the whole universe on the same day.
Setup (breakout quality) and Risk (overheating) are separate 0–100 scores, never mixed into Combined.

Two steps like `strength.py`, because several parts are cross-sectional:
  1. `acc_metrics(df, bench_df)`: everything that only needs one stock's 1D OHLCV (+ VN30).
  2. `rank_acc(metrics, sectors)`: percentiles (RS, ROC20, ATR%), the final scores, signal and rank.
Only completed sessions are scored (`completed`): during the trading day the partial bar is left out.
These are algorithmic labels for screening, not automatic buy/sell recommendations.
"""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pandas as pd

from app.analysis.ichimoku import IchiParams, compute_ichimoku

BENCHMARK = "VN30"
PERIODS = {"1M": 21, "3M": 63, "6M": 126, "12M": 252}
RS_SWING = {"1M": 0.40, "3M": 0.35, "6M": 0.25}
RS_POSITION = {"3M": 0.15, "6M": 0.35, "12M": 0.50}
WEIGHTS = {"rs": 0.30, "trend": 0.25, "momentum": 0.20, "flow": 0.15, "ichimoku": 0.10}
MIN_BARS = 60
SESSION_END = (15, 0)   # ATC 14:45 + a margin: after this today's bar is complete
# Ichimoku V1 parameters from the requirement (Tenkan 9, Kijun 17, Senkou B 26, displacement 26)
ICHI = IchiParams(tenkan=9, kijun=17, senkou_b=26, displacement=26, long_kijun=(), time_cycles=(),
                  cycle_tolerance=0, pivot_lookback=0, preset="acc")

# key: (points, label) — one source of truth for the scoring and the dashboard legend
TREND = {
    "c_ma20": (10, "Giá > MA20"), "c_ma50": (15, "Giá > MA50"), "c_ma100": (10, "Giá > MA100"),
    "c_ma200": (15, "Giá > MA200"), "ma20_50": (15, "MA20 > MA50"), "ma50_100": (10, "MA50 > MA100"),
    "ma100_200": (15, "MA100 > MA200"), "ma50_up": (5, "MA50 đang tăng (so với 5 phiên trước)"),
    "ma200_up": (5, "MA200 đang tăng (so với 20 phiên trước)"),
}
MOMENTUM = {
    "macd_sig": (10, "MACD > Signal"), "macd_pos": (10, "MACD > 0"), "hist_up": (10, "Histogram MACD tăng"),
    "adx20": (10, "ADX14 > 20"), "di": (10, "+DI > −DI"),
}
MOMENTUM_SCALED = {"rsi": (30, "RSI14: (RSI − 30) / 40, kẹp 0–1"), "roc20": (20, "ROC20: percentile trong universe")}
FLOW = {"obv_ma": (15, "OBV > MA20(OBV)"), "obv_up": (10, "OBV tăng so với 5 phiên trước")}
FLOW_SCALED = {"rvol": (30, "RVOL = KL / MA20(KL): (RVOL − 0.7) / 0.8"), "cmf": (25, "CMF20: (CMF + 0.2) / 0.4"),
               "mfi": (20, "MFI14: (MFI − 20) / 60")}
ICHIMOKU = {
    "cloud": (25, "Giá > mây Kumo"), "tk": (20, "Tenkan > Kijun"), "future": (20, "Mây tương lai tăng"),
    "chikou": (15, "Chikou > giá 26 phiên trước"), "kijun": (10, "Giá > Kijun"),
    "kijun_up": (10, "Kijun đang tăng (so với 5 phiên trước)"),
}
SETUP = {
    "near_high": (15, "Cách đỉnh 52 tuần ≤ 5%"),
    "base": (20, "Tích lũy: biên độ 20 phiên trước ≤ 15%"),
    "vcp": (15, "Biến động co hẹp: ATR 10 phiên < 0.75 × ATR 50 phiên"),
    "dry_vol": (15, "KL cạn: TB 10 phiên < 0.85 × TB 50 phiên (trước phiên hiện tại)"),
    "rs_up": (15, "Đường RS (giá / VN30) cao hơn 20 phiên trước"),
    "breakout": (20, "Đóng cửa vượt Pivot (đỉnh 20 phiên) với KL > 1.5 × MA20"),
}
RISK = {
    "ext_ma20": (25, "Giá cách MA20 > 10%"), "rsi75": (20, "RSI > 75"),
    "atr_top": (25, "ATR% thuộc 20% biến động cao nhất universe"), "dd20": (30, "Giảm > 10% từ đỉnh 20 phiên"),
}
SIGNALS = ["OVERHEATED", "BREAKOUT", "STRONG BUY WATCH", "STRONG", "ACCUMULATION", "WEAK", "NEUTRAL"]


def _f(v, nd: int = 2):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    return round(float(v), nd)


def clamp01(x) -> float:
    return 0.0 if x is None or pd.isna(x) else max(0.0, min(1.0, float(x)))


def completed(df: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """Drop today's bar while the session is still running (scores use completed sessions only)."""
    now = now or datetime.now()
    if df.empty or pd.Timestamp(df["date"].iloc[-1]).date() != now.date():
        return df
    return df if (now.hour, now.minute) >= SESSION_END else df.iloc[:-1]


# ---------------- indicators not in the rest of the app ----------------
def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def adx(df: pd.DataFrame, n: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Wilder ADX, +DI, −DI."""
    up, down = df["high"].diff(), -df["low"].diff()
    plus = up.where((up > down) & (up > 0), 0.0)
    minus = down.where((down > up) & (down > 0), 0.0)
    w = dict(alpha=1 / n, min_periods=n, adjust=False)
    atr = true_range(df).ewm(**w).mean()
    pdi = 100 * plus.ewm(**w).mean() / atr
    mdi = 100 * minus.ewm(**w).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(**w).mean(), pdi, mdi


def obv(df: pd.DataFrame) -> pd.Series:
    return (np.sign(df["close"].diff()).fillna(0) * df["volume"]).cumsum()


def cmf(df: pd.DataFrame, n: int = 20) -> pd.Series:
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    mfm = (((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng).fillna(0)
    return (mfm * df["volume"]).rolling(n).sum() / df["volume"].rolling(n).sum().replace(0, np.nan)


def mfi(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    flow = tp * df["volume"]
    pos = flow.where(tp > tp.shift(1), 0.0).rolling(n).sum()
    neg = flow.where(tp < tp.shift(1), 0.0).rolling(n).sum()
    out = 100 - 100 / (1 + pos / neg.replace(0, np.nan))
    return out.where(neg != 0, 100.0).where(pos.notna())


def _ret(s: pd.Series, n: int) -> float | None:
    if len(s) <= n or not s.iloc[-1 - n]:
        return None
    return float(s.iloc[-1]) / float(s.iloc[-1 - n]) - 1


def _hits(conds: dict[str, bool], table: dict) -> tuple[list[str], int]:
    hits = [k for k in table if conds.get(k)]
    return hits, sum(table[k][0] for k in hits)


# ---------------- step 1: per stock ----------------
def acc_metrics(df: pd.DataFrame, bench_df: pd.DataFrame | None = None) -> dict:
    """Per-stock metrics from completed 1D OHLCV (date, open, high, low, close, volume), latest bar last."""
    from app.analysis.macd import compute_macd
    from app.analysis.rsi import compute_rsi

    if len(df) < MIN_BARS:
        raise ValueError(f"need at least {MIN_BARS} daily bars, got {len(df)}")
    e = df.sort_values("date").reset_index(drop=True)
    e = compute_rsi(compute_macd(compute_ichimoku(e, ICHI)))
    close, vol = e["close"], e["volume"]
    last, prev = e.iloc[-1], e.iloc[-2]
    c = float(last["close"])
    at = lambda s, k=1: s.iloc[-k] if len(s) >= k else float("nan")  # noqa: E731
    gt = lambda a, b: bool(pd.notna(a) and pd.notna(b) and a > b)    # noqa: E731

    # ---- relative strength vs VN30: excess return per period (falls back to plain return without a benchmark)
    ret, excess, rs_up = {}, {}, None
    m = None
    if bench_df is not None and not bench_df.empty:
        m = e[["date", "close"]].merge(bench_df[["date", "close"]].rename(columns={"close": "b"}), on="date")
    for k, n in PERIODS.items():
        r = _ret(close, n)
        ret[k] = _f(r * 100 if r is not None else None)
        if m is not None:
            rs_, rb = _ret(m["close"], n), _ret(m["b"], n)
            excess[k] = _f((rs_ - rb) * 100 if rs_ is not None and rb is not None else None)
        else:
            excess[k] = ret[k]
    if m is not None and len(m) > 20:
        line = m["close"] / m["b"]
        rs_up = bool(line.iloc[-1] > line.iloc[-21])

    # ---- trend: SMA structure
    ma = {n: close.rolling(n).mean() for n in (20, 50, 100, 200)}
    ml = {n: at(ma[n]) for n in ma}
    trend_hits, trend = _hits({
        "c_ma20": gt(c, ml[20]), "c_ma50": gt(c, ml[50]), "c_ma100": gt(c, ml[100]), "c_ma200": gt(c, ml[200]),
        "ma20_50": gt(ml[20], ml[50]), "ma50_100": gt(ml[50], ml[100]), "ma100_200": gt(ml[100], ml[200]),
        "ma50_up": gt(ml[50], at(ma[50], 6)), "ma200_up": gt(ml[200], at(ma[200], 21)),
    }, TREND)

    # ---- momentum (ROC20 percentile is added in rank_acc)
    a, pdi, mdi = adx(e)
    rsi = last["rsi"]
    rsi_pts = 30 * clamp01((rsi - 30) / 40)
    mom_hits, mom_base = _hits({
        "macd_sig": gt(last["macd"], last["macd_signal"]), "macd_pos": gt(last["macd"], 0),
        "hist_up": gt(last["macd_hist"], prev["macd_hist"]), "adx20": gt(at(a), 20), "di": gt(at(pdi), at(mdi)),
    }, MOMENTUM)

    # ---- volume & money flow
    vma20 = vol.rolling(20).mean()
    rvol = float(last["volume"]) / at(vma20) if at(vma20) else None
    o = obv(e)
    cmf_v, mfi_v = at(cmf(e)), at(mfi(e))
    flow_hits, flow_base = _hits({"obv_ma": gt(at(o), at(o.rolling(20).mean())), "obv_up": gt(at(o), at(o, 6))}, FLOW)
    flow_scaled = {"rvol": 30 * clamp01(((rvol or 0) - 0.7) / 0.8) if rvol is not None else 0.0,
                   "cmf": 25 * clamp01((cmf_v + 0.2) / 0.4), "mfi": 20 * clamp01((mfi_v - 20) / 60)}

    # ---- Ichimoku 9-17-26
    top = max(last["senkou_a"], last["senkou_b"]) if pd.notna(last["senkou_a"]) and pd.notna(last["senkou_b"]) else None
    d = ICHI.displacement
    ichi_hits, ichi = _hits({
        "cloud": top is not None and c > top, "tk": gt(last["tenkan"], last["kijun"]),
        "future": bool(pd.notna(last["span_a_raw"]) and pd.notna(last["span_b_raw"]) and last["span_a_raw"] >= last["span_b_raw"]),
        "chikou": len(e) > d and c > float(close.iloc[-1 - d]), "kijun": gt(c, last["kijun"]),
        "kijun_up": gt(last["kijun"], at(e["kijun"], 6)),
    }, ICHIMOKU)

    # ---- breakout setup (windows end the session *before* the latest bar unless stated)
    tr = true_range(e)
    before = e.iloc[:-1]
    hi52 = float(e["high"].tail(252).max())
    pivot = float(before["high"].tail(20).max())
    base_rng = (pivot - float(before["low"].tail(20).min())) / pivot if pivot else None
    vma20_prev = at(vma20, 2)
    vol_prev = before["volume"]
    breakout = bool(c > pivot and pd.notna(vma20_prev) and vma20_prev and float(last["volume"]) > 1.5 * vma20_prev)
    setup_hits, setup = _hits({
        "near_high": hi52 > 0 and c >= 0.95 * hi52,
        "base": base_rng is not None and base_rng <= 0.15,
        "vcp": len(tr) >= 50 and tr.tail(10).mean() < 0.75 * tr.tail(50).mean(),
        "dry_vol": len(vol_prev) >= 50 and vol_prev.tail(10).mean() < 0.85 * vol_prev.tail(50).mean(),
        "rs_up": bool(rs_up), "breakout": breakout,
    }, SETUP)

    # ---- risk (ATR% top 20% is cross-sectional, added in rank_acc)
    atr = tr.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    hi20 = float(e["high"].tail(20).max())
    dist_ma20 = (c / ml[20] - 1) * 100 if pd.notna(ml[20]) and ml[20] else None
    drawdown = (c / hi20 - 1) * 100 if hi20 else None
    risk_hits, _ = _hits({"ext_ma20": dist_ma20 is not None and dist_ma20 > 10, "rsi75": gt(rsi, 75),
                          "dd20": drawdown is not None and drawdown < -10}, RISK)

    return {
        "date": pd.Timestamp(last["date"]).strftime("%Y-%m-%d"),
        "close": c, "change_pct": _f((c / float(prev["close"]) - 1) * 100),
        "bars": len(e), "benchmark": m is not None,
        "ret": ret, "excess": excess,
        "trend": {"score": trend, "hits": trend_hits},
        "momentum": {"base": mom_base, "hits": mom_hits, "rsi": _f(rsi, 1), "rsi_pts": _f(rsi_pts),
                     "roc20": _f((_ret(close, 20) or 0) * 100 if _ret(close, 20) is not None else None)},
        "flow": {"score": _f(flow_base + sum(flow_scaled.values()), 1), "hits": flow_hits,
                 "pts": {k: _f(v, 1) for k, v in flow_scaled.items()},
                 "rvol": _f(rvol), "cmf": _f(cmf_v, 3), "mfi": _f(mfi_v, 1)},
        "ichimoku": {"score": ichi, "hits": ichi_hits},
        "setup": {"score": setup, "hits": setup_hits, "breakout": breakout, "pivot": _f(pivot),
                  "base_range_pct": _f(base_rng * 100 if base_rng is not None else None, 1),
                  "from_high_pct": _f((c / hi52 - 1) * 100 if hi52 else None, 1)},
        "risk": {"hits": risk_hits, "atr_pct": _f(at(atr) / c * 100 if c else None, 2),
                 "dist_ma20": _f(dist_ma20, 1), "drawdown20": _f(drawdown, 1)},
        "above": {str(n): gt(c, ml[n]) for n in (20, 50, 200)},
    }


def acc_pair(df: pd.DataFrame, bench_df: pd.DataFrame | None = None) -> dict:
    """Today's metrics and the previous session's (for the day-over-day comparison)."""
    out = {"today": acc_metrics(df, bench_df), "prev": None}
    try:
        out["prev"] = acc_metrics(df.iloc[:-1], bench_df)
    except ValueError:
        pass
    return out


# ---------------- step 2: across the universe ----------------
def percentiles(values: dict[str, float | None]) -> dict[str, float]:
    """0–100 percentile, 100 = best: (N − Rank) / (N − 1) × 100, ties share the average rank. N = 1 -> 50."""
    vals = {k: v for k, v in values.items() if v is not None}
    if not vals:
        return {}
    if len(vals) == 1:
        return {k: 50.0 for k in vals}
    r = pd.Series(vals).rank(method="average")          # 1 = lowest
    n = len(vals)
    return {k: round((x - 1) / (n - 1) * 100, 2) for k, x in r.items()}


def _wavg(parts: dict[str, float | None], weights: dict[str, float]) -> float | None:
    """Weighted mean of the available parts (weights rescaled when a period lacks history)."""
    w = {k: wt for k, wt in weights.items() if parts.get(k) is not None}
    return sum(parts[k] * w[k] for k in w) / sum(w.values()) if w else None


def signal(strength: float | None, setup: int, risk: int, breakout: bool) -> str:
    """Priority: OVERHEATED > BREAKOUT > STRONG BUY WATCH > STRONG > ACCUMULATION > WEAK > NEUTRAL."""
    s = strength or 0
    if risk >= 60:
        return "OVERHEATED"
    if breakout and setup >= 75:
        return "BREAKOUT"
    if s >= 80 and setup >= 75 and risk <= 40:
        return "STRONG BUY WATCH"
    if s >= 80:
        return "STRONG"
    if s >= 70 and setup >= 60 and not breakout:
        return "ACCUMULATION"
    if strength is not None and s < 40:
        return "WEAK"
    return "NEUTRAL"


def _band(v: float | None, hi: float, lo: float, names=("Bullish", "Neutral", "Bearish")) -> str | None:
    return None if v is None else names[0] if v >= hi else names[2] if v < lo else names[1]


def score_universe(metrics: dict[str, dict]) -> dict[str, dict]:
    """Final scores of every stock from `acc_metrics` outputs taken on the same day."""
    pct = {k: percentiles({s: m["excess"].get(k) for s, m in metrics.items()}) for k in PERIODS}
    roc = percentiles({s: m["momentum"]["roc20"] for s, m in metrics.items()})
    atr = percentiles({s: m["risk"]["atr_pct"] for s, m in metrics.items()})
    out = {}
    for s, m in metrics.items():
        p = {k: pct[k].get(s) for k in PERIODS}
        rs_sw, rs_pos = _wavg(p, RS_SWING), _wavg(p, RS_POSITION)
        rs = _wavg({"s": rs_sw, "p": rs_pos}, {"s": 0.5, "p": 0.5})
        trend = m["trend"]["score"]
        roc_pts = 20 * roc[s] / 100 if s in roc else 0.0
        momentum = m["momentum"]["base"] + (m["momentum"]["rsi_pts"] or 0) + roc_pts
        flow, ichi = m["flow"]["score"] or 0, m["ichimoku"]["score"]
        other = (0.25 * trend + 0.20 * momentum + 0.15 * flow + 0.10 * ichi) / 0.70
        swing = 0.30 * rs_sw + 0.70 * other if rs_sw is not None else None
        position = 0.30 * rs_pos + 0.70 * other if rs_pos is not None else None
        combined = _wavg({"s": swing, "p": position}, {"s": 0.5, "p": 0.5})
        risk_hits = list(m["risk"]["hits"]) + (["atr_top"] if atr.get(s, 0) >= 80 else [])
        risk = sum(RISK[k][0] for k in risk_hits)
        setup = m["setup"]["score"]
        out[s] = {
            "p": {k: _f(v, 1) for k, v in p.items()}, "rs_swing": _f(rs_sw, 1), "rs_position": _f(rs_pos, 1),
            "rs": _f(rs, 1), "trend": trend, "momentum": _f(momentum, 1), "flow": _f(flow, 1), "ichimoku": ichi,
            "roc_pct": _f(roc.get(s), 1), "atr_pct_rank": _f(atr.get(s), 1),
            "swing": _f(swing, 1), "position": _f(position, 1), "combined": _f(combined, 1),
            "setup": setup, "risk": risk, "risk_hits": risk_hits,
            "signal": signal(combined, setup, risk, m["setup"]["breakout"]),
        }
    return out


def _ranks(scores: dict[str, dict], key: str) -> dict[str, int]:
    order = sorted((s for s in scores if scores[s][key] is not None), key=lambda s: (-scores[s][key], s))
    return {s: i for i, s in enumerate(order, 1)}


def rank_acc(pairs: dict[str, dict], sectors: list[tuple[str, list[str]]] | None = None) -> dict:
    """Rank every stock with `acc_pair` output. `sectors` = the board's industry groups (for the sector column
    and the sector table). Today and the previous session are each ranked on their own universe."""
    today = {s: p["today"] for s, p in pairs.items() if p and p.get("today")}
    prev = {s: p["prev"] for s, p in pairs.items() if p and p.get("prev")}
    sc, sc_prev = score_universe(today), score_universe(prev)
    rank, rank_prev = _ranks(sc, "combined"), _ranks(sc_prev, "combined")
    ranks_by = {k: _ranks(sc, k) for k in ("swing", "position", "setup")}
    member_of: dict[str, list[str]] = {}
    for name, syms in sectors or []:
        for s in dict.fromkeys(syms):
            member_of.setdefault(s, []).append(name)

    rows = []
    for s, m in today.items():
        x, xp = sc[s], sc_prev.get(s) or {}
        rows.append({
            "symbol": s, "sectors": member_of.get(s, []), "sector": (member_of.get(s) or [None])[0],
            "date": m["date"], "close": m["close"], "change_pct": m["change_pct"], "bars": m["bars"],
            **x, "rank": rank.get(s), "rank_swing": ranks_by["swing"].get(s),
            "rank_position": ranks_by["position"].get(s), "rank_setup": ranks_by["setup"].get(s),
            "prev_combined": xp.get("combined"), "prev_rank": rank_prev.get(s),
            "delta": _f(x["combined"] - xp["combined"], 1) if x["combined"] is not None and xp.get("combined") is not None else None,
            "rank_change": rank_prev[s] - rank[s] if s in rank and s in rank_prev else None,
            "prev_signal": xp.get("signal"),
            "trend_state": _band(x["trend"], 70, 40),
            "risk_level": _band(x["risk"], 60, 30, ("High", "Medium", "Low")),
            "setup_state": "Breakout" if m["setup"]["breakout"] else "Accumulation" if "base" in m["setup"]["hits"] else None,
            "excess": m["excess"], "ret": m["ret"],
            "hits": {"trend": m["trend"]["hits"], "momentum": m["momentum"]["hits"], "flow": m["flow"]["hits"],
                     "ichimoku": m["ichimoku"]["hits"], "setup": m["setup"]["hits"]},
            "detail": {"rsi": m["momentum"]["rsi"], "rsi_pts": m["momentum"]["rsi_pts"], "roc20": m["momentum"]["roc20"],
                       "flow_pts": m["flow"]["pts"], "rvol": m["flow"]["rvol"], "cmf": m["flow"]["cmf"],
                       "mfi": m["flow"]["mfi"], "pivot": m["setup"]["pivot"],
                       "base_range_pct": m["setup"]["base_range_pct"], "from_high_pct": m["setup"]["from_high_pct"],
                       "atr_pct": m["risk"]["atr_pct"], "dist_ma20": m["risk"]["dist_ma20"],
                       "drawdown20": m["risk"]["drawdown20"], "benchmark": m["benchmark"]},
            "above": m["above"],
        })
    rows.sort(key=lambda r: (r["rank"] is None, r["rank"] or 0, r["symbol"]))

    # ---- market overview (V1.1 A)
    n = len(rows)
    combined = [r["combined"] for r in rows if r["combined"] is not None]
    buckets = [0] * 10
    for v in combined:
        buckets[min(9, int(v // 10))] += 1
    sig_count = {k: sum(r["signal"] == k for r in rows) for k in SIGNALS}
    sec_rows = []
    for name, syms in sectors or []:
        mem = [r for r in rows if name in r["sectors"] and r["combined"] is not None]
        if not mem:
            continue
        sec_rows.append({"name": name, "count": len(mem),
                         "combined": _f(sum(r["combined"] for r in mem) / len(mem), 1),
                         "rs": _f(sum(r["rs"] or 0 for r in mem) / len(mem), 1),
                         "strong": sum((r["combined"] or 0) >= 80 for r in mem),
                         "top": [r["symbol"] for r in sorted(mem, key=lambda r: -r["combined"])[:3]]})
    sec_rows.sort(key=lambda r: -r["combined"])
    overview = {
        "count": n,
        "strong": sum(v >= 80 for v in combined), "weak": sum(v < 40 for v in combined),
        "above": {k: sum(r["above"][k] for r in rows) for k in ("20", "50", "200")},
        "distribution": buckets, "signals": sig_count,
        "avg_combined": _f(sum(combined) / len(combined), 1) if combined else None,
        "sectors": sec_rows,
    }
    return {"rows": rows, "overview": overview, "date": max((r["date"] for r in rows), default=None),
            "prev_date": max((m["date"] for m in prev.values()), default=None)}


def legend() -> dict:
    t = lambda d: {k: {"points": p, "label": lbl} for k, (p, lbl) in d.items()}  # noqa: E731
    return {"benchmark": BENCHMARK, "weights": WEIGHTS, "periods": PERIODS,
            "rs_swing": RS_SWING, "rs_position": RS_POSITION,
            "trend": t(TREND), "momentum": t(MOMENTUM), "momentum_scaled": t(MOMENTUM_SCALED),
            "flow": t(FLOW), "flow_scaled": t(FLOW_SCALED), "ichimoku": t(ICHIMOKU),
            "setup": t(SETUP), "risk": t(RISK), "signals": SIGNALS,
            "ichimoku_params": {"tenkan": ICHI.tenkan, "kijun": ICHI.kijun, "senkou_b": ICHI.senkou_b,
                                "displacement": ICHI.displacement}}
