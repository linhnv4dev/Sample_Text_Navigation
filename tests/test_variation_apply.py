"""Test cho phần applicability + composition mới trong variation.py — phase
6. Xem docs/generation.md#config-variation-yaml, đoạn "Applicability là
logic của code, không phải config".
"""

from __future__ import annotations

import random
import re
from pathlib import Path

import pytest

from navtext.loaders import load_master
from navtext.schema import (
    Category,
    Cell,
    HierarchyDepth,
    LocationRef,
    NameVariant,
    NumberStyle,
    Register,
    SentenceType,
    Source,
    Subcategory,
)
from navtext.templates import HeadClass, TailClass, load_lexicon
from navtext.variation import (
    apply_variations,
    compose_entity,
    draw_axes,
    load_variation_config,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
MASTER_DIR = REPO_ROOT / "data" / "master"
VARIATION_CONFIG_PATH = REPO_ROOT / "config" / "variation.yaml"
TEMPLATES_DIR = REPO_ROOT / "data" / "templates"
_DIGIT = re.compile(r"[0-9]")


@pytest.fixture(scope="module")
def master():
    if not MASTER_DIR.exists():
        pytest.skip("data/master/ chưa build")
    return load_master(MASTER_DIR)


@pytest.fixture(scope="module")
def config():
    return load_variation_config(VARIATION_CONFIG_PATH)


@pytest.fixture(scope="module")
def lexicon():
    return load_lexicon(TEMPLATES_DIR)


def _cell(**overrides) -> Cell:
    base = dict(
        province_id="01",
        category=Category.STREET,
        sentence_type=SentenceType.NAV_COMMAND,
        variation_profile="casual_short",
        target_count=1,
    )
    base.update(overrides)
    return Cell(**base)


def _street_entity(name: str, district_id: str | None = None) -> LocationRef:
    return LocationRef(
        entity_id="street_x",
        name=name,
        category=Category.STREET,
        subcategory=Subcategory.STREET,
        province_id="01",
        district_id=district_id,
        ward_id=None,
        street_id="street_x",
        popularity_tier=2,
        source=Source.OSM,
    )


def _poi_entity(name: str, category: Category, subcategory: Subcategory) -> LocationRef:
    return LocationRef(
        entity_id="poi_x",
        name=name,
        category=category,
        subcategory=subcategory,
        province_id="01",
        district_id=None,
        ward_id=None,
        street_id=None,
        popularity_tier=2,
        source=Source.OSM,
    )


# ---------------------------------------------------------------------------
# draw_axes — luật 1: name_variant ép CURRENT khi không nhắc tên tỉnh
# ---------------------------------------------------------------------------


def test_draw_axes_rule1_non_province_forces_current_name_variant(config) -> None:
    entity = _street_entity("Nguyễn Văn Linh")
    cell = _cell()
    for seed in range(200):
        draw = draw_axes(entity, cell, Register.CASUAL, config, random.Random(seed))
        if draw.hierarchy_depth != HierarchyDepth.FULL_ADDRESS:
            assert draw.name_variant == NameVariant.CURRENT


def test_draw_axes_rule1_province_entity_can_get_legacy(config) -> None:
    entity = LocationRef(
        entity_id="province_01",
        name="Hà Nội",
        category=Category.ADMIN_UNIT,
        subcategory=Subcategory.PROVINCE,
        province_id="01",
        district_id=None,
        ward_id=None,
        street_id=None,
        popularity_tier=1,
        source=Source.MANUAL,
    )
    cell = _cell(category=Category.ADMIN_UNIT)
    variants = {
        draw_axes(entity, cell, Register.CASUAL, config, random.Random(seed)).name_variant
        for seed in range(200)
    }
    assert NameVariant.LEGACY in variants or NameVariant.SPOKEN_ALIAS in variants


# ---------------------------------------------------------------------------
# draw_axes — luật 2: number_style ép none/không-none theo entity có số
# ---------------------------------------------------------------------------


def test_draw_axes_rule2_no_digit_entity_forces_none(config) -> None:
    entity = _street_entity("Nguyễn Văn Linh")
    cell = _cell()
    for seed in range(100):
        draw = draw_axes(entity, cell, Register.NEUTRAL, config, random.Random(seed))
        assert draw.number_style == NumberStyle.NONE


def test_draw_axes_rule2_digit_entity_never_none(config) -> None:
    entity = _street_entity("Trung Yên 11")
    cell = _cell()
    for seed in range(100):
        draw = draw_axes(entity, cell, Register.NEUTRAL, config, random.Random(seed))
        assert draw.number_style != NumberStyle.NONE


def test_draw_axes_rule2_address_category_never_none(config) -> None:
    entity = _street_entity("Nguyễn Văn Linh")  # street entity dùng chung pool cho address
    cell = _cell(category=Category.ADDRESS)
    for seed in range(100):
        draw = draw_axes(entity, cell, Register.NEUTRAL, config, random.Random(seed))
        assert draw.number_style != NumberStyle.NONE


# ---------------------------------------------------------------------------
# draw_axes — luật 3: number_prefix chỉ có nghĩa khi số ở đầu tên
# ---------------------------------------------------------------------------


def test_draw_axes_rule3_trailing_number_never_gets_prefix(config) -> None:
    entity = _street_entity("Trung Yên 11")  # số ở ĐUÔI
    cell = _cell()
    for seed in range(200):
        draw = draw_axes(entity, cell, Register.NEUTRAL, config, random.Random(seed))
        assert draw.number_prefix is False


def test_draw_axes_rule3_leading_number_can_get_prefix(config) -> None:
    entity = _street_entity("Hẻm 553 Lũy Bán Bích")  # số ở ĐẦU (sau tiền tố Hẻm)
    cell = _cell()
    results = {
        draw_axes(entity, cell, Register.NEUTRAL, config, random.Random(seed)).number_prefix
        for seed in range(200)
    }
    assert True in results


def test_draw_axes_rule3_no_number_never_gets_prefix(config) -> None:
    entity = _street_entity("Nguyễn Văn Linh")
    cell = _cell()
    for seed in range(100):
        draw = draw_axes(entity, cell, Register.NEUTRAL, config, random.Random(seed))
        assert draw.number_prefix is False


# ---------------------------------------------------------------------------
# draw_axes — luật 4: admin_prefix ép absent cho POI
# ---------------------------------------------------------------------------


def test_draw_axes_rule4_poi_forces_admin_prefix_absent(config) -> None:
    entity = _poi_entity("Bệnh viện Bạch Mai", Category.PUBLIC_FACILITY, Subcategory.HOSPITAL)
    cell = _cell(category=Category.PUBLIC_FACILITY)
    for seed in range(100):
        draw = draw_axes(entity, cell, Register.FORMAL, config, random.Random(seed))
        assert draw.admin_prefix is False


def test_draw_axes_rule4_street_admin_prefix_varies(config) -> None:
    entity = _street_entity("Nguyễn Văn Linh")
    cell = _cell(category=Category.STREET)
    results = {
        draw_axes(entity, cell, Register.NEUTRAL, config, random.Random(seed)).admin_prefix
        for seed in range(100)
    }
    assert results == {True, False}


# ---------------------------------------------------------------------------
# draw_axes — luật 5: hierarchy_depth degrade poi_district -> full_address
# khi district_id rỗng
# ---------------------------------------------------------------------------


def test_draw_axes_rule5_degrades_poi_district_when_no_district(config) -> None:
    entity = _street_entity("Nguyễn Văn Linh", district_id=None)
    cell = _cell(category=Category.ADDRESS)  # address có poi_district weight cao (0.40)
    for seed in range(300):
        draw = draw_axes(entity, cell, Register.NEUTRAL, config, random.Random(seed))
        assert draw.hierarchy_depth != HierarchyDepth.POI_DISTRICT


def test_draw_axes_rule5_keeps_poi_district_when_district_known(config) -> None:
    entity = _street_entity("Nguyễn Văn Linh", district_id="dn_haichau")
    cell = _cell(category=Category.ADDRESS)
    depths = {
        draw_axes(entity, cell, Register.NEUTRAL, config, random.Random(seed)).hierarchy_depth
        for seed in range(300)
    }
    assert HierarchyDepth.POI_DISTRICT in depths


# ---------------------------------------------------------------------------
# compose_entity — property: không còn chữ số, không lặp tiền tố
# ---------------------------------------------------------------------------


def test_compose_entity_street_no_digit_leak(master, config, lexicon) -> None:
    cell = _cell(category=Category.STREET)
    entity = _street_entity("Hẻm 553/18 Lũy Bán Bích")
    for seed in range(50):
        rng = random.Random(seed)
        draw = draw_axes(entity, cell, Register.CASUAL, config, rng)
        phrase = compose_entity(entity, cell, draw, master, lexicon, config, rng)
        assert not _DIGIT.search(phrase.text), phrase.text


def test_compose_entity_address_no_digit_leak_and_has_house_number(master, config, lexicon) -> None:
    cell = _cell(category=Category.ADDRESS)
    entity = _street_entity("Nguyễn Văn Linh")
    for seed in range(50):
        rng = random.Random(seed)
        draw = draw_axes(entity, cell, Register.NEUTRAL, config, rng)
        phrase = compose_entity(entity, cell, draw, master, lexicon, config, rng)
        assert not _DIGIT.search(phrase.text), phrase.text
        assert phrase.address is not None
        assert "Nguyễn Văn Linh" in phrase.address


def test_compose_entity_no_duplicated_embedded_prefix(master, config, lexicon) -> None:
    """"đường Đường Bàu Trảng bốn" là lỗi — tiền tố nhúng sẵn không được
    lặp lại khi admin_prefix=present."""
    cell = _cell(category=Category.STREET)
    entity = _street_entity("Đường Bàu Trảng 4")
    for seed in range(50):
        rng = random.Random(seed)
        draw = draw_axes(entity, cell, Register.NEUTRAL, config, rng)
        phrase = compose_entity(entity, cell, draw, master, lexicon, config, rng)
        assert "đường đường" not in phrase.text.lower()
        assert "phố đường" not in phrase.text.lower()


def test_compose_entity_alley_prefix_always_kept(master, config, lexicon) -> None:
    """Ngõ/Hẻm/Ngách/Kiệt mang nghĩa — không bao giờ bị lược bỏ dù
    admin_prefix drawn absent."""
    cell = _cell(category=Category.STREET)
    entity = _street_entity("Hẻm 553 Lũy Bán Bích")
    for seed in range(50):
        rng = random.Random(seed)
        draw = draw_axes(entity, cell, Register.NEUTRAL, config, rng)
        phrase = compose_entity(entity, cell, draw, master, lexicon, config, rng)
        assert "hẻm" in phrase.text.lower()


def test_compose_entity_province_legacy_audit_field(master, config, lexicon) -> None:
    """Dùng Đà Nẵng (04, gộp Đà Nẵng cũ + Quảng Nam) thay vì Hà Nội (01,
    không sáp nhập) — Hà Nội chỉ có legacy alias identity ("Hà Nội" == tên
    hiện hành), nên LEGACY luôn fallback CURRENT (xem
    test_aliases.py::test_resolve_province_legacy_falls_back_to_current_for_unchanged_province),
    và test này cần một tỉnh THỰC SỰ có legacy alias khác tên hiện hành để
    quan sát được province_legacy != None."""
    entity = LocationRef(
        entity_id="province_04",
        name="Đà Nẵng",
        category=Category.ADMIN_UNIT,
        subcategory=Subcategory.PROVINCE,
        province_id="04",
        district_id=None,
        ward_id=None,
        street_id=None,
        popularity_tier=1,
        source=Source.MANUAL,
    )
    cell = _cell(province_id="04", category=Category.ADMIN_UNIT)
    found_legacy = False
    for seed in range(300):
        rng = random.Random(seed)
        draw = draw_axes(entity, cell, Register.CASUAL, config, rng)
        phrase = compose_entity(entity, cell, draw, master, lexicon, config, rng)
        assert not _DIGIT.search(phrase.text)
        # variant_used là nguồn sự thật, không phải draw.name_variant —
        # resolve_province() có thể fallback (xem aliases.py). province_legacy
        # chỉ được set khi variant_used THỰC SỰ là LEGACY.
        if phrase.variant_used == NameVariant.LEGACY:
            found_legacy = True
            assert phrase.province_legacy is not None
            # 04 gộp từ 2 tỉnh cũ thật sự: "Quảng Nam" VÀ "Đà Nẵng" (predecessor
            # trùng tên với tỉnh mới — vẫn là một legacy alias hợp lệ, không bị
            # lọc, xem aliases.resolve_province()) — cả hai đều là kết quả đúng.
            assert phrase.province_legacy in {"Quảng Nam", "Đà Nẵng"}
        elif draw.name_variant == NameVariant.LEGACY:
            # LEGACY được rút nhưng fallback về CURRENT — chỉ xảy ra nếu
            # allowed_aliases lọc rỗng, không áp dụng ở đây (không có hint).
            assert phrase.province_legacy is None
    assert found_legacy


def test_compose_entity_legacy_province_never_gets_current_prefix(master, config, lexicon) -> None:
    """Fix chốt trực tiếp bug user báo: "...ở thành phố Quảng Nam" — Đà
    Nẵng (province_id 04) hiện là thành phố, nhưng legacy alias "Quảng Nam"
    của nó vốn là tỉnh. full_address + LEGACY không bao giờ được sinh ra
    cụm "thành phố Quảng Nam".

    legacy_alias_hint="Quảng Nam" pin entity vào đúng alias đó (04 giờ có 2
    candidate thật — "Quảng Nam" VÀ "Đà Nẵng", xem A4/resolve_province()) —
    không có hint, LEGACY sẽ fallback CURRENT theo A4 và test không quan sát
    được nhánh "Quảng Nam" đang muốn chốt."""
    from navtext.schema import HierarchyDepth
    from navtext.variation import AxisDraw

    entity = LocationRef(
        entity_id="street_da_nang",
        name="Nguyễn Văn Linh",
        category=Category.STREET,
        subcategory=Subcategory.STREET,
        province_id="04",
        district_id=None,
        ward_id=None,
        street_id="street_da_nang",
        popularity_tier=2,
        source=Source.OSM,
        legacy_alias_hint="Quảng Nam",
    )
    cell = _cell(province_id="04", category=Category.STREET)
    draw = AxisDraw(
        name_variant=NameVariant.LEGACY,
        number_style=NumberStyle.CARDINAL,
        number_prefix=False,
        admin_prefix=True,
        hierarchy_depth=HierarchyDepth.FULL_ADDRESS,
        legacy_marker=False,
        hierarchy_admin_prefix=True,
    )
    seen_quang_nam = False
    for seed in range(200):
        rng = random.Random(seed)
        phrase = compose_entity(entity, cell, draw, master, lexicon, config, rng)
        assert "thành phố Quảng Nam" not in phrase.text, phrase.text
        if "Quảng Nam" in phrase.text:
            seen_quang_nam = True
            assert "tỉnh Quảng Nam" in phrase.text, phrase.text
        if "Đà Nẵng" in phrase.text:
            assert "thành phố Đà Nẵng" in phrase.text, phrase.text
    assert seen_quang_nam


def test_compose_entity_full_address_mentions_province(master, config, lexicon) -> None:
    """category street/POI với hierarchy_depth=full_address phải có đuôi
    " ở <prefix> <tên tỉnh theo variant>" — điều kiện cần cho coverage
    63/63 legacy alias (tên cụ thể tuỳ name_variant: current/legacy/
    spoken_alias, không nhất thiết luôn là tên hiện hành).

    Dùng tỉnh 04 (Đà Nẵng, gộp Quảng Nam) thay vì mặc định 01 (Hà Nội,
    không sáp nhập — LEGACY luôn fallback CURRENT nên không bao giờ quan
    sát được province_legacy != None, xem test_aliases.py). legacy_alias_hint
    pin entity vào "Quảng Nam" — không có hint, A4 ép LEGACY fallback CURRENT
    vì 04 có 2 candidate thật (xem test_compose_entity_ambiguous_legacy_*)."""
    entity = LocationRef(
        entity_id="street_x",
        name="Nguyễn Văn Linh",
        category=Category.STREET,
        subcategory=Subcategory.STREET,
        province_id="04",
        district_id=None,
        ward_id=None,
        street_id="street_x",
        popularity_tier=2,
        source=Source.OSM,
        legacy_alias_hint="Quảng Nam",
    )
    cell = _cell(province_id="04", category=Category.STREET)
    found_full_address = False
    found_legacy_mention = False
    for seed in range(300):
        rng = random.Random(seed)
        draw = draw_axes(entity, cell, Register.NEUTRAL, config, rng)
        if draw.hierarchy_depth == HierarchyDepth.FULL_ADDRESS:
            found_full_address = True
            phrase = compose_entity(entity, cell, draw, master, lexicon, config, rng)
            assert " ở " in phrase.text, phrase.text
            if phrase.variant_used == NameVariant.LEGACY:
                found_legacy_mention = True
                assert phrase.province_legacy is not None
                assert phrase.province_legacy in phrase.text
    assert found_full_address
    assert found_legacy_mention


# ---------------------------------------------------------------------------
# hierarchy_admin_prefix — đuôi hierarchy phải có CẢ hai dạng có/không tiền
# tố hành chính ("ở Đà Nẵng" lẫn "ở thành phố Đà Nẵng"), không nối vô điều
# kiện như trước (fix bug user báo cáo — 99% sample có tiền tố, "Tượng Mẹ
# Nhu ở thành phố Đà Nẵng" không tự nhiên bằng "ở Đà Nẵng").
# ---------------------------------------------------------------------------


def test_draw_axes_hierarchy_admin_prefix_varies_for_poi(config) -> None:
    """POI category (admin_prefix của CHÍNH entity luôn ép absent — rule #4)
    vẫn phải rút được cả present/absent cho hierarchy_admin_prefix — đây là
    trục riêng, không lây rule #4 của entity sang đuôi hierarchy."""
    entity = _poi_entity("Tượng Mẹ Nhu", Category.LANDMARK, Subcategory.MONUMENT)
    cell = _cell(category=Category.LANDMARK, province_id="04")
    results = set()
    for seed in range(400):
        draw = draw_axes(entity, cell, Register.CASUAL, config, random.Random(seed))
        if draw.hierarchy_depth != HierarchyDepth.POI_ONLY:
            results.add(draw.hierarchy_admin_prefix)
    assert results == {True, False}


def test_draw_axes_hierarchy_admin_prefix_moot_when_poi_only(config) -> None:
    """poi_only không sinh đuôi hierarchy nào -> trục này luôn False, không
    lãng phí một lượt rút rng vô nghĩa."""
    entity = _poi_entity("Tượng Mẹ Nhu", Category.LANDMARK, Subcategory.MONUMENT)
    cell = _cell(category=Category.LANDMARK, province_id="04")
    for seed in range(200):
        draw = draw_axes(entity, cell, Register.CASUAL, config, random.Random(seed))
        if draw.hierarchy_depth == HierarchyDepth.POI_ONLY:
            assert draw.hierarchy_admin_prefix is False


def test_compose_entity_full_address_tail_varies_prefix_for_poi(master, config, lexicon) -> None:
    """Chốt trực tiếp bug user báo: entity POI (landmark) với đuôi
    full_address phải sinh được CẢ "ở Đà Nẵng" (không tiền tố) LẪN "ở thành
    phố Đà Nẵng" (có tiền tố) — không phải luôn luôn một trong hai."""
    entity = LocationRef(
        entity_id="poi_tuong_me_nhu",
        name="Tượng Mẹ Nhu",
        category=Category.LANDMARK,
        subcategory=Subcategory.MONUMENT,
        province_id="04",
        district_id=None,
        ward_id=None,
        street_id=None,
        popularity_tier=2,
        source=Source.OSM,
    )
    cell = _cell(category=Category.LANDMARK, province_id="04")
    seen_with_prefix = False
    seen_without_prefix = False
    for seed in range(500):
        rng = random.Random(seed)
        draw = draw_axes(entity, cell, Register.CASUAL, config, rng)
        if draw.hierarchy_depth != HierarchyDepth.FULL_ADDRESS or draw.name_variant != NameVariant.CURRENT:
            continue
        phrase = compose_entity(entity, cell, draw, master, lexicon, config, rng)
        if "ở thành phố Đà Nẵng" in phrase.text:
            seen_with_prefix = True
        elif "ở Đà Nẵng" in phrase.text:
            seen_without_prefix = True
    assert seen_with_prefix
    assert seen_without_prefix


def test_compose_entity_admin_prefix_used_reflects_hierarchy_tail(master, config, lexicon) -> None:
    """EntityPhrase.admin_prefix_used phải phản ánh tiền tố THỰC SỰ xuất
    hiện trong text kể cả khi tiền tố đó chỉ nằm ở đuôi hierarchy (core_text
    của POI không bao giờ có admin_prefix riêng — rule #4) — nếu không, cột
    `variation` audit sai khi core_text absent nhưng tail present."""
    entity = LocationRef(
        entity_id="poi_tuong_me_nhu",
        name="Tượng Mẹ Nhu",
        category=Category.LANDMARK,
        subcategory=Subcategory.MONUMENT,
        province_id="04",
        district_id=None,
        ward_id=None,
        street_id=None,
        popularity_tier=2,
        source=Source.OSM,
    )
    cell = _cell(category=Category.LANDMARK, province_id="04")
    seen_used_true = False
    for seed in range(500):
        rng = random.Random(seed)
        draw = draw_axes(entity, cell, Register.CASUAL, config, rng)
        phrase = compose_entity(entity, cell, draw, master, lexicon, config, rng)
        if "thành phố Đà Nẵng" in phrase.text:
            assert phrase.admin_prefix_used is True, phrase.text
            seen_used_true = True
        if not phrase.admin_prefix_used:
            assert "thành phố" not in phrase.text and "tỉnh" not in phrase.text, phrase.text
    assert seen_used_true


# ---------------------------------------------------------------------------
# A4 — chỉ nói tên tỉnh cũ ở đuôi hierarchy khi BIẾT CHẮC alias nào đúng.
# Fix bug user báo: "Tượng Mẹ Nhu ở tỉnh Quảng Nam cũ" (tượng thực tế ở Đà
# Nẵng cũ, không phải Quảng Nam cũ) — 04 Đà Nẵng có 2 legacy alias
# ("Quảng Nam", "Đà Nẵng"), entity không có legacy_alias_hint nên không có
# căn cứ chọn đúng.
# ---------------------------------------------------------------------------


def test_compose_entity_ambiguous_legacy_never_leaks_wrong_alias_without_hint(
    master, config, lexicon
) -> None:
    """Entity POI thuộc tỉnh 04 (Đà Nẵng, gộp Quảng Nam — 2 legacy alias),
    KHÔNG có legacy_alias_hint: đuôi hierarchy FULL_ADDRESS + LEGACY intent
    không bao giờ được sinh "Quảng Nam" — chỉ được fallback CURRENT (luôn
    "Đà Nẵng")."""
    from navtext.variation import AxisDraw

    entity = LocationRef(
        entity_id="poi_tuong_me_nhu",
        name="Tượng Mẹ Nhu",
        category=Category.LANDMARK,
        subcategory=Subcategory.MONUMENT,
        province_id="04",
        district_id=None,
        ward_id=None,
        street_id=None,
        popularity_tier=2,
        source=Source.OSM,
        legacy_alias_hint=None,
    )
    cell = _cell(category=Category.LANDMARK, province_id="04")
    draw = AxisDraw(
        name_variant=NameVariant.LEGACY,
        number_style=NumberStyle.CARDINAL,
        number_prefix=False,
        admin_prefix=False,
        hierarchy_depth=HierarchyDepth.FULL_ADDRESS,
        legacy_marker=False,
        hierarchy_admin_prefix=True,
    )
    for seed in range(300):
        rng = random.Random(seed)
        phrase = compose_entity(entity, cell, draw, master, lexicon, config, rng)
        assert "Quảng Nam" not in phrase.text, phrase.text
        assert "Đà Nẵng" in phrase.text, phrase.text
        assert phrase.variant_used == NameVariant.CURRENT


def test_compose_entity_ambiguous_legacy_respects_hint_when_present(master, config, lexicon) -> None:
    """Cùng tỉnh 04 (ambiguous), nhưng entity CÓ legacy_alias_hint="Quảng
    Nam" (vd landmark ở Hội An, xem data/raw/admin/landmarks.yaml) — hint
    đã pin chính xác, LEGACY vẫn được phép sinh "Quảng Nam"."""
    from navtext.variation import AxisDraw

    entity = LocationRef(
        entity_id="poi_pho_co_hoi_an",
        name="Phố cổ Hội An",
        category=Category.LANDMARK,
        subcategory=Subcategory.HERITAGE_SITE,
        province_id="04",
        district_id=None,
        ward_id=None,
        street_id=None,
        popularity_tier=1,
        source=Source.MANUAL,
        legacy_alias_hint="Quảng Nam",
    )
    cell = _cell(category=Category.LANDMARK, province_id="04")
    draw = AxisDraw(
        name_variant=NameVariant.LEGACY,
        number_style=NumberStyle.CARDINAL,
        number_prefix=False,
        admin_prefix=False,
        hierarchy_depth=HierarchyDepth.FULL_ADDRESS,
        legacy_marker=False,
        hierarchy_admin_prefix=True,
    )
    seen_quang_nam = False
    for seed in range(200):
        rng = random.Random(seed)
        phrase = compose_entity(entity, cell, draw, master, lexicon, config, rng)
        assert "Bà Rịa" not in phrase.text
        if "Quảng Nam" in phrase.text:
            seen_quang_nam = True
            assert phrase.variant_used == NameVariant.LEGACY
    assert seen_quang_nam


def test_compose_entity_unambiguous_legacy_still_works_without_hint(master, config, lexicon) -> None:
    """Tỉnh KHÔNG sáp nhập nhưng có legacy alias thật khác tên hiện hành
    (06 Huế -> "Thừa Thiên Huế", đổi tên năm 2025 nhưng không gộp thêm tỉnh
    nào khác) — không có candidate thứ hai nào để nhầm, LEGACY vẫn hoạt động
    bình thường dù entity không có hint. (Không dùng tỉnh MERGED nào ở đây —
    sau A4/resolve_province fix, MỌI tỉnh merged đều có >=2 candidate thật,
    xem test_has_unambiguous_legacy_alias_false_for_multi_alias_province.)"""
    entity = LocationRef(
        entity_id="street_x",
        name="Nguyễn Văn Linh",
        category=Category.STREET,
        subcategory=Subcategory.STREET,
        province_id="06",
        district_id=None,
        ward_id=None,
        street_id="street_x",
        popularity_tier=2,
        source=Source.OSM,
    )
    cell = _cell(category=Category.STREET, province_id="06")
    seen_legacy = False
    for seed in range(300):
        rng = random.Random(seed)
        draw = draw_axes(entity, cell, Register.NEUTRAL, config, rng)
        if draw.hierarchy_depth != HierarchyDepth.FULL_ADDRESS or draw.name_variant != NameVariant.LEGACY:
            continue
        phrase = compose_entity(entity, cell, draw, master, lexicon, config, rng)
        if "Thừa Thiên Huế" in phrase.text:
            seen_legacy = True
            assert phrase.variant_used == NameVariant.LEGACY
    assert seen_legacy


# ---------------------------------------------------------------------------
# legacy_marker ("cũ") — cách nói phổ biến nhất hiện nay để phân biệt tên
# tỉnh cũ với tỉnh mới sau sáp nhập 1/7/2025 (vd "Bình Dương cũ").
# ---------------------------------------------------------------------------


def test_compose_entity_legacy_marker_appends_cu_when_variant_used_legacy(master, config, lexicon) -> None:
    """legacy_alias_hint pin entity vào "Quảng Nam" — 04 có 2 candidate thật
    (Quảng Nam, Đà Nẵng — xem A4/resolve_province fix), không có hint LEGACY
    sẽ fallback CURRENT và test không quan sát được nhánh "cũ" đang muốn
    chốt."""
    from navtext.variation import AxisDraw

    entity = LocationRef(
        entity_id="street_x",
        name="Nguyễn Văn Linh",
        category=Category.STREET,
        subcategory=Subcategory.STREET,
        province_id="04",
        district_id=None,
        ward_id=None,
        street_id="street_x",
        popularity_tier=2,
        source=Source.OSM,
        legacy_alias_hint="Quảng Nam",
    )
    cell = _cell(province_id="04", category=Category.STREET)
    draw = AxisDraw(
        name_variant=NameVariant.LEGACY,
        number_style=NumberStyle.CARDINAL,
        number_prefix=False,
        admin_prefix=True,
        hierarchy_depth=HierarchyDepth.FULL_ADDRESS,
        legacy_marker=True,
        hierarchy_admin_prefix=True,
    )
    rng = random.Random(1)
    phrase = compose_entity(entity, cell, draw, master, lexicon, config, rng)
    assert "tỉnh Quảng Nam cũ" in phrase.text, phrase.text
    # province_legacy audit vẫn ghi tên GỐC (không kèm "cũ") — khớp
    # province_alias.csv, dùng để đếm coverage 63/63 bằng substring-search.
    assert phrase.province_legacy == "Quảng Nam"


def test_compose_entity_legacy_marker_never_appended_on_fallback_to_current(master, config, lexicon) -> None:
    """Hà Nội (01) chỉ có legacy alias identity — dù legacy_marker=True được
    rút, resolve_province() fallback về CURRENT nên KHÔNG BAO GIỜ được nối
    "cũ" (nối "cũ" vào tên hiện hành sẽ sai — "Hà Nội cũ" không có nghĩa)."""
    from navtext.variation import AxisDraw

    entity = LocationRef(
        entity_id="street_hn",
        name="Nguyễn Trãi",
        category=Category.STREET,
        subcategory=Subcategory.STREET,
        province_id="01",
        district_id=None,
        ward_id=None,
        street_id="street_hn",
        popularity_tier=2,
        source=Source.OSM,
    )
    cell = _cell(province_id="01", category=Category.STREET)
    draw = AxisDraw(
        name_variant=NameVariant.LEGACY,
        number_style=NumberStyle.CARDINAL,
        number_prefix=False,
        admin_prefix=True,
        hierarchy_depth=HierarchyDepth.FULL_ADDRESS,
        legacy_marker=True,
        hierarchy_admin_prefix=True,
    )
    for seed in range(50):
        rng = random.Random(seed)
        phrase = compose_entity(entity, cell, draw, master, lexicon, config, rng)
        assert "cũ" not in phrase.text, phrase.text
        assert phrase.variant_used == NameVariant.CURRENT


def test_compose_entity_legacy_marker_absent_never_appends_cu(master, config, lexicon) -> None:
    from navtext.variation import AxisDraw

    entity = LocationRef(
        entity_id="street_x",
        name="Nguyễn Văn Linh",
        category=Category.STREET,
        subcategory=Subcategory.STREET,
        province_id="04",
        district_id=None,
        ward_id=None,
        street_id="street_x",
        popularity_tier=2,
        source=Source.OSM,
    )
    cell = _cell(province_id="04", category=Category.STREET)
    draw = AxisDraw(
        name_variant=NameVariant.LEGACY,
        number_style=NumberStyle.CARDINAL,
        number_prefix=False,
        admin_prefix=True,
        hierarchy_depth=HierarchyDepth.FULL_ADDRESS,
        legacy_marker=False,
        hierarchy_admin_prefix=True,
    )
    for seed in range(50):
        rng = random.Random(seed)
        phrase = compose_entity(entity, cell, draw, master, lexicon, config, rng)
        assert "cũ" not in phrase.text, phrase.text


def test_draw_axes_legacy_marker_forced_absent_when_not_legacy(master, config) -> None:
    """legacy_marker chỉ được rút (có thể True) khi name_variant=legacy VỪA
    được rút cùng lượt — mọi trường hợp khác luôn False, bất kể weight
    config nói gì (applicability là logic của code, không phải config)."""
    entity = _street_entity("Nguyễn Văn Linh")
    cell = _cell(category=Category.STREET)
    for seed in range(300):
        rng = random.Random(seed)
        draw = draw_axes(entity, cell, Register.NEUTRAL, config, rng)
        if draw.name_variant != NameVariant.LEGACY:
            assert draw.legacy_marker is False


# ---------------------------------------------------------------------------
# apply_variations — opener/closer/filler
# ---------------------------------------------------------------------------


def test_apply_variations_leaves_entity_untouched(config, lexicon) -> None:
    cell = _cell()
    slots = {"opener": "", "entity": "đường Nguyễn Văn Linh", "closer": ""}
    result = apply_variations(
        slots, cell, config, random.Random(1), register=Register.CASUAL, lexicon=lexicon
    )
    assert result["entity"] == "đường Nguyễn Văn Linh"


def test_apply_variations_only_touches_declared_slots(config, lexicon) -> None:
    cell = _cell()
    slots = {"entity": "đường Nguyễn Văn Linh"}
    result = apply_variations(
        slots, cell, config, random.Random(1), register=Register.CASUAL, lexicon=lexicon
    )
    assert set(result) == {"entity"}


def test_apply_variations_opener_present_draws_from_pool(config, lexicon) -> None:
    cell = _cell()
    slots = {"opener": "", "entity": "x", "closer": ""}
    filler_texts = {f.text for f in lexicon.fillers}
    seen_nonempty = False
    for seed in range(200):
        result = apply_variations(
            slots, cell, config, random.Random(seed), register=Register.CASUAL, lexicon=lexicon
        )
        if result["opener"]:
            seen_nonempty = True
            # phải là một base word thật trong lexicon.openers, HOẶC filler
            # đứng một mình khi opener drawn absent nhưng filler present.
            assert any(
                result["opener"].startswith(o.text) for o in lexicon.openers[Register.CASUAL]
            ) or result["opener"] in filler_texts
    assert seen_nonempty


def test_apply_variations_filler_never_lands_on_closer(config, lexicon) -> None:
    """Fix: filler (kiểu/tự dưng/à/...) từng bị nối vào closer khi template
    không có {opener} -> câu kết thúc bằng filler ("...Sea Food kiểu",
    "...không tự dưng"). Giờ filler chỉ được nối vào opener; template
    không có {opener} thì filler bị bỏ qua hoàn toàn."""
    cell = _cell()
    slots_no_opener = {"entity": "x", "closer": ""}
    filler_texts = [f.text for f in lexicon.fillers]
    for seed in range(300):
        result = apply_variations(
            slots_no_opener,
            cell,
            config,
            random.Random(seed),
            register=Register.CASUAL,
            lexicon=lexicon,
        )
        assert result["closer"] not in filler_texts
        for filler in filler_texts:
            assert result["closer"] != filler
            assert not result["closer"].endswith(f" {filler}")


def test_apply_variations_filler_only_ever_on_opener(config, lexicon) -> None:
    cell = _cell()
    slots = {"opener": "", "entity": "x", "closer": ""}
    filler_texts = [f.text for f in lexicon.fillers]
    seen_filler = False
    for seed in range(300):
        result = apply_variations(
            slots, cell, config, random.Random(seed), register=Register.CASUAL, lexicon=lexicon
        )
        for filler in filler_texts:
            if result["closer"] == filler or result["closer"].endswith(f" {filler}"):
                pytest.fail(f"filler {filler!r} lọt vào closer: {result!r}")
            if result["opener"] == filler or result["opener"].endswith(f" {filler}"):
                seen_filler = True
    assert seen_filler


def test_apply_variations_self_asks_excludes_ask_openers(config, lexicon) -> None:
    """Fix lặp cụm hỏi ("xin phép hỏi xin hỏi..."): template.self_asks=True
    (template đã tự mở đầu bằng "hỏi" ngay sau {opener}, vd
    '{opener}xin hỏi chỗ này...') phải chặn không rút thêm một opener CŨNG
    chứa "hỏi" — kể cả khi mọi opener của register đó đều chứa "hỏi"
    (formal), auto-fallback về pool gốc là SAI (sẽ luôn lặp)."""
    cell = _cell()
    slots = {"opener": "", "entity": "x", "closer": ""}
    for register in (Register.FORMAL, Register.NEUTRAL, Register.CASUAL):
        for seed in range(200):
            result = apply_variations(
                slots, cell, config, random.Random(seed), register=register, lexicon=lexicon, self_asks=True
            )
            assert "hỏi" not in result["opener"], (register, result["opener"])


def test_apply_variations_self_asks_false_keeps_ask_openers_available(config, lexicon) -> None:
    """Đối chứng: self_asks=False (mặc định) không bị chặn — opener chứa
    "hỏi" vẫn rút được bình thường trước một mệnh đề tương thích (formal chỉ
    có loại opener này, và toàn bộ đều gated head=[statement, question] —
    xem test_apply_variations_ask_framed_opener_never_on_imperative cho
    nhánh IMPERATIVE bị chặn)."""
    cell = _cell()
    slots = {"opener": "", "entity": "x", "closer": ""}
    seen_ask_opener = False
    for seed in range(200):
        result = apply_variations(
            slots,
            cell,
            config,
            random.Random(seed),
            register=Register.FORMAL,
            lexicon=lexicon,
            head_class=HeadClass.STATEMENT,
            self_asks=False,
        )
        if "hỏi" in result["opener"]:
            seen_ask_opener = True
    assert seen_ask_opener


def test_apply_variations_ask_framed_opener_never_on_imperative(config, lexicon) -> None:
    """Fix "xin phép hỏi xin chỉ đường đến ... giúp tôi với ạ" (ví dụ user):
    opener khung hỏi ("xin phép hỏi", "cho tôi hỏi", ...) chỉ hợp lý trước
    một mệnh đề TRẦN THUẬT/NGHI VẤN, không phải trước một câu CẦU KHIẾN
    (chính opener đó rồi lại đứng trước một câu "xin ..." khác) —
    head_class=IMPERATIVE phải luôn loại các opener này khỏi pool, kể cả khi
    self_asks=False (template không tự bắt đầu bằng "hỏi", nên cơ chế
    self_asks cũ không bắt được case này)."""
    cell = _cell()
    slots = {"opener": "", "entity": "x", "closer": ""}
    ask_framed = {
        o.text
        for reg_pool in lexicon.openers.values()
        for o in reg_pool
        if HeadClass.QUESTION in o.heads and HeadClass.IMPERATIVE not in o.heads
    }
    assert ask_framed, "cần ít nhất 1 opener chỉ heads=[statement, question] để test có ý nghĩa"
    for register in (Register.FORMAL, Register.NEUTRAL, Register.CASUAL):
        for seed in range(300):
            result = apply_variations(
                slots,
                cell,
                config,
                random.Random(seed),
                register=register,
                lexicon=lexicon,
                head_class=HeadClass.IMPERATIVE,
                self_asks=False,
            )
            assert result["opener"] not in ask_framed, (register, result["opener"])


def test_apply_variations_ask_framed_opener_available_on_statement(config, lexicon) -> None:
    """Đối chứng: cùng opener khung hỏi đó vẫn rút được bình thường khi
    head_class=STATEMENT/QUESTION (khung câu tương thích)."""
    cell = _cell()
    slots = {"opener": "", "entity": "x", "closer": ""}
    seen_ask_opener = False
    for seed in range(300):
        result = apply_variations(
            slots,
            cell,
            config,
            random.Random(seed),
            register=Register.NEUTRAL,
            lexicon=lexicon,
            head_class=HeadClass.STATEMENT,
            self_asks=False,
        )
        if "hỏi" in result["opener"]:
            seen_ask_opener = True
    assert seen_ask_opener


def test_apply_variations_vocative_opener_available_on_imperative(config, lexicon) -> None:
    """Opener hô gọi trung tính (này/ê/à/ơ, heads phủ cả IMPERATIVE) không
    bị chặn bởi head-gating — vẫn rút được trước câu cầu khiến."""
    cell = _cell()
    slots = {"opener": "", "entity": "x", "closer": ""}
    vocative = {
        o.text
        for o in lexicon.openers.get(Register.CASUAL, ())
        if HeadClass.IMPERATIVE in o.heads
    }
    assert vocative, "cần ít nhất 1 opener casual dùng được cho IMPERATIVE"
    seen = False
    for seed in range(300):
        result = apply_variations(
            slots,
            cell,
            config,
            random.Random(seed),
            register=Register.CASUAL,
            lexicon=lexicon,
            head_class=HeadClass.IMPERATIVE,
            self_asks=False,
        )
        if result["opener"] in vocative:
            seen = True
    assert seen


def test_apply_variations_self_helps_excludes_help_closers(config, lexicon) -> None:
    """Fix lặp "giúp" ("tìm giúp mình đường tới ... giúp mình"):
    template.self_helps=True (template tự chứa "giúp" ở đâu đó trong text)
    phải chặn không rút thêm closer CŨNG chứa "giúp"."""
    cell = _cell()
    slots = {"opener": "", "entity": "x", "closer": ""}
    for register in (Register.FORMAL, Register.NEUTRAL, Register.CASUAL):
        for seed in range(200):
            result = apply_variations(
                slots, cell, config, random.Random(seed), register=register, lexicon=lexicon, self_helps=True
            )
            assert "giúp" not in result["closer"], (register, result["closer"])


def test_apply_variations_closer_after_final_particle_uses_narrow_pool(config, lexicon) -> None:
    """Fix: template đã kết ở tiểu từ cuối câu ("...không{closer}") không
    còn được rút closer bất kỳ trong pool casual đầy đủ — tránh
    "...ở đâu không được không", "...không nha"."""
    cell = _cell()
    slots = {"entity": "x", "closer": ""}
    casual_full_pool = set(lexicon.closers[Register.CASUAL])
    narrow_pool = set(lexicon.closers_after_particle.get(Register.CASUAL, ())) | {""}
    for seed in range(300):
        result = apply_variations(
            slots,
            cell,
            config,
            random.Random(seed),
            register=Register.CASUAL,
            lexicon=lexicon,
            tail_class=TailClass.FINAL_PARTICLE,
        )
        assert result["closer"] in narrow_pool
        if result["closer"]:
            assert result["closer"] not in casual_full_pool - narrow_pool


def test_apply_variations_closer_after_trailing_clause_is_always_empty(config, lexicon) -> None:
    """Fix: template kết bằng một mệnh đề BÌNH LUẬN sau dấu phẩy, tách khỏi
    {entity} (vd "...tới {entity} với, mình không rành đường lắm{closer}")
    không được rút thêm closer nào — closer không thể dính vào SAU mệnh đề
    bình luận đã trọn vẹn ("...mình không rành đường lắm đi" sai)."""
    cell = _cell()
    slots = {"entity": "x", "closer": ""}
    for register in (Register.FORMAL, Register.NEUTRAL, Register.CASUAL):
        for seed in range(200):
            result = apply_variations(
                slots,
                cell,
                config,
                random.Random(seed),
                register=register,
                lexicon=lexicon,
                tail_class=TailClass.TRAILING_CLAUSE,
            )
            assert result["closer"] == "", (register, result["closer"])


def test_apply_variations_closer_after_question_word_uses_narrow_pool(config, lexicon) -> None:
    """Cùng bug với FINAL_PARTICLE, khác chỗ template kết ở từ nghi vấn
    ("...ở đâu{closer}", "...đường nào{closer}")."""
    cell = _cell()
    slots = {"entity": "x", "closer": ""}
    narrow_pool = set(lexicon.closers_after_question.get(Register.CASUAL, ())) | {""}
    for seed in range(300):
        result = apply_variations(
            slots,
            cell,
            config,
            random.Random(seed),
            register=Register.CASUAL,
            lexicon=lexicon,
            tail_class=TailClass.QUESTION_WORD,
        )
        assert result["closer"] in narrow_pool


# ---------------------------------------------------------------------------
# HeadClass — filler mang nghĩa (tự dưng/kiểu/ấy) chỉ được rút khi
# head_class=STATEMENT (fix "tự dưng kiếm giúp tôi chỗ thành phố Đà Nẵng
# nhé" — filler mang nghĩa gắn vào câu mệnh lệnh, sai nghĩa).
# ---------------------------------------------------------------------------


def test_apply_variations_meaningful_filler_never_on_imperative(config, lexicon) -> None:
    cell = _cell()
    slots = {"opener": "", "entity": "x", "closer": ""}
    meaningful = {f.text for f in lexicon.fillers if HeadClass.STATEMENT in f.heads and HeadClass.IMPERATIVE not in f.heads}
    assert meaningful, "cần ít nhất 1 filler chỉ heads=[statement] để test có ý nghĩa"
    for seed in range(300):
        result = apply_variations(
            slots,
            cell,
            config,
            random.Random(seed),
            register=Register.CASUAL,
            lexicon=lexicon,
            head_class=HeadClass.IMPERATIVE,
        )
        for filler in meaningful:
            assert result["opener"] != filler
            assert not result["opener"].endswith(f" {filler}")


def test_apply_variations_meaningful_filler_can_appear_on_statement(config, lexicon) -> None:
    cell = _cell()
    slots = {"opener": "", "entity": "x", "closer": ""}
    meaningful = {f.text for f in lexicon.fillers if HeadClass.STATEMENT in f.heads and HeadClass.IMPERATIVE not in f.heads}
    seen = False
    for seed in range(600):
        result = apply_variations(
            slots,
            cell,
            config,
            random.Random(seed),
            register=Register.CASUAL,
            lexicon=lexicon,
            head_class=HeadClass.STATEMENT,
        )
        if result["opener"] in meaningful or any(result["opener"].endswith(f" {f}") for f in meaningful):
            seen = True
            break
    assert seen


def test_merge_filler_does_not_duplicate_opener_word() -> None:
    """'à' có mặt ở cả fillers lẫn openers.casual — nếu opener đã rút trúng
    đúng filler đó, không được nối thêm lần hai (fix 'à à đi Ngọc Hà...')."""
    from navtext.variation import _merge_filler

    assert _merge_filler("à", "à") == "à"
    assert _merge_filler("à cho hỏi", "à") == "à cho hỏi"
    assert _merge_filler("", "à") == "à"
    assert _merge_filler("cho hỏi", "tự dưng") == "cho hỏi tự dưng"


# ---------------------------------------------------------------------------
# {category_noun} — fill độc lập, luôn khác rỗng khi template khai báo slot.
# ---------------------------------------------------------------------------


def test_apply_variations_fills_category_noun_when_declared(config, lexicon) -> None:
    cell = _cell()
    slots = {"opener": "", "category_noun": "", "entity": "x", "closer": ""}
    all_nouns = {noun for nouns in lexicon.category_nouns.values() for noun in nouns}
    for seed in range(50):
        result = apply_variations(
            slots, cell, config, random.Random(seed), register=Register.CASUAL, lexicon=lexicon
        )
        assert result["category_noun"] in all_nouns
