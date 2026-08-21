"""Build statistics report.

Hai loại report, ĐỪNG lẫn vào nhau:

- `build_report(samples)` — phase 12, thống kê dataset câu đã sinh. Coverage
  34/34 + 63/63 ở đó nghĩa là "địa danh xuất hiện trong `text`", và là điều
  kiện PASS/FAIL để xuất final_100k.csv. Xem docs/output.md#statistics-report.
- `build_master_report(master, ...)` — thống kê MASTER DATA (bảng địa danh),
  phục vụ `navtext serve`. Coverage ở đây chỉ nghĩa là "tỉnh/alias có tồn tại
  trong bảng và có entity thuộc về nó" — điều kiện CẦN, yếu hơn nhiều. Không
  được trình bày kết quả của nó như thể đã đạt yêu cầu phase 12.

Module này không đọc file: caller truyền `MasterData` và config vào tường
minh (CLAUDE.md#code-conventions — không side-effect ở import time, và
không hardcode ngưỡng: `admin_invariants` đến từ config/run.yaml).
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

from navtext.loaders import MasterData
from navtext.schema import Category, LengthClass, NameVariant, Register, Sample, SentenceType, Subcategory
from navtext.templates import Template
from navtext.variation import Axis, VariationConfig

# Ghi chú đi kèm tier_distribution — build_master.build_pois() hiện gán cứng
# popularity_tier=2 cho mọi POI vì chưa có tín hiệu độ nổi tiếng thật, nên
# bảng phân bố tier chưa mang thông tin. Nói rõ trong report thay vì để người
# đọc tưởng đây là phân bố đã tính toán.
TIER_NOTE = (
    "Mọi POI từ OSM hiện mang popularity_tier=2 (giá trị default của "
    "build_pois) — phân bố này chưa mang thông tin cho tới khi có tín hiệu "
    "độ nổi tiếng thật."
)

MASTER_COVERAGE_NOTE = (
    "Đây là coverage ở tầng MASTER DATA (tỉnh/alias có tồn tại trong bảng, và "
    "có entity thuộc về nó), KHÔNG phải coverage 34/34 + 63/63 của "
    "docs/output.md — cái đó đo địa danh xuất hiện trong text của dataset "
    "cuối và là điều kiện PASS/FAIL của phase 12."
)

# Ghi chú cho trang /samples (navtext serve) — xem đúng tinh thần
# MASTER_COVERAGE_NOTE/VARIATION_WEIGHT_NOTE: một con số nhìn giống coverage
# chính thức nhưng KHÔNG PHẢI, phải nói rõ ngay trên UI để không ai nhầm.
SAMPLES_PREVIEW_NOTE = (
    "Đây là xem nhanh MỘT file CSV cụ thể do --samples chỉ định (vd "
    "prototype.csv/candidates.csv), KHÔNG phải coverage/QC chính thức của "
    "docs/output.md — coverage 34/34 + 63/63 chính thức chỉ đo trên "
    "final_100k.csv ở phase 12, sau khi qua đủ qc/balance/finalize."
)


def build_report(samples: list[Sample]) -> dict[str, Any]:
    """Tính toàn bộ số liệu bắt buộc: total, geographic/category/sentence_type/
    variation distribution, 34/34 + 63/63 coverage PASS/FAIL, entity coverage,
    duplicate rate, template distribution. Report này FAIL thì không được
    xuất final_100k.csv chính thức.
    """
    raise NotImplementedError


def build_master_report(
    master: MasterData, admin_invariants: Mapping[str, int]
) -> dict[str, Any]:
    """Thống kê master data. `admin_invariants` đến từ
    config/run.yaml#admin_invariants (expected_province_count,
    expected_legacy_alias_count) — không hardcode 34/63 ở đây (hard rule #8).
    """
    return {
        "totals": _totals(master),
        "master_coverage": _master_coverage(master, admin_invariants),
        "by_province": _by_province(master),
        "by_category": _by_category(master),
        "by_subcategory": _by_subcategory(master),
        "empty_subcategories": _empty_subcategories(master),
        "duplicate_entity_names": _duplicate_entity_names(master),
        "tier_distribution": _tier_distribution(master),
    }


def _totals(master: MasterData) -> dict[str, int]:
    return {
        "provinces": len(master.provinces),
        "province_alias": sum(len(v) for v in master.aliases_by_province.values()),
        "districts": len(master.districts),
        "wards": len(master.wards),
        "streets": len(master.streets),
        "pois": len(master.pois),
        "entities": sum(len(v) for v in master.entities_by_province.values()),
    }


def _alias_counts(master: MasterData) -> Counter[str]:
    counts: Counter[str] = Counter()
    for aliases in master.aliases_by_province.values():
        for alias in aliases:
            counts[alias.alias_type] += 1
    return counts


def _master_coverage(
    master: MasterData, admin_invariants: Mapping[str, int]
) -> dict[str, Any]:
    expected_provinces = admin_invariants["expected_province_count"]
    expected_legacy = admin_invariants["expected_legacy_alias_count"]
    alias_counts = _alias_counts(master)

    without_entities = [
        {"province_id": pid, "name_current": province.name_current}
        for pid, province in sorted(master.provinces.items())
        if not master.entities_by_province.get(pid)
    ]

    return {
        "note": MASTER_COVERAGE_NOTE,
        "province_count": len(master.provinces),
        "expected_province_count": expected_provinces,
        "province_count_ok": len(master.provinces) == expected_provinces,
        "legacy_alias_count": alias_counts["legacy"],
        "expected_legacy_alias_count": expected_legacy,
        "legacy_alias_count_ok": alias_counts["legacy"] == expected_legacy,
        "alias_counts_by_type": dict(sorted(alias_counts.items())),
        "provinces_with_entities": len(master.provinces) - len(without_entities),
        "provinces_without_entities": without_entities,
    }


def _by_province(master: MasterData) -> list[dict[str, Any]]:
    district_counts: Counter[str] = Counter(
        d.province_id for d in master.districts.values()
    )
    ward_counts: Counter[str] = Counter()
    for ward in master.wards.values():
        district = master.districts.get(ward.district_id)
        if district is not None:
            ward_counts[district.province_id] += 1
    street_counts: Counter[str] = Counter(
        s.province_id for s in master.streets.values()
    )
    poi_counts: Counter[str] = Counter(
        p.province_id for p in master.pois.values() if p.province_id
    )

    rows: list[dict[str, Any]] = []
    for pid, province in sorted(master.provinces.items()):
        alias_by_type: Counter[str] = Counter(
            a.alias_type for a in master.aliases_by_province.get(pid, ())
        )
        rows.append(
            {
                "province_id": pid,
                "name_current": province.name_current,
                "admin_status": province.admin_status,
                "region": province.region,
                "is_municipality": province.is_municipality,
                "popularity_tier": province.popularity_tier,
                "alias_counts": dict(sorted(alias_by_type.items())),
                "districts": district_counts[pid],
                "wards": ward_counts[pid],
                "streets": street_counts[pid],
                "pois": poi_counts[pid],
                "entities": len(master.entities_by_province.get(pid, ())),
            }
        )
    return rows


def _by_category(master: MasterData) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for entities in master.entities_by_province.values():
        for entity in entities:
            counts[entity.category.value] += 1
    return dict(sorted(counts.items()))


def _by_subcategory(master: MasterData) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for entities in master.entities_by_province.values():
        for entity in entities:
            counts[entity.subcategory.value] += 1
    return dict(sorted(counts.items()))


def _empty_subcategories(master: MasterData) -> list[str]:
    """Subcategory khai trong taxonomy nhưng master data không có entity nào.

    Tín hiệu cho phase 4: viết template cho subcategory này thì slot sẽ không
    có gì để fill.
    """
    present = set(_by_subcategory(master))
    return sorted(sub.value for sub in Subcategory if sub.value not in present)


def _duplicate_entity_names(
    master: MasterData, limit: int = 50
) -> list[dict[str, Any]]:
    """Tên trùng trong cùng một tỉnh.

    `build_master.build_streets()` đã dedupe street theo tên trong tỉnh, nên
    trùng còn lại chủ yếu là POI trùng tên thật (nhiều chi nhánh "BIDV", nhiều
    "Chợ Đêm"). Cần biết trước khi phase 6 áp trần entity 0,15%: nếu coi
    chúng là entity khác nhau thì cùng một cái tên có thể vượt trần mà không
    validator nào bắt được.
    """
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for pid, entities in master.entities_by_province.items():
        for entity in entities:
            grouped[(pid, entity.name)].append(entity.entity_id)

    duplicates = [
        {
            "province_id": pid,
            "name": name,
            "count": len(entity_ids),
            "entity_ids": sorted(entity_ids)[:5],
        }
        for (pid, name), entity_ids in grouped.items()
        if len(entity_ids) > 1
    ]
    duplicates.sort(key=lambda row: (-row["count"], row["province_id"], row["name"]))
    return duplicates[:limit]


def _tier_distribution(master: MasterData) -> dict[str, Any]:
    counts: Counter[int] = Counter(p.popularity_tier for p in master.pois.values())
    return {
        "note": TIER_NOTE,
        "poi_tiers": {str(tier): counts[tier] for tier in sorted(counts)},
        "province_tiers": {
            str(tier): count
            for tier, count in sorted(
                Counter(p.popularity_tier for p in master.provinces.values()).items()
            )
        },
    }


# ---------------------------------------------------------------------------
# Template bank report — phục vụ `navtext serve` /templates
# ---------------------------------------------------------------------------

_SLOT_RE = re.compile(r"\{[a-zA-Z_]+\}")


def _skeleton_length(text: str) -> int:
    """Độ dài phần khung chữ, bỏ mọi `{slot}`.

    Đây là đại lượng so được giữa các template; `len(text)` thô thì không,
    vì tên slot dài ngắn khác nhau (`{opener}` 8 ký tự, `{entity}` 8) làm
    nhiễu con số mà không tương ứng gì với câu sau khi fill.
    """
    return len(_SLOT_RE.sub("", text).strip())


def build_template_report(
    templates: Sequence[Template], distribution_config: Mapping[str, Any]
) -> dict[str, Any]:
    """Thống kê template bank (data/templates/templates.yaml).

    `distribution_config` đến từ config/distribution.yaml — ngưỡng
    `min_templates_per_cell` và `caps.template.max_share` đọc từ đó, không
    hardcode ở đây (hard rule #8).
    """
    profiles = [
        {
            "name": p["name"],
            "register": p["register"],
            "length_class": p["length_class"],
            "weight": float(p["weight"]),
        }
        for p in distribution_config["variation_profiles"]
    ]
    min_per_cell = int(distribution_config["min_templates_per_cell"])
    target_size = int(distribution_config["target_size"])
    max_share = float(distribution_config["caps"]["template"]["max_share"])
    max_per_template = max_share * target_size

    counts: Counter[tuple[str, str, str]] = Counter(
        (t.sentence_type.value, t.register.value, t.length_class.value) for t in templates
    )

    sentence_types = [st.value for st in SentenceType]
    matrix: list[dict[str, Any]] = []
    thin_cells: list[dict[str, Any]] = []
    cap_risks: list[dict[str, Any]] = []

    n_cells = len(sentence_types) * len(profiles)
    approx_per_cell = target_size / n_cells if n_cells else 0.0

    for st in sentence_types:
        row_cells: list[dict[str, Any]] = []
        for profile in profiles:
            count = counts[(st, profile["register"], profile["length_class"])]
            cell = {
                "sentence_type": st,
                "profile": profile["name"],
                "register": profile["register"],
                "length_class": profile["length_class"],
                "count": count,
                "ok": count >= min_per_cell,
            }
            row_cells.append(cell)
            if count < min_per_cell:
                thin_cells.append(cell)
            if count > 0:
                per_template = approx_per_cell / count
                if per_template > max_per_template:
                    cap_risks.append({**cell, "approx_per_template": round(per_template)})
        matrix.append({"sentence_type": st, "cells": row_cells})

    length_stats: dict[str, Any] = {}
    for lc in LengthClass:
        lengths = sorted(_skeleton_length(t.text) for t in templates if t.length_class == lc)
        if lengths:
            length_stats[lc.value] = {
                "count": len(lengths),
                "min": lengths[0],
                "median": int(statistics.median(lengths)),
                "max": lengths[-1],
            }

    return {
        "total": len(templates),
        "cell_count": n_cells,
        "min_templates_per_cell": min_per_cell,
        "max_per_template": max_per_template,
        "profiles": profiles,
        "coverage_matrix": matrix,
        "thin_cells": thin_cells,
        "cap_risks": cap_risks,
        "by_sentence_type": {
            st.value: sum(1 for t in templates if t.sentence_type == st) for st in SentenceType
        },
        "by_register": {
            r.value: sum(1 for t in templates if t.register == r) for r in Register
        },
        "by_length_class": {
            lc.value: sum(1 for t in templates if t.length_class == lc) for lc in LengthClass
        },
        "length_stats": length_stats,
    }


# ---------------------------------------------------------------------------
# Variation report — phục vụ `navtext serve` /variation
# ---------------------------------------------------------------------------

# Thứ tự hiển thị cố định — chỉ là thứ tự trình bày, không phải ngưỡng tỷ
# trọng (hard rule #8 áp cho số/ngưỡng, không áp cho thứ tự UI).
_AXIS_DISPLAY_ORDER: tuple[str, ...] = (
    "name_variant",
    "legacy_marker",
    "number_style",
    "number_prefix",
    "admin_prefix",
    "hierarchy_depth",
)

VARIATION_WEIGHT_NOTE = (
    "Trọng số trên trang này là tỷ lệ MONG MUỐN khai báo trong "
    "config/variation.yaml, KHÔNG phải tần suất thực tế trong dataset: "
    "generator (phase 6) ép giá trị khi trục không áp dụng được cho entity "
    "(vd name_variant=legacy chỉ có nghĩa khi câu nhắc tên tỉnh) — phân bố "
    "thật chỉ đo được sau phase 7 bằng `navtext stats`."
)


def _lexicon_pool(lexicon: Mapping[str, Any] | None) -> dict[str, dict[str, int]]:
    """Đếm số giá trị KHÁC RỖNG trong data/templates/lexicon.yaml theo
    slot × register. `fillers` là list mapping {text, heads} (không tách
    theo register — chỉ tách theo HeadClass, xem templates.HeadClass) nên
    cùng một count tổng cho mọi register.
    """
    if lexicon is None:
        return {}

    pool: dict[str, dict[str, int]] = {}
    for slot, yaml_key in (("opener", "openers"), ("closer", "closers")):
        per_register = lexicon.get(yaml_key) or {}
        pool[slot] = {
            r.value: sum(1 for v in (per_register.get(r.value) or []) if v) for r in Register
        }

    filler_count = sum(1 for v in (lexicon.get("fillers") or []) if (v or {}).get("text"))
    pool["filler"] = {r.value: filler_count for r in Register}
    return pool


def _axis_tables(axis: Axis) -> list[tuple[str, Mapping[str, float]]]:
    """Mọi bảng weight của một axis: default + từng override, gắn nhãn để
    warning trỏ đúng vị trí trong config/variation.yaml.
    """
    tables: list[tuple[str, Mapping[str, float]]] = [("weights", axis.weights)]
    tables += [
        (f"by_register.{reg.value}", w)
        for reg, w in sorted(axis.by_register.items(), key=lambda kv: kv[0].value)
    ]
    tables += [
        (f"by_category.{cat.value}", w)
        for cat, w in sorted(axis.by_category.items(), key=lambda kv: kv[0].value)
    ]
    return tables


def _lexicon_multiplier(rate: float, pool_count: int) -> int:
    """Hệ số nhân vào variation_space cho một slot filler/opener/closer.

    rate=0 -> luôn absent -> 1 lựa chọn. rate=1 -> luôn present -> chọn
    trong pool (>=1 để không nhân không gian về 0 khi pool rỗng, trường
    hợp đó đã được flag riêng ở warning empty_lexicon_pool). 0<rate<1 ->
    cả present (pool lựa chọn) lẫn absent (1 lựa chọn) đều khả thi.
    """
    if rate <= 0:
        return 1
    if rate >= 1:
        return max(pool_count, 1)
    return max(pool_count, 0) + 1


def _axis_live_count(axis: Axis, register: Register) -> int:
    """Số giá trị có weight > 0 cho một register — dùng bảng by_register
    nếu axis có override cho register đó, không thì rơi về default.
    """
    weights = axis.by_register.get(register, axis.weights)
    return sum(1 for w in weights.values() if w > 0)


def _variation_space(
    config: VariationConfig,
    lexicon_pool: Mapping[str, Mapping[str, int]],
    template_report: Mapping[str, Any] | None,
) -> dict[str, int]:
    """Ước lượng kích thước không gian variation (số tổ hợp khả thi) cho
    mỗi register — tích của số giá trị sống mỗi axis, hệ số opener/closer,
    và (nếu có) số template cùng register. Đối chiếu với candidate count
    (target_size * oversample_factor) để cảnh báo trùng lặp chắc chắn xảy
    ra theo nguyên lý chuồng bồ câu, trước cả khi dedup phase 11 chạy.
    """
    result: dict[str, int] = {}
    for register in Register:
        space = 1
        for axis in config.axes.values():
            space *= max(_axis_live_count(axis, register), 1)
        for slot in ("opener", "closer"):
            rate = config.lexicon_rates.get(slot, {}).get(register, 0.0)
            pool_count = lexicon_pool.get(slot, {}).get(register.value, 0)
            space *= _lexicon_multiplier(rate, pool_count)
        if template_report is not None:
            template_count = template_report["by_register"].get(register.value, 0)
            space *= max(template_count, 1)
        result[register.value] = space
    return result


def build_variation_report(
    config: VariationConfig,
    lexicon: Mapping[str, Any] | None = None,
    distribution_config: Mapping[str, Any] | None = None,
    template_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Thống kê config/variation.yaml (phase 5) — trọng số từng trục, hệ
    số lexicon, và không gian variation ước lượng.

    Ba nguồn phụ (lexicon, distribution_config, template_report) đều tuỳ
    chọn — thiếu nguồn nào chỉ bỏ qua phần phụ thuộc và ghi vào `notes`,
    không raise (mẫu degrade sẵn có của `navtext serve`).
    """
    lexicon_pool = _lexicon_pool(lexicon)
    variation_space = _variation_space(config, lexicon_pool, template_report)

    warnings: list[dict[str, str]] = []

    axes_out: list[dict[str, Any]] = []
    for axis_name in _AXIS_DISPLAY_ORDER:
        axis = config.axes.get(axis_name)
        if axis is None:
            continue
        axes_out.append(
            {
                "name": axis_name,
                "values": sorted(axis.weights),
                "weights": dict(axis.weights),
                "by_register": {reg.value: dict(w) for reg, w in axis.by_register.items()},
                "by_category": {cat.value: dict(w) for cat, w in axis.by_category.items()},
                "live_value_count": sum(1 for w in axis.weights.values() if w > 0),
            }
        )
        for table_label, weights in _axis_tables(axis):
            for value, weight in weights.items():
                if weight == 0:
                    warnings.append(
                        {
                            "id": "dead_value",
                            "detail": (
                                f"{axis_name}.{table_label}: giá trị {value!r} có weight 0 — "
                                "pick_weighted() sẽ không bao giờ chọn nó"
                            ),
                        }
                    )

    for slot, per_register in config.lexicon_rates.items():
        for register, rate in per_register.items():
            if rate > 0 and lexicon_pool.get(slot, {}).get(register.value, 0) == 0:
                warnings.append(
                    {
                        "id": "empty_lexicon_pool",
                        "detail": (
                            f"lexicon_rates.{slot}.{register.value}={rate} > 0 nhưng "
                            "lexicon không có giá trị khác rỗng nào cho register này"
                        ),
                    }
                )

    if distribution_config is not None:
        candidate_count = int(distribution_config["target_size"]) * float(
            distribution_config["oversample_factor"]
        )
        for register, space in variation_space.items():
            if space < candidate_count:
                warnings.append(
                    {
                        "id": "space_below_candidates",
                        "detail": (
                            f"register={register}: không gian variation ~{space:,} < "
                            f"candidate count {candidate_count:,.0f} — trùng lặp chắc chắn "
                            "xảy ra trước dedup (phase 11)"
                        ),
                    }
                )

    notes: list[str] = []
    if lexicon is None:
        notes.append(
            "Chưa nạp lexicon (data/templates/lexicon.yaml) — không gian variation bỏ "
            "qua hệ số opener/closer/filler."
        )
    if template_report is None:
        notes.append("Chưa nạp template bank — không gian variation bỏ qua hệ số số lượng template.")
    if distribution_config is None:
        notes.append(
            "Chưa nạp config/distribution.yaml — không đối chiếu được không gian "
            "variation với candidate count."
        )

    name_variant_axis = config.axes.get("name_variant")
    achievable_legacy_share = 0.0
    if name_variant_axis is not None:
        admin_unit_weights = name_variant_axis.by_category.get(
            Category.ADMIN_UNIT, name_variant_axis.weights
        )
        achievable_legacy_share = admin_unit_weights.get(NameVariant.LEGACY.value, 0.0)

    return {
        "note": VARIATION_WEIGHT_NOTE,
        "axes": axes_out,
        "lexicon_rates": {
            slot: {r.value: rate for r, rate in per_register.items()}
            for slot, per_register in config.lexicon_rates.items()
        },
        "lexicon_pool": lexicon_pool,
        "min_legacy_share_per_province": {
            "min_share": config.min_legacy_share_per_province,
            "achievable_share": achievable_legacy_share,
        },
        "address_synthesis": {
            "house_number_min": config.address_synthesis.house_number_min,
            "house_number_max": config.address_synthesis.house_number_max,
            "alley_suffix_rate": config.address_synthesis.alley_suffix_rate,
            "letter_suffix_rate": config.address_synthesis.letter_suffix_rate,
        },
        "variation_space": variation_space,
        "warnings": warnings,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Raw data report — phục vụ `navtext serve` /raw
# ---------------------------------------------------------------------------


def build_raw_report(raw: Any, master: MasterData) -> dict[str, Any]:
    """Đối chiếu data/raw/ với data/master/ đã build.

    `raw` là build_master.RawData — nhận Any để stats không phải import
    build_master (module đó nặng và thuộc pipeline ghi file; ở đây chỉ cần
    đọc field).
    """
    raw_province_ids = {str(p["province_id"]) for p in raw.provinces}
    master_province_ids = set(master.provinces)

    osm_provinces = sorted(raw.osm_by_province)
    osm_element_counts = {pid: len(elements) for pid, elements in sorted(raw.osm_by_province.items())}

    return {
        "totals": {
            "provinces": len(raw.provinces),
            "districts": len(raw.districts),
            "wards": len(raw.wards),
            "osm_snapshots": len(raw.osm_by_province),
            "osm_elements": sum(osm_element_counts.values()),
        },
        "osm_provinces": osm_provinces,
        "osm_element_counts": osm_element_counts,
        "provinces_without_osm": sorted(raw_province_ids - set(osm_provinces)),
        "reconciliation": {
            "in_raw_not_master": sorted(raw_province_ids - master_province_ids),
            "in_master_not_raw": sorted(master_province_ids - raw_province_ids),
            "district_count_raw": len(raw.districts),
            "district_count_master": len(master.districts),
            "district_count_ok": len(raw.districts) == len(master.districts),
            "ward_count_raw": len(raw.wards),
            "ward_count_master": len(master.wards),
            "ward_count_ok": len(raw.wards) == len(master.wards),
        },
    }


# ---------------------------------------------------------------------------
# Readiness report — phục vụ `navtext serve` /readiness
# ---------------------------------------------------------------------------


def _check(
    check_id: str, status: str, title: str, detail: str, action: str = "", href: str = ""
) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": status,
        "title": title,
        "detail": detail,
        "action": action,
        "href": href,
    }


def build_readiness_report(
    master: MasterData,
    master_report: Mapping[str, Any],
    template_report: Mapping[str, Any] | None,
    distribution_config: Mapping[str, Any] | None,
    template_issues: Sequence[str] = (),
    variation_issues: Sequence[str] = (),
    variation_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Gom mọi tín hiệu "đã đủ điều kiện chạy phase tiếp theo chưa" thành
    một danh sách check có thể hành động.

    Không tính lại số liệu đã có: coverage tỉnh/subcategory lấy thẳng từ
    `master_report`, số liệu template lấy từ `template_report`. Phần tính
    mới duy nhất là thử `build_plan()` để biến `PlanError` thành check
    hiển thị được — hiện đây là lỗi chặn thật của pipeline.
    """
    checks: list[dict[str, Any]] = []

    # --- template bank ------------------------------------------------
    if template_issues:
        checks.append(
            _check(
                "template_bank_valid",
                "fail",
                "Template bank không load được",
                f"{len(template_issues)} lỗi validator: " + "; ".join(template_issues[:5]),
                "Sửa data/templates/templates.yaml theo từng lỗi rồi restart server.",
                "/templates",
            )
        )
    elif template_report is None:
        checks.append(
            _check(
                "template_bank_valid",
                "warn",
                "Chưa nạp template bank",
                "Server chạy mà không có data/templates/.",
                "Chạy lại: navtext serve --template-dir data/templates",
            )
        )
    else:
        checks.append(
            _check(
                "template_bank_valid",
                "ok",
                "Template bank hợp lệ",
                f"{template_report['total']} template qua toàn bộ validator.",
                href="/templates",
            )
        )

    if template_report is not None:
        thin = template_report["thin_cells"]
        min_per_cell = template_report["min_templates_per_cell"]
        checks.append(
            _check(
                "template_coverage",
                "fail" if thin else "ok",
                "Coverage template theo ô",
                (
                    f"{len(thin)}/{template_report['cell_count']} ô có dưới {min_per_cell} template: "
                    + ", ".join(f"{c['sentence_type']}×{c['profile']}" for c in thin[:5])
                )
                if thin
                else f"Cả {template_report['cell_count']} ô đều có ≥ {min_per_cell} template.",
                "Soạn thêm template cho các ô thiếu trong data/templates/templates.yaml." if thin else "",
                "/templates",
            )
        )

        cap_risks = template_report["cap_risks"]
        checks.append(
            _check(
                "template_cap",
                "warn" if cap_risks else "ok",
                "Trần tỷ trọng template",
                (
                    f"{len(cap_risks)} ô có nguy cơ ép một template vượt trần "
                    f"{template_report['max_per_template']:.0f} sample."
                )
                if cap_risks
                else "Không ô nào ép template vượt caps.template.max_share.",
                "Thêm template vào các ô đó để chia mỏng tỷ trọng." if cap_risks else "",
                "/templates",
            )
        )

    # --- plan feasibility ---------------------------------------------
    if distribution_config is None:
        checks.append(
            _check(
                "plan_feasible",
                "warn",
                "Chưa nạp config/distribution.yaml",
                "Không kiểm tra được tính khả thi của plan.",
                "Chạy lại: navtext serve --distribution-config config/distribution.yaml",
            )
        )
    else:
        # Import cục bộ: chỉ readiness cần sampling, và giữ import top-level
        # của stats.py gọn cho hai report còn lại.
        from navtext.sampling import PlanError, build_plan

        try:
            plan = build_plan(dict(distribution_config), master)
        except PlanError as exc:
            checks.append(
                _check(
                    "plan_feasible",
                    "fail",
                    "build_plan() không chạy được",
                    f"{len(exc.issues)} violation: " + " | ".join(exc.issues[:3]),
                    "Fetch thêm OSM data cho tỉnh còn thiếu, hoặc nới trần trong config/distribution.yaml.",
                    "/config",
                )
            )
        else:
            checks.append(
                _check(
                    "plan_feasible",
                    "ok",
                    "Plan table build được",
                    f"{len(plan)} cell, tổng {sum(c.target_count for c in plan):,} sample.",
                    href="/config",
                )
            )

    # --- master data ---------------------------------------------------
    without = master_report["master_coverage"]["provinces_without_entities"]
    checks.append(
        _check(
            "province_entities",
            "warn" if without else "ok",
            "Tỉnh có entity",
            (
                f"{len(without)} tỉnh chưa có street/POI nào: "
                + ", ".join(p["name_current"] for p in without[:5])
                + ("..." if len(without) > 5 else "")
            )
            if without
            else "Mọi tỉnh đều có ít nhất 1 entity.",
            "Chạy scripts/fetch_osm.py cho các tỉnh đó rồi build-master lại." if without else "",
            "/provinces",
        )
    )

    empty_subs = master_report["empty_subcategories"]
    checks.append(
        _check(
            "empty_subcategories",
            "warn" if empty_subs else "ok",
            "Subcategory có dữ liệu",
            (f"{len(empty_subs)} subcategory chưa có entity: " + ", ".join(empty_subs[:8]))
            if empty_subs
            else "Mọi subcategory trong taxonomy đều có entity.",
            "Template dùng các subcategory này sẽ không có gì để fill slot." if empty_subs else "",
            "/entities",
        )
    )

    # --- variation config (phase 5) -------------------------------------
    if variation_issues:
        checks.append(
            _check(
                "variation_config_valid",
                "fail",
                "config/variation.yaml không load được",
                f"{len(variation_issues)} lỗi validator: " + "; ".join(variation_issues[:5]),
                "Sửa config/variation.yaml theo từng lỗi rồi restart server.",
                "/variation",
            )
        )
    elif variation_report is None:
        checks.append(
            _check(
                "variation_config_valid",
                "warn",
                "Chưa nạp config/variation.yaml",
                "Server chạy mà không có config/variation.yaml.",
                "Chạy lại: navtext serve --variation-config config/variation.yaml",
            )
        )
    else:
        checks.append(
            _check(
                "variation_config_valid",
                "ok",
                "config/variation.yaml hợp lệ",
                f"{len(variation_report['axes'])} trục qua toàn bộ validator.",
                href="/variation",
            )
        )

    if variation_report is not None:
        space_warnings = [w for w in variation_report["warnings"] if w["id"] == "space_below_candidates"]
        dead_warnings = [w for w in variation_report["warnings"] if w["id"] == "dead_value"]
        risky = space_warnings + dead_warnings
        checks.append(
            _check(
                "variation_space",
                "warn" if risky else "ok",
                "Không gian variation",
                (
                    f"{len(space_warnings)} register có không gian nhỏ hơn candidate count, "
                    f"{len(dead_warnings)} giá trị weight 0 (không bao giờ được chọn)."
                )
                if risky
                else "Không gian variation đủ lớn, không giá trị nào chết.",
                "Xem chi tiết ở /variation." if risky else "",
                "/variation",
            )
        )

    status_counts = Counter(c["status"] for c in checks)
    if status_counts["fail"]:
        overall = "fail"
    elif status_counts["warn"]:
        overall = "warn"
    else:
        overall = "ok"

    return {
        "overall": overall,
        "counts": {s: status_counts[s] for s in ("ok", "warn", "fail")},
        "checks": checks,
    }
