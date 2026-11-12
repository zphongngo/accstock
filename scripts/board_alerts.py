"""Scheduled Telegram bulletin from the price board: 1. strong, 2. weak (± threshold vs reference price) and
3. the "Quan Tâm" list (A-Z), the same message as the Bảng điện tab's "Gửi tin nhắn" button.

The app's background scheduler sends this bulletin according to the time window and interval saved on the Bảng điện
page. The old Windows Task Scheduler task is intentionally a no-op unless called with --once or --force.

The Quan Tâm list and the threshold come from data/board_settings.json, which the dashboard keeps in sync.
Works without the server running: one request for the quotes; %V20p uses today's board_scores.json when the
server has computed it, else the 1D CSV cache. Non-trading days are skipped: nothing is sent on Saturday/Sunday,
nor when VN-Index has no 1-minute bar dated today (public holidays, Tết). If that check can't be made (network),
nothing is sent either. Turned off ("Tự động" box unticked on the Bảng điện tab, auto_board=false): nothing is
sent unless --force.

Usage (from project root):
    .venv\\Scripts\\python scripts\\board_alerts.py --once       send one message now
    .venv\\Scripts\\python scripts\\board_alerts.py --dry-run    print the message only
    .venv\\Scripts\\python scripts\\board_alerts.py --once       send one message now
    .venv\\Scripts\\python scripts\\board_alerts.py --force      send even on a non-trading day
    .venv\\Scripts\\python scripts\\board_alerts.py --threshold 4
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402,F401  - Windows cert store for HTTPS behind the corporate proxy
from app.core.config import get_settings  # noqa: E402
from app.services import board_settings  # noqa: E402
from app.services.priceboard_service import PriceBoardService  # noqa: E402
from app.services.telegram_service import TelegramService  # noqa: E402

log = logging.getLogger("board_alerts")


def setup_logging(s) -> Path:
    logdir = Path(s.DATA_DIR) / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    path = logdir / "board_alerts.log"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler(sys.stdout)])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return path


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, help="default: the threshold saved from the Bảng điện tab")
    ap.add_argument("--dry-run", action="store_true", help="print the message, do not send")
    ap.add_argument("--once", action="store_true", help="send one bulletin now (respects the auto-send switch)")
    ap.add_argument("--force", action="store_true",
                    help="send even if today is not a trading day or auto-send is turned off")
    a = ap.parse_args()

    s = get_settings()
    setup_logging(s)
    cfg = board_settings.load()
    if not a.dry_run and not a.once and not a.force:
        log.info("Bản tin được gửi theo lịch trong app; lần chạy Task Scheduler cũ được bỏ qua.")
        return 0
    threshold = a.threshold or cfg["threshold"]
    if not cfg["auto_board"] and not a.force and not a.dry_run:
        log.info("Gửi tự động bản tin bảng điện đang TẮT (Bảng điện > Tự động): không gửi.")
        return 0
    tg = TelegramService()
    if not a.dry_run and not tg.configured:
        log.error("Telegram chưa cấu hình: điền TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID trong telegram.env")
        return 2
    svc = PriceBoardService(stock=None)
    try:
        if not a.force:
            try:
                trading = svc.trading_today()
            except Exception as e:  # noqa: BLE001  (can't tell -> don't risk a bulletin on a holiday)
                log.warning("Không kiểm tra được ngày giao dịch (%s): không gửi.", e)
                return 0
            if not trading:
                log.info("Hôm nay không phải ngày giao dịch (cuối tuần / ngày lễ): không gửi.")
                return 0
        messages, strong, weak = svc.movers_message(threshold, cfg["watch"])
    finally:
        svc.close()
    log.info("Ngưỡng ±%g%%: %d mạnh, %d yếu, Quan Tâm %d mã, %d tin", threshold, strong, weak,
             len(cfg["watch"]), len(messages))
    if a.dry_run:
        print("\n\n".join(messages))
        return 0
    sent = 0
    for m in messages:
        sent += tg.send(m)
    log.info("Đã gửi %d tin tới %d chat", sent, len(tg.chat_ids))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        log.exception("board_alerts failed")
        sys.exit(1)
