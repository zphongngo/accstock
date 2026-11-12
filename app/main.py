"""PStock — FastAPI entry point."""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import ai, analysis, board, market
from app.api.deps import get_board_service, get_stock_service
from app.core.config import get_settings
from app.core.netaddr import install_uvicorn_banner
from app.core.security import mask, verify_api_key
from app.services.board_scheduler import run_board_scheduler
from app.services.bo_scheduler import run_bo_scheduler
from app.services.telegram_bot import run_telegram_bot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per SSI page request otherwise
log = logging.getLogger("pstock")
install_uvicorn_banner()  # "running on http://0.0.0.0:8000" -> http://127.0.0.1:8000 + the LAN addresses

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    log.info("SSI consumer ID: %s | Claude key: %s | model: %s | source: %s",
             mask(settings.SSI_CONSUMER_ID), mask(settings.ANTHROPIC_API_KEY),
             settings.CLAUDE_MODEL, settings.DATA_SOURCE)
    stop_scheduler = asyncio.Event()
    scheduler = asyncio.create_task(run_board_scheduler(stop_scheduler))
    bo_scheduler = asyncio.create_task(run_bo_scheduler(stop_scheduler))
    bot = asyncio.create_task(run_telegram_bot(stop_scheduler))
    try:
        yield
    finally:
        stop_scheduler.set()
        await scheduler
        await bo_scheduler
        await bot          # at most one long poll (~10 s)
    get_stock_service().close()  # disconnect the SSI stream
    if get_board_service.cache_info().currsize:
        get_board_service().close()


app = FastAPI(title=settings.APP_NAME, version="1.0.0", lifespan=lifespan)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

protected = [Depends(verify_api_key)]
app.include_router(market.router, dependencies=protected)
app.include_router(analysis.router, dependencies=protected)
app.include_router(ai.router, dependencies=protected)
app.include_router(board.router, dependencies=protected)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard(request: Request):
    return templates.TemplateResponse(
        request, "dashboard.html",
        {"app_name": settings.APP_NAME, "watchlist": settings.watchlist,
         "needs_key": bool(settings.APP_API_KEY)},
    )


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME}
