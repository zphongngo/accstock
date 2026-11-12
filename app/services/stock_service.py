"""Unified market-data access for every chart timeframe.

REST history: Vietcap first, SSI (ssi-sdk) fallback (DATA_SOURCE), cached as CSV in data/cache/<SYMBOL>_<TF>.csv.
Realtime: when the SSI stream is watching a symbol, today's streamed 1-minute bars are rolled up
and merged over the REST bars (the cache only ever holds REST data).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, timedelta

import pandas as pd

from app.core.config import get_settings
from app.services.realtime_service import RealtimeService
from app.services.ssi_service import SSIService
from app.services.timeframes import TF, get_tf, resample
from app.services.vietcap_service import VietcapService

log = logging.getLogger(__name__)


SOURCE_ORDERS = {
    "auto": ["vietcap", "ssi"],       # default: Vietcap first, SSI as fallback
    "vietcap": ["vietcap"],
    "ssi": ["ssi"],
    "ssi_first": ["ssi", "vietcap"],
}


IMPORT_SOURCE = "cafef"            # rows of the 1D cache that come from an End-Of-Day file import
OVERLAP_DAYS = {"1D": 10, "1W": 21}  # top-up overlap in calendar days (intraday: 3)
ADJUST_TOL = 0.005                   # > 0.5% gap on overlapping sessions = the source re-adjusted its history


def adjust_ratio(old: pd.DataFrame, new: pd.DataFrame) -> float | None:
    """Median new/old close on the sessions both have, skipping each side's last bar (may be in progress).
    None when they agree or there is nothing to compare."""
    if old is None or new is None or old.empty or new.empty:
        return None
    o = old[old["date"] < old["date"].max()][["date", "close"]]
    n = new[new["date"] < new["date"].max()][["date", "close"]]
    m = o.merge(n, on="date", suffixes=("_o", "_n"))
    m = m[(m["close_o"] > 0) & (m["close_n"] > 0)]
    if m.empty:
        return None
    r = float((m["close_n"] / m["close_o"]).median())
    return r if abs(r - 1) > ADJUST_TOL else None


def source_order(setting: str) -> list[str]:
    """REST sources to try, in order, for a DATA_SOURCE value (unknown values behave like 'auto')."""
    return SOURCE_ORDERS.get((setting or "auto").strip().lower(), SOURCE_ORDERS["auto"])


class StockService:
    def __init__(self) -> None:
        self.s = get_settings()
        self.ssi = SSIService()
        self.vietcap = VietcapService()
        self.realtime = RealtimeService(self.ssi)
        self._lock = threading.RLock()   # cache read-merge-write (board worker threads vs EOD import)

    # ---------- cache ----------
    def _cache_path(self, symbol: str, tf: TF):
        return self.s.cache_dir / f"{symbol.upper()}_{tf.key}.csv"

    def _read_cache(self, symbol: str, tf: TF, fresh_only: bool) -> pd.DataFrame | None:
        p = self._cache_path(symbol, tf)
        if not p.exists():
            return None
        ttl = (tf.cache_minutes or self.s.CACHE_TTL_MINUTES) * 60
        if fresh_only and time.time() - p.stat().st_mtime > ttl:
            return None
        df = pd.read_csv(p, parse_dates=["date"])
        return df if not df.empty else None

    def _write_cache(self, symbol: str, tf: TF, df: pd.DataFrame, source: str) -> None:
        """Rows keep their own `source` ("cafef" = imported End-Of-Day history); rows without one get `source`."""
        out = df.copy()
        out["source"] = out["source"].fillna(source) if "source" in out else source
        p = self._cache_path(symbol, tf)
        tmp = p.with_name(p.name + ".tmp")
        out.to_csv(tmp, index=False)
        os.replace(tmp, p)   # never leave a half-written cache for a concurrent reader

    @staticmethod
    def _label(df: pd.DataFrame, default: str) -> str:
        """Source label: 'cafef + vietcap' for imported history topped up from Vietcap."""
        if "source" not in df:
            return default
        srcs = [str(x) for x in pd.unique(df["source"].dropna())]
        return " + ".join(sorted(srcs, key=lambda x: x != IMPORT_SOURCE)) or default

    @staticmethod
    def _has_import(df: pd.DataFrame | None) -> bool:
        return df is not None and "source" in df and bool((df["source"] == IMPORT_SOURCE).any())

    # ---------- End-Of-Day import ----------
    def _readjust_path(self):
        return self.s.cache_dir / "eod_readjust.json"

    def readjust_flags(self) -> dict[str, str]:
        """Imported symbols whose REST prices no longer match the stored history (corporate action since the import)."""
        try:
            return json.loads(self._readjust_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _set_readjust(self, changes: dict[str, str | None]) -> None:
        with self._lock:
            flags = self.readjust_flags()
            for sym, note in changes.items():
                if note is None:
                    flags.pop(sym, None)
                else:
                    flags[sym] = note
            self._readjust_path().write_text(json.dumps(flags, ensure_ascii=False, indent=1), encoding="utf-8")

    def import_eod(self, bars: pd.DataFrame) -> dict:
        """Merge parsed End-Of-Day bars (`eod_import.parse_eod`) into the 1D caches; imported rows win over cached
        rows of the same date. The caches are then marked stale, so the next request only tops up the newest
        sessions from Vietcap/SSI. The 1W cache is dropped: 1W is built from the imported 1D history from now on."""
        tf, wk = get_tf("1D"), get_tf("1W")
        stale = time.time() - 30 * 86400
        created = new_bars = 0
        for sym, g in bars.groupby("symbol", sort=True):
            new = g.drop(columns=["symbol"]).assign(source=IMPORT_SOURCE)
            with self._lock:
                old = self._read_cache(sym, tf, fresh_only=False)
                if old is None:
                    created += 1
                    new_bars += len(new)
                    df = new
                else:
                    if "source" not in old:
                        old["source"] = "cache"
                    new_bars += int((~new["date"].isin(old["date"])).sum())
                    df = pd.concat([old, new]).drop_duplicates("date", keep="last")
                self._write_cache(sym, tf, df.sort_values("date").reset_index(drop=True), IMPORT_SOURCE)
                os.utime(self._cache_path(sym, tf), (stale, stale))
                self._cache_path(sym, wk).unlink(missing_ok=True)
        syms = sorted(bars["symbol"].unique())
        self._set_readjust({s: None for s in syms})
        return {"symbols": len(syms), "new_symbols": created, "new_bars": new_bars}

    # ---------- public ----------
    def get_ohlcv(self, symbol: str, days: int | None = None, refresh: bool = False,
                  timeframe: str = "1D") -> tuple[pd.DataFrame, str]:
        """Return (dataframe, source). Columns: date, open, high, low, close, volume.

        Fresh cache is returned directly. A stale cache is topped up incrementally (only the last
        few days are downloaded). 1D keeps any imported End-Of-Day history; 1W is then resampled from it.
        """
        symbol = symbol.upper().strip()
        tf = get_tf(timeframe)
        if tf.key == "1W" and self._has_import(self._read_cache(symbol, get_tf("1D"), fresh_only=False)):
            df, source = self._get_rest(symbol, get_tf("1D"), days, refresh)
            df = resample(df, tf)
        else:
            df, source = self._get_rest(symbol, tf, days, refresh)
        df, live = self._apply_live(df, symbol, tf)
        if not tf.intraday and symbol in self.readjust_flags():
            source += " · ⚠ giá điều chỉnh đã thay đổi, cần Import lại"
        return df, f"{source} + SSI stream" if live else source

    def _get_rest(self, symbol: str, tf: TF, days: int | None, refresh: bool) -> tuple[pd.DataFrame, str]:
        if not refresh:
            cached = self._read_cache(symbol, tf, fresh_only=True)
            if cached is not None:
                label = self._label(cached, "cache")
                return cached.drop(columns=["source"], errors="ignore"), f"{label} (cache)"

        days = days or tf.lookback_days or self.s.HISTORY_DAYS
        end = date.today()
        start = full_start = end - timedelta(days=days)
        old = self._read_cache(symbol, tf, fresh_only=False)
        if old is not None and old["date"].min().date() <= start + timedelta(days=7):
            # incremental top-up; for 1D/1W the overlap also reveals a re-adjusted history (corporate action)
            start = old["date"].max().date() - timedelta(days=OVERLAP_DAYS.get(tf.key, 3))
        order = source_order(self.s.DATA_SOURCE)
        errors: list[str] = []
        for src in order:
            try:
                if src == "ssi" and not self.ssi.configured:
                    raise RuntimeError("SSI keys not configured")
                svc = self.ssi if src == "ssi" else self.vietcap
                fetched = svc.get_ohlcv(symbol, start, end, tf)
                flag = None
                with self._lock:   # re-read: an import may have rewritten the cache during the fetch
                    cur = self._read_cache(symbol, tf, fresh_only=False)
                    ratio = adjust_ratio(cur, fetched) if start != full_start and not tf.intraday else None
                    if ratio is not None and self._has_import(cur):
                        # keep the imported history, take the new sessions, and ask the user to re-import
                        log.warning("%s: %s prices differ from the imported history (x%.4f), re-import needed",
                                    symbol, src, ratio)
                        flag = f"{date.today():%Y-%m-%d} x{ratio:.4f}"
                    elif ratio is not None:
                        log.info("%s %s: history re-adjusted (x%.4f), reloading it", symbol, tf.key, ratio)
                        fetched, cur = svc.get_ohlcv(symbol, full_start, end, tf), None
                    df = fetched.assign(source=src)
                    if cur is not None:
                        if "source" not in cur:
                            cur["source"] = src
                        df = pd.concat([cur, df]).drop_duplicates("date", keep="last")
                    df = df.sort_values("date").reset_index(drop=True)
                    if df.empty:
                        raise RuntimeError("no data returned")
                    self._write_cache(symbol, tf, df, src)
                if flag:
                    self._set_readjust({symbol: flag})
                return df.drop(columns=["source"]), self._label(df, src)
            except Exception as e:  # noqa: BLE001
                log.warning("%s %s fetch failed for %s: %s", src, tf.key, symbol, e)
                errors.append(f"{src}: {e}")
        raise RuntimeError(f"Could not load {tf.key} data for {symbol} — " + "; ".join(errors))

    def _apply_live(self, df: pd.DataFrame, symbol: str, tf: TF) -> tuple[pd.DataFrame, bool]:
        live = self.realtime.bars_1m(symbol)
        if live.empty or df.empty:
            return df, False
        if tf.intraday:
            # Only buckets that started after streaming began are complete in the stream;
            # earlier ones keep the REST bar.
            agg = resample(live, tf)
            agg = agg[agg["date"] >= live["date"].min()]
            if agg.empty:
                return df, False
            out = pd.concat([df, agg]).drop_duplicates("date", keep="last")
            return out.sort_values("date").reset_index(drop=True), True
        # 1D / 1W: extend today's (this week's) REST bar with the latest streamed prices.
        bucket = resample(live, tf).iloc[-1]
        out = df.copy()
        i = out.index[-1]
        if pd.Timestamp(out.at[i, "date"]) != pd.Timestamp(bucket["date"]):
            return df, False
        out.at[i, "high"] = max(out.at[i, "high"], bucket["high"])
        out.at[i, "low"] = min(out.at[i, "low"], bucket["low"])
        out.at[i, "close"] = bucket["close"]
        return out, True

    def live_bars(self, symbol: str, timeframe: str = "1D", count: int = 2) -> tuple[list[dict], bool]:
        """Last `count` bars (REST + stream) for incremental chart updates; subscribes the symbol."""
        watching = self.realtime.watch(symbol)
        df, _ = self.get_ohlcv(symbol, timeframe=timeframe)
        return df.tail(count).to_dict("records"), watching

    def close(self) -> None:
        self.realtime.stop()
        self.ssi.close()
        self.vietcap.close()
