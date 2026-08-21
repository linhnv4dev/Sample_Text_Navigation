"""Phase 2: data/raw/ -> data/master/*.csv.

Transform thuần offline: đọc raw (admin YAML/CSV curate tay + OSM JSON
snapshot đã fetch trước bởi scripts/fetch_osm.py), normalize, resolve alias,
validate FK hierarchy + bất biến 34/63, rồi ghi CSV deterministic.

KHÔNG BAO GIỜ gọi network ở đây — snapshot OSM phải tồn tại sẵn trong
data/raw/osm/*.json (xem scripts/fetch_osm.py). Đây là ranh giới cứng giữa
"ingest có network, chạy tay" và "build, thuần offline, chạy trong CI".

Không có side-effect ở import time (CLAUDE.md#code-conventions).
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from navtext.schema import SUBCATEGORIES, Category

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BuildError(Exception):
    """Raise với TOÀN BỘ danh sách violation cùng lúc — không raise ở
    violation đầu tiên rồi để người dùng sửa từng cái một lần build.
    """

    def __init__(self, issues: list[str]) -> None:
        self.issues = issues
        super().__init__("\n".join(f"- {i}" for i in issues))


# ---------------------------------------------------------------------------
# Raw data containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RawData:
    provinces: list[dict[str, Any]]
    districts: list[dict[str, str]]
    wards: list[dict[str, str]]
    osm_by_province: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    osm_bbox_by_province: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)
    landmarks: list[dict[str, Any]] = field(default_factory=list)


def read_raw(raw_dir: Path) -> RawData:
    admin_dir = raw_dir / "admin"
    osm_dir = raw_dir / "osm"

    with (admin_dir / "provinces.yaml").open(encoding="utf-8") as f:
        provinces = yaml.safe_load(f) or []

    districts = _read_csv_rows(admin_dir / "districts.csv")
    # wards.csv (curate tay, gắn với district cũ) + wards_osm.csv (trích từ
    # .pbf bởi scripts/extract_wards.py, gắn THẲNG với tỉnh theo mô hình 2
    # cấp sau 1/7/2025). Hai nguồn cùng đổ vào một bảng wards — xem
    # SOURCES.md#wards_osmcsv.
    wards = _read_csv_rows(admin_dir / "wards.csv") + _read_csv_rows(admin_dir / "wards_osm.csv")

    osm_by_province: dict[str, list[dict[str, Any]]] = {}
    osm_bbox_by_province: dict[str, tuple[float, float, float, float]] = {}
    if osm_dir.exists():
        for snapshot_path in sorted(osm_dir.glob("*.json")):
            with snapshot_path.open(encoding="utf-8") as f:
                snapshot = json.load(f)
            province_id = snapshot["province_id"]
            osm_by_province[province_id] = snapshot["elements"]
            bbox = snapshot.get("bbox")
            if bbox and len(bbox) == 4:
                osm_bbox_by_province[province_id] = tuple(bbox)

    # landmarks.yaml là nguồn curate tay bổ sung (source=manual) — tuỳ chọn
    # (fixture test không có file này, và bản thân master data cũ trước khi
    # curate cũng không có) nên đọc mềm dẻo, không bắt buộc tồn tại.
    landmarks_path = admin_dir / "landmarks.yaml"
    landmarks: list[dict[str, Any]] = []
    if landmarks_path.exists():
        with landmarks_path.open(encoding="utf-8") as f:
            landmarks = yaml.safe_load(f) or []

    return RawData(
        provinces=provinces,
        districts=districts,
        wards=wards,
        osm_by_province=osm_by_province,
        osm_bbox_by_province=osm_bbox_by_province,
        landmarks=landmarks,
    )


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _element_coord(element: dict[str, Any]) -> tuple[float, float] | None:
    if "lat" in element and "lon" in element:
        return element["lat"], element["lon"]
    center = element.get("center")
    if center:
        return center["lat"], center["lon"]
    return None


def _bbox_center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    south, west, north, east = bbox
    return (south + north) / 2, (west + east) / 2


def _distance_sq(a: tuple[float, float], b: tuple[float, float]) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def dedupe_cross_province_osm_elements(
    osm_by_province: dict[str, list[dict[str, Any]]],
    bbox_by_province: dict[str, tuple[float, float, float, float]],
    counter: Counter[str],
) -> dict[str, list[dict[str, Any]]]:
    """Bbox tỉnh (hình chữ nhật, từ Nominatim — xem scripts/fetch_osm.py)
    chỉ là XẤP XỈ ranh giới hành chính thật (đa giác bất kỳ). Với 34 tỉnh
    liền kề phủ toàn quốc, bbox hàng xóm CHẮC CHẮN overlap ở vùng biên,
    khiến Overpass trả cùng một OSM element (node/way) trong nhiều snapshot
    tỉnh khác nhau. poi_id/street_id derive thuần từ osm ref (không kèm
    province) — không dedupe thì trùng id xuyên tỉnh (BuildError), và tệ
    hơn nếu không phát hiện: cùng một địa danh thật bị đếm ở cả hai tỉnh.

    Mỗi element ref chỉ được giữ ở ĐÚNG MỘT tỉnh — tỉnh có bbox center gần
    toạ độ element nhất, coi là proxy hợp lý cho "tỉnh nào thực sự chứa
    nó" khi không có ranh giới đa giác thật để point-in-polygon.
    """
    occurrences_by_ref: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for province_id, elements in osm_by_province.items():
        for el in elements:
            ref = f"{el['type']}/{el['id']}"
            occurrences_by_ref.setdefault(ref, []).append((province_id, el))

    keep_province_by_ref: dict[str, str] = {}
    for ref, occurrences in occurrences_by_ref.items():
        if len(occurrences) == 1:
            keep_province_by_ref[ref] = occurrences[0][0]
            continue

        counter["osm.cross_province_duplicate"] += len(occurrences) - 1
        coord = _element_coord(occurrences[0][1])
        if coord is None:
            # Không có toạ độ để so khoảng cách (hiếm) — chọn tỉnh đầu
            # tiên theo thứ tự deterministic (province_id) thay vì crash.
            keep_province_by_ref[ref] = min(p for p, _ in occurrences)
            continue

        def _dist_to(item: tuple[str, dict[str, Any]]) -> float:
            province_id = item[0]
            if province_id not in bbox_by_province:
                return float("inf")
            return _distance_sq(coord, _bbox_center(bbox_by_province[province_id]))

        keep_province_by_ref[ref] = min(occurrences, key=_dist_to)[0]

    result: dict[str, list[dict[str, Any]]] = {}
    for province_id, elements in osm_by_province.items():
        result[province_id] = [
            el for el in elements if keep_province_by_ref[f"{el['type']}/{el['id']}"] == province_id
        ]
    return result


# ---------------------------------------------------------------------------
# Normalize
# ---------------------------------------------------------------------------


def normalize_name(raw: str) -> str:
    """NFC + collapse whitespace + strip. KHÔNG expand viết tắt ở đây —
    expand_abbrev() thuộc numbers.py, chạy ở generation time (phase 6), vì
    nó cần biết ngữ cảnh câu để verbalize đúng (xem docs/generation.md).
    Tương tự, tên gốc có thể còn lẫn chữ số (vd "Bến xe Mỹ Đình 2") — đó là
    dữ liệu hợp lệ ở tầng master; verbalize số khi fill vào template là việc
    của generator.py + numbers.py, không phải build_master.
    """
    return " ".join(unicodedata.normalize("NFC", raw).split())


def _dedupe_adjacent_repeats_pass(tokens: list[str]) -> list[str]:
    """Một lượt gỡ cụm từ liền kề lặp nguyên khối, thử mọi kích thước cửa sổ
    k từ lớn xuống nhỏ. MỘT lượt không đủ triệt tiêu chuỗi lặp ≥3 lần (vd
    "Nhậu Nhậu Nhậu": lượt đầu chỉ gộp được cặp đầu thành "Nhậu Nhậu", còn
    dư 1 token lẻ) — clean_poi_name() gọi lặp hàm này tới điểm bất động."""
    n = len(tokens)
    for k in range(n // 2, 0, -1):
        i = 0
        deduped: list[str] = []
        while i < n:
            if (
                i + 2 * k <= n
                and [t.casefold() for t in tokens[i : i + k]]
                == [t.casefold() for t in tokens[i + k : i + 2 * k]]
            ):
                deduped.extend(tokens[i : i + k])
                i += 2 * k
            else:
                deduped.append(tokens[i])
                i += 1
        tokens = deduped
        n = len(tokens)
    return tokens


# Ký tự nhiễu OSM không mang nghĩa nói — không ai đọc thành tiếng "gạch dưới",
# "dấu chấm giữa", "ống", "chấm than" khi nói tên địa điểm. Thay bằng space
# (không xoá trần) để không dính 2 token liền kề vào nhau. Gồm cả gạch nối
# ASCII "-" lẫn en-dash "–"/em-dash "—" (OSM/wards_osm.csv lẫn cả hai, vd
# tên phường gộp thật "Văn Miếu – Quốc Tử Giám").
_NOISE_CHARS = re.compile(r"[|_·!'\"\-–—]")

# "(...)" là markup CHÚ THÍCH, không phải nội dung được nói ra nguyên vẹn
# kèm dấu ngoặc — bỏ CHÍNH DẤU NGOẶC (giữ lại chữ bên trong, vd "cơ sở 2" vẫn
# là thông tin hữu ích khi nói), thay bằng space. Đây cũng là fix cho bug
# "dấu ) bị nuốt": verbalize_entity_name() (numbers.py) không xử lý đúng
# token dính liền số+ngoặc kiểu "2)", để ngoặc lọt qua tới generate time thì
# output cụt mất vế đóng — loại bỏ ngay ở build time triệt để hơn.
_PAREN_CHARS = re.compile(r"[()]")

# "&" không ai đọc là "and dấu" — thay bằng "và" đúng nghĩa nối 2 vế.
_AMPERSAND = re.compile(r"&")

# "Tp."/"TP."/"tp." dính liền từ sau (case OSM thật: "Tp.Da Nang") — khác
# ABBREV_MAP trong numbers.py (numbers.ABBREV_MAP["TP"] chỉ khớp token
# TRỌN VẸN, case-sensitive, không xử lý được biến thể có dấu chấm/dính chữ
# sau). Xử lý riêng ở BUILD time vì cần chèn khoảng trắng sau khi mở rộng.
_TP_DOT_PREFIX = re.compile(r"(?<!\w)[Tt][Pp]\.")


def clean_poi_name(raw: str) -> str:
    """Làm sạch tên POI/street thô từ OSM trước khi ghi vào pois.csv/streets.csv.

    OSM tag `name` đôi khi nhồi nhiều biến thể tên vào cùng một field, phân
    tách bằng xuống dòng hoặc dấu ";" (vd "Khách Sạn Sea Castle Hotel\\nSea
    Castle Hotel", "Hải sản Thiên Ngọc;Bún đậu Phố Cổ") — chỉ giữ phần đầu
    tiên. Sau đó chuẩn hoá ký tự/viết tắt không phải dạng nói (xem
    _NOISE_CHARS/_PAREN_CHARS/_AMPERSAND/_TP_DOT_PREFIX ở trên), rồi gỡ cụm
    từ liền kề bị lặp nguyên khối (vd tên gốc đã lặp "Sea Castle Hotel" hai
    lần cạnh nhau dù không có dấu phân tách, hoặc lặp ≥3 lần như "Nhậu Nhậu
    Nhậu" — tên quán nhậu thật, xem _dedupe_adjacent_repeats_pass), so sánh
    không phân biệt hoa/thường. Lặp lại tới khi đạt điểm bất động —
    validate() dựa vào tính chất này (xem test_clean_poi_name_is_idempotent).
    """
    first = re.split(r"[\n;]", raw, maxsplit=1)[0]
    name = normalize_name(first)

    name = _TP_DOT_PREFIX.sub("Thành phố ", name)
    name = _AMPERSAND.sub(" và ", name)
    name = _PAREN_CHARS.sub(" ", name)
    name = _NOISE_CHARS.sub(" ", name)
    name = normalize_name(name)

    tokens = name.split(" ")
    while True:
        deduped = _dedupe_adjacent_repeats_pass(tokens)
        if deduped == tokens:
            break
        tokens = deduped

    return " ".join(tokens)


_GENERIC_ENTITY_NAMES: frozenset[str] = frozenset(
    n.casefold()
    for n in (
        "Công viên", "Trạm xăng", "Chợ", "Quán ăn", "Nhà hàng", "Khách sạn",
        "Nhà nghỉ", "Trường", "Cây xăng", "Trạm y tế", "Bệnh viện", "Siêu thị",
        "Nhà thuốc", "Bãi đỗ xe", "Nhà văn hóa", "Cửa hàng", "Trung tâm",
    )
)

# Mã ngân hàng/thương hiệu phổ biến ngắn (<=3 ký tự) — ngoại lệ của luật
# "tên quá ngắn = rác". Danh sách best-effort, không đầy đủ tuyệt đối — mở
# rộng khi gặp case hợp lệ mới bị reject nhầm.
_SHORT_NAME_ALLOWLIST: frozenset[str] = frozenset(
    n.casefold()
    for n in (
        "GO!", "OCB", "VIB", "VDB", "MB", "ACB", "SHB", "TPB", "VPB", "HDB",
        "SCB", "DAB", "NAB", "SGB", "VAB", "EIB", "STB", "CTG", "VCB", "BID",
    )
)

# Cụm từ tiếng Việt phổ biến khi bị gõ KHÔNG DẤU — dấu hiệu tên đã mất dấu
# (khác thương hiệu Latin thật như "Techcombank"/"WinMart", vốn không có
# dạng có dấu để mất). Không đòi khớp toàn bộ tên, chỉ cần match một cụm
# đặc trưng đủ hiếm để tránh false positive trên tên Latin thật.
_UNACCENTED_VIETNAMESE_PATTERN = re.compile(
    r"\b(Benh Vien|Truong (Tieu|Trung|Mam)|Cho |Duong |Nha (Hang|Thuoc|Nghi)|"
    r"Cong Ty|Khach San|Tiem |Quan An|Cua Hang|Uy Ban|Truong Hoc|Cong Vien|"
    r"Sieu Thi|Ngan Hang|Buu Dien|Tram Y Te|Tram Xa|Ben Xe)",
    re.IGNORECASE,
)


def is_usable_entity_name(name: str) -> bool:
    """Loại tên POI/street KHÔNG dùng được cho dataset spoken-form — gọi
    SAU clean_poi_name(). Ba loại bị loại:

    1. Tên chung chung không phải danh từ riêng (vd "Công viên" trần,
       không kèm tên cụ thể) — nghe như một mô tả, không phải một địa danh
       tìm được cụ thể.
    2. Tên quá ngắn (<=3 ký tự) không thuộc whitelist mã ngân hàng/thương
       hiệu phổ biến — phần lớn là dữ liệu rác OSM (vd "d", "MX", "345").
    3. Tên tiếng Việt KHÔNG DẤU (vd "Benh Vien Y Hoc Co Truyen", "Khach San
       Misa") — không thể tự thêm dấu đáng tin cậy, để nguyên thì sai mục
       đích dataset ASR tiếng Việt.
    """
    stripped = name.strip()
    if not stripped:
        return False
    if stripped.casefold() in _GENERIC_ENTITY_NAMES:
        return False
    if len(stripped) <= 3 and stripped.casefold() not in _SHORT_NAME_ALLOWLIST:
        return False
    if _UNACCENTED_VIETNAMESE_PATTERN.search(stripped):
        return False
    return True


# ---------------------------------------------------------------------------
# Build từng bảng
# ---------------------------------------------------------------------------

_ALIAS_TYPES = ("legacy", "spoken", "abbrev_expanded")
_VALID_ADMIN_PREFIXES = ("tỉnh", "thành phố", "")


def _province_alias_spoken_form(name: str) -> str:
    """Bỏ dấu "-" nối 2 vế tên tỉnh ghép (vd "Bà Rịa - Vũng Tàu" -> "Bà Rịa
    Vũng Tàu"). Dấu gạch ngang là quy ước CHÍNH TẢ VIẾT — không ai đọc
    thành tiếng khi nói, cùng tinh thần hard rule #1 (chữ số/viết tắt phải
    chuyển sang dạng nói) dù kỹ thuật không phải digit/abbreviation. Xử lý
    ở TẦNG BUILD (không phải lúc generate như "cũ"/legacy_marker) vì phép
    biến đổi này không phụ thuộc ngữ cảnh câu — province_alias.csv lưu
    thẳng dạng đã chuẩn hoá, nên province_legacy audit và text hiển thị
    LUÔN khớp nhau, không lệch nhau như khi sửa riêng ở tầng hiển thị."""
    return name.replace(" - ", " ")


def build_provinces(
    raw: RawData, counter: Counter[str]
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[str]]:
    province_rows: list[dict[str, str]] = []
    alias_rows: list[dict[str, str]] = []
    issues: list[str] = []

    for p in raw.provinces:
        province_rows.append(
            {
                "province_id": str(p["province_id"]),
                "name_current": normalize_name(p["name_current"]),
                "admin_status": p["admin_status"],
                "region": p["region"],
                "is_municipality": str(bool(p["is_municipality"])),
                "popularity_tier": str(p["popularity_tier"]),
            }
        )
        counter["provinces"] += 1

        aliases = p.get("aliases", {}) or {}
        for alias_type in _ALIAS_TYPES:
            for alias in aliases.get(alias_type, []) or []:
                if not isinstance(alias, dict) or "name" not in alias or "admin_prefix" not in alias:
                    issues.append(
                        f"provinces.yaml: tỉnh {p.get('province_id')!r} có alias {alias_type} "
                        f"{alias!r} không đúng dạng {{name, admin_prefix}}"
                    )
                    continue
                admin_prefix = alias["admin_prefix"]
                if admin_prefix not in _VALID_ADMIN_PREFIXES:
                    issues.append(
                        f"provinces.yaml: tỉnh {p.get('province_id')!r} alias {alias['name']!r} "
                        f"có admin_prefix {admin_prefix!r} không hợp lệ (chỉ nhận "
                        f"{_VALID_ADMIN_PREFIXES!r})"
                    )
                    continue
                alias_rows.append(
                    {
                        "province_id": str(p["province_id"]),
                        "alias": _province_alias_spoken_form(normalize_name(alias["name"])),
                        "alias_type": alias_type,
                        "weight": "1.0",
                        "admin_prefix": admin_prefix,
                    }
                )
                counter[f"alias.{alias_type}"] += 1

    return province_rows, alias_rows, issues


def _legacy_aliases_by_province(aliases: list[dict[str, str]]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for a in aliases:
        if a["alias_type"] != "legacy":
            continue
        result.setdefault(a["province_id"], set()).add(a["alias"])
    return result


def build_districts(
    raw: RawData, aliases: list[dict[str, str]], counter: Counter[str]
) -> tuple[list[dict[str, str]], list[str]]:
    """legacy_alias (optional, xem docs/data-model.md#sub-region-hint) đánh dấu
    district này thuộc tỉnh CŨ nào trong số các legacy alias của tỉnh hiện
    hành — dùng để tránh ghép sai tên tỉnh cũ lúc generate FULL_ADDRESS
    (vd Tam Kỳ là Quảng Nam cũ, không phải Đà Nẵng cũ, dù cùng province_id
    04). Rỗng nghĩa là chưa xác định được / tỉnh unchanged không cần."""
    legacy_by_province = _legacy_aliases_by_province(aliases)
    issues: list[str] = []
    rows: list[dict[str, str]] = []
    for d in raw.districts:
        legacy_alias = (d.get("legacy_alias") or "").strip()
        if legacy_alias:
            valid = legacy_by_province.get(d["province_id"], set())
            if legacy_alias not in valid:
                issues.append(
                    f"districts.csv: district {d['district_id']!r} có legacy_alias "
                    f"{legacy_alias!r} không khớp legacy alias nào của tỉnh {d['province_id']!r} "
                    f"({sorted(valid)!r})"
                )
        rows.append(
            {
                "district_id": d["district_id"],
                "province_id": d["province_id"],
                "name": normalize_name(d["name"]),
                "type": d["type"],
                "status": d.get("status", "current"),
                "legacy_alias": legacy_alias,
            }
        )
        counter["districts"] += 1
    return rows, issues


def build_wards(
    raw: RawData, aliases: list[dict[str, str]], counter: Counter[str]
) -> tuple[list[dict[str, str]], list[str]]:
    """Ward gắn với tỉnh theo MỘT trong hai đường: qua `district_id` (nguồn
    curate tay, quận/huyện cũ trước 1/7/2025) hoặc thẳng qua `province_id`
    (nguồn OSM, mô hình 2 cấp hiện hành). validate() bắt buộc có đúng ít
    nhất một trong hai.

    `legacy_alias` (optional, chỉ từ nguồn wards_osm.csv qua
    scripts/extract_wards.py) là sub-region hint — cùng cơ chế validate với
    build_districts()/_build_manual_landmarks() (xem
    docs/data-model.md#sub-region-hint)."""
    legacy_by_province = _legacy_aliases_by_province(aliases)
    issues: list[str] = []
    rows: list[dict[str, str]] = []
    for w in raw.wards:
        province_id = (w.get("province_id") or "").strip()
        legacy_alias = (w.get("legacy_alias") or "").strip()
        if legacy_alias:
            valid = legacy_by_province.get(province_id, set())
            if legacy_alias not in valid:
                issues.append(
                    f"wards.csv: ward {w['ward_id']!r} có legacy_alias {legacy_alias!r} "
                    f"không khớp legacy alias nào của tỉnh {province_id!r} ({sorted(valid)!r})"
                )
        rows.append(
            {
                "ward_id": w["ward_id"],
                "district_id": (w.get("district_id") or "").strip(),
                "province_id": province_id,
                "name": clean_poi_name(w["name"]),
                "type": w["type"],
                "legacy_alias": legacy_alias,
            }
        )
        counter["wards"] += 1
    return rows, issues


def _element_ref(element: dict[str, Any]) -> str:
    return f"{element['type']}/{element['id']}"


def build_streets(
    raw: RawData, osm_tags: dict[str, Any], counter: Counter[str]
) -> list[dict[str, str]]:
    """Một street entity = một TÊN đường trong một tỉnh, không phải một OSM
    way segment. OSM chặt một con đường dài thành hàng chục way cùng
    "name" — với người dùng nói "đường Nguyễn Văn Linh" chỉ là MỘT thực thể,
    nên dedupe theo (province_id, tên đã normalize), giữ way có id nhỏ nhất
    làm đại diện (deterministic).

    district_id để trống: dữ liệu OSM chỉ cho toạ độ, không có ranh giới
    hành chính quận/phường để point-in-polygon — xem ghi chú trong
    docs/data-model.md. province_id vẫn có, vì đó chính là tỉnh đã fetch.
    """
    highway_map: dict[str, str] = osm_tags["street_highway_map"]
    best_by_key: dict[tuple[str, str], dict[str, Any]] = {}

    for province_id, elements in raw.osm_by_province.items():
        for el in elements:
            tags = el.get("tags", {})
            highway = tags.get("highway")
            name = tags.get("name")
            if highway not in highway_map or not name:
                continue
            if not is_usable_entity_name(clean_poi_name(name)):
                counter["street.rejected_unusable_name"] += 1
                continue
            key = (province_id, clean_poi_name(name).casefold())
            candidate = {"province_id": province_id, "name": name, "highway": highway, "el": el}
            current = best_by_key.get(key)
            if current is None or el["id"] < current["el"]["id"]:
                best_by_key[key] = candidate

    rows: list[dict[str, str]] = []
    for (province_id, _key_name), candidate in best_by_key.items():
        el = candidate["el"]
        rows.append(
            {
                "street_id": f"osm_street_{_element_ref(el).replace('/', '_')}",
                "province_id": province_id,
                "district_id": "",
                "name": clean_poi_name(candidate["name"]),
                "street_type": highway_map[candidate["highway"]],
            }
        )
        counter["streets"] += 1
    return rows


def build_pois(
    raw: RawData, osm_tags: dict[str, Any], aliases: list[dict[str, str]], counter: Counter[str]
) -> tuple[list[dict[str, str]], list[str]]:
    """Map mỗi OSM element sang đúng 1 (category, subcategory) theo
    config/osm_tags.yaml. Element khớp nhiều rule cùng lúc (hiếm, do
    exclude_if/require_if) chỉ nhận rule đầu tiên khớp.
    """
    issues: list[str] = []
    rows: list[dict[str, str]] = []
    poi_tags: list[dict[str, Any]] = osm_tags["poi_tags"]

    for province_id, elements in raw.osm_by_province.items():
        for el in elements:
            tags = el.get("tags", {})
            name = tags.get("name")
            if not name:
                continue
            for rule in poi_tags:
                key, _, value = rule["osm_tag"].partition("=")
                if tags.get(key) != value:
                    continue
                if "require_if" in rule:
                    rk, _, rv = rule["require_if"].partition("=")
                    if tags.get(rk) != rv:
                        continue
                if "exclude_if" in rule:
                    xk, _, xv = rule["exclude_if"].partition("=")
                    if tags.get(xk) == xv:
                        continue
                if "extra_filter" in rule:
                    fk, _, fv = rule["extra_filter"].partition("=")
                    if tags.get(fk) != fv:
                        continue
                cleaned_name = clean_poi_name(name)
                if not is_usable_entity_name(cleaned_name):
                    counter["poi.rejected_unusable_name"] += 1
                    break
                rows.append(
                    {
                        "poi_id": f"osm_poi_{_element_ref(el).replace('/', '_')}",
                        "name": cleaned_name,
                        "category": rule["category"],
                        "subcategory": rule["subcategory"],
                        "ward_id": "",
                        "district_id": "",
                        "province_id": province_id,
                        "popularity_tier": "2",  # OSM không mang tín hiệu độ nổi tiếng — xem landmarks.yaml (manual)
                        "source": "osm",
                        "osm_id": _element_ref(el),
                        "legacy_alias_hint": "",  # OSM không đáng tin cậy cho sub-region — xem plan/SOURCES.md
                    }
                )
                counter[f"poi.{rule['category']}"] += 1
                break

    manual_rows, manual_issues = _build_manual_landmarks(raw, aliases, counter)
    issues += manual_issues

    # Dedupe: landmark curate tay (source=manual) THẮNG bản OSM trùng tên
    # trong cùng tỉnh — manual mang popularity_tier thật + category đúng
    # (vd "Văn Miếu" từ OSM mang category=poi_other/worship_place sai,
    # landmarks.yaml sửa đúng thành landmark/heritage_site).
    manual_keys = {(r["province_id"], normalize_name(r["name"]).casefold()) for r in manual_rows}
    rows = [
        r for r in rows if (r["province_id"], normalize_name(r["name"]).casefold()) not in manual_keys
    ]
    rows.extend(manual_rows)

    return rows, issues


def _slugify(text: str) -> str:
    """ASCII slug ổn định cho poi_id thủ công — không phụ thuộc unicode
    normalize form của tên gốc (hard rule #4-adjacent: id phải deterministic
    qua các lần build)."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")
    return slug or "unnamed"


def _build_manual_landmarks(
    raw: RawData, aliases: list[dict[str, str]], counter: Counter[str]
) -> tuple[list[dict[str, str]], list[str]]:
    """Nạp data/raw/admin/landmarks.yaml — nguồn curate tay bổ sung cho
    Category.LANDMARK, xem SOURCES.md#landmarksyaml. Mọi entry ở đây LUÔN
    category=landmark (đó là mục đích của file: bù cho địa danh nổi tiếng
    thiếu trong OSM), subcategory phải nằm trong SUBCATEGORIES[LANDMARK].

    legacy_alias (optional) là sub-region hint — xem build_districts() cho
    ý nghĩa đầy đủ, cùng cơ chế validate.
    """
    issues: list[str] = []
    rows: list[dict[str, str]] = []
    valid_subcats = {s.value for s in SUBCATEGORIES[Category.LANDMARK]}
    legacy_by_province = _legacy_aliases_by_province(aliases)

    for entry in raw.landmarks:
        province_id = str(entry.get("province_id", ""))
        for lm in entry.get("landmarks", []) or []:
            name = lm.get("name")
            subcategory = lm.get("subcategory")
            fame_tier = lm.get("fame_tier")
            if not name or subcategory is None or fame_tier is None:
                issues.append(
                    f"landmarks.yaml: tỉnh {province_id!r} có entry thiếu field bắt buộc "
                    f"(name/subcategory/fame_tier): {lm!r}"
                )
                continue
            if subcategory not in valid_subcats:
                issues.append(
                    f"landmarks.yaml: tỉnh {province_id!r} landmark {name!r} có subcategory "
                    f"{subcategory!r} không hợp lệ — phải là một trong {sorted(valid_subcats)}"
                )
                continue
            legacy_alias = (lm.get("legacy_alias") or "").strip()
            if legacy_alias:
                valid = legacy_by_province.get(province_id, set())
                if legacy_alias not in valid:
                    issues.append(
                        f"landmarks.yaml: tỉnh {province_id!r} landmark {name!r} có legacy_alias "
                        f"{legacy_alias!r} không khớp legacy alias nào của tỉnh đó ({sorted(valid)!r})"
                    )
            clean_name = clean_poi_name(name)
            rows.append(
                {
                    "poi_id": f"manual_{province_id}_{_slugify(clean_name)}",
                    "name": clean_name,
                    "category": Category.LANDMARK.value,
                    "subcategory": subcategory,
                    "ward_id": "",
                    "district_id": "",
                    "province_id": province_id,
                    "popularity_tier": str(fame_tier),
                    "source": "manual",
                    "osm_id": "",
                    "legacy_alias_hint": legacy_alias,
                }
            )
            counter["poi.landmark.manual"] += 1

    return rows, issues


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def validate(
    provinces: list[dict[str, str]],
    aliases: list[dict[str, str]],
    districts: list[dict[str, str]],
    wards: list[dict[str, str]],
    streets: list[dict[str, str]],
    pois: list[dict[str, str]],
    admin_invariants: dict[str, int],
) -> list[str]:
    issues: list[str] = []

    province_ids = {p["province_id"] for p in provinces}
    if len(province_ids) != len(provinces):
        issues.append("provinces.csv: có province_id trùng lặp")

    expected_provinces = admin_invariants["expected_province_count"]
    if len(provinces) != expected_provinces:
        issues.append(f"provinces.csv: có {len(provinces)} tỉnh, kỳ vọng {expected_provinces}")

    legacy_aliases = [a["alias"] for a in aliases if a["alias_type"] == "legacy"]
    expected_legacy = admin_invariants["expected_legacy_alias_count"]
    if len(legacy_aliases) != expected_legacy:
        issues.append(
            f"province_alias.csv: có {len(legacy_aliases)} legacy alias, kỳ vọng {expected_legacy}"
        )
    dup_legacy = {a for a in legacy_aliases if legacy_aliases.count(a) > 1}
    if dup_legacy:
        issues.append(f"province_alias.csv: legacy alias trùng lặp xuyên tỉnh: {sorted(dup_legacy)}")

    for a in aliases:
        if a["province_id"] not in province_ids:
            issues.append(f"province_alias.csv: alias {a['alias']!r} trỏ tới province_id lạ {a['province_id']!r}")
        if a["admin_prefix"] not in _VALID_ADMIN_PREFIXES:
            issues.append(
                f"province_alias.csv: alias {a['alias']!r} có admin_prefix {a['admin_prefix']!r} không hợp lệ"
            )

    for d in districts:
        if d["province_id"] not in province_ids:
            issues.append(f"districts.csv: district {d['district_id']!r} trỏ tới province_id lạ {d['province_id']!r}")
    district_ids = {d["district_id"] for d in districts}
    if len(district_ids) != len(districts):
        issues.append("districts.csv: có district_id trùng lặp")

    for w in wards:
        district_id = w.get("district_id", "")
        province_id = w.get("province_id", "")
        if district_id and district_id not in district_ids:
            issues.append(f"wards.csv: ward {w['ward_id']!r} trỏ tới district_id lạ {district_id!r}")
        if province_id and province_id not in province_ids:
            issues.append(f"wards.csv: ward {w['ward_id']!r} trỏ tới province_id lạ {province_id!r}")
        if not district_id and not province_id:
            issues.append(
                f"wards.csv: ward {w['ward_id']!r} không có FK nào — phải có district_id "
                "(nguồn curate tay, quận/huyện cũ) hoặc province_id (nguồn OSM, mô hình 2 cấp)"
            )
    ward_ids = {w["ward_id"] for w in wards}
    if len(ward_ids) != len(wards):
        issues.append("wards.csv: có ward_id trùng lặp")

    for s in streets:
        if s["province_id"] not in province_ids:
            issues.append(f"streets.csv: street {s['street_id']!r} trỏ tới province_id lạ {s['province_id']!r}")
        if s["district_id"] and s["district_id"] not in district_ids:
            issues.append(f"streets.csv: street {s['street_id']!r} trỏ tới district_id lạ {s['district_id']!r}")

    for poi in pois:
        if poi["province_id"] and poi["province_id"] not in province_ids:
            issues.append(f"pois.csv: poi {poi['poi_id']!r} trỏ tới province_id lạ {poi['province_id']!r}")
        if poi["district_id"] and poi["district_id"] not in district_ids:
            issues.append(f"pois.csv: poi {poi['poi_id']!r} trỏ tới district_id lạ {poi['district_id']!r}")
        if poi["ward_id"] and poi["ward_id"] not in ward_ids:
            issues.append(f"pois.csv: poi {poi['poi_id']!r} trỏ tới ward_id lạ {poi['ward_id']!r}")
        if not poi["ward_id"] and not poi["district_id"] and not poi["province_id"]:
            issues.append(f"pois.csv: poi {poi['poi_id']!r} không có FK nào (ward/district/province đều rỗng)")
        if not poi["name"]:
            issues.append(f"pois.csv: poi {poi['poi_id']!r} tên rỗng")
        elif poi["name"] != clean_poi_name(poi["name"]):
            issues.append(
                f"pois.csv: poi {poi['poi_id']!r} tên {poi['name']!r} chưa sạch "
                "(còn ';'/xuống dòng hoặc cụm từ lặp liền kề) — clean_poi_name() không idempotent trên nó"
            )

    poi_ids = [p["poi_id"] for p in pois]
    if len(set(poi_ids)) != len(poi_ids):
        issues.append("pois.csv: có poi_id trùng lặp")

    street_ids = [s["street_id"] for s in streets]
    if len(set(street_ids)) != len(street_ids):
        issues.append("streets.csv: có street_id trùng lặp")

    # Sàn landmark mỗi tỉnh (source=manual, từ landmarks.yaml) — tuỳ chọn:
    # bỏ qua nếu run.yaml chưa khai báo ngưỡng (fixture test không cần).
    min_landmarks = admin_invariants.get("min_landmarks_per_province")
    if min_landmarks is not None:
        landmark_count: Counter[str] = Counter(
            p["province_id"] for p in pois if p["category"] == "landmark" and p["source"] == "manual"
        )
        for province_id in sorted(province_ids):
            n = landmark_count.get(province_id, 0)
            if n < min_landmarks:
                issues.append(
                    f"pois.csv: tỉnh {province_id!r} chỉ có {n} landmark manual, "
                    f"kỳ vọng >= {min_landmarks} (admin_invariants.min_landmarks_per_province) "
                    "— bổ sung vào data/raw/admin/landmarks.yaml"
                )

    return issues


# ---------------------------------------------------------------------------
# Write (deterministic, atomic)
# ---------------------------------------------------------------------------

_TABLE_SPECS: dict[str, tuple[str, ...]] = {
    "provinces.csv": ("province_id", "name_current", "admin_status", "region", "is_municipality", "popularity_tier"),
    "province_alias.csv": ("province_id", "alias", "alias_type", "weight", "admin_prefix"),
    "districts.csv": ("district_id", "province_id", "name", "type", "status", "legacy_alias"),
    "wards.csv": ("ward_id", "district_id", "province_id", "name", "type", "legacy_alias"),
    "streets.csv": ("street_id", "province_id", "district_id", "name", "street_type"),
    "pois.csv": (
        "poi_id",
        "name",
        "category",
        "subcategory",
        "ward_id",
        "district_id",
        "province_id",
        "popularity_tier",
        "source",
        "osm_id",
        "legacy_alias_hint",
    ),
}

_SORT_KEY: dict[str, Any] = {
    "provinces.csv": lambda r: r["province_id"],
    "province_alias.csv": lambda r: (r["province_id"], r["alias_type"], r["alias"]),
    "districts.csv": lambda r: r["district_id"],
    "wards.csv": lambda r: r["ward_id"],
    "streets.csv": lambda r: r["street_id"],
    "pois.csv": lambda r: r["poi_id"],
}


def write_master(master_dir: Path, tables: dict[str, list[dict[str, str]]]) -> None:
    """Ghi tất cả bảng ra file tạm trước, chỉ replace atomic khi TẤT CẢ đã
    ghi thành công — build fail giữa chừng không được để data/master/ ở
    trạng thái nửa vời (một số file mới, một số file cũ)."""
    master_dir.mkdir(parents=True, exist_ok=True)
    tmp_paths: list[tuple[Path, Path]] = []

    for filename, columns in _TABLE_SPECS.items():
        rows = sorted(tables[filename], key=_SORT_KEY[filename])
        final_path = master_dir / filename
        tmp_path = master_dir / f".{filename}.tmp"
        with tmp_path.open("w", encoding="utf-8", newline="\n") as f:
            writer = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        tmp_paths.append((tmp_path, final_path))

    for tmp_path, final_path in tmp_paths:
        tmp_path.replace(final_path)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def build_master(
    raw_dir: Path,
    master_dir: Path,
    osm_tag_config_path: Path = Path("config/osm_tags.yaml"),
    run_config_path: Path = Path("config/run.yaml"),
) -> Counter[str]:
    counter: Counter[str] = Counter()

    raw = read_raw(raw_dir)
    with osm_tag_config_path.open(encoding="utf-8") as f:
        osm_tags = yaml.safe_load(f)
    with run_config_path.open(encoding="utf-8") as f:
        run_config = yaml.safe_load(f)

    deduped_osm = dedupe_cross_province_osm_elements(
        raw.osm_by_province, raw.osm_bbox_by_province, counter
    )
    raw = replace(raw, osm_by_province=deduped_osm)

    provinces, aliases, province_issues = build_provinces(raw, counter)
    districts, district_issues = build_districts(raw, aliases, counter)
    wards, ward_issues = build_wards(raw, aliases, counter)
    streets = build_streets(raw, osm_tags, counter)
    pois, poi_issues = build_pois(raw, osm_tags, aliases, counter)

    issues = province_issues + district_issues + ward_issues + poi_issues + validate(
        provinces, aliases, districts, wards, streets, pois, run_config["admin_invariants"]
    )
    if issues:
        raise BuildError(issues)

    write_master(
        master_dir,
        {
            "provinces.csv": provinces,
            "province_alias.csv": aliases,
            "districts.csv": districts,
            "wards.csv": wards,
            "streets.csv": streets,
            "pois.csv": pois,
        },
    )

    counter["total_rows"] = (
        len(provinces) + len(aliases) + len(districts) + len(wards) + len(streets) + len(pois)
    )
    return counter
