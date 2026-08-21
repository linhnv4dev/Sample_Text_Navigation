"""Golden test cho generate_all() — docs/testing.md#golden-test.

Chạy generate_all(plan, ctx) với global_seed cố định trên một plan nhỏ (10
cell, 83 sample, phủ street/address/admin_unit/commercial/public_facility/
landmark/transport/poi_other qua 4 tỉnh), hash toàn bộ output, so với hash
đã lưu ở tests/golden/generate_small.sha256. Đổi bất kỳ điều gì trong
numbers.py/templates.py/variation.py/generator.py mà không cố ý thay output
sẽ làm test này báo đỏ ngay.

Cập nhật golden hash là hành động CÓ CHỦ ĐÍCH — nếu test fail, đọc
tests/golden/generate_small.csv (sinh cùng lúc với hash) để hiểu output đã
đổi như thế nào trước khi chấp nhận hash mới, không tự ý ghi đè.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from navtext.generator import build_context, generate_all
from navtext.sampling import plan_from_doc
from navtext.schema import CSV_COLUMNS
from navtext.variation import load_variation_config

REPO_ROOT = Path(__file__).resolve().parent.parent
MASTER_DIR = REPO_ROOT / "data" / "master"
TEMPLATES_DIR = REPO_ROOT / "data" / "templates"
DISTRIBUTION_CONFIG_PATH = REPO_ROOT / "config" / "distribution.yaml"
VARIATION_CONFIG_PATH = REPO_ROOT / "config" / "variation.yaml"

GOLDEN_DIR = Path(__file__).parent / "golden"
PLAN_PATH = GOLDEN_DIR / "plan_small.json"
HASH_PATH = GOLDEN_DIR / "generate_small.sha256"


def _hash_samples(samples) -> str:
    lines = [",".join(s.to_row()[c] for c in CSV_COLUMNS) for s in samples]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def test_golden_generate_matches_stored_hash() -> None:
    if not MASTER_DIR.exists():
        pytest.skip("data/master/ chưa build")

    plan_doc = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    plan = plan_from_doc(plan_doc)

    distribution_config = yaml.safe_load(DISTRIBUTION_CONFIG_PATH.read_text(encoding="utf-8"))
    variation_config = load_variation_config(VARIATION_CONFIG_PATH)
    ctx = build_context(
        master_dir=MASTER_DIR,
        template_dir=TEMPLATES_DIR,
        distribution_config=distribution_config,
        variation_config=variation_config,
        global_seed=plan_doc["global_seed"],
    )

    samples = list(generate_all(plan, ctx))
    assert len(samples) == plan_doc["total"]

    actual_hash = _hash_samples(samples)
    expected_hash = HASH_PATH.read_text(encoding="utf-8").strip()
    assert actual_hash == expected_hash, (
        "Golden hash lệch — output của generate_all() đã đổi. Nếu đây là thay đổi "
        "CÓ CHỦ ĐÍCH (sửa numbers.py/templates.py/variation.py/generator.py), xem "
        "tests/golden/generate_small.csv để xác nhận output mới đúng, rồi cập nhật "
        "tests/golden/generate_small.sha256 (và generate_small.csv) một cách tường minh."
    )
