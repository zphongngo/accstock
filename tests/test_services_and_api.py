import time
from datetime import date

import pandas as pd
from fastapi.testclient import TestClient

from app.api.deps import get_claude_service, get_stock_service
from app.main import app
from app.services.ssi_service import SSIService
from app.services.vietcap_service import VietcapService
from tests.conftest import make_ohlcv


def _ssi_bar(ts, o, h, lo, c, v):
    return {"symbol": "FPT", "trading_date": ts, "open_price": o, "high_price": h, "low_price": lo,
            "close_price": c, "volume": v, "value": 1}


def test_ssi_normalize_daily_and_intraday():
    # ssi-sdk OHLCData fields (newest first, like the server)
    df = SSIService.normalize([_ssi_bar("2026/10/02", 120000, 121500, 119000, 121000, 3500000),
                               _ssi_bar("2026/10/01", 119000, 120500, 118000, 120000, 3000000)])
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert df["date"].iloc[0].day == 1 and df["close"].iloc[-1] == 121000
    di = SSIService.normalize([_ssi_bar("2026/11/03 09:15:00", 1, 2, 1, 2, 10),
                               _ssi_bar("2026/11/03 09:30:00", 2, 3, 2, 3, 20)], intraday=True)
    assert di["date"].iloc[1].hour == 9 and di["date"].iloc[1].minute == 30


def test_ssi_30m_from_15m(monkeypatch):
    bars = [_ssi_bar(f"2026/11/03 {t}", o, o + 1, o - 1, o, 100)
            for t, o in (("09:00:00", 10), ("09:15:00", 11), ("09:30:00", 12), ("09:45:00", 13))]
    svc = SSIService()
    monkeypatch.setattr(svc, "_fetch", lambda sym, tf, s, e: bars if tf == "15m" else [])
    df = svc.get_ohlcv("FPT", date(2026, 11, 3), date(2026, 11, 3), "30m")
    assert len(df) == 2 and df["open"].tolist() == [10, 12] and df["close"].tolist() == [11, 13]
    assert df["volume"].tolist() == [200, 200] and df["high"].iloc[0] == 12


def test_vietcap_normalize():
    t0 = int(time.mktime((2026, 10, 1, 9, 0, 0, 0, 0, 0)))
    payload = [{"symbol": "FPT", "o": [119000, 120000], "h": [120500, 121500], "l": [118000, 119000],
                "c": [120000, 121000], "v": [3000000, 3500000], "t": [str(t0), str(t0 + 86400)]}]
    df = VietcapService.normalize(payload)
    assert len(df) == 2 and df["close"].iloc[-1] == 121000
    di = VietcapService.normalize(payload, intraday=True)
    assert di["date"].iloc[0].hour == 9  # Vietnam local time


class FakeRealtime:
    def watch(self, symbol):
        return False

    def status(self):
        return {"enabled": False, "state": "idle"}


class FakeStock:
    realtime = FakeRealtime()

    def get_ohlcv(self, symbol, days=None, refresh=False, timeframe="1D"):
        if symbol.upper() == "BAD":
            raise RuntimeError("no data")
        df = make_ohlcv(300, seed=hash(symbol.upper()) % 1000)
        if timeframe != "1D":  # intraday-like timestamps: 5-minute steps
            df["date"] = pd.date_range("2026-10-01 09:00", periods=len(df), freq="5min")
        return df, "fake"

    def live_bars(self, symbol, timeframe="1D", count=2):
        df, _ = self.get_ohlcv(symbol, timeframe=timeframe)
        return df.tail(count).to_dict("records"), False


class FakeClaude:
    configured = True

    class s:  # noqa: N801
        CLAUDE_MODEL = "test-model"

    def analyze(self, analysis, question=None):
        return f"Nhận định {analysis['symbol']} điểm {analysis['score']}"


app.dependency_overrides[get_stock_service] = lambda: FakeStock()
app.dependency_overrides[get_claude_service] = lambda: FakeClaude()
client = TestClient(app)


def test_dashboard_and_health():
    assert client.get("/health").json()["status"] == "ok"
    r = client.get("/")
    assert r.status_code == 200 and "Scanner" in r.text


def test_market_and_analysis():
    r = client.get("/api/market/ohlcv/fpt")
    assert r.status_code == 200 and r.json()["count"] == 300
    a = client.get("/api/analysis/FPT").json()
    assert "ichimoku" in a and "time_cycles" in a["ichimoku"]
    c = client.get("/api/analysis/FPT/chart").json()
    assert c["candles"] and c["rsi"]


def test_timeframes_endpoints():
    r = client.get("/api/market/ohlcv/FPT?timeframe=5m").json()
    assert r["timeframe"] == "5m" and r["data"][0]["date"].endswith("09:00")
    c = client.get("/api/analysis/FPT/chart?timeframe=15m").json()
    assert c["intraday"] and isinstance(c["candles"][0]["time"], int)
    a = client.get("/api/analysis/FPT?timeframe=1h").json()
    assert a["timeframe"] == "1h" and ":" in a["date"]
    lv = client.get("/api/market/live/FPT?timeframe=5m").json()
    assert len(lv["candles"]) == 2 and lv["streaming"] is False
    assert client.get("/api/market/ohlcv/FPT?timeframe=2h").status_code == 422


def test_scan_and_ai():
    r = client.post("/api/analysis/scan", json={"symbols": ["FPT", "HPG", "BAD"]}).json()
    assert r["count"] == 3
    assert r["results"][-1]["symbol"] == "BAD" and r["results"][-1]["error"]
    scores = [x["score"] for x in r["results"][:2]]
    assert scores == sorted(scores, reverse=True)
    ai = client.post("/api/ai/analyze/FPT", json={}).json()
    assert ai["commentary"].startswith("Nhận định FPT")


def test_chart_custom_ichimoku_params():
    c = client.get("/api/analysis/FPT/chart?tenkan=7&kijun=22&senkou_a=30&senkou_b=44&chikou=20").json()
    assert {k: c["params"][k] for k in ("tenkan", "kijun", "senkou_a", "senkou_b", "chikou")} == \
        {"tenkan": 7, "kijun": 22, "senkou_a": 30, "senkou_b": 44, "chikou": 20}
    assert c["default_params"]["tenkan"] == 9
    a = client.get("/api/analysis/FPT?kijun=22").json()
    assert a["ichimoku"]["params"]["kijun"] == 22 and a["ichimoku"]["params"]["tenkan"] == 9
    assert client.get("/api/analysis/FPT/chart?tenkan=0").status_code == 422
