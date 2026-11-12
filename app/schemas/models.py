"""Pydantic request/response models."""
from pydantic import BaseModel, Field


class Bar(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class OHLCVResponse(BaseModel):
    symbol: str
    timeframe: str = "1D"
    source: str
    count: int
    data: list[Bar]


class ScanRequest(BaseModel):
    symbols: list[str] | None = Field(default=None, description="Empty = use WATCHLIST from .env")
    min_score: int = -100
    refresh: bool = False
    timeframe: str = Field(default="1D", pattern="^(5m|15m|30m|1h|60m|1D|1W)$")


class ScanRow(BaseModel):
    symbol: str
    date: str | None = None
    close: float | None = None
    change_pct: float | None = None
    score: int | None = None
    rating: str | None = None
    price_vs_cloud: str | None = None
    rsi: float | None = None
    macd_cross: str | None = None
    vol_ratio: float | None = None
    breakout: bool | None = None
    cycle_hit: list[int] = []
    error: str | None = None


class ScanResponse(BaseModel):
    count: int
    results: list[ScanRow]


class AIRequest(BaseModel):
    question: str | None = None
    refresh: bool = False
    timeframe: str = Field(default="1D", pattern="^(5m|15m|30m|1h|60m|1D|1W)$")


class AIResponse(BaseModel):
    symbol: str
    model: str
    commentary: str
    analysis: dict
