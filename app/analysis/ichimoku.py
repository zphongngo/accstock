"""Ichimoku kiểu Trịnh Phát (9-17-26-65-129).

Thiết lập mặc định (đổi được trong .env):
    Tenkan-sen   = (HH + LL) / 2 trong 9 phiên
    Kijun-sen    = (HH + LL) / 2 trong 17 phiên
    Chikou span  = giá đóng cửa vẽ lùi 26 phiên
    Senkou A     = (Tenkan + Kijun) / 2, đẩy tới 26 phiên
    Senkou B     = (HH + LL) / 2 trong 33 phiên, đẩy tới 26 phiên
    Kijun 65     = (HH + LL) / 2 trong 65 phiên   -> hỗ trợ/kháng cự trung hạn
    Kijun 129    = (HH + LL) / 2 trong 129 phiên  -> "Dao Găm", hỗ trợ/kháng cự dài hạn
Đặt ICHI_PRESET=classic để dùng Ichimoku truyền thống 9-26-26-52 + Dao Găm 129.

Lý thuyết số học thời gian của Trịnh Phát: các con số 9, 17, 26, 33, 42, 65, 76, 129, 172, 200, 257
(17 = 9+9-1, 26 = 9+17, 33 = 17+17-1, 65 = 33+33-1, 129 = 65+65-1, 257 = 129+129-1 ...) là các mốc
thời gian có khả năng gây đảo chiều. Module đếm số phiên từ đỉnh/đáy gần nhất và báo khi chạm mốc.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import pandas as pd

from app.core.config import get_settings
from app.services.timeframes import TF, fmt_time, future_times, get_tf


@dataclass(frozen=True)
class IchiParams:
    tenkan: int
    kijun: int
    senkou_b: int
    displacement: int
    long_kijun: tuple[int, ...]
    time_cycles: tuple[int, ...]
    cycle_tolerance: int
    pivot_lookback: int
    preset: str
    chikou: int | None = None   # Chikou lag; None = displacement (the cloud shift)

    @property
    def lag(self) -> int:
        return self.chikou or self.displacement


PRESETS = {
    "trinhphat": dict(tenkan=9, kijun=17, senkou_b=33, displacement=26, long_kijun=(65, 129)),
    "classic": dict(tenkan=9, kijun=26, senkou_b=52, displacement=26, long_kijun=(129,)),
}


def get_params() -> IchiParams:
    s = get_settings()
    preset = s.ICHI_PRESET.lower()
    base = PRESETS.get(preset, PRESETS["trinhphat"])
    if preset == "custom":
        base = dict(tenkan=s.ICHI_TENKAN, kijun=s.ICHI_KIJUN, senkou_b=s.ICHI_SENKOU_B,
                    displacement=s.ICHI_DISPLACEMENT,
                    long_kijun=tuple(int(x) for x in s.ICHI_LONG_KIJUN.split(",") if x.strip()))
    return IchiParams(
        **base,
        time_cycles=tuple(int(x) for x in s.ICHI_TIME_CYCLES.split(",") if x.strip()),
        cycle_tolerance=s.ICHI_CYCLE_TOLERANCE,
        pivot_lookback=s.ICHI_PIVOT_LOOKBACK,
        preset=preset if preset in PRESETS or preset == "custom" else "trinhphat",
    )


# Settings the dashboard lets the user change (Cổ phiếu tab): name -> IchiParams field
CUSTOM_FIELDS = {"tenkan": "tenkan", "kijun": "kijun", "senkou_a": "displacement", "senkou_b": "senkou_b",
                 "chikou": "chikou"}


def custom_params(base: IchiParams | None = None, **overrides: int | None) -> IchiParams:
    """`base` (default: the app setup) with the user's Tenkan / Kijun / Senkou A (cloud shift) / Senkou B / Chikou."""
    base = base or get_params()
    changes = {CUSTOM_FIELDS[k]: int(v) for k, v in overrides.items() if v is not None}
    if not changes:
        return base
    if "chikou" not in changes and "displacement" in changes and base.chikou is None:
        changes["chikou"] = base.displacement   # moving the cloud must not move the Chikou
    return replace(base, **changes)


def params_dict(p: IchiParams) -> dict:
    return {"tenkan": p.tenkan, "kijun": p.kijun, "senkou_a": p.displacement, "senkou_b": p.senkou_b,
            "chikou": p.lag, "displacement": p.displacement, "long_kijun": list(p.long_kijun), "preset": p.preset}


def _mid(df: pd.DataFrame, n: int) -> pd.Series:
    return (df["high"].rolling(n).max() + df["low"].rolling(n).min()) / 2


def compute_ichimoku(df: pd.DataFrame, p: IchiParams | None = None) -> pd.DataFrame:
    p = p or get_params()
    out = df.copy()
    out["tenkan"] = _mid(out, p.tenkan)
    out["kijun"] = _mid(out, p.kijun)
    out["span_a_raw"] = (out["tenkan"] + out["kijun"]) / 2       # value plotted displacement bars ahead
    out["span_b_raw"] = _mid(out, p.senkou_b)
    out["senkou_a"] = out["span_a_raw"].shift(p.displacement)    # cloud that applies to this bar
    out["senkou_b"] = out["span_b_raw"].shift(p.displacement)
    out["chikou"] = out["close"].shift(-p.lag)
    for n in p.long_kijun:
        out[f"kijun_{n}"] = _mid(out, n)
    # Kumo break: the close crosses out of the cloud on this bar (it was inside/below, now above; or the reverse).
    out["cloud_top"] = out[["senkou_a", "senkou_b"]].max(axis=1, skipna=False)
    out["cloud_bottom"] = out[["senkou_a", "senkou_b"]].min(axis=1, skipna=False)
    prev_close, prev_top, prev_bot = out["close"].shift(1), out["cloud_top"].shift(1), out["cloud_bottom"].shift(1)
    out["cloud_breakout"] = (out["close"] > out["cloud_top"]) & (prev_close <= prev_top)
    out["cloud_breakdown"] = (out["close"] < out["cloud_bottom"]) & (prev_close >= prev_bot)
    return out


def future_cloud(df: pd.DataFrame, p: IchiParams | None = None, tf: TF | None = None) -> list[dict]:
    """Cloud plotted `displacement` bars ahead of the last bar; `date` is a Timestamp."""
    p, tf = p or get_params(), tf or get_tf("1D")
    tail = df.tail(p.displacement)
    dates = future_times(df["date"].iloc[-1], p.displacement, tf)
    return [
        {"date": d, "senkou_a": float(r["span_a_raw"]), "senkou_b": float(r["span_b_raw"])}
        for d, (_, r) in zip(dates, tail.iterrows())
        if pd.notna(r["span_a_raw"]) and pd.notna(r["span_b_raw"])
    ]


def time_cycles(df: pd.DataFrame, p: IchiParams | None = None, tf: TF | None = None) -> dict:
    """Số phiên (nến) từ đỉnh / đáy cao nhất-thấp nhất trong `pivot_lookback` nến, so với các con số thời gian."""
    p, tf = p or get_params(), tf or get_tf("1D")
    win = df.tail(p.pivot_lookback).reset_index(drop=True)
    n = len(win)
    out = {}
    for name, idx, col in (("from_high", int(win["high"].idxmax()), "high"),
                           ("from_low", int(win["low"].idxmin()), "low")):
        bars = n - 1 - idx
        hits = [c for c in p.time_cycles if abs(bars - c) <= p.cycle_tolerance]
        upcoming = [c for c in p.time_cycles if c > bars + p.cycle_tolerance]
        nxt = upcoming[0] if upcoming else None
        nxt_date = None
        if nxt:
            nxt_date = fmt_time(future_times(win["date"].iloc[-1], nxt - bars, tf)[-1], tf)
        out[name] = {
            "pivot_date": fmt_time(win["date"].iloc[idx], tf),
            "pivot_price": float(win[col].iloc[idx]),
            "bars_since": bars,
            "cycle_hit": hits,
            "next_cycle": nxt,
            "bars_to_next": (nxt - bars) if nxt else None,
            "next_cycle_date": nxt_date,  # ước tính theo phiên giao dịch, chưa trừ ngày lễ
        }
    return out


def _f(v):
    return None if pd.isna(v) else round(float(v), 2)


def ichimoku_signals(df: pd.DataFrame, p: IchiParams | None = None, tf: TF | None = None) -> dict:
    p = p or get_params()
    last = df.iloc[-1]
    close = float(last["close"])
    sa, sb = last["senkou_a"], last["senkou_b"]
    if pd.isna(sa) or pd.isna(sb):
        position = "unknown"
    elif close > max(sa, sb):
        position = "above_cloud"
    elif close < min(sa, sb):
        position = "below_cloud"
    else:
        position = "inside_cloud"

    tk_cross = "none"
    if len(df) >= 2:
        q = df.iloc[-2]
        if q["tenkan"] <= q["kijun"] and last["tenkan"] > last["kijun"]:
            tk_cross = "bullish"
        elif q["tenkan"] >= q["kijun"] and last["tenkan"] < last["kijun"]:
            tk_cross = "bearish"

    chikou = "unknown"
    if len(df) > p.lag:
        chikou = "above_price" if close > float(df["close"].iloc[-1 - p.lag]) else "below_price"

    fa, fb = last["span_a_raw"], last["span_b_raw"]
    future_color = "unknown" if pd.isna(fa) or pd.isna(fb) else ("bullish" if fa >= fb else "bearish")

    long_lines = {}
    for n in p.long_kijun:
        v = last[f"kijun_{n}"]
        long_lines[str(n)] = {
            "value": _f(v),
            "price_above": None if pd.isna(v) else bool(close > v),
            "distance_pct": None if pd.isna(v) else round((close / float(v) - 1) * 100, 2),
        }

    return {
        "preset": p.preset,
        "params": params_dict(p),
        "tenkan": _f(last["tenkan"]),
        "kijun": _f(last["kijun"]),
        "senkou_a": _f(sa),
        "senkou_b": _f(sb),
        "price_vs_cloud": position,
        "price_above_kijun": None if pd.isna(last["kijun"]) else bool(close > last["kijun"]),
        "tenkan_above_kijun": None if pd.isna(last["kijun"]) else bool(last["tenkan"] > last["kijun"]),
        "tk_cross": tk_cross,
        "chikou": chikou,
        "future_cloud": future_color,
        "cloud_top": _f(last.get("cloud_top")),
        "cloud_bottom": _f(last.get("cloud_bottom")),
        "cloud_breakout": bool(last.get("cloud_breakout", False)),   # BO: close crossed above the cloud on this bar
        "cloud_breakdown": bool(last.get("cloud_breakdown", False)),  # BD: close crossed below the cloud on this bar
        "long_kijun": long_lines,
        "time_cycles": time_cycles(df, p, tf),
    }
