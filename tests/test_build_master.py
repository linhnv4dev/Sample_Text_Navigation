"""Test cho navtext.build_master — xem docs/data-model.md#master-dataset-schema
và docs/sampling.md (reproducibility của master data feed vào seed cell).

Dùng fixture tí hon ở tests/fixtures/raw/, KHÔNG phụ thuộc snapshot OSM
thật để chạy nhanh và ổn định.
"""

from __future__ import annotations

import csv
import shutil
import unicodedata
from pathlib import Path

import pytest

from collections import Counter

from navtext.build_master import (
    BuildError,
    RawData,
    build_master,
    build_pois,
    clean_poi_name,
    is_usable_entity_name,
    normalize_name,
    read_raw,
    validate,
)

FIXTURE_RAW = Path(__file__).parent / "fixtures" / "raw"
FIXTURE_OSM_TAGS = Path(__file__).parent / "fixtures" / "config" / "osm_tags.yaml"
FIXTURE_RUN_CONFIG = Path(__file__).parent / "fixtures" / "config" / "run.yaml"


def _build(tmp_path: Path, raw_dir: Path = FIXTURE_RAW) -> Path:
    master_dir = tmp_path / "master"
    build_master(
        raw_dir=raw_dir,
        master_dir=master_dir,
        osm_tag_config_path=FIXTURE_OSM_TAGS,
        run_config_path=FIXTURE_RUN_CONFIG,
    )
    return master_dir


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_normalize_name_collapses_whitespace_and_strips() -> None:
    assert normalize_name("  Hồ   Hoàn   Kiếm  ") == "Hồ Hoàn Kiếm"


def test_normalize_name_converts_nfd_to_nfc() -> None:
    # NFD: ký tự có dấu tách thành base + combining mark riêng — cùng một
    # chữ nhưng khác byte sequence với dạng NFC. Nguồn dữ liệu ngoài (OSM,
    # file người khác soạn) không đảm bảo luôn ở NFC.
    nfd = unicodedata.normalize("NFD", "Đường Nguyễn Huệ")
    nfc = unicodedata.normalize("NFC", "Đường Nguyễn Huệ")
    assert nfd != nfc  # xác nhận fixture thật sự lệch dạng
    assert normalize_name(nfd) == nfc


def test_build_master_province_alias_strips_dash_to_spoken_form(tmp_path: Path) -> None:
    """"Bà Rịa - Vũng Tàu" (province_alias.csv thật) không ai đọc thành
    tiếng dấu "-" khi nói — build_provinces() phải chuẩn hoá về "Bà Rịa
    Vũng Tàu" ngay ở tầng build, để province_legacy audit và text hiển thị
    luôn khớp nhau (không xử lý ở tầng generate, xem
    _province_alias_spoken_form())."""
    raw_dashed = tmp_path / "raw_dashed_alias"
    shutil.copytree(FIXTURE_RAW, raw_dashed)
    provinces_yaml = raw_dashed / "admin" / "provinces.yaml"
    text = provinces_yaml.read_text(encoding="utf-8")
    text = text.replace(
        'name: "Tỉnh Hai Cũ A"', 'name: "Tỉnh Hai Cũ A - Phần B"'
    )
    provinces_yaml.write_text(text, encoding="utf-8")
    master_dir = _build(tmp_path, raw_dir=raw_dashed)
    aliases = _read_csv(master_dir / "province_alias.csv")
    names = {a["alias"] for a in aliases}
    assert "Tỉnh Hai Cũ A Phần B" in names
    assert "Tỉnh Hai Cũ A - Phần B" not in names


def test_build_master_writes_all_six_tables(tmp_path: Path) -> None:
    master_dir = _build(tmp_path)
    for filename in (
        "provinces.csv",
        "province_alias.csv",
        "districts.csv",
        "wards.csv",
        "streets.csv",
        "pois.csv",
    ):
        assert (master_dir / filename).exists()


def test_build_master_province_and_alias_counts(tmp_path: Path) -> None:
    master_dir = _build(tmp_path)
    provinces = _read_csv(master_dir / "provinces.csv")
    aliases = _read_csv(master_dir / "province_alias.csv")
    assert len(provinces) == 2
    legacy = [a for a in aliases if a["alias_type"] == "legacy"]
    assert len(legacy) == 3


def test_build_master_dedupes_street_segments_by_name(tmp_path: Path) -> None:
    """Fixture osm/01.json có 2 way (id=2, id=3) cùng name "Đường Test" —
    phải gộp thành 1 street entity, giữ id nhỏ hơn."""
    master_dir = _build(tmp_path)
    streets = _read_csv(master_dir / "streets.csv")
    assert len(streets) == 1
    assert streets[0]["name"] == "Đường Test"
    assert streets[0]["street_id"] == "osm_street_way_2"


def test_build_master_maps_poi_tag_to_taxonomy(tmp_path: Path) -> None:
    master_dir = _build(tmp_path)
    pois = _read_csv(master_dir / "pois.csv")
    assert len(pois) == 1
    assert pois[0]["category"] == "public_facility"
    assert pois[0]["subcategory"] == "hospital"
    assert pois[0]["province_id"] == "01"


def test_build_master_is_deterministic(tmp_path: Path) -> None:
    master_dir_a = _build(tmp_path / "run_a")
    master_dir_b = _build(tmp_path / "run_b")
    for filename in ("provinces.csv", "province_alias.csv", "streets.csv", "pois.csv"):
        assert (master_dir_a / filename).read_bytes() == (master_dir_b / filename).read_bytes()


def test_build_master_fk_violation_raises_and_writes_nothing(tmp_path: Path) -> None:
    broken_raw = tmp_path / "broken_raw"
    shutil.copytree(FIXTURE_RAW, broken_raw)
    with (broken_raw / "admin" / "wards.csv").open("a", encoding="utf-8") as f:
        f.write("bad_ward,nonexistent_district,Phường Ma,phường\n")

    master_dir = tmp_path / "master_should_not_exist"
    with pytest.raises(BuildError) as exc_info:
        build_master(
            raw_dir=broken_raw,
            master_dir=master_dir,
            osm_tag_config_path=FIXTURE_OSM_TAGS,
            run_config_path=FIXTURE_RUN_CONFIG,
        )
    assert any("nonexistent_district" in issue for issue in exc_info.value.issues)
    assert not master_dir.exists()


def test_build_master_district_legacy_alias_appears_in_output(tmp_path: Path) -> None:
    """legacy_alias hợp lệ (khớp đúng 1 legacy alias thật của tỉnh) phải
    xuất hiện nguyên vẹn ở cột districts.csv#legacy_alias — sub-region hint
    cho generator (xem docs/data-model.md#sub-region-hint)."""
    raw_with_hint = tmp_path / "raw_with_hint"
    shutil.copytree(FIXTURE_RAW, raw_with_hint)
    districts_csv = raw_with_hint / "admin" / "districts.csv"
    districts_csv.write_text(
        "district_id,province_id,name,type,status,legacy_alias\n"
        "d1,01,Quận Một,quận,abolished,\n"
        "d2,02,Quận Hai,quận,abolished,Tỉnh Hai Cũ A\n",
        encoding="utf-8",
    )
    master_dir = _build(tmp_path, raw_dir=raw_with_hint)
    districts = _read_csv(master_dir / "districts.csv")
    by_id = {d["district_id"]: d for d in districts}
    assert by_id["d1"]["legacy_alias"] == ""
    assert by_id["d2"]["legacy_alias"] == "Tỉnh Hai Cũ A"


def test_build_master_district_legacy_alias_mismatch_raises(tmp_path: Path) -> None:
    raw_bad_hint = tmp_path / "raw_bad_hint"
    shutil.copytree(FIXTURE_RAW, raw_bad_hint)
    districts_csv = raw_bad_hint / "admin" / "districts.csv"
    districts_csv.write_text(
        "district_id,province_id,name,type,status,legacy_alias\n"
        "d1,01,Quận Một,quận,abolished,\n"
        "d2,02,Quận Hai,quận,abolished,Tỉnh Không Tồn Tại\n",
        encoding="utf-8",
    )
    with pytest.raises(BuildError) as exc_info:
        build_master(
            raw_dir=raw_bad_hint,
            master_dir=tmp_path / "master_should_not_exist",
            osm_tag_config_path=FIXTURE_OSM_TAGS,
            run_config_path=FIXTURE_RUN_CONFIG,
        )
    assert any("legacy_alias" in issue and "d2" in issue for issue in exc_info.value.issues)


def test_build_master_ward_osm_legacy_alias_appears_in_output(tmp_path: Path) -> None:
    """Cùng cơ chế với district (test trên) nhưng cho ward nối THẲNG qua
    province_id (nguồn wards_osm.csv, mô hình 2 cấp — xem
    scripts/extract_wards.py)."""
    raw_with_hint = tmp_path / "raw_ward_hint"
    shutil.copytree(FIXTURE_RAW, raw_with_hint)
    (raw_with_hint / "admin" / "wards_osm.csv").write_text(
        "ward_id,district_id,province_id,name,type,legacy_alias\n"
        "osm_w1,,02,Xã Có Hint,xã,Tỉnh Hai Cũ A\n",
        encoding="utf-8",
    )
    master_dir = _build(tmp_path, raw_dir=raw_with_hint)
    wards = {w["ward_id"]: w for w in _read_csv(master_dir / "wards.csv")}
    assert wards["osm_w1"]["legacy_alias"] == "Tỉnh Hai Cũ A"
    assert wards["osm_w1"]["province_id"] == "02"


def test_build_master_ward_osm_legacy_alias_mismatch_raises(tmp_path: Path) -> None:
    raw_bad_hint = tmp_path / "raw_bad_ward_hint"
    shutil.copytree(FIXTURE_RAW, raw_bad_hint)
    (raw_bad_hint / "admin" / "wards_osm.csv").write_text(
        "ward_id,district_id,province_id,name,type,legacy_alias\n"
        "osm_w1,,02,Xã Sai Hint,xã,Tỉnh Không Tồn Tại\n",
        encoding="utf-8",
    )
    with pytest.raises(BuildError) as exc_info:
        build_master(
            raw_dir=raw_bad_hint,
            master_dir=tmp_path / "master_should_not_exist",
            osm_tag_config_path=FIXTURE_OSM_TAGS,
            run_config_path=FIXTURE_RUN_CONFIG,
        )
    assert any("legacy_alias" in issue and "osm_w1" in issue for issue in exc_info.value.issues)


def test_build_master_ward_can_attach_directly_to_province(tmp_path: Path) -> None:
    """Mô hình 2 cấp sau 1/7/2025: xã/phường thuộc THẲNG tỉnh, không qua
    quận/huyện — nguồn wards_osm.csv (scripts/extract_wards.py) ghi
    province_id và để district_id rỗng. Xem docs/data-model.md."""
    raw_two_level = tmp_path / "raw_two_level"
    shutil.copytree(FIXTURE_RAW, raw_two_level)
    (raw_two_level / "admin" / "wards_osm.csv").write_text(
        "ward_id,district_id,province_id,name,type\n"
        "osm_ward_1,,01,Xã Không Huyện,xã\n",
        encoding="utf-8",
    )
    master_dir = _build(tmp_path, raw_dir=raw_two_level)
    wards = {w["ward_id"]: w for w in _read_csv(master_dir / "wards.csv")}
    assert wards["osm_ward_1"]["province_id"] == "01"
    assert wards["osm_ward_1"]["district_id"] == ""
    # ward curate tay cũ vẫn đi qua district, không bị ép phải có province_id
    assert wards["w1"]["district_id"] == "d1"
    assert wards["w1"]["province_id"] == ""


def test_build_master_ward_without_any_fk_raises(tmp_path: Path) -> None:
    raw_orphan = tmp_path / "raw_orphan_ward"
    shutil.copytree(FIXTURE_RAW, raw_orphan)
    (raw_orphan / "admin" / "wards_osm.csv").write_text(
        "ward_id,district_id,province_id,name,type\n"
        "osm_ward_orphan,,,Xã Mồ Côi,xã\n",
        encoding="utf-8",
    )
    with pytest.raises(BuildError) as exc_info:
        build_master(
            raw_dir=raw_orphan,
            master_dir=tmp_path / "master_should_not_exist",
            osm_tag_config_path=FIXTURE_OSM_TAGS,
            run_config_path=FIXTURE_RUN_CONFIG,
        )
    assert any("không có FK nào" in issue for issue in exc_info.value.issues)


def test_build_master_ward_unknown_province_raises(tmp_path: Path) -> None:
    raw_bad = tmp_path / "raw_bad_ward_province"
    shutil.copytree(FIXTURE_RAW, raw_bad)
    (raw_bad / "admin" / "wards_osm.csv").write_text(
        "ward_id,district_id,province_id,name,type\n"
        "osm_ward_bad,,99,Xã Tỉnh Ma,xã\n",
        encoding="utf-8",
    )
    with pytest.raises(BuildError) as exc_info:
        build_master(
            raw_dir=raw_bad,
            master_dir=tmp_path / "master_should_not_exist",
            osm_tag_config_path=FIXTURE_OSM_TAGS,
            run_config_path=FIXTURE_RUN_CONFIG,
        )
    assert any("province_id lạ" in issue and "osm_ward_bad" in issue for issue in exc_info.value.issues)


def test_build_master_wrong_province_count_raises(tmp_path: Path) -> None:
    """run.yaml kỳ vọng 2 tỉnh; nếu raw chỉ có 1, phải fail loud thay vì
    build_master âm thầm ghi một dataset thiếu tỉnh."""
    broken_raw = tmp_path / "one_province_raw"
    shutil.copytree(FIXTURE_RAW, broken_raw)
    provinces_yaml = broken_raw / "admin" / "provinces.yaml"
    lines = provinces_yaml.read_text(encoding="utf-8").splitlines(keepends=True)
    # Giữ lại chỉ block tỉnh đầu tiên (tới trước dòng "- province_id: \"02\"").
    cutoff = next(i for i, line in enumerate(lines) if line.startswith('- province_id: "02"'))
    provinces_yaml.write_text("".join(lines[:cutoff]), encoding="utf-8")

    with pytest.raises(BuildError) as exc_info:
        build_master(
            raw_dir=broken_raw,
            master_dir=tmp_path / "master_should_not_exist",
            osm_tag_config_path=FIXTURE_OSM_TAGS,
            run_config_path=FIXTURE_RUN_CONFIG,
        )
    assert any("kỳ vọng 2" in issue for issue in exc_info.value.issues)


# ---------------------------------------------------------------------------
# clean_poi_name — fix "Khách Sạn Sea Castle Hotel Sea Castle Hotel": OSM
# name tag nhồi nhiều tên bằng \n hoặc ";", và đôi khi tự lặp cụm liền kề.
# ---------------------------------------------------------------------------


def test_clean_poi_name_cuts_at_newline() -> None:
    raw = "Khách Sạn Sea Castle Hotel\nSea Castle Hotel"
    assert clean_poi_name(raw) == "Khách Sạn Sea Castle Hotel"


def test_clean_poi_name_cuts_at_semicolon() -> None:
    assert clean_poi_name("Hải sản Thiên Ngọc;Bún đậu Phố Cổ") == "Hải sản Thiên Ngọc"


def test_clean_poi_name_removes_adjacent_repeated_phrase_without_separator() -> None:
    """Tên gốc có thể tự lặp cụm liền kề mà KHÔNG có dấu phân tách — case
    thật trong data/raw/osm/04.json."""
    assert clean_poi_name("Khách Sạn Sea Castle Hotel Sea Castle Hotel") == "Khách Sạn Sea Castle Hotel"


def test_clean_poi_name_removes_repeated_single_word() -> None:
    assert clean_poi_name("Chợ Chợ Bến Thành") == "Chợ Bến Thành"


def test_clean_poi_name_collapses_odd_count_repeats_to_single_token() -> None:
    """Tên thật từ data/raw/osm/27.json (quán nhậu ở Lâm Đồng) — MỘT lượt
    dedupe cửa sổ trượt chỉ gộp được cặp đầu ("Nhậu Nhậu Nhậu" -> "Nhậu
    Nhậu", còn dư 1 token lẻ chưa gộp), phải lặp tới điểm bất động mới ra
    "Nhậu". Bug thật, bắt được bởi build_master.validate() khi build-master
    chạy trên dữ liệu .pbf toàn quốc (không xuất hiện ở fixture nhỏ trước
    đó)."""
    assert clean_poi_name("Nhậu Nhậu Nhậu") == "Nhậu"
    assert clean_poi_name("Nhậu Nhậu Nhậu Nhậu Nhậu") == "Nhậu"


def test_clean_poi_name_is_idempotent() -> None:
    """clean_poi_name(clean_poi_name(x)) == clean_poi_name(x) — build_master
    validate() dựa vào tính chất này để phát hiện tên chưa sạch trong
    pois.csv (xem hard rule #7: data/master/ là build artifact)."""
    cases = [
        "Khách Sạn Sea Castle Hotel\nSea Castle Hotel",
        "Hải sản Thiên Ngọc;Bún đậu Phố Cổ",
        "Khách Sạn Sea Castle Hotel Sea Castle Hotel",
        "Chợ Bến Thành",
        "Nhà thuốc Long Châu",
        "Nhậu Nhậu Nhậu",
        "Nhậu Nhậu Nhậu Nhậu Nhậu",
        "Bia Hơi & Nước Mía",
        "Tp.Đà Nẵng",
        "Trường Tiểu học Tam Trinh (điểm 2)",
        "Văn Miếu - Quốc Tử Giám",
        "Văn Miếu – Quốc Tử Giám",
        "El Gaucho | Saigon Pearl",
        "Restaurant_TrungTamHoiNghi",
    ]
    for raw in cases:
        once = clean_poi_name(raw)
        assert clean_poi_name(once) == once


def test_clean_poi_name_expands_ampersand_to_va() -> None:
    assert clean_poi_name("Bia Hơi & Nước Mía") == "Bia Hơi và Nước Mía"


def test_clean_poi_name_expands_tp_dot_prefix() -> None:
    """Tên OSM thật dính liền "Tp." không có khoảng trắng sau — case đã gặp
    thực tế: "Benh Vien Y Hoc Co Truyen Tp.Da Nang"."""
    assert clean_poi_name("Tp.Đà Nẵng") == "Thành phố Đà Nẵng"
    assert clean_poi_name("TP.HCM") == "Thành phố HCM"


def test_clean_poi_name_removes_parens_keeps_content() -> None:
    """Bỏ dấu ngoặc (markup, không ai đọc thành tiếng) nhưng GIỮ nội dung
    bên trong ("cơ sở 2" vẫn là thông tin hữu ích khi nói) — đồng thời fix
    bug "dấu ) bị nuốt" lúc verbalize số (numbers.py không xử lý đúng token
    dính liền số+ngoặc kiểu "2)")."""
    assert clean_poi_name("Trường Tiểu học Tam Trinh (điểm 2)") == "Trường Tiểu học Tam Trinh điểm 2"


def test_clean_poi_name_strips_hyphens_and_dashes() -> None:
    assert clean_poi_name("Văn Miếu - Quốc Tử Giám") == "Văn Miếu Quốc Tử Giám"
    assert clean_poi_name("Văn Miếu – Quốc Tử Giám") == "Văn Miếu Quốc Tử Giám"


def test_clean_poi_name_strips_noise_punctuation() -> None:
    assert clean_poi_name("El Gaucho | Saigon Pearl") == "El Gaucho Saigon Pearl"
    assert clean_poi_name("Restaurant_TrungTamHoiNghi") == "Restaurant TrungTamHoiNghi"


# ---------------------------------------------------------------------------
# is_usable_entity_name — loại tên rác/chung chung/không dấu trước khi ghi
# vào pois.csv/streets.csv (xem docs/generation.md, plan sample-text-sát-thực-tế).
# ---------------------------------------------------------------------------


def test_is_usable_entity_name_rejects_generic_names() -> None:
    for name in ("Công viên", "Trạm xăng", "Chợ", "Nhà hàng", "công viên"):
        assert not is_usable_entity_name(name), name


def test_is_usable_entity_name_rejects_short_junk() -> None:
    for name in ("d", "MX", "345"):
        assert not is_usable_entity_name(name), name


def test_is_usable_entity_name_allows_short_bank_brand_codes() -> None:
    for name in ("GO!", "OCB", "VIB"):
        assert is_usable_entity_name(name), name


def test_is_usable_entity_name_rejects_unaccented_vietnamese() -> None:
    for name in ("Benh Vien Y Hoc Co Truyen", "Khach San Misa", "Nha Nghi Boi Nhi"):
        assert not is_usable_entity_name(name), name


def test_is_usable_entity_name_allows_latin_brand_names() -> None:
    """Thương hiệu Latin thật (không có dạng có dấu để mất) phải được giữ
    lại — khác tên tiếng Việt bị gõ thiếu dấu."""
    for name in ("Techcombank", "WinMart", "Kichi-Kichi", "BIDV", "Petrolimex"):
        assert is_usable_entity_name(name), name


def test_is_usable_entity_name_allows_ordinary_vietnamese_names() -> None:
    for name in ("Nhà thuốc Long Châu", "Bến xe Mỹ Đình", "Chợ Bến Thành"):
        assert is_usable_entity_name(name), name


def test_clean_poi_name_leaves_ordinary_names_untouched() -> None:
    """Không được đụng vào tên hợp lệ — vd tên POI tự nhiên chứa 1 từ lặp
    không liền kề (không phải cụm lặp) vẫn phải giữ nguyên."""
    assert clean_poi_name("Nhà thuốc Long Châu") == "Nhà thuốc Long Châu"
    assert clean_poi_name("Bến xe Mỹ Đình") == "Bến xe Mỹ Đình"


def test_clean_poi_name_normalizes_whitespace_and_nfc() -> None:
    nfd = unicodedata.normalize("NFD", "Khách   Sạn  Hoàng Anh")
    assert clean_poi_name(nfd) == unicodedata.normalize("NFC", "Khách Sạn Hoàng Anh")


def test_validate_detects_duplicate_legacy_alias_across_provinces() -> None:
    provinces = [
        {"province_id": "01"},
        {"province_id": "02"},
    ]
    aliases = [
        {"province_id": "01", "alias": "Trùng", "alias_type": "legacy", "admin_prefix": "tỉnh"},
        {"province_id": "02", "alias": "Trùng", "alias_type": "legacy", "admin_prefix": "tỉnh"},
    ]
    issues = validate(
        provinces=provinces,
        aliases=aliases,
        districts=[],
        wards=[],
        streets=[],
        pois=[],
        admin_invariants={"expected_province_count": 2, "expected_legacy_alias_count": 2},
    )
    assert any("trùng lặp xuyên tỉnh" in issue for issue in issues)


# ---------------------------------------------------------------------------
# landmarks.yaml — nguồn curate tay bổ sung (source=manual), xem
# SOURCES.md#landmarksyaml và docs/data-model.md.
# ---------------------------------------------------------------------------


def test_build_pois_loads_manual_landmarks() -> None:
    raw = RawData(
        provinces=[],
        districts=[],
        wards=[],
        osm_by_province={},
        landmarks=[
            {
                "province_id": "01",
                "landmarks": [
                    {"name": "Hồ Hoàn Kiếm", "subcategory": "lake", "fame_tier": 1},
                ],
            }
        ],
    )
    rows, issues = build_pois(raw, {"poi_tags": []}, [], Counter())
    assert not issues
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "Hồ Hoàn Kiếm"
    assert row["category"] == "landmark"
    assert row["subcategory"] == "lake"
    assert row["province_id"] == "01"
    assert row["popularity_tier"] == "1"
    assert row["source"] == "manual"
    assert row["poi_id"] == "manual_01_ho_hoan_kiem"


def test_build_pois_manual_landmark_wins_dedupe_over_osm() -> None:
    """OSM và landmarks.yaml cùng có một tên trong cùng tỉnh -> giữ bản
    manual (mang popularity_tier + category đúng), loại bản OSM."""
    raw = RawData(
        provinces=[],
        districts=[],
        wards=[],
        osm_by_province={
            "01": [
                {
                    "type": "node",
                    "id": 1,
                    "tags": {"amenity": "marketplace", "name": "Chợ Bến Thành"},
                }
            ]
        },
        landmarks=[
            {
                "province_id": "01",
                "landmarks": [
                    {"name": "Chợ Bến Thành", "subcategory": "market", "fame_tier": 1},
                ],
            }
        ],
    )
    osm_tags = {
        "poi_tags": [
            {"osm_tag": "amenity=marketplace", "category": "landmark", "subcategory": "market"},
        ]
    }
    rows, issues = build_pois(raw, osm_tags, [], Counter())
    assert not issues
    matching = [r for r in rows if r["name"] == "Chợ Bến Thành"]
    assert len(matching) == 1
    assert matching[0]["source"] == "manual"
    assert matching[0]["popularity_tier"] == "1"


def test_build_pois_rejects_invalid_landmark_subcategory() -> None:
    raw = RawData(
        provinces=[],
        districts=[],
        wards=[],
        osm_by_province={},
        landmarks=[
            {
                "province_id": "01",
                "landmarks": [
                    {"name": "Chỗ Lạ", "subcategory": "not_a_real_subcategory", "fame_tier": 1},
                ],
            }
        ],
    )
    rows, issues = build_pois(raw, {"poi_tags": []}, [], Counter())
    assert not rows
    assert any("không hợp lệ" in issue for issue in issues)


def test_build_pois_rejects_missing_required_field() -> None:
    raw = RawData(
        provinces=[],
        districts=[],
        wards=[],
        osm_by_province={},
        landmarks=[{"province_id": "01", "landmarks": [{"name": "Thiếu Tier", "subcategory": "lake"}]}],
    )
    rows, issues = build_pois(raw, {"poi_tags": []}, [], Counter())
    assert not rows
    assert any("thiếu field bắt buộc" in issue for issue in issues)


def test_build_pois_landmark_legacy_alias_valid() -> None:
    raw = RawData(
        provinces=[],
        districts=[],
        wards=[],
        osm_by_province={},
        landmarks=[
            {
                "province_id": "02",
                "landmarks": [
                    {
                        "name": "Núi Lạ",
                        "subcategory": "mountain",
                        "fame_tier": 1,
                        "legacy_alias": "Tỉnh Hai Cũ A",
                    },
                ],
            }
        ],
    )
    aliases = [
        {"province_id": "02", "alias": "Tỉnh Hai Cũ A", "alias_type": "legacy", "admin_prefix": "tỉnh"},
        {"province_id": "02", "alias": "Tỉnh Hai Cũ B", "alias_type": "legacy", "admin_prefix": "tỉnh"},
    ]
    rows, issues = build_pois(raw, {"poi_tags": []}, aliases, Counter())
    assert not issues
    assert rows[0]["legacy_alias_hint"] == "Tỉnh Hai Cũ A"


def test_build_pois_landmark_legacy_alias_mismatch_rejected() -> None:
    raw = RawData(
        provinces=[],
        districts=[],
        wards=[],
        osm_by_province={},
        landmarks=[
            {
                "province_id": "02",
                "landmarks": [
                    {
                        "name": "Núi Lạ",
                        "subcategory": "mountain",
                        "fame_tier": 1,
                        "legacy_alias": "Không Tồn Tại",
                    },
                ],
            }
        ],
    )
    aliases = [
        {"province_id": "02", "alias": "Tỉnh Hai Cũ A", "alias_type": "legacy", "admin_prefix": "tỉnh"},
    ]
    rows, issues = build_pois(raw, {"poi_tags": []}, aliases, Counter())
    assert any("legacy_alias" in issue and "Núi Lạ" in issue for issue in issues)


def test_read_raw_landmarks_yaml_optional() -> None:
    """Fixture tests/fixtures/raw/ không có landmarks.yaml -> read_raw()
    không raise, trả list rỗng (build_master vẫn chạy được như trước khi có
    tính năng này)."""
    raw = read_raw(FIXTURE_RAW)
    assert raw.landmarks == []


def test_build_master_end_to_end_with_landmarks(tmp_path: Path) -> None:
    """build_master() thật, đi qua landmarks.yaml, ghi ra pois.csv."""
    raw_with_landmarks = tmp_path / "raw_with_landmarks"
    shutil.copytree(FIXTURE_RAW, raw_with_landmarks)
    (raw_with_landmarks / "admin" / "landmarks.yaml").write_text(
        "- province_id: \"01\"\n"
        "  landmarks:\n"
        "    - {name: \"Núi Lạ\", subcategory: mountain, fame_tier: 1}\n",
        encoding="utf-8",
    )
    master_dir = _build(tmp_path, raw_dir=raw_with_landmarks)
    pois = _read_csv(master_dir / "pois.csv")
    manual = [p for p in pois if p["source"] == "manual"]
    assert len(manual) == 1
    assert manual[0]["name"] == "Núi Lạ"
    assert manual[0]["category"] == "landmark"


def test_build_master_raises_when_province_below_min_landmarks(tmp_path: Path) -> None:
    raw_thin = tmp_path / "raw_thin_landmarks"
    shutil.copytree(FIXTURE_RAW, raw_thin)
    # Chỉ tỉnh "01" có landmark, tỉnh "02" (có trong fixture provinces.yaml)
    # không có mục nào -> phải fail vì dưới min_landmarks_per_province.
    (raw_thin / "admin" / "landmarks.yaml").write_text(
        "- province_id: \"01\"\n"
        "  landmarks:\n"
        "    - {name: \"Núi Lạ\", subcategory: mountain, fame_tier: 1}\n",
        encoding="utf-8",
    )
    run_config_with_min = tmp_path / "run_with_min.yaml"
    run_config_with_min.write_text(
        "global_seed: 1\n"
        "admin_invariants:\n"
        "  expected_province_count: 2\n"
        "  expected_legacy_alias_count: 3\n"
        "  min_landmarks_per_province: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(BuildError) as exc_info:
        build_master(
            raw_dir=raw_thin,
            master_dir=tmp_path / "master_should_not_exist",
            osm_tag_config_path=FIXTURE_OSM_TAGS,
            run_config_path=run_config_with_min,
        )
    assert any("landmark manual" in issue for issue in exc_info.value.issues)
