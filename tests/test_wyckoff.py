import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.analysis import wyckoff as wy
from app.api.deps import get_stock_service
from app.main import app


def make_base_breakout(seed: int = 3, rally: bool = True) -> pd.DataFrame:
    """Giảm 140 -> 100, đi ngang 100–120 trong 90 phiên, rồi phá vỡ đỉnh và tăng tiếp (nghìn đồng x 1000)."""
    rng = np.random.default_rng(seed)
    down = np.linspace(140_000, 102_000, 80)
    t = np.arange(90)
    rng_part = 110_000 + 8_500 * np.sin(t / 90 * 6 * np.pi) + rng.normal(0, 700, 90)
    up = np.linspace(123_000, 150_000, 30) if rally else np.array([])
    close = np.concatenate([down, rng_part, up])
    high = close * 1.006
    low = close * 0.994
    # đỉnh / đáy TR cố định để test chính xác
    start, end = 80, 169
    high[start:end + 1] = np.minimum(high[start:end + 1], 120_000)
    low[start:end + 1] = np.maximum(low[start:end + 1], 100_000)
    vol = np.full(len(close), 1_000_000.0)
    if rally:
        vol[end + 1] = 3_000_000
    return pd.DataFrame({"date": pd.bdate_range("2025-01-02", periods=len(close)), "open": close, "high": high,
                         "low": low, "close": close, "volume": vol})


def test_auto_box_uses_vn_tick_multiples():
    assert wy.auto_box(8_000) == 80 or wy.auto_box(8_000) % 10 == 0
    b = wy.auto_box(110_000)
    assert b % 100 == 0 and 900 <= b <= 1500
    assert wy.auto_box(25_000) % 50 == 0


def test_pnf_columns_three_box_reversal():
    box = 1000.0
    high = [10_500, 12_500, 12_200, 9_900, 9_000, 9_600, 12_100]
    low = [10_100, 11_000, 10_100, 9_000, 8_500, 8_900, 9_700]
    cols = wy.pnf_columns(high, low, high, high, box)
    # bar 1 tăng, bar 3 đảo chiều xuống (>= 3 box), bar 6 đảo chiều lên
    assert [c["dir"] for c in cols][:3] == [1, -1, 1]
    assert cols[0]["hi"] == 12 and cols[1]["lo"] <= 9


def test_find_tr_after_breakout_and_targets():
    df = make_base_breakout()
    r = wy.analyze_wyckoff("TST", df, "1D")
    tr = r["tr"]
    assert tr["found"] and tr["status"] == "breakout_up" and tr["direction"] == "up"
    assert tr["top"] == pytest.approx(120_000, rel=0.02) and tr["bottom"] == pytest.approx(100_000, rel=0.03)
    assert tr["bars"] >= 60 and tr["break_vol_ratio"] == pytest.approx(3.0, rel=0.05)
    t1, t2, t3 = r["targets"]
    count = r["pnf"]["count_points"]
    assert count == pytest.approx(r["pnf"]["count_columns"] * r["pnf"]["box"] * 3)
    assert (t1["price"], t2["price"], t3["price"]) == pytest.approx((tr["bottom"] + count, tr["mid"] + count, tr["top"] + count))
    assert t1["price"] < t2["price"] < t3["price"]
    assert [e["type"] for e in r["events"]].count("SOS") == 1


def test_inside_tr_without_breakout():
    df = make_base_breakout(rally=False)
    r = wy.analyze_wyckoff("TST", df, "1D")
    assert r["tr"]["status"] == "inside" and r["tr"]["break_date"] is None
    assert "dự kiến" in r["tr"]["phase"] and r["tr"]["direction"] == "up"  # sau một nhịp giảm


def test_downside_targets_mirror():
    r = wy.analyze_wyckoff("TST", make_base_breakout(rally=False), "1D", direction="down")
    count = r["pnf"]["count_points"]
    assert [t["key"] for t in r["targets"]] == ["T1", "T2", "T3"]
    assert r["targets"][0]["price"] == pytest.approx(r["tr"]["top"] - count)
    assert r["targets"][2]["price"] == pytest.approx(max(r["tr"]["bottom"] - count, 0))


def test_manual_tr_overrides_detection():
    df = make_base_breakout()
    d0, d1 = df["date"].iloc[85], df["date"].iloc[160]
    r = wy.analyze_wyckoff("TST", df, "1D", start=str(d0.date()), end=str(d1.date()))
    assert r["manual"] and r["tr"]["start"] == str(d0.date()) and r["tr"]["end"] == str(d1.date())
    with pytest.raises(ValueError):
        wy.analyze_wyckoff("TST", df, "1D", start="2030-01-01")


def test_confluence_golden_and_blocker():
    targets = [{"key": "T1", "price": 130.0}, {"key": "T2", "price": 140.0}, {"key": "T3", "price": 150.0}]
    levels = [
        {"tf": "1W", "kind": "kijun", "name": "Kijun 17 phẳng (Tuần)", "price": 140.5, "weight": 3.0, "flat": True},
        {"tf": "1D", "kind": "kumo", "name": "Mây trên (Ngày)", "price": 135.0, "weight": 1.5, "thick": True},
        {"tf": "1D", "kind": "kijun", "name": "Kijun (Ngày)", "price": 90.0, "weight": 1.0, "flat": False},
    ]
    c = wy.confluence(targets, levels, close=120.0, sgn=1, tol=1.0)
    z = {x["target"]: x for x in c["zones"]}
    assert z["T2"]["golden"] and z["T2"]["rating"] == "Có" and not z["T1"]["matches"]
    codes = [s["code"] for s in c["scenarios"]]
    assert "A" in codes and "B" in codes          # mây dày chặn T2 + Kijun Tuần phẳng trùng T2
    assert [o["price"] for o in c["obstacles"]] == [135.0, 140.5]  # kijun 90 nằm sau giá, bị loại


def test_ichimoku_levels_cover_both_timeframes():
    df = make_base_breakout()
    r = wy.analyze_wyckoff("TST", df, "1D")
    lv = r["ichimoku"]["levels"]
    assert {"1D", "1W"} <= {x["tf"] for x in lv}
    assert {"kijun", "kumo"} <= {x["kind"] for x in lv}
    assert r["chart"]["tr_box"]["top"] == pytest.approx(r["tr"]["top"]) and r["chart"]["candles"]


def test_weekly_timeframe_and_api():
    class Stock:
        def get_ohlcv(self, symbol, days=None, refresh=False, timeframe="1D"):
            return make_base_breakout(rally=True), "fake"

    prev = app.dependency_overrides.get(get_stock_service)
    app.dependency_overrides[get_stock_service] = lambda: Stock()
    try:
        c = TestClient(app)
        r = c.get("/api/analysis/TST/wyckoff?timeframe=1W")
        assert r.status_code == 200 and r.json()["timeframe"] == "1W"
        r = c.get("/api/analysis/TST/wyckoff?tol=2&direction=up")
        assert r.status_code == 200 and r.json()["tr"]["status"] == "breakout_up"
        assert c.get("/api/analysis/TST/wyckoff?timeframe=1h").status_code == 422
        assert c.get("/api/analysis/TST/wyckoff?start=2030-01-01").status_code == 422
    finally:
        if prev is None:
            app.dependency_overrides.pop(get_stock_service, None)
        else:
            app.dependency_overrides[get_stock_service] = prev


def test_chart_sends_whole_history_zoomed_on_recent_bars():
    from tests.conftest import make_ohlcv

    df = make_ohlcv(2500, seed=3)                      # ~10 years, e.g. after an EOD import
    ch = wy.analyze_wyckoff("TST", df, "1D")["chart"]
    assert len(ch["candles"]) == 2500 and 320 <= ch["view_bars"] < 2500
