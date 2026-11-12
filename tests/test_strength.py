from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.analysis import strength as st
from app.api.deps import get_board_service
from app.main import app
from tests.conftest import make_ohlcv


def test_roc_and_ibd_weights():
    close = pd.Series([100.0] * 300)
    close.iloc[-1] = 120.0                      # +20% over every horizon
    assert st.roc(close, 63) == pytest.approx(20)
    assert st.rs_raw(close) == pytest.approx(20)
    # the last quarter counts double: +40% over 63 sessions, flat before
    c = pd.Series([100.0] * 237 + [100 + 40 * i / 63 for i in range(1, 64)])
    assert st.rs_raw(c) == pytest.approx(0.4 * 40 + 0.2 * 40 * 3)
    # less than a year: weights of the available ROCs are rescaled; no ROC63 -> None
    short = pd.Series([100.0] * 100 + [110.0])
    assert st.rs_raw(short) == pytest.approx(10) and st.roc(short, 126) is None
    assert st.rs_raw(pd.Series([100.0] * 50)) is None


def test_percentile_ratings_1_to_99():
    r = st.percentile_ratings({"A": -10, "B": 0, "C": 5, "D": 50})
    assert (r["A"], r["D"]) == (1, 99) and r["A"] < r["B"] < r["C"] < r["D"]
    assert st.percentile_ratings({"X": 1, "Y": 1}) == {"X": 50, "Y": 50}
    assert st.percentile_ratings({"X": 3}) == {"X": 99} and st.percentile_ratings({}) == {}


def test_strength_metrics_vs_index():
    df = make_ohlcv(n=320, trend=0.003)                       # strong uptrend
    idx = df[["date"]].assign(close=1000.0)                   # flat index
    m = st.strength_metrics(df, idx)
    assert m["rs_index"] > 1 and m["rs_raw"] > 0 and m["bars"] == 320
    assert set(m["tech_hits"]) <= set(st.TECH) and "rs80" not in m["tech_hits"]
    assert set(m["template"]) == set(st.TEMPLATE) - {"rs70"}
    assert st.strength_metrics(df, None)["rs_index"] is None   # VN-Index unavailable: still works
    with pytest.raises(ValueError):
        st.strength_metrics(df.head(40), idx)


def _m(raw, hits=(), tpl=True):
    return {"date": "2026-11-04", "close": 10000.0, "change_pct": 1.0, "roc": {}, "rs_raw": raw, "rs_index": 1.1,
            "rs_line_high": True, "rs_leads": False, "tech_hits": list(hits), "breakout_52w": False, "rsi": 60.0,
            "vol_ratio": 1.0, "from_high_pct": -5.0, "from_low_pct": 40.0,
            "template": {k: tpl for k in ("ma_stack", "ma200_up", "above_low", "near_high")}}


def test_rank_universe_composite_sectors_and_template():
    metrics = {"AAA": _m(50, ["cloud", "tk"]), "BBB": _m(10), "CCC": _m(-5), "DDD": _m(-20, tpl=False)}
    out = st.rank_universe(metrics, [("Thép", ["AAA", "BBB"]), ("Ngân hàng", ["CCC", "DDD", "ZZZ"])])
    rows = {r["symbol"]: r for r in out["rows"]}
    a = rows["AAA"]
    assert a["rs_rating"] == 99 and "rs80" in a["tech_hits"] and a["tech"] == 15 + 10 + 10
    assert a["tech_groups"] == {"trend": 25, "momentum": 10, "flow": 0}
    assert a["sector"] == "Thép" and a["sector_rs"] == pytest.approx((99 + 66) / 2, abs=0.6)
    assert a["composite"] == pytest.approx(0.4 * 99 + 0.4 * 35 + 0.2 * a["sector_rs"], abs=0.1)
    assert a["template_pass"] and not rows["BBB"]["template_pass"]          # BBB: RS Rating < 70
    assert rows["DDD"]["template_count"] == 0 and out["rows"][0]["symbol"] == "AAA" and a["rank"] == 1
    assert [s["name"] for s in out["sectors"]] == ["Thép", "Ngân hàng"] and out["sectors"][0]["top"] == ["AAA", "BBB"]


def test_strength_endpoint_and_refresh():
    from tests.test_priceboard import FakeBoard

    board = FakeBoard()
    board._scores_day = date.today().isoformat()
    board._scores = {"FPT": {"strength": _m(30), "acc": None}, "VCB": {"strength": _m(5), "acc": None},
                     "HPG": {"strength": None, "acc": None}}
    app.dependency_overrides[get_board_service] = lambda: board
    try:
        c = TestClient(app)
        d = c.get("/api/board/strength?watch=hpg").json()
        assert [r["symbol"] for r in d["rows"]][:2] == ["FPT", "VCB"]
        assert "HPG" in d["failed"] and d["progress"]["done"] == 3 and d["legend"]["weights"]["rs"] == 0.4
        assert c.post("/api/board/strength/refresh").json() == {"ok": True} and board._scores == {}
    finally:
        app.dependency_overrides.pop(get_board_service, None)
