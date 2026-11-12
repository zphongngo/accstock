"""End-of-day scan of every Bảng điện symbol for Ichimoku BO/BD and send the result to Telegram.

Symbols = VN30 + every sector table + the Quan Tâm list (data/board_settings.json), each once; the bulletin is
sorted by score, high -> low.

Scheduled by Windows Task Scheduler at 15:05 Mon–Fri (daily_signals.bat). Public holidays are detected
automatically: if no symbol has a bar dated today, nothing is sent.

Usage (from project root):
    .venv\\Scripts\\python scripts\\daily_signals.py              scan every board symbol, send to Telegram
    .venv\\Scripts\\python scripts\\daily_signals.py --dry-run    print the message only
    .venv\\Scripts\\python scripts\\daily_signals.py --force      send even if today is not a trading day
    .venv\\Scripts\\python scripts\\daily_signals.py FPT HPG      only these symbols
"""
import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402,F401  - Windows cert store for HTTPS behind the corporate proxy
from app.analysis.daily_signals import board_symbols, build_message, scan  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.services import board_settings  # noqa: E402
from app.services.priceboard_service import PriceBoardService  # noqa: E402
from app.services.stock_service import StockService  # noqa: E402
from app.services.telegram_service import TelegramService  # noqa: E402
from app.services.timeframes import fmt_time, get_tf  # noqa: E402

log = logging.getLogger("daily_signals")


def setup_logging(s) -> Path:
    logdir = Path(s.DATA_DIR) / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    path = logdir / "daily_signals.log"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler(sys.stdout)])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return path


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*")
    ap.add_argument("--timeframe", default="1D")
    ap.add_argument("--dry-run", action="store_true", help="print the message, do not send")
    ap.add_argument("--force", action="store_true",
                    help="send even if today has no trading data or auto-send is turned off")
    a = ap.parse_args()

    s = get_settings()
    setup_logging(s)
    if not board_settings.load()["auto_daily"] and not a.force and not a.dry_run:
        log.info("Gửi tự động BO/BD 15:05 đang TẮT (Bảng điện > Tự động): không gửi.")
        return 0
    tf = get_tf(a.timeframe)
    symbols = [x.upper() for x in a.symbols]
    if not symbols:
        board = PriceBoardService(stock=None)
        try:
            symbols = board_symbols(board, board_settings.load()["watch"])
        finally:
            board.close()
    tg = TelegramService()
    if not a.dry_run and not tg.configured:
        log.error("Telegram chưa cấu hình: điền TELEGRAM_CHAT_ID trong telegram.env (xem scripts/telegram_chat_id.py)")
        return 2

    log.info("Scan %d symbols, timeframe %s", len(symbols), tf.key)
    svc = StockService()
    try:
        results, errors = scan(svc, symbols, tf.key)
    finally:
        svc.close()
    for r in results:
        flags = [n for n, v in (("cloud BO", r.cloud_bo), ("cloud BD", r.cloud_bd),
                                ("vol BO", r.vol_bo), ("vol BD", r.vol_bd)) if v]
        if flags:
            log.info("%-5s %s close %.0f score %4d %s", r.symbol, r.date, r.close, r.score, ",".join(flags))
    log.info("%d OK, %d lỗi", len(results), len(errors))

    today = fmt_time(date.today(), get_tf("1D"))
    traded_today = [r for r in results if r.date[:10] == today]
    if not traded_today and not a.force:
        log.info("Không có dữ liệu phiên %s (ngày nghỉ/lễ hoặc dữ liệu chưa cập nhật) - không gửi.", today)
        return 0
    # Only report symbols whose latest bar is today's (a suspended symbol would repeat an old signal).
    current = traded_today if traded_today else results
    msg = build_message(current, errors, datetime.now().strftime("%d/%m/%Y"), tf.key, scanned=len(symbols))
    has_signal = any(r.cloud_bo or r.cloud_bd or r.vol_bo or r.vol_bd for r in current)

    if a.dry_run:
        print("\n" + msg)
        return 0
    if not has_signal and not s.TELEGRAM_SEND_EMPTY:
        log.info("Không có tín hiệu, TELEGRAM_SEND_EMPTY=false - không gửi.")
        return 0
    try:
        n = tg.send(msg)
    except Exception as e:  # noqa: BLE001
        log.error("Gửi Telegram thất bại: %s", e)
        return 1
    log.info("Đã gửi %d tin tới %d chat.", n, len(tg.chat_ids))
    return 0


if __name__ == "__main__":
    sys.exit(main())
