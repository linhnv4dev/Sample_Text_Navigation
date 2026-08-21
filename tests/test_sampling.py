"""Test cho navtext.sampling — phase 3. Chia 3 nhóm:

- pure math helpers (normalize_weights, clamped_shares, largest_remainder):
  không cần fixture, test trực tiếp trên dict số.
- entity_pool: cần build_master trên fixture 2 tỉnh (xem test_loaders.py).
- build_plan / validate_plan / scale_plan: integration trên cùng fixture,
  dùng tests/fixtures/config/distribution.yaml (KHÔNG phải config thật —
  trần/sàn chỉ đủ khả thi cho 2 tỉnh / 4 category của fixture).
"""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from navtext.build_master import build_master
from navtext.schema import Category, SentenceType

FIXTURE_RAW = Path(__file__).parent / "fixtures" / "raw"
FIXTURE_OSM_TAGS = Path(__file__).parent / "fixtures" / "config" / "osm_tags.yaml"
FIXTURE_RUN_CONFIG = Path(__file__).parent / "fixtures" / "config" / "run.yaml"
FIXTURE_DISTRIBUTION = Path(__file__).parent / "fixtures" / "config" / "distribution.yaml"


def _build(tmp_path: Path) -> Path:
    master_dir = tmp_path / "master"
    build_master(
        raw_dir=FIXTURE_RAW,
        master_dir=master_dir,
        osm_tag_config_path=FIXTURE_OSM_TAGS,
        run_config_path=FIXTURE_RUN_CONFIG,
    )
    return master_dir


def _load_master(tmp_path: Path):
    from navtext.loaders import load_master

    return load_master(_build(tmp_path))


def _fixture_config() -> dict:
    with FIXTURE_DISTRIBUTION.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# normalize_weights
# ---------------------------------------------------------------------------


def test_normalize_weights_sums_to_one():
    from navtext.sampling import normalize_weights

    result = normalize_weights({"a": 2.0, "b": 2.0, "c": 4.0})
    assert result == {"a": 0.25, "b": 0.25, "c": 0.5}


def test_normalize_weights_rejects_zero_total():
    from navtext.sampling import PlanError, normalize_weights

    with pytest.raises(PlanError):
        normalize_weights({"a": 0.0, "b": 0.0})


# ---------------------------------------------------------------------------
# clamped_shares
# ---------------------------------------------------------------------------


def test_clamped_shares_sums_to_one_when_uncapped():
    from navtext.sampling import clamped_shares

    result = clamped_shares({"a": 1.0, "b": 1.0}, min_share=0.0, max_share=1.0)
    assert math.isclose(sum(result.values()), 1.0)
    assert math.isclose(result["a"], 0.5)


def test_clamped_shares_respects_max_cap():
    # "a" gánh 90% trọng số thô nhưng trần 60% -> phần dư chảy sang b/c.
    from navtext.sampling import clamped_shares

    result = clamped_shares({"a": 90.0, "b": 5.0, "c": 5.0}, min_share=0.0, max_share=0.6)
    assert math.isclose(result["a"], 0.6, abs_tol=1e-9)
    assert math.isclose(sum(result.values()), 1.0)


def test_clamped_shares_respects_min_floor():
    # "c" gần như không có trọng số thô nhưng sàn 10% vẫn phải đạt.
    from navtext.sampling import clamped_shares

    result = clamped_shares({"a": 100.0, "b": 100.0, "c": 0.001}, min_share=0.1, max_share=1.0)
    assert math.isclose(result["c"], 0.1, abs_tol=1e-9)
    assert math.isclose(sum(result.values()), 1.0)


def test_clamped_shares_raises_when_infeasible():
    # 3 phần tử, sàn 40% mỗi phần tử -> tổng sàn tối thiểu 120% > 100%.
    from navtext.sampling import PlanError, clamped_shares

    with pytest.raises(PlanError):
        clamped_shares({"a": 1.0, "b": 1.0, "c": 1.0}, min_share=0.4, max_share=1.0)


# ---------------------------------------------------------------------------
# largest_remainder
# ---------------------------------------------------------------------------


def test_largest_remainder_sums_exactly_to_total():
    from navtext.sampling import largest_remainder

    shares = {"a": 1 / 3, "b": 1 / 3, "c": 1 / 3}
    result = largest_remainder(shares, 100)
    assert sum(result.values()) == 100


def test_largest_remainder_is_deterministic_on_ties():
    from navtext.sampling import largest_remainder

    shares = {"a": 0.5, "b": 0.5}
    result1 = largest_remainder(shares, 3)
    result2 = largest_remainder(shares, 3)
    assert result1 == result2
    assert sum(result1.values()) == 3


def test_largest_remainder_matches_shares_closely():
    from navtext.sampling import largest_remainder

    shares = {"a": 0.7, "b": 0.3}
    result = largest_remainder(shares, 1000)
    assert result["a"] == 700
    assert result["b"] == 300


# ---------------------------------------------------------------------------
# entity_pool
# ---------------------------------------------------------------------------


def test_entity_pool_admin_unit_always_has_province_itself(tmp_path):
    from navtext.sampling import entity_pool

    master = _load_master(tmp_path)
    # Tỉnh "02" không có street/POI nào trong fixture nhưng luôn có tên tỉnh.
    pool = entity_pool(master, "02", Category.ADMIN_UNIT)
    assert len(pool) >= 1
    assert any(e.name == master.provinces["02"].name_current for e in pool)


def test_entity_pool_admin_unit_includes_district_and_ward(tmp_path):
    from navtext.sampling import entity_pool

    master = _load_master(tmp_path)
    pool = entity_pool(master, "01", Category.ADMIN_UNIT)
    names = {e.name for e in pool}
    assert "Quận Một" in names
    assert "Phường A" in names


def test_entity_pool_admin_unit_includes_ward_attached_directly_to_province(tmp_path):
    """Ward theo mô hình 2 cấp (province_id trực tiếp, district_id rỗng —
    nguồn wards_osm.csv) phải vào đúng pool admin_unit của tỉnh đó. Đây là
    thứ gỡ nghẽn `navtext plan --target 100000`: trước khi có nguồn này, 31
    tỉnh chỉ có ĐÚNG 1 entity admin_unit nên vượt caps.entity.max_share."""
    import shutil

    from navtext.loaders import load_master
    from navtext.sampling import entity_pool

    raw_dir = tmp_path / "raw_two_level"
    shutil.copytree(FIXTURE_RAW, raw_dir)
    (raw_dir / "admin" / "wards_osm.csv").write_text(
        "ward_id,district_id,province_id,name,type\n"
        "osm_ward_1,,02,Xã Trực Thuộc Tỉnh,xã\n",
        encoding="utf-8",
    )
    master_dir = tmp_path / "master_two_level"
    build_master(
        raw_dir=raw_dir,
        master_dir=master_dir,
        osm_tag_config_path=FIXTURE_OSM_TAGS,
        run_config_path=FIXTURE_RUN_CONFIG,
    )
    master = load_master(master_dir)

    pool_02 = entity_pool(master, "02", Category.ADMIN_UNIT)
    assert "Xã Trực Thuộc Tỉnh" in {e.name for e in pool_02}
    # và KHÔNG rò sang tỉnh khác
    pool_01 = entity_pool(master, "01", Category.ADMIN_UNIT)
    assert "Xã Trực Thuộc Tỉnh" not in {e.name for e in pool_01}


def test_entity_pool_admin_unit_ward_carries_own_legacy_alias_hint(tmp_path):
    """Ward nối THẲNG qua province_id (nguồn wards_osm.csv) mang
    legacy_alias_hint CỦA CHÍNH NÓ (không kế thừa từ district, vì không có
    district cha) — khác nhánh district-path đã test ở
    test_entity_pool_admin_unit_includes_district_and_ward (kế thừa hint từ
    district). Xem docs/data-model.md#sub-region-hint,
    scripts/extract_wards.py."""
    import shutil

    from navtext.loaders import load_master
    from navtext.sampling import entity_pool

    raw_dir = tmp_path / "raw_ward_own_hint"
    shutil.copytree(FIXTURE_RAW, raw_dir)
    (raw_dir / "admin" / "wards_osm.csv").write_text(
        "ward_id,district_id,province_id,name,type,legacy_alias\n"
        "osm_ward_hint,,02,Xã Có Hint Riêng,xã,Tỉnh Hai Cũ A\n",
        encoding="utf-8",
    )
    master_dir = tmp_path / "master_ward_own_hint"
    build_master(
        raw_dir=raw_dir,
        master_dir=master_dir,
        osm_tag_config_path=FIXTURE_OSM_TAGS,
        run_config_path=FIXTURE_RUN_CONFIG,
    )
    master = load_master(master_dir)

    pool_02 = entity_pool(master, "02", Category.ADMIN_UNIT)
    ward = next(e for e in pool_02 if e.name == "Xã Có Hint Riêng")
    assert ward.legacy_alias_hint == "Tỉnh Hai Cũ A"


def test_entity_pool_street_only_for_province_with_osm_data(tmp_path):
    from navtext.sampling import entity_pool

    master = _load_master(tmp_path)
    assert len(entity_pool(master, "01", Category.STREET)) == 1
    assert len(entity_pool(master, "02", Category.STREET)) == 0


def test_entity_pool_address_reuses_street_support(tmp_path):
    from navtext.sampling import entity_pool

    master = _load_master(tmp_path)
    streets = entity_pool(master, "01", Category.STREET)
    addresses = entity_pool(master, "01", Category.ADDRESS)
    assert addresses == streets


def test_entity_pool_poi_category_filters_by_category(tmp_path):
    from navtext.sampling import entity_pool

    master = _load_master(tmp_path)
    pool = entity_pool(master, "01", Category.PUBLIC_FACILITY)
    assert len(pool) == 1
    assert pool[0].category == Category.PUBLIC_FACILITY
    assert len(entity_pool(master, "01", Category.LANDMARK)) == 0


def test_entity_pool_landmark_available_via_manual_source_without_osm(tmp_path):
    """Tỉnh "02" không có street/POI support trong fixture OSM (xem test
    test_entity_pool_street_only_for_province_with_osm_data ở trên) — nhưng
    landmarks.yaml (source=manual) vẫn cho entity_pool(LANDMARK) khác rỗng,
    không phụ thuộc OSM snapshot có tồn tại hay không."""
    import shutil

    from navtext.build_master import build_master
    from navtext.loaders import load_master
    from navtext.sampling import entity_pool

    raw_with_landmark = tmp_path / "raw_with_landmark"
    shutil.copytree(FIXTURE_RAW, raw_with_landmark)
    (raw_with_landmark / "admin" / "landmarks.yaml").write_text(
        "- province_id: \"02\"\n"
        "  landmarks:\n"
        "    - {name: \"Núi Thử Nghiệm\", subcategory: mountain, fame_tier: 1}\n",
        encoding="utf-8",
    )
    master_dir = tmp_path / "master_with_landmark"
    build_master(
        raw_dir=raw_with_landmark,
        master_dir=master_dir,
        osm_tag_config_path=FIXTURE_OSM_TAGS,
        run_config_path=FIXTURE_RUN_CONFIG,
    )
    master = load_master(master_dir)
    assert len(entity_pool(master, "02", Category.STREET)) == 0
    pool = entity_pool(master, "02", Category.LANDMARK)
    assert len(pool) == 1
    assert pool[0].name == "Núi Thử Nghiệm"


# ---------------------------------------------------------------------------
# build_plan
# ---------------------------------------------------------------------------


def test_build_plan_total_matches_target_size(tmp_path):
    from navtext.sampling import build_plan

    master = _load_master(tmp_path)
    config = _fixture_config()
    plan = build_plan(config, master)
    assert sum(c.target_count for c in plan) == config["target_size"]


def test_build_plan_only_generates_feasible_cells(tmp_path):
    from navtext.sampling import build_plan

    master = _load_master(tmp_path)
    config = _fixture_config()
    plan = build_plan(config, master)
    # Tỉnh "02" không có street/public_facility -> không cell nào của "02"
    # thuộc hai category đó.
    bad = [
        c
        for c in plan
        if c.province_id == "02" and c.category in (Category.STREET, Category.ADDRESS, Category.PUBLIC_FACILITY)
    ]
    assert bad == []


def test_build_plan_covers_all_provinces_via_admin_unit(tmp_path):
    from navtext.sampling import build_plan

    master = _load_master(tmp_path)
    config = _fixture_config()
    plan = build_plan(config, master)
    provinces_in_plan = {c.province_id for c in plan}
    assert provinces_in_plan == set(master.provinces)


def test_build_plan_is_deterministic(tmp_path):
    from navtext.sampling import build_plan

    master = _load_master(tmp_path)
    config = _fixture_config()
    plan1 = build_plan(config, master)
    plan2 = build_plan(config, master)
    assert [c.cell_key for c in plan1] == [c.cell_key for c in plan2]
    assert [c.target_count for c in plan1] == [c.target_count for c in plan2]


def test_build_plan_deterministic_across_pythonhashseed(tmp_path):
    master_dir = _build(tmp_path)
    script = f"""
import sys
from pathlib import Path
sys.path.insert(0, {str(Path(__file__).parent.parent / "src")!r})
from navtext.loaders import load_master
from navtext.sampling import build_plan
import yaml
master = load_master(Path({str(master_dir)!r}))
with open({str(FIXTURE_DISTRIBUTION)!r}, encoding="utf-8") as f:
    config = yaml.safe_load(f)
plan = build_plan(config, master)
print([(c.cell_key, c.target_count) for c in plan])
"""
    out1 = subprocess.run(
        [sys.executable, "-c", script],
        env={"PYTHONHASHSEED": "1", "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    out2 = subprocess.run(
        [sys.executable, "-c", script],
        env={"PYTHONHASHSEED": "42", "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert out1.stdout == out2.stdout


def test_build_plan_raises_when_category_weight_cannot_absorb_forced_mass(tmp_path):
    from navtext.sampling import PlanError, build_plan

    master = _load_master(tmp_path)
    config = _fixture_config()
    # Ép admin_unit gần 0 trong khi tỉnh "02" CHỈ khả thi ở admin_unit ->
    # marginal category admin_unit buộc phải vượt trần category.max_share.
    config["category_weights"] = {
        "admin_unit": 0.001,
        "street": 0.4,
        "address": 0.3,
        "public_facility": 0.299,
    }
    config["caps"]["category"] = {"max_share": 0.2, "min_share": 0.03}
    with pytest.raises(PlanError):
        build_plan(config, master)


def test_build_plan_without_overrides_key_is_backward_compatible(tmp_path):
    # tests/fixtures/config/distribution.yaml không có province_weights.overrides
    # — mọi test build_plan phía trên đã ngầm chứng minh điều này, nhưng test
    # này nói rõ ý định: config cũ (trước khi thêm overrides) vẫn build được.
    from navtext.sampling import build_plan

    master = _load_master(tmp_path)
    config = _fixture_config()
    assert "overrides" not in config["province_weights"]
    plan = build_plan(config, master)
    assert sum(c.target_count for c in plan) == config["target_size"]


def test_province_weights_override_shifts_share_toward_province(tmp_path):
    from navtext.sampling import build_plan

    master = _load_master(tmp_path)

    baseline_config = _fixture_config()
    baseline_plan = build_plan(baseline_config, master)
    baseline_share_02 = sum(c.target_count for c in baseline_plan if c.province_id == "02") / sum(
        c.target_count for c in baseline_plan
    )

    overridden_config = _fixture_config()
    overridden_config["province_weights"]["overrides"] = {"02": 2.0}
    overridden_plan = build_plan(overridden_config, master)
    overridden_share_02 = sum(c.target_count for c in overridden_plan if c.province_id == "02") / sum(
        c.target_count for c in overridden_plan
    )

    # tỉnh "02" (tier 2, weight thô 1.0) nhân override 2.0 -> weight thô 2.0,
    # gần bằng tỉnh "01" (tier 1, weight thô 3.0) -> share phải tăng rõ rệt.
    assert overridden_share_02 > baseline_share_02


def test_province_weights_override_unknown_province_id_raises(tmp_path):
    from navtext.sampling import PlanError, build_plan

    master = _load_master(tmp_path)
    config = _fixture_config()
    config["province_weights"]["overrides"] = {"99": 1.5}
    with pytest.raises(PlanError, match="99"):
        build_plan(config, master)


# ---------------------------------------------------------------------------
# validate_plan
# ---------------------------------------------------------------------------


def test_validate_plan_accepts_plan_from_build_plan(tmp_path):
    from navtext.sampling import build_plan, validate_plan

    master = _load_master(tmp_path)
    config = _fixture_config()
    plan = build_plan(config, master)
    validate_plan(plan, config, master, total=config["target_size"])


def test_validate_plan_rejects_wrong_total(tmp_path):
    from navtext.sampling import PlanError, build_plan, validate_plan

    master = _load_master(tmp_path)
    config = _fixture_config()
    plan = build_plan(config, master)
    with pytest.raises(PlanError):
        validate_plan(plan, config, master, total=config["target_size"] + 1)


def test_validate_plan_rejects_missing_province_coverage(tmp_path):
    from navtext.sampling import PlanError, build_plan, validate_plan

    master = _load_master(tmp_path)
    config = _fixture_config()
    plan = build_plan(config, master)
    plan_missing_02 = [c for c in plan if c.province_id != "02"]
    # Đắp lại target_count cho tổng vẫn đúng, chỉ để lộ vi phạm coverage.
    with pytest.raises(PlanError):
        validate_plan(plan_missing_02, config, master, total=sum(c.target_count for c in plan_missing_02))


# ---------------------------------------------------------------------------
# scale_plan
# ---------------------------------------------------------------------------


def test_scale_plan_preserves_total(tmp_path):
    from navtext.sampling import build_plan, scale_plan

    master = _load_master(tmp_path)
    config = _fixture_config()
    plan = build_plan(config, master)
    scaled = scale_plan(plan, 200)
    assert sum(c.target_count for c in scaled) == 200


def test_scale_plan_preserves_relative_shares(tmp_path):
    from navtext.sampling import build_plan, scale_plan

    master = _load_master(tmp_path)
    config = _fixture_config()
    plan = build_plan(config, master)
    scaled = scale_plan(plan, 10_000)
    original_total = sum(c.target_count for c in plan)
    by_key = {c.cell_key: c.target_count for c in plan}
    for c in scaled:
        expected_share = by_key[c.cell_key] / original_total
        actual_share = c.target_count / 10_000
        assert math.isclose(expected_share, actual_share, abs_tol=0.01)
