"""RSI (Wilder, 14)."""
import pandas as pd

PERIOD = 14


def compute_rsi(df: pd.DataFrame, period: int = PERIOD) -> pd.DataFrame:
    out = df.copy()
    delta = out["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.where(avg_loss != 0, 100.0)
    rsi = rsi.where(avg_gain.notna())
    out["rsi"] = rsi
    return out


def rsi_signals(df: pd.DataFrame) -> dict:
    v = float(df["rsi"].iloc[-1])
    zone = "overbought" if v >= 70 else "oversold" if v <= 30 else "bullish" if v >= 50 else "bearish"
    return {"rsi": round(v, 2), "zone": zone, "rising": bool(df["rsi"].iloc[-1] > df["rsi"].iloc[-2])}
