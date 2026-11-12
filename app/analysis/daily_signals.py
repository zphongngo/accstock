"""End-of-day BO/BD scan of the watchlist and the Telegram message built from it.

Ichimoku signals (main):
    BO = phá mây lên:  close > cloud top on the latest bar, while the previous close was <= cloud top.
    BD = thủng mây:    close < cloud bottom on the latest bar, while the previous close was >= cloud bottom.
    Each signal lists the Ichimoku confirmations (Tenkan/Kijun, Chikou, future cloud, Kijun 129) and the
    volume ratio, so a weak break (e.g. low volume, Chikou against it) is visible at a glance.
Price/volume signals (the BO/BD markers on the chart):
    close beyond the 20-bar high/low with volume >= 1.5x MA20.
"""
from __future__ import annotations

import html
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from app.analysis.stock_scanner import analyze_dataframe
from app.services.timeframes import get_tf

log = logging.getLogger(__name__)
MAX_ERRORS_LISTED = 20


@dataclass
class SymbolSignal:
    symbol: str
    date: str
    close: float
    change_pct: float
    score: int
    rating: str
    vol_ratio: float | None
    cloud_bo: bool
    cloud_bd: bool
    vol_bo: bool
    vol_bd: bool
    checks: dict = field(default_factory=dict)  # key -> True (bullish) / False (bearish) / None (unknown)

    @property
    def any(self) -> bool:
        return self.cloud_bo or self.cloud_bd or self.vol_bo or self.vol_bd


def evaluate(symbol: str, df: pd.DataFrame, timeframe: str = "1D") -> SymbolSignal:
    a = analyze_dataframe(symbol, df, timeframe)
    i, b = a["ichimoku"], a["volume_breakout"]
    k129 = i["long_kijun"].get("129", {}).get("price_above")
    checks = {
        "tk": i["tenkan_above_kijun"],
        "chikou": {"above_price": True, "below_price": False}.get(i["chikou"]),
        "future": {"bullish": True, "bearish": False}.get(i["future_cloud"]),
        "k129": k129,
    }
    return SymbolSignal(
        symbol=a["symbol"], date=a["date"], close=a["close"], change_pct=a["change_pct"], score=a["score"],
        rating=a["rating"], vol_ratio=b["vol_ratio"], cloud_bo=i["cloud_breakout"], cloud_bd=i["cloud_breakdown"],
        vol_bo=b["breakout_today"], vol_bd=b["breakdown_today"], checks=checks,
    )


def board_symbols(board, watch: list[str] | None = None) -> list[str]:
    """Every symbol of the Bảng điện: VN30 + every sector table + the Quan Tâm list, each symbol once."""
    return board.universe([w.strip().upper() for w in (watch or []) if w.strip()])[0]


def scan(stock, symbols: list[str], timeframe: str = "1D", until: date | None = None,
         workers: int = 4, prepare=None) -> tuple[list[SymbolSignal], dict[str, str]]:
    """Fetch a fresh bar for every symbol (in parallel) and evaluate it. `until` drops bars after that day,
    `prepare(df) -> df` is applied before evaluating (e.g. drop the bar in progress).
    Returns (results in `symbols` order, {symbol: error})."""
    def one(sym: str):
        try:
            df, _ = stock.get_ohlcv(sym, refresh=True, timeframe=timeframe)
            if until is not None and not df.empty:
                df = df[pd.to_datetime(df["date"]).dt.date <= until].reset_index(drop=True)
            if prepare is not None and not df.empty:
                df = prepare(df)
            if df.empty:
                raise ValueError("không có dữ liệu" + (f" đến {until:%d/%m/%Y}" if until else ""))
            return sym, evaluate(sym, df, timeframe), None
        except Exception as e:  # noqa: BLE001
            log.warning("%-5s ERROR %s", sym, e)
            return sym, None, str(e)

    results, errors = [], {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for sym, r, err in ex.map(one, list(dict.fromkeys(symbols))):
            if r is not None:
                results.append(r)
            else:
                errors[sym] = err
    return results, errors


# Confirmation labels per direction: ✅ = the condition holds (confirms the signal), ❌ = it does not.
LABELS = {
    True: {"tk": "Tenkan>Kijun", "chikou": "Chikou trên giá", "future": "Mây TL tăng", "k129": "Trên Kijun 129"},
    False: {"tk": "Tenkan<Kijun", "chikou": "Chikou dưới giá", "future": "Mây TL giảm", "k129": "Dưới Kijun 129"},
}


def _px(v: float) -> str:
    return f"{v / 1000:,.2f}"


def _line(s: SymbolSignal, bullish: bool) -> str:
    chg = f"{s.change_pct:+.2f}%"
    vol = f"KL {s.vol_ratio:.2f}x MA20" if s.vol_ratio is not None else "KL –"
    if s.vol_ratio is not None and s.vol_ratio >= 1.5:
        vol = f"<b>{vol}</b>"
    marks = []
    for key, v in s.checks.items():
        if v is None:
            continue
        marks.append(f"{html.escape(LABELS[bullish][key])} {'✅' if v == bullish else '❌'}")
    return (f"• <b>{s.symbol}</b> {_px(s.close)} ({chg}) · Điểm {s.score} {s.rating}\n"
            f"   {vol} · " + " · ".join(marks))


def build_message(results: list[SymbolSignal], errors: dict[str, str], trade_date: str,
                  timeframe: str = "1D", scanned: int | None = None) -> str:
    tf = get_tf(timeframe)
    # both sections: highest score first
    bo = sorted([r for r in results if r.cloud_bo], key=lambda r: (-r.score, r.symbol))
    bd = sorted([r for r in results if r.cloud_bd], key=lambda r: (-r.score, r.symbol))
    vol = [r for r in results if r.vol_bo or r.vol_bd]
    scanned = scanned if scanned is not None else len(results) + len(errors)

    out = [f"📊 <b>PStock · Tín hiệu Ichimoku</b> · {trade_date} (khung {tf.label})",
           f"Quét {scanned} mã · 🟢 {len(bo)} BO · 🔴 {len(bd)} BD · giá: nghìn đồng"]
    if bo:
        out += ["", "🟢 <b>BREAK OUT – giá vượt lên trên mây</b>"] + [_line(r, True) for r in bo]
    if bd:
        out += ["", "🔴 <b>BREAK DOWN – giá thủng xuống dưới mây</b>"] + [_line(r, False) for r in bd]
    if not bo and not bd:
        out += ["", "Không có mã nào phá mây hoặc thủng mây Ichimoku trong phiên."]
    if vol:
        out += ["", "📈 <b>BO/BD giá–khối lượng</b> (vượt đỉnh/thủng đáy 20 phiên, KL ≥ 1.5× MA20)"]
        for r in sorted(vol, key=lambda r: (-r.score, r.symbol)):
            tag = "🟢 BO" if r.vol_bo else "🔴 BD"
            out.append(f"• {tag} <b>{r.symbol}</b> {_px(r.close)} ({r.change_pct:+.2f}%) · KL {r.vol_ratio:.2f}x")
    if errors:
        items = list(errors.items())
        line = "⚠️ Không tải được: " + ", ".join(
            f"{html.escape(sym)} ({html.escape(err[:80])})" for sym, err in items[:MAX_ERRORS_LISTED])
        if len(items) > MAX_ERRORS_LISTED:
            line += f" … và {len(items) - MAX_ERRORS_LISTED} mã khác"
        out += ["", line]
    out += ["", "<i>Phân tích kỹ thuật tham khảo, không phải khuyến nghị đầu tư.</i>"]
    return "\n".join(out)
