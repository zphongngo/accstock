from datetime import datetime
from fastapi.testclient import TestClient

from app.api.deps import get_board_service
from app.main import app
from app.services.priceboard_service import PriceBoardService, group_summary, normalize_quote, state
from app.services.sectors import SECTORS, all_symbols

RAW = {"s": "FPT", "bo": "HSX", "orgn": "FPT Corp", "ref": 50400, "cei": 53900, "flo": 46900, "c": 53300,
       "op": 50400, "h": 53400, "l": 50400, "vo": 9436400, "va": 495000.5, "frbv": 3521893, "frsv": 613943}


def test_state_classes():
    assert state(53900, 50400, 53900, 46900) == "ceil"
    assert state(46900, 50400, 53900, 46900) == "floor"
    assert state(51000, 50400, 53900, 46900) == "up"
    assert state(50000, 50400, 53900, 46900) == "down"
    assert state(50400, 50400, 53900, 46900) == "ref"
    assert state(0, 50400, 53900, 46900) == "none"


def test_normalize_quote():
    q = normalize_quote(RAW)
    assert q["symbol"] == "FPT" and q["state"] == "up" and q["change"] == 2900
    assert q["change_pct"] == 5.75 and q["value"] == 495000.5e6 and q["volume"] == 9436400
    q = normalize_quote({**RAW, "c": 0})
    assert q["price"] == 50400 and q["change_pct"] == 0 and q["state"] == "none"


def test_group_summary_is_value_weighted_and_sorted():
    a = normalize_quote({**RAW, "s": "AAA", "c": 52920, "va": 300})        # +5%
    b = normalize_quote({**RAW, "s": "BBB", "c": 49896, "va": 100})        # -1%
    c = normalize_quote({**RAW, "s": "CCC", "c": 0})                       # no trade, ignored
    g = group_summary("X", [b, c, a])
    assert g["change_pct"] == 3.5 and (g["up"], g["down"], g["flat"]) == (1, 1, 0)
    assert g["symbols"] == ["AAA", "BBB", "CCC"]


def test_sectors_have_no_duplicate_within_a_group():
    for name, syms in SECTORS:
        assert len(syms) == len(set(syms)), name
    assert len(all_symbols()) == len(set(all_symbols()))


class FakeBoard(PriceBoardService):
    def __init__(self):  # no network, no scores
        super().__init__(stock=None)

    def vn30(self):
        return ["FPT"]

    def quotes(self, symbols):
        return {s: normalize_quote({**RAW, "s": s}) for s in symbols}

    def indexes(self):
        return []

    def _load_scores(self):
        pass


def _q(sym, pct, state=None):
    ref = 10000
    price = round(ref * (1 + pct / 100))
    q = normalize_quote({**RAW, "s": sym, "ref": ref, "cei": 10700, "flo": 9300, "c": price})
    if state:
        q["state"] = state
    return q


def test_movers_threshold_and_message():
    from datetime import datetime

    from app.analysis.board_alerts import HEADER, RULE, build_movers_message, movers, row

    quotes = {s: q for s, q in [("SSI", _q("SSI", 7)), ("FPT", _q("FPT", 3.5)), ("HPG", _q("HPG", 3)),
                                ("VNM", _q("VNM", -4)), ("MWG", _q("MWG", -7)), ("NOT", _q("NOT", -5, "none")),
                                ("ACB", _q("ACB", 2.9))]}
    for q in quotes.values():
        q["vol_ratio"] = None
    strong, weak = movers(quotes, 3)
    assert [q["symbol"] for q in strong] == ["SSI", "FPT", "HPG"]
    assert [q["symbol"] for q in weak] == ["MWG", "VNM"]
    msgs, n_up, n_dn = build_movers_message(quotes, 3, now=datetime(2026, 11, 4, 14, 30))
    assert len(msgs) == 1 and (n_up, n_dn) == (3, 2)
    msg = msgs[0]
    assert "14:30 04/11/2026" in msg
    assert "hiện có 3 mã mạnh · 2 mã yếu" in msg
    assert "<b>1. Co Phieu Manh (&gt;=3%):</b>\n<pre>" + HEADER + "\n" + RULE + "\n" in msg
    assert "<b>2. Co Phieu Yeu (&lt;=-3%):</b>" in msg
    assert msg.index("SSI") < msg.index("FPT") < msg.index("HPG") < msg.index("MWG") < msg.index("VNM")
    assert "NOT" not in msg and "ACB" not in msg
    empty, _, _ = build_movers_message({}, 2.5)
    assert empty[0].count("Không có mã nào.") == 2 and "(&gt;=2.5%)" in empty[0]


def test_row_matches_the_template_layout():
    from app.analysis.board_alerts import HEADER, row

    assert HEADER == "Ma      Gia     +/-%    KL (K)  %V20p"
    q = {"symbol": "AMS", "price": 8000, "change_pct": 14.29, "volume": 204600, "vol_ratio": 24.94}
    assert row(q) == "AMS    8.00  +14.29%     204.6  2494%"
    q = {"symbol": "HHG", "price": 900, "change_pct": -12.5, "volume": 44_812_300, "vol_ratio": None}
    assert row(q) == "HHG    0.90  -12.50%  44,812.3      -"


def test_long_lists_split_into_whole_pre_blocks():
    from app.analysis.board_alerts import build_movers_message

    quotes = {f"S{i:03d}": _q(f"S{i:03d}", 5) for i in range(200)}
    msgs, n_up, _ = build_movers_message(quotes, 3, limit=1500)
    assert n_up == 200 and len(msgs) > 3
    assert all(len(m) <= 1500 and m.count("<pre>") == m.count("</pre>") for m in msgs)
    assert sum(m.count("\nS") for m in msgs) == 200
    # The summary line is now prepended, so the raw title with parentheses may be missing – check continuation marker
    assert " (tiếp)" in "".join(msgs) and "Manh (>=3%) (tiếp):" not in "".join(msgs)


class FakeTelegram:
    configured = True
    chat_ids = ["-100"]
    sent: list[str] = []

    def send(self, text):
        FakeTelegram.sent.append(text)
        return 1


def test_movers_endpoint_sends_and_previews():
    from app.api.deps import get_telegram_service

    app.dependency_overrides[get_board_service] = FakeBoard
    app.dependency_overrides[get_telegram_service] = FakeTelegram
    FakeTelegram.sent = []
    try:
        c = TestClient(app)
        r = c.post("/api/board/telegram", json={"threshold": 5, "dry_run": True})
        assert r.status_code == 200 and r.json()["sent"] == 0 and not FakeTelegram.sent
        assert r.json()["strong"] > 0
        r = c.post("/api/board/telegram", json={"threshold": 5})
        d = r.json()
        assert r.status_code == 200 and d["sent"] == d["messages"] == len(FakeTelegram.sent) > 1
        assert "Co Phieu Manh" in "".join(FakeTelegram.sent)
        assert all(len(m) <= 4000 and m.count("<pre>") == m.count("</pre>") for m in FakeTelegram.sent)
        assert c.post("/api/board/telegram", json={"threshold": 0}).status_code == 422
        FakeTelegram.configured = False
        r = c.post("/api/board/telegram", json={"threshold": 3})
        assert r.status_code == 400 and "telegram.env" in r.json()["detail"]
    finally:
        FakeTelegram.configured = True
        app.dependency_overrides.pop(get_board_service, None)
        app.dependency_overrides.pop(get_telegram_service, None)


def test_board_endpoint_groups_and_watchlist():
    app.dependency_overrides[get_board_service] = FakeBoard
    try:
        r = TestClient(app).get("/api/board?watch=hpg, mwg")
    finally:
        app.dependency_overrides.pop(get_board_service, None)
    assert r.status_code == 200
    d = r.json()
    names = [g["name"] for g in d["groups"]]
    assert names[:2] == ["Quan Tâm", "VN30"] and d["groups"][0]["symbols"] == ["HPG", "MWG"]
    assert [g.get("pinned") for g in d["groups"][:3]] == [True, True, False]
    assert "Ngân hàng" in names and d["quotes"]["FPT"]["score"] is None


def test_indexes_endpoint_for_the_global_header():
    class Idx(FakeBoard):
        def indexes(self):
            return [{"label": "VNINDEX", "value": 1733.02}]

    app.dependency_overrides[get_board_service] = Idx
    try:
        r = TestClient(app).get("/api/board/indexes")
    finally:
        app.dependency_overrides.pop(get_board_service, None)
    assert r.status_code == 200 and r.json()[0]["label"] == "VNINDEX"


def test_colour_dots_and_quan_tam_section_sorted_a_to_z():
    from datetime import datetime

    from app.analysis.board_alerts import build_movers_message, mark

    assert [mark(_q("X", p)) for p in (6.9, 3, 0, -2, -6.9)] == ["🩷", "🟢", "🟡", "🔴", "🔵"]
    assert mark(_q("X", 6.5)) == "🟢" and mark(_q("X", -6.5)) == "🔴"
    assert mark(_q("X", 0, state="none")) == "⚪"
    quotes = {s: _q(s, p) for s, p in (("VIC", 6.9), ("HPG", 3.2), ("FPT", -1.0), ("ACB", -6.8), ("MWG", 0))}
    msgs, strong, weak = build_movers_message(quotes, 3, now=datetime(2026, 11, 4, 9, 18),
                                              watch=["MWG", "FPT", "ACB", "ZZZ"])
    text = "\n".join(msgs)
    assert (strong, weak) == (2, 1) and "🩷 &gt; +6.5%" in text and "&lt; −6.5%" in text
    lines = text.splitlines()
    vic = next(x for x in lines if x.startswith("VIC")); hpg = next(x for x in lines if x.startswith("HPG"))
    assert vic.endswith(" 🩷") and hpg.replace("</pre>", "").endswith(" 🟢")
    s3 = text.split("3. Quan Tam (A-Z):")[1]
    assert [x.split()[0] for x in s3.splitlines() if x[:3] in ("ACB", "FPT", "MWG")] == ["ACB", "FPT", "MWG"]
    assert "ZZZ" not in s3 and s3.count("🔵") == 1
    assert "3. Quan Tam" not in "\n".join(build_movers_message(quotes, 3)[0])


def test_board_settings_shared_with_the_scheduler(tmp_path):
    from app.services import board_settings as bs

    bs.settings_path().unlink(missing_ok=True)
    c = TestClient(app)
    on = {"auto_board": True, "auto_daily": True}
    defaults = {"board_start": "09:18", "board_end": "15:10", "board_interval": 30, "auto_bo": False, "bo_tf": "1h"}
    assert c.get("/api/board/settings").json() == {"watch": [], "threshold": 3.0, **on, **defaults}
    d = c.put("/api/board/settings", json={"watch": ["fpt", " hpg", "FPT", "???"], "threshold": 4.5}).json()
    assert d == {"watch": ["FPT", "HPG"], "threshold": 4.5, **on, **defaults} and bs.load() == d
    assert c.put("/api/board/settings", json={"threshold": 2}).json()["watch"] == ["FPT", "HPG"]
    assert c.put("/api/board/settings", json={"threshold": 99}).status_code == 422
    d = c.put("/api/board/settings", json={"board_start": "09:30", "board_end": "15:00",
                                            "board_interval": 15}).json()
    assert (d["board_start"], d["board_end"], d["board_interval"]) == ("09:30", "15:00", 15)


def test_board_schedule_endpoint(tmp_path):
    """GET /api/board/settings/schedule returns the scheduler's view of the config."""
    from app.services import board_settings as bs

    assert bs.settings_path().parent == tmp_path
    bs.settings_path().unlink(missing_ok=True)
    bs.save(auto_board=True, auto_daily=True)
    c = TestClient(app)
    r = c.get("/api/board/settings/schedule").json()
    assert r["auto_board"] is True
    assert r["threshold"] == 3.0
    assert r["board_start"] == "09:18" and r["board_end"] == "15:10" and r["board_interval"] == 30
    assert r["slots_per_day"] == 12
    assert r["next"] is not None
    assert "date" in r["next"] and "time" in r["next"] and "in_minutes" in r["next"]
    c.put("/api/board/settings", json={"auto_board": False})
    r = c.get("/api/board/settings/schedule").json()
    assert r["auto_board"] is False
    assert r["next"] is None
    r = c.get("/api/board/settings").json()
    assert r["auto_board"] is False and r["auto_daily"] is True


def test_board_scheduler_slots_are_interval_based_weekdays_only():
    from datetime import datetime

    from app.services.board_scheduler import scheduled_slot, next_slot, slot_minutes

    config = {"board_start": "09:18", "board_end": "15:10", "board_interval": 30}
    assert scheduled_slot(datetime(2026, 11, 9, 9, 17), config) is None
    assert scheduled_slot(datetime(2026, 11, 9, 9, 18), config) == "2026-11-09T09:18"
    assert scheduled_slot(datetime(2026, 11, 9, 9, 47), config) == "2026-11-09T09:18"
    assert scheduled_slot(datetime(2026, 11, 9, 9, 48), config) == "2026-11-09T09:48"
    assert scheduled_slot(datetime(2026, 11, 9, 15, 10), config) == "2026-11-09T14:48"
    assert scheduled_slot(datetime(2026, 11, 9, 15, 11), config) is None
    assert scheduled_slot(datetime(2026, 11, 8, 10, 0), config) is None
    assert slot_minutes(config) == [558, 588, 618, 648, 678, 708, 738, 768, 798, 828, 858, 888]
    ns = next_slot(datetime(2026, 11, 9, 8, 0), config)
    assert ns["date"] == "2026-11-09" and ns["time"] == "09:18"
    assert ns["in_minutes"] == 78
    ns = next_slot(datetime(2026, 11, 9, 16, 0), config)
    assert ns["date"] == "2026-11-10" and ns["time"] == "09:18"
    ns = next_slot(datetime(2026, 11, 14, 10, 0), config)
    assert ns["date"] == "2026-11-16" and ns["time"] == "09:18"


def test_scheduled_jobs_respect_the_auto_send_switches(monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path

    from app.services import board_settings as bs

    root = Path(__file__).resolve().parents[1]
    sent = []

    class FakeTg:
        configured, chat_ids = True, ["1"]

        def send(self, m):
            sent.append(m)
            return 1

    for name, flag in (("board_alerts", "auto_board"), ("daily_signals", "auto_daily")):
        spec = importlib.util.spec_from_file_location(f"_job_{name}", root / "scripts" / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        monkeypatch.setattr(mod, "TelegramService", FakeTg)
        monkeypatch.setattr(mod, "setup_logging", lambda s: None)
        bs.save(**{flag: False})
        monkeypatch.setattr(sys, "argv", [name])
        assert mod.main() == 0
        assert sent == []


def _raw_minutes(day: str, times: list[str], start=100.0, vol=10.0):
    """Vietcap-style 1-minute payload; `times` are VN wall-clock HH:MM on `day` (epoch = VN time - 7 h)."""
    epoch0 = datetime(1970, 1, 1)
    ts = [str(int((datetime.fromisoformat(f"{day}T{t}") - epoch0).total_seconds()) - 7 * 3600) for t in times]
    return {"t": ts, "c": [start + i for i in range(len(times))], "v": [vol] * len(times)}


def test_build_index_chart_latest_session_ref_and_3_minute_buckets():
    from app.services.priceboard_service import _vn_time, build_index_chart

    raw = _raw_minutes("2026-11-03", ["14:44", "14:45"], start=90)
    today = _raw_minutes("2026-11-04", ["09:15", "09:16", "09:17", "09:18", "13:00"], start=100)
    raw = {k: raw[k] + today[k] for k in raw}
    assert _vn_time(raw["t"][2]).strftime("%Y-%m-%d %H:%M") == "2026-11-04 09:15"
    c1 = build_index_chart("VNINDEX", "VNINDEX", raw, 1)
    assert c1["date"] == "2026-11-04" and len(c1["bars"]) == 5 and c1["ref"] == 91
    assert c1["bars"][0]["time"] == int((datetime(2026, 11, 4, 9, 15) - datetime(1970, 1, 1)).total_seconds())
    c3 = build_index_chart("VNINDEX", "VNINDEX", raw, 3, index={"value": 105, "change": 3})
    assert c3["ref"] == 102
    assert [b["volume"] for b in c3["bars"]] == [30, 10, 10] and [b["value"] for b in c3["bars"]] == [102, 103, 104]
    assert build_index_chart("X", "X", {}, 1)["bars"] == []


def test_trading_today_weekend_holiday_and_session():
    from datetime import date

    class B(FakeBoard):
        payload = {}

        def _minute_bars(self, symbol, count_back=400):
            return self.payload

    b = B()
    B.payload = _raw_minutes("2026-11-04", ["09:15"])
    assert b.trading_today(date(2026, 11, 4)) is True
    B.payload = _raw_minutes("2026-11-03", ["14:45"])
    assert b.trading_today(date(2026, 11, 4)) is False
    B.payload = {}
    assert b.trading_today(date(2026, 11, 4)) is False

def test_board_day_switches_at_0856_and_skips_weekends():
    from app.services.priceboard_service import board_day, is_live

    assert board_day(datetime(2026, 11, 11, 8, 55)) == datetime(2026, 11, 10).date()   # Wed before 08:56 -> Tue
    assert board_day(datetime(2026, 11, 11, 8, 56)) == datetime(2026, 11, 11).date()
    assert board_day(datetime(2026, 11, 16, 7, 0)) == datetime(2026, 11, 13).date()    # Mon morning -> Fri
    assert board_day(datetime(2026, 11, 15, 12, 0)) == datetime(2026, 11, 13).date()   # Sunday -> Fri
    assert not is_live(datetime(2026, 11, 14, 10, 0)) and is_live(datetime(2026, 11, 13, 20, 0))


def test_new_session_chart_starts_empty_at_0900_with_previous_close_as_ref():
    from app.services.priceboard_service import build_index_chart

    raw = _raw_minutes("2026-11-10", ["14:44", "14:45"], start=90)          # yesterday: last close 91
    c = build_index_chart("VNINDEX", "VNINDEX", raw, 1, index={"value": 50, "change": 1},
                          day=datetime(2026, 11, 11).date())
    nine = int((datetime(2026, 11, 11, 9, 0) - datetime(1970, 1, 1)).total_seconds())
    atc = int((datetime(2026, 11, 11, 14, 45) - datetime(1970, 1, 1)).total_seconds())
    assert c["date"] == "2026-11-11" and c["bars"] == [] and c["ref"] == 91   # stale index value ignored
    assert c["axis"][0] == nine and c["axis"][-1] == atc and len(c["axis"]) == 150 + 105 + 1
    c3 = build_index_chart("VNINDEX", "VNINDEX", raw, 3, day=datetime(2026, 11, 11).date())
    assert c3["axis"][0] == nine and c3["axis"][1] - c3["axis"][0] == 180


def test_chart_day_keeps_last_session_on_a_holiday():
    from app.services.priceboard_service import chart_day

    raw = _raw_minutes("2026-11-10", ["14:45"])
    assert chart_day(raw, datetime(2026, 11, 11, 9, 5)).isoformat() == "2026-11-11"    # waiting for 09:00 bars
    assert chart_day(raw, datetime(2026, 11, 11, 9, 20)).isoformat() == "2026-11-10"   # no bar by 09:15: holiday


def test_quotes_and_indexes_are_held_until_0856(monkeypatch):
    from app.services import priceboard_service as pb

    calls = []

    class B(pb.PriceBoardService):
        def _load_scores(self):
            pass

        def _post(self, path, body):
            calls.append(path)
            if "getList" in path:
                return [{"symbol": "VNINDEX", "price": 1700 + len(calls), "change": 5}]
            return [{**RAW, "s": s, "c": 10000 + len(calls)} for s in body["symbols"]]

    monkeypatch.setattr(pb, "QUOTE_TTL", 0)
    monkeypatch.setattr(pb, "SNAPSHOT_SAVE_EVERY", 0)
    monkeypatch.setattr(pb, "_now", lambda: datetime(2026, 11, 10, 14, 0))          # Tue, in session
    b = B()
    live_q = b.quotes(["FPT"])["FPT"]["price"]
    live_i = b.indexes()[0]["value"]
    monkeypatch.setattr(pb, "_now", lambda: datetime(2026, 11, 11, 8, 30))          # Wed pre-open
    b2 = B()                                                                          # also survives a restart
    n = len(calls)
    assert b2.quotes(["FPT"])["FPT"]["price"] == live_q and b2.indexes()[0]["value"] == live_i
    assert len(calls) == n                                                            # nothing fetched
    assert "HPG" in b2.quotes(["FPT", "HPG"]) and len(calls) == n + 1                 # only the new symbol
    monkeypatch.setattr(pb, "_now", lambda: datetime(2026, 11, 11, 8, 56))          # new session: live again
    assert b2.quotes(["FPT"])["FPT"]["price"] != live_q
