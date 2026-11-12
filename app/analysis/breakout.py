"""Volume MA20 and price breakout detection.

Breakout  = close above the highest high of the previous N sessions AND volume >= k x Volume MA20.
Breakdown = close below the lowest low of the previous N sessions AND volume >= k x Volume MA20.
"""
import pandas as pd

VOL_MA = 20
LOOKBACK = 20
VOL_MULTIPLIER = 1.5


def compute_breakout(df: pd.DataFrame, lookback: int = LOOKBACK, vol_mult: float = VOL_MULTIPLIER) -> pd.DataFrame:
    out = df.copy()
    out["vol_ma20"] = out["volume"].rolling(VOL_MA).mean()
    out["vol_ratio"] = out["volume"] / out["vol_ma20"]
    out["resistance"] = out["high"].shift(1).rolling(lookback).max()
    out["support"] = out["low"].shift(1).rolling(lookback).min()
    strong_vol = out["vol_ratio"] >= vol_mult
    out["breakout"] = (out["close"] > out["resistance"]) & strong_vol
    out["breakdown"] = (out["close"] < out["support"]) & strong_vol
    return out


def breakout_signals(df: pd.DataFrame) -> dict:
    last = df.iloc[-1]
    recent = df.tail(5)
    return {
        "volume": int(last["volume"]),
        "vol_ma20": None if pd.isna(last["vol_ma20"]) else int(last["vol_ma20"]),
        "vol_ratio": None if pd.isna(last["vol_ratio"]) else round(float(last["vol_ratio"]), 2),
        "resistance_20": None if pd.isna(last["resistance"]) else float(last["resistance"]),
        "support_20": None if pd.isna(last["support"]) else float(last["support"]),
        "breakout_today": bool(last["breakout"]),
        "breakdown_today": bool(last["breakdown"]),
        "breakout_last_5d": bool(recent["breakout"].any()),
        "distance_to_resistance_pct": None if pd.isna(last["resistance"])
        else round((float(last["resistance"]) / float(last["close"]) - 1) * 100, 2),
    }
