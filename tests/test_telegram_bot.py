from app.analysis.stock_brief import build_ck_message, parse_command
from app.analysis.stock_scanner import analyze_dataframe
from app.services import telegram_bot as bot
from tests.conftest import make_ohlcv


def test_parse_command():
    assert parse_command("/ck fpt") == ("ck", ["FPT"])
    assert parse_command("/ck@PStockBot  hpg,") == ("ck", ["HPG"])
    assert parse_command("hello /ck FPT") is None


def test_reply_for_help_invalid_ok_and_error():
    assert "Cú pháp" in bot.reply_for("/ck")
    assert "Cú pháp" in bot.reply_for("/help")
    assert "Mã không hợp lệ: &lt;B&gt;" in bot.reply_for("/ck <b>")
    assert bot.reply_for("/ck fpt", ck=lambda s: f"OK {s}") == "OK FPT"
    assert bot.reply_for("/other x") is None and bot.reply_for("just text") is None

    def boom(s):
        raise RuntimeError("no data")
    assert "Không lấy được dữ liệu mã <b>ZZZ</b>" in bot.reply_for("/ck zzz", ck=boom)


class FakeTg:
    def __init__(self):
        self.sent = []

    def reply(self, chat, text, reply_to=None):
        self.sent.append((chat, text, reply_to))
        return 1


def test_handle_update_only_allowed_chats_and_fresh_messages():
    tg, now = FakeTg(), 1_000_000
    upd = lambda chat, date, text="/ck fpt": {"update_id": 1, "message": {   # noqa: E731
        "message_id": 7, "chat": {"id": chat}, "date": date, "text": text}}
    ck = lambda s: f"OK {s}"   # noqa: E731
    assert bot.handle_update(upd(-100, now), {"-100"}, now, tg, ck) is True
    assert tg.sent == [("-100", "OK FPT", 7)]
    assert bot.handle_update(upd(555, now), {"-100"}, now, tg, ck) is False          # not a configured chat
    assert bot.handle_update(upd(-100, now - 3600), {"-100"}, now, tg, ck) is False  # sent while the app was down
    assert bot.handle_update(upd(-100, now, "hi"), {"-100"}, now, tg, ck) is False
    assert len(tg.sent) == 1


def test_ck_message_has_the_main_sections():
    a = analyze_dataframe("FPT", make_ohlcv(), "1D")
    msg = build_ck_message(a, {"name": "FPT Corp", "price": 48500, "ref": 49400, "ceiling": 52800, "floor": 45950,
                               "open": 49400, "high": 49700, "low": 48500, "change_pct": -1.82, "volume": 10825500,
                               "value": 531.5e9, "foreign_buy": 1060618, "foreign_sell": 4049341, "state": "down"})
    for part in ("<b>FPT</b> · FPT Corp", "\nTC 49.40 • Trần 52.80 • Sàn 45.95\nMở 49.40 • Cao 49.70 • Thấp 48.50\n"
                 "KL 10,825,500 • GT 531.5 tỷ\nNN mua 1,060,618 • NN bán 4,049,341\n", f"Điểm {a['score']}",
                 "Ichimoku", "MACD", "RSI", "Kháng cự 20p", "không phải khuyến nghị"):
        assert part in msg, part
    assert msg.count("<b>") == msg.count("</b>") and msg.count("<i>") == msg.count("</i>")


def test_ck_message_is_valid_telegram_html():
    import re

    a = analyze_dataframe("FPT", make_ohlcv(), "1D")
    msg = build_ck_message(a)
    stripped = re.sub(r"</?(b|i|code|pre)>", "", msg)
    assert "<" not in stripped and ">" not in stripped     # any other < or > makes Telegram reject the message


def test_ck_message_starts_with_the_board_table():
    from app.analysis.board_alerts import HEADER, RULE

    a = analyze_dataframe("FPT", make_ohlcv(), "1D")
    q = {"name": "X", "price": 50600, "ref": 50900, "ceiling": 54400, "floor": 47350, "change_pct": -0.59,
         "volume": 1746400, "state": "down", "vol_ratio": 0.21}
    msg = build_ck_message(a, q)
    assert f"<pre>{RULE}\n{HEADER}\n{RULE}\nFPT   50.60   -0.59%   1,746.4    21%\n{RULE}</pre>" in msg
