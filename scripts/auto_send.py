"""Turn the scheduled Telegram jobs on/off without the server (the same switches as the "Tự động" tick boxes on the
Bảng điện tab, stored in data/board_settings.json).

Usage (from project root):
    .venv\\Scripts\\python scripts\\auto_send.py                 show the current state
    .venv\\Scripts\\python scripts\\auto_send.py off             turn both off
    .venv\\Scripts\\python scripts\\auto_send.py on board        board bulletin (12 times a day) only
    .venv\\Scripts\\python scripts\\auto_send.py off daily       15:05 BO/BD alert only
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import board_settings  # noqa: E402

JOBS = {"board": ("auto_board", "Bản tin bảng điện (12 khung giờ)"), "daily": ("auto_daily", "BO/BD 15:05")}


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = [a.lower() for a in argv]
    if args and args[0] in ("on", "off"):
        which = args[1] if len(args) > 1 else "all"
        if which not in (*JOBS, "all"):
            print(f"Không rõ lịch '{which}'. Dùng: board, daily hoặc all.")
            return 2
        keys = JOBS if which == "all" else {which: JOBS[which]}
        board_settings.save(**{k: args[0] == "on" for k, _ in keys.values()})
    elif args and args[0] not in ("status",):
        print(__doc__)
        return 2
    cfg = board_settings.load()
    for key, label in JOBS.values():
        print(f"  {'BẬT' if cfg[key] else 'TẮT'}  {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
