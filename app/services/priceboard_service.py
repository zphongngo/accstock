"""Price board (Bảng điện): live quotes by sector, index cards and a per-symbol technical score.

Quotes and indexes come from Vietcap's public price board API (trading.vietcap.com.vn), polled on
demand and cached for a few seconds so several open dashboards share one request. SSI's REST API only
has end-of-day summaries and its stream is capped at 50 symbols, so it isn't used for the board.

The "Điểm" column is the 1D technical score (`analyze_dataframe`) computed in a background thread,
one symbol at a time, from the regular OHLCV cache. Scores are kept for the day in
data/cache/board_scores.json, so a restart doesn't recompute them.

Session day: the board, the index cards and the header's index charts show the latest session until 08:56 of the
next weekday (`board_day`), then switch to the new session (charts restart from 09:00, empty until the first bar).
Outside that live window (weekday before 08:56, weekends) quotes and indexes are served from the last live snapshot
(data/cache/board_last_session.json), so whatever Vietcap shows overnight / pre-open never replaces them.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date, datetime, time as dtime, timedelta, timezone

import httpx
import pandas as pd

from app.core.config import get_settings
from app.services.sectors import VN30_NAME, WATCH_NAME, get_sectors, split_pinned
from app.services.timeframes import bar_starts
from app.services.vietcap_service import HEADERS

log = logging.getLogger(__name__)

BASE = "https://trading.vietcap.com.vn/api/price/"
INDEXES = [("VNINDEX", "VNINDEX"), ("VN30", "VN30"), ("HNXIndex", "HNX"), ("HNX30", "HNX30"),
           ("HNXUpcomIndex", "UPCOM")]
QUOTE_TTL = 3.0        # seconds a quote snapshot is reused
VN30_TTL = 6 * 3600    # VN30 basket refresh
ICHART_TTL = 20.0      # seconds the intraday index charts are reused
VN_UTC_OFFSET = 7 * 3600
SWITCH_TIME = dtime(8, 56)      # the board moves on to the new session at 08:56 on weekdays
ATC_TIME = dtime(14, 45)        # closing auction bar (the last point of the session axis)
HOLIDAY_AFTER = dtime(9, 15)    # live, but still no bar of today by then: holiday, keep the last session
SNAPSHOT_SAVE_EVERY = 60.0      # seconds between writes of the last-session snapshot


def _now() -> datetime:
    return datetime.now()


def is_live(now: datetime | None = None) -> bool:
    """True from 08:56 on weekdays: the board follows Vietcap live. Otherwise it shows the last session."""
    now = now or _now()
    return now.weekday() < 5 and now.time() >= SWITCH_TIME


def board_day(now: datetime | None = None) -> date:
    """The session the board shows: today from 08:56 on weekdays, else the previous weekday."""
    now = now or _now()
    if is_live(now):
        return now.date()
    d = now.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _epoch(d) -> int:
    return int((pd.Timestamp(d) - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1))


def session_axis(day: date, interval: int = 1) -> list[int]:
    """Chart times of a whole session (09:00 ... 14:45 ATC), so the chart starts at 09:00 and fills to the right."""
    starts = bar_starts(datetime.combine(day, dtime(0)), interval) + [datetime.combine(day, ATC_TIME)]
    return [_epoch(x) for x in dict.fromkeys(starts)]


def state(price: float, ref: float, ceiling: float, floor: float) -> str:
    """Board colour class: ceil / up / ref / down / floor / none (no trade)."""
    if not price:
        return "none"
    if ceiling and price >= ceiling:
        return "ceil"
    if floor and price <= floor:
        return "floor"
    if price > ref:
        return "up"
    if price < ref:
        return "down"
    return "ref"


def normalize_quote(x: dict) -> dict:
    ref = float(x.get("ref") or 0)
    ceiling, floor = float(x.get("cei") or 0), float(x.get("flo") or 0)
    last = float(x.get("c") or 0)
    price = last or ref
    change = price - ref if last else 0.0
    return {
        "symbol": x.get("s", ""),
        "board": x.get("bo"),
        "name": x.get("orgn") or "",
        "price": price,
        "ref": ref, "ceiling": ceiling, "floor": floor,
        "open": float(x.get("op") or 0), "high": float(x.get("h") or 0), "low": float(x.get("l") or 0),
        "change": change,
        "change_pct": round(change / ref * 100, 2) if ref else 0.0,
        "volume": int(x.get("vo") or 0),
        "value": float(x.get("va") or 0) * 1e6,          # API sends million VND
        "foreign_buy": int(x.get("frbv") or 0), "foreign_sell": int(x.get("frsv") or 0),
        "state": state(last, ref, ceiling, floor),
    }


def normalize_index(x: dict, label: str) -> dict:
    return {
        "symbol": x.get("symbol"), "label": label,
        "value": float(x.get("price") or 0),
        "change": float(x.get("change") or 0),
        "change_pct": round(float(x.get("changePercent") or 0), 2),
        "total_value": float(x.get("totalValue") or 0) * 1e6,
        "up": int(x.get("totalStockIncrease") or 0), "ceil": int(x.get("totalStockCeiling") or 0),
        "flat": int(x.get("totalStockNoChange") or 0),
        "down": int(x.get("totalStockDecline") or 0), "floor": int(x.get("totalStockFloor") or 0),
    }


def _vn_time(epoch) -> datetime:
    """Vietcap epoch seconds -> naive Vietnam wall-clock time."""
    return datetime.fromtimestamp(int(float(epoch)) + VN_UTC_OFFSET, timezone.utc).replace(tzinfo=None)


def build_index_chart(symbol: str, label: str, raw: dict | None, interval: int = 1, index: dict | None = None,
                      day: date | None = None) -> dict:
    """Intraday chart of one index for the global header: the 1-minute bars (or 3-minute buckets) of `day` (default:
    the latest session in `raw`), the reference level and per-bar volume, plus `axis` = every time of that session.
    A `day` without bars yet (08:56-09:00) gives no bars and ref = the previous session's last close.
    Chart time = VN wall-clock encoded as UTC seconds."""
    out = {"symbol": symbol, "label": label, "interval": interval, "date": None, "ref": None, "bars": [], "axis": []}
    ts = (raw or {}).get("t") or []
    if not ts and day is None:
        return out
    df = pd.DataFrame({"date": [_vn_time(x) for x in ts], "close": (raw or {}).get("c") or [],
                       "volume": (raw or {}).get("v") or []})
    for c in ("close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna().sort_values("date")
    day = day or df["date"].iloc[-1].date()
    prev = df[df["date"].dt.date < day]
    df = df[df["date"].dt.date == day]
    if interval > 1:
        df = (df.set_index("date").resample(f"{interval}min", origin="start_day", label="left")
              .agg({"close": "last", "volume": "sum"}).dropna().reset_index())
    if len(df) and index and index.get("value"):   # session under way: the board's own reference level
        ref = float(index["value"]) - float(index.get("change") or 0)
    elif len(prev):
        ref = float(prev["close"].iloc[-1])
    else:
        ref = None
    out.update(date=day.isoformat(), ref=round(ref, 2) if ref else None, axis=session_axis(day, interval), bars=[
        {"time": _epoch(d), "value": round(float(c), 2), "volume": float(v)}
        for d, c, v in zip(df["date"], df["close"], df["volume"])])
    return out


def chart_day(raw: dict | None, now: datetime) -> date:
    """Session the header charts show: `board_day`, unless it is live, past 09:15 and VN-Index still has no bar of
    today (a public holiday): then the latest session that has bars."""
    day = board_day(now)
    ts = (raw or {}).get("t") or []
    if ts and is_live(now) and now.time() >= HOLIDAY_AFTER:
        latest = _vn_time(ts[-1]).date()
        if latest < day:
            return latest
    return day


def group_summary(name: str, quotes: list[dict]) -> dict:
    traded = [q for q in quotes if q["state"] != "none"]
    val = sum(q["value"] for q in traded)
    # value-weighted % change, so a big stock moves the sector more than a thin one
    chg = (sum(q["change_pct"] * q["value"] for q in traded) / val) if val else 0.0
    return {
        "name": name,
        "change_pct": round(chg, 2),
        "up": sum(q["state"] in ("up", "ceil") for q in traded),
        "down": sum(q["state"] in ("down", "floor") for q in traded),
        "flat": sum(q["state"] == "ref" for q in traded),
        "value": val,
        "symbols": [q["symbol"] for q in sorted(quotes, key=lambda q: (q["state"] == "none", -q["change_pct"]))],
    }


def _done(entry: dict | None) -> bool:
    """A symbol's background work for today is finished (entries from older versions lack 'strength' / 'acc')."""
    return bool(entry) and "strength" in entry and "acc" in entry


def avg_volume_20(df, today: date | None = None) -> float | None:
    """Average volume of the 20 sessions before today (today's partial bar excluded)."""
    today = today or date.today()
    past = df[df["date"].dt.date < today]["volume"].tail(20)
    return float(past.mean()) if len(past) and past.mean() > 0 else None


class PriceBoardService:
    def __init__(self, stock=None) -> None:
        self.s = get_settings()
        self.stock = stock                      # StockService, used for the background scores
        self._client = httpx.Client(headers=HEADERS, timeout=20)
        self._lock = threading.Lock()
        self._quotes: dict[str, dict] = {}
        self._quotes_key: tuple = ()
        self._quotes_at = 0.0
        self._indexes: list[dict] = []
        self._indexes_at = 0.0
        self._vn30: list[str] = []
        self._vn30_at = 0.0
        self._ichart: dict[int, tuple[float, list[dict]]] = {}
        self._snap: dict = {"day": "", "indexes": [], "quotes": {}}   # last live session (see module docstring)
        self._snap_saved_at = 0.0
        self._load_snapshot()
        self._scores: dict[str, dict] = {}
        self._scores_day = ""
        self._score_thread: threading.Thread | None = None
        self._load_scores()

    # ---------- Vietcap ----------
    def _post(self, path: str, body: dict):
        r = self._client.post(BASE + path, json=body)
        r.raise_for_status()
        return r.json()

    def vn30(self) -> list[str]:
        if not self._vn30 or time.time() - self._vn30_at > VN30_TTL:
            try:
                r = self._client.get(BASE + "symbols/getByGroup", params={"group": "VN30"})
                r.raise_for_status()
                self._vn30 = sorted(x["symbol"] for x in r.json() if x.get("symbol"))
                self._vn30_at = time.time()
            except Exception as e:  # noqa: BLE001
                log.warning("VN30 basket unavailable (%s), using WATCHLIST", e)
                self._vn30 = self._vn30 or self.s.watchlist
        return self._vn30

    # ---------- last-session snapshot (held until 08:56 of the next weekday) ----------
    def _snapshot_path(self):
        return self.s.cache_dir / "board_last_session.json"

    def _load_snapshot(self) -> None:
        try:
            d = json.loads(self._snapshot_path().read_text(encoding="utf-8"))
            if isinstance(d.get("quotes"), dict) and isinstance(d.get("indexes"), list):
                self._snap = d
        except Exception:  # noqa: BLE001  (missing/corrupt file: nothing to hold)
            pass

    def _remember(self, quotes: dict | None = None, indexes: list | None = None) -> None:
        """Keep the live data of today's session (called only inside the live window)."""
        today = _now().date().isoformat()
        if self._snap.get("day") != today:
            self._snap = {"day": today, "indexes": [], "quotes": {}}
        if quotes:
            self._snap["quotes"].update(quotes)
        if indexes:
            self._snap["indexes"] = indexes
        if time.time() - self._snap_saved_at >= SNAPSHOT_SAVE_EVERY:
            self._snap_saved_at = time.time()
            try:
                self._snapshot_path().write_text(json.dumps(self._snap), encoding="utf-8")
            except Exception as e:  # noqa: BLE001
                log.warning("Could not save the board snapshot: %s", e)

    def _held(self) -> bool:
        """Outside the live window with a saved session: show it instead of Vietcap's overnight / pre-open data."""
        return not is_live() and bool(self._snap.get("day"))

    def _fetch_quotes(self, symbols: list[str]) -> dict[str, dict]:
        raw = self._post("v1/w/priceboard/tickers/price", {"symbols": list(symbols)})
        return {q["symbol"]: q for q in map(normalize_quote, raw) if q["symbol"]}

    def quotes(self, symbols: list[str]) -> dict[str, dict]:
        key = tuple(sorted(set(symbols)))
        with self._lock:
            if key == self._quotes_key and time.time() - self._quotes_at < QUOTE_TTL:
                return self._quotes
            if self._held():
                saved = self._snap["quotes"]
                out = {s: saved[s] for s in key if s in saved}
                missing = [s for s in key if s not in saved]   # e.g. a symbol added to Quan Tâm overnight
                if missing:
                    out.update(self._fetch_quotes(missing))
            else:
                out = self._fetch_quotes(list(key))
                if is_live():
                    self._remember(quotes=out)
            self._quotes, self._quotes_key, self._quotes_at = out, key, time.time()
            return self._quotes

    def indexes(self) -> list[dict]:
        if time.time() - self._indexes_at < QUOTE_TTL:
            return self._indexes
        if self._held() and self._snap.get("indexes"):
            self._indexes = self._snap["indexes"]
        else:
            raw = self._post("marketIndex/getList", {"symbols": [s for s, _ in INDEXES]})
            by = {x.get("symbol"): x for x in raw}
            self._indexes = [normalize_index(by[s], label) for s, label in INDEXES if s in by]
            if is_live():
                self._remember(indexes=self._indexes)
        self._indexes_at = time.time()
        return self._indexes

    # ---------- intraday index charts (global header) + trading-day check ----------
    def _minute_bars(self, symbol: str, count_back: int = 400) -> dict:
        """Vietcap 1-minute bars of one index (the API returns nothing when several symbols are asked at once)."""
        to = int(time.time()) + 60
        r = self._client.post(self.s.VIETCAP_URL, json={"timeFrame": "ONE_MINUTE", "symbols": [symbol],
                                                         "to": to, "countBack": count_back})
        r.raise_for_status()
        data = r.json()
        return data[0] if data else {}

    def index_charts(self, interval: int = 1) -> list[dict]:
        hit = self._ichart.get(interval)
        if hit and time.time() - hit[0] < ICHART_TTL:
            return hit[1]
        from concurrent.futures import ThreadPoolExecutor

        def fetch(sym):
            try:
                return self._minute_bars(sym)
            except Exception as e:  # noqa: BLE001  (one index failing must not hide the others)
                log.warning("Intraday bars failed for %s: %s", sym, e)
                return {}

        with ThreadPoolExecutor(len(INDEXES)) as ex:
            raws = list(ex.map(fetch, [s for s, _ in INDEXES]))
        try:
            idx = {i["symbol"]: i for i in self.indexes()}
        except Exception:  # noqa: BLE001
            idx = {}
        day = chart_day(raws[0], _now())
        out = [build_index_chart(s, label, raw, interval, idx.get(s), day) for (s, label), raw in zip(INDEXES, raws)]
        self._ichart[interval] = (time.time(), out)
        return out

    def trading_today(self, today: date | None = None) -> bool:
        """True when VN-Index has 1-minute bars dated today (weekends, public holidays and Tết have none).
        Raises if Vietcap can't be reached, so the caller can decide (the scheduler then does NOT send)."""
        today = today or date.today()
        if today.weekday() >= 5:
            return False
        ts = (self._minute_bars("VNINDEX", count_back=30) or {}).get("t") or []
        return bool(ts) and _vn_time(ts[-1]).date() == today

    # ---------- board ----------
    def groups(self) -> list[tuple[str, list[str]]]:
        """VN30 (pinned, the list's own VN30 group or the live basket) then the other sectors. "Quan Tâm" is not
        here: it is the user's watchlist, sent by the browser (`watch`)."""
        _, vn30, rest = split_pinned(get_sectors())
        return [(VN30_NAME, vn30 if vn30 else self.vn30())] + rest

    def snapshot(self, watch: list[str] | None = None) -> tuple[list[tuple[str, list[str]]], dict, list[dict]]:
        """(groups, quotes for every board symbol + `watch`, index cards)."""
        groups = self.groups()
        symbols = {s for _, syms in groups for s in syms} | set(watch or [])
        quotes = self.quotes(sorted(symbols))
        try:
            idx = self.indexes()
        except Exception as e:  # noqa: BLE001  (the board is still useful without the index cards)
            log.warning("Index summary failed: %s", e)
            idx = self._indexes
        return groups, quotes, idx

    def movers_quotes(self, watch: list[str] | None = None) -> dict[str, dict]:
        """Quotes of every board symbol + `watch`, with vol_ratio, for the strong/weak bulletin."""
        _, quotes, _ = self.snapshot(watch or [])
        self._ensure_scores(list(quotes))
        if self.stock is None:          # the scheduled script: no background worker, use the CSV cache
            self.fill_vol_avg_from_cache(list(quotes))
        self._attach_scores(quotes)
        return quotes

    def movers_message(self, threshold: float, watch: list[str] | None = None) -> tuple[list[str], int, int]:
        from app.analysis.board_alerts import build_movers_message

        watch = [w.upper() for w in (watch or []) if w.strip()]
        return build_movers_message(self.movers_quotes(watch), threshold, watch=watch)

    def fill_vol_avg_from_cache(self, symbols: list[str]) -> int:
        """vol_avg20 (average volume of the 20 sessions *before* today, so it doesn't change during the day) from
        data/cache/<SYM>_1D.csv for symbols today's board_scores.json doesn't have. No network. Returns the count."""
        import pandas as pd

        n = 0
        for sym in symbols:
            if (self._scores.get(sym) or {}).get("vol_avg20"):
                continue
            path = self.s.cache_dir / f"{sym}_1D.csv"
            if not path.exists():
                continue
            try:
                df = pd.read_csv(path, parse_dates=["date"])
                self._scores.setdefault(sym, {})["vol_avg20"] = avg_volume_20(df)
                n += 1
            except Exception as e:  # noqa: BLE001
                log.debug("vol_avg20 from cache failed for %s: %s", sym, e)
        return n

    def _attach_scores(self, quotes: dict[str, dict]) -> None:
        """Add score/rating and vol_ratio (today's volume / average of the previous 20 sessions)."""
        for q in quotes.values():
            sc = self._scores.get(q["symbol"]) or {}
            q["score"], q["rating"] = sc.get("score"), sc.get("rating")
            avg = sc.get("vol_avg20")
            q["vol_avg20"] = avg
            q["vol_ratio"] = round(q["volume"] / avg, 4) if avg else None

    def board(self, watch: list[str] | None = None) -> dict:
        watch = [w.upper() for w in (watch or []) if w.strip()]
        groups, quotes, idx = self.snapshot(watch)
        # pinned first: "Quan Tâm" (always shown, even empty, so the user can add symbols) then VN30
        out_groups = [{**group_summary(WATCH_NAME, [quotes[s] for s in watch if s in quotes]),
                       "symbols": [s for s in watch if s in quotes], "watchlist": True, "pinned": True}]
        for i, (name, syms) in enumerate(groups):
            qs = [quotes[s] for s in dict.fromkeys(syms) if s in quotes]
            if qs or i == 0:
                out_groups.append({**group_summary(name, qs), "pinned": i == 0})
        self._ensure_scores(list(quotes))
        self._attach_scores(quotes)
        scores = self._scores
        return {
            "updated": time.strftime("%H:%M:%S"),
            "indexes": idx,
            "groups": out_groups,
            "quotes": quotes,
            "scores": {"done": sum(_done(scores.get(s)) for s in quotes), "total": len(quotes),
                       "running": bool(self._score_thread and self._score_thread.is_alive())},
        }

    # ---------- background scores ----------
    def _scores_path(self):
        return self.s.cache_dir / "board_scores.json"

    def _load_scores(self) -> None:
        try:
            d = json.loads(self._scores_path().read_text(encoding="utf-8"))
            if d.get("day") == date.today().isoformat():
                self._scores, self._scores_day = d.get("scores", {}), d["day"]
        except Exception:  # noqa: BLE001  (missing/corrupt file: start empty)
            pass

    def _save_scores(self) -> None:
        try:
            self._scores_path().write_text(json.dumps({"day": self._scores_day, "scores": self._scores}),
                                           encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            log.warning("Could not save board scores: %s", e)

    def _ensure_scores(self, symbols: list[str]) -> None:
        if self.stock is None:
            return
        today = date.today().isoformat()
        if self._scores_day != today:
            self._scores, self._scores_day = {}, today
        todo = [s for s in symbols if not _done(self._scores.get(s))]
        if not todo or (self._score_thread and self._score_thread.is_alive()):
            return
        self._score_thread = threading.Thread(target=self._score_worker, args=(todo,), daemon=True,
                                              name="board-scores")
        self._score_thread.start()

    def _score_worker(self, symbols: list[str]) -> None:
        from app.analysis.stock_scanner import analyze_dataframe
        from app.analysis.acc_strength import BENCHMARK, acc_pair, completed
        from app.analysis.strength import strength_metrics

        log.info("Board: computing 1D scores + strength for %d symbols", len(symbols))
        try:   # VN-Index once, for RS vs the market and the RS line
            index_df, _ = self.stock.get_ohlcv("VNINDEX", timeframe="1D")
        except Exception as e:  # noqa: BLE001
            log.warning("VNINDEX history unavailable, RS vs VN-Index skipped: %s", e)
            index_df = None
        try:   # VN30 once, the ACC Ranking benchmark (completed sessions only)
            bench_df, _ = self.stock.get_ohlcv(BENCHMARK, timeframe="1D")
            bench_df = completed(bench_df)
        except Exception as e:  # noqa: BLE001
            log.warning("%s history unavailable, ACC RS uses plain returns: %s", BENCHMARK, e)
            bench_df = None
        for i, sym in enumerate(symbols, 1):
            entry = {"score": None, "rating": None, "vol_avg20": None, "strength": None, "acc": None}
            try:
                df, _ = self.stock.get_ohlcv(sym, timeframe="1D")
                a = analyze_dataframe(sym, df, "1D")
                entry.update(score=a["score"], rating=a["rating"], vol_avg20=avg_volume_20(df))
                entry["strength"] = strength_metrics(df, index_df)
            except Exception as e:  # noqa: BLE001  (new listings, too little history...)
                log.debug("Board score %s failed: %s", sym, e)
            try:
                entry["acc"] = acc_pair(completed(df), bench_df)
            except Exception as e:  # noqa: BLE001
                log.debug("ACC score %s failed: %s", sym, e)
            self._scores[sym] = entry
            if i % 20 == 0:
                self._save_scores()
        self._save_scores()
        log.info("Board: scores done")

    # ---------- Scanner: RS Rating / strength of every stock on the board ----------
    def universe(self, watch: list[str] | None = None) -> tuple[list[str], list[tuple[str, list[str]]]]:
        """(every board symbol incl. VN30 and `watch`, the industry sectors used for sector strength)."""
        groups = self.groups()
        symbols = list(dict.fromkeys([s for _, syms in groups for s in syms] + list(watch or [])))
        return symbols, groups[1:]

    def strength(self, watch: list[str] | None = None) -> dict:
        from app.analysis.strength import legend, rank_universe

        symbols, sectors = self.universe([w.upper() for w in (watch or []) if w.strip()])
        self._ensure_scores(symbols)
        entries = {s: self._scores.get(s) for s in symbols}
        metrics = {s: e["strength"] for s, e in entries.items() if e and e.get("strength")}
        out = rank_universe(metrics, sectors)
        out.update(
            progress={"done": sum(_done(e) for e in entries.values()), "total": len(symbols),
                      "running": bool(self._score_thread and self._score_thread.is_alive())},
            failed=sorted(s for s, e in entries.items() if _done(e) and not e.get("strength")),
            day=self._scores_day, legend=legend(),
        )
        return out

    def acc(self, watch: list[str] | None = None) -> dict:
        """ACC Ranking: Swing / Position / Combined, Setup, Risk and signal of every board symbol vs VN30."""
        from app.analysis.acc_strength import legend, rank_acc

        symbols, sectors = self.universe([w.upper() for w in (watch or []) if w.strip()])
        self._ensure_scores(symbols)
        entries = {s: self._scores.get(s) for s in symbols}
        out = rank_acc({s: e["acc"] for s, e in entries.items() if e and e.get("acc")}, sectors)
        out.update(
            progress={"done": sum(_done(e) for e in entries.values()), "total": len(symbols),
                      "running": bool(self._score_thread and self._score_thread.is_alive())},
            failed=sorted(s for s, e in entries.items() if _done(e) and not e.get("acc")),
            day=self._scores_day, legend=legend(),
        )
        return out

    def refresh_scores(self) -> bool:
        """Recompute today's scores/strength from fresh data (False while a run is in progress)."""
        if self._score_thread and self._score_thread.is_alive():
            return False
        self._scores = {}
        return True

    def clear(self) -> None:
        """Forget the cached board (quotes of the old sector list), e.g. before importing a new list."""
        with self._lock:
            self._quotes, self._quotes_key, self._quotes_at = {}, (), 0.0

    def close(self) -> None:
        self._client.close()
