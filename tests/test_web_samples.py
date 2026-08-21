"""Test cho /samples + /api/samples.json (navtext serve) — xem CSV đã
generate. Gọi route() trực tiếp, KHÔNG mở socket, theo đúng mẫu
tests/test_web_routes.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from navtext.build_master import build_master
from navtext.loaders import load_master
from navtext.stats import build_master_report
from navtext.web.query import SampleQuery, filter_samples, summarize_samples
from navtext.web.routes import AppContext, route

FIXTURE_RAW = Path(__file__).parent / "fixtures" / "raw"
FIXTURE_OSM_TAGS = Path(__file__).parent / "fixtures" / "config" / "osm_tags.yaml"
FIXTURE_RUN_CONFIG = Path(__file__).parent / "fixtures" / "config" / "run.yaml"
ADMIN_INVARIANTS = {"expected_province_count": 2, "expected_legacy_alias_count": 3}

_ROWS = [
    {
        "id": "1",
        "text": "chỉ đường đến Hà Nội",
        "category": "admin_unit",
        "subcategory": "province",
        "entity": "Hà Nội",
        "entity_id": "province_01",
        "province": "Hà Nội",
        "province_legacy": "Hà Nội",
        "district": "",
        "ward": "",
        "street": "",
        "address": "",
        "sentence_type": "nav_command",
        "template_id": "t1",
        "variation": "legacy+none+no_prefix+casual",
        "name_variant": "legacy",
        "number_style": "none",
        "register": "casual",
        "source": "manual",
        "seed_cell": "01|admin_unit|nav_command|casual_short",
    },
    {
        "id": "2",
        "text": "tìm đường Nguyễn Văn Linh giúp tôi",
        "category": "street",
        "subcategory": "street",
        "entity": "đường Nguyễn Văn Linh",
        "entity_id": "s1",
        "province": "Đà Nẵng",
        "province_legacy": "",
        "district": "",
        "ward": "",
        "street": "Nguyễn Văn Linh",
        "address": "",
        "sentence_type": "search",
        "template_id": "t2",
        "variation": "current+none+prefix+neutral",
        "name_variant": "current",
        "number_style": "none",
        "register": "neutral",
        "source": "osm",
        "seed_cell": "04|street|search|neutral_short",
    },
]


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


def _ctx_with_samples(tmp_path: Path) -> AppContext:
    return _ctx(
        tmp_path,
        samples=tuple(_ROWS),
        samples_summary=summarize_samples(_ROWS),
        samples_source="outputs/demo/prototype.csv",
    )


# ---------------------------------------------------------------------------
# web.query.filter_samples / summarize_samples — hàm thuần
# ---------------------------------------------------------------------------


def test_filter_samples_by_province() -> None:
    result = filter_samples(_ROWS, SampleQuery(province="Hà Nội"))
    assert [r["id"] for r in result] == ["1"]


def test_filter_samples_by_category() -> None:
    result = filter_samples(_ROWS, SampleQuery(category="street"))
    assert [r["id"] for r in result] == ["2"]


def test_filter_samples_q_folds_diacritics() -> None:
    result = filter_samples(_ROWS, SampleQuery(q="nguyen van linh"))
    assert [r["id"] for r in result] == ["2"]


def test_filter_samples_no_filter_returns_all() -> None:
    assert filter_samples(_ROWS, SampleQuery()) == _ROWS


def test_summarize_samples_counts() -> None:
    summary = summarize_samples(_ROWS)
    assert summary["total"] == 2
    assert summary["by_category"] == {"admin_unit": 1, "street": 1}
    assert summary["distinct_provinces"] == 2
    assert summary["distinct_templates"] == 2
    assert summary["legacy_mentions"] == 1


# ---------------------------------------------------------------------------
# route("/samples")
# ---------------------------------------------------------------------------


def test_samples_route_without_source_shows_callout(tmp_path: Path) -> None:
    resp = route("/samples", "", _ctx(tmp_path))
    assert resp.status == 200
    assert "Chưa nạp" in resp.body.decode("utf-8")


def test_samples_route_shows_stage_label_from_filename(tmp_path: Path) -> None:
    """samples_source="outputs/demo/prototype.csv" -> banner phải nói rõ đây
    là giai đoạn prototype (chưa qua QC), không chỉ hiện đường dẫn trơ."""
    resp = route("/samples", "", _ctx_with_samples(tmp_path))
    body = resp.body.decode("utf-8")
    assert "outputs/demo/prototype.csv" in body
    assert "CHƯA qua QC" in body
    assert "Tự động chọn" not in body  # _ctx_with_samples không set samples_auto


def test_samples_route_shows_auto_discovery_banner_when_auto(tmp_path: Path) -> None:
    ctx = _ctx(
        tmp_path,
        samples=tuple(_ROWS),
        samples_summary=summarize_samples(_ROWS),
        samples_source="outputs/latest_run/final_100k.csv",
        samples_auto=True,
    )
    resp = route("/samples", "", ctx)
    body = resp.body.decode("utf-8")
    assert "Tự động chọn" in body
    assert "--no-auto-samples" in body


def test_samples_route_renders_table(tmp_path: Path) -> None:
    resp = route("/samples", "", _ctx_with_samples(tmp_path))
    assert resp.status == 200
    body = resp.body.decode("utf-8")
    assert "Hà Nội" in body
    assert "Nguyễn Văn Linh" in body
    assert "2 kết quả" in body


def test_samples_route_applies_province_filter(tmp_path: Path) -> None:
    resp = route("/samples", "province=Hà+Nội", _ctx_with_samples(tmp_path))
    body = resp.body.decode("utf-8")
    assert "chỉ đường đến Hà Nội" in body
    assert "1 kết quả" in body


def test_samples_route_escapes_hostile_text(tmp_path: Path) -> None:
    """Text sample là dữ liệu do generator sinh từ tên OSM — cùng nguyên
    tắc XSS như các trang khác (docs/web.md#104)."""
    hostile_row = dict(_ROWS[0])
    hostile_row["text"] = "<script>alert(1)</script>"
    ctx = _ctx(
        tmp_path,
        samples=(hostile_row,),
        samples_summary=summarize_samples([hostile_row]),
        samples_source="x.csv",
    )
    resp = route("/samples", "", ctx)
    body = resp.body.decode("utf-8")
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


# ---------------------------------------------------------------------------
# route("/api/samples.json")
# ---------------------------------------------------------------------------


def test_samples_json_route_returns_aggregate_not_rows(tmp_path: Path) -> None:
    resp = route("/api/samples.json", "", _ctx_with_samples(tmp_path))
    assert resp.status == 200
    payload = json.loads(resp.body.decode("utf-8"))
    assert payload["total"] == 2
    assert payload["source"] == "outputs/demo/prototype.csv"
    assert payload["summary"]["distinct_templates"] == 2
    assert "items" not in payload  # aggregate, không dump toàn bộ hàng


def test_samples_json_route_without_source_404(tmp_path: Path) -> None:
    resp = route("/api/samples.json", "", _ctx(tmp_path))
    assert resp.status == 404
