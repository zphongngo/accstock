"""Check SSI (REST + realtime stream), Vietcap and Claude connectivity.

Usage (from project root):  .venv\\Scripts\\python scripts\\check_connection.py [SYMBOL] [--stream-seconds N]
"""
import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.security import mask  # noqa: E402
from app.services.claude_service import ClaudeService  # noqa: E402
from app.services.realtime_service import RealtimeService  # noqa: E402
from app.services.ssi_service import SSIService  # noqa: E402
from app.services.timeframes import TIMEFRAMES  # noqa: E402
from app.services.vietcap_service import VietcapService  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", nargs="?", default="FPT")
    ap.add_argument("--stream-seconds", type=int, default=10)
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console is cp1252
    s, symbol = get_settings(), a.symbol.upper()
    end, start = date.today(), date.today() - timedelta(days=10)
    print(f"SSI consumer ID: {mask(s.SSI_CONSUMER_ID)} | API {s.SSI_API_URL}")

    ssi = SSIService()
    for name, svc in (("SSI", ssi), ("Vietcap", VietcapService())):
        for tf in TIMEFRAMES:
            try:
                df = svc.get_ohlcv(symbol, start - timedelta(days=30 if tf == "1W" else 0), end, tf)
                last = df.iloc[-1]
                print(f"[OK]   {name:7} {tf:3}: {len(df):4} bars, last {last['date']} close {last['close']:.0f}")
            except Exception as e:  # noqa: BLE001
                print(f"[FAIL] {name:7} {tf:3}: {e}")

    if s.SSI_STREAM_ENABLED and ssi.configured:
        rt = RealtimeService(ssi)
        ok = rt.watch(symbol)
        print(f"[{'OK' if ok else 'FAIL'}]   SSI stream: {rt.status()['state']} {rt.status()['error'] or ''}")
        if ok:
            time.sleep(a.stream_seconds)
            bars = rt.bars_1m(symbol)
            st = rt.status()
            print(f"       {st['messages']} bar messages in {a.stream_seconds}s"
                  + (f", last {bars.iloc[-1].to_dict()}" if len(bars) else " (ngoài giờ giao dịch thì không có dữ liệu)"))
            rt.stop()
    ssi.close()

    ai = ClaudeService()
    if not ai.configured:
        print("[SKIP] Claude: ANTHROPIC_API_KEY not set")
    else:
        try:
            msg = ai._get_client().messages.create(model=s.CLAUDE_MODEL, max_tokens=20,
                                                    messages=[{"role": "user", "content": "Reply: OK"}])
            print(f"[OK]   Claude ({s.CLAUDE_MODEL}): {msg.content[0].text.strip()}")
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] Claude: {e}")


if __name__ == "__main__":
    main()
