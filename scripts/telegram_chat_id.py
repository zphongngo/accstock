"""Show the chat IDs that have messaged the bot, and optionally save one into telegram.env.

1. In Telegram, open the bot and send /start (for a group: add the bot to the group and send any message).
2. Run:   .venv\\Scripts\\python scripts\\telegram_chat_id.py           list chats
          .venv\\Scripts\\python scripts\\telegram_chat_id.py --save    save the most recent chat ID
          .venv\\Scripts\\python scripts\\telegram_chat_id.py --test    send a test message to TELEGRAM_CHAT_ID
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app  # noqa: E402,F401
from app.core.config import get_settings  # noqa: E402
from app.services.telegram_service import TelegramService  # noqa: E402


def save_chat_id(chat_id: str) -> None:
    path = ROOT / "telegram.env"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if re.search(r"^TELEGRAM_CHAT_ID=.*$", text, flags=re.M):
        text = re.sub(r"^TELEGRAM_CHAT_ID=.*$", f"TELEGRAM_CHAT_ID={chat_id}", text, flags=re.M)
    else:
        text += f"\nTELEGRAM_CHAT_ID={chat_id}\n"
    path.write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="write the most recent chat ID into telegram.env")
    ap.add_argument("--test", action="store_true", help="send a test message to TELEGRAM_CHAT_ID")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    tg = TelegramService()

    if a.test:
        n = tg.send("✅ PStock: kết nối Telegram thành công. Bạn sẽ nhận tín hiệu BO/BD lúc 15:05 các ngày giao dịch.")
        print(f"Đã gửi tin thử tới {len(tg.chat_ids)} chat ({n} tin).")
        return 0

    chats: dict[str, str] = {}
    for u in tg.get_updates():
        m = u.get("message") or u.get("channel_post") or u.get("edited_message") or u.get("my_chat_member") or {}
        c = m.get("chat") or {}
        if "id" in c:
            name = c.get("title") or " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x) \
                or c.get("username") or ""
            chats[str(c["id"])] = f"{c.get('type')}: {name}"
    if not chats:
        print("Chưa có chat nào. Mở bot trên Telegram, nhắn /start rồi chạy lại lệnh này.")
        return 1
    print("Các chat đã nhắn cho bot:")
    for cid, desc in chats.items():
        print(f"  {cid:>16}  {desc}")
    print(f"TELEGRAM_CHAT_ID hiện tại: {get_settings().TELEGRAM_CHAT_ID or '(trống)'}")
    if a.save:
        cid = list(chats)[-1]
        save_chat_id(cid)
        print(f"Đã lưu TELEGRAM_CHAT_ID={cid} vào telegram.env")
    return 0


if __name__ == "__main__":
    sys.exit(main())
