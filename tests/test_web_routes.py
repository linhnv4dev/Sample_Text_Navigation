"""Test cho navtext.web.routes/render/query — gọi route() trực tiếp, KHÔNG
mở socket (xem tests/test_web_server.py cho smoke test 1 lần duy nhất mở
server thật).
"""

from __future__ import annotations

import json
from pathlib import Path

from navtext.build_master import build_master
from navtext.loaders import load_master
from navtext.stats import build_master_report
from navtext.web.query import EntityQuery, all_entities, filter_entities, fold_search_key, paginate
from navtext.web.routes import AppContext, route

FIXTURE_RAW = Path(__file__).parent / "fixtures" / "raw"
FIXTURE_WEB_RAW = Path(__file__).parent / "fixtures" / "web_raw"
FIXTURE_OSM_TAGS = Path(__file__).parent / "fixtures" / "config" / "osm_tags.yaml"
FIXTURE_RUN_CONFIG = Path(__file__).parent / "fixtures" / "config" / "run.yaml"
ADMIN_INVARIANTS = {"expected_province_count": 2, "expected_legacy_alias_count": 3}


def _ctx(tmp_path: Path, raw_dir: Path = FIXTURE_RAW) -> AppContext:
    master_dir = tmp_path / "master"
    build_master(
        raw_dir=raw_dir,
        master_dir=master_dir,
        osm_tag_config_path=FIXTURE_OSM_TAGS,
        run_config_path=FIXTURE_RUN_CONFIG,
    )
    master = load_master(master_dir)
    report = build_master_report(master, ADMIN_INVARIANTS)
    return AppContext(master=master, report=report)


# ---------------------------------------------------------------------------
# route() — endpoints
# ---------------------------------------------------------------------------


def test_dashboard_returns_200_and_mentions_coverage_note(tmp_path: Path) -> None:
    resp = route("/", "", _ctx(tmp_path))
    assert resp.status == 200
    body = resp.body.decode("utf-8")
    assert "MASTER DATA" in body  # phải phân biệt rõ với coverage phase 12


def test_provinces_list_returns_200(tmp_path: Path) -> None:
    resp = route("/provinces", "", _ctx(tmp_path))
    assert resp.status == 200
    body = resp.body.decode("utf-8")
    assert "Tỉnh Một" in body
    assert "Tỉnh Hai" in body


def test_province_detail_returns_200_for_existing_province(tmp_path: Path) -> None:
    resp = route("/provinces/01", "", _ctx(tmp_path))
    assert resp.status == 200
    body = resp.body.decode("utf-8")
    assert "Tỉnh Một Cũ" in body  # legacy alias
    assert "Một" in body  # spoken alias


def test_province_detail_404_for_unknown_province(tmp_path: Path) -> None:
    resp = route("/provinces/99", "", _ctx(tmp_path))
    assert resp.status == 404


def test_unknown_path_returns_404(tmp_path: Path) -> None:
    resp = route("/khong-ton-tai", "", _ctx(tmp_path))
    assert resp.status == 404


def test_entities_returns_200(tmp_path: Path) -> None:
    resp = route("/entities", "", _ctx(tmp_path))
    assert resp.status == 200


def test_static_css_is_served(tmp_path: Path) -> None:
    resp = route("/static/app.css", "", _ctx(tmp_path))
    assert resp.status == 200
    assert resp.content_type.startswith("text/css")
    assert b"--color-accent" in resp.body


def test_static_path_traversal_is_not_served(tmp_path: Path) -> None:
    # Allowlist tường minh: path lạ (kể cả cố tình traversal) không khớp
    # key nào trong _STATIC nên rơi thẳng xuống 404, không đọc file nào.
    resp = route("/static/../../etc/passwd", "", _ctx(tmp_path))
    assert resp.status == 404


def test_provinces_page_sortable_by_entities(tmp_path: Path) -> None:
    resp = route("/provinces", "sort=entities", _ctx(tmp_path))
    assert resp.status == 200
    body = resp.body.decode("utf-8")
    assert "sort=entities" in body


def test_entities_page_has_select_dropdowns_not_free_text(tmp_path: Path) -> None:
    """Filter phải là <select> có option, không phải ô text trống người
    dùng phải tự đoán giá trị hợp lệ."""
    resp = route("/entities", "", _ctx(tmp_path))
    body = resp.body.decode("utf-8")
    assert '<select name="category">' in body
    assert '<select name="province">' in body
    assert '<select name="subcategory">' in body


def test_entities_filter_by_province(tmp_path: Path) -> None:
    resp = route("/entities", "province=02", _ctx(tmp_path))
    body = resp.body.decode("utf-8")
    # Tỉnh 02 không có entity nào trong fixture.
    assert "Không có kết quả" in body


def test_entities_filter_by_category(tmp_path: Path) -> None:
    resp = route("/entities", "category=public_facility", _ctx(tmp_path))
    body = resp.body.decode("utf-8")
    assert "Bệnh Viện Test" in body
    assert "Đường Test" not in body


def test_api_report_json_is_valid_and_matches_report(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    resp = route("/api/report.json", "", ctx)
    assert resp.status == 200
    parsed = json.loads(resp.body)
    assert parsed["totals"] == ctx.report["totals"]


# ---------------------------------------------------------------------------
# XSS: tên entity độc phải bị escape
# ---------------------------------------------------------------------------


def test_malicious_entity_name_is_escaped_in_entities_page(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, raw_dir=FIXTURE_WEB_RAW)
    resp = route("/entities", "q=Vien", ctx)
    body = resp.body.decode("utf-8")
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_malicious_entity_name_is_escaped_in_province_detail(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, raw_dir=FIXTURE_WEB_RAW)
    resp = route("/provinces/01", "", ctx)
    body = resp.body.decode("utf-8")
    assert "<script>alert(1)</script>" not in body


def test_query_string_reflected_in_form_is_escaped(tmp_path: Path) -> None:
    """?q= chứa payload phải bị escape khi web render lại giá trị vào
    value="..." của input, không chỉ dữ liệu từ master."""
    ctx = _ctx(tmp_path)
    resp = route("/entities", "q=%22%3E%3Cscript%3Ealert(2)%3C%2Fscript%3E", ctx)
    body = resp.body.decode("utf-8")
    assert "<script>alert(2)</script>" not in body


# ---------------------------------------------------------------------------
# query.py — filter/search/paginate (hàm thuần)
# ---------------------------------------------------------------------------


def test_fold_search_key_matches_accent_insensitive() -> None:
    assert fold_search_key("Nguyễn Huệ") == fold_search_key("nguyen hue")


def test_filter_entities_by_search_query(tmp_path: Path) -> None:
    master = load_master(_build_master_dir(tmp_path))
    entities = all_entities(master)
    result = filter_entities(entities, EntityQuery(q="duong test"))
    assert len(result) == 1
    assert result[0].name == "Đường Test"


def _build_master_dir(tmp_path: Path) -> Path:
    master_dir = tmp_path / "master_for_query"
    build_master(
        raw_dir=FIXTURE_RAW,
        master_dir=master_dir,
        osm_tag_config_path=FIXTURE_OSM_TAGS,
        run_config_path=FIXTURE_RUN_CONFIG,
    )
    return master_dir


def test_paginate_splits_without_overlap_or_gap() -> None:
    class Fake:
        def __init__(self, entity_id: str) -> None:
            self.entity_id = entity_id

    items = [Fake(f"e{i}") for i in range(25)]
    page1, total = paginate(items, page=1, page_size=10)
    page2, _ = paginate(items, page=2, page_size=10)
    page3, _ = paginate(items, page=3, page_size=10)

    assert total == 25
    assert [i.entity_id for i in page1] == [f"e{i}" for i in range(0, 10)]
    assert [i.entity_id for i in page2] == [f"e{i}" for i in range(10, 20)]
    assert [i.entity_id for i in page3] == [f"e{i}" for i in range(20, 25)]


def test_paginate_out_of_range_page_returns_empty() -> None:
    class Fake:
        def __init__(self, entity_id: str) -> None:
            self.entity_id = entity_id

    items = [Fake("e0")]
    page, total = paginate(items, page=99, page_size=10)
    assert page == []
    assert total == 1
