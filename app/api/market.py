"""Market data endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_stock_service
from app.core.config import get_settings
from app.schemas.models import OHLCVResponse
from app.services.stock_service import StockService
from app.services.timeframes import TIMEFRAMES, chart_time, fmt_time, get_tf

router = APIRouter(prefix="/api/market", tags=["market"])

TF_PATTERN = "^(5m|15m|30m|1h|60m|1D|1W)$"


@router.get("/timeframes")
def timeframes():
    return {"timeframes": [{"key": t.key, "label": t.label, "intraday": t.intraday} for t in TIMEFRAMES.values()]}


@router.get("/ohlcv/{symbol}", response_model=OHLCVResponse)
def get_ohlcv(
    symbol: str,
    timeframe: str = Query("1D", pattern=TF_PATTERN),
    days: int | None = Query(None, ge=1, le=3650, description="Calendar days; default depends on timeframe"),
    refresh: bool = False,
    svc: StockService = Depends(get_stock_service),
):
    tf = get_tf(timeframe)
    try:
        df, source = svc.get_ohlcv(symbol, days=days, refresh=refresh, timeframe=tf.key)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)) from e
    out = df.copy()
    out["date"] = [fmt_time(d, tf) for d in out["date"]]
    return {"symbol": symbol.upper(), "timeframe": tf.key, "source": source, "count": len(out),
            "data": out.to_dict("records")}


@router.get("/live/{symbol}")
def live(
    symbol: str,
    timeframe: str = Query("1D", pattern=TF_PATTERN),
    svc: StockService = Depends(get_stock_service),
):
    """Last 2 candles (REST + SSI stream) for incremental chart updates. Subscribes the symbol."""
    tf = get_tf(timeframe)
    try:
        rows, watching = svc.live_bars(symbol, tf.key)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)) from e
    candles = [{"time": chart_time(r["date"], tf), "label": fmt_time(r["date"], tf),
                "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
                "close": float(r["close"]), "volume": float(r["volume"])} for r in rows]
    return {"symbol": symbol.upper(), "timeframe": tf.key, "streaming": watching,
            "stream": svc.realtime.status(), "candles": candles}


@router.get("/stream/status")
def stream_status(svc: StockService = Depends(get_stock_service)):
    return svc.realtime.status()


@router.get("/watchlist")
def watchlist():
    return {"symbols": get_settings().watchlist}
