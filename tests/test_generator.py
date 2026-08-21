"""Test cho src/navtext/generator.py — phase 6. Dùng master/template/config
THẬT (không phải fixture tí hon) vì generate_cell() không cần build_plan()
đi qua feasibility check — chỉ cần entity_pool() khác rỗng cho
(province, category) được test, và pool/template bank thật đã đủ lớn để đo
property một cách có ý nghĩa (xem tests/test_numbers.py, cùng cách tiếp cận).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from navtext.generator import build_context, generate_all, generate_cell
from navtext.schema import Category, Cell, SentenceType, Subcategory
from navtext.variation import load_variation_config

REPO_ROOT = Path(__file__).resolve().parent.parent
MASTER_DIR = REPO_ROOT / "data" / "master"
TEMPLATES_DIR = REPO_ROOT / "data" / "templates"
DISTRIBUTION_CONFIG_PATH = REPO_ROOT / "config" / "distribution.yaml"
VARIATION_CONFIG_PATH = REPO_ROOT / "config" / "variation.yaml"

_DIGIT = re.compile(r"[0-9]")
_BRACE = re.compile(r"[{}]")

GLOBAL_SEED = 20260815


def _distribution_config() -> dict:
    return yaml.safe_load(DISTRIBUTION_CONFIG_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ctx():
    if not MASTER_DIR.exists():
        pytest.skip("data/master/ chưa build")
    variation_config = load_variation_config(VARIATION_CONFIG_PATH)
    return build_context(
        master_dir=MASTER_DIR,
        template_dir=TEMPLATES_DIR,
        distribution_config=_distribution_config(),
        variation_config=variation_config,
        global_seed=GLOBAL_SEED,
    )


def _cell(**overrides) -> Cell:
    base = dict(
        province_id="01",
        category=Category.STREET,
        sentence_type=SentenceType.NAV_COMMAND,
        variation_profile="casual_short",
        target_count=30,
    )
    base.update(overrides)
    return Cell(**base)


# ---------------------------------------------------------------------------
# target_count đúng, đủ 20 cột, không thiếu field bắt buộc
# ---------------------------------------------------------------------------


def test_generate_cell_exact_target_count(ctx) -> None:
    cell = _cell(target_count=37)
    samples = generate_cell(cell, ctx)
    assert len(samples) == 37


@pytest.mark.parametrize(
    "category",
    [Category.ADMIN_UNIT, Category.STREET, Category.ADDRESS, Category.PUBLIC_FACILITY, Category.COMMERCIAL],
)
def test_generate_cell_various_categories(ctx, category: Category) -> None:
    cell = _cell(province_id="01", category=category, target_count=15)
    samples = generate_cell(cell, ctx)
    assert len(samples) == 15
    for s in samples:
        assert s.category == category
        assert s.seed_cell == cell.cell_key


# ---------------------------------------------------------------------------
# Property (bản Python thuần của qc/validators.py tầng 1 — docs/testing.md)
# ---------------------------------------------------------------------------


def test_generate_cell_text_properties(ctx) -> None:
    cell = _cell(target_count=200)
    samples = generate_cell(cell, ctx)
    assert len(samples) == 200
    for s in samples:
        assert not _DIGIT.search(s.text), s.text
        assert not _BRACE.search(s.text), s.text
        assert "  " not in s.text, s.text
        assert s.text == s.text.strip(), repr(s.text)


def test_generate_cell_text_properties_across_categories(ctx) -> None:
    for category in Category:
        cell = _cell(province_id="01", category=category, target_count=20)
        samples = generate_cell(cell, ctx)
        for s in samples:
            assert not _DIGIT.search(s.text), (category, s.text)
            assert not _BRACE.search(s.text), (category, s.text)
            assert "  " not in s.text, (category, s.text)


# ---------------------------------------------------------------------------
# Determinism — cùng seed, cùng plan -> output y hệt
# ---------------------------------------------------------------------------


def test_generate_cell_deterministic(ctx) -> None:
    cell = _cell(target_count=50)
    first = generate_cell(cell, ctx)
    second = generate_cell(cell, ctx)
    assert first == second


def test_generate_cell_deterministic_fresh_context() -> None:
    variation_config = load_variation_config(VARIATION_CONFIG_PATH)
    ctx_a = build_context(MASTER_DIR, TEMPLATES_DIR, _distribution_config(), variation_config, GLOBAL_SEED)
    ctx_b = build_context(MASTER_DIR, TEMPLATES_DIR, _distribution_config(), variation_config, GLOBAL_SEED)
    cell = _cell(target_count=20)
    assert generate_cell(cell, ctx_a) == generate_cell(cell, ctx_b)


def test_generate_cell_different_seed_differs(ctx) -> None:
    variation_config = load_variation_config(VARIATION_CONFIG_PATH)
    ctx_other_seed = build_context(MASTER_DIR, TEMPLATES_DIR, _distribution_config(), variation_config, GLOBAL_SEED + 1)
    cell = _cell(target_count=20)
    a = generate_cell(cell, ctx)
    b = generate_cell(cell, ctx_other_seed)
    assert [s.text for s in a] != [s.text for s in b]


# ---------------------------------------------------------------------------
# Regenerate 1 cell không đổi cell khác (docs/sampling.md#cell-key--rng)
# ---------------------------------------------------------------------------


def test_regenerate_one_cell_does_not_affect_other(ctx) -> None:
    cell_a = _cell(province_id="01", category=Category.STREET, target_count=20)
    cell_b = _cell(province_id="02", category=Category.STREET, target_count=20)

    plan = [cell_a, cell_b]
    all_first_pass = {c.cell_key: generate_cell(c, ctx) for c in plan}

    # "Regenerate" cell_a độc lập (mô phỏng qc/balance.find_gaps gọi lại
    # đúng một cell) không được làm lệch output đã sinh trước đó của cell_b.
    regenerated_a = generate_cell(cell_a, ctx)
    assert regenerated_a == all_first_pass[cell_a.cell_key]

    regenerated_b = generate_cell(cell_b, ctx)
    assert regenerated_b == all_first_pass[cell_b.cell_key]


def test_generate_all_matches_individual_generate_cell(ctx) -> None:
    cell_a = _cell(province_id="01", category=Category.STREET, target_count=10)
    cell_b = _cell(province_id="02", category=Category.ADMIN_UNIT, target_count=10)
    plan = [cell_a, cell_b]

    all_samples = list(generate_all(plan, ctx))
    assert len(all_samples) == 20

    expected_a = generate_cell(cell_a, ctx)
    expected_b = generate_cell(cell_b, ctx)
    assert all_samples == list(expected_a) + list(expected_b)


# ---------------------------------------------------------------------------
# Ràng buộc cặp (template_id, entity_id) duy nhất trong cell (docs/qc.md)
# ---------------------------------------------------------------------------


def test_pair_template_entity_unique_within_cell(ctx) -> None:
    cell = _cell(target_count=100)
    samples = generate_cell(cell, ctx)
    pairs = [(s.template_id, s.entity_id) for s in samples]
    assert len(pairs) == len(set(pairs))


# ---------------------------------------------------------------------------
# id ổn định, không phụ thuộc cell khác
# ---------------------------------------------------------------------------


def test_sample_id_stable_and_unique_within_cell(ctx) -> None:
    cell = _cell(target_count=50)
    samples = generate_cell(cell, ctx)
    ids = [s.id for s in samples]
    assert len(ids) == len(set(ids))
    # Chạy lại -> id y hệt (ổn định qua build, docs/output.md#csv-schema).
    samples2 = generate_cell(cell, ctx)
    assert ids == [s.id for s in samples2]


# ---------------------------------------------------------------------------
# Coverage: có mặt câu dùng tên tỉnh cũ (điều kiện cần cho 63/63)
# ---------------------------------------------------------------------------


def test_admin_unit_cell_can_produce_legacy_province_mentions(ctx) -> None:
    """Tỉnh 04 (Đà Nẵng, gộp Quảng Nam) — không dùng 01 (Hà Nội, không sáp
    nhập): legacy alias duy nhất của Hà Nội trùng đúng tên hiện hành nên
    LEGACY luôn fallback CURRENT (xem test_aliases.py và hard rule chống
    cột audit nói dối), không bao giờ sinh được province_legacy != None."""
    cell = _cell(province_id="04", category=Category.ADMIN_UNIT, target_count=300)
    samples = generate_cell(cell, ctx)
    assert any(s.province_legacy for s in samples)


def test_admin_unit_cell_quota_produces_province_subcategory_samples(ctx) -> None:
    """Fix gốc "0,2% câu entity=tỉnh": trước khi có quota subcategory
    (admin_unit_subcategory_weights), pool admin_unit của tỉnh có hàng trăm
    ward cạnh tranh với ĐÚNG 1 entity province khiến weighted-shuffle gần
    như không bao giờ rút được nó. target_count=100 với quota
    province=0.30 (config/distribution.yaml thật, qua fixture `ctx`) phải
    sinh được ~30 sample subcategory=province."""
    cell = _cell(province_id="01", category=Category.ADMIN_UNIT, target_count=100)
    samples = generate_cell(cell, ctx)
    province_samples = [s for s in samples if s.subcategory == Subcategory.PROVINCE]
    # Không đòi đúng 30 tuyệt đối (làm tròn largest_remainder + entity_id
    # trùng vẫn tính 1 sample) — chỉ cần đủ lớn để chứng minh quota có hiệu
    # lực thật, không phải may rủi như trước fix.
    assert len(province_samples) >= 20, (
        f"chỉ {len(province_samples)}/100 sample là province — quota "
        "admin_unit_subcategory_weights có vẻ không được áp dụng"
    )
    valid_names = ("Hà Nội", "thủ đô", "Hà Tây")
    assert all(any(name in s.entity for name in valid_names) for s in province_samples), (
        [s.entity for s in province_samples if not any(name in s.entity for name in valid_names)]
    )


# ---------------------------------------------------------------------------
# Naturalness regression — 6 câu lỗi user báo, mỗi câu quy về 1 trong 3 root
# cause sửa ở generator/lexicon (root cause thứ 4 — tên POI lặp cụm — đã
# khoá ở test_build_master.py::test_clean_poi_name_*, không lặp lại ở đây).
# ---------------------------------------------------------------------------

_CASUAL_SENTENCE_TYPES = [
    SentenceType.CONVERSATIONAL,
    SentenceType.ROUTE_QUESTION,
    SentenceType.SHORT_COMMAND,
    SentenceType.NAV_COMMAND,
    SentenceType.SEARCH,
    SentenceType.CONTEXTUAL,
]


def _generate_many_casual(ctx, province_id: str, category: Category, target_count: int = 120) -> list:
    samples = []
    for sentence_type in _CASUAL_SENTENCE_TYPES:
        cell = _cell(
            province_id=province_id,
            category=category,
            sentence_type=sentence_type,
            variation_profile="casual_short",
            target_count=target_count,
        )
        samples.extend(generate_cell(cell, ctx))
    return samples


def test_generate_cell_no_sentence_final_filler(ctx) -> None:
    """Fix: filler (kiểu/tự dưng/à/thế/ấy) từng bị nối vào cuối câu khi
    template không có {opener} — "…Sea Food kiểu", "…không tự dưng".

    "thế" bị loại khỏi danh sách kiểm tra ở đây: nó VỪA là filler VỪA là
    một giá trị hợp lệ trong closers_after_question (xem lexicon.yaml) —
    câu kết bằng "…thế" có thể đến từ closer đúng luật, không phải bug.
    Cơ chế "filler chỉ nối vào opener" đã được khoá chặt ở mức unit test
    (test_variation_apply.py::test_apply_variations_filler_*).
    """
    fillers = [f for f in ctx.lexicon.fillers if f != "thế"]
    samples = _generate_many_casual(ctx, "01", Category.STREET)
    for s in samples:
        for filler in fillers:
            assert not s.text.endswith(f" {filler}"), (filler, s.text)
            assert s.text != filler


def test_generate_cell_no_stacked_final_particle_or_question_word(ctx) -> None:
    """Fix: closer từng được rút vô điều kiện kể cả khi template đã kết ở
    tiểu từ/từ nghi vấn — "…ở đâu không được không", "…không nha",
    "…không đi"."""
    bad_substrings = [
        "không được không",
        "không nha",
        "không đi",
        "không với",
        "không cái nhé",
        "không giúp mình",
        "không đi mà",
        "đâu được không",
        "đâu nha",
        "nào được không",
        "nào nha",
    ]
    samples = _generate_many_casual(ctx, "01", Category.STREET)
    for s in samples:
        for bad in bad_substrings:
            assert bad not in s.text, (bad, s.text)


def test_generate_cell_legacy_province_prefix_never_mismatches(ctx) -> None:
    """Fix chốt trực tiếp bug user báo: "…ở thành phố Quảng Nam" — Đà Nẵng
    (province_id 04) hiện là thành phố nhưng legacy alias "Quảng Nam" của
    nó vốn là tỉnh. Property này phải giữ đúng bất kể "Quảng Nam" có xuất
    hiện hay không (xem test A4 bên dưới cho việc "có xuất hiện không")."""
    samples = _generate_many_casual(ctx, "04", Category.STREET, target_count=200)
    for s in samples:
        assert "thành phố Quảng Nam" not in s.text, s.text
        assert "tỉnh Đà Nẵng" not in s.text, s.text


def test_generate_cell_ambiguous_merged_province_never_leaks_wrong_alias(ctx) -> None:
    """A4: STREET (không có legacy_alias_hint — hard rule thực tế của master
    data hiện tại) thuộc tỉnh 04 (Đà Nẵng, gộp Quảng Nam — 2 candidate thật)
    không bao giờ được sinh "Quảng Nam" — không có căn cứ nào để biết street
    đó thuộc phần Đà Nẵng cũ hay Quảng Nam cũ (fix bug user báo: "Tượng Mẹ
    Nhu ở tỉnh Quảng Nam cũ" — tượng thực tế ở Đà Nẵng cũ, không phải Quảng
    Nam cũ)."""
    samples = _generate_many_casual(ctx, "04", Category.STREET, target_count=200)
    for s in samples:
        assert "Quảng Nam" not in s.text, s.text


def test_generate_cell_hinted_landmark_can_use_ambiguous_merged_province_legacy(ctx) -> None:
    """Đối chứng: landmark THỰC SỰ có legacy_alias_hint="Quảng Nam" trong
    data/raw/admin/landmarks.yaml (Phố cổ Hội An, Thánh địa Mỹ Sơn — nằm ở
    Quảng Nam cũ, không phải Đà Nẵng cũ) — NẾU legacy được rút cho các
    entity này thì luôn đúng tiền tố ("tỉnh", không phải "thành phố"). Chỉ
    khẳng định property phủ định (giống test_..._never_mismatches_sub_region
    ở trên) — entity_pool của tỉnh 04 rất lớn và generate_cell dùng một rng
    liên tục xuyên suốt cell (không độc lập từng sample), nên việc BẮT BUỘC
    quan sát được legacy cho đúng 2 entity hiếm này trong một lần chạy hữu
    hạn không ổn định; cơ chế hint hoạt động đã được chốt trực tiếp, không
    flaky, ở test_variation_apply.py::
    test_compose_entity_ambiguous_legacy_respects_hint_when_present."""
    samples = _generate_many_casual(ctx, "04", Category.LANDMARK, target_count=600)
    hinted = [s for s in samples if s.entity in {"Phố cổ Hội An", "Thánh địa Mỹ Sơn"}]
    assert hinted, "không sinh được sample nào cho 2 landmark có hint — tăng target_count nếu flaky"
    for s in hinted:
        assert "thành phố Quảng Nam" not in s.text, s.text
        if "Quảng Nam" in s.text:
            assert "tỉnh Quảng Nam" in s.text, s.text


# ---------------------------------------------------------------------------
# Regression đợt 2 — 4 lỗi user báo tiếp theo, quy về 3 root cause sửa ở
# templates.py (HeadClass + validator #10) và variation.py (_filler_pool):
# "tự dưng kiếm giúp tôi chỗ thành phố Đà Nẵng nhé" (filler mang nghĩa gắn
# vào câu mệnh lệnh), "gần đây có Chợ Miếu Bông nào không" (lượng từ bất
# định trên tên riêng).
# ---------------------------------------------------------------------------

_MEANINGFUL_FILLERS = ("tự dưng", "kiểu", "ấy")
_IMPERATIVE_HEAD_TOKENS = (
    "chỉ", "dẫn", "đưa", "tìm", "kiếm", "dò", "tra", "ghé", "chạy", "rẽ",
    "dừng", "về", "vào", "xin", "nhờ", "mong", "vui", "hỏi",
)


def test_generate_cell_no_meaningful_filler_on_imperative_sentence(ctx) -> None:
    """Fix "tự dưng kiếm giúp tôi chỗ thành phố Đà Nẵng nhé": filler mang
    nghĩa (tự dưng/kiểu/ấy) không được đứng ngay trước một động từ mệnh
    lệnh — nó chỉ hợp lệ trước một mệnh đề trần thuật có chủ ngữ."""
    samples = _generate_many_casual(ctx, "01", Category.STREET, target_count=300)
    for s in samples:
        for filler in _MEANINGFUL_FILLERS:
            for verb in _IMPERATIVE_HEAD_TOKENS:
                assert not s.text.startswith(f"{filler} {verb} "), s.text


def test_generate_cell_no_indefinite_quantifier_after_proper_noun(ctx) -> None:
    """Fix "gần đây có Chợ Miếu Bông nào không": {entity} là tên riêng, lượng
    từ bất định "nào" không được bám ngay sau một token viết hoa (tên
    entity đã compose, luôn viết hoa chữ cái đầu mỗi từ riêng)."""
    samples = _generate_many_casual(ctx, "01", Category.LANDMARK, target_count=200)
    for s in samples:
        # entity đã compose luôn nằm trong s.entity — kiểm tra trực tiếp
        # entity đó không bị "nào" bám ngay sau trong text.
        assert f"{s.entity} nào" not in s.text, s.text


def test_generate_cell_landmark_never_mismatches_sub_region(ctx) -> None:
    """Chốt bug sub-region: "Núi Bà Đen" chỉ thuộc Tây Ninh cũ, chưa bao giờ
    thuộc Long An — dù tỉnh 30 (hiện hành) gộp cả hai. legacy_alias_hint
    trong landmarks.yaml (xem docs/data-model.md#sub-region-hint) phải chặn
    resolve_province() không bao giờ chọn "Long An" cho entity này, kể cả
    khi name_variant=legacy + hierarchy_depth=full_address được rút."""
    samples = _generate_many_casual(ctx, "30", Category.LANDMARK, target_count=600)
    nui_ba_den = [s for s in samples if s.entity == "Núi Bà Đen"]
    assert nui_ba_den, "không sinh được sample nào cho Núi Bà Đen — tăng target_count nếu flaky"
    for s in nui_ba_den:
        assert s.province_legacy != "Long An", s.text
        assert "Long An" not in s.text, s.text


def test_generate_cell_landmark_available_for_province_without_osm(ctx) -> None:
    """Chốt trực tiếp phần địa danh nổi tiếng: tỉnh không có OSM snapshot
    (vd 22 Ninh Bình) vẫn sinh được câu landmark nhờ data/raw/admin/
    landmarks.yaml (source=manual)."""
    cell = _cell(province_id="22", category=Category.LANDMARK, target_count=20)
    samples = generate_cell(cell, ctx)
    assert len(samples) == 20
    assert all(s.category == Category.LANDMARK for s in samples)
