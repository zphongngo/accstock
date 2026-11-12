"""Stock strength for the Scanner: RS vs VN-Index, IBD-style RS Rating (1–99), a 0–100 technical score,
Minervini's Trend Template, sector strength (top-down) and a composite rank.

Two steps, because RS Rating is a percentile over the whole universe (every stock on the board):
  1. `strength_metrics(df, index_df)`: everything that only needs one stock's 1D OHLCV (+ VN-Index).
  2. `rank_universe(metrics, sectors)`: RS Rating, the RS-dependent conditions, sector strength, composite.

Composite = 40% RS Rating + 40% technical score + 20% sector strength (sector = average RS Rating of its stocks).
These scores only help screening; they don't replace an investment decision.
"""
from __future__ import annotations

import math

import pandas as pd

from app.analysis.ichimoku import get_params

ROC_WEIGHTS = {63: 0.4, 126: 0.2, 189: 0.2, 252: 0.2}   # latest quarter counts double
YEAR = 252                                               # sessions in 52 weeks
LEADER_RS = 80                                           # RS Rating of a market leader
TEMPLATE_RS = 70                                         # RS Rating needed by the Trend Template
MIN_BARS = 64                                            # at least ROC(63)
WEIGHTS = {"rs": 0.4, "tech": 0.4, "sector": 0.2}

# technical score (max 100), grouped so the user sees *where* a stock is strong
TECH = {  # key: (points, group, label)
    "cloud": (15, "trend", "Giá trên mây Ichimoku"),
    "tk": (10, "trend", "Tenkan > Kijun"),
    "long_kijun": (15, "trend", "Giá trên Kijun 65 và 129"),
    "macd": (10, "momentum", "MACD > Signal, histogram tăng"),
    "rsi": (10, "momentum", "RSI 50–70"),
    "rs80": (10, "momentum", f"RS Rating ≥ {LEADER_RS}"),
    "volume": (15, "flow", "KL > 1.5 × MA20 trong phiên tăng"),
    "breakout": (15, "flow", "Vượt đỉnh 20 tuần / 52 tuần"),
}
GROUP_MAX = {g: sum(p for p, gg, _ in TECH.values() if gg == g) for g in ("trend", "momentum", "flow")}
TEMPLATE = {  # Minervini Trend Template, all must pass
    "ma_stack": "Giá > MA50 > MA150 > MA200",
    "ma200_up": "MA200 đi lên ít nhất 1 tháng",
    "above_low": "Giá cao hơn đáy 52 tuần ≥ 30%",
    "near_high": "Giá cách đỉnh 52 tuần ≤ 25%",
    "rs70": f"RS Rating ≥ {TEMPLATE_RS}",
}


def _f(v, nd: int = 4):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    return round(float(v), nd)


def roc(close: pd.Series, n: int) -> float | None:
    """% change of the close over the last n sessions (None when there is not enough history)."""
    if len(close) <= n or not close.iloc[-1 - n]:
        return None
    return (float(close.iloc[-1]) / float(close.iloc[-1 - n]) - 1) * 100


def rs_raw(close: pd.Series) -> float | None:
    """IBD/O'Neil: 0.4×ROC63 + 0.2×ROC126 + 0.2×ROC189 + 0.2×ROC252. With less than a year of history the
    weights of the available ROCs are rescaled (ROC63 is required)."""
    parts = {n: roc(close, n) for n in ROC_WEIGHTS}
    if parts[63] is None:
        return None
    w = {n: ROC_WEIGHTS[n] for n, v in parts.items() if v is not None}
    return sum(parts[n] * w[n] for n in w) / sum(w.values())


def strength_metrics(df: pd.DataFrame, index_df: pd.DataFrame | None = None) -> dict:
    """Per-stock metrics from 1D OHLCV (columns date, open, high, low, close, volume), latest bar last."""
    from app.analysis.stock_scanner import enrich

    if len(df) < MIN_BARS:
        raise ValueError(f"need at least {MIN_BARS} daily bars, got {len(df)}")
    p = get_params()
    e = enrich(df)
    last, prev = e.iloc[-1], e.iloc[-2]
    close = e["close"]
    c = float(last["close"])

    # ---- 1. RS vs VN-Index and the RS line
    rs_index = rs_line_high = rs_leads = None
    if index_df is not None and not index_df.empty:
        idx = index_df[["date", "close"]].rename(columns={"close": "idx"})
        m = e[["date", "close", "high"]].merge(idx, on="date", how="inner")
        if len(m) > 63:
            r_stock, r_idx = roc(m["close"], 63), roc(m["idx"], 63)
            if r_stock is not None and r_idx is not None:
                rs_index = (1 + r_stock / 100) / (1 + r_idx / 100)
            line = m["close"] / m["idx"]
            window = line.tail(YEAR)
            rs_line_high = bool(line.iloc[-1] >= window.max() * 0.999)
            # RS line at a new high while the price is not: the classic early-strength signal
            rs_leads = bool(rs_line_high and float(m["close"].iloc[-1]) < float(m["high"].tail(YEAR).max()) * 0.98)

    # ---- 3. technical conditions (RS Rating ≥ 80 is added in rank_universe)
    hits = []
    top = max(last["senkou_a"], last["senkou_b"]) if pd.notna(last["senkou_a"]) and pd.notna(last["senkou_b"]) else None
    if top is not None and c > top:
        hits.append("cloud")
    if pd.notna(last["kijun"]) and last["tenkan"] > last["kijun"]:
        hits.append("tk")
    lks = [last.get(f"kijun_{n}") for n in p.long_kijun]
    if lks and all(pd.notna(v) and c > v for v in lks):
        hits.append("long_kijun")
    if pd.notna(last["macd"]) and last["macd"] > last["macd_signal"] and last["macd_hist"] > prev["macd_hist"]:
        hits.append("macd")
    if pd.notna(last["rsi"]) and 50 <= last["rsi"] < 70:
        hits.append("rsi")
    if pd.notna(last["vol_ratio"]) and last["vol_ratio"] >= 1.5 and c > float(prev["close"]):
        hits.append("volume")
    high_20w = e["high"].shift(1).tail(100).max()          # 20 weeks = 100 sessions, before today
    high_52w_prev = e["high"].shift(1).tail(YEAR).max()
    breakout_52w = bool(pd.notna(high_52w_prev) and c > high_52w_prev)
    if pd.notna(high_20w) and c > high_20w:
        hits.append("breakout")

    # ---- 4. Trend Template (RS Rating ≥ 70 is added in rank_universe)
    ma = {n: close.rolling(n).mean() for n in (50, 150, 200)}
    ma50, ma150, ma200 = (_f(ma[n].iloc[-1]) for n in (50, 150, 200))
    ma200_month = ma[200].iloc[-22] if len(e) >= 222 else float("nan")
    hi52, lo52 = float(e["high"].tail(YEAR).max()), float(e["low"].tail(YEAR).min())
    tpl = {
        "ma_stack": bool(ma200 is not None and c > ma50 > ma150 > ma200),
        "ma200_up": bool(ma200 is not None and pd.notna(ma200_month) and ma200 > ma200_month),
        "above_low": bool(lo52 > 0 and c >= lo52 * 1.3),
        "near_high": bool(hi52 > 0 and c >= hi52 * 0.75),
    }
    return {
        "date": pd.Timestamp(last["date"]).strftime("%Y-%m-%d"),
        "close": c,
        "change_pct": _f((c / float(prev["close"]) - 1) * 100, 2),
        "bars": len(e),
        "roc": {str(n): _f(roc(close, n), 2) for n in ROC_WEIGHTS},
        "rs_raw": _f(rs_raw(close)),
        "rs_index": _f(rs_index),
        "rs_line_high": rs_line_high,
        "rs_leads": rs_leads,
        "tech_hits": hits,
        "breakout_52w": breakout_52w,
        "rsi": _f(last["rsi"], 1),
        "vol_ratio": _f(last["vol_ratio"], 2),
        "from_high_pct": _f((c / hi52 - 1) * 100, 1),
        "from_low_pct": _f((c / lo52 - 1) * 100, 1),
        "template": tpl,
    }


def percentile_ratings(values: dict[str, float]) -> dict[str, int]:
    """1–99 percentile rank (99 = strongest); ties share the same rating."""
    if not values:
        return {}
    if len(values) == 1:
        return {k: 99 for k in values}
    s = pd.Series(values).rank(method="average")
    return {k: int(round(1 + 98 * (r - 1) / (len(values) - 1))) for k, r in s.items()}


def tech_score(hits: list[str]) -> tuple[int, dict[str, int]]:
    groups = {g: 0 for g in GROUP_MAX}
    for h in hits:
        pts, g, _ = TECH[h]
        groups[g] += pts
    return sum(groups.values()), groups


def rank_universe(metrics: dict[str, dict], sectors: list[tuple[str, list[str]]]) -> dict:
    """Rank every stock with metrics. `sectors` = the board's industry groups (not VN30 / Quan Tâm)."""
    rated = {s: m["rs_raw"] for s, m in metrics.items() if m.get("rs_raw") is not None}
    rs = percentile_ratings(rated)

    # ---- 5. sector strength = average RS Rating of its stocks
    sec_rows, member_of = [], {}
    for name, syms in sectors:
        members = [s for s in dict.fromkeys(syms) if s in rs]
        for s in dict.fromkeys(syms):
            member_of.setdefault(s, []).append(name)
        if not members:
            continue
        avg = sum(rs[s] for s in members) / len(members)
        sec_rows.append({"name": name, "rs_avg": round(avg, 1), "count": len(members),
                         "leaders": sum(rs[s] >= LEADER_RS for s in members),
                         "top": sorted(members, key=lambda s: -rs[s])[:3]})
    sec_rows.sort(key=lambda r: -r["rs_avg"])
    for i, r in enumerate(sec_rows, 1):
        r["rank"] = i
    sec_avg = {r["name"]: r["rs_avg"] for r in sec_rows}

    rows = []
    for sym, m in metrics.items():
        rr = rs.get(sym)
        hits = list(m["tech_hits"]) + (["rs80"] if rr is not None and rr >= LEADER_RS else [])
        tech, groups = tech_score(hits)
        tpl = {**m["template"], "rs70": rr is not None and rr >= TEMPLATE_RS}
        names = member_of.get(sym, [])
        strength = max((sec_avg[n] for n in names if n in sec_avg), default=None)
        sector = max(names, key=lambda n: sec_avg.get(n, -1)) if names else None
        parts = {"rs": rr, "tech": tech, "sector": strength}
        w = {k: WEIGHTS[k] for k, v in parts.items() if v is not None}
        composite = sum(parts[k] * w[k] for k in w) / sum(w.values()) if w else None
        rows.append({
            "symbol": sym, "sector": sector, "sectors": names, "date": m["date"], "close": m["close"], "change_pct": m["change_pct"],
            "rs_rating": rr, "rs_index": m["rs_index"], "rs_line_high": m["rs_line_high"], "rs_leads": m["rs_leads"],
            "roc": m["roc"], "tech": tech, "tech_groups": groups, "tech_hits": hits,
            "breakout_52w": m["breakout_52w"], "rsi": m["rsi"], "vol_ratio": m["vol_ratio"],
            "from_high_pct": m["from_high_pct"], "from_low_pct": m["from_low_pct"],
            "template": tpl, "template_pass": all(tpl.values()), "template_count": sum(tpl.values()),
            "sector_rs": strength, "composite": _f(composite, 1),
        })
    rows.sort(key=lambda r: (r["composite"] is None, -(r["composite"] or 0), r["symbol"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {"rows": rows, "sectors": sec_rows}


def legend() -> dict:
    """Labels for the dashboard (one source of truth for points and conditions)."""
    return {"tech": {k: {"points": p, "group": g, "label": lbl} for k, (p, g, lbl) in TECH.items()},
            "group_max": GROUP_MAX, "template": TEMPLATE, "weights": WEIGHTS,
            "leader_rs": LEADER_RS, "template_rs": TEMPLATE_RS}
