"""Import of End-Of-Day history files (CafeF "SolieuGD" format) into the 1D cache.

Columns: <Ticker>,<DTYYYYMMDD>,<Open>,<High>,<Low>,<Close>,<Volume> (extra columns are ignored). A .zip holding such
CSV files (CafeF "Upto" archives) is read file by file. CafeF prices are in thousand VND; the app stores VND.
Only 3-character tickers are kept: covered warrants / ETFs (8 characters) and indexes (points, not prices) are skipped.
"""
from __future__ import annotations

import io
import re
import zipfile

import pandas as pd

REQUIRED = ["ticker", "dtyyyymmdd", "open", "high", "low", "close", "volume"]
STOCK_RE = re.compile(r"^[A-Z0-9]{3}$")
PRICES = ["open", "high", "low", "close"]


def _norm(col: str) -> str:
    return str(col).strip().lstrip("﻿").strip("<>").strip().lower()


def _read_csv(raw: bytes, name: str) -> pd.DataFrame:
    try:
        df = pd.read_csv(io.BytesIO(raw), encoding="utf-8-sig", dtype=str, skipinitialspace=True)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"{name}: không đọc được CSV ({e})") from e
    df.columns = [_norm(c) for c in df.columns]
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"{name}: thiếu cột {', '.join('<' + c.capitalize() + '>' for c in missing)} "
                         "(cần <Ticker>,<DTYYYYMMDD>,<Open>,<High>,<Low>,<Close>,<Volume>)")
    return df[REQUIRED]


def read_files(content: bytes, filename: str) -> list[tuple[str, pd.DataFrame]]:
    """(name, raw table) for a CSV upload, or for every CSV inside a .zip upload."""
    if content[:4] == b"PK\x03\x04" or filename.lower().endswith(".zip"):
        try:
            zf = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile as e:
            raise ValueError(f"{filename}: file .zip bị hỏng") from e
        names = [n for n in zf.namelist() if n.lower().endswith((".csv", ".txt")) and not n.endswith("/")]
        if not names:
            raise ValueError(f"{filename}: trong file .zip không có file .csv nào")
        return [(n, _read_csv(zf.read(n), n)) for n in names]
    return [(filename or "file", _read_csv(content, filename or "file"))]


def parse_eod(content: bytes, filename: str = "") -> tuple[pd.DataFrame, dict]:
    """Uploaded bytes -> (bars, stats). Bars: symbol, date, open, high, low, close, volume in VND, one row per
    (symbol, date), sorted. Raises ValueError when nothing usable is found."""
    if not content:
        raise ValueError("File rỗng")
    parts, stats = [], {"files": [], "rows": 0, "skipped_cw": 0, "skipped_other": 0, "bad_rows": 0}
    for name, raw in read_files(content, filename):
        stats["files"].append(name)
        stats["rows"] += len(raw)
        t = raw["ticker"].fillna("").str.strip().str.upper()
        is_stock = t.str.match(STOCK_RE)
        stats["skipped_cw"] += int((t.str.len() == 8).sum())
        stats["skipped_other"] += int((~is_stock & (t.str.len() != 8)).sum())
        df = pd.DataFrame({"symbol": t, "date": pd.to_datetime(raw["dtyyyymmdd"].str.strip(), format="%Y%m%d",
                                                               errors="coerce")})
        for c in PRICES + ["volume"]:
            df[c] = pd.to_numeric(raw[c].str.replace(",", "", regex=False), errors="coerce")
        df = df[is_stock]
        ok = df["date"].notna() & df[PRICES].notna().all(axis=1) & (df[PRICES] > 0).all(axis=1)
        stats["bad_rows"] += int((~ok).sum())
        df = df[ok].copy()
        if df.empty:
            continue
        # CafeF quotes in thousand VND (FPT 50.9); a file already in VND (median > 1,000) is kept as is.
        if df["close"].median() < 1000:
            df[PRICES] = df[PRICES] * 1000
        df[PRICES] = df[PRICES].round(3)
        df["high"] = df[["open", "high", "low", "close"]].max(axis=1)   # repair inconsistent rows
        df["low"] = df[["open", "high", "low", "close"]].min(axis=1)
        df["volume"] = df["volume"].fillna(0).clip(lower=0).round()
        parts.append(df)
    if not parts:
        raise ValueError("Không có dòng dữ liệu cổ phiếu hợp lệ nào trong file")
    out = (pd.concat(parts).drop_duplicates(["symbol", "date"], keep="last")
           .sort_values(["symbol", "date"]).reset_index(drop=True))
    stats.update(symbols=int(out["symbol"].nunique()), bars=len(out),
                 first=out["date"].min().strftime("%Y-%m-%d"), last=out["date"].max().strftime("%Y-%m-%d"))
    return out, stats
