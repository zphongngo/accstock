"""Claude AI commentary endpoint."""
from fastapi import APIRouter, Depends, HTTPException

from app.analysis.stock_scanner import analyze_dataframe
from app.api.deps import get_claude_service, get_stock_service
from app.schemas.models import AIRequest, AIResponse
from app.services.claude_service import ClaudeService
from app.services.stock_service import StockService

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/status")
def status(ai: ClaudeService = Depends(get_claude_service)):
    return {"configured": ai.configured, "model": ai.s.CLAUDE_MODEL}


@router.post("/analyze/{symbol}", response_model=AIResponse)
def ai_analyze(
    symbol: str,
    req: AIRequest | None = None,
    svc: StockService = Depends(get_stock_service),
    ai: ClaudeService = Depends(get_claude_service),
):
    req = req or AIRequest()
    if not ai.configured:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY chưa được cấu hình trong .env")
    try:
        df, source = svc.get_ohlcv(symbol, refresh=req.refresh, timeframe=req.timeframe)
        analysis = analyze_dataframe(symbol, df, req.timeframe)
        analysis["source"] = source
        text = ai.analyze(analysis, req.question)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"symbol": symbol.upper(), "model": ai.s.CLAUDE_MODEL, "commentary": text, "analysis": analysis}
