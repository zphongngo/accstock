import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_board_service
from app.main import app
from app.services import sectors as sec

TEMPLATE = ("Ngành\tMã\nNgân Hàng\tBID\nNgân Hàng\tCTG\nNgân Hàng\tVCB\nNgân Hàng\tVPB\n"
            "Chứng Khoán\tSSI\nChứng Khoán\tVND\nChứng Khoán\tTCX\nChứng Khoán\tHCM\n")
EXPECTED = [("Ngân Hàng", ["BID", "CTG", "VCB", "VPB"]), ("Chứng Khoán", ["SSI", "VND", "TCX", "HCM"])]


@pytest.fixture
def tmp_sectors(tmp_path, monkeypatch):
    path = tmp_path / "sectors.json"
    monkeypatch.setattr(sec, "sectors_path", lambda: path)
    return path


def test_parse_user_template_tab_separated():
    parsed, warnings = sec.parse_table(TEMPLATE.encode("utf-8"), "nganh.txt")
    assert parsed == EXPECTED and warnings == []


def test_xlsx_round_trip():
    data = sec.to_xlsx(EXPECTED)
    assert data[:2] == b"PK"
    parsed, warnings = sec.parse_table(data, "x.xlsx")
    assert parsed == EXPECTED and warnings == []


def test_parse_is_forgiving_and_reports_bad_rows():
    text = ("Mã;Ngành\nbid;ngân hàng\nCTG;Ngân Hàng\n\nBID;NGÂN HÀNG\n???;Thép\nHPG;\nHPG;Thép\n").encode("utf-8")
    parsed, warnings = sec.parse_table(text, "x.csv")
    # columns found by header name, sector names merged ignoring case/accents, first spelling kept
    assert parsed == [("ngân hàng", ["BID", "CTG"]), ("Thép", ["HPG"])]
    assert len(warnings) == 3 and warnings[0].startswith("Dòng 5:")   # spreadsheet row numbers
    no_header, _ = sec.parse_table(b"Thep,HPG\nThep,HSG\n", "x.csv")
    assert no_header == [("Thep", ["HPG", "HSG"])]
    with pytest.raises(ValueError):
        sec.parse_table(b"Nganh,Ma\n", "x.csv")


def test_save_get_reset(tmp_sectors):
    assert sec.get_sectors() is sec.DEFAULT_SECTORS and not sec.is_custom()
    sec.save_sectors(EXPECTED)
    assert sec.get_sectors() == EXPECTED and sec.is_custom()
    assert sec.all_symbols() == ["BID", "CTG", "VCB", "VPB", "SSI", "VND", "TCX", "HCM"]
    sec.reset_sectors()
    assert sec.get_sectors() is sec.DEFAULT_SECTORS


def test_import_export_endpoints_and_board(tmp_sectors):
    from tests.test_priceboard import FakeBoard

    app.dependency_overrides[get_board_service] = FakeBoard
    try:
        c = TestClient(app)
        r = c.post("/api/board/sectors/import?filename=nganh.xlsx", content=sec.to_xlsx(EXPECTED),
                   headers={"Content-Type": "application/octet-stream"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["custom"] and (d["groups"], d["symbols"], d["warning_count"]) == (2, 8, 0)
        names = [g["name"] for g in c.get("/api/board").json()["groups"]]
        assert names == ["Quan Tâm", "VN30", "Ngân Hàng", "Chứng Khoán"]   # old sectors gone, pinned tables stay
        x = c.get("/api/board/sectors.xlsx?watch=HPG,MWG")
        assert x.status_code == 200 and "attachment" in x.headers["content-disposition"]
        assert sec.parse_table(x.content, "a.xlsx")[0] == [("Quan Tâm", ["HPG", "MWG"]), ("VN30", ["FPT"])] + EXPECTED
        assert c.post("/api/board/sectors/import", content=b"").status_code == 400
        assert c.post("/api/board/sectors/import", content=b"Nganh,Ma\n").status_code == 400
        assert c.delete("/api/board/sectors").json()["custom"] is False
        assert not tmp_sectors.exists()
    finally:
        app.dependency_overrides.pop(get_board_service, None)


def test_import_deletes_everything_first_but_bad_file_deletes_nothing(tmp_sectors):
    from tests.test_priceboard import FakeBoard

    board = FakeBoard()
    cleared = []
    board.clear = lambda: cleared.append(True)
    app.dependency_overrides[get_board_service] = lambda: board
    try:
        c = TestClient(app)
        sec.save_sectors([("Thép", ["HPG", "HSG"]), ("Ngân Hàng", ["ACB"])])
        # a bad file is rejected before anything is deleted
        assert c.post("/api/board/sectors/import?filename=x.csv", content=b"Nganh,Ma\n").status_code == 400
        assert sec.get_sectors() == [("Thép", ["HPG", "HSG"]), ("Ngân Hàng", ["ACB"])] and not cleared
        # a good file: old groups/symbols are not merged in, the cached board is cleared
        r = c.post("/api/board/sectors/import?filename=x.txt", content=TEMPLATE.encode("utf-8"))
        assert r.status_code == 200 and sec.get_sectors() == EXPECTED and cleared == [True]
        quotes = c.get("/api/board").json()["quotes"]
        assert "HPG" not in quotes and "HSG" not in quotes and "ACB" not in quotes
    finally:
        app.dependency_overrides.pop(get_board_service, None)


def test_default_list_pins_quan_tam_and_vn30_and_exports_vn30(tmp_sectors):
    from tests.test_priceboard import FakeBoard

    groups = FakeBoard().board()["groups"]
    assert [g["name"] for g in groups][:2] == ["Quan Tâm", "VN30"] and groups[0]["symbols"] == []  # empty but shown
    app.dependency_overrides[get_board_service] = FakeBoard
    try:
        x = TestClient(app).get("/api/board/sectors.xlsx")
    finally:
        app.dependency_overrides.pop(get_board_service, None)
    parsed, _ = sec.parse_table(x.content, "a.xlsx")
    assert parsed[0] == ("VN30", ["FPT"]) and parsed[1:] == sec.DEFAULT_SECTORS


def test_own_vn30_group_replaces_the_automatic_one(tmp_sectors):
    from tests.test_priceboard import FakeBoard

    sec.save_sectors([("Thép", ["HPG"]), ("vn30", ["FPT", "HPG"])])
    groups = FakeBoard().board()["groups"]
    assert [g["name"] for g in groups] == ["Quan Tâm", "VN30", "Thép"] and set(groups[1]["symbols"]) == {"FPT", "HPG"}


def test_import_sends_quan_tam_to_the_browser_and_keeps_own_vn30(tmp_sectors):
    from tests.test_priceboard import FakeBoard

    file = [("Thép", ["HPG"]), ("Quan tâm", ["MWG", "FPT"]), ("VN30", ["VCB", "BID"])]
    app.dependency_overrides[get_board_service] = FakeBoard
    try:
        d = TestClient(app).post("/api/board/sectors/import?filename=x.xlsx", content=sec.to_xlsx(file)).json()
    finally:
        app.dependency_overrides.pop(get_board_service, None)
    assert d["watch"] == ["MWG", "FPT"]                      # the browser keeps the Quan Tâm list
    assert sec.get_sectors() == [("VN30", ["VCB", "BID"]), ("Thép", ["HPG"])]
    assert sec.split_pinned([("Danh mục", ["A1"]), ("x", ["B1"])]) == (["A1"], None, [("x", ["B1"])])
