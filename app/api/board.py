"""Price board (Bảng điện) endpoints."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import get_board_service, get_stock_service, get_telegram_service
from app.services import board_settings as bs
from app.services import sectors as sec

router = APIRouter(prefix="/api/board", tags=["board"])


def _symbols(text: str) -> list[str]:
    return [s.strip().upper() for s in text.replace(";", ",").split(",") if s.strip()][:100]


@router.get("")
def board(
    watch: str = Query("", description="Comma-separated symbols for the 'Danh mục của tôi' group"),
    svc=Depends(get_board_service),
):
    """Live quotes grouped by sector, index cards, and the 1D score per symbol (filled in the background)."""
    try:
        return svc.board(_symbols(watch))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Không tải được bảng giá: {e}") from e


@router.get("/indexes")
def indexes(svc=Depends(get_board_service)):
    """Only the index cards (VNINDEX, VN30, HNX, HNX30, UPCOM) for the global header shown on every tab."""
    try:
        return svc.indexes()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Không tải được chỉ số: {e}") from e


@router.get("/index-chart")
def index_chart(interval: int = Query(1, description="1 or 3 minutes"), svc=Depends(get_board_service)):
    """Intraday line of VNINDEX, VN30, HNX, HNX30, UPCOM (latest session) with the reference level and volume."""
    if interval not in (1, 3):
        raise HTTPException(status_code=422, detail="interval phải là 1 hoặc 3")
    try:
        return svc.index_charts(interval)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Không tải được đồ thị chỉ số: {e}") from e


@router.get("/strength")
def strength(watch: str = Query("", description="The browser's 'Quan Tâm' list, added to the universe"),
             svc=Depends(get_board_service)):
    """Scanner: RS Rating (1–99), technical score (0–100), Trend Template, sector strength and the composite rank
    of every stock on the board. Computed in the background (once a day); poll until progress.done == total."""
    try:
        return svc.strength(_symbols(watch))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Không tính được sức mạnh cổ phiếu: {e}") from e


@router.get("/acc")
def acc_ranking(watch: str = Query("", description="The browser's 'Quan Tâm' list, added to the universe"),
                svc=Depends(get_board_service)):
    """ACC Stock Strength Monitor V1: RS vs VN30 (1M/3M/6M/12M percentiles), Trend, Momentum, Money Flow, Ichimoku
    -> Swing / Position / Combined (0–100), plus Setup, Risk, signal and the change vs the previous session.
    Shares the board's background worker with /strength (POST /strength/refresh recomputes both)."""
    try:
        return svc.acc(_symbols(watch))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Không tính được xếp hạng ACC: {e}") from e


@router.post("/strength/refresh")
def refresh_strength(svc=Depends(get_board_service)):
    """Recompute today's scores and strength (e.g. late in the session, with the latest prices)."""
    if not svc.refresh_scores():
        raise HTTPException(status_code=409, detail="Đang tính, vui lòng đợi lượt hiện tại xong.")
    return {"ok": True}


class SettingsUpdate(BaseModel):
    watch: list[str] | None = Field(None, description="The Quan Tâm list")
    threshold: float | None = Field(None, gt=0, le=15)
    auto_board: bool | None = Field(None, description="Interval-based weekday board bulletin on/off")
    auto_daily: bool | None = Field(None, description="15:05 Ichimoku BO/BD alert on/off")
    board_start: str | None = Field(None, pattern=r"^\d{2}:\d{2}$", description="Board bulletin start time")
    board_end: str | None = Field(None, pattern=r"^\d{2}:\d{2}$", description="Board bulletin end time")
    board_interval: int | None = Field(None, ge=1, le=720, description="Minutes between board bulletins")
    auto_bo: bool | None = Field(None, description="Intraday Ichimoku BO bulletin (30m/60m) on/off")
    bo_tf: str | None = Field(None, description="Timeframe of the intraday BO bulletin: 30m or 60m")


@router.get("/settings")
def get_board_settings():
    """The Quan Tâm list, the strong/weak threshold and the auto-send switches, shared by the browser and the
    scheduled Telegram jobs."""
    return bs.load()


@router.get("/settings/schedule")
def get_board_schedule():
    """The effective bulletin schedule for the Bảng điện panel: tick state, slot count per day, the next slot and
    the last one sent. Read from the saved settings, so the panel can show what the scheduler will actually do."""
    from app.services.board_scheduler import schedule_info

    return schedule_info()


@router.put("/settings")
def put_board_settings(req: SettingsUpdate):
    try:
        return bs.save(watch=req.watch, threshold=req.threshold, auto_board=req.auto_board, auto_daily=req.auto_daily,
                       board_start=req.board_start, board_end=req.board_end, board_interval=req.board_interval,
                       auto_bo=req.auto_bo, bo_tf=req.bo_tf)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


class MoversRequest(BaseModel):
    threshold: float = Field(3.0, gt=0, le=15, description="% change vs reference price")
    watch: str = ""
    dry_run: bool = Field(False, description="Only build the message, don't send it")


class DailyRequest(BaseModel):
    dry_run: bool = Field(False, description="Only build the message, don't send it")


@router.post("/daily")
def send_daily(req: DailyRequest, svc=Depends(get_board_service), stock=Depends(get_stock_service),
               tg=Depends(get_telegram_service)):
    """Ichimoku BO/BD bulletin for every board symbol (VN30 + sector tables + Quan Tâm, each once), sorted by
    score high -> low. Before 15:05 it is built for the previous trading day."""
    if not req.dry_run and not tg.configured:
        raise HTTPException(status_code=400, detail="Telegram chưa cấu hình: điền TELEGRAM_BOT_TOKEN và TELEGRAM_CHAT_ID trong telegram.env")
    try:
        from datetime import datetime, timedelta
        from app.analysis.daily_signals import board_symbols, build_message, scan

        symbols = board_symbols(svc, bs.load()["watch"])
        now = datetime.now()
        target = now.date()
        if (now.hour, now.minute) < (15, 5):   # session not closed yet: previous trading day
            target -= timedelta(days=1)
            while target.weekday() >= 5:
                target -= timedelta(days=1)
        results, errors = scan(stock, symbols, "1D", until=target)
        msg = build_message(results, errors, target.strftime("%d/%m/%Y"), timeframe="1D", scanned=len(symbols))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Không tải được tín hiệu: {e}") from e
    sent = 0
    if not req.dry_run:
        try:
            sent = tg.send(msg)
        except Exception as e:  # noqa: BLE001  (TelegramError never contains the token)
            raise HTTPException(status_code=502, detail=str(e)) from e
    return {"sent": sent, "chats": len(tg.chat_ids), "scanned": len(symbols), "message": msg}


class BoRequest(BaseModel):
    timeframe: str = Field("1h", description="30m or 60m (1h)")
    dry_run: bool = Field(False, description="Only build the message, don't send it")


@router.post("/bo")
def send_bo(req: BoRequest, svc=Depends(get_board_service), stock=Depends(get_stock_service),
            tg=Depends(get_telegram_service)):
    """Ichimoku BO (no BD) of every board symbol on the latest completed 30m / 60m bar. Sent to Telegram only when
    at least one symbol breaks out; the message is returned either way."""
    from app.analysis.daily_signals import board_symbols
    from app.analysis.intraday_bo import bo_tf, build_bo_message, scan_bo

    try:
        key = bo_tf(req.timeframe)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if not req.dry_run and not tg.configured:
        raise HTTPException(status_code=400, detail="Telegram chưa cấu hình: điền TELEGRAM_BOT_TOKEN và TELEGRAM_CHAT_ID trong telegram.env")
    try:
        symbols = board_symbols(svc, bs.load()["watch"])
        bo, errors, bar, _ = scan_bo(stock, symbols, key)
        msg = build_bo_message(bo, key, bar, len(symbols), errors)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Không quét được tín hiệu BO: {e}") from e
    sent = 0
    if bo and not req.dry_run:
        try:
            sent = tg.send(msg)
        except Exception as e:  # noqa: BLE001  (TelegramError never contains the token)
            raise HTTPException(status_code=502, detail=str(e)) from e
    return {"bo": len(bo), "sent": sent, "chats": len(tg.chat_ids), "scanned": len(symbols),
            "bar": bar.strftime("%Y-%m-%d %H:%M") if bar else None, "message": msg}


@router.post("/telegram")
def send_movers(req: MoversRequest, svc=Depends(get_board_service), tg=Depends(get_telegram_service)):
    """Send '1. Cổ phiếu mạnh (> +x%)' and '2. Cổ phiếu yếu (< -x%)' from the live board to Telegram.
    If the user requests a daily BO/BD bulletin via the UI, the endpoint also returns the latest Ichimoku BO/BD
    message for today, or the previous trading day when the session hasn't reached 15:05 yet. Sending still only
    covers the movers unless the UI explicitly asks for the daily report (which is the normal behaviour of the
    'Gửi ngay' button)."""
    if not req.dry_run and not tg.configured:
        raise HTTPException(status_code=400, detail="Telegram chưa cấu hình: điền TELEGRAM_BOT_TOKEN và "
                                                    "TELEGRAM_CHAT_ID trong telegram.env")
    try:
        watch = _symbols(req.watch) or bs.load()["watch"]
        messages, strong, weak = svc.movers_message(req.threshold, watch)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Không tải được bảng giá: {e}") from e
    sent = 0
    if not req.dry_run:
        try:
            for m in messages:  # each one is <= 4000 chars with whole <pre> tables
                sent += tg.send(m)
        except Exception as e:  # noqa: BLE001  (TelegramError never contains the token)
            raise HTTPException(status_code=502, detail=str(e)) from e
    return {"strong": strong, "weak": weak, "sent": sent, "chats": len(tg.chat_ids),
            "messages": len(messages), "message": "\n\n".join(messages)}


# ---------- sectors (Excel import / export) ----------
MAX_UPLOAD = 2 * 1024 * 1024
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _sectors_json() -> dict:
    s = sec.get_sectors()
    return {"custom": sec.is_custom(), "groups": len(s), "symbols": len(sec.all_symbols(s)),
            "sectors": [{"name": n, "symbols": syms} for n, syms in s]}


@router.get("/sectors")
def get_sectors():
    """Current sector list (the user's import, or the built-in default)."""
    return _sectors_json()


@router.get("/sectors.xlsx")
def export_sectors(watch: str = Query("", description="The browser's 'Quan Tâm' list"), svc=Depends(get_board_service)):
    """Download every board table as Excel: columns Ngành | Mã, one row per stock, starting with the pinned
    "Quan Tâm" and "VN30" tables, so export -> edit -> import keeps every table."""
    try:
        groups = svc.groups()
    except Exception:  # noqa: BLE001  (export the sectors even if the live VN30 basket is unavailable)
        _, vn30, rest = sec.split_pinned(sec.get_sectors())
        groups = ([(sec.VN30_NAME, vn30)] if vn30 else []) + rest
    w = _symbols(watch)
    sectors = ([(sec.WATCH_NAME, w)] if w else []) + [g for g in groups if g[1]]
    name = f"pstock_nganh_{date.today():%Y%m%d}.xlsx"
    return Response(sec.to_xlsx(sectors), media_type=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/sectors/import")
async def import_sectors(request: Request, filename: str = Query("", max_length=200), svc=Depends(get_board_service)):
    """Delete every sector table, then load the uploaded .xlsx / .csv (raw request body). The file is validated
    first, so a bad file deletes nothing. A "Quan Tâm" group is not stored here: it is returned as `watch` for the
    browser (which keeps that list); a "VN30" group replaces the live basket in the pinned VN30 table."""
    content = await request.body()
    if not content:
        raise HTTPException(status_code=400, detail="File rỗng.")
    if len(content) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="File quá lớn (tối đa 2 MB).")
    try:
        parsed, warnings = sec.parse_table(content, filename)
    except Exception as e:  # noqa: BLE001  (bad workbook, wrong columns...)
        raise HTTPException(status_code=400, detail=f"Không đọc được file: {e}") from e
    # 1) delete all current sector tables and the cached board, 2) import the file
    sec.reset_sectors()
    svc.clear()
    watch, vn30, rest = sec.split_pinned(parsed)
    sec.save_sectors(([(sec.VN30_NAME, vn30)] if vn30 else []) + rest)
    return {**_sectors_json(), "watch": watch, "warnings": warnings[:50], "warning_count": len(warnings)}


@router.delete("/sectors")
def reset_sectors(svc=Depends(get_board_service)):
    """Go back to the built-in sector list."""
    sec.reset_sectors()
    svc.clear()
    return _sectors_json()
