"""Sinh Sample từ Cell — điểm nối schema/sampling/templates/variation/numbers.
Implement ở phase 6. Xem docs/generation.md.
"""

from __future__ import annotations

import hashlib
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from navtext.loaders import MasterData, load_master
from navtext.sampling import entity_pool, largest_remainder, normalize_weights, resolve_profiles
from navtext.schema import Category, Cell, LengthClass, LocationRef, Register, Sample, SentenceType, Subcategory
from navtext.templates import Lexicon, Template, index_templates, load_lexicon, load_templates, pick_template
from navtext.variation import VariationConfig, apply_variations, compose_entity, draw_axes, format_variation_key

# Số lần thử lại tối đa khi cặp (template_id, entity_id) vừa rút đã dùng
# trong cell này (docs/qc.md: "cấm cặp (template_id, entity_id) xuất hiện
# quá 1 lần trong dataset cuối"). Tham số thuật toán (giống _IPF_ITERATIONS
# ở sampling.py), không phải trọng số tỷ trọng — không thuộc hard rule #8.
_PAIR_RETRY_LIMIT = 20


@dataclass(frozen=True, slots=True)
class GenerationContext:
    """Toàn bộ input bất biến cho một run generate: master data, template
    bank đã index, lexicon, variation config, distribution config, và map
    variation_profile -> (register, length_class).
    """

    master: MasterData
    templates_by_key: Mapping[tuple[SentenceType, str], tuple[Template, ...]]
    lexicon: Lexicon
    variation: VariationConfig
    distribution: Mapping[str, Any]
    profiles: Mapping[str, tuple[Register, LengthClass]]
    global_seed: int


def build_context(
    master_dir: Path,
    template_dir: Path,
    distribution_config: Mapping[str, Any],
    variation_config: VariationConfig,
    global_seed: int,
) -> GenerationContext:
    """Load toàn bộ input tĩnh (master/template/lexicon) và build index một
    lần — generate_cell() không đọc file, không build lại index mỗi cell.
    """
    master = load_master(master_dir)
    templates = load_templates(template_dir)
    lexicon = load_lexicon(template_dir)
    profiles = resolve_profiles(distribution_config)
    min_per_cell = int(distribution_config["min_templates_per_cell"])
    templates_by_key = index_templates(templates, profiles, min_per_cell)

    return GenerationContext(
        master=master,
        templates_by_key=templates_by_key,
        lexicon=lexicon,
        variation=variation_config,
        distribution=distribution_config,
        profiles=profiles,
        global_seed=global_seed,
    )


def _weighted_shuffle(
    pool: tuple[LocationRef, ...], weight_by_tier: Mapping[int, float], rng: random.Random
) -> list[LocationRef]:
    """Weighted sampling without replacement kiểu Efraimidis–Spirakis: mỗi
    entity nhận key = rng.random() ** (1/w), sort giảm dần theo key. Cho
    diversity tối đa (mọi entity trong pool đều có cơ hội) mà vẫn nghiêng
    về tier cao — không phải random.choice() lặp lại (anti-pattern,
    CLAUDE.md#anti-patterns).
    """

    def _key(entity: LocationRef) -> float:
        weight = weight_by_tier.get(entity.popularity_tier, 1.0)
        # Tránh log(0)/chia 0 khi rng.random() ra đúng 0.0 (xác suất ~0
        # nhưng vẫn phải an toàn tuyệt đối).
        u = rng.random() or 1e-12
        return u ** (1.0 / weight)

    return sorted(pool, key=_key, reverse=True)


def _admin_unit_ordered_pool(
    pool: tuple[LocationRef, ...],
    subcategory_weights: Mapping[str, float],
    weight_by_tier: Mapping[int, float],
    target_count: int,
    rng: random.Random,
) -> list[LocationRef]:
    """Chia target_count theo quota subcategory (province/district/ward)
    TRƯỚC, weighted-shuffle bên trong từng nhóm SAU — đúng hard rule #3
    (quota-driven, random chỉ xảy ra bên trong cell/nhóm).

    Không có bước này thì weighted-shuffle đơn thuần trên toàn pool admin_unit
    gần như không bao giờ rút được entity province: một tỉnh chỉ có ĐÚNG 1
    entity province cạnh tranh với hàng trăm ward (nguồn wards_osm.csv) —
    xác suất bị chọn quá thấp dù entity_weights.by_tier đã ưu tiên tier 1.

    Nhóm rỗng (tỉnh chưa có district/ward) tự loại khỏi phân bổ, quota của
    nó chia lại cho nhóm còn lại (normalize_weights chỉ tính trên nhóm có
    entity) — không lỗi, không cần xử lý đặc biệt ở caller.
    """
    groups: dict[str, list[LocationRef]] = {}
    for entity in pool:
        groups.setdefault(entity.subcategory.value, []).append(entity)

    available = {sub: w for sub, w in subcategory_weights.items() if groups.get(sub)}
    shares = normalize_weights(available)
    sub_counts = largest_remainder(shares, target_count)

    ordered: list[LocationRef] = []
    for sub in sorted(sub_counts):
        count = sub_counts[sub]
        if count <= 0:
            continue
        shuffled = _weighted_shuffle(tuple(groups[sub]), weight_by_tier, rng)
        ordered.extend(shuffled[i % len(shuffled)] for i in range(count))
    return ordered


def _substitute_slots(text: str, slots: Mapping[str, str]) -> str:
    """Thay `{slot}` bằng giá trị, tự chèn space đúng hướng khi giá trị
    khác rỗng (opener: space sau, closer: space trước — {entity} đã có
    space literal quanh nó trong template.text nên giữ nguyên), rồi chuẩn
    hoá `" ".join(text.split())` để opener/closer rỗng không để lại double
    space (docs/generation.md: "Quy ước spacing").
    """
    result = text
    for name, value in slots.items():
        if name == "opener":
            replacement = f"{value} " if value else ""
        elif name == "closer":
            replacement = f" {value}" if value else ""
        else:
            replacement = value
        result = result.replace("{" + name + "}", replacement)
    return " ".join(result.split())


def generate_cell(cell: Cell, ctx: GenerationContext, counters: Counter[str] | None = None) -> list[Sample]:
    """Sinh đúng cell.target_count sample cho một cell, dùng
    cell.derive_seed(ctx.global_seed) làm rng seed — reproducible, độc lập
    với các cell khác (docs/sampling.md#cell-key--rng-theo-cell).
    """
    if counters is None:
        counters = Counter()

    rng = random.Random(cell.derive_seed(ctx.global_seed))
    register, _length_class = ctx.profiles[cell.variation_profile]

    templates = ctx.templates_by_key.get((cell.sentence_type, cell.variation_profile), ())
    if not templates:
        counters[f"no_templates::{cell.cell_key}"] += cell.target_count
        return []

    pool = entity_pool(ctx.master, cell.province_id, cell.category)
    if not pool:
        counters[f"entity_pool_empty::{cell.cell_key}"] += cell.target_count
        return []

    weight_by_tier = {int(k): float(v) for k, v in ctx.distribution["entity_weights"]["by_tier"].items()}
    if cell.category == Category.ADMIN_UNIT:
        subcategory_weights = ctx.distribution["admin_unit_subcategory_weights"]
        ordered_pool = _admin_unit_ordered_pool(
            pool, subcategory_weights, weight_by_tier, cell.target_count, rng
        )
    else:
        ordered_pool = _weighted_shuffle(pool, weight_by_tier, rng)

    province = ctx.master.provinces[cell.province_id]
    used_pairs: set[tuple[str, str]] = set()
    samples: list[Sample] = []

    for i in range(cell.target_count):
        entity = ordered_pool[i % len(ordered_pool)]

        template: Template | None = None
        for _attempt in range(_PAIR_RETRY_LIMIT):
            candidate = pick_template(cell, list(templates), rng)
            if (candidate.template_id, entity.entity_id) not in used_pairs:
                template = candidate
                break
        if template is None:
            # Hết lượt thử — chấp nhận trùng cặp thay vì rơi vào vòng lặp
            # vô hạn (tệp thiếu diversity template/entity cho ô này); dedup
            # phase 11 (qc/dedup.py) sẽ là lưới an toàn cuối cùng.
            template = pick_template(cell, list(templates), rng)
            counters[f"pair_retry_exhausted::{cell.cell_key}"] += 1
        used_pairs.add((template.template_id, entity.entity_id))

        draw = draw_axes(entity, cell, register, ctx.variation, rng)
        phrase = compose_entity(entity, cell, draw, ctx.master, ctx.lexicon, ctx.variation, rng)

        slots: dict[str, str] = {slot: "" for slot in template.slots}
        slots["entity"] = phrase.text
        slots = apply_variations(
            slots,
            cell,
            ctx.variation,
            rng,
            register=register,
            lexicon=ctx.lexicon,
            tail_class=template.tail_class,
            head_class=template.head_class,
            self_asks=template.self_asks,
            self_helps=template.self_helps,
            counters=counters,
        )

        text = _substitute_slots(template.text, slots)

        subcategory = Subcategory.HOUSE_ADDRESS if cell.category == Category.ADDRESS else entity.subcategory
        sample_id = hashlib.sha1(f"{cell.cell_key}|{i}".encode("utf-8")).hexdigest()[:16]

        samples.append(
            Sample(
                id=sample_id,
                text=text,
                category=cell.category,
                subcategory=subcategory,
                entity=phrase.text,
                entity_id=entity.entity_id,
                province=province.name_current,
                province_legacy=phrase.province_legacy,
                district=phrase.district,
                ward=phrase.ward,
                street=phrase.street,
                address=phrase.address,
                sentence_type=cell.sentence_type,
                template_id=template.template_id,
                variation=format_variation_key(phrase.variant_used, draw.number_style, phrase.admin_prefix_used, register),
                name_variant=phrase.variant_used,
                number_style=draw.number_style,
                register=register,
                source=entity.source,
                seed_cell=cell.cell_key,
            )
        )

    counters[f"generated::{cell.cell_key}"] += len(samples)
    return samples


def generate_all(
    plan: list[Cell], ctx: GenerationContext, counters: Counter[str] | None = None
) -> Iterator[Sample]:
    """Chạy generate_cell cho toàn bộ plan, yield từng Sample. Mỗi cell có
    rng riêng (derive_seed) nên thứ tự duyệt plan không ảnh hưởng tới output
    của bất kỳ cell nào — chỉ ảnh hưởng thứ tự dòng trong file cuối.
    """
    if counters is None:
        counters = Counter()
    for cell in plan:
        for sample in generate_cell(cell, ctx, counters):
            yield sample
