"""Sector groups shown on the price board (Bảng điện).

`DEFAULT_SECTORS` is a hand-picked list of the liquid stocks in each sector, in board order. The user can
replace it from the board (Excel import), which writes data/sectors.json; `get_sectors()` returns that file
when it exists, else the defaults. A symbol the price feed doesn't know is simply skipped.
The board always starts with two pinned tables: "Quan Tâm" (the user's list, kept in the browser) then "VN30"
(the list's own "VN30" group, else the live index basket); see `split_pinned`.

Excel / CSV layout (one row per stock, header optional):
    Ngành        Mã
    Ngân hàng    BID
    Chứng khoán  SSI
"""
from __future__ import annotations

import csv
import io
import json
import re
import threading
import unicodedata
from pathlib import Path

DEFAULT_SECTORS: list[tuple[str, list[str]]] = [
    ("Ngân hàng", ["VCB", "BID", "CTG", "TCB", "MBB", "ACB", "VPB", "HDB", "STB", "SHB", "LPB", "VIB", "TPB",
                   "SSB", "MSB", "OCB", "EIB", "NAB", "ABB", "BAB", "VAB", "KLB", "BVB", "PGB", "SGB"]),
    ("Chứng khoán", ["SSI", "VND", "VCI", "HCM", "TCX", "SHS", "MBS", "VIX", "FTS", "BSI", "CTS", "AGR", "ORS",
                     "TVS", "VDS", "BVS", "DSC", "APG"]),
    ("Bất động sản", ["VHM", "NVL", "KDH", "NLG", "DXG", "DIG", "PDR", "CEO", "HDG", "TCH", "NTL", "HDC", "SCR",
                      "DXS", "AGG", "HQC", "QCG", "KHG", "NBB", "LDG", "VPI", "CRE", "HPX"]),
    ("Họ Vingroup", ["VIC", "VHM", "VRE", "VPL"]),
    ("Thép", ["HPG", "HSG", "NKG", "TLH", "SMC", "POM", "VGS", "TVN", "TIS", "HMC"]),
    ("Vật liệu xây dựng", ["VCS", "HT1", "BCC", "DHA", "VLB", "PLC", "CVT", "BTS", "DTL", "KSB"]),
    ("Xây dựng", ["CTD", "HBC", "VCG", "FCN", "C4G", "LCG", "HHV", "CII", "DPG", "G36", "HUT", "TCD"]),
    ("Khu công nghiệp", ["KBC", "IDC", "SZC", "BCM", "VGC", "NTC", "LHG", "SIP", "TIP", "D2D", "SNZ", "IJC"]),
    ("Đa ngành", ["GEX", "REE", "PET", "HHS", "SAM", "TTH"]),
    ("Bán lẻ", ["MWG", "FRT", "DGW", "PNJ", "PSD", "PET"]),
    ("Công nghệ viễn thông", ["FPT", "CMG", "CTR", "VGI", "FOX", "ELC", "ITD", "VTK"]),
    ("Dầu khí", ["GAS", "PVD", "PVS", "BSR", "PVC", "PVB", "OIL", "PVT"]),
    ("Xăng dầu & khí đốt", ["PLX", "OIL", "PGC", "CNG", "PGS", "PVG", "ASP", "PGD"]),
    ("Điện", ["POW", "PC1", "NT2", "GEG", "QTP", "HND", "PPC", "VSH", "TV2", "CHP", "SBA", "BCG", "GEE"]),
    ("Nước", ["BWE", "TDM", "DNW", "TDW"]),
    ("Hóa chất", ["DGC", "CSV", "DDV", "LIX", "NET", "HVT", "PAT"]),
    ("Phân bón & BVTV", ["DCM", "DPM", "BFC", "LAS", "SFG", "VFG", "NFC"]),
    ("Cao su", ["GVR", "PHR", "DPR", "DRI", "TRC", "HRC", "TNC"]),
    ("Nhựa", ["BMP", "NTP", "AAA", "APH", "DAG", "HCD"]),
    ("Ô tô & phụ tùng", ["HAX", "SVC", "TMT", "CTF", "VEA", "DRC", "CSM", "SRC"]),
    ("Thiết bị điện", ["GEE", "CAV", "PAC", "TYA", "RAL"]),
    ("Cảng & kho bãi", ["GMD", "SGP", "VSC", "PHP", "DVP", "CDN", "TCL", "VTP", "STG"]),
    ("Vận tải biển", ["HAH", "VOS", "PVT", "GSP", "VIP", "VTO"]),
    ("Hàng không", ["HVN", "VJC", "ACV", "SCS", "AST", "NCT", "SAS", "SGN"]),
    ("Khai khoáng", ["MSR", "BMC", "KSV", "TVD", "HLC", "NBC", "KSB"]),
    ("Thủy sản", ["VHC", "ANV", "IDI", "FMC", "CMX", "MPC", "ACL", "ABT"]),
    ("Dệt may", ["TCM", "TNG", "MSH", "VGT", "STK", "GIL", "PPH"]),
    ("Thực phẩm", ["MSN", "KDC", "PAN", "LTG", "TAR", "NAF", "SBT", "QNS", "LSS"]),
    ("Sữa & đồ uống", ["VNM", "MCM", "SAB", "BHN", "SMB", "HAD", "WSB"]),
    ("Chăn nuôi", ["DBC", "HAG", "BAF", "MML", "VLC"]),
    ("Bảo hiểm", ["BVH", "BMI", "PVI", "MIG", "PGI", "BIC", "VNR"]),
    ("Dược phẩm & y tế", ["DHG", "IMP", "DBD", "DMC", "TRA", "DVN", "AMV"]),
    ("Đồ gia dụng & văn phòng phẩm", ["PTB", "GDT", "SHI", "TLG", "EVE"]),
]


SECTORS = DEFAULT_SECTORS  # backwards-compatible name

Sectors = list[tuple[str, list[str]]]
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,10}$")
_lock = threading.Lock()
_cache: dict = {"mtime": None, "sectors": None}


def sectors_path() -> Path:
    from app.core.config import get_settings

    return Path(get_settings().DATA_DIR) / "sectors.json"


def get_sectors() -> Sectors:
    """The user's sectors (data/sectors.json) if present, else the defaults. Re-read when the file changes."""
    p = sectors_path()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return DEFAULT_SECTORS
    with _lock:
        if _cache["mtime"] != mtime:
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                _cache["sectors"] = [(g["name"], list(g["symbols"])) for g in raw["sectors"]]
            except Exception:  # noqa: BLE001  (corrupt file: fall back to the defaults)
                _cache["sectors"] = DEFAULT_SECTORS
            _cache["mtime"] = mtime
        return _cache["sectors"]


def is_custom() -> bool:
    return sectors_path().exists()


def save_sectors(sectors: Sectors) -> None:
    p = sectors_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"sectors": [{"name": n, "symbols": s} for n, s in sectors]}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def reset_sectors() -> None:
    sectors_path().unlink(missing_ok=True)


def all_symbols(sectors: Sectors | None = None) -> list[str]:
    seen: dict[str, None] = {}
    for _, syms in (sectors if sectors is not None else get_sectors()):
        for s in syms:
            seen.setdefault(s.upper(), None)
    return list(seen)


# ---------- the two pinned tables: "Quan Tâm" (the user's own list) and "VN30" ----------
WATCH_NAME, VN30_NAME = "Quan Tâm", "VN30"
_WATCH_KEYS = {"quan tam", "danh muc", "danh muc cua toi", "watchlist"}


def split_pinned(sectors: Sectors) -> tuple[list[str] | None, list[str] | None, Sectors]:
    """(watch symbols, VN30 symbols, other sectors) of a list: the pinned groups are found by name, ignoring
    case/accents; None when the list has no such group."""
    watch = vn30 = None
    rest: Sectors = []
    for name, syms in sectors:
        key = _norm(name)
        if key in _WATCH_KEYS:
            watch = list(dict.fromkeys((watch or []) + list(syms)))
        elif key == "vn30":
            vn30 = list(dict.fromkeys((vn30 or []) + list(syms)))
        else:
            rest.append((name, syms))
    return watch, vn30, rest


# ---------- Excel / CSV ----------
def _norm(text) -> str:
    s = unicodedata.normalize("NFD", str(text or "")).replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").strip().lower()


def to_xlsx(sectors: Sectors) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Nganh"
    ws.append(["Ngành", "Mã"])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="4C8DFF")
    for name, syms in sectors:
        for s in syms:
            ws.append([name, s])
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 10
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _rows(content: bytes, filename: str) -> list[list]:
    if content[:2] == b"PK" or filename.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    text = content.decode("utf-8-sig", errors="replace")
    sample = text[:2000]
    delim = "\t" if "\t" in sample else ";" if sample.count(";") > sample.count(",") else ","
    return [r for r in csv.reader(io.StringIO(text), delimiter=delim)]


def parse_table(content: bytes, filename: str = "") -> tuple[Sectors, list[str]]:
    """Parse an Excel/CSV 'Ngành | Mã' table. Returns (sectors in file order, warnings)."""
    # keep the spreadsheet row number for the warnings, skip empty rows
    rows = [(n, r) for n, r in enumerate(_rows(content, filename), 1) if any(str(c or "").strip() for c in r)]
    if not rows:
        raise ValueError("File không có dữ liệu.")
    col_sec, col_sym, start = 0, 1, 0
    head = [_norm(c) for c in rows[0][1]]
    if any(h in ("nganh", "ma", "ma ck", "ma cp", "sector", "symbol") for h in head):
        start = 1
        for i, h in enumerate(head):
            if h in ("nganh", "sector", "nhom", "nhom nganh"):
                col_sec = i
            elif h in ("ma", "ma ck", "ma cp", "symbol", "ticker"):
                col_sym = i
    sectors: dict[str, list[str]] = {}
    names: dict[str, str] = {}   # normalised name -> name as first written (so "Ngân Hàng" == "ngân hàng")
    warnings: list[str] = []
    for n, r in rows[start:]:
        sec = str(r[col_sec] if col_sec < len(r) and r[col_sec] is not None else "").strip()
        sym = str(r[col_sym] if col_sym < len(r) and r[col_sym] is not None else "").strip().upper()
        if not sec or not sym:
            warnings.append(f"Dòng {n}: thiếu ngành hoặc mã, bỏ qua")
            continue
        if not SYMBOL_RE.match(sym):
            warnings.append(f"Dòng {n}: mã '{sym}' không hợp lệ, bỏ qua")
            continue
        key = _norm(sec)
        name = names.setdefault(key, sec)
        syms = sectors.setdefault(name, [])
        if sym in syms:
            warnings.append(f"Dòng {n}: {sym} bị lặp trong ngành {name}, bỏ qua")
        else:
            syms.append(sym)
    if not sectors:
        raise ValueError("Không đọc được dòng nào hợp lệ. Cần 2 cột: Ngành, Mã.")
    return list(sectors.items()), warnings
