"""Test bất biến hard rule #2 ở mức DỮ LIỆU THẬT (data/raw/admin/), không
phải fixture — nếu ai đó sửa provinces.yaml tay và làm lệch 34/63, test này
phải đỏ trước khi build_master chạy.

Xem CLAUDE.md hard rule #2 và docs/data-model.md#mô-hình-34--63--cách-gọi-phổ-biến.
"""

from __future__ import annotations

from pathlib import Path

import yaml

PROVINCES_PATH = Path(__file__).parent.parent / "data" / "raw" / "admin" / "provinces.yaml"
LANDMARKS_PATH = Path(__file__).parent.parent / "data" / "raw" / "admin" / "landmarks.yaml"


def _load_provinces() -> list[dict]:
    with PROVINCES_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_landmarks() -> list[dict]:
    with LANDMARKS_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_exactly_34_provinces() -> None:
    provinces = _load_provinces()
    assert len(provinces) == 34


def test_province_ids_are_unique() -> None:
    provinces = _load_provinces()
    ids = [p["province_id"] for p in provinces]
    assert len(set(ids)) == len(ids)


def test_exactly_63_unique_legacy_aliases() -> None:
    provinces = _load_provinces()
    legacy: list[str] = []
    for p in provinces:
        legacy.extend(a["name"] for a in p["aliases"]["legacy"])
    assert len(legacy) == 63
    assert len(set(legacy)) == 63, "có legacy alias trùng lặp xuyên tỉnh — vi phạm hard rule #2"


def test_every_legacy_alias_maps_to_exactly_one_province() -> None:
    """Bản chất là test_exactly_63_unique_legacy_aliases nhìn từ góc khác:
    build một map ngược alias -> province_id và xác nhận không key nào bị
    ghi đè bởi tỉnh khác."""
    provinces = _load_provinces()
    owner: dict[str, str] = {}
    for p in provinces:
        for alias in p["aliases"]["legacy"]:
            name = alias["name"]
            assert name not in owner, (
                f"legacy alias {name!r} thuộc cả {owner.get(name)!r} và {p['province_id']!r}"
            )
            owner[name] = p["province_id"]


def test_every_legacy_alias_has_valid_admin_prefix() -> None:
    """A: admin_prefix sống trên CHÍNH alias, không suy từ is_municipality
    của tỉnh hiện hành — chỉ 5 tên legacy từng là "thành phố trực thuộc
    trung ương" trong hệ 63 tỉnh (2008–2025): Hà Nội, Hồ Chí Minh, Hải
    Phòng, Đà Nẵng, Cần Thơ.
    """
    provinces = _load_provinces()
    expected_municipality_legacy = {"Hà Nội", "Hồ Chí Minh", "Hải Phòng", "Đà Nẵng", "Cần Thơ"}
    seen_municipality_legacy: set[str] = set()
    for p in provinces:
        for alias_type in ("legacy", "spoken", "abbrev_expanded"):
            for alias in p["aliases"][alias_type]:
                assert alias["admin_prefix"] in ("tỉnh", "thành phố", ""), (
                    f"alias {alias['name']!r} ({alias_type}) có admin_prefix "
                    f"{alias['admin_prefix']!r} không hợp lệ"
                )
                if alias_type == "legacy" and alias["admin_prefix"] == "thành phố":
                    seen_municipality_legacy.add(alias["name"])
    assert seen_municipality_legacy == expected_municipality_legacy


def test_every_province_has_required_fields() -> None:
    required = {
        "province_id",
        "name_current",
        "admin_status",
        "region",
        "is_municipality",
        "popularity_tier",
        "aliases",
    }
    for p in _load_provinces():
        assert required.issubset(p.keys()), f"tỉnh {p.get('province_id')} thiếu field"
        assert p["admin_status"] in {"unchanged", "merged"}
        assert 1 <= p["popularity_tier"] <= 3


def test_no_alias_type_missing_key() -> None:
    for p in _load_provinces():
        aliases = p["aliases"]
        assert set(aliases.keys()) == {"legacy", "spoken", "abbrev_expanded"}


# ---------------------------------------------------------------------------
# landmarks.yaml — nguồn curate tay bù cho lỗ hổng "thiếu địa danh nổi
# tiếng" (0 landmark manual ở 31/34 tỉnh trước khi có file này). Xem
# SOURCES.md#landmarksyaml.
# ---------------------------------------------------------------------------

_VALID_LANDMARK_SUBCATEGORIES = {
    "lake", "bridge", "monument", "heritage_site", "market",
    "mountain", "beach", "museum", "tourist_attraction",
}


def test_landmarks_cover_all_34_provinces() -> None:
    provinces = {p["province_id"] for p in _load_provinces()}
    landmark_provinces = {e["province_id"] for e in _load_landmarks()}
    missing = provinces - landmark_provinces
    assert not missing, f"tỉnh chưa có landmark nào trong landmarks.yaml: {sorted(missing)}"


def test_landmarks_only_reference_real_provinces() -> None:
    provinces = {p["province_id"] for p in _load_provinces()}
    for entry in _load_landmarks():
        assert entry["province_id"] in provinces, (
            f"landmarks.yaml trỏ tới province_id lạ {entry['province_id']!r}"
        )


def test_landmarks_have_valid_subcategory_and_fame_tier() -> None:
    for entry in _load_landmarks():
        for lm in entry["landmarks"]:
            assert lm["subcategory"] in _VALID_LANDMARK_SUBCATEGORIES, (
                f"tỉnh {entry['province_id']!r} landmark {lm['name']!r} có subcategory "
                f"{lm['subcategory']!r} không hợp lệ"
            )
            assert lm["fame_tier"] in (1, 2, 3)


def test_landmarks_no_duplicate_name_within_same_province() -> None:
    for entry in _load_landmarks():
        names = [lm["name"] for lm in entry["landmarks"]]
        assert len(names) == len(set(names)), (
            f"tỉnh {entry['province_id']!r} có tên landmark trùng lặp: {names}"
        )


def test_landmarks_legacy_alias_matches_real_alias_of_its_province() -> None:
    """legacy_alias (optional, sub-region hint — xem docs/data-model.md)
    khi có phải khớp đúng 1 trong các aliases.legacy.name của CHÍNH tỉnh đó
    SAU khi build_provinces() chuẩn hoá (vd bỏ dấu "-" nối 2 vế tên ghép,
    xem build_master._province_alias_spoken_form) — nếu không,
    resolve_province() sẽ không bao giờ dùng được hint này (silent fallback
    về pool đầy đủ, mất tác dụng). So khớp ở dạng ĐÃ chuẩn hoá vì đó chính
    là dạng thật sự nằm trong data/master/province_alias.csv lúc build."""
    from navtext.build_master import _province_alias_spoken_form

    provinces = _load_provinces()
    legacy_by_province = {
        p["province_id"]: {_province_alias_spoken_form(a["name"]) for a in p["aliases"]["legacy"]}
        for p in provinces
    }
    for entry in _load_landmarks():
        province_id = entry["province_id"]
        for lm in entry["landmarks"]:
            legacy_alias = lm.get("legacy_alias")
            if not legacy_alias:
                continue
            valid = legacy_by_province.get(province_id, set())
            assert legacy_alias in valid, (
                f"tỉnh {province_id!r} landmark {lm['name']!r} có legacy_alias "
                f"{legacy_alias!r} không khớp legacy alias nào của tỉnh đó ({sorted(valid)})"
            )


def test_landmarks_no_empty_name() -> None:
    """Chữ số trong tên gốc (vd "Landmark 81") là dữ liệu hợp lệ ở tầng
    raw — verbalize_entity_name() đọc số lúc generate (numbers.py), giống
    hệt tên đường/POI khác. Hard rule #1 chỉ áp cho cột `text` cuối cùng,
    không áp cho nguồn thô (xem build_master.normalize_name())."""
    for entry in _load_landmarks():
        for lm in entry["landmarks"]:
            assert lm["name"].strip(), f"tỉnh {entry['province_id']!r} có landmark tên rỗng"
