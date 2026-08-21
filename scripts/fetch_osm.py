#!/usr/bin/env python3
"""Ingest OSM data cho một tỉnh, ghi JSON thô vào data/raw/osm/<province_id>.json.

Đây là SCRIPT CHẠY TAY, ngoài pipeline `navtext` — hard rule #5 (runtime
không gọi LLM) không áp dụng ở đây vì đây không phải runtime generation, còn
việc gọi network thì CHỈ được phép ở script này, không bao giờ trong
`navtext build-master` hay bất kỳ phase runtime nào khác (build-master chỉ
đọc snapshot đã commit).

Snapshot ghi ra là JSON thô từ Overpass, KHÔNG transform — map sang
category/subcategory là việc của build_master.py, để đổi mapping không cần
fetch lại.

Cách dùng:
    python3 scripts/fetch_osm.py --province-id 01
    python3 scripts/fetch_osm.py --province-id 01 --bbox 20.90,105.60,21.35,106.05
    python3 scripts/fetch_osm.py --all              # fetch mọi tỉnh còn thiếu snapshot
    python3 scripts/fetch_osm.py --all --force       # fetch lại cả tỉnh đã có snapshot
    python3 scripts/fetch_osm.py --from-pbf data/raw/osm_pbf/vietnam-latest.osm.pbf
                                                       # ingest cả 34 tỉnh 1 lần, offline

Bbox tự động lấy qua Nominatim (geocode_province_bbox) dựa trên
data/raw/admin/provinces.yaml — không cần tự tay đoán toạ độ. SAMPLE_PROVINCE_BBOX
bên dưới chỉ còn là override thủ công/lịch sử cho 3 tỉnh mẫu ban đầu.

--from-pbf là đường ingest THAY THẾ cho --all/--province-id (Overpass API
công cộng — xem OVERPASS_URL — chứng minh không đủ tin cậy để fetch đủ 34
tỉnh: rate limit 2 slot/instance, 504 Gateway Timeout thoáng qua, kết nối bị
ngắt giữa chừng — xem SOURCES.md#snapshot-osm). Nguồn: Geofabrik Vietnam
extract (https://download.geofabrik.de/asia/vietnam-latest.osm.pbf, ~320MB,
cập nhật hàng ngày) — tải file này về TAY (không tự động trong script, để
người chạy chọn đúng ngày/mirror), rồi chạy `--from-pbf <path>` để lọc offline
bằng pyosmium, ghi ra ĐÚNG format snapshot JSON như đường Overpass (build_master.py
không cần biết snapshot đến từ nguồn nào).
"""

from __future__ import annotations

import argparse
import http.client
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

import yaml

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "navtext-dataset-builder/0.1 (research; contact: linhnv.4dev@gmail.com)"

# south,west,north,east — override thủ công lịch sử cho 3 tỉnh mẫu ban đầu
# (lõi đô thị, KHÔNG phải ranh giới hành chính đầy đủ). Tỉnh không có ở đây
# lấy bbox tự động qua geocode_province_bbox().
SAMPLE_PROVINCE_BBOX: dict[str, tuple[float, float, float, float]] = {
    "01": (20.90, 105.60, 21.35, 106.05),  # Hà Nội (lõi cũ)
    "02": (10.70, 106.55, 10.90, 106.75),  # Hồ Chí Minh (lõi cũ)
    "04": (15.95, 108.10, 16.15, 108.30),  # Đà Nẵng (lõi cũ)
}

MAX_RETRIES = 5
RETRY_BACKOFF_SECONDS = 10.0  # instance công cộng có 504 Gateway Timeout THOÁNG QUA
# (đã xác nhận thực tế: query giống hệt fail rồi thành công vài giây sau) —
# cần backoff đủ dài + đủ lượt retry để sống sót qua đợt overload ngắn.
INTER_REQUEST_SLEEP_SECONDS = 5.0  # tự giác rate-limit, tránh dội Overpass công cộng
# (nâng từ 2.0 — quan sát thực tế 504 Gateway Timeout THOÁNG QUA khi chạy
# nhiều tile/tỉnh liên tiếp sát nhau; --all vốn đã bỏ qua tỉnh đã có
# snapshot nên chạy lại nhiều lần để "vét" tỉnh lỡ fail là chiến lược chấp
# nhận được, rẻ hơn cố loại bỏ hoàn toàn flakiness của instance công cộng).
NOMINATIM_SLEEP_SECONDS = 1.0  # Nominatim usage policy: tối đa 1 request/giây

# Ngân sách thực thi Overpass, giây — truyền vào [out:json][timeout:N] TRONG
# query. 60s (giá trị mặc định phổ biến) đủ cho bbox lõi đô thị nhỏ nhưng
# KHÔNG đủ cho bbox nguyên tỉnh gộp (quan sát thực tế: "Query timed out ...
# after 68 seconds" trên tỉnh 03 Hải Phòng+Hải Dương, và ngay cả sau khi
# nâng lên 180s vẫn "HTTP Error 504: Gateway Timeout" — nginx gateway của
# overpass-api.de có trần thời lượng request RIÊNG, độc lập với
# [timeout:N] trong query, và KHÔNG nới được bằng cách tăng con số này).
# Giải pháp thật là chia nhỏ bbox lớn thành lưới ô con — xem
# MAX_TILE_AREA_DEG2/_split_bbox()/fetch_province() — nên giữ ngân sách
# mỗi request ở mức khiêm tốn, đáng tin cậy hơn là cố kéo dài.
OVERPASS_QUERY_TIMEOUT_SECONDS = 90
OVERPASS_HTTP_TIMEOUT_SECONDS = 120

# Diện tích tối đa (độ vuông) một bbox được fetch trong MỘT request Overpass
# — bbox tỉnh gộp lớn hơn mức này bị chia lưới ô vuông con trước khi fetch
# (xem _split_bbox()/fetch_province()). 0.25 ~ kích thước tổ hợp các bbox
# lõi đô thị đã chạy ổn định ở 3 tỉnh mẫu ban đầu (Hà Nội 0.2025, HCM 0.04,
# Đà Nẵng 0.04) — chọn dư ra một chút, không sát ngưỡng đã biết là fail.
MAX_TILE_AREA_DEG2 = 0.25


class FetchError(RuntimeError):
    """Raise khi Overpass trả lỗi hoặc response rỗng bất thường — không được
    lặng lẽ ghi snapshot rỗng, vì đó là cách tệ nhất để mất dữ liệu mà không
    ai biết."""


def load_tag_config(config_path: Path) -> dict:
    with config_path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_provinces(provinces_path: Path) -> list[dict]:
    with provinces_path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


# Diện tích tối thiểu (độ vuông, xấp xỉ thô) một bbox tỉnh hợp lệ phải có —
# lưới an toàn cuối cùng chống việc lọt qua một match sai/quá nhỏ (vd một
# chi nhánh ngân hàng trùng tên tỉnh, hoặc chỉ mỗi lõi thành phố cũ trước
# sáp nhập) dù đã lọc osm_type ở _fetch_nominatim_bbox. 0.05 độ vuông ~
# 500km² bbox, thấp hơn diện tích tỉnh nhỏ nhất VN nhưng đủ lớn để loại
# match cấp phường/thành phố nhỏ (case "Bắc Ninh" thực đo được 0.012).
_MIN_PROVINCE_BBOX_AREA_DEG2 = 0.05

# type chấp nhận cho relation kết quả — "administrative" là chuẩn, nhưng
# nhiều ranh giới tỉnh GỘP (sau 1/7/2025) chưa được cộng đồng OSM cập nhật
# kịp, quan sát thấy relation ranh giới TỈNH CŨ (trước sáp nhập) vẫn còn
# nhưng bị đổi type thành "historic" thay vì bị xoá — chấp nhận cả hai,
# ưu tiên "administrative" trước qua thứ tự trong tuple.
_ACCEPTED_RELATION_TYPES = ("administrative", "historic")

# Bao lãnh thổ đất liền Việt Nam (không tính Hoàng Sa/Trường Sa) — dùng để
# CLIP bbox trả về từ Nominatim. Một vài tỉnh (quan sát thực tế: Khánh Hòa)
# có relation hành chính OSM bao gồm cả quần đảo Trường Sa (Nominatim trả
# bbox area=32.6 độ vuông so với tỉnh gộp lớn nhất khác chỉ ~7.5) — khiến
# bbox center lệch hẳn ra biển khơi, làm heuristic "tỉnh nào gần center
# nhất" (xem ingest_from_pbf) gán sai gần như toàn bộ element đất liền
# thật (Nha Trang...) sang tỉnh lân cận. Trường Sa/Hoàng Sa xa hơn biên này
# nhiều (~111-117°E, ~6.5-9.5°N) nên clip không cắt vào lãnh thổ đất liền
# thật (điểm cực đông đất liền, Mũi Đôi/Khánh Hòa, chỉ ~109.36°E).
_MAINLAND_LAT_MIN = 8.0
_MAINLAND_LON_MAX = 110.0


def _clip_to_mainland(
    bbox: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    south, west, north, east = bbox
    return (max(south, _MAINLAND_LAT_MIN), west, north, min(east, _MAINLAND_LON_MAX))


def _fetch_nominatim_bbox(query: str) -> tuple[float, float, float, float] | None:
    """Tìm bbox qua Nominatim free-text search, CHỈ chấp nhận kết quả là
    boundary relation thật (osm_type=relation) — free-text search có thể
    khớp nhầm sang một POI trùng tên tỉnh (vd chi nhánh ngân hàng "Agribank
    ... Đồng Nai", node/way) nếu không lọc, trả bbox bé xíu sai hoàn toàn.
    Query KHÔNG kèm tiền tố "Tỉnh"/"Thành phố" — thử nghiệm cho thấy kèm
    tiền tố làm Nominatim khớp sai/khớp rỗng thường xuyên hơn hẳn so với
    tên trần (xem SOURCES.md#snapshot-osm).

    Trong các relation candidate còn lại (đã lọc type, xem
    _ACCEPTED_RELATION_TYPES), chọn cái có DIỆN TÍCH LỚN NHẤT — tỉnh luôn
    lớn hơn hẳn phường/thành phố trực thuộc cùng tên, nên đây là tiêu chí
    phân biệt đáng tin hơn dựa thuần vào `type` (vốn không nhất quán giữa
    các tỉnh do OSM cập nhật ranh giới gộp không đồng đều). Diện tích dùng
    để CHỌN candidate lấy từ bbox GỐC (trước clip) — clip chỉ áp lên bbox
    cuối cùng trả về, không ảnh hưởng bước so sánh diện tích.
    """
    params = urllib.parse.urlencode({"q": query, "format": "jsonv2", "limit": 10})
    req = urllib.request.Request(
        f"{NOMINATIM_URL}?{params}", headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    results = json.loads(raw)
    time.sleep(NOMINATIM_SLEEP_SECONDS)

    candidates: list[tuple[float, tuple[float, float, float, float]]] = []
    for r in results:
        if r.get("osm_type") != "relation" or r.get("type") not in _ACCEPTED_RELATION_TYPES:
            continue
        # Nominatim trả boundingbox = [south, north, west, east] dạng
        # string — reorder về (south, west, north, east) mà
        # SAMPLE_PROVINCE_BBOX/--bbox dùng.
        south, north, west, east = (float(x) for x in r["boundingbox"])
        area = (north - south) * (east - west)
        if area >= _MIN_PROVINCE_BBOX_AREA_DEG2:
            candidates.append((area, (south, west, north, east)))

    if not candidates:
        return None
    best = max(candidates, key=lambda item: item[0])[1]
    return _clip_to_mainland(best)


def geocode_province_bbox(province: dict) -> tuple[float, float, float, float]:
    """Lấy bbox tỉnh qua Nominatim. Thử tên hiện hành trước, rồi lần lượt
    từng legacy alias nếu tên hiện hành không khớp relation nào đủ lớn
    (tỉnh gộp mà OSM chưa cập nhật ranh giới hợp nhất — xem
    _ACCEPTED_RELATION_TYPES). Fail loud (FetchError) nếu tất cả đều rỗng
    — không âm thầm bỏ qua tỉnh.

    RỦI RO TỒN DƯ đã biết: khi phải fallback sang MỘT legacy alias, bbox
    trả về chỉ phủ ĐÚNG lãnh thổ tỉnh cũ đó (vd chỉ Bắc Giang cũ, không có
    Bắc Ninh cũ) — không phải toàn bộ tỉnh gộp hiện hành. Chấp nhận được vì
    (a) vẫn tốt hơn 0 dữ liệu, (b) ghi rõ trong SOURCES.md để biết coverage
    thật sự hẹp hơn ranh giới hành chính đầy đủ ở những tỉnh này.
    """
    name_current = province["name_current"]

    bbox = _fetch_nominatim_bbox(f"{name_current}, Vietnam")
    if bbox is not None:
        return bbox

    for alias in province["aliases"]["legacy"]:
        bbox = _fetch_nominatim_bbox(f"{alias['name']}, Vietnam")
        if bbox is not None:
            return bbox

    raise FetchError(
        f"Nominatim không tìm được bbox cho tỉnh {province['province_id']!r} "
        f"({name_current!r}), kể cả sau khi thử mọi legacy alias."
    )


def build_query(bbox: tuple[float, float, float, float], tag_config: dict) -> str:
    south, west, north, east = bbox
    bbox_str = f"{south},{west},{north},{east}"
    clauses: list[str] = []

    for entry in tag_config["poi_tags"]:
        key, _, value = entry["osm_tag"].partition("=")
        filt = f'["{key}"="{value}"]'
        if tag_config.get("require_name", True):
            filt += '["name"]'
        if "require_if" in entry:
            rk, _, rv = entry["require_if"].partition("=")
            filt += f'["{rk}"="{rv}"]'
        if "exclude_if" in entry:
            xk, _, xv = entry["exclude_if"].partition("=")
            filt += f'["{xk}"!="{xv}"]'
        clauses.append(f"nwr{filt}({bbox_str});")

    highway_values = "|".join(tag_config["street_highway_map"].keys())
    name_filt = '["name"]' if tag_config.get("require_name", True) else ""
    clauses.append(f'way["highway"~"^({highway_values})$"]{name_filt}({bbox_str});')

    body = "\n  ".join(clauses)
    return (
        f"[out:json][timeout:{OVERPASS_QUERY_TIMEOUT_SECONDS}];\n"
        f"(\n  {body}\n);\nout center tags;"
    )


def fetch_overpass(query: str) -> dict:
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    req = urllib.request.Request(
        OVERPASS_URL, data=data, headers={"User-Agent": USER_AGENT}
    )

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=OVERPASS_HTTP_TIMEOUT_SECONDS) as resp:
                raw = resp.read()
            payload = json.loads(raw)
            if "elements" not in payload:
                raise FetchError(f"Response thiếu key 'elements': {payload!r}")
            remark = payload.get("remark", "")
            if not payload["elements"] and remark:
                # Overpass trả JSON hợp lệ NHƯNG rỗng kèm remark giải thích
                # lỗi thực thi (thường là "Query timed out ... after Ns") —
                # coi như lỗi tạm thời, retry thay vì trả 0 elements âm thầm
                # (fetch_province() sẽ hiểu nhầm thành "tỉnh không có POI").
                raise FetchError(f"Overpass trả remark lỗi: {remark!r}")
            return payload
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            http.client.HTTPException,  # bao gồm IncompleteRead — quan sát thực tế
            # khi kết nối bị ngắt giữa chừng lúc đọc response lớn, không phải
            # subclass của URLError nên phải bắt riêng, nếu không cả batch
            # --all sẽ crash chỉ vì MỘT tỉnh gặp trục trặc mạng thoáng qua.
            json.JSONDecodeError,
            FetchError,
        ) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise FetchError(f"Overpass request thất bại sau {MAX_RETRIES} lần thử: {last_error}")


def _split_bbox(
    bbox: tuple[float, float, float, float], max_area: float
) -> list[tuple[float, float, float, float]]:
    """Chia bbox thành lưới ô vuông con nếu diện tích vượt max_area — mỗi ô
    con fetch trong MỘT request Overpass riêng (xem fetch_province()).
    n×n ô, n chọn tối thiểu để mỗi ô ≤ max_area."""
    south, west, north, east = bbox
    height = north - south
    width = east - west
    area = height * width
    if area <= max_area:
        return [bbox]

    n = math.ceil(math.sqrt(area / max_area))
    lat_step = height / n
    lon_step = width / n
    tiles: list[tuple[float, float, float, float]] = []
    for i in range(n):
        for j in range(n):
            tiles.append(
                (
                    south + i * lat_step,
                    west + j * lon_step,
                    south + (i + 1) * lat_step,
                    west + (j + 1) * lon_step,
                )
            )
    return tiles


def fetch_province(
    province_id: str,
    bbox: tuple[float, float, float, float],
    tag_config: dict,
    out_dir: Path,
) -> Path:
    """Fetch toàn bộ elements trong bbox — chia lưới ô con nếu bbox lớn hơn
    MAX_TILE_AREA_DEG2 (Overpass public instance 504 Gateway Timeout trên
    bbox nguyên tỉnh gộp, xem OVERPASS_QUERY_TIMEOUT_SECONDS). Element gần
    ranh giới ô có thể match ở nhiều ô cạnh nhau — dedupe theo (type, id)
    trước khi ghi, giữ occurrence đầu tiên (deterministic vì tiles duyệt
    theo thứ tự cố định)."""
    tiles = _split_bbox(bbox, MAX_TILE_AREA_DEG2)

    seen_refs: set[str] = set()
    elements: list[dict] = []
    for idx, tile in enumerate(tiles):
        query = build_query(tile, tag_config)
        payload = fetch_overpass(query)
        for el in payload.get("elements", []):
            ref = f"{el['type']}/{el['id']}"
            if ref in seen_refs:
                continue
            seen_refs.add(ref)
            elements.append(el)
        if idx < len(tiles) - 1:
            time.sleep(INTER_REQUEST_SLEEP_SECONDS)

    if not elements:
        raise FetchError(
            f"Province {province_id}: Overpass trả 0 elements trên {len(tiles)} ô "
            "— fail loud thay vì ghi snapshot rỗng. Kiểm tra lại bbox hoặc tag filter."
        )

    snapshot = {
        "province_id": province_id,
        "bbox": list(bbox),
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "overpass_url": OVERPASS_URL,
        "tile_count": len(tiles),
        "element_count": len(elements),
        "elements": elements,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{province_id}.json"
    out_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return out_path


def _fetch_one(province_id: str, bbox, tag_config: dict, out_dir: Path) -> int:
    try:
        out_path = fetch_province(province_id, bbox, tag_config, out_dir)
    except FetchError as exc:
        print(f"FAIL {province_id}: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {out_path} ({out_path.stat().st_size} bytes)")
    return 0


def _run_batch(
    provinces_path: Path, tag_config: dict, out_dir: Path, force: bool
) -> int:
    """Một tỉnh lỗi (network hiccup, Overpass 504 thoáng qua, bbox không
    geocode được...) KHÔNG BAO GIỜ được làm crash cả batch — --all chạy
    không giám sát, có thể mất hàng chục phút; try/except Exception rộng ở
    đây là lưới an toàn cuối cùng bên cạnh retry nội bộ của
    fetch_overpass(), phòng lỗi phát sinh chưa lường trước (đã xảy ra thật:
    http.client.IncompleteRead làm crash toàn bộ tiến trình giữa batch)."""
    provinces = load_provinces(provinces_path)
    failures = 0
    for province in provinces:
        province_id = province["province_id"]
        out_path = out_dir / f"{province_id}.json"
        if out_path.exists() and not force:
            print(f"SKIP {province_id}: snapshot đã tồn tại ({out_path})")
            continue

        try:
            if province_id in SAMPLE_PROVINCE_BBOX:
                bbox = SAMPLE_PROVINCE_BBOX[province_id]
            else:
                bbox = geocode_province_bbox(province)
            failures += _fetch_one(province_id, bbox, tag_config, out_dir)
        except Exception as exc:  # noqa: BLE001 — lưới an toàn cuối, xem docstring
            print(f"FAIL {province_id}: lỗi không lường trước — {type(exc).__name__}: {exc}", file=sys.stderr)
            failures += 1

        time.sleep(INTER_REQUEST_SLEEP_SECONDS)

    return 1 if failures else 0


# ---------------------------------------------------------------------------
# --from-pbf: ingest offline từ Geofabrik extract, thay thế Overpass API.
#
# LÝ DO: Overpass public instance (OVERPASS_URL) không đủ tin cậy để fetch
# đủ 34 tỉnh trong một phiên — rate limit 2 slot/instance (thấy thực tế
# "Slot available after: ... 47 giây"), 504 Gateway Timeout THOÁNG QUA
# (cùng query fail rồi thành công vài giây sau khi thử lại), và kết nối bị
# ngắt giữa chừng (IncompleteRead/RemoteDisconnected). Geofabrik cung cấp
# snapshot .pbf toàn quốc, cập nhật hàng ngày, tải một lần rồi lọc OFFLINE
# bằng pyosmium — không rate limit, không phụ thuộc mạng lúc build.
# ---------------------------------------------------------------------------


def _poi_tag_matches(tags: dict[str, str], rule: dict[str, Any], require_name: bool) -> bool:
    """Cùng tiêu chí lọc một `poi_tags` rule mà build_query() áp cho Overpass
    QL — PHẢI giữ đồng bộ tay với build_query() nếu osm_tags.yaml đổi cấu
    trúc rule. Không áp `extra_filter` (build_query() cũng không áp — chỉ
    build_master.build_pois() áp lúc map category, xem đó), giữ đúng phạm vi
    over-fetch-rồi-lọc-sau như đường Overpass cũ, tránh lệch hành vi giữa
    hai nguồn ingest."""
    key, _, value = rule["osm_tag"].partition("=")
    if tags.get(key) != value:
        return False
    if require_name and not tags.get("name"):
        return False
    if "require_if" in rule:
        rk, _, rv = rule["require_if"].partition("=")
        if tags.get(rk) != rv:
            return False
    if "exclude_if" in rule:
        xk, _, xv = rule["exclude_if"].partition("=")
        if tags.get(xk) == xv:
            return False
    return True


def _highway_tag_matches(
    tags: dict[str, str], highway_values: frozenset[str], require_name: bool
) -> bool:
    if tags.get("highway") not in highway_values:
        return False
    if require_name and not tags.get("name"):
        return False
    return True


def _way_center(node_locations: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Center = trung điểm bbox của các node hợp lệ thuộc way — cùng định
    nghĩa "center" mà Overpass `out center` dùng (KHÔNG phải centroid trung
    bình), để hai nguồn ingest cho ra toạ độ nhất quán."""
    if not node_locations:
        return None
    lats = [lat for lat, _ in node_locations]
    lons = [lon for _, lon in node_locations]
    return ((min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2)


def iter_matching_elements_from_pbf(
    pbf_path: Path, tag_config: dict
) -> Iterator[dict[str, Any]]:
    """Một lượt duyệt qua toàn bộ file .pbf, yield mỗi element (node/way)
    khớp `poi_tags` hoặc `street_highway_map` trong tag_config — cùng tiêu
    chí lọc mà build_query() dùng cho Overpass QL (xem _poi_tag_matches()).
    Format mỗi dict giống hệt element Overpass trả về (`type`, `id`, `tags`,
    cộng `lat`/`lon` cho node hoặc `center` cho way) — build_master.py không
    cần phân biệt nguồn snapshot.

    KHÔNG xử lý relation (multipolygon...) — poi_tags/street_highway_map
    hiện tại chỉ target node/way, giống hệt phạm vi `nwr`/`way` trong
    Overpass QL cũ (nwr thật ra có bao relation, nhưng osm_tags.yaml hiện
    tại chưa có rule nào cần dữ liệu ở dạng relation — residual gap giống
    nhau ở cả hai nguồn, không phải lỗi mới của đường pbf).
    """
    import osmium  # lazy import — chỉ cần khi dùng --from-pbf (CLAUDE.md
    # "chỉ thêm dependency khi thật sự cần", pyosmium khá nặng)

    require_name = tag_config.get("require_name", True)
    poi_tags = tag_config["poi_tags"]
    highway_values = frozenset(tag_config["street_highway_map"].keys())

    fp = (
        osmium.FileProcessor(str(pbf_path))
        .with_locations()
        .with_filter(osmium.filter.EmptyTagFilter())
    )
    for obj in fp:
        tags = dict(obj.tags)

        if obj.is_node():
            if not obj.location.valid():
                continue
            for rule in poi_tags:
                if _poi_tag_matches(tags, rule, require_name):
                    yield {
                        "type": "node",
                        "id": obj.id,
                        "lat": obj.location.lat,
                        "lon": obj.location.lon,
                        "tags": tags,
                    }
                    break
        elif obj.is_way():
            matched_poi = any(_poi_tag_matches(tags, rule, require_name) for rule in poi_tags)
            matched_highway = _highway_tag_matches(tags, highway_values, require_name)
            if not matched_poi and not matched_highway:
                continue
            locs = [(nr.location.lat, nr.location.lon) for nr in obj.nodes if nr.location.valid()]
            center = _way_center(locs)
            if center is None:
                continue
            yield {
                "type": "way",
                "id": obj.id,
                "center": {"lat": center[0], "lon": center[1]},
                "tags": tags,
            }


def _bbox_contains(bbox: tuple[float, float, float, float], lat: float, lon: float) -> bool:
    south, west, north, east = bbox
    return south <= lat <= north and west <= lon <= east


def _element_latlon(element: dict[str, Any]) -> tuple[float, float]:
    if element["type"] == "node":
        return element["lat"], element["lon"]
    return element["center"]["lat"], element["center"]["lon"]


def ingest_from_pbf(
    pbf_path: Path,
    provinces_path: Path,
    tag_config: dict,
    out_dir: Path,
    counter: Counter[str],
) -> list[str]:
    """Lọc TOÀN BỘ 34 tỉnh trong MỘT lượt duyệt file .pbf — mỗi element
    khớp tag filter được gán vào ĐÚNG MỘT tỉnh (tỉnh có bbox center gần toạ
    độ element nhất, trong số các tỉnh mà bbox chứa toạ độ đó) rồi ghi ra
    snapshot JSON đúng format cũ. Gán trực tiếp một-tỉnh-một-lần ở đây (thay
    vì ghi trùng vào mọi tỉnh overlap rồi để build_master.py dedupe như
    đường Overpass cũ) vì với .pbf ta xử lý toàn bộ 34 tỉnh cùng lúc nên
    biết ngay tập overlap đầy đủ — không cần vòng dedupe riêng nữa, dù
    build_master.dedupe_cross_province_osm_elements() vẫn chạy vô hại
    (không tìm thấy gì để dedupe) nếu module khác gọi lại đường Overpass.

    Trả về danh sách issue (tỉnh có 0 element khớp) — KHÔNG raise, để
    caller quyết định fail loud hay chấp nhận (một số tỉnh nhỏ/thưa dữ liệu
    OSM có thể hợp lệ có ít/0 kết quả thật)."""
    provinces = load_provinces(provinces_path)
    print(f"Geocode bbox cho {len(provinces)} tỉnh qua Nominatim...", file=sys.stderr)
    bbox_by_province: dict[str, tuple[float, float, float, float]] = {}
    issues: list[str] = []
    for province in provinces:
        province_id = province["province_id"]
        try:
            if province_id in SAMPLE_PROVINCE_BBOX:
                bbox_by_province[province_id] = SAMPLE_PROVINCE_BBOX[province_id]
            else:
                bbox_by_province[province_id] = geocode_province_bbox(province)
        except FetchError as exc:
            issues.append(f"Tỉnh {province_id!r}: không geocode được bbox — {exc}")

    print(
        f"Duyệt {pbf_path} cho {len(bbox_by_province)} tỉnh đã có bbox...",
        file=sys.stderr,
    )
    elements_by_province: dict[str, list[dict[str, Any]]] = {pid: [] for pid in bbox_by_province}
    for element in iter_matching_elements_from_pbf(pbf_path, tag_config):
        lat, lon = _element_latlon(element)
        candidates = [
            pid for pid, bbox in bbox_by_province.items() if _bbox_contains(bbox, lat, lon)
        ]
        if not candidates:
            continue
        if len(candidates) == 1:
            best = candidates[0]
        else:
            best = min(
                candidates,
                key=lambda pid: (
                    lambda c: (c[0] - lat) ** 2 + (c[1] - lon) ** 2
                )(_bbox_center_point(bbox_by_province[pid])),
            )
            counter["pbf.cross_province_ambiguous"] += 1
        elements_by_province[best].append(element)
        counter["pbf.matched_elements"] += 1

    out_dir.mkdir(parents=True, exist_ok=True)
    for province_id, elements in elements_by_province.items():
        if not elements:
            issues.append(f"Tỉnh {province_id!r}: 0 element khớp trong .pbf (bbox {bbox_by_province[province_id]})")
            continue
        snapshot = {
            "province_id": province_id,
            "bbox": list(bbox_by_province[province_id]),
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": f"pbf:{pbf_path.name}",
            "element_count": len(elements),
            "elements": elements,
        }
        out_path = out_dir / f"{province_id}.json"
        out_path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        counter["pbf.provinces_written"] += 1
        print(f"OK: {out_path} ({len(elements)} elements)")

    return issues


def _bbox_center_point(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    south, west, north, east = bbox
    return (south + north) / 2, (west + east) / 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--province-id")
    parser.add_argument(
        "--bbox",
        type=str,
        default=None,
        help="south,west,north,east — mặc định tra SAMPLE_PROVINCE_BBOX",
    )
    parser.add_argument(
        "--all", action="store_true", help="fetch mọi tỉnh còn thiếu snapshot"
    )
    parser.add_argument(
        "--force", action="store_true", help="dùng với --all: fetch lại cả tỉnh đã có snapshot"
    )
    parser.add_argument(
        "--from-pbf",
        type=Path,
        default=None,
        help=(
            "ingest offline cả 34 tỉnh từ Geofabrik .pbf đã tải sẵn (thay thế "
            "--all/--province-id — xem docstring module)"
        ),
    )
    parser.add_argument(
        "--provinces-path", type=Path, default=Path("data/raw/admin/provinces.yaml")
    )
    parser.add_argument(
        "--tag-config", type=Path, default=Path("config/osm_tags.yaml")
    )
    parser.add_argument("--out-dir", type=Path, default=Path("data/raw/osm"))
    args = parser.parse_args(argv)

    tag_config = load_tag_config(args.tag_config)

    if args.from_pbf:
        counter: Counter[str] = Counter()
        issues = ingest_from_pbf(
            args.from_pbf, args.provinces_path, tag_config, args.out_dir, counter
        )
        for issue in issues:
            print(f"WARN: {issue}", file=sys.stderr)
        print(f"Summary: {dict(counter)}")
        return 1 if counter["pbf.provinces_written"] == 0 else 0

    if args.all:
        return _run_batch(args.provinces_path, tag_config, args.out_dir, args.force)

    if not args.province_id:
        parser.error("cần --province-id hoặc --all")
        return 2

    if args.bbox:
        parts = [float(x) for x in args.bbox.split(",")]
        if len(parts) != 4:
            parser.error("--bbox cần đúng 4 giá trị: south,west,north,east")
        bbox = (parts[0], parts[1], parts[2], parts[3])
    elif args.province_id in SAMPLE_PROVINCE_BBOX:
        bbox = SAMPLE_PROVINCE_BBOX[args.province_id]
    else:
        provinces = load_provinces(args.provinces_path)
        province = next(
            (p for p in provinces if p["province_id"] == args.province_id), None
        )
        if province is None:
            parser.error(
                f"province-id {args.province_id!r} không tồn tại trong "
                f"{args.provinces_path}."
            )
            return 2
        try:
            bbox = geocode_province_bbox(province)
        except FetchError as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1

    result = _fetch_one(args.province_id, bbox, tag_config, args.out_dir)
    time.sleep(INTER_REQUEST_SLEEP_SECONDS)
    return result


if __name__ == "__main__":
    sys.exit(main())
