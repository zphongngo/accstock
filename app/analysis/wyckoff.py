"""Wyckoff (đếm ngang / Point & Figure) kết hợp bản đồ Ichimoku -> "vùng hợp lưu".

Wyckoff trả lời "giá đi được bao xa" (mục tiêu định lượng), Ichimoku trả lời "trên đường đi có
chướng ngại / vùng cân bằng nào" (Kijun, biên mây Kumo, điểm xoắn mây ở khung Ngày và Tuần).

Quy ước (mọi giá là VND, thời gian là giờ VN, không dùng dữ liệu tương lai):
  1. Vùng tích lũy / phân phối (TR) tự dò: đoạn đi ngang dài nhất kết thúc ngay trước nến phá vỡ gần nhất
     (hoặc kết thúc ở nến mới nhất nếu giá vẫn nằm trong TR). Có thể chọn tay ngày bắt đầu / kết thúc.
  2. Đếm ngang = số cột P&F (box, đảo chiều 3 box) trong TR; Count = số cột x box x 3.
  3. Mục tiêu (hướng lên): T1 = đáy TR + Count (thận trọng), T2 = giữa TR + Count, T3 = đỉnh TR + Count
     (toàn bộ nguyên nhân). Hướng xuống đối xứng: đỉnh / giữa / đáy TR - Count.
  4. Hợp lưu: mục tiêu nằm trong +-tol% của một mốc Ichimoku (Kijun, Kijun 65/129, biên mây hiện tại và +26,
     điểm xoắn mây) ở khung Ngày hoặc Tuần. Mốc Tuần có trọng số gấp đôi, Kijun phẳng cộng thêm điểm.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.analysis.ichimoku import IchiParams, compute_ichimoku, future_cloud, get_params
from app.analysis.stock_scanner import chart_payload
from app.services.timeframes import chart_time, fmt_time, get_tf, resample

REVERSAL = 3
HOLD_BARS = 15  # sau phá vỡ, giá không được đóng cửa quay lại quá nửa TR trong ngần này nến


@dataclass(frozen=True)
class TRConfig:
    min_bars: int       # TR ngắn hơn số nến này không được coi là vùng tích lũy
    max_width: float    # (đỉnh - đáy) / đáy tối đa của TR
    recent: int         # chỉ tìm nến phá vỡ trong số nến gần nhất này
    prior: int          # số nến trước TR để xác định xu hướng trước đó
    flat_min: int       # Kijun đứng yên từng này nến thì coi là "phẳng"
    thick_pct: float    # mây dày hơn x% giá được coi là chướng ngại mạnh


CONFIG = {
    "1D": TRConfig(min_bars=20, max_width=0.30, recent=60, prior=60, flat_min=8, thick_pct=6.0),
    "1W": TRConfig(min_bars=10, max_width=0.40, recent=26, prior=26, flat_min=4, thick_pct=10.0),
}


def _k(v: float) -> str:
    return f"{v / 1000:.2f}"  # nghìn đồng, giống cách hiển thị trên dashboard


# ---------------------------------------------------------------- P&F
def tick_size(price: float) -> int:
    return 10 if price < 10_000 else 50 if price < 50_000 else 100


def auto_box(price: float) -> float:
    """~1% giá, làm tròn về bội số 'đẹp' (1 / 2 / 2.5 / 5 x 10^k) và bội của bước giá."""
    tick = tick_size(price)
    raw = max(price * 0.01, tick)
    mag = 10 ** math.floor(math.log10(raw))
    step = min((mag * m for m in (1, 2, 2.5, 5, 10)), key=lambda s: abs(math.log(s / raw)))
    return float(max(tick, round(step / tick) * tick))


def pnf_columns(high, low, open_, close, box: float, reversal: int = REVERSAL) -> list[dict]:
    """Cột Point & Figure kiểu high/low. Mỗi cột: dir (+1 X / -1 O), hi, lo (chỉ số box), i (nến bắt đầu)."""
    cols: list[dict] = []
    for i in range(len(high)):
        hi = math.floor(high[i] / box + 1e-9)
        lo = min(hi, math.ceil(low[i] / box - 1e-9))
        up_bar = close[i] >= open_[i]
        if not cols:
            cols.append({"dir": 1 if up_bar else -1, "hi": hi, "lo": lo, "i": i})
            continue
        c = cols[-1]
        if c["dir"] == 1:
            ext, rev = hi > c["hi"], lo <= c["hi"] - reversal
            if ext and rev:
                ext, rev = up_bar, not up_bar
            if ext:
                c["hi"] = hi
            elif rev:
                cols.append({"dir": -1, "hi": c["hi"] - 1, "lo": lo, "i": i})
        else:
            ext, rev = lo < c["lo"], hi >= c["lo"] + reversal
            if ext and rev:
                ext, rev = not up_bar, up_bar
            if ext:
                c["lo"] = lo
            elif rev:
                cols.append({"dir": 1, "hi": hi, "lo": c["lo"] + 1, "i": i})
    return cols


# ---------------------------------------------------------------- trading range
def _tr_ok(h, l, c, s: int, e: int, cfg: TRConfig) -> bool:
    """TR thật phải dao động: đủ dài, đóng cửa cắt đường giữa nhiều lần, lấp cả vùng trên lẫn vùng dưới."""
    if e - s + 1 < cfg.min_bars:
        return False
    top, bot = h[s:e + 1].max(), l[s:e + 1].min()
    rng = top - bot
    if rng <= 0 or rng / bot > cfg.max_width:
        return False
    cl = c[s:e + 1]
    sign = np.sign(cl - (top + bot) / 2)
    sign = sign[sign != 0]
    if int((sign[1:] != sign[:-1]).sum()) < 2:
        return False
    upper = float((cl >= bot + rng * 2 / 3).mean())
    lower = float((cl <= bot + rng / 3).mean())
    return upper >= 0.10 and lower >= 0.10 and abs(cl[-1] - cl[0]) <= 0.7 * rng


def _window_before(h, l, c, end: int, cfg: TRConfig) -> tuple[int, int] | None:
    """Đoạn dài nhất kết thúc tại `end` có biên độ <= max_width, bỏ các nến đầu nằm ngoài vùng đóng cửa chính."""
    top, bot, s = h[end], l[end], end
    while s > 0:
        nt, nb = max(top, h[s - 1]), min(bot, l[s - 1])
        if (nt - nb) / nb > cfg.max_width:
            break
        top, bot, s = nt, nb, s - 1
    if end - s + 1 < cfg.min_bars:
        return None
    # Bỏ các nến đầu nằm ngoài dải giá của 60% nến cuối (nhịp giảm / tăng trước TR còn lọt vào cửa sổ)
    core = c[s + int((end - s + 1) * 0.4):end + 1]
    inside = np.nonzero((c[s:end + 1] >= core.min()) & (c[s:end + 1] <= core.max()))[0]
    s += int(inside[0]) if len(inside) else 0
    return (s, end) if _tr_ok(h, l, c, s, end, cfg) else None


def find_tr(df: pd.DataFrame, cfg: TRConfig) -> dict | None:
    """Phá vỡ đầu tiên (đã được giữ vững) trong `recent` nến gần nhất của một TR hợp lệ; nếu không có thì TR đang diễn ra."""
    h, l, c = (df[k].to_numpy(float) for k in ("high", "low", "close"))
    n = len(df)
    for e in range(max(cfg.min_bars, n - cfg.recent), n):
        w = _window_before(h, l, c, e - 1, cfg)
        if not w:
            continue
        top, bot = h[w[0]:w[1] + 1].max(), l[w[0]:w[1] + 1].min()
        mid, after = (top + bot) / 2, c[e + 1:e + 1 + HOLD_BARS]
        # nhịp "phá vỡ" bên trong TR rộng hơn (giá quay về giữa TR ngay sau đó) không phải SOS/SOW thật
        if c[e] > top and not (after < mid).any():
            return {"s": w[0], "e": w[1], "break_i": e}
        if c[e] < bot and not (after > mid).any():
            return {"s": w[0], "e": w[1], "break_i": e}
    w = _window_before(h, l, c, n - 1, cfg)
    if w:
        top, bot = h[w[0]:w[1] + 1].max(), l[w[0]:w[1] + 1].min()
        if bot <= c[-1] <= top:
            return {"s": w[0], "e": w[1], "break_i": None}
    return None


def manual_tr(df: pd.DataFrame, start: str | None, end: str | None) -> dict:
    d = df["date"]
    s = int(np.argmax((d >= pd.Timestamp(start)).to_numpy())) if start else 0
    if start and d.iloc[-1] < pd.Timestamp(start):
        raise ValueError("Ngày bắt đầu TR nằm sau nến cuối cùng")
    e_idx = np.nonzero((d <= pd.Timestamp(end)).to_numpy())[0] if end else np.array([len(df) - 1])
    if not len(e_idx):
        raise ValueError("Ngày kết thúc TR nằm trước nến đầu tiên")
    e = int(e_idx[-1])
    if e - s + 1 < 5:
        raise ValueError("TR chọn tay cần tối thiểu 5 nến")
    top, bot = df["high"].iloc[s:e + 1].max(), df["low"].iloc[s:e + 1].min()
    later = df["close"].iloc[e + 1:]
    out = later[(later > top) | (later < bot)]
    return {"s": s, "e": e, "break_i": int(out.index[0]) if len(out) else None}


# ---------------------------------------------------------------- Wyckoff structure
def _episodes(mask: np.ndarray) -> int:
    return int(((mask[1:] & ~mask[:-1]).sum()) + (1 if len(mask) and mask[0] else 0))


def _atr(df: pd.DataFrame, n: int = 14) -> float:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    v = tr.tail(n).mean()
    return float(v) if pd.notna(v) else 0.0


def _events(df: pd.DataFrame, tr: dict, tf) -> list[dict]:
    h, l, c, v = (df[k].to_numpy(float) for k in ("high", "low", "close", "volume"))
    s, e, bi = tr["s"], tr["e"], tr["break_i"]
    avg = pd.Series(v).rolling(20, min_periods=5).mean().shift(1).to_numpy()
    out: list[dict] = []

    def add(kind, i, price, note):
        ratio = float(v[i] / avg[i]) if avg[i] and not math.isnan(avg[i]) else None
        out.append({"type": kind, "date": fmt_time(df["date"].iloc[i], tf), "price": float(price),
                    "vol_ratio": round(ratio, 2) if ratio else None, "note": note})

    for i in range(s + max(3, (e - s) // 3), e + 1):  # chỉ xét 2/3 sau của TR
        prev_lo, prev_hi = l[s:i].min(), h[s:i].max()
        if l[i] < prev_lo and c[i] > prev_lo:
            add("SPRING", i, l[i], "Thủng đáy TR rồi đóng cửa quay lại (rũ bỏ)")
        if h[i] > prev_hi and c[i] < prev_hi:
            add("UT", i, h[i], "Vượt đỉnh TR rồi đóng cửa quay lại (upthrust)")
    if bi is not None:
        up = c[bi] > h[s:e + 1].max()
        add("SOS" if up else "SOW", bi, c[bi], "Phá vỡ đỉnh TR" if up else "Phá vỡ đáy TR")
        level = h[s:e + 1].max() if up else l[s:e + 1].min()
        for i in range(bi + 1, len(df)):
            pulled = (l[i] <= level * 1.03 and c[i] >= level) if up else (h[i] >= level * 0.97 and c[i] <= level)
            if pulled:
                add("LPS" if up else "LPSY", i, l[i] if up else h[i], "Nhịp lùi về vùng phá vỡ, giữ được")
                break
    return out


# ---------------------------------------------------------------- Ichimoku map
def _flat_bars(s: pd.Series) -> int:
    s = s.dropna()
    if s.empty:
        return 0
    last, k = s.iloc[-1], 0
    for x in s.iloc[::-1]:
        if abs(x - last) > 1e-6:
            break
        k += 1
    return k


def ichimoku_levels(df: pd.DataFrame, tf_key: str, p: IchiParams) -> list[dict]:
    """Các mốc Ichimoku có thể là nam châm / kháng cự / hỗ trợ của giá ở một khung."""
    tf, cfg = get_tf(tf_key), CONFIG[tf_key]
    e = compute_ichimoku(df, p)
    close = float(e["close"].iloc[-1])
    base_w = 2.0 if tf_key == "1W" else 1.0
    lab = "Tuần" if tf_key == "1W" else "Ngày"
    out: list[dict] = []

    def add(kind, name, price, weight, **extra):
        if price is None or pd.isna(price):
            return
        out.append({"tf": tf_key, "kind": kind, "name": f"{name} ({lab})", "price": float(price),
                    "weight": weight, **extra})

    for col, label, kind in [("kijun", f"Kijun {p.kijun}", "kijun")] + \
                            [(f"kijun_{n}", f"Kijun {n}", "long_kijun") for n in p.long_kijun]:
        fb = _flat_bars(e[col])
        flat = fb >= cfg.flat_min
        w = base_w + (0.5 if kind == "long_kijun" else 0) + (1.0 if flat else 0)
        add(kind, label + (" phẳng" if flat else ""), e[col].iloc[-1], w, flat=flat, flat_bars=fb)

    def cloud(prefix, top, bot):
        if pd.isna(top) or pd.isna(bot):
            return
        thick = float((top - bot) / close * 100)
        w = base_w + (0.5 if thick >= cfg.thick_pct else 0)
        extra = {"thickness_pct": round(thick, 2), "thick": thick >= cfg.thick_pct}
        add("kumo", f"{prefix} trên", top, w, **extra)
        add("kumo", f"{prefix} dưới", bot, w, **extra)

    cloud("Mây", e["cloud_top"].iloc[-1], e["cloud_bottom"].iloc[-1])
    fc = future_cloud(e, p, tf)
    if fc:
        far = fc[-1]
        cloud(f"Mây +{p.displacement}", max(far["senkou_a"], far["senkou_b"]), min(far["senkou_a"], far["senkou_b"]))
        diff = [x["senkou_a"] - x["senkou_b"] for x in fc]
        for i in range(1, len(diff)):
            if diff[i - 1] * diff[i] < 0:  # span A cắt span B: xoắn mây
                a, b = fc[i], fc[i - 1]
                price = (a["senkou_a"] + a["senkou_b"] + b["senkou_a"] + b["senkou_b"]) / 4
                add("twist", "Xoắn mây", price, base_w + 0.5, bars_ahead=p.displacement - len(fc) + i + 1,
                    date=fmt_time(a["date"], tf))
    return out


# ---------------------------------------------------------------- confluence
def _rating(score: float) -> str:
    return "Mạnh" if score >= 4 else "Có" if score >= 2 else "Yếu" if score > 0 else "Không"


def _is_golden(lv: dict) -> bool:
    return lv["tf"] == "1W" and lv["kind"] in ("kijun", "long_kijun") and lv.get("flat", False)


def _is_strong_obstacle(lv: dict) -> bool:
    return _is_golden(lv) or lv.get("thick", False) or lv["kind"] == "twist" or (lv["tf"] == "1W" and lv["kind"] == "long_kijun")


def confluence(targets: list[dict], levels: list[dict], close: float, sgn: int, tol: float) -> dict:
    tol_f = tol / 100
    zones = []
    for t in targets:
        m = [dict(lv, diff_pct=round((lv["price"] / t["price"] - 1) * 100, 2)) for lv in levels
             if abs(lv["price"] / t["price"] - 1) <= tol_f]
        m.sort(key=lambda x: abs(x["diff_pct"]))
        score = round(sum(x["weight"] for x in m), 1)
        prices = [t["price"]] + [x["price"] for x in m]
        zones.append({"target": t["key"], "price": t["price"], "score": score, "rating": _rating(score),
                      "golden": any(_is_golden(x) for x in m), "zone": [min(prices), max(prices)], "matches": m})

    far = targets[-1]["price"]  # T3 luôn là mục tiêu xa nhất
    obstacles = []
    for lv in sorted(levels, key=lambda x: (x["price"] - close) * sgn):
        if (lv["price"] - close) * sgn <= 0 or (lv["price"] - far) * sgn > far * tol_f:
            continue
        near = next((t["key"] for t in targets if abs(lv["price"] / t["price"] - 1) <= tol_f), None)
        obstacles.append(dict(lv, strong=_is_strong_obstacle(lv), near_target=near,
                              dist_pct=round((lv["price"] / close - 1) * 100, 2)))

    scenarios = []
    blocker = next((o for o in obstacles if o["strong"] and o["near_target"] is None
                    and any((t["price"] - o["price"]) * sgn > o["price"] * tol_f for t in targets)), None)
    if blocker:
        beyond = next(t for t in targets if (t["price"] - blocker["price"]) * sgn > blocker["price"] * tol_f)
        scenarios.append({
            "code": "A", "title": "Bị chặn trước mục tiêu",
            "price": blocker["price"],
            "text": f"{beyond['key']} ({_k(beyond['price'])}) nằm sau {blocker['name']} tại {_k(blocker['price'])} "
                    f"({blocker['dist_pct']:+.1f}%). Đà {'tăng' if sgn > 0 else 'giảm'} dễ khựng quanh vùng này.",
            "action": f"Chốt một phần tại vùng {_k(blocker['price'])} thay vì chờ đủ {_k(beyond['price'])}."})
    gold = next((z for z in zones if z["golden"]), None)
    if gold:
        scenarios.append({
            "code": "B", "title": "Hợp lưu vàng",
            "price": gold["price"],
            "text": f"{gold['target']} ({_k(gold['price'])}) trùng Kijun Tuần phẳng — mức cân bằng dài hạn của dòng tiền lớn.",
            "action": "Nắm giữ tới khi chạm mốc, rồi quan sát phản ứng khối lượng (KL lớn mà giá không đi tiếp = chốt)."})
    for z in zones:
        if z["score"] >= 2 and not z["golden"]:
            names = ", ".join(x["name"] for x in z["matches"][:3])
            scenarios.append({"code": "C", "title": "Hợp lưu", "price": z["price"],
                              "text": f"{z['target']} ({_k(z['price'])}) trùng {names}.",
                              "action": f"Vùng chốt lời đáng tin: {_k(z['zone'][0])} – {_k(z['zone'][1])}."})
    if not scenarios:
        free = not any(o["strong"] for o in obstacles)
        scenarios.append({"code": "D", "title": "Đường thoáng" if free else "Không hợp lưu", "price": None,
                          "text": "Không có chướng ngại Ichimoku mạnh giữa giá và mục tiêu." if free
                                  else "Mục tiêu Wyckoff không trùng mốc Ichimoku nào; có chướng ngại nhỏ trên đường đi.",
                          "action": "Dùng mục tiêu Wyckoff làm tham chiếu, trailing stop theo Kijun Ngày."})
    return {"zones": zones, "obstacles": obstacles, "scenarios": scenarios}


# ---------------------------------------------------------------- main
MAX_CHART_BARS = 8000   # ~32 years of sessions


def _chart(base: pd.DataFrame, tf_key: str, tr: dict | None) -> dict:
    tf = get_tf(tf_key)
    need = (len(base) - tr["s"] + 80) if tr else 0
    # the whole history is sent (imported EOD data can go back many years: scroll / zoom out to see it);
    # `view_bars` = the initial zoom on the recent bars and the TR
    ch = chart_payload(base, bars=MAX_CHART_BARS, timeframe=tf_key)
    ch["view_bars"] = min(max(320 if tf_key == "1D" else 200, need), len(ch["candles"]))
    for k in ("macd", "macd_signal", "macd_hist", "rsi", "score", "chikou", "tenkan", "markers"):
        ch.pop(k, None)
    if tr:
        ch["tr_box"] = {"from": chart_time(base["date"].iloc[tr["s"]], tf), "to": chart_time(base["date"].iloc[tr["e"]], tf),
                        "top": tr["top"], "bottom": tr["bottom"]}
    return ch


def analyze_wyckoff(symbol: str, daily: pd.DataFrame, timeframe: str = "1D", start: str | None = None,
                    end: str | None = None, box: float | None = None, reversal: int = REVERSAL,
                    tol_pct: float = 3.0, direction: str = "auto") -> dict:
    tf_key = get_tf(timeframe).key
    if tf_key not in CONFIG:
        raise ValueError("Phân tích Wyckoff chỉ hỗ trợ khung Ngày (1D) và Tuần (1W)")
    tf, cfg, p = get_tf(tf_key), CONFIG[tf_key], get_params()
    d = daily.sort_values("date").reset_index(drop=True)
    weekly = resample(d, get_tf("1W"))
    base = d if tf_key == "1D" else weekly
    if len(base) < cfg.min_bars + 10:
        raise ValueError(f"{symbol}: cần tối thiểu {cfg.min_bars + 10} nến {tf.label}, hiện có {len(base)}")

    found = manual_tr(base, start, end) if (start or end) else find_tr(base, cfg)
    last = base.iloc[-1]
    close = float(last["close"])
    levels = ichimoku_levels(d, "1D", p) + (ichimoku_levels(weekly, "1W", p) if len(weekly) > p.kijun else [])
    out: dict = {"symbol": symbol.upper(), "timeframe": tf_key, "date": fmt_time(last["date"], tf), "close": close,
                 "manual": bool(start or end), "tol_pct": tol_pct,
                 "ichimoku": {"levels": sorted(levels, key=lambda x: x["price"]),
                              "params": {"tenkan": p.tenkan, "kijun": p.kijun, "senkou_b": p.senkou_b,
                                         "displacement": p.displacement, "long_kijun": list(p.long_kijun)}}}
    if not found:
        out.update(tr={"found": False, "status": "none"}, chart=_chart(base, tf_key, None),
                   message="Chưa thấy vùng tích lũy / phân phối rõ ràng. Hãy chọn tay ngày bắt đầu và kết thúc TR.")
        return out

    s, e, bi = found["s"], found["e"], found["break_i"]
    h, l, c = (base[k].to_numpy(float) for k in ("high", "low", "close"))
    top, bot = float(h[s:e + 1].max()), float(l[s:e + 1].min())
    mid = (top + bot) / 2
    prior_i = max(0, s - cfg.prior)
    prior_pct = (c[s] / c[prior_i] - 1) * 100 if s > prior_i else 0.0
    if bi is not None:
        status = "breakout_up" if c[bi] > top else "breakout_down"
    else:
        status = "inside"
    if direction in ("up", "down"):
        dir_ = direction
    elif bi is not None:
        dir_ = "up" if status == "breakout_up" else "down"
    else:
        dir_ = "down" if prior_pct > 10 else "up"  # chưa phá vỡ: lấy xu hướng trước TR làm thiên hướng
    sgn = 1 if dir_ == "up" else -1
    if dir_ == "up":
        phase = "Tái tích lũy" if prior_pct > 10 else "Tích lũy"
    else:
        phase = "Tái phân phối" if prior_pct < -10 else "Phân phối"
    if bi is None:
        phase += " (dự kiến)"

    box_size = float(box) if box else auto_box(float(np.median(c[s:e + 1])))
    cols = pnf_columns(h[s:e + 1], l[s:e + 1], base["open"].to_numpy(float)[s:e + 1], c[s:e + 1], box_size, reversal)
    count = len(cols) * box_size * reversal
    bases = ([("T1", "Thận trọng", "đáy TR", bot), ("T2", "Trung bình", "giữa TR", mid), ("T3", "Toàn bộ nguyên nhân", "đỉnh TR", top)]
             if sgn > 0 else
             [("T1", "Thận trọng", "đỉnh TR", top), ("T2", "Trung bình", "giữa TR", mid), ("T3", "Toàn bộ nguyên nhân", "đáy TR", bot)])
    since = (bi if bi is not None else e + 1)
    hi_since = float(h[since:].max()) if since < len(h) else close
    lo_since = float(l[since:].min()) if since < len(l) else close
    targets = []
    floor_price = float(tick_size(close))
    for key, label, base_name, base_price in bases:
        raw_price = base_price + sgn * count
        price = max(raw_price, floor_price)  # hướng xuống: Count có thể lớn hơn cả giá
        reached = hi_since >= price if sgn > 0 else lo_since <= price
        targets.append({"key": key, "label": label, "base": base_name, "base_price": float(base_price),
                        "price": float(price), "pct": round((price / close - 1) * 100, 2), "reached": bool(reached),
                        "capped": raw_price < floor_price})

    vols = base["volume"].to_numpy(float)
    ref = bi if bi is not None else len(base)
    avg20 = float(vols[max(0, ref - 20):ref].mean()) if ref > 0 else 0.0
    half = s + (e - s) // 2
    brk_ratio = float(vols[bi] / avg20) if bi is not None and avg20 else None
    tr = {"found": True, "status": status, "direction": dir_, "phase": phase,
          "start": fmt_time(base["date"].iloc[s], tf), "end": fmt_time(base["date"].iloc[e], tf), "bars": e - s + 1,
          "top": top, "bottom": bot, "mid": mid, "width_pct": round((top / bot - 1) * 100, 2),
          "touches_top": _episodes(h[s:e + 1] >= top - (top - bot) * 0.1),
          "touches_bottom": _episodes(l[s:e + 1] <= bot + (top - bot) * 0.1),
          "prior_trend_pct": round(float(prior_pct), 1),
          "vol_contraction": round(float(vols[half:e + 1].mean() / vols[s:half + 1].mean()), 2) if half > s else None,
          "break_date": fmt_time(base["date"].iloc[bi], tf) if bi is not None else None,
          "break_vol_ratio": round(brk_ratio, 2) if brk_ratio else None,
          "bars_since_break": (len(base) - 1 - bi) if bi is not None else None,
          "position_pct": round((close - bot) / (top - bot) * 100, 1) if top > bot else None,
          "s": s, "e": e}
    out["tr"] = {k: v for k, v in tr.items() if k not in ("s", "e")}
    out["pnf"] = {"box": box_size, "reversal": reversal, "columns": [{"dir": x["dir"], "hi": x["hi"], "lo": x["lo"]} for x in cols],
                  "count_columns": len(cols), "count_points": float(count), "count_pct": round(count / mid * 100, 1)}
    out["targets"] = targets
    out["events"] = _events(base, found, tf)
    out["confluence"] = confluence(targets, levels, close, sgn, tol_pct)

    atr = _atr(base)
    if status in ("breakout_up", "breakout_down"):
        stop = (top - atr) if sgn > 0 else (bot + atr)  # phá vỡ thất bại nếu giá quay lại TR
        stop_name = "đỉnh TR − 1 ATR" if sgn > 0 else "đáy TR + 1 ATR"
    else:
        stop = (bot - atr * 0.5) if sgn > 0 else (top + atr * 0.5)
        stop_name = "dưới đáy TR" if sgn > 0 else "trên đỉnh TR"
    risk = (close - stop) * sgn
    out["risk"] = {"entry": close, "stop": float(stop), "stop_name": stop_name, "atr": round(atr, 2),
                   "rr": [{"target": t["key"], "rr": round((t["price"] - close) * sgn / risk, 2) if risk > 0 else None}
                          for t in targets]}
    out["chart"] = _chart(base, tf_key, {"s": s, "e": e, "top": top, "bottom": bot})
    warnings = []
    if out["pnf"]["count_pct"] > 60:
        warnings.append(f"Count bằng {out['pnf']['count_pct']:.0f}% giá: TR dài / box nhỏ nên mục tiêu rất xa — ưu tiên T1, "
                        "hoặc tăng box / chọn tay đoạn TR gọn hơn.")
    if any(t["capped"] for t in targets):
        warnings.append("Count lớn hơn cả giá: mục tiêu hướng xuống bị chặn ở mức tối thiểu.")
    if tr["bars"] < 2 * cfg.min_bars:
        warnings.append(f"TR chỉ {tr['bars']} nến — nguyên nhân tích lũy còn ngắn, độ tin cậy mục tiêu thấp.")
    if tr["break_vol_ratio"] is not None and tr["break_vol_ratio"] < 1.2:
        warnings.append(f"Nến phá vỡ có KL chỉ {tr['break_vol_ratio']}x trung bình 20 nến: chưa xác nhận bằng khối lượng.")
    if tr["vol_contraction"] is not None and tr["vol_contraction"] > 1.3 and sgn > 0:
        warnings.append("KL nửa sau TR lớn hơn nửa đầu: cung chưa cạn, cẩn trọng với tích lũy.")
    out["warnings"] = warnings
    return out
