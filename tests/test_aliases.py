"""Test cho src/navtext/aliases.py — phase 6.

Xem docs/data-model.md#mô-hình-34--63--cách-gọi-phổ-biến.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from navtext.aliases import (
    has_unambiguous_legacy_alias,
    legacy_to_current,
    resolve_name,
    resolve_province,
)
from navtext.loaders import load_master
from navtext.schema import NameVariant

REPO_ROOT = Path(__file__).resolve().parent.parent
MASTER_DIR = REPO_ROOT / "data" / "master"


@pytest.fixture(scope="module")
def master():
    if not MASTER_DIR.exists():
        pytest.skip("data/master/ chưa build")
    return load_master(MASTER_DIR)


def test_resolve_name_current(master) -> None:
    rng = random.Random(1)
    assert resolve_name("Hà Nội", NameVariant.CURRENT, master, rng) == "Hà Nội"


def test_resolve_name_legacy_round_trip_all_63(master) -> None:
    """Mọi legacy alias trong master (đủ 63) phải resolve được ngược lại
    thành province_id đúng qua legacy_to_current, và resolve_name với
    NameVariant.LEGACY trên tên tỉnh hiện hành phải luôn trả về MỘT trong
    các legacy alias hợp lệ của tỉnh đó (không phải tên hiện hành)."""
    legacy_aliases = [
        a
        for aliases in master.aliases_by_province.values()
        for a in aliases
        if a.alias_type == "legacy"
    ]
    assert len(legacy_aliases) == 63

    rng = random.Random(42)
    for alias in legacy_aliases:
        assert legacy_to_current(alias.alias, master) == alias.province_id

        province = master.provinces[alias.province_id]
        valid_legacy_names = {
            a.alias for a in master.aliases_by_province[alias.province_id] if a.alias_type == "legacy"
        }
        for _ in range(5):
            result = resolve_name(province.name_current, NameVariant.LEGACY, master, rng)
            assert result in valid_legacy_names


def test_resolve_name_spoken_fallback_to_current(master) -> None:
    """31/34 tỉnh không có spoken alias — phải fallback về tên hiện hành,
    không raise."""
    rng = random.Random(7)
    province = master.provinces["04"]  # Đà Nẵng — không có spoken alias
    has_spoken = any(
        a.alias_type == "spoken" for a in master.aliases_by_province.get("04", ())
    )
    assert not has_spoken
    result = resolve_name(province.name_current, NameVariant.SPOKEN_ALIAS, master, rng)
    assert result == province.name_current


def test_resolve_name_spoken_present(master) -> None:
    rng = random.Random(3)
    province = master.provinces["01"]  # Hà Nội — có spoken alias
    spoken_names = {
        a.alias for a in master.aliases_by_province["01"] if a.alias_type == "spoken"
    }
    assert spoken_names
    for _ in range(10):
        result = resolve_name(province.name_current, NameVariant.SPOKEN_ALIAS, master, rng)
        assert result in spoken_names


def test_resolve_name_passthrough_non_province_entity(master) -> None:
    """Entity không phải tên tỉnh (street/POI) không có alias — trả nguyên
    vẹn bất kể name_variant, vì province_id_by_alias không khớp."""
    rng = random.Random(9)
    entity = "Hẻm 553/18 Lũy Bán Bích"
    assert resolve_name(entity, NameVariant.LEGACY, master, rng) == entity
    assert resolve_name(entity, NameVariant.CURRENT, master, rng) == entity
    assert resolve_name(entity, NameVariant.SPOKEN_ALIAS, master, rng) == entity


def test_legacy_to_current_unknown_raises(master) -> None:
    with pytest.raises(ValueError):
        legacy_to_current("Không Tồn Tại Chấm Chấm", master)


# ---------------------------------------------------------------------------
# resolve_province — admin_prefix đi theo alias, không theo tỉnh hiện hành
# (fix "ở thành phố Quảng Nam": Đà Nẵng hiện là thành phố nhưng legacy alias
# "Quảng Nam" của nó vốn là tỉnh).
# ---------------------------------------------------------------------------


def test_resolve_province_current_matches_is_municipality(master) -> None:
    rng = random.Random(1)
    da_nang = resolve_province("Đà Nẵng", NameVariant.CURRENT, master, rng)
    assert da_nang.text == "Đà Nẵng"
    assert da_nang.admin_prefix == "thành phố"

    lai_chau = resolve_province("Lai Châu", NameVariant.CURRENT, master, rng)
    assert lai_chau.admin_prefix == "tỉnh"


def test_resolve_province_legacy_prefix_never_leaks_from_current_province(master) -> None:
    """Chốt đúng bug user báo: legacy alias 'Quảng Nam' của tỉnh Đà Nẵng
    (is_municipality=True hiện tại) phải luôn đi kèm admin_prefix 'tỉnh',
    KHÔNG BAO GIỜ 'thành phố' — dù rng chọn variant nào."""
    rng = random.Random(2)
    for _ in range(200):
        resolved = resolve_province("Đà Nẵng", NameVariant.LEGACY, master, rng)
        assert resolved.text in {"Đà Nẵng", "Quảng Nam"}
        if resolved.text == "Quảng Nam":
            assert resolved.admin_prefix == "tỉnh"
        else:
            assert resolved.admin_prefix == "thành phố"


def test_resolve_province_legacy_prefix_consistent_for_all_63(master) -> None:
    """Mọi legacy alias của mọi tỉnh: admin_prefix trả về phải khớp đúng
    dữ liệu curate trong data/master/province_alias.csv, không lệch cặp."""
    rng = random.Random(3)
    legacy_aliases = [
        a
        for aliases in master.aliases_by_province.values()
        for a in aliases
        if a.alias_type == "legacy"
    ]
    assert len(legacy_aliases) == 63
    for alias in legacy_aliases:
        province = master.provinces[alias.province_id]
        for _ in range(3):
            resolved = resolve_province(province.name_current, NameVariant.LEGACY, master, rng)
            if resolved.text == alias.alias:
                assert resolved.admin_prefix == alias.admin_prefix


def test_resolve_province_spoken_alias_prefix_can_be_empty(master) -> None:
    """Spoken alias như 'thủ đô'/'thành phố mang tên Bác' tự mang nghĩa
    hành chính — admin_prefix phải rỗng, cấm gắn thêm 'thành phố'/'tỉnh'."""
    rng = random.Random(4)
    seen_empty = False
    for _ in range(200):
        resolved = resolve_province("Hà Nội", NameVariant.SPOKEN_ALIAS, master, rng)
        if resolved.text == "thủ đô":
            assert resolved.admin_prefix == ""
            seen_empty = True
    assert seen_empty


# ---------------------------------------------------------------------------
# allowed_aliases — sub-region hint (docs/data-model.md#sub-region-hint).
# Chốt bug "Núi Bà Đen ở tỉnh Long An": entity biết chắc mình thuộc tỉnh cũ
# nào thì resolve_province() không được chọn alias legacy khác của cùng
# tỉnh gộp.
# ---------------------------------------------------------------------------


def test_resolve_province_allowed_aliases_restricts_choice(master) -> None:
    rng = random.Random(11)
    for _ in range(200):
        resolved = resolve_province(
            "Tây Ninh",
            NameVariant.LEGACY,
            master,
            rng,
            allowed_aliases=frozenset({"Tây Ninh"}),
        )
        assert resolved.text == "Tây Ninh"
        assert resolved.text != "Long An"


def test_resolve_province_allowed_aliases_no_match_falls_back_to_full_pool(master) -> None:
    """allowed_aliases không khớp legacy alias thật nào của tỉnh (data bug
    phòng hờ) -> fallback về pool đầy đủ, không raise, không trả rỗng. Pool
    đầy đủ của Tây Ninh (tỉnh MERGED) gồm CẢ "Tây Ninh" (predecessor trùng
    tên tỉnh mới — vẫn là candidate hợp lệ, xem aliases.resolve_province())
    lẫn "Long An" — không lọc "Tây Ninh" ra như trước fix (xem
    test_resolve_province_legacy_can_return_identity_named_predecessor)."""
    rng = random.Random(12)
    seen = set()
    for _ in range(200):
        resolved = resolve_province(
            "Tây Ninh",
            NameVariant.LEGACY,
            master,
            rng,
            allowed_aliases=frozenset({"Không Tồn Tại"}),
        )
        seen.add(resolved.text)
    assert seen == {"Long An", "Tây Ninh"}


def test_resolve_province_allowed_aliases_none_keeps_old_behavior(master) -> None:
    """Mặc định allowed_aliases=None phải cho kết quả y hệt như trước khi
    có tham số này (không truyền tường minh) — 2 call site production
    (variation.py) không đổi cách gọi."""
    rng_a = random.Random(13)
    rng_b = random.Random(13)
    for _ in range(50):
        with_default = resolve_province("Tây Ninh", NameVariant.LEGACY, master, rng_a)
        without_param = resolve_province(
            "Tây Ninh", NameVariant.LEGACY, master, rng_b, allowed_aliases=None
        )
        assert with_default == without_param


# ---------------------------------------------------------------------------
# variant_used + identity-alias filtering theo admin_status — fix "33/63
# legacy alias trùng nguyên văn tên hiện hành" (11 tỉnh KHÔNG sáp nhập
# 1/7/2025, vd Hà Nội/Nghệ An/Sơn La có mọi legacy alias == tên hiện hành).
# Với nhóm này, name_variant=legacy sinh text Y HỆT bản current trong khi
# cột audit vẫn ghi "legacy" nếu không lọc — cột audit nói dối.
#
# NHƯNG lọc identity KHÔNG được áp cho tỉnh MERGED (23/34): với nhóm này,
# một legacy alias trùng chữ với tên hiện hành là một predecessor THẬT (vd
# Tây Ninh cũ + Long An -> Tây Ninh mới; "Tây Ninh" là tên tỉnh cũ có thật,
# không phải trùng lặp vô nghĩa) — lọc nó ra khiến LEGACY không bao giờ trả
# về được predecessor trùng tên, một bug thật (gốc rễ "Tượng Mẹ Nhu ở tỉnh
# Quảng Nam cũ": trước fix, resolve_province("Đà Nẵng", LEGACY) LUÔN trả
# "Quảng Nam", KHÔNG BAO GIỜ "Đà Nẵng"). Xem docs/data-model.md#mô-hình-34
# --63--cách-gọi-phổ-biến.
# ---------------------------------------------------------------------------


def test_resolve_province_legacy_merged_can_return_identity_named_predecessor(master) -> None:
    """Tây Ninh (tỉnh 30, MERGED từ Tây Ninh cũ + Long An) có 2 legacy alias
    thật: "Tây Ninh" (trùng tên hiện hành — vẫn là 1 tỉnh cũ có thật, không
    bị lọc) và "Long An" (khác). LEGACY phải rút được CẢ hai, không chỉ ưu
    tiên "Long An" như trước fix."""
    rng = random.Random(21)
    seen = set()
    for _ in range(200):
        resolved = resolve_province("Tây Ninh", NameVariant.LEGACY, master, rng)
        assert resolved.variant_used == NameVariant.LEGACY
        seen.add(resolved.text)
    assert seen == {"Tây Ninh", "Long An"}


def test_resolve_province_legacy_falls_back_to_current_for_unchanged_province(master) -> None:
    """Hà Nội (tỉnh 01, KHÔNG sáp nhập) chỉ có 1 legacy alias, trùng đúng
    tên hiện hành ("Hà Nội"). LEGACY phải fallback về CURRENT — text vẫn
    đúng "Hà Nội" nhưng variant_used phải ghi CURRENT (trung thực), không
    phải LEGACY."""
    rng = random.Random(22)
    for _ in range(50):
        resolved = resolve_province("Hà Nội", NameVariant.LEGACY, master, rng)
        assert resolved.text == "Hà Nội"
        assert resolved.variant_used == NameVariant.CURRENT
        assert resolved.admin_prefix == "thành phố"


def test_resolve_province_variant_used_current_for_current_request(master) -> None:
    rng = random.Random(23)
    resolved = resolve_province("Đà Nẵng", NameVariant.CURRENT, master, rng)
    assert resolved.variant_used == NameVariant.CURRENT


def test_resolve_province_variant_used_spoken_alias_when_present(master) -> None:
    rng = random.Random(24)
    seen_spoken = False
    for _ in range(100):
        resolved = resolve_province("Hà Nội", NameVariant.SPOKEN_ALIAS, master, rng)
        if resolved.text != "Hà Nội":
            seen_spoken = True
            assert resolved.variant_used == NameVariant.SPOKEN_ALIAS
    assert seen_spoken


def test_resolve_province_variant_used_current_when_no_spoken_alias(master) -> None:
    """31/34 tỉnh không có spoken alias — SPOKEN_ALIAS phải fallback về
    CURRENT với variant_used trung thực (không ghi spoken_alias giả)."""
    rng = random.Random(25)
    for _ in range(50):
        resolved = resolve_province("Đà Nẵng", NameVariant.SPOKEN_ALIAS, master, rng)
        assert resolved.variant_used == NameVariant.CURRENT


def test_resolve_province_legacy_always_within_correct_candidate_set(master) -> None:
    """Cho toàn bộ 34 tỉnh: LEGACY luôn rút trong đúng tập candidate hợp lệ
    — tỉnh MERGED thì tập đó là MỌI legacy alias (kể cả alias trùng tên
    hiện hành, vì đó vẫn là một predecessor thật); tỉnh UNCHANGED thì tập đó
    chỉ gồm alias THỰC SỰ khác tên hiện hành (identity bị lọc — legacy của
    tỉnh không sáp nhập chỉ là chính nó, không có gì để nói khác)."""
    rng = random.Random(26)
    for aliases in master.aliases_by_province.values():
        legacy = [a for a in aliases if a.alias_type == "legacy"]
        if not legacy:
            continue
        province = master.provinces[legacy[0].province_id]
        if province.admin_status == "unchanged":
            candidates = [a for a in legacy if a.alias != province.name_current]
        else:
            candidates = legacy
        if not candidates:
            continue
        valid_names = {a.alias for a in candidates}
        for _ in range(10):
            resolved = resolve_province(province.name_current, NameVariant.LEGACY, master, rng)
            assert resolved.text in valid_names
            assert resolved.variant_used == NameVariant.LEGACY


# ---------------------------------------------------------------------------
# has_unambiguous_legacy_alias — A4: chỉ nói tên tỉnh cũ ở đuôi hierarchy
# (entity KHÔNG phải province) khi biết chắc alias nào đúng. 23/34 tỉnh mới
# có >=2 legacy alias (gộp từ nhiều tỉnh cũ) — không có hint xác định entity
# thuộc phần nào thì resolve_province() sẽ bốc alias NGẪU NHIÊN, gán sai địa
# lý (fix bug user báo: "Tượng Mẹ Nhu ở tỉnh Quảng Nam cũ" — tượng thực tế ở
# Đà Nẵng cũ, không phải Quảng Nam cũ). Dùng ở variation._hierarchy_tail().
# ---------------------------------------------------------------------------


def test_has_unambiguous_legacy_alias_true_for_single_alias_province(master) -> None:
    # 06 Huế: KHÔNG sáp nhập (chỉ đổi tên), đúng 1 legacy alias thật khác
    # tên hiện hành ("Thừa Thiên Huế"), không candidate thứ hai nào để nhầm.
    # (Không dùng tỉnh merged nào — mọi tỉnh merged đều có >=2 candidate
    # thật sau fix resolve_province(), xem test false bên dưới.)
    assert has_unambiguous_legacy_alias("06", master) is True


def test_has_unambiguous_legacy_alias_false_for_multi_alias_province(master) -> None:
    # 02 Hồ Chí Minh: 2 legacy alias khác tên hiện hành ("Bà Rịa Vũng Tàu",
    # "Bình Dương") — không đủ căn cứ để chọn đúng nếu không có hint.
    assert has_unambiguous_legacy_alias("02", master) is False


def test_has_unambiguous_legacy_alias_true_for_unchanged_province(master) -> None:
    # 01 Hà Nội: legacy alias duy nhất trùng tên hiện hành (non-identity =
    # 0) — không có gì để nhầm, coi là unambiguous.
    assert has_unambiguous_legacy_alias("01", master) is True


def test_has_unambiguous_legacy_alias_true_for_unknown_province(master) -> None:
    """province_id lạ (data bug phòng hờ) -> True (không chặn oan), để
    caller không phải tự xử lý trường hợp thiếu dữ liệu này."""
    assert has_unambiguous_legacy_alias("không tồn tại", master) is True


def test_resolve_name_still_returns_text_only(master) -> None:
    """resolve_name() là wrapper mỏng quanh resolve_province() — text phải
    khớp nhau cho cùng input/rng state."""
    rng_a = random.Random(5)
    rng_b = random.Random(5)
    assert resolve_name("Đà Nẵng", NameVariant.LEGACY, master, rng_a) == resolve_province(
        "Đà Nẵng", NameVariant.LEGACY, master, rng_b
    ).text
