import io
import os
import time
import zipfile

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.services.eod_import import parse_eod
from app.services.stock_service import StockService, adjust_ratio
from app.services.timeframes import get_tf

HEADER = "﻿<Ticker>,<DTYYYYMMDD>,<Open>,<High>,<Low>,<Close>,<Volume>\n"


def cafef(rows: list[str]) -> bytes:
    return (HEADER + "\n".join(rows) + "\n").encode("utf-8")


def history(sym: str, start: str, n: int, price: float = 50.0) -> list[str]:
    return [f"{sym},{d:%Y%m%d},{price},{price + 1},{price - 1},{price + 0.5},1000"
            for d in pd.bdate_range(start, periods=n)]


def test_parse_scales_to_vnd_and_skips_warrants_and_indexes():
    raw = cafef(["FPT,20261111,51,51.1,50.2,50.9,6533000", "CFPT2501,20261111,1.2,1.3,1.1,1.2,100",
                 "VNINDEX,20261111,1200,1210,1190,1205,5", "HPG,20261111,21.2,21.35,21.15,21.35,10152300",
                 "VNM,xx,1,1,1,1,1", "FPT,20261110,51.6,52.5,50.9,50.9,7268500"])
    bars, st = parse_eod(raw, "CafeF.HSX.csv")
    assert st["symbols"] == 2 and st["bars"] == 3 and st["skipped_cw"] == 1 and st["skipped_other"] == 1
    assert st["bad_rows"] == 1 and (st["first"], st["last"]) == ("2026-11-10", "2026-11-11")
    fpt = bars[bars["symbol"] == "FPT"]
    assert fpt["date"].is_monotonic_increasing
    assert fpt.iloc[-1][["open", "high", "low", "close", "volume"]].tolist() == [51000, 51100, 50200, 50900, 6533000]


def test_parse_reads_zip_and_rejects_wrong_columns():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("CafeF.HSX.Upto.csv", cafef(history("FPT", "2020-01-01", 5)))
        z.writestr("CafeF.HNX.Upto.csv", cafef(history("SHS", "2020-01-01", 5)))
    bars, st = parse_eod(buf.getvalue(), "CafeF.SolieuGD.Upto.zip")
    assert st["symbols"] == 2 and len(st["files"]) == 2 and len(bars) == 10
    with pytest.raises(ValueError, match="thiếu cột"):
        parse_eod(b"a,b,c\n1,2,3\n", "x.csv")


@pytest.fixture
def svc(tmp_path):
    s = StockService()
    s.s = s.s.model_copy(update={"DATA_DIR": str(tmp_path), "DATA_SOURCE": "vietcap"})
    return s


def vietcap_rows(dates, close):
    d = pd.to_datetime(list(dates))
    return pd.DataFrame({"date": d, "open": close, "high": close + 1000, "low": close - 1000, "close": close,
                         "volume": 5.0})


class Src:
    configured = True

    def __init__(self, close=50500.0):
        self.close, self.calls = close, []

    def get_ohlcv(self, symbol, start, end, tf):
        self.calls.append(start)
        days = pd.bdate_range(start, end)
        return vietcap_rows(days, self.close)


def test_import_merges_into_cache_and_marks_it_stale(svc):
    tf = get_tf("1D")
    svc._write_cache("FPT", tf, vietcap_rows(pd.bdate_range("2026-10-01", periods=5), 99000.0), "vietcap")
    svc._cache_path("FPT", get_tf("1W")).write_text("date\n")
    bars, _ = parse_eod(cafef(history("FPT", "2026-09-01", 25)), "f.csv")
    r = svc.import_eod(bars)
    assert r == {"symbols": 1, "new_symbols": 0, "new_bars": 22}         # 01, 02, 05/10 overlap the cache
    df = svc._read_cache("FPT", tf, fresh_only=False).set_index("date")
    assert len(df) == 27 and df.loc["2026-10-05", "close"] == 50500 and df.loc["2026-10-05", "source"] == "cafef"
    assert df.loc["2026-10-07", "source"] == "vietcap"                       # newer cached sessions are kept
    assert time.time() - os.path.getmtime(svc._cache_path("FPT", tf)) > 86400                     # stale -> top-up
    assert not svc._cache_path("FPT", get_tf("1W")).exists()


def test_top_up_keeps_imported_history_and_builds_weeks_from_it(svc):
    bars, _ = parse_eod(cafef(history("FPT", "2015-01-01", 3000)), "f.csv")
    svc.import_eod(bars)
    last = bars["date"].max()
    svc.vietcap = Src(close=50500.0)       # same price as the imported closes -> no re-adjustment
    df, source = svc.get_ohlcv("FPT", timeframe="1D")
    assert source == "cafef + vietcap" and len(svc.vietcap.calls) == 1
    assert svc.vietcap.calls[0] == (last - pd.Timedelta(days=10)).date()        # only the newest sessions
    assert df["date"].min() == pd.Timestamp("2015-01-01") and df["date"].max() > last
    wk, src_w = svc.get_ohlcv("FPT", timeframe="1W")
    assert wk["date"].min() == pd.Timestamp("2014-12-29") and (wk["date"].dt.day_of_week == 0).all()
    assert src_w.startswith("cafef + vietcap") and not svc._cache_path("FPT", get_tf("1W")).exists()


def test_readjusted_prices_flag_an_imported_symbol(svc):
    bars, _ = parse_eod(cafef(history("FPT", "2020-01-01", 1700)), "f.csv")
    svc.import_eod(bars)
    svc.vietcap = Src(close=40000.0)       # corporate action: the source re-adjusted the whole history
    df, source = svc.get_ohlcv("FPT")
    assert "cần Import lại" in source and "FPT" in svc.readjust_flags()
    assert df["date"].min() == pd.Timestamp("2020-01-01")                      # imported history kept
    svc.import_eod(bars)                                                       # re-import clears the flag
    assert "FPT" not in svc.readjust_flags()


def test_readjusted_rest_cache_is_reloaded(svc):
    tf = get_tf("1D")
    svc._write_cache("VNM", tf, vietcap_rows(pd.bdate_range(end=pd.Timestamp.today().normalize()
                                                            - pd.Timedelta(days=5), periods=400), 60000.0), "vietcap")
    os.utime(svc._cache_path("VNM", tf), (0, 0))
    svc.vietcap = Src(close=55000.0)
    svc.get_ohlcv("VNM")
    assert len(svc.vietcap.calls) == 2                                         # top-up, then full reload
    assert (svc._read_cache("VNM", tf, fresh_only=False)["close"] == 55000).all()
    assert svc.readjust_flags() == {}


def test_adjust_ratio_ignores_last_bars_and_small_gaps():
    old = vietcap_rows(pd.bdate_range("2026-11-02", periods=5), 50000.0)
    new = old.copy()
    new.loc[new.index[-1], "close"] = 40000                                    # bar in progress
    assert adjust_ratio(old, new) is None
    new["close"] = 50100.0
    assert adjust_ratio(old, new) is None                                      # 0.2% < tolerance
    new["close"] = 25000.0
    assert adjust_ratio(old, new) == pytest.approx(0.5)


def test_import_endpoint(svc):
    from app.api.deps import get_stock_service
    from app.main import app

    prev = app.dependency_overrides.get(get_stock_service)
    app.dependency_overrides[get_stock_service] = lambda: svc
    try:
        c = TestClient(app)
        r = c.post("/api/analysis/eod/import?filename=CafeF.HNX.csv",
                   content=cafef(["SHS,20261111,15,15.2,14.8,15.1,100", "ABCDEFGH,20261111,1,1,1,1,1"]))
        assert r.status_code == 200, r.text
        assert r.json()["symbols"] == 1 and r.json()["skipped_cw"] == 1 and r.json()["new_symbols"] == 1
        assert c.post("/api/analysis/eod/import?filename=x.csv", content=b"x\n1\n").status_code == 422
    finally:
        if prev is None:
            app.dependency_overrides.pop(get_stock_service, None)
        else:
            app.dependency_overrides[get_stock_service] = prev
