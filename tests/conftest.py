import numpy as np
import pandas as pd
import pytest


def make_ohlcv(n: int = 300, seed: int = 7, trend: float = 0.0008) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-06-02", periods=n)
    close = 50000 * np.exp(np.cumsum(rng.normal(trend, 0.015, n)))
    open_ = close * (1 + rng.normal(0, 0.004, n))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.01, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.01, n))
    vol = rng.integers(500_000, 2_000_000, n).astype(float)
    return pd.DataFrame({"date": dates, "open": open_, "high": high, "low": low, "close": close, "volume": vol})


@pytest.fixture(autouse=True)
def _isolated_sectors_file(tmp_path, monkeypatch):
    """Never read or write the user's data/sectors.json (Excel import) from tests: default list unless a test saves."""
    from app.services import sectors

    from app.services import board_settings

    path = tmp_path / "sectors.json"
    monkeypatch.setattr(sectors, "sectors_path", lambda: path)
    # same for the Quan Tâm list / threshold shared with the scheduled alerts (data/board_settings.json)
    monkeypatch.setattr(board_settings, "settings_path", lambda: tmp_path / "board_settings.json")
    # and the price board's last-session snapshot (data/cache/board_last_session.json)
    from app.services.priceboard_service import PriceBoardService

    monkeypatch.setattr(PriceBoardService, "_snapshot_path", lambda self: tmp_path / "board_last_session.json")
    return path


@pytest.fixture
def ohlcv() -> pd.DataFrame:
    return make_ohlcv()
