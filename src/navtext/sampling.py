"""Sinh plan table quota-driven từ config/distribution.yaml + master data.

Phase 3. Xem docs/sampling.md. Toàn bộ hàm ở đây DETERMINISTIC — không nhận
`rng`, vì random chỉ xảy ra bên trong cell (ở phase 6), không phải khi phân
bổ target_count cho từng cell.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Mapping

from navtext.loaders import MasterData
from navtext.schema import Category, Cell, LengthClass, Register, SentenceType

# Số vòng lặp cố định cho iterative proportional fitting (IPF) — tham số
# thuật toán số học, không phải ngưỡng tỷ trọng dataset, nên không thuộc
# phạm vi hard rule #8 (những con số đó sống trong config/distribution.yaml).
_IPF_ITERATIONS = 200
_TOLERANCE = 1e-6


class PlanError(Exception):
    """Raise với TOÀN BỘ danh sách violation cùng lúc — theo mẫu
    build_master.BuildError, không fail-fast ở violation đầu tiên.
    """

    def __init__(self, issues: list[str]) -> None:
        self.issues = issues
        super().__init__("\n".join(f"- {i}" for i in issues))


# ---------------------------------------------------------------------------
# Pure math helpers
# ---------------------------------------------------------------------------


def normalize_weights(weights: Mapping[str, float]) -> dict[str, float]:
    """Chuẩn hoá trọng số thô về share cộng đúng bằng 1.0, không áp trần/sàn."""
    total = sum(weights.values())
    if total <= 0:
        raise PlanError([f"tổng trọng số phải > 0, nhận {total}"])
    return {k: v / total for k, v in weights.items()}


def clamped_shares(weights: Mapping[str, float], min_share: float, max_share: float) -> dict[str, float]:
    """Chuẩn hoá trọng số thô về share, kẹp mỗi phần tử trong [min_share,
    max_share] bằng water-filling: phần tử vượt trần/dưới sàn bị kẹp cứng,
    phần dư chia lại cho phần tử còn tự do, lặp tới hội tụ.

    Raise PlanError nếu bất khả thi (vd n phần tử * min_share > 1).
    """
    keys = sorted(weights)
    if not keys:
        raise PlanError(["clamped_shares: không có phần tử nào để chia share"])

    n = len(keys)
    if n * min_share > 1.0 + _TOLERANCE:
        raise PlanError(
            [
                f"clamped_shares: bất khả thi — {n} phần tử * sàn {min_share} "
                f"= {n * min_share} > 1.0"
            ]
        )
    if n * max_share < 1.0 - _TOLERANCE:
        raise PlanError(
            [
                f"clamped_shares: bất khả thi — {n} phần tử * trần {max_share} "
                f"= {n * max_share} < 1.0"
            ]
        )

    w = {k: max(weights[k], 0.0) for k in keys}
    fixed: dict[str, float] = {}
    free = set(keys)

    for _ in range(n + 1):
        remaining_mass = 1.0 - sum(fixed.values())
        free_weight_total = sum(w[k] for k in free)
        if free_weight_total <= 0:
            # không còn trọng số thô để phân bổ — chia đều phần dư.
            share_each = remaining_mass / len(free) if free else 0.0
            for k in free:
                fixed[k] = share_each
            free = set()
            break

        changed = False
        for k in list(free):
            s = remaining_mass * (w[k] / free_weight_total)
            if s < min_share - _TOLERANCE:
                fixed[k] = min_share
                free.discard(k)
                changed = True
            elif s > max_share + _TOLERANCE:
                fixed[k] = max_share
                free.discard(k)
                changed = True

        if not changed:
            for k in free:
                fixed[k] = remaining_mass * (w[k] / free_weight_total)
            free = set()
            break

    return {k: fixed[k] for k in keys}


def largest_remainder(shares: Mapping[Any, float], total: int) -> dict[Any, int]:
    """Làm tròn share -> số nguyên sao cho tổng ĐÚNG bằng total (largest
    remainder method). Deterministic: tie-break theo key (sort ổn định),
    không phụ thuộc thứ tự dict đầu vào hay PYTHONHASHSEED.
    """
    keys = sorted(shares)
    raw = {k: shares[k] * total for k in keys}
    floor_vals = {k: math.floor(raw[k]) for k in keys}
    remainder = total - sum(floor_vals.values())

    if remainder > 0:
        order = sorted(keys, key=lambda k: (-(raw[k] - floor_vals[k]), k))
        for k in order[:remainder]:
            floor_vals[k] += 1
    elif remainder < 0:
        # Có thể xảy ra khi share âm/lỗi làm tròn ngược — bớt từ phần tử có
        # phần dư nhỏ nhất trước, vẫn deterministic.
        order = sorted(keys, key=lambda k: ((raw[k] - floor_vals[k]), k))
        for k in order[: -remainder]:
            floor_vals[k] -= 1

    return floor_vals


# ---------------------------------------------------------------------------
# Entity pool — feasibility layer
# ---------------------------------------------------------------------------


_SPEAKABLE_RANGES: tuple[tuple[int, int], ...] = (
    (0x0000, 0x024F),  # Basic Latin + Latin-1 Supplement + Latin Extended A/B
    (0x1E00, 0x1EFF),  # Latin Extended Additional — phần lớn dấu tiếng Việt
)


def _is_speakable(name: str) -> bool:
    """Loại tên chứa chữ cái ngoài Latin cơ bản + dấu tiếng Việt (vd chữ
    Nhật/Hàn/Cyrillic lẫn trong OSM POI) — TTS/người đọc không đọc được,
    và không nên tính vào entity pool khả thi. Lọc ở đây (không phải ở QC
    phase 11) để feasibility của build_plan phản ánh đúng thực tế: entity
    không đọc được coi như không tồn tại ngay từ bước lập plan.

    Giữ nguyên thương hiệu Latin không dấu (BIDV, WinMart, GO!) — chúng vẫn
    đọc được bình thường trong tiếng Việt nói.
    """
    if not name or not name.strip():
        return False
    for ch in name:
        if not ch.isalpha():
            continue
        code = ord(ch)
        if not any(lo <= code <= hi for lo, hi in _SPEAKABLE_RANGES):
            return False
    return True


def entity_pool(master: MasterData, province_id: str, category: Category) -> tuple:
    """Trả về mọi LocationRef khả thi cho (province, category) này trong
    master data hiện có — dùng để quyết định cell nào KHẢ THI trước khi
    build_plan gán target_count (docs/sampling.md#nguyên-tắc-quota-driven).

    admin_unit: tên tỉnh (luôn có) + district/ward thuộc tỉnh nếu có.
    address: dùng chung support với street — số nhà synthesize lúc generate
    (phase 6), không có entity address riêng trong master.
    Các category POI khác: lọc entities_by_province theo category.

    Mọi nhánh đều lọc qua _is_speakable() trước khi trả về.
    """
    if category == Category.ADMIN_UNIT:
        from navtext.schema import LocationRef, Source, Subcategory

        entities = []
        province = master.provinces.get(province_id)
        if province is not None:
            entities.append(
                LocationRef(
                    entity_id=f"province_{province_id}",
                    name=province.name_current,
                    category=Category.ADMIN_UNIT,
                    subcategory=Subcategory.PROVINCE,
                    province_id=province_id,
                    district_id=None,
                    ward_id=None,
                    street_id=None,
                    popularity_tier=province.popularity_tier,
                    source=Source.MANUAL,
                )
            )
        for district in master.districts.values():
            if district.province_id != province_id:
                continue
            entities.append(
                LocationRef(
                    entity_id=district.district_id,
                    name=district.name,
                    category=Category.ADMIN_UNIT,
                    subcategory=Subcategory.DISTRICT,
                    province_id=province_id,
                    district_id=district.district_id,
                    ward_id=None,
                    street_id=None,
                    popularity_tier=2,
                    source=Source.MANUAL,
                    legacy_alias_hint=district.legacy_alias_hint,
                )
            )
        for ward in master.wards.values():
            # Ward nối lên tỉnh theo MỘT trong hai đường (xem
            # build_master.build_wards): qua district cha (nguồn curate tay)
            # hoặc thẳng qua province_id (nguồn OSM, mô hình 2 cấp hiện
            # hành). Cả hai đường đều có thể mang sub-region hint: đường
            # district kế thừa từ district cha; đường OSM mang
            # ward.legacy_alias_hint của chính nó (scripts/extract_wards.py
            # — point-in-polygon trên ranh giới tỉnh cũ khi còn tồn tại
            # trong .pbf, phần lớn KHÔNG còn nên hint rỗng là bình thường,
            # không phải lỗi — xem SOURCES.md#wards_osmcsv).
            district = master.districts.get(ward.district_id) if ward.district_id else None
            if district is not None:
                if district.province_id != province_id:
                    continue
                legacy_alias_hint = district.legacy_alias_hint
            elif ward.province_id == province_id:
                legacy_alias_hint = ward.legacy_alias_hint
            else:
                continue
            entities.append(
                LocationRef(
                    entity_id=ward.ward_id,
                    name=ward.name,
                    category=Category.ADMIN_UNIT,
                    subcategory=Subcategory.WARD,
                    province_id=province_id,
                    district_id=ward.district_id,
                    ward_id=ward.ward_id,
                    street_id=None,
                    popularity_tier=3,
                    source=Source.MANUAL,
                    legacy_alias_hint=legacy_alias_hint,
                )
            )
        return tuple(e for e in entities if _is_speakable(e.name))

    if category == Category.ADDRESS:
        category = Category.STREET

    return tuple(
        e
        for e in master.entities_by_province.get(province_id, ())
        if e.category == category and _is_speakable(e.name)
    )


# ---------------------------------------------------------------------------
# build_plan — province x category block shares (IPF) -> cell shares
# ---------------------------------------------------------------------------


def _block_shares(config: Mapping[str, Any], master: MasterData) -> dict[tuple[str, str], float]:
    """IPF trên ma trận (province, category) restricted tới các ô khả thi
    (entity_pool khác rỗng), khớp đồng thời marginal province và marginal
    category. Raise PlanError nếu kết quả cuối lệch khỏi trần/sàn gốc của
    một trong hai trục — nghĩa là support khả thi không đủ để thoả cả hai
    (vd category_weights quá thấp cho category duy nhất mà nhiều tỉnh nhỏ
    buộc phải dùng).
    """
    provinces = sorted(master.provinces)
    categories = sorted(config["category_weights"])

    province_caps = config["caps"]["province"]
    category_caps = config["caps"]["category"]

    overrides = config["province_weights"].get("overrides") or {}
    unknown_override_ids = set(overrides) - set(provinces)
    if unknown_override_ids:
        raise PlanError(
            [
                f"build_plan: province_weights.overrides có province_id không tồn tại "
                f"trong master data: {sorted(unknown_override_ids)!r} — "
                "kiểm tra config/distribution.yaml"
            ]
        )

    province_weight = {}
    for pid in provinces:
        p = master.provinces[pid]
        weight = float(config["province_weights"]["by_tier"][p.popularity_tier])
        if p.is_municipality:
            weight *= float(config["province_weights"]["municipality_bonus"])
        weight *= float(overrides.get(pid, 1.0))
        province_weight[pid] = weight
    province_share = clamped_shares(province_weight, province_caps["min_share"], province_caps["max_share"])

    category_weight = {c: float(config["category_weights"][c]) for c in categories}
    category_share = clamped_shares(category_weight, category_caps["min_share"], category_caps["max_share"])

    feasible: set[tuple[str, str]] = set()
    for pid in provinces:
        for cat in categories:
            if entity_pool(master, pid, Category(cat)):
                feasible.add((pid, cat))

    if not feasible:
        raise PlanError(["build_plan: không có (province, category) nào khả thi trong master data"])

    feasible_categories = {cat for _, cat in feasible}
    missing_categories = set(categories) - feasible_categories
    if missing_categories:
        raise PlanError(
            [
                f"build_plan: category {cat!r} không có entity nào trong bất kỳ tỉnh nào — "
                "fetch thêm OSM data cho category này hoặc bỏ khỏi category_weights"
                for cat in sorted(missing_categories)
            ]
        )

    matrix = {(pid, cat): province_share[pid] * category_share[cat] for pid, cat in feasible}

    for _ in range(_IPF_ITERATIONS):
        row_sum: dict[str, float] = defaultdict(float)
        for (pid, cat), v in matrix.items():
            row_sum[pid] += v
        for pid, cat in matrix:
            if row_sum[pid] > 0:
                matrix[(pid, cat)] *= province_share[pid] / row_sum[pid]

        col_sum: dict[str, float] = defaultdict(float)
        for (pid, cat), v in matrix.items():
            col_sum[cat] += v
        for pid, cat in matrix:
            if col_sum[cat] > 0:
                matrix[(pid, cat)] *= category_share[cat] / col_sum[cat]

    total = sum(matrix.values())
    if total <= 0:
        raise PlanError(["build_plan: IPF hội tụ về tổng khối lượng 0 — kiểm tra category_weights/province_weights"])
    matrix = {k: v / total for k, v in matrix.items()}

    issues: list[str] = []
    achieved_province: dict[str, float] = defaultdict(float)
    achieved_category: dict[str, float] = defaultdict(float)
    for (pid, cat), v in matrix.items():
        achieved_province[pid] += v
        achieved_category[cat] += v

    for cat in categories:
        share = achieved_category.get(cat, 0.0)
        if share > category_caps["max_share"] + _TOLERANCE:
            issues.append(
                f"build_plan: category {cat!r} bị ép lên {share:.4f} (trần {category_caps['max_share']}) "
                "— các tỉnh chỉ khả thi ở category này đang chiếm quá nhiều mass; "
                f"tăng caps.category.max_share hoặc giảm province_weights của các tỉnh đó trong config/distribution.yaml"
            )
        if share < category_caps["min_share"] - _TOLERANCE:
            issues.append(
                f"build_plan: category {cat!r} chỉ đạt {share:.4f} (sàn {category_caps['min_share']}) "
                "— tăng category_weights cho category này trong config/distribution.yaml"
            )

    for pid in provinces:
        share = achieved_province.get(pid, 0.0)
        if share > province_caps["max_share"] + _TOLERANCE:
            scarce_categories = sorted(
                cat
                for cat in categories
                if (pid, cat) in feasible and sum(1 for p2 in provinces if (p2, cat) in feasible) <= 5
            )
            issues.append(
                f"build_plan: province {pid!r} bị ép lên {share:.4f} (trần {province_caps['max_share']}) "
                f"— category {scarce_categories} chỉ khả thi ở rất ít tỉnh (thiếu OSM data cho tỉnh khác, xem "
                "docs/data-model.md#nguồn-dữ-liệu--build); fetch thêm data cho category đó ở tỉnh khác, hạ "
                "province_weights của tỉnh này, hoặc nới caps.province.max_share trong config/distribution.yaml"
            )
        if share < province_caps["min_share"] - _TOLERANCE:
            issues.append(
                f"build_plan: province {pid!r} chỉ đạt {share:.4f} (sàn {province_caps['min_share']}) "
                "— thiếu category khả thi để hấp thụ đủ province_weight của tỉnh này"
            )

    if issues:
        raise PlanError(issues)

    return matrix


def build_plan(config: dict[str, Any], master: MasterData) -> list[Cell]:
    """Sinh plan table: tích Descartes có kiểm soát của geography x category
    x sentence_type x variation_profile, mỗi cell có target_count tính từ
    config. Không random ở bước này — random chỉ xảy ra bên trong cell.
    """
    total = int(config["target_size"])
    min_cell_size = int(config.get("min_cell_size", 1))

    block_share = _block_shares(config, master)

    sentence_caps = config["caps"]["sentence_type"]
    sentence_weight = {st: float(w) for st, w in config["sentence_type_weights"].items()}
    sentence_share = clamped_shares(sentence_weight, sentence_caps["min_share"], sentence_caps["max_share"])

    profile_weight = {p["name"]: float(p["weight"]) for p in config["variation_profiles"]}
    profile_share = normalize_weights(profile_weight)

    cell_share: dict[tuple[str, str, str, str], float] = {}
    for (pid, cat), bshare in block_share.items():
        for st, sshare in sentence_share.items():
            for prof, pshare in profile_share.items():
                cell_share[(pid, cat, st, prof)] = bshare * sshare * pshare

    counts = largest_remainder(cell_share, total)

    survivors = {k: v for k, v in counts.items() if v >= min_cell_size}
    dropped_total = total - sum(survivors.values())
    if dropped_total > 0 and survivors:
        survivor_weight = {k: cell_share[k] for k in survivors}
        extra = largest_remainder(normalize_weights(survivor_weight), dropped_total)
        for k in survivors:
            survivors[k] += extra.get(k, 0)

    cells = [
        Cell(
            province_id=pid,
            category=Category(cat),
            sentence_type=SentenceType(st),
            variation_profile=prof,
            target_count=cnt,
        )
        for (pid, cat, st, prof), cnt in sorted(survivors.items())
        if cnt > 0
    ]
    return cells


def scale_plan(plan: list[Cell], total: int) -> list[Cell]:
    """Scale một plan đã build (thường ở target_size canonical) xuống/lên
    một total khác, giữ nguyên tỷ trọng qua cùng thuật toán largest_remainder
    dùng ở build_plan — prototype 1k / candidate 130k / final 100k không
    lệch tỷ trọng so với plan gốc.
    """
    original_total = sum(c.target_count for c in plan)
    if original_total <= 0:
        raise PlanError(["scale_plan: plan gốc có tổng target_count <= 0"])

    lookup = {c.cell_key: c for c in plan}
    shares = {c.cell_key: c.target_count / original_total for c in plan}
    counts = largest_remainder(shares, total)

    result = []
    for key in sorted(counts):
        cnt = counts[key]
        if cnt <= 0:
            continue
        c = lookup[key]
        result.append(
            Cell(
                province_id=c.province_id,
                category=c.category,
                sentence_type=c.sentence_type,
                variation_profile=c.variation_profile,
                target_count=cnt,
            )
        )
    return result


# ---------------------------------------------------------------------------
# validate_plan
# ---------------------------------------------------------------------------


def validate_plan(plan: list[Cell], config: dict[str, Any], master: MasterData, total: int) -> None:
    """Raise PlanError (gom toàn bộ violation) nếu tổng target_count != total,
    hoặc bất kỳ cell/trục nào vi phạm trần/sàn ở
    docs/sampling.md#distribution-caps, hoặc thiếu coverage 34/34 province,
    hoặc một cell đòi nhiều sample hơn trần entity cho phép từ entity pool
    của nó.
    """
    issues: list[str] = []
    min_cell_size = int(config.get("min_cell_size", 1))

    actual_total = sum(c.target_count for c in plan)
    if actual_total != total:
        issues.append(f"validate_plan: tổng target_count = {actual_total}, kỳ vọng {total}")

    for c in plan:
        if c.target_count < min_cell_size:
            issues.append(f"validate_plan: cell {c.cell_key!r} có target_count={c.target_count} < min_cell_size={min_cell_size}")

    def _check_axis(name: str, get_key, caps: Mapping[str, float]) -> None:
        totals: dict[str, int] = defaultdict(int)
        for c in plan:
            totals[get_key(c)] += c.target_count
        for key, count in totals.items():
            share = count / total if total > 0 else 0.0
            if share > caps["max_share"] + _TOLERANCE:
                issues.append(f"validate_plan: {name} {key!r} chiếm {share:.4f} > trần {caps['max_share']}")
            if share < caps["min_share"] - _TOLERANCE:
                issues.append(f"validate_plan: {name} {key!r} chiếm {share:.4f} < sàn {caps['min_share']}")

    _check_axis("province", lambda c: c.province_id, config["caps"]["province"])
    _check_axis("category", lambda c: c.category.value, config["caps"]["category"])
    _check_axis("sentence_type", lambda c: c.sentence_type.value, config["caps"]["sentence_type"])

    provinces_in_plan = {c.province_id for c in plan}
    missing_provinces = set(master.provinces) - provinces_in_plan
    if missing_provinces:
        issues.append(f"validate_plan: thiếu coverage cho province {sorted(missing_provinces)}")

    entity_max_share = config["caps"]["entity"]["max_share"]
    max_repeats = max(1, math.floor(entity_max_share * total))
    block_totals: dict[tuple[str, str], int] = defaultdict(int)
    for c in plan:
        block_totals[(c.province_id, c.category.value)] += c.target_count
    for (pid, cat), count in block_totals.items():
        pool_size = len(entity_pool(master, pid, Category(cat)))
        capacity = pool_size * max_repeats
        if pool_size == 0:
            issues.append(f"validate_plan: cell ({pid!r}, {cat!r}) có target_count={count} nhưng entity pool rỗng")
        elif count > capacity:
            issues.append(
                f"validate_plan: block ({pid!r}, {cat!r}) đòi {count} sample từ pool {pool_size} entity, "
                f"vượt trần entity {entity_max_share} (capacity={capacity}) — cần thêm entity vào master data "
                "hoặc giảm target_count của block này"
            )

    if issues:
        raise PlanError(issues)


# ---------------------------------------------------------------------------
# variation_profile -> (register, length_class) — nguồn duy nhất, phase 6
# ---------------------------------------------------------------------------


def resolve_profiles(config: Mapping[str, Any]) -> dict[str, tuple[Register, LengthClass]]:
    """Map `variation_profile` (tên gộp register+length_class, xem
    config/distribution.yaml#variation_profiles) ngược về hai enum gốc.
    `Cell` không mang register/length_class trực tiếp — đây là nguồn DUY
    NHẤT dùng để suy ra chúng; không parse từ chuỗi tên profile ở nơi khác
    (docs/sampling.md#variation_profile--trục-thứ-4-của-cell_key).
    """
    result: dict[str, tuple[Register, LengthClass]] = {}
    for p in config["variation_profiles"]:
        result[p["name"]] = (Register(p["register"]), LengthClass(p["length_class"]))
    return result


# ---------------------------------------------------------------------------
# plan.json serialize/deserialize — dùng chung giữa `navtext plan` (ghi) và
# `navtext generate` (đọc), xem docs/output.md#planjson.
# ---------------------------------------------------------------------------

_PLAN_SCHEMA_VERSION = 1


def plan_to_doc(
    plan: list[Cell],
    *,
    run_id: str,
    total: int,
    global_seed: int,
    config_hash: str,
    master_data_hash: str,
    generated_at: str,
) -> dict[str, Any]:
    """Sinh dict khớp đúng schema plan.json ở docs/output.md#planjson."""
    return {
        "schema_version": _PLAN_SCHEMA_VERSION,
        "run_id": run_id,
        "total": total,
        "global_seed": global_seed,
        "config_hash": config_hash,
        "master_data_hash": master_data_hash,
        "generated_at": generated_at,
        "cells": [
            {
                "province_id": c.province_id,
                "category": c.category.value,
                "sentence_type": c.sentence_type.value,
                "variation_profile": c.variation_profile,
                "target_count": c.target_count,
            }
            for c in plan
        ],
    }


def plan_from_doc(doc: Mapping[str, Any]) -> list[Cell]:
    """Đọc ngược một plan.json (đã parse JSON) thành list[Cell] — dùng bởi
    `navtext generate` để không phải build lại plan từ config mỗi lần chạy
    (docs/testing.md#reproduce-một-run yêu cầu tái dùng đúng plan đã lưu).
    """
    schema_version = doc.get("schema_version")
    if schema_version != _PLAN_SCHEMA_VERSION:
        raise PlanError(
            [f"plan_from_doc: schema_version {schema_version!r} không được hỗ trợ — phải là {_PLAN_SCHEMA_VERSION}"]
        )
    cells: list[Cell] = []
    for raw in doc.get("cells", []):
        cells.append(
            Cell(
                province_id=raw["province_id"],
                category=Category(raw["category"]),
                sentence_type=SentenceType(raw["sentence_type"]),
                variation_profile=raw["variation_profile"],
                target_count=int(raw["target_count"]),
            )
        )
    return cells
