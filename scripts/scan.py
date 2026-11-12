"""Run the technical scanner from the command line and save results to data/scan_YYYYMMDD.csv.

Usage:  .venv\\Scripts\\python scripts\\scan.py              (WATCHLIST from .env)
        .venv\\Scripts\\python scripts\\scan.py FPT HPG MWG --min-score 25
"""
import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analysis.stock_scanner import analyze_dataframe  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.services.stock_service import StockService  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("symbols", nargs="*")
    p.add_argument("--min-score", type=int, default=-100)
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--timeframe", default="1D")
    a = p.parse_args()
    s = get_settings()
    symbols = [x.upper() for x in a.symbols] or s.watchlist
    svc, rows = StockService(), []
    for sym in symbols:
        try:
            df, _ = svc.get_ohlcv(sym, refresh=a.refresh, timeframe=a.timeframe)
            r = analyze_dataframe(sym, df, a.timeframe)
            rows.append({
                "symbol": sym, "date": r["date"], "close": r["close"], "change_pct": r["change_pct"],
                "score": r["score"], "rating": r["rating"], "cloud": r["ichimoku"]["price_vs_cloud"],
                "rsi": r["rsi"]["rsi"], "macd_cross": r["macd"]["cross"],
                "vol_ratio": r["volume_breakout"]["vol_ratio"], "breakout": r["volume_breakout"]["breakout_today"],
            })
            print(f"{sym:6} score {r['score']:>4}  {r['rating']}")
        except Exception as e:  # noqa: BLE001
            print(f"{sym:6} ERROR {e}")
    if rows:
        out = pd.DataFrame(rows).query("score >= @a.min_score").sort_values("score", ascending=False)
        path = Path(s.DATA_DIR) / f"scan_{date.today():%Y%m%d}.csv"
        out.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"\nSaved {len(out)} rows -> {path}")


if __name__ == "__main__":
    main()
