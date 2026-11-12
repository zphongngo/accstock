"""Strong / weak stocks on the price board, as a Telegram message (sent from the Bảng điện tab).

Strong = % change vs today's reference price >= +threshold, sorted high -> low.
Weak   = % change <= -threshold, sorted low -> high. Symbols that haven't traded yet are ignored.

Each section is a fixed-width table inside <pre> (monospace in Telegram):

    1. Co Phieu Manh (>=3%):
    Ma      Gia     +/-%    KL (K)  %V20p
    -------------------------------------
    AMS    8.00  +14.29%     204.6  2494%

Gia = thousand VND, KL (K) = today's volume in thousands, %V20p = today's volume / average volume of the
previous 20 sessions.

Telegram bots cannot colour text (the HTML mode has no colour tag), so each row ends with a coloured dot, the
board's colours: 🩷 > +6.5% · 🟢 up · 🟡 reference · 🔴 down · 🔵 < -6.5% · ⚪ not traded yet. The dot is at the end
so the fixed-width columns stay aligned. Section 3 = the "Quan Tâm" list, every symbol, sorted A-Z.
"""
from __future__ import annotations

import html
from datetime import datetime

from app.services.telegram_service import MAX_LEN

HEADER = f"{'Ma':<5}{'Gia':>6}{'+/-%':>9}{'KL (K)':>10}{'%V20p':>7}"
RULE = "-" * len(HEADER)
BIG_MOVE = 6.5   # % change shown purple (up) / blue (down)
LEGEND = "🩷 > +6.5% · 🟢 tăng · 🟡 tham chiếu · 🔴 giảm · 🔵 < −6.5%"


MARKS = {"big_up": "🩷", "up": "🟢", "ref": "🟡", "down": "🔴", "big_down": "🔵", "none": "⚪"}


def kind(q: dict) -> str:
    """big_up (> +6.5%) / up / ref / down / big_down (< -6.5%) / none (not traded): the row's colour."""
    if q.get("state") == "none":
        return "none"
    pct = q["change_pct"]
    if pct > BIG_MOVE:
        return "big_up"
    if pct < -BIG_MOVE:
        return "big_down"
    return "up" if pct > 0 else "down" if pct < 0 else "ref"


def mark(q: dict) -> str:
    """Coloured marker for a text row (Telegram has no text colour)."""
    return MARKS[kind(q)]


def movers(quotes: dict[str, dict], threshold: float) -> tuple[list[dict], list[dict]]:
    traded = [q for q in quotes.values() if q.get("state") != "none"]
    strong = sorted((q for q in traded if q["change_pct"] >= threshold), key=lambda q: (-q["change_pct"], q["symbol"]))
    weak = sorted((q for q in traded if q["change_pct"] <= -threshold), key=lambda q: (q["change_pct"], q["symbol"]))
    return strong, weak


def row(q: dict) -> str:
    ratio = q.get("vol_ratio")
    ratio_s = f"{ratio * 100:.0f}%" if ratio is not None else "-"
    return (f"{q['symbol']:<5}{q['price'] / 1000:>6.2f}{q['change_pct']:>+8.2f}%"
            f"{q['volume'] / 1000:>10,.1f}{ratio_s:>7}")


def marked_row(q: dict) -> str:
    return f"{row(q)} {mark(q)}"


def _fmt_threshold(threshold: float) -> str:
    return f"{threshold:g}"


def section(title: str, quotes: list[dict], limit: int = MAX_LEN) -> list[str]:
    """One table as one or more self-contained <pre> blocks (a block never spans two Telegram messages)."""
    head = f"<b>{html.escape(title)}</b>"
    if not quotes:
        return [f"{head}\nKhông có mã nào."]
    blocks, rows = [], [marked_row(q) for q in quotes]
    while rows:
        lines, size = [], len(head) + len(HEADER) + len(RULE) + 20
        while rows and size + len(rows[0]) + 2 <= limit:   # +1: the dot is 2 UTF-16 units for Telegram's limit
            size += len(rows[0]) + 2
            lines.append(rows.pop(0))
        blocks.append(f"{head}\n<pre>" + html.escape("\n".join([HEADER, RULE, *lines])) + "</pre>")
        head = f"<b>{html.escape(title)} (tiếp)</b>"
    return blocks


def build_movers_message(quotes: dict[str, dict], threshold: float, now: datetime | None = None,
                         limit: int = MAX_LEN, watch: list[str] | None = None) -> tuple[list[str], int, int]:
    """Return (Telegram messages, number of strong, number of weak). `watch` = the Quan Tâm list (section 3)."""
    now = now or datetime.now()
    strong, weak = movers(quotes, threshold)
    t = _fmt_threshold(threshold)
    summary_line = f"<i>hiện có {len(strong)} mã mạnh · {len(weak)} mã yếu</i>"
    blocks = [f"📋 <b>PStock · Bảng điện</b> · {now:%H:%M %d/%m/%Y}\n<i>{html.escape(LEGEND)}</i>\n{summary_line}"]
    blocks += section(f"1. Co Phieu Manh (>={t}%):", strong, limit)
    blocks += section(f"2. Co Phieu Yeu (<=-{t}%):", weak, limit)
    if watch is not None:
        mine = [quotes[s] for s in sorted(set(watch)) if s in quotes]
        blocks += section("3. Quan Tam (A-Z):", mine, limit)
    messages, cur = [], ""
    for b in blocks:
        if cur and len(cur) + 2 + len(b) > limit:
            messages.append(cur)
            cur = b
        else:
            cur = f"{cur}\n\n{b}" if cur else b
    messages.append(cur)
    return messages, len(strong), len(weak)
