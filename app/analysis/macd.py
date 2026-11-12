"""MACD (12, 26, 9)."""
import pandas as pd

FAST, SLOW, SIGNAL = 12, 26, 9


def compute_macd(df: pd.DataFrame, fast: int = FAST, slow: int = SLOW, signal: int = SIGNAL) -> pd.DataFrame:
    out = df.copy()
    ema_fast = out["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = out["close"].ewm(span=slow, adjust=False).mean()
    out["macd"] = ema_fast - ema_slow
    out["macd_signal"] = out["macd"].ewm(span=signal, adjust=False).mean()
    out["macd_hist"] = out["macd"] - out["macd_signal"]
    out.loc[out.index[: slow - 1], ["macd", "macd_signal", "macd_hist"]] = float("nan")
    return out


def macd_signals(df: pd.DataFrame) -> dict:
    last, prev = df.iloc[-1], df.iloc[-2]
    cross = "none"
    if prev["macd"] <= prev["macd_signal"] and last["macd"] > last["macd_signal"]:
        cross = "bullish"
    elif prev["macd"] >= prev["macd_signal"] and last["macd"] < last["macd_signal"]:
        cross = "bearish"
    return {
        "macd": round(float(last["macd"]), 3),
        "signal": round(float(last["macd_signal"]), 3),
        "histogram": round(float(last["macd_hist"]), 3),
        "cross": cross,
        "above_zero": bool(last["macd"] > 0),
        "histogram_rising": bool(last["macd_hist"] > prev["macd_hist"]),
    }
