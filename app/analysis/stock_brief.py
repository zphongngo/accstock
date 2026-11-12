"""Reply of the Telegram command /ck <symbol>: one stock's technical summary (daily timeframe), as Telegram HTML."""
from __future__ import annotations

import html
import re

SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,10}$")
HELP = ("Cú pháp: <code>/ck MãCK</code>, ví dụ <code>/ck FPT</code>\n"
        "Trả về giá, Điểm kỹ thuật khung Ngày, Ichimoku, MACD, RSI, khối lượng và tín hiệu BO/BD.")


def parse_command(text: str) -> tuple[str, list[str]] | None:
    """'/ck@MyBot fpt' -> ('ck', ['FPT']). None when the text is not a command."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return None
    head, *args = text.split()
    cmd = head[1:].split("@", 1)[0].lower()
    return cmd, [a.strip(",;").upper() for a in args if a.strip(",;")]


def _px(v) -> str:
    return f"{float(v) / 1000:,.2f}"


def _pct(v) -> str:
    return f"{float(v):+.2f}%"


def _dmy(iso: str) -> str:
    return "/".join(reversed(iso[:10].split("-"))) if iso else "-"


def table(a: dict, quote: dict | None = None) -> str:
    """The one-row board table (same columns as the Bảng điện bulletin), as a <pre> block.
    %V20p = `quote["vol_ratio"]` (today's volume / average of the previous 20 sessions) when given."""
    from app.analysis.board_alerts import HEADER, RULE, row

    if quote and quote.get("ref"):
        q = {"symbol": a["symbol"], "price": quote["price"], "change_pct": quote["change_pct"],
             "volume": quote.get("volume") or 0, "vol_ratio": quote.get("vol_ratio")}
    else:
        q = {"symbol": a["symbol"], "price": a["close"], "change_pct": a["change_pct"], "volume": a["volume"] or 0,
             "vol_ratio": a["volume_breakout"].get("vol_ratio")}
    return "<pre>" + html.escape("\n".join([RULE, HEADER, RULE, row(q), RULE])) + "</pre>"


def build_ck_message(a: dict, quote: dict | None = None) -> str:
    """`a` = analyze_dataframe(..., '1D'); `quote` = the live board quote (price vs today's reference), if any."""
    sym, i, m, r, b = a["symbol"], a["ichimoku"], a["macd"], a["rsi"], a["volume_breakout"]
    name = f" · {html.escape(quote['name'])}" if quote and quote.get("name") else ""
    out = [f"📈 <b>{sym}</b>{name}", table(a, quote), ""]
    if quote and quote.get("ref"):
        traded = quote.get("state") != "none"
        price = _px(quote["price"])
        chg = f"{_pct(quote['change_pct'])}" if traded else "chưa khớp"
        out.append(f"Giá {price} ({chg}) · KL {quote.get('volume', 0):,} · TC {_px(quote['ref'])} · "
                   f"Trần {_px(quote['ceiling'])} · Sàn {_px(quote['floor'])}")
    else:
        out.append(f"Giá {_px(a['close'])} ({_pct(a['change_pct'])}) · KL {a['volume'] or 0:,} · phiên {_dmy(a['date'])}")
    out.append(f"<b>Điểm {a['score']}</b> · {a['rating']} (khung Ngày, phiên {_dmy(a['date'])})")

    pos = {"above_cloud": "trên mây", "below_cloud": "dưới mây", "inside_cloud": "trong mây"}.get(
        i["price_vs_cloud"], i["price_vs_cloud"])
    pr = i["params"]
    tk = "&gt;" if i["tenkan_above_kijun"] else "&lt;"
    ich = [f"Giá {pos} ({_px(i['cloud_bottom'])}–{_px(i['cloud_top'])})",
           f"Tenkan {_px(i['tenkan'])} {tk} Kijun {_px(i['kijun'])}"]
    if i["tk_cross"] in ("bullish", "bearish"):
        ich.append("Tenkan cắt lên Kijun" if i["tk_cross"] == "bullish" else "Tenkan cắt xuống Kijun")
    ich.append({"above_price": "Chikou trên giá", "below_price": "Chikou dưới giá"}.get(i["chikou"], "Chikou –"))
    ich.append({"bullish": "Mây TL tăng", "bearish": "Mây TL giảm"}.get(i["future_cloud"], "Mây TL –"))
    for n, lk in i["long_kijun"].items():
        if lk.get("value"):
            ich.append(f"Kijun {n}: {_px(lk['value'])} ({_pct(lk['distance_pct'])})")
    out += ["", f"☁️ <b>Ichimoku</b> {pr['tenkan']}-{pr['kijun']}-{pr['senkou_b']}: " + " · ".join(ich)]
    if i["cloud_breakout"]:
        out.append("🟢 <b>BO: giá vượt lên trên mây trong phiên này</b>")
    if i["cloud_breakdown"]:
        out.append("🔴 <b>BD: giá thủng xuống dưới mây trong phiên này</b>")
    cyc = []
    for key, lbl in (("from_high", "đỉnh"), ("from_low", "đáy")):
        c = (i.get("time_cycles") or {}).get(key) or {}
        if c.get("pivot_date") and c.get("next_cycle"):
            cyc.append(f"từ {lbl} {_dmy(c['pivot_date'])} ({c['bars_since']} phiên) → mốc {c['next_cycle']} "
                       f"ngày {_dmy(c.get('next_cycle_date') or '')}")
    if cyc:
        out.append("⏳ Chu kỳ: " + "; ".join(cyc))

    cross = {"bullish": " · vừa cắt lên", "bearish": " · vừa cắt xuống"}.get(m["cross"], "")
    out += ["", f"📊 MACD {m['macd']:,.0f} / Signal {m['signal']:,.0f} (hist {m['histogram']:,.0f}, "
                f"{'tăng' if m['histogram_rising'] else 'giảm'}){cross} · RSI {r['rsi']:.1f} "
                f"({'tăng' if r['rising'] else 'giảm'})"]
    vol = f"KL {b['vol_ratio']:.2f}x MA20" if b.get("vol_ratio") is not None else "KL –"
    line = f"📦 {vol} · Kháng cự 20p {_px(b['resistance_20'])}"
    if b.get("distance_to_resistance_pct") is not None:
        line += f" (+{b['distance_to_resistance_pct']:.2f}%)"
    out.append(line + f" · Hỗ trợ 20p {_px(b['support_20'])}")
    if b.get("breakout_today"):
        out.append("🟢 Vượt đỉnh 20 phiên với KL ≥ 1.5× MA20")
    if b.get("breakdown_today"):
        out.append("🔴 Thủng đáy 20 phiên với KL ≥ 1.5× MA20")
    if a.get("score_notes"):
        out += ["", "<i>" + html.escape(" · ".join(a["score_notes"])) + "</i>"]
    out += ["", "<i>Phân tích kỹ thuật tham khảo, không phải khuyến nghị đầu tư.</i>"]
    return "\n".join(out)
