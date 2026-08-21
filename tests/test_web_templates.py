"""Test cho lớp template/readiness/config/raw của `navtext serve`.

Gọi route() trực tiếp, KHÔNG mở socket — theo đúng mẫu test_web_routes.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from navtext.build_master import build_master, read_raw
from navtext.loaders import load_master
from navtext.stats import (
    build_master_report,
    build_raw_report,
    build_readiness_report,
    build_template_report,
)
from navtext.templates import load_lexicon, load_templates
from navtext.web.query import TemplateQuery, filter_templates, paginate
from navtext.web.routes import AppContext, route

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_TEMPLATES_DIR = REPO_ROOT / "data" / "templates"
REAL_DISTRIBUTION = REPO_ROOT / "config" / "distribution.yaml"

FIXTURE_RAW = Path(__file__).parent / "fixtures" / "raw"
FIXTURE_OSM_TAGS = Path(__file__).parent / "fixtures" / "config" / "osm_tags.yaml"
FIXTURE_RUN_CONFIG = Path(__file__).parent / "fixtures" / "config" / "run.yaml"
ADMIN_INVARIANTS = {"expected_province_count": 2, "expected_legacy_alias_count": 3}


@pytest.fixture(scope="module")
def templates() -> tuple:
    return tuple(load_templates(REAL_TEMPLATES_DIR))


@pytest.fixture(scope="module")
def distribution() -> dict:
    return yaml.safe_load(REAL_DISTRIBUTION.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def template_report(templates, distribution) -> dict:
    return build_template_report(templates, distribution)


@pytest.fixture(scope="module")
def lexicon():
    return load_lexicon(REAL_TEMPLATES_DIR)


def _ctx(tmp_path: Path, **overrides) -> AppContext:
    master_dir = tmp_path / "master"
    build_master(
        raw_dir=FIXTURE_RAW,
        master_dir=master_dir,
        osm_tag_config_path=FIXTURE_OSM_TAGS,
        run_config_path=FIXTURE_RUN_CONFIG,
    )
    master = load_master(master_dir)
    report = build_master_report(master, ADMIN_INVARIANTS)
    return AppContext(master=master, report=report, **overrides)


# ---------------------------------------------------------------------------
# build_template_report
# ---------------------------------------------------------------------------


def test_template_report_matches_real_bank(template_report) -> None:
    assert template_report["total"] == 300
    assert template_report["cell_count"] == 30
    assert template_report["thin_cells"] == []
    assert template_report["cap_risks"] == []


def test_template_report_matrix_shape(template_report) -> None:
    matrix = template_report["coverage_matrix"]
    assert len(matrix) == 6  # 6 sentence_type
    for row in matrix:
        assert len(row["cells"]) == 5  # 5 variation_profile
        assert all(c["count"] == 10 for c in row["cells"])


def test_template_report_flags_thin_cell(templates, distribution) -> None:
    """Bỏ bớt template của một ô -> ô đó phải bị đánh dấu thin."""
    reduced = tuple(
        t
        for t in templates
        if not (t.sentence_type.value == "search" and t.register.value == "casual" and t.length_class.value == "short")
    )
    report = build_template_report(reduced, distribution)
    assert len(report["thin_cells"]) == 1
    thin = report["thin_cells"][0]
    assert thin["sentence_type"] == "search"
    assert thin["profile"] == "casual_short"
    assert thin["count"] == 0


def test_template_report_length_stats_ordered(template_report) -> None:
    stats = template_report["length_stats"]
    assert stats["short"]["median"] < stats["medium"]["median"] < stats["long"]["median"]


# ---------------------------------------------------------------------------
# filter_templates / paginate
# ---------------------------------------------------------------------------


def test_filter_by_sentence_type(templates) -> None:
    result = filter_templates(templates, TemplateQuery(sentence_type="short_command"))
    assert len(result) == 50
    assert all(t.sentence_type.value == "short_command" for t in result)


def test_filter_combines_axes(templates) -> None:
    result = filter_templates(
        templates, TemplateQuery(sentence_type="nav_command", register="casual", length_class="long")
    )
    assert len(result) == 10


def test_filter_search_is_accent_insensitive(templates) -> None:
    with_accent = filter_templates(templates, TemplateQuery(q="chỉ đường"))
    without_accent = filter_templates(templates, TemplateQuery(q="chi duong"))
    assert len(with_accent) > 0
    assert [t.template_id for t in with_accent] == [t.template_id for t in without_accent]


def test_filter_search_matches_template_id(templates) -> None:
    result = filter_templates(templates, TemplateQuery(q="contextual_formal"))
    assert len(result) == 10


def test_paginate_is_generic_over_templates(templates) -> None:
    """paginate() dùng chung cho LocationRef lẫn Template — cùng một hàm,
    không bản sao riêng cho từng kiểu."""
    all_templates = list(templates)
    page1, total = paginate(all_templates, page=1, page_size=100)
    page2, _ = paginate(all_templates, page=2, page_size=100)
    assert total == 300
    assert len(page1) == len(page2) == 100
    assert page1 == all_templates[:100]
    assert page2 == all_templates[100:200]
    assert not set(t.template_id for t in page1) & set(t.template_id for t in page2)


# ---------------------------------------------------------------------------
# route() — trang mới
# ---------------------------------------------------------------------------


def test_templates_route_renders_matrix(tmp_path: Path, templates, template_report) -> None:
    ctx = _ctx(tmp_path, templates=templates, template_report=template_report)
    resp = route("/templates", "", ctx)
    assert resp.status == 200
    body = resp.body.decode("utf-8")
    assert "matrix-cell" in body
    assert "300" in body


def test_templates_route_applies_filter(tmp_path: Path, templates, template_report) -> None:
    ctx = _ctx(tmp_path, templates=templates, template_report=template_report)
    resp = route("/templates", "sentence_type=short_command", ctx)
    body = resp.body.decode("utf-8")
    assert "50 kết quả" in body


def test_templates_route_without_bank_shows_callout(tmp_path: Path) -> None:
    """Thiếu template bank KHÔNG được làm 500 — server vẫn phải xem được
    master data (xem docstring AppContext)."""
    resp = route("/templates", "", _ctx(tmp_path))
    assert resp.status == 200
    assert "Chưa nạp" in resp.body.decode("utf-8")


def test_templates_route_renders_lexicon_with_real_data(
    tmp_path: Path, templates, template_report, lexicon
) -> None:
    """Regression: render_templates() từng tự đọc lexicon.yaml thô, giả định
    openers là list[str] — schema thật là list[{text, heads}] (phase 5) nên
    escape() crash với AttributeError. Test cũ luôn gọi _ctx() không truyền
    lexicon (mặc định None) nên không bao giờ chạy nhánh này — bug lọt tới
    production. Test này PHẢI dùng lexicon thật (không phải None/dict rỗng)
    để bịt đúng khe hở đó."""
    ctx = _ctx(tmp_path, templates=templates, template_report=template_report, lexicon=lexicon)
    resp = route("/templates", "", ctx)
    assert resp.status == 200
    body = resp.body.decode("utf-8")
    assert "xin cho hỏi" in body  # opener thật, chứng minh Opener.text được đọc đúng
    for section in ("openers", "closers", "closers_after_question", "closers_after_particle"):
        assert section in body


def test_config_route_renders_weights(tmp_path: Path, distribution) -> None:
    ctx = _ctx(tmp_path, distribution_config=distribution)
    resp = route("/config", "", ctx)
    assert resp.status == 200
    body = resp.body.decode("utf-8")
    assert "0.015" in body  # caps.template.max_share
    assert "formal_medium" in body


def test_config_route_renders_entity_weights(tmp_path: Path, distribution) -> None:
    """entity_weights.by_tier (phase 6) phải hiện trên /config — trang này
    liệt kê field thủ công, không tự động show key mới."""
    ctx = _ctx(tmp_path, distribution_config=distribution)
    resp = route("/config", "", ctx)
    body = resp.body.decode("utf-8")
    assert "Trọng số entity theo tier" in body


def test_config_route_renders_province_weight_overrides(tmp_path: Path, distribution) -> None:
    """province_weights.overrides (Đà Nẵng tier 1, hệ số tinh chỉnh riêng)
    phải hiện trên /config — không chỉ sống âm thầm trong build_plan."""
    ctx = _ctx(tmp_path, distribution_config=distribution)
    resp = route("/config", "", ctx)
    body = resp.body.decode("utf-8")
    assert "Override 04" in body


def test_config_route_without_config_shows_callout(tmp_path: Path) -> None:
    resp = route("/config", "", _ctx(tmp_path))
    assert resp.status == 200
    assert "Chưa nạp" in resp.body.decode("utf-8")


def test_raw_route_renders_reconciliation(tmp_path: Path) -> None:
    master_dir = tmp_path / "master"
    build_master(
        raw_dir=FIXTURE_RAW,
        master_dir=master_dir,
        osm_tag_config_path=FIXTURE_OSM_TAGS,
        run_config_path=FIXTURE_RUN_CONFIG,
    )
    master = load_master(master_dir)
    raw_report = build_raw_report(read_raw(FIXTURE_RAW), master)
    ctx = AppContext(
        master=master,
        report=build_master_report(master, ADMIN_INVARIANTS),
        raw_report=raw_report,
    )
    resp = route("/raw", "", ctx)
    assert resp.status == 200
    assert "khớp" in resp.body.decode("utf-8")


# ---------------------------------------------------------------------------
# readiness
# ---------------------------------------------------------------------------


def test_readiness_reports_template_issues(tmp_path: Path) -> None:
    """TemplateError phải thành check FAIL hiển thị được, không phải crash."""
    master_dir = tmp_path / "master"
    build_master(
        raw_dir=FIXTURE_RAW,
        master_dir=master_dir,
        osm_tag_config_path=FIXTURE_OSM_TAGS,
        run_config_path=FIXTURE_RUN_CONFIG,
    )
    master = load_master(master_dir)
    report = build_master_report(master, ADMIN_INVARIANTS)
    readiness = build_readiness_report(
        master, report, None, None, template_issues=["template 'x': text chứa chữ số"]
    )
    assert readiness["overall"] == "fail"
    bank_check = next(c for c in readiness["checks"] if c["id"] == "template_bank_valid")
    assert bank_check["status"] == "fail"
    assert "chữ số" in bank_check["detail"]


def test_readiness_route_and_json(tmp_path: Path, templates, template_report, distribution) -> None:
    master_dir = tmp_path / "master"
    build_master(
        raw_dir=FIXTURE_RAW,
        master_dir=master_dir,
        osm_tag_config_path=FIXTURE_OSM_TAGS,
        run_config_path=FIXTURE_RUN_CONFIG,
    )
    master = load_master(master_dir)
    report = build_master_report(master, ADMIN_INVARIANTS)
    readiness = build_readiness_report(master, report, template_report, None)
    ctx = AppContext(master=master, report=report, templates=templates, readiness=readiness)

    resp = route("/readiness", "", ctx)
    assert resp.status == 200
    assert "check-list" in resp.body.decode("utf-8")

    api = route("/api/readiness.json", "", ctx)
    assert api.status == 200
    payload = json.loads(api.body.decode("utf-8"))
    assert payload["overall"] in {"ok", "warn", "fail"}
    assert {c["id"] for c in payload["checks"]} >= {"template_coverage", "province_entities"}


def test_readiness_json_404_when_not_loaded(tmp_path: Path) -> None:
    resp = route("/api/readiness.json", "", _ctx(tmp_path))
    assert resp.status == 404


# ---------------------------------------------------------------------------
# Footer — mốc khởi động server
# ---------------------------------------------------------------------------


def test_footer_shows_started_at(tmp_path: Path, templates, template_report) -> None:
    """Mốc khởi động phải có mặt trên MỌI trang: đây là thứ phân biệt
    "UI hỏng" với "server đang chạy code cũ, cần restart"."""
    ctx = _ctx(
        tmp_path,
        templates=templates,
        template_report=template_report,
        started_at="12:00:00 15/08/2026",
    )
    for path in ("/", "/provinces", "/entities", "/templates", "/khong-ton-tai"):
        body = route(path, "", ctx).body.decode("utf-8")
        assert "12:00:00 15/08/2026" in body, f"thiếu mốc khởi động ở {path}"
        assert "restart" in body


def test_footer_reports_template_count(tmp_path: Path, templates, template_report) -> None:
    ctx = _ctx(
        tmp_path, templates=templates, template_report=template_report, started_at="12:00:00"
    )
    assert "300 template" in route("/", "", ctx).body.decode("utf-8")

    empty = _ctx(tmp_path, started_at="12:00:00")
    assert "chưa nạp template bank" in route("/", "", empty).body.decode("utf-8")


def test_footer_absent_when_started_at_empty(tmp_path: Path) -> None:
    """Default rỗng -> HTML không đổi, giữ tương thích với test dựng
    AppContext tối giản."""
    body = route("/", "", _ctx(tmp_path)).body.decode("utf-8")
    assert "page-footer" not in body
    assert "server khởi động" not in body


def test_footer_injected_before_main_close(tmp_path: Path) -> None:
    body = route("/", "", _ctx(tmp_path, started_at="12:00:00")).body.decode("utf-8")
    assert body.count('<footer class="page-footer">') == 1
    assert body.count("</main>") == 1
    assert body.index("page-footer") < body.index("</main>")
