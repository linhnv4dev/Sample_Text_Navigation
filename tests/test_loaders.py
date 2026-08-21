"""Test cho navtext.loaders — round-trip build_master -> load_master trên
fixture tí hon, và bảo vệ "không side-effect ở import time"
(CLAUDE.md#code-conventions).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from navtext.build_master import build_master
from navtext.schema import Category, Subcategory

FIXTURE_RAW = Path(__file__).parent / "fixtures" / "raw"
FIXTURE_OSM_TAGS = Path(__file__).parent / "fixtures" / "config" / "osm_tags.yaml"
FIXTURE_RUN_CONFIG = Path(__file__).parent / "fixtures" / "config" / "run.yaml"


def _build(tmp_path: Path) -> Path:
    master_dir = tmp_path / "master"
    build_master(
        raw_dir=FIXTURE_RAW,
        master_dir=master_dir,
        osm_tag_config_path=FIXTURE_OSM_TAGS,
        run_config_path=FIXTURE_RUN_CONFIG,
    )
    return master_dir


def test_load_master_round_trips_provinces(tmp_path: Path) -> None:
    from navtext.loaders import load_master

    master = load_master(_build(tmp_path))
    assert set(master.provinces) == {"01", "02"}
    assert master.provinces["01"].name_current == "Tỉnh Một"
    assert master.provinces["02"].admin_status == "merged"


def test_province_id_by_alias_resolves_legacy_and_spoken(tmp_path: Path) -> None:
    from navtext.loaders import load_master

    master = load_master(_build(tmp_path))
    assert master.province_id_by_alias["tỉnh một cũ"] == "01"
    assert master.province_id_by_alias["một"] == "01"
    assert master.province_id_by_alias["tỉnh hai cũ a"] == "02"
    assert master.province_id_by_alias["tỉnh hai cũ b"] == "02"
    # Tên hiện hành cũng tra được, kể cả khi không có trong alias table.
    assert master.province_id_by_alias["tỉnh một"] == "01"


def test_entities_by_province_contains_deduped_street_and_poi(tmp_path: Path) -> None:
    from navtext.loaders import load_master

    master = load_master(_build(tmp_path))
    entities = master.entities_by_province["01"]
    # 1 street (2 way segment đã dedupe ở build_master) + 1 poi.
    assert len(entities) == 2
    categories = {e.category for e in entities}
    assert categories == {Category.STREET, Category.PUBLIC_FACILITY}
    poi_entity = next(e for e in entities if e.category == Category.PUBLIC_FACILITY)
    assert poi_entity.subcategory == Subcategory.HOSPITAL
    assert poi_entity.name == "Bệnh Viện Test"


def test_entities_by_province_empty_for_province_without_osm_data(tmp_path: Path) -> None:
    from navtext.loaders import load_master

    master = load_master(_build(tmp_path))
    # Fixture chỉ có snapshot OSM cho tỉnh 01, tỉnh 02 không có street/poi.
    assert master.entities_by_province.get("02", ()) == ()


def test_import_loaders_has_no_side_effect() -> None:
    """Import module không được đọc file nào — chạy trong subprocess với cwd
    không chứa data/master/ để đảm bảo import không âm thầm cố đọc gì."""
    result = subprocess.run(
        [sys.executable, "-c", "import navtext.loaders"],
        cwd="/tmp",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
