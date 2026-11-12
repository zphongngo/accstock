from datetime import date, datetime

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.analysis import acc_strength as acc
from app.api.deps import get_board_service
from app.main import app
from tests.conftest import make_ohlcv


def test_points_tables_sum_to_100():
    assert sum(p for p, _ in acc.TREND.values()) == 100
    assert sum(p for p, _ in {**acc.MOMENTUM, **acc.MOMENTUM_SCALED}.values()) == 100
    assert sum(p for p, _ in {**acc.FLOW, **acc.FLOW_SCALED}.values()) == 100
    assert sum(p for p, _ in acc.ICHIMOKU.values()) == 100
    assert sum(p for p, _ in acc.SETUP.values()) == 100
    assert sum(p for p, _ in acc.RISK.values()) == 100
    assert sum(acc.WEIGHTS.values()) == pytest.approx(1)


def test_percentiles_formula():
    p = acc.percentiles({"A": -5, "B": 0, "C": 10, "D": None})
    assert p == {"A": 0.0, "B": 50.0, "C": 100.0}                 # (N − Rank) / (N − 1) × 100, None skipped
    assert acc.percentiles({"X": 1, "Y": 1}) == {"X": 50.0, "Y": 50.0}
    assert acc.percentiles({"X": 3}) == {"X": 50.0} and acc.percentiles({}) == {}   # N = 1: no division by 0


def test_completed_drops_the_running_session():
    df = make_ohlcv(n=80)
    day = df["date"].iloc[-1]
    assert len(acc.completed(df, datetime.combine(day.date(), datetime.min.time()).replace(hour=10))) == 79
    assert len(acc.completed(df, datetime.combine(day.date(), datetime.min.time()).replace(hour=15, minute=5))) == 80
    assert len(acc.completed(df, datetime(2099, 1, 1, 10))) == 80             # last bar is not today


def test_metrics_vs_benchmark():
    df = make_ohlcv(n=320, trend=0.003)                                       # strong uptrend
    bench = df[["date"]].assign(close=1000.0)                                 # flat VN30
    m = acc.acc_metrics(df, bench)
    assert m["benchmark"] and m["excess"]["3M"] == pytest.approx(m["ret"]["3M"])
    assert set(m["trend"]["hits"]) <= set(acc.TREND) and m["trend"]["score"] >= 70
    assert 0 <= m["flow"]["score"] <= 100 and 0 <= m["setup"]["score"] <= 100
    assert "rs_up" in m["setup"]["hits"]
    plain = acc.acc_metrics(df, None)                                         # no VN30: plain returns
    assert not plain["benchmark"] and plain["excess"] == plain["ret"]
    with pytest.raises(ValueError):
        acc.acc_metrics(df.head(40), bench)
    pair = acc.acc_pair(df, bench)
    assert pair["prev"]["date"] < pair["today"]["date"]


def test_signal_priority():
    assert acc.signal(95, 90, 60, True) == "OVERHEATED"
    assert acc.signal(60, 80, 10, True) == "BREAKOUT"
    assert acc.signal(85, 80, 40, False) == "STRONG BUY WATCH"
    assert acc.signal(85, 80, 45, False) == "STRONG"
    assert acc.signal(72, 60, 0, False) == "ACCUMULATION"
    assert acc.signal(72, 60, 0, True) == "NEUTRAL"                       # breakout, Setup < 75
    assert acc.signal(30, 0, 0, False) == "WEAK" and acc.signal(None, 0, 0, False) == "NEUTRAL"


def _pairs():
    up, flat, down = (make_ohlcv(n=320, seed=s, trend=t) for s, t in ((1, 0.003), (2, 0.0), (3, -0.003)))
    bench = up[["date"]].assign(close=1000.0)
    return {"UP": acc.acc_pair(up, bench), "FLAT": acc.acc_pair(flat, bench), "DOWN": acc.acc_pair(down, bench)}


def test_rank_acc_scores_and_formulas():
    out = acc.rank_acc(_pairs(), [("Thép", ["UP", "FLAT"]), ("Ngân hàng", ["DOWN"])])
    rows = {r["symbol"]: r for r in out["rows"]}
    u = rows["UP"]
    assert out["rows"][0]["symbol"] == "UP" and u["rank"] == 1 and rows["DOWN"]["rank"] == 3
    assert u["p"]["12M"] == 100 and rows["DOWN"]["p"]["12M"] == 0 and rows["DOWN"]["rs"] < u["rs"]
    other = (0.25 * u["trend"] + 0.20 * u["momentum"] + 0.15 * u["flow"] + 0.10 * u["ichimoku"]) / 0.70
    assert u["swing"] == pytest.approx(0.30 * u["rs_swing"] + 0.70 * other, abs=0.2)
    assert u["position"] == pytest.approx(0.30 * u["rs_position"] + 0.70 * other, abs=0.2)
    assert u["combined"] == pytest.approx((u["swing"] + u["position"]) / 2, abs=0.1)
    # Combined equals the overall 30/25/20/15/10 formula
    assert u["combined"] == pytest.approx(0.30 * u["rs"] + 0.25 * u["trend"] + 0.20 * u["momentum"]
                                          + 0.15 * u["flow"] + 0.10 * u["ichimoku"], abs=0.2)
    assert u["prev_combined"] is not None and u["delta"] == pytest.approx(u["combined"] - u["prev_combined"], abs=0.11)
    assert u["sector"] == "Thép" and u["signal"] in acc.SIGNALS and u["trend_state"] == "Bullish"
    assert {r["rank_swing"] for r in out["rows"]} == {1, 2, 3}
    ov = out["overview"]
    assert ov["count"] == 3 and sum(ov["distribution"]) == 3 and sum(ov["signals"].values()) == 3
    assert [s["name"] for s in ov["sectors"]] == ["Thép", "Ngân hàng"]
    assert out["date"] == u["date"] and out["prev_date"] < out["date"]


def test_acc_endpoint():
    from tests.test_priceboard import FakeBoard

    board = FakeBoard()
    board._scores_day = date.today().isoformat()
    p = _pairs()
    board._scores = {"FPT": {"strength": None, "acc": p["UP"]}, "VCB": {"strength": None, "acc": p["DOWN"]},
                     "HPG": {"strength": None, "acc": None}}
    app.dependency_overrides[get_board_service] = lambda: board
    try:
        d = TestClient(app).get("/api/board/acc?watch=hpg").json()
        assert [r["symbol"] for r in d["rows"]] == ["FPT", "VCB"]
        assert d["failed"] == ["HPG"] and d["progress"]["done"] == 3 and d["legend"]["benchmark"] == "VN30"
        assert d["overview"]["count"] == 2
    finally:
        app.dependency_overrides.pop(get_board_service, None)


def test_entries_without_acc_are_recomputed():
    from app.services.priceboard_service import _done

    assert not _done({"strength": {}}) and _done({"strength": None, "acc": None})


def test_indicators_basic_ranges():
    df = make_ohlcv(n=200)
    a, pdi, mdi = acc.adx(df)
    assert a.dropna().between(0, 100).all() and pdi.dropna().ge(0).all()
    assert acc.cmf(df).dropna().between(-1, 1).all() and acc.mfi(df).dropna().between(0, 100).all()
    assert isinstance(acc.obv(df), pd.Series)
