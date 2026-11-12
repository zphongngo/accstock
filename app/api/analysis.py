"""Technical analysis endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from app.analysis.ichimoku import IchiParams, custom_params
from app.analysis.stock_scanner import analyze_dataframe, chart_payload
from app.analysis.wyckoff import analyze_wyckoff
from app.api.deps import get_stock_service
from app.api.market import TF_PATTERN
from app.core.config import get_settings
from app.schemas.models import ScanRequest, ScanResponse, ScanRow
from app.services.stock_service import StockService

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


def ichi_params(tenkan: int | None = Query(None, ge=1, le=300), kijun: int | None = Query(None, ge=1, le=300),
                senkou_a: int | None = Query(None, ge=1, le=300, description="độ dịch mây (Senkou A/B) tới trước"),
                senkou_b: int | None = Query(None, ge=1, le=300), chikou: int | None = Query(None, ge=1, le=300)
                ) -> IchiParams:
    """Ichimoku Trịnh Phát tuỳ chỉnh của biểu đồ Cổ phiếu; tham số bỏ trống = thiết lập mặc định của ứng dụng."""
    return custom_params(tenkan=tenkan, kijun=kijun, senkou_a=senkou_a, senkou_b=senkou_b, chikou=chikou)


@router.post("/eod/import")
async def import_eod(request: Request, filename: str = Query("", max_length=200),
                     svc: StockService = Depends(get_stock_service)):
    """Import End-Of-Day history (CafeF CSV <Ticker>,<DTYYYYMMDD>,<Open>,<High>,<Low>,<Close>,<Volume>, or a .zip
    of such files; raw request body) into the 1D cache. Only 3-character stock tickers are kept."""
    from app.services.eod_import import parse_eod

    content = await request.body()

    def run() -> dict:
        bars, stats = parse_eod(content, filename)
        return {**stats, **svc.import_eod(bars)}

    try:
        return await run_in_threadpool(run)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/{symbol}")
def analyze(symbol: str, timeframe: str = Query("1D", pattern=TF_PATTERN), refresh: bool = False,
            p: IchiParams = Depends(ichi_params), svc: StockService = Depends(get_stock_service)):
    try:
        df, source = svc.get_ohlcv(symbol, refresh=refresh, timeframe=timeframe)
        result = analyze_dataframe(symbol, df, timeframe, p)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)) from e
    result["source"] = source
    return result


@router.get("/{symbol}/wyckoff")
def wyckoff(symbol: str, timeframe: str = Query("1D", pattern="^(1D|1W)$"),
            start: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
            end: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
            box: float | None = Query(None, gt=0), reversal: int = Query(3, ge=1, le=5),
            tol: float = Query(3.0, gt=0, le=15), direction: str = Query("auto", pattern="^(auto|up|down)$"),
            svc: StockService = Depends(get_stock_service)):
    """Mục tiêu Wyckoff (đếm ngang P&F) + vùng hợp lưu với Ichimoku Ngày / Tuần."""
    try:
        df, source = svc.get_ohlcv(symbol, timeframe="1D")
        result = analyze_wyckoff(symbol, df, timeframe, start=start, end=end, box=box, reversal=reversal,
                                 tol_pct=tol, direction=direction)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)) from e
    result["source"] = source
    return result


@router.get("/{symbol}/chart")
def chart(symbol: str, timeframe: str = Query("1D", pattern=TF_PATTERN), bars: int = Query(300, ge=50, le=5000),
          live: bool = True, p: IchiParams = Depends(ichi_params), svc: StockService = Depends(get_stock_service)):
    try:
        if live:
            svc.realtime.watch(symbol)  # start streaming this symbol (no-op if SSI stream is off)
        df, source = svc.get_ohlcv(symbol, timeframe=timeframe)
        payload = chart_payload(df, bars=bars, timeframe=timeframe, p=p)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)) from e
    payload.update({"symbol": symbol.upper(), "source": source})
    return payload


@router.post("/scan", response_model=ScanResponse)
def scan(req: ScanRequest, svc: StockService = Depends(get_stock_service)):
    symbols = [s.upper().strip() for s in (req.symbols or get_settings().watchlist) if s.strip()]
    rows: list[ScanRow] = []
    for sym in symbols:
        try:
            df, _ = svc.get_ohlcv(sym, refresh=req.refresh, timeframe=req.timeframe)
            a = analyze_dataframe(sym, df, req.timeframe)
            cyc = a["ichimoku"]["time_cycles"]
            rows.append(ScanRow(
                symbol=sym, date=a["date"], close=a["close"], change_pct=a["change_pct"],
                score=a["score"], rating=a["rating"],
                price_vs_cloud=a["ichimoku"]["price_vs_cloud"], rsi=a["rsi"]["rsi"],
                macd_cross=a["macd"]["cross"], vol_ratio=a["volume_breakout"]["vol_ratio"],
                breakout=a["volume_breakout"]["breakout_today"],
                cycle_hit=sorted(set(cyc["from_high"]["cycle_hit"] + cyc["from_low"]["cycle_hit"])),
            ))
        except Exception as e:  # noqa: BLE001
            rows.append(ScanRow(symbol=sym, error=str(e)))
    ok = sorted([r for r in rows if r.error is None and r.score >= req.min_score], key=lambda r: -r.score)
    failed = [r for r in rows if r.error is not None]
    results = ok + failed
    return {"count": len(results), "results": results}
