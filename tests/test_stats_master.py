"""Test cho navtext.stats.build_master_report — dùng fixture tí hon ở
tests/fixtures/raw/ (giống test_build_master.py/test_loaders.py), không
phụ thuộc data/master/ thật.
"""

from __future__ import annotations

from pathlib import Path

from navtext.build_master import build_master
from navtext.loaders import load_master
from navtext.stats import build_master_report

FIXTURE_RAW = Path(__file__).parent / "fixtures" / "raw"
FIXTURE_OSM_TAGS = Path(__file__).parent / "fixtures" / "config" / "osm_tags.yaml"
FIXTURE_RUN_CONFIG = Path(__file__).parent / "fixtures" / "config" / "run.yaml"

ADMIN_INVARIANTS = {"expected_province_count": 2, "expected_legacy_alias_count": 3}


def _master(tmp_path: Path):
    master_dir = tmp_path / "master"
    build_master(
        raw_dir=FIXTURE_RAW,
        master_dir=master_dir,
        osm_tag_config_path=FIXTURE_OSM_TAGS,
        run_config_path=FIXTURE_RUN_CONFIG,
    )
    return load_master(master_dir)


def test_totals_match_fixture(tmp_path: Path) -> None:
    report = build_master_report(_master(tmp_path), ADMIN_INVARIANTS)
    totals = report["totals"]
    assert totals["provinces"] == 2
    assert totals["province_alias"] == 4  # 3 legacy (1+2) + 1 spoken ("Một" của tỉnh 01)
    assert totals["districts"] == 2
    assert totals["wards"] == 2
    assert totals["streets"] == 1  # 2 way segment cùng tên đã dedupe ở build_master
    assert totals["pois"] == 1


def test_master_coverage_ok_when_matching_invariants(tmp_path: Path) -> None:
    report = build_master_report(_master(tmp_path), ADMIN_INVARIANTS)
    coverage = report["master_coverage"]
    assert coverage["province_count_ok"] is True
    assert coverage["legacy_alias_count_ok"] is True


def test_master_coverage_flags_mismatch() -> None:
    # Không cần build gì — chỉ cần MasterData rỗng để test nhánh mismatch.
    from navtext.loaders import MasterData

    empty = MasterData(
        provinces={},
        aliases_by_province={},
        province_id_by_alias={},
        districts={},
        wards={},
        streets={},
        pois={},
        entities_by_province={},
    )
    report = build_master_report(empty, ADMIN_INVARIANTS)
    coverage = report["master_coverage"]
    assert coverage["province_count_ok"] is False
    assert coverage["legacy_alias_count_ok"] is False


def test_province_without_entities_is_listed(tmp_path: Path) -> None:
    """Fixture chỉ có snapshot OSM cho tỉnh 01 — tỉnh 02 không có entity nào
    và PHẢI xuất hiện trong provinces_without_entities, không được im lặng."""
    report = build_master_report(_master(tmp_path), ADMIN_INVARIANTS)
    without = report["master_coverage"]["provinces_without_entities"]
    assert [p["province_id"] for p in without] == ["02"]
    assert report["master_coverage"]["provinces_with_entities"] == 1


def test_by_category_and_subcategory(tmp_path: Path) -> None:
    report = build_master_report(_master(tmp_path), ADMIN_INVARIANTS)
    assert report["by_category"] == {"public_facility": 1, "street": 1}
    assert report["by_subcategory"] == {"hospital": 1, "street": 1}


def test_empty_subcategories_excludes_present_ones(tmp_path: Path) -> None:
    report = build_master_report(_master(tmp_path), ADMIN_INVARIANTS)
    empty = report["empty_subcategories"]
    assert "hospital" not in empty
    assert "street" not in empty
    assert "airport" in empty  # taxonomy có, fixture không có POI nào loại này


def test_by_province_counts_hierarchy_correctly(tmp_path: Path) -> None:
    report = build_master_report(_master(tmp_path), ADMIN_INVARIANTS)
    row_01 = next(p for p in report["by_province"] if p["province_id"] == "01")
    assert row_01["districts"] == 1
    assert row_01["wards"] == 1
    assert row_01["streets"] == 1
    assert row_01["pois"] == 1
    assert row_01["entities"] == 2


def test_tier_distribution_has_note(tmp_path: Path) -> None:
    report = build_master_report(_master(tmp_path), ADMIN_INVARIANTS)
    assert "note" in report["tier_distribution"]
    assert report["tier_distribution"]["poi_tiers"] == {"2": 1}
