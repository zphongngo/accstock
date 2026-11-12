import pandas as pd
import pytest

from app.services.stock_service import StockService, source_order
from tests.conftest import make_ohlcv


def test_source_order():
    assert source_order("auto") == ["vietcap", "ssi"]       # default: Vietcap first, SSI fallback
    assert source_order("") == source_order("whatever") == ["vietcap", "ssi"]
    assert source_order("VIETCAP") == ["vietcap"] and source_order("ssi") == ["ssi"]
    assert source_order("ssi_first") == ["ssi", "vietcap"]


class Src:
    def __init__(self, name, fail=False):
        self.name, self.fail, self.calls, self.configured = name, fail, 0, True

    def get_ohlcv(self, symbol, start, end, tf):
        self.calls += 1
        if self.fail:
            raise RuntimeError(f"{self.name} down")
        return make_ohlcv(50)


@pytest.fixture
def svc(monkeypatch):
    s = StockService()
    s.s = s.s.model_copy(update={"DATA_SOURCE": "auto"})
    monkeypatch.setattr(s, "_read_cache", lambda *a, **k: None)
    written = []
    monkeypatch.setattr(s, "_write_cache", lambda sym, tf, df, src: written.append(src))
    s.written = written
    return s


def test_auto_uses_vietcap_first(svc):
    svc.vietcap, svc.ssi = Src("vietcap"), Src("ssi")
    from app.services.timeframes import get_tf

    df, source = svc._get_rest("FPT", get_tf("1D"), None, False)
    assert source == "vietcap" and svc.vietcap.calls == 1 and svc.ssi.calls == 0 and svc.written == ["vietcap"]
    assert isinstance(df, pd.DataFrame) and not df.empty


def test_auto_falls_back_to_ssi(svc):
    from app.services.timeframes import get_tf

    svc.vietcap, svc.ssi = Src("vietcap", fail=True), Src("ssi")
    _, source = svc._get_rest("FPT", get_tf("1h"), None, False)
    assert source == "ssi" and svc.vietcap.calls == 1 and svc.ssi.calls == 1


def test_both_down_reports_both(svc):
    from app.services.timeframes import get_tf

    svc.vietcap, svc.ssi = Src("vietcap", fail=True), Src("ssi", fail=True)
    with pytest.raises(RuntimeError, match="vietcap: vietcap down; ssi: ssi down"):
        svc._get_rest("FPT", get_tf("1D"), None, False)
