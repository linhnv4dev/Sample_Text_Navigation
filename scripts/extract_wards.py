#!/usr/bin/env python3
"""Trích phường/xã (đơn vị hành chính cấp xã) từ Geofabrik .pbf ra
data/raw/admin/wards_osm.csv.

Đây là SCRIPT CHẠY TAY, ngoài pipeline `navtext` — cùng ranh giới với
scripts/fetch_osm.py: ingest có thể đọc file nặng/gọi network, còn
`navtext build-master` thì thuần offline, chỉ đọc artifact đã commit.

LÝ DO TỒN TẠI: sau sáp nhập 1/7/2025 Việt Nam bỏ cấp huyện, còn mô hình 2
cấp (tỉnh → xã/phường). data/raw/admin/wards.csv curate tay chỉ có 18
phường của 3 tỉnh mẫu, nên 31 tỉnh còn lại chỉ có ĐÚNG 1 entity admin_unit
(chính tên tỉnh) — không đủ pool để `navtext plan` phân bổ quota
admin_unit mà không vượt caps.entity.max_share. Script này lấp đúng lỗ đó.

QUY ƯỚC OSM: sau đợt cập nhật ranh giới 2025, cộng đồng OSM Việt Nam map
cấp xã/phường hiện hành thành `boundary=administrative` +
`admin_level=6` (KHÔNG phải 8 — level 8 trong extract này gần như chỉ còn
vài quan hệ vùng biên Trung Quốc). Xem SOURCES.md#wards_osmcsv.

Cách dùng:
    python3 scripts/extract_wards.py data/raw/osm_pbf/vietnam-latest.osm.pbf
"""

from __future__ import annotations

import argparse
import csv
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

# Tiền tố tên OSM -> giá trị cột `type` trong wards.csv. Tên OSM nhúng sẵn
# tiền tố ("Phường Sơn Trà") còn schema tách riêng name/type ("Sơn Trà" +
# "phường") — xem data/raw/admin/wards.csv. Tên KHÔNG khớp tiền tố nào ở
# đây bị bỏ qua: extract Geofabrik bao cả vùng biên Trung Quốc/Campuchia/
# Lào (vd "田蓬镇", "កំពង់សោម") và chúng không phải đơn vị hành chính VN.
_NAME_PREFIX_TO_TYPE: dict[str, str] = {
    "Phường ": "phường",
    "Xã ": "xã",
    "Thị trấn ": "thị trấn",
    # "Đặc khu" là cấp mới sau 2025 cho một số đảo (Phú Quốc, Côn Đảo, Lý
    # Sơn, Cô Tô...) — vẫn là đơn vị cấp xã, vẫn là cách người dùng nói.
    "Đặc khu ": "đặc khu",
}

ADMIN_LEVEL_WARD = "6"


def normalize_name(raw: str) -> str:
    """Giữ đồng bộ với build_master.normalize_name() — NFC + gộp khoảng
    trắng. Trùng lặp một hàm 1 dòng ở đây thay vì import navtext để script
    chạy được độc lập với package đã cài (nó là tool ingest, không phải
    một phase của pipeline)."""
    return " ".join(unicodedata.normalize("NFC", raw).split())


def split_ward_prefix(osm_name: str) -> tuple[str, str] | None:
    """"Phường Sơn Trà" -> ("Sơn Trà", "phường"). None nếu tên không mang
    tiền tố đơn vị hành chính cấp xã nào của Việt Nam."""
    name = normalize_name(osm_name)
    for prefix, ward_type in _NAME_PREFIX_TO_TYPE.items():
        if name.startswith(prefix):
            core = name[len(prefix) :].strip()
            if core:
                return core, ward_type
    return None


ADMIN_LEVEL_PROVINCE = "4"

_PROVINCE_NAME_PREFIXES = ("Thành phố ", "Tỉnh ", "Đặc khu ")


def _strip_province_prefix(osm_name: str) -> str:
    name = normalize_name(osm_name)
    for prefix in _PROVINCE_NAME_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix) :].strip()
    return name


def load_province_ids_by_name(provinces_path: Path) -> dict[str, str]:
    import yaml

    with provinces_path.open(encoding="utf-8") as f:
        provinces = yaml.safe_load(f) or []
    return {normalize_name(p["name_current"]): p["province_id"] for p in provinces}


def load_legacy_names_by_province(provinces_path: Path) -> dict[str, list[str]]:
    """Tên legacy alias (đã prefix-strip, đã chuẩn hoá dấu "-" như
    build_master._province_alias_spoken_form) của MỌI tỉnh GỘP (>1 alias)
    — chỉ tỉnh gộp mới cần sub-region hint (tỉnh 1 alias không mơ hồ, xem
    docs/data-model.md#sub-region-hint).

    CỐ Ý GIỮ alias identity (== tên hiện hành, vd "Đà Nẵng" là 1 trong 2
    legacy alias của chính tỉnh 04 Đà Nẵng) trong danh sách — đây là danh
    sách ỨNG VIÊN cho thuật toán loại trừ trong resolve_legacy_alias(), cần
    biết tỉnh có TỔNG CỘNG bao nhiêu alias để tính "thiếu đúng 1 thì suy ra
    được". Việc alias identity KHÔNG được tìm làm đa giác riêng (lý do: xem
    extract_from_pbf()) là quyết định ở bước khác, không phải ở đây."""
    import yaml

    with provinces_path.open(encoding="utf-8") as f:
        provinces = yaml.safe_load(f) or []
    result: dict[str, list[str]] = {}
    for p in provinces:
        legacy = p.get("aliases", {}).get("legacy", []) or []
        if len(legacy) <= 1:
            continue
        result[p["province_id"]] = [
            _strip_province_prefix(a["name"]).replace(" - ", " ") for a in legacy
        ]
    return result


def _stitch_rings(ways: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """Ghép các way rời rạc của một relation ranh giới thành các vòng khép
    kín. Relation OSM chỉ liệt kê way thành viên KHÔNG theo thứ tự và
    không đảm bảo chiều — phải nối theo điểm đầu/cuối trùng nhau."""
    remaining = [w for w in ways if len(w) >= 2]
    rings: list[list[tuple[float, float]]] = []

    while remaining:
        ring = list(remaining.pop())
        extended = True
        while extended and ring[0] != ring[-1]:
            extended = False
            for i, candidate in enumerate(remaining):
                if candidate[0] == ring[-1]:
                    ring.extend(candidate[1:])
                elif candidate[-1] == ring[-1]:
                    ring.extend(reversed(candidate[:-1]))
                elif candidate[-1] == ring[0]:
                    ring = candidate[:-1] + ring
                elif candidate[0] == ring[0]:
                    ring = list(reversed(candidate[1:])) + ring
                else:
                    continue
                remaining.pop(i)
                extended = True
                break
        if len(ring) >= 4:
            rings.append(ring)
    return rings


def _point_in_rings(rings: list[list[tuple[float, float]]], lat: float, lon: float) -> bool:
    """Ray casting: điểm nằm trong đa giác nếu số lần cắt cạnh là lẻ. Đếm
    trên TẤT CẢ ring của tỉnh — đảo/enclave là ring riêng, và một điểm nằm
    trong đúng một ring vẫn cho tổng lẻ."""
    inside = False
    for ring in rings:
        for i in range(len(ring) - 1):
            lat1, lon1 = ring[i]
            lat2, lon2 = ring[i + 1]
            if (lat1 > lat) != (lat2 > lat):
                x = (lon2 - lon1) * (lat - lat1) / (lat2 - lat1) + lon1
                if lon < x:
                    inside = not inside
    return inside


def _ring_bbox(rings: list[list[tuple[float, float]]]) -> tuple[float, float, float, float]:
    lats = [p[0] for ring in rings for p in ring]
    lons = [p[1] for ring in rings for p in ring]
    return (min(lats), min(lons), max(lats), max(lons))


def extract_from_pbf(
    pbf_path: Path,
    province_ids_by_name: dict[str, str],
    legacy_names_by_province: dict[str, list[str]],
    counter: Counter[str],
) -> tuple[
    list[dict[str, Any]],
    dict[str, list[list[tuple[float, float]]]],
    dict[tuple[str, str], list[list[tuple[float, float]]]],
]:
    """Hai lượt duyệt .pbf, lấy CÙNG LÚC ranh giới tỉnh hiện hành
    (admin_level=4, tên khớp `provinces.yaml#name_current`), ranh giới tỉnh
    CŨ (admin_level=4, tên khớp một legacy alias của tỉnh gộp — sub-region
    hint, xem docs/data-model.md#sub-region-hint) và phường/xã
    (admin_level=6): lượt 1 lấy relation + danh sách way thành viên, lượt 2
    resolve toạ độ node của các way đó.

    Ranh giới tỉnh CŨ thường bị cộng đồng OSM đổi tag `boundary` từ
    "administrative" sang "historic" sau khi tỉnh đó sáp nhập (ranh giới
    hiện hành mới là "administrative") — CHỈ nhánh tỉnh cũ chấp nhận cả
    hai, ranh giới tỉnh hiện hành và phường/xã giữ nguyên "administrative"
    (đã chạy đúng 34/34 tỉnh, không đổi để tránh rủi ro trộn phải relation
    trùng tên đã lỗi thời).

    Cố ý KHÔNG dùng osmium `.with_areas()`: area manager mặc định chỉ ráp
    `type=multipolygon`, còn ranh giới hành chính là `type=boundary` nên
    trả về 0 area (đã thử thực tế). Hai lượt tay rẻ hơn nhiều so với cấu
    hình lại area manager.

    Trả về (danh sách phường/xã kèm điểm đại diện, đa giác từng tỉnh hiện
    hành, đa giác từng (tỉnh hiện hành, legacy alias)).
    """
    import osmium  # lazy import — chỉ script này cần pyosmium

    # legacy_lookup dùng để TÌM RELATION đại diện cho một alias — CỐ Ý loại
    # alias identity (== tên hiện hành của chính tỉnh đó, xem
    # load_legacy_names_by_province): quan hệ admin_level=4 mang tên đó
    # trong .pbf CHÍNH LÀ ranh giới tỉnh HIỆN HÀNH (đã gộp, phủ toàn bộ
    # lãnh thổ mới), không phải một ranh giới con nhỏ hơn. Nếu không loại,
    # "đa giác legacy" dựng ra sẽ trùng khít đa giác tỉnh hiện hành, khiến
    # MỌI điểm trong tỉnh (kể cả lãnh thổ tỉnh cũ kia) match nhầm vào alias
    # identity — resolve_legacy_alias() vẫn nhận đủ danh sách alias qua
    # legacy_names_by_province để tính "thiếu đúng 1 -> suy ra được", chỉ
    # riêng bước TÌM ĐA GIÁC là bỏ qua alias identity.
    name_current_by_province = {pid: name for name, pid in province_ids_by_name.items()}
    legacy_lookup: dict[str, tuple[str, str]] = {
        name: (pid, name)
        for pid, names in legacy_names_by_province.items()
        for name in names
        if name != name_current_by_province.get(pid)
    }

    ward_tags: dict[int, dict[str, str]] = {}
    province_tags: dict[int, str] = {}  # relation_id -> province_id hiện hành
    legacy_tags: dict[int, tuple[str, str]] = {}  # relation_id -> (province_id, legacy_name)
    way_to_relations: dict[int, list[int]] = {}

    for obj in osmium.FileProcessor(str(pbf_path), osmium.osm.RELATION):
        tags = dict(obj.tags)
        boundary = tags.get("boundary")
        level = tags.get("admin_level")
        name = tags.get("name")
        if not name:
            continue

        matched = False
        if boundary == "administrative" and level == ADMIN_LEVEL_WARD:
            ward_tags[obj.id] = tags
            matched = True
        if boundary == "administrative" and level == ADMIN_LEVEL_PROVINCE:
            province_id = province_ids_by_name.get(_strip_province_prefix(name))
            if province_id is not None:
                province_tags[obj.id] = province_id
                matched = True
        if boundary in ("administrative", "historic") and level == ADMIN_LEVEL_PROVINCE:
            legacy_key = legacy_lookup.get(_strip_province_prefix(name))
            if legacy_key is not None:
                legacy_tags.setdefault(obj.id, legacy_key)
                matched = True
        if not matched:
            continue

        for member in obj.members:
            if member.type == "w":
                way_to_relations.setdefault(member.ref, []).append(obj.id)

    counter["pbf.ward_relations"] = len(ward_tags)
    counter["pbf.province_relations"] = len(province_tags)
    counter["pbf.legacy_relations"] = len(legacy_tags)

    ward_bounds: dict[int, list[float]] = {}
    province_ways: dict[int, list[list[tuple[float, float]]]] = {}
    legacy_ways: dict[int, list[list[tuple[float, float]]]] = {}
    for obj in osmium.FileProcessor(str(pbf_path)).with_locations():
        if not obj.is_way():
            continue
        relation_ids = way_to_relations.get(obj.id)
        if not relation_ids:
            continue
        coords = [
            (nr.location.lat, nr.location.lon) for nr in obj.nodes if nr.location.valid()
        ]
        if not coords:
            continue
        for relation_id in relation_ids:
            if relation_id in province_tags:
                province_ways.setdefault(relation_id, []).append(coords)
            if relation_id in legacy_tags:
                legacy_ways.setdefault(relation_id, []).append(coords)
            if relation_id in province_tags or relation_id in legacy_tags:
                continue
            box = ward_bounds.get(relation_id)
            if box is None:
                ward_bounds[relation_id] = [
                    min(c[0] for c in coords),
                    min(c[1] for c in coords),
                    max(c[0] for c in coords),
                    max(c[1] for c in coords),
                ]
            else:
                box[0] = min(box[0], min(c[0] for c in coords))
                box[1] = min(box[1], min(c[1] for c in coords))
                box[2] = max(box[2], max(c[0] for c in coords))
                box[3] = max(box[3], max(c[1] for c in coords))

    rings_by_province: dict[str, list[list[tuple[float, float]]]] = {}
    for relation_id, province_id in province_tags.items():
        ways = province_ways.get(relation_id)
        if not ways:
            continue
        rings_by_province.setdefault(province_id, []).extend(_stitch_rings(ways))

    legacy_rings: dict[tuple[str, str], list[list[tuple[float, float]]]] = {}
    for relation_id, key in legacy_tags.items():
        ways = legacy_ways.get(relation_id)
        if not ways:
            continue
        legacy_rings.setdefault(key, []).extend(_stitch_rings(ways))

    wards: list[dict[str, Any]] = []
    for relation_id, tags in ward_tags.items():
        box = ward_bounds.get(relation_id)
        if box is None:
            counter["skip.no_geometry"] += 1
            continue
        split = split_ward_prefix(tags["name"])
        if split is None:
            counter["skip.not_vietnamese_admin_name"] += 1
            continue
        name, ward_type = split
        wards.append(
            {
                "relation_id": relation_id,
                "name": name,
                "type": ward_type,
                "lat": (box[0] + box[2]) / 2,
                "lon": (box[1] + box[3]) / 2,
            }
        )
    return wards, rings_by_province, legacy_rings


def _bbox_contains(bbox: tuple[float, float, float, float], lat: float, lon: float) -> bool:
    south, west, north, east = bbox
    return south <= lat <= north and west <= lon <= east


def resolve_legacy_alias(
    province_id: str,
    lat: float,
    lon: float,
    legacy_names_by_province: dict[str, list[str]],
    legacy_rings: dict[tuple[str, str], list[list[tuple[float, float]]]],
    counter: Counter[str],
) -> str:
    """Trả legacy alias mà điểm (lat, lon) thuộc về, trong số các alias của
    tỉnh gộp `province_id` — "" nếu tỉnh không gộp (1 alias, không mơ hồ)
    hoặc không xác định được (thiếu đa giác để suy luận).

    - Đa giác thiếu ĐÚNG 1 alias trong số các alias của tỉnh: match đa giác
      nào có thì trả alias đó; không match cái nào -> suy luận LOẠI TRỪ ra
      alias còn thiếu (điểm đã biết chắc thuộc tỉnh gộp này, "không thuộc
      N-1 alias có đa giác" nghĩa là thuộc alias thứ N).
    - Đa giác thiếu >= 2 alias: không đủ để loại trừ — chỉ trả được khi
      match đúng MỘT đa giác có, còn lại "" (rủi ro tồn dư đã ghi nhận,
      xem SOURCES.md#wards_osmcsv).
    """
    names = legacy_names_by_province.get(province_id)
    if not names:
        return ""

    found = {
        name: legacy_rings[(province_id, name)]
        for name in names
        if (province_id, name) in legacy_rings
    }
    missing = [name for name in names if name not in found]

    for name, rings in found.items():
        if _point_in_rings(rings, lat, lon):
            counter[f"legacy_hint.matched.{province_id}"] += 1
            return name

    if len(missing) == 1:
        counter[f"legacy_hint.eliminated.{province_id}"] += 1
        return missing[0]

    counter[f"legacy_hint.unresolved.{province_id}"] += 1
    return ""


def assign_provinces(
    wards: list[dict[str, Any]],
    rings_by_province: dict[str, list[list[tuple[float, float]]]],
    legacy_names_by_province: dict[str, list[str]],
    legacy_rings: dict[tuple[str, str], list[list[tuple[float, float]]]],
    counter: Counter[str],
) -> list[dict[str, str]]:
    """Gán mỗi phường/xã vào tỉnh chứa nó, bằng POINT-IN-POLYGON trên ranh
    giới hành chính THẬT (admin_level=4 trong .pbf) — không dùng bbox chữ
    nhật.

    Bbox từng gây sai nghiêm trọng: bbox tỉnh Khánh Hòa (`26`) bao cả quần
    đảo Trường Sa nên tâm bbox rơi ra giữa biển (10.4°N), khiến gần như
    toàn bộ phường đất liền Nha Trang bị gán nhầm sang tỉnh lân cận có tâm
    bbox gần hơn — chỉ 1/khoảng 130 phường ở lại đúng tỉnh. Đa giác thật
    không có lớp lỗi này.

    bbox vẫn được dùng, nhưng CHỈ để loại nhanh ứng viên trước khi chạy ray
    casting (đắt hơn nhiều) — không tham gia quyết định.
    """
    bbox_by_province = {pid: _ring_bbox(rings) for pid, rings in rings_by_province.items()}

    rows: list[dict[str, str]] = []
    for ward in wards:
        lat, lon = ward["lat"], ward["lon"]
        matches = [
            pid
            for pid, bbox in bbox_by_province.items()
            if _bbox_contains(bbox, lat, lon)
            and _point_in_rings(rings_by_province[pid], lat, lon)
        ]
        if not matches:
            counter["skip.outside_all_province_polygon"] += 1
            continue
        if len(matches) > 1:
            # Điểm đại diện là tâm bbox của phường, có thể lệch ra ngoài
            # hình thật (phường hình vòng cung/ven biển) và rơi vào vùng
            # chồng lấn — hiếm; chọn deterministic theo province_id.
            counter["cross_province_ambiguous"] += 1
        province_id = min(matches)
        legacy_alias = resolve_legacy_alias(
            province_id, lat, lon, legacy_names_by_province, legacy_rings, counter
        )
        rows.append(
            {
                "ward_id": f"osm_ward_{ward['relation_id']}",
                "district_id": "",  # mô hình 2 cấp sau 2025 — xã thuộc thẳng tỉnh
                "province_id": province_id,
                "name": ward["name"],
                "type": ward["type"],
                "legacy_alias": legacy_alias,
            }
        )
        counter[f"ward.{province_id}"] += 1
    return rows


def write_wards_csv(rows: list[dict[str, str]], out_path: Path) -> None:
    columns = ("ward_id", "district_id", "province_id", "name", "type", "legacy_alias")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows, key=lambda r: r["ward_id"])
    with out_path.open("w", encoding="utf-8", newline="\n") as f:
        writer = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in ordered:
            writer.writerow(row)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pbf_path", type=Path)
    parser.add_argument(
        "--provinces-path", type=Path, default=Path("data/raw/admin/provinces.yaml")
    )
    parser.add_argument("--out", type=Path, default=Path("data/raw/admin/wards_osm.csv"))
    args = parser.parse_args(argv)

    counter: Counter[str] = Counter()

    province_ids_by_name = load_province_ids_by_name(args.provinces_path)
    legacy_names_by_province = load_legacy_names_by_province(args.provinces_path)
    print(
        f"Đọc {len(province_ids_by_name)} tỉnh từ {args.provinces_path} "
        f"({len(legacy_names_by_province)} tỉnh gộp cần sub-region hint)",
        file=sys.stderr,
    )

    print(
        f"Duyệt {args.pbf_path} lấy admin_level={ADMIN_LEVEL_PROVINCE} (ranh giới tỉnh hiện "
        f"hành + tỉnh cũ) + admin_level={ADMIN_LEVEL_WARD} (phường/xã)...",
        file=sys.stderr,
    )
    wards, rings_by_province, legacy_rings = extract_from_pbf(
        args.pbf_path, province_ids_by_name, legacy_names_by_province, counter
    )
    print(
        f"  -> {len(wards)} phường/xã có tên hợp lệ, "
        f"{len(rings_by_province)}/{len(province_ids_by_name)} tỉnh có đa giác, "
        f"{len(legacy_rings)}/{sum(len(v) for v in legacy_names_by_province.values())} "
        "alias tỉnh cũ có đa giác",
        file=sys.stderr,
    )
    unmatched = sorted(set(province_ids_by_name.values()) - set(rings_by_province))
    if unmatched:
        print(f"FAIL: không dựng được ranh giới cho tỉnh {unmatched}", file=sys.stderr)
        return 1

    rows = assign_provinces(wards, rings_by_province, legacy_names_by_province, legacy_rings, counter)
    if not rows:
        print("FAIL: 0 phường/xã gán được vào tỉnh nào", file=sys.stderr)
        return 1

    write_wards_csv(rows, args.out)

    per_province = {k.split(".", 1)[1]: v for k, v in counter.items() if k.startswith("ward.")}
    missing = sorted(set(province_ids_by_name.values()) - set(per_province))
    with_hint = sum(1 for r in rows if r["legacy_alias"])
    print(
        f"OK: {args.out} ({len(rows)} phường/xã, "
        f"{len(per_province)}/{len(province_ids_by_name)} tỉnh, "
        f"{with_hint} có legacy_alias hint)"
    )
    if missing:
        print(f"  CẢNH BÁO: tỉnh không có phường/xã nào: {missing}", file=sys.stderr)
    for key in ("pbf.ward_relations", "pbf.province_relations", "pbf.legacy_relations",
                "skip.no_geometry", "skip.not_vietnamese_admin_name",
                "skip.outside_all_province_polygon", "cross_province_ambiguous"):
        if counter.get(key):
            print(f"  {key}: {counter[key]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
