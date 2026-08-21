"""Test cho stats.build_variation_report() + trang /variation của
`navtext serve` (đưa data phase 5 — config/variation.yaml — lên web UI).

Gọi route() trực tiếp, KHÔNG mở socket — theo đúng mẫu test_web_templates.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from navtext.build_master import build_master
from navtext.loaders import load_master
from navtext.schema import Register
from navtext.stats import build_master_report, build_readiness_report, build_variation_report
from navtext.templates import load_templates
from navtext.variation import AddressSynthesisConfig, Axis, VariationConfig, load_variation_config

# Placeholder cho các test dựng VariationConfig thủ công (không qua
# load_variation_config()) — chỉ address_synthesis mặc định, các test này
# không đụng tới trục đó.
_DUMMY_ADDRESS_SYNTHESIS = AddressSynthesisConfig(
    house_number_min=1, house_number_max=300, alley_suffix_rate=0.3, letter_suffix_rate=0.15
)
from navtext.web.routes import AppContext, route

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_VARIATION_CONFIG = REPO_ROOT / "config" / "variation.yaml"
REAL_LEXICON = REPO_ROOT / "data" / "templates" / "lexicon.yaml"
REAL_DISTRIBUTION = REPO_ROOT / "config" / "distribution.yaml"
REAL_TEMPLATES_DIR = REPO_ROOT / "data" / "templates"

FIXTURE_RAW = Path(__file__).parent / "fixtures" / "raw"
FIXTURE_OSM_TAGS = Path(__file__).parent / "fixtures" / "config" / "osm_tags.yaml"
FIXTURE_RUN_CONFIG = Path(__file__).parent / "fixtures" / "config" / "run.yaml"
ADMIN_INVARIANTS = {"expected_province_count": 2, "expected_legacy_alias_count": 3}


@pytest.fixture(scope="module")
def variation_config() -> VariationConfig:
    return load_variation_config(REAL_VARIATION_CONFIG)


@pytest.fixture(scope="module")
def lexicon() -> dict:
    return yaml.safe_load(REAL_LEXICON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def distribution() -> dict:
    return yaml.safe_load(REAL_DISTRIBUTION.read_text(encoding="utf-8"))


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


def _minimal_config(**axis_weights: dict[str, float]) -> VariationConfig:
    """Dựng VariationConfig tối giản cho test không cần đọc file YAML —
    chỉ axis được truyền vào có mặt, đủ cho các hàm build_variation_report
    xử lý riêng lẻ (không đi qua validator load_variation_config()).
    """
    axes = {
        name: Axis(name=name, weights=weights, by_register={}, by_category={})
        for name, weights in axis_weights.items()
    }
    return VariationConfig(
        schema_version=1,
        axes=axes,
        lexicon_rates={},
        min_legacy_share_per_province=0.0,
        address_synthesis=_DUMMY_ADDRESS_SYNTHESIS,
    )


# ---------------------------------------------------------------------------
# build_variation_report() — config thật
# ---------------------------------------------------------------------------


def test_report_on_real_config_has_all_axes(variation_config) -> None:
    report = build_variation_report(variation_config)
    axis_names = [a["name"] for a in report["axes"]]
    assert axis_names == [
        "name_variant",
        "legacy_marker",
        "number_style",
        "number_prefix",
        "admin_prefix",
        "hierarchy_depth",
    ]


def test_lexicon_pool_matches_real_lexicon(variation_config, lexicon) -> None:
    report = build_variation_report(variation_config, lexicon=lexicon)
    raw_openers_casual = [v for v in lexicon["openers"]["casual"] if v]
    assert report["lexicon_pool"]["opener"]["casual"] == len(raw_openers_casual)
    raw_fillers = [v for v in lexicon["fillers"] if (v or {}).get("text")]
    # fillers không tách theo register (chỉ tách theo HeadClass) — cùng
    # count tổng cho mọi register.
    assert report["lexicon_pool"]["filler"]["formal"] == len(raw_fillers)
    assert report["lexicon_pool"]["filler"]["casual"] == len(raw_fillers)


def test_variation_space_positive_for_every_register(variation_config, lexicon, distribution) -> None:
    templates = tuple(load_templates(REAL_TEMPLATES_DIR))
    from navtext.stats import build_template_report

    template_report = build_template_report(templates, distribution)
    report = build_variation_report(
        variation_config,
        lexicon=lexicon,
        distribution_config=distribution,
        template_report=template_report,
    )
    assert all(v > 0 for v in report["variation_space"].values())


def test_address_synthesis_in_report(variation_config) -> None:
    """address_synthesis (phase 6, config/variation.yaml) phải xuất hiện
    trong report và trên /variation — trang không tự động show key mới."""
    report = build_variation_report(variation_config)
    assert report["address_synthesis"]["house_number_min"] == 1
    assert report["address_synthesis"]["house_number_max"] == 300
    assert report["address_synthesis"]["alley_suffix_rate"] == pytest.approx(0.30)


def test_no_source_missing_gives_notes_not_raise(variation_config) -> None:
    report = build_variation_report(variation_config)
    assert len(report["notes"]) == 3  # thiếu cả lexicon, template_report, distribution_config
    assert all(v > 0 for v in report["variation_space"].values())  # vẫn tính được, chỉ thiếu hệ số phụ


# ---------------------------------------------------------------------------
# warnings
# ---------------------------------------------------------------------------


def test_dead_value_warning_on_zero_weight() -> None:
    config = _minimal_config(number_prefix={"present": 0.0, "absent": 1.0})
    report = build_variation_report(config)
    dead = [w for w in report["warnings"] if w["id"] == "dead_value"]
    assert any("present" in w["detail"] for w in dead)


def test_empty_lexicon_pool_warning() -> None:
    config = VariationConfig(
        schema_version=1,
        axes={},
        lexicon_rates={"opener": {Register.CASUAL: 0.5}},
        min_legacy_share_per_province=0.0,
        address_synthesis=_DUMMY_ADDRESS_SYNTHESIS,
    )
    report = build_variation_report(config, lexicon={"openers": {"casual": ["", ""]}})
    empty = [w for w in report["warnings"] if w["id"] == "empty_lexicon_pool"]
    assert any("opener" in w["detail"] and "casual" in w["detail"] for w in empty)


def test_space_below_candidates_warning() -> None:
    config = _minimal_config(name_variant={"current": 1.0})
    report = build_variation_report(
        config, distribution_config={"target_size": 100_000, "oversample_factor": 1.3}
    )
    risky = [w for w in report["warnings"] if w["id"] == "space_below_candidates"]
    assert len(risky) == 3  # 1 giá trị sống, không lexicon/template -> space=1 cho mọi register


# ---------------------------------------------------------------------------
# route("/variation")
# ---------------------------------------------------------------------------


def test_variation_route_renders_axis_labels(tmp_path: Path, variation_config, lexicon) -> None:
    report = build_variation_report(variation_config, lexicon=lexicon)
    ctx = _ctx(tmp_path, variation_report=report)
    resp = route("/variation", "", ctx)
    assert resp.status == 200
    body = resp.body.decode("utf-8")
    assert "Biến thể tên" in body
    assert "Applicability" in body or "applicability" in body.lower() or "logic của generator" in body
    assert "Synthesize số nhà" in body


def test_variation_route_without_config_shows_callout(tmp_path: Path) -> None:
    resp = route("/variation", "", _ctx(tmp_path))
    assert resp.status == 200
    assert "Chưa nạp" in resp.body.decode("utf-8")


def test_variation_route_escapes_hostile_values(tmp_path: Path) -> None:
    """Giá trị lạ trong report (vd axis name độc hại) phải đi qua escape()
    — cùng nguyên tắc XSS như phần còn lại của web/ (docs/web.md#104)."""
    config = _minimal_config(**{"name_variant": {"<script>alert(1)</script>": 1.0}})
    report = build_variation_report(config)
    ctx = _ctx(tmp_path, variation_report=report)
    resp = route("/variation", "", ctx)
    body = resp.body.decode("utf-8")
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


# ---------------------------------------------------------------------------
# route("/api/variation.json")
# ---------------------------------------------------------------------------


def test_variation_json_route(tmp_path: Path, variation_config) -> None:
    report = build_variation_report(variation_config)
    ctx = _ctx(tmp_path, variation_report=report)
    resp = route("/api/variation.json", "", ctx)
    assert resp.status == 200
    payload = json.loads(resp.body.decode("utf-8"))
    assert "axes" in payload
    assert "variation_space" in payload


def test_variation_json_404_when_not_loaded(tmp_path: Path) -> None:
    resp = route("/api/variation.json", "", _ctx(tmp_path))
    assert resp.status == 404
    payload = json.loads(resp.body.decode("utf-8"))
    assert "error" in payload


# ---------------------------------------------------------------------------
# readiness
# ---------------------------------------------------------------------------


def test_readiness_variation_check_ok(tmp_path: Path, variation_config) -> None:
    master_dir = tmp_path / "master"
    build_master(
        raw_dir=FIXTURE_RAW,
        master_dir=master_dir,
        osm_tag_config_path=FIXTURE_OSM_TAGS,
        run_config_path=FIXTURE_RUN_CONFIG,
    )
    master = load_master(master_dir)
    report = build_master_report(master, ADMIN_INVARIANTS)
    variation_report = build_variation_report(variation_config)
    readiness = build_readiness_report(
        master, report, None, None, variation_report=variation_report
    )
    check = next(c for c in readiness["checks"] if c["id"] == "variation_config_valid")
    assert check["status"] == "ok"
    assert "6 trục" in check["detail"]


def test_readiness_variation_check_fail_on_issues(tmp_path: Path) -> None:
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
        master, report, None, None, variation_issues=["axes.name_variant.weights: weight âm"]
    )
    check = next(c for c in readiness["checks"] if c["id"] == "variation_config_valid")
    assert check["status"] == "fail"
    assert readiness["overall"] == "fail"
    assert "âm" in check["detail"]


def test_readiness_variation_check_warn_when_not_loaded(tmp_path: Path) -> None:
    master_dir = tmp_path / "master"
    build_master(
        raw_dir=FIXTURE_RAW,
        master_dir=master_dir,
        osm_tag_config_path=FIXTURE_OSM_TAGS,
        run_config_path=FIXTURE_RUN_CONFIG,
    )
    master = load_master(master_dir)
    report = build_master_report(master, ADMIN_INVARIANTS)
    readiness = build_readiness_report(master, report, None, None)
    check = next(c for c in readiness["checks"] if c["id"] == "variation_config_valid")
    assert check["status"] == "warn"


def test_readiness_variation_space_check_reflects_warnings(variation_config) -> None:
    clean_report = build_variation_report(variation_config)
    risky_config = _minimal_config(name_variant={"current": 1.0})
    risky_report = build_variation_report(
        risky_config, distribution_config={"target_size": 100_000, "oversample_factor": 1.3}
    )

    # readiness cần master/master_report thật — dựng nhanh bằng fixture chung.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        master_dir = Path(tmp) / "master"
        build_master(
            raw_dir=FIXTURE_RAW,
            master_dir=master_dir,
            osm_tag_config_path=FIXTURE_OSM_TAGS,
            run_config_path=FIXTURE_RUN_CONFIG,
        )
        master = load_master(master_dir)
        report = build_master_report(master, ADMIN_INVARIANTS)

        readiness_clean = build_readiness_report(
            master, report, None, None, variation_report=clean_report
        )
        readiness_risky = build_readiness_report(
            master, report, None, None, variation_report=risky_report
        )

    clean_check = next(c for c in readiness_clean["checks"] if c["id"] == "variation_space")
    risky_check = next(c for c in readiness_risky["checks"] if c["id"] == "variation_space")
    assert clean_check["status"] == "ok"
    assert risky_check["status"] == "warn"


# ---------------------------------------------------------------------------
# Nav link
# ---------------------------------------------------------------------------


def test_nav_has_variation_link_on_dashboard(tmp_path: Path) -> None:
    resp = route("/", "", _ctx(tmp_path))
    assert 'href="/variation"' in resp.body.decode("utf-8")
