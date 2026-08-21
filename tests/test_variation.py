"""Test cho src/navtext/variation.py — phase 5 (trọng số trục variation).

Xem docs/generation.md#config-variation-yaml và kế hoạch phase 5 (bảng 9
check + Axis.resolve() + pick_weighted()).
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest
import yaml

from navtext.schema import (
    Category,
    HierarchyDepth,
    NameVariant,
    NumberStyle,
    Register,
)
from navtext.variation import (
    VariationConfig,
    VariationError,
    format_variation_key,
    load_variation_config,
    pick_weighted,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_CONFIG_PATH = REPO_ROOT / "config" / "variation.yaml"


def _load_real_raw() -> dict:
    return yaml.safe_load(REAL_CONFIG_PATH.read_text(encoding="utf-8"))


def _write_config(tmp_path: Path, raw: dict) -> Path:
    path = tmp_path / "variation.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Load config thật
# ---------------------------------------------------------------------------


def test_load_real_config() -> None:
    config = load_variation_config(REAL_CONFIG_PATH)
    assert isinstance(config, VariationConfig)
    assert config.schema_version == 1
    assert set(config.axes) == {
        "name_variant",
        "number_style",
        "number_prefix",
        "admin_prefix",
        "hierarchy_depth",
        "legacy_marker",
    }


def test_all_axes_normalized_to_one() -> None:
    config = load_variation_config(REAL_CONFIG_PATH)
    for axis_name, axis in config.axes.items():
        total = sum(axis.weights.values())
        assert total == pytest.approx(1.0), f"{axis_name}.weights không normalize về 1.0"
        for reg, weights in axis.by_register.items():
            assert sum(weights.values()) == pytest.approx(1.0), f"{axis_name}.by_register.{reg} lệch 1.0"
        for cat, weights in axis.by_category.items():
            assert sum(weights.values()) == pytest.approx(1.0), f"{axis_name}.by_category.{cat} lệch 1.0"


def test_enum_axes_cover_exactly_schema_enums() -> None:
    """Bắt được cả khi ai đó thêm giá trị enum mới vào schema.py mà quên
    cập nhật config/variation.yaml.
    """
    config = load_variation_config(REAL_CONFIG_PATH)
    assert set(config.axes["name_variant"].weights) == {e.value for e in NameVariant}
    assert set(config.axes["number_style"].weights) == {e.value for e in NumberStyle}
    assert set(config.axes["hierarchy_depth"].weights) == {e.value for e in HierarchyDepth}


def test_lexicon_rates_cover_all_registers() -> None:
    config = load_variation_config(REAL_CONFIG_PATH)
    for slot, per_register in config.lexicon_rates.items():
        assert set(per_register) == set(Register), f"lexicon_rates.{slot} thiếu register"
        for rate in per_register.values():
            assert 0.0 <= rate <= 1.0


# ---------------------------------------------------------------------------
# Axis.resolve()
# ---------------------------------------------------------------------------


def test_resolve_by_category_wins_over_by_register() -> None:
    config = load_variation_config(REAL_CONFIG_PATH)
    axis = config.axes["name_variant"]
    resolved = axis.resolve(register=None, category=Category.ADMIN_UNIT)
    assert resolved == axis.by_category[Category.ADMIN_UNIT]


def test_resolve_falls_back_to_default_without_override() -> None:
    config = load_variation_config(REAL_CONFIG_PATH)
    axis = config.axes["number_prefix"]
    resolved = axis.resolve(register=Register.NEUTRAL, category=Category.STREET)
    assert resolved == axis.weights


def test_resolve_by_register_used_when_no_category_override() -> None:
    config = load_variation_config(REAL_CONFIG_PATH)
    axis = config.axes["admin_prefix"]
    resolved = axis.resolve(register=Register.FORMAL, category=Category.STREET)
    assert resolved == axis.by_register[Register.FORMAL]


# ---------------------------------------------------------------------------
# 9 check reject — mỗi test sửa một chỗ trên bản copy của config thật.
# ---------------------------------------------------------------------------


def test_rejects_missing_axis(tmp_path: Path) -> None:
    raw = _load_real_raw()
    del raw["axes"]["hierarchy_depth"]
    path = _write_config(tmp_path, raw)
    with pytest.raises(VariationError) as exc_info:
        load_variation_config(path)
    assert any("hierarchy_depth" in i for i in exc_info.value.issues)


def test_rejects_extra_axis(tmp_path: Path) -> None:
    raw = _load_real_raw()
    raw["axes"]["bogus_axis"] = {"weights": {"a": 1.0}}
    path = _write_config(tmp_path, raw)
    with pytest.raises(VariationError) as exc_info:
        load_variation_config(path)
    assert any("bogus_axis" in i for i in exc_info.value.issues)


def test_rejects_enum_axis_not_covering_schema(tmp_path: Path) -> None:
    raw = _load_real_raw()
    del raw["axes"]["name_variant"]["weights"]["spoken_alias"]
    path = _write_config(tmp_path, raw)
    with pytest.raises(VariationError) as exc_info:
        load_variation_config(path)
    assert any("spoken_alias" in i for i in exc_info.value.issues)


def test_rejects_boolean_axis_bad_keys(tmp_path: Path) -> None:
    raw = _load_real_raw()
    raw["axes"]["number_prefix"]["weights"] = {"yes": 0.5, "no": 0.5}
    path = _write_config(tmp_path, raw)
    with pytest.raises(VariationError) as exc_info:
        load_variation_config(path)
    issues_text = "\n".join(exc_info.value.issues)
    assert "present" in issues_text and "absent" in issues_text


def test_rejects_negative_weight(tmp_path: Path) -> None:
    raw = _load_real_raw()
    raw["axes"]["name_variant"]["weights"]["current"] = -0.1
    path = _write_config(tmp_path, raw)
    with pytest.raises(VariationError) as exc_info:
        load_variation_config(path)
    assert any("âm" in i for i in exc_info.value.issues)


def test_rejects_zero_total_weight(tmp_path: Path) -> None:
    raw = _load_real_raw()
    raw["axes"]["number_prefix"]["weights"] = {"present": 0.0, "absent": 0.0}
    path = _write_config(tmp_path, raw)
    with pytest.raises(VariationError) as exc_info:
        load_variation_config(path)
    assert any("tổng weight phải > 0" in i for i in exc_info.value.issues)


def test_rejects_invalid_register_override_key(tmp_path: Path) -> None:
    raw = _load_real_raw()
    raw["axes"]["admin_prefix"]["by_register"]["bogus_register"] = {"present": 0.5, "absent": 0.5}
    path = _write_config(tmp_path, raw)
    with pytest.raises(VariationError) as exc_info:
        load_variation_config(path)
    assert any("bogus_register" in i for i in exc_info.value.issues)


def test_rejects_invalid_category_override_key(tmp_path: Path) -> None:
    raw = _load_real_raw()
    raw["axes"]["name_variant"]["by_category"]["bogus_category"] = {
        "current": 0.5,
        "legacy": 0.3,
        "spoken_alias": 0.2,
    }
    path = _write_config(tmp_path, raw)
    with pytest.raises(VariationError) as exc_info:
        load_variation_config(path)
    assert any("bogus_category" in i for i in exc_info.value.issues)


def test_rejects_partial_override(tmp_path: Path) -> None:
    """Override một phần (thiếu key so với weights mặc định) là bug âm
    thầm — phải bị reject, không được âm thầm merge với default.
    """
    raw = _load_real_raw()
    raw["axes"]["hierarchy_depth"]["by_category"]["address"] = {"poi_only": 1.0}
    path = _write_config(tmp_path, raw)
    with pytest.raises(VariationError) as exc_info:
        load_variation_config(path)
    issues_text = "\n".join(exc_info.value.issues)
    assert "by_category.address" in issues_text
    assert "poi_district" in issues_text or "full_address" in issues_text


def test_rejects_lexicon_rate_out_of_range(tmp_path: Path) -> None:
    raw = _load_real_raw()
    raw["lexicon_rates"]["opener"]["casual"] = 1.5
    path = _write_config(tmp_path, raw)
    with pytest.raises(VariationError) as exc_info:
        load_variation_config(path)
    assert any("lexicon_rates.opener.casual" in i for i in exc_info.value.issues)


def test_rejects_lexicon_rates_missing_register(tmp_path: Path) -> None:
    raw = _load_real_raw()
    del raw["lexicon_rates"]["filler"]["casual"]
    path = _write_config(tmp_path, raw)
    with pytest.raises(VariationError) as exc_info:
        load_variation_config(path)
    assert any("lexicon_rates.filler" in i for i in exc_info.value.issues)


def test_rejects_min_legacy_share_out_of_range(tmp_path: Path) -> None:
    raw = _load_real_raw()
    raw["axes"]["name_variant"]["min_legacy_share_per_province"] = 1.5
    path = _write_config(tmp_path, raw)
    with pytest.raises(VariationError) as exc_info:
        load_variation_config(path)
    assert any("min_legacy_share_per_province" in i for i in exc_info.value.issues)


def test_rejects_unachievable_min_legacy_share(tmp_path: Path) -> None:
    """Sàn lớn hơn share legacy khả thi của admin_unit -> bất khả thi."""
    raw = _load_real_raw()
    raw["axes"]["name_variant"]["by_category"]["admin_unit"] = {
        "current": 0.98,
        "legacy": 0.01,
        "spoken_alias": 0.01,
    }
    raw["axes"]["name_variant"]["min_legacy_share_per_province"] = 0.5
    path = _write_config(tmp_path, raw)
    with pytest.raises(VariationError) as exc_info:
        load_variation_config(path)
    assert any("sàn không thể đạt được" in i for i in exc_info.value.issues)


def test_rejects_unsupported_schema_version(tmp_path: Path) -> None:
    raw = _load_real_raw()
    raw["schema_version"] = 2
    path = _write_config(tmp_path, raw)
    with pytest.raises(VariationError) as exc_info:
        load_variation_config(path)
    assert any("schema_version" in i for i in exc_info.value.issues)


def test_collects_all_issues_not_fail_fast(tmp_path: Path) -> None:
    """Nhiều lỗi độc lập trong cùng file phải xuất hiện đủ trong
    VariationError.issues, không dừng ở lỗi đầu tiên.
    """
    raw = _load_real_raw()
    raw["schema_version"] = 99
    raw["axes"]["name_variant"]["weights"]["current"] = -1.0
    del raw["axes"]["number_style"]["weights"]["none"]
    raw["lexicon_rates"]["closer"]["formal"] = 2.0
    path = _write_config(tmp_path, raw)
    with pytest.raises(VariationError) as exc_info:
        load_variation_config(path)
    issues_text = "\n".join(exc_info.value.issues)
    assert "schema_version" in issues_text
    assert "âm" in issues_text
    assert "none" in issues_text
    assert "lexicon_rates.closer.formal" in issues_text


# ---------------------------------------------------------------------------
# pick_weighted()
# ---------------------------------------------------------------------------


def test_pick_weighted_deterministic_same_seed() -> None:
    weights = {"a": 0.2, "b": 0.3, "c": 0.5}
    seq_1 = [pick_weighted(weights, random.Random(42)) for _ in range(20)]
    seq_2 = [pick_weighted(weights, random.Random(42)) for _ in range(20)]
    assert seq_1 == seq_2


def test_pick_weighted_never_picks_zero_weight() -> None:
    weights = {"a": 1.0, "b": 0.0}
    rng = random.Random(7)
    results = {pick_weighted(weights, rng) for _ in range(500)}
    assert results == {"a"}


def test_pick_weighted_distribution_matches_weights() -> None:
    weights = {"a": 0.1, "b": 0.3, "c": 0.6}
    rng = random.Random(123)
    n = 20_000
    counts = {"a": 0, "b": 0, "c": 0}
    for _ in range(n):
        counts[pick_weighted(weights, rng)] += 1
    for key, weight in weights.items():
        assert counts[key] / n == pytest.approx(weight, abs=0.02)


def test_pick_weighted_raises_on_empty() -> None:
    with pytest.raises(ValueError):
        pick_weighted({}, random.Random(1))


def test_pick_weighted_raises_on_nonpositive_total() -> None:
    with pytest.raises(ValueError):
        pick_weighted({"a": 0.0, "b": 0.0}, random.Random(1))


# ---------------------------------------------------------------------------
# format_variation_key()
# ---------------------------------------------------------------------------


def test_format_variation_key_matches_docs_example() -> None:
    key = format_variation_key(
        name_variant=NameVariant.LEGACY,
        number_style=NumberStyle.CARDINAL,
        admin_prefix_present=True,
        register=Register.CASUAL,
    )
    assert key == "legacy+cardinal+prefix+casual"


def test_format_variation_key_absent_prefix() -> None:
    key = format_variation_key(
        name_variant=NameVariant.CURRENT,
        number_style=NumberStyle.NONE,
        admin_prefix_present=False,
        register=Register.FORMAL,
    )
    assert key == "current+none+no_prefix+formal"


# ---------------------------------------------------------------------------
# Import side-effect
# ---------------------------------------------------------------------------


def test_no_import_side_effect() -> None:
    """Import module không được đọc file (CLAUDE.md#code-conventions)."""
    sys.modules.pop("navtext.variation", None)
    import navtext.variation  # noqa: F401 - chỉ kiểm tra import không raise/đọc file
