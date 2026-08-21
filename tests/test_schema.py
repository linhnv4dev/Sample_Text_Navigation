"""Test cho navtext.schema — xem docs/output.md#csv-schema và
docs/sampling.md#cell-key--rng-theo-cell.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import FrozenInstanceError

import pytest

from navtext.schema import (
    CSV_COLUMNS,
    SUBCATEGORIES,
    Category,
    Cell,
    NameVariant,
    NumberStyle,
    Register,
    Sample,
    SentenceType,
    Source,
    Subcategory,
)

# Bản sao độc lập của bảng cột trong docs/output.md#csv-schema — nếu lệch,
# một trong hai (test hoặc schema.py) đang sai so với doc.
EXPECTED_CSV_COLUMNS = (
    "id",
    "text",
    "category",
    "subcategory",
    "entity",
    "entity_id",
    "province",
    "province_legacy",
    "district",
    "ward",
    "street",
    "address",
    "sentence_type",
    "template_id",
    "variation",
    "name_variant",
    "number_style",
    "register",
    "source",
    "seed_cell",
)


def test_csv_columns_match_output_doc() -> None:
    assert CSV_COLUMNS == EXPECTED_CSV_COLUMNS


def test_every_category_has_subcategories() -> None:
    for category in Category:
        assert category in SUBCATEGORIES
        assert len(SUBCATEGORIES[category]) > 0


def test_every_subcategory_belongs_to_exactly_one_category() -> None:
    seen: dict[Subcategory, Category] = {}
    for category, subs in SUBCATEGORIES.items():
        for sub in subs:
            assert sub not in seen, (
                f"{sub} listed under both {seen.get(sub)} and {category}"
            )
            seen[sub] = category
    assert set(seen) == set(Subcategory)


def _make_sample(province_legacy: str | None = None) -> Sample:
    return Sample(
        id="s1",
        text="Chỉ đường đến Hồ Hoàn Kiếm.",
        category=Category.LANDMARK,
        subcategory=Subcategory.LAKE,
        entity="Hồ Hoàn Kiếm",
        entity_id="poi_001",
        province="Hà Nội",
        province_legacy=province_legacy,
        district=None,
        ward=None,
        street=None,
        address=None,
        sentence_type=SentenceType.NAV_COMMAND,
        template_id="nav_cmd_003",
        variation="current+cardinal+casual",
        name_variant=NameVariant.CURRENT,
        number_style=NumberStyle.NONE,
        register=Register.CASUAL,
        source=Source.OSM,
        seed_cell="01|landmark|nav_command|casual_short",
    )


def test_sample_to_row_keys_match_csv_columns() -> None:
    row = _make_sample().to_row()
    assert tuple(row.keys()) == CSV_COLUMNS


def test_sample_to_row_none_becomes_empty_string() -> None:
    row = _make_sample(province_legacy=None).to_row()
    assert row["province_legacy"] == ""
    assert row["district"] == ""


def test_sample_to_row_keeps_non_empty_optional_field() -> None:
    row = _make_sample(province_legacy="Hà Tây").to_row()
    assert row["province_legacy"] == "Hà Tây"


def test_sample_is_frozen() -> None:
    sample = _make_sample()
    with pytest.raises(FrozenInstanceError):
        sample.text = "khác"  # type: ignore[misc]


def test_cell_is_frozen() -> None:
    cell = Cell(
        province_id="01",
        category=Category.LANDMARK,
        sentence_type=SentenceType.NAV_COMMAND,
        variation_profile="casual_short",
        target_count=100,
    )
    with pytest.raises(FrozenInstanceError):
        cell.target_count = 200  # type: ignore[misc]


def test_cell_key_is_stable_string() -> None:
    cell = Cell(
        province_id="01",
        category=Category.LANDMARK,
        sentence_type=SentenceType.NAV_COMMAND,
        variation_profile="casual_short",
        target_count=100,
    )
    assert cell.cell_key == "01|landmark|nav_command|casual_short"


def test_derive_seed_same_cell_same_global_seed_is_identical() -> None:
    cell = Cell(
        province_id="01",
        category=Category.LANDMARK,
        sentence_type=SentenceType.NAV_COMMAND,
        variation_profile="casual_short",
        target_count=100,
    )
    assert cell.derive_seed(20260815) == cell.derive_seed(20260815)


def test_derive_seed_different_cells_differ() -> None:
    base = dict(
        category=Category.LANDMARK,
        sentence_type=SentenceType.NAV_COMMAND,
        variation_profile="casual_short",
        target_count=100,
    )
    cell_a = Cell(province_id="01", **base)
    cell_b = Cell(province_id="02", **base)
    assert cell_a.derive_seed(20260815) != cell_b.derive_seed(20260815)


def _seed_in_subprocess(pythonhashseed: str) -> int:
    code = (
        "from navtext.schema import Cell, Category, SentenceType; "
        "c = Cell(province_id='01', category=Category.LANDMARK, "
        "sentence_type=SentenceType.NAV_COMMAND, "
        "variation_profile='casual_short', target_count=100); "
        "print(c.derive_seed(20260815))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={"PYTHONHASHSEED": pythonhashseed, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    return int(result.stdout.strip())


def test_derive_seed_stable_across_pythonhashseed() -> None:
    """Bảo vệ trực tiếp hard rule #4 / reproducibility: seed không được phụ
    thuộc PYTHONHASHSEED của process (hash() built-in thì có, sha256 thì không).
    """
    seed_fixed = _seed_in_subprocess("0")
    seed_random = _seed_in_subprocess("1")
    assert seed_fixed == seed_random
