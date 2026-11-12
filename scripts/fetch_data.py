"""Download / refresh OHLCV into data/cache and export CSV copies to data/.

Usage:  .venv\\Scripts\\python scripts\\fetch_data.py FPT HPG VNM [--timeframe 5m|15m|30m|1h|1D|1W]
        .venv\\Scripts\\python scripts\\fetch_data.py --watchlist
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.services.stock_service import StockService  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("symbols", nargs="*")
    p.add_argument("--watchlist", action="store_true")
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--timeframe", default="1D")
    a = p.parse_args()
    s = get_settings()
    symbols = s.watchlist if a.watchlist or not a.symbols else [x.upper() for x in a.symbols]
    svc = StockService()
    for sym in symbols:
        try:
            df, src = svc.get_ohlcv(sym, days=a.days, refresh=True, timeframe=a.timeframe)
            out = Path(s.DATA_DIR) / f"{sym}_{a.timeframe}.csv"
            df.to_csv(out, index=False)
            print(f"[OK]   {sym}: {len(df)} bars ({src}) -> {out.name}")
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {sym}: {e}")


if __name__ == "__main__":
    main()
