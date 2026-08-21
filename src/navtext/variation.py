"""Trọng số các trục variation (phase 5) + áp dụng lên slot đã fill (phase 6).

Phase 5 xong ở đây: `load_variation_config()` đọc config/variation.yaml,
validate ngay lúc load (giống templates.load_templates()), và
`pick_weighted()`/`format_variation_key()` là helper dùng chung cho phase 6.
`apply_variations()` vẫn là NotImplementedError — nó phụ thuộc numbers.py
(chưa implement) và là việc của phase 6.

Xem docs/generation.md#config-variation-yaml.
"""

from __future__ import annotations

import random
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml

from navtext.loaders import MasterData
from navtext.numbers import read_house_number, verbalize_entity_name
from navtext.templates import HeadClass, Lexicon, TailClass
from navtext.schema import (
    Category,
    Cell,
    HierarchyDepth,
    LocationRef,
    NameVariant,
    NumberStyle,
    Register,
    Subcategory,
)

_SUPPORTED_SCHEMA_VERSION = 1

# Mỗi entry: (tên axis, enum bắt buộc phủ đúng | None nếu axis boolean).
# None -> validator dùng _BOOLEAN_KEYS thay vì duyệt enum.
_ENUM_AXES: Mapping[str, type] = {
    "name_variant": NameVariant,
    "number_style": NumberStyle,
    "hierarchy_depth": HierarchyDepth,
}
_BOOLEAN_AXES: frozenset[str] = frozenset({"number_prefix", "admin_prefix", "legacy_marker"})
_BOOLEAN_KEYS: frozenset[str] = frozenset({"present", "absent"})
_ALL_AXES: frozenset[str] = frozenset(_ENUM_AXES) | _BOOLEAN_AXES

_LEXICON_SLOTS: frozenset[str] = frozenset({"opener", "closer", "filler"})


class VariationError(Exception):
    """Raise với TOÀN BỘ danh sách violation cùng lúc — theo mẫu
    templates.TemplateError / sampling.PlanError / build_master.BuildError,
    không fail-fast ở violation đầu tiên.
    """

    def __init__(self, issues: list[str]) -> None:
        self.issues = issues
        super().__init__("\n".join(f"- {i}" for i in issues))


def _normalize(weights: Mapping[str, float]) -> dict[str, float]:
    """Chuẩn hoá một bảng weight thô về share cộng đúng 1.0. Giả định
    weights đã qua validate (không âm, tổng > 0) — gọi sau khi check #4.
    """
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()}


@dataclass(frozen=True, slots=True)
class Axis:
    """Trọng số một trục variation, đã normalize, cộng override theo
    register/category. resolve() quyết định thứ tự ưu tiên override.
    """

    name: str
    weights: Mapping[str, float]
    by_register: Mapping[Register, Mapping[str, float]]
    by_category: Mapping[Category, Mapping[str, float]]

    def resolve(self, register: Register | None, category: Category | None) -> Mapping[str, float]:
        """by_category thắng by_register thắng weights mặc định. Không có
        override khớp -> rơi về weights mặc định.
        """
        if category is not None and category in self.by_category:
            return self.by_category[category]
        if register is not None and register in self.by_register:
            return self.by_register[register]
        return self.weights


@dataclass(frozen=True, slots=True)
class AddressSynthesisConfig:
    """Tham số synthesize số nhà cho category=address — xem
    config/variation.yaml#address_synthesis.
    """

    house_number_min: int
    house_number_max: int
    alley_suffix_rate: float
    letter_suffix_rate: float


@dataclass(frozen=True, slots=True)
class VariationConfig:
    schema_version: int
    axes: Mapping[str, Axis]
    lexicon_rates: Mapping[str, Mapping[Register, float]]
    min_legacy_share_per_province: float
    address_synthesis: AddressSynthesisConfig


# ---------------------------------------------------------------------------
# Validation helpers — mỗi hàm trả list[str] issue (rỗng nếu sạch), đánh số
# theo bảng 9 check ở docs/generation.md#config-variation-yaml.
# ---------------------------------------------------------------------------


def _check_axes_present(raw_axes: Mapping[str, object]) -> list[str]:
    """#1 axis bắt buộc: đủ 6 axis, không axis lạ."""
    issues = []
    missing = _ALL_AXES - set(raw_axes)
    extra = set(raw_axes) - _ALL_AXES
    if missing:
        issues.append(f"axes: thiếu {sorted(missing)}")
    if extra:
        issues.append(f"axes: có axis lạ ngoài danh sách hợp lệ {sorted(extra)}")
    return issues


def _check_weight_values(context: str, weights: Mapping[str, object]) -> list[str]:
    """#4 weight hợp lệ: không âm, là số, tổng > 0."""
    issues = []
    total = 0.0
    for key, value in weights.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            issues.append(f"{context}: weight của {key!r} không phải số ({value!r})")
            continue
        if value < 0:
            issues.append(f"{context}: weight của {key!r} âm ({value})")
            continue
        total += float(value)
    if total <= 0:
        issues.append(f"{context}: tổng weight phải > 0, nhận {total}")
    return issues


def _check_key_set(context: str, keys: set[str], expected: set[str]) -> list[str]:
    """#2/#3/#6 phủ đúng tập key mong đợi — thiếu hoặc thừa đều reject."""
    issues = []
    missing = expected - keys
    extra = keys - expected
    if missing:
        issues.append(f"{context}: thiếu key {sorted(missing)}")
    if extra:
        issues.append(f"{context}: có key thừa {sorted(extra)}")
    return issues


def _expected_keys(axis_name: str) -> set[str]:
    if axis_name in _ENUM_AXES:
        return {e.value for e in _ENUM_AXES[axis_name]}
    return set(_BOOLEAN_KEYS)


def _load_axis(axis_name: str, raw_axis: Mapping[str, object]) -> tuple[list[str], Axis | None]:
    issues: list[str] = []
    expected = _expected_keys(axis_name)

    raw_weights = raw_axis.get("weights")
    if not isinstance(raw_weights, dict):
        return [f"axes.{axis_name}.weights: thiếu hoặc không phải mapping"], None

    # #2/#3 enum/boolean phủ đủ
    issues += _check_key_set(f"axes.{axis_name}.weights", set(raw_weights), expected)
    # #4 weight hợp lệ
    issues += _check_weight_values(f"axes.{axis_name}.weights", raw_weights)

    by_register: dict[Register, Mapping[str, float]] = {}
    raw_by_register = raw_axis.get("by_register") or {}
    if not isinstance(raw_by_register, dict):
        issues.append(f"axes.{axis_name}.by_register: không phải mapping")
        raw_by_register = {}
    for reg_key, override in raw_by_register.items():
        try:
            register = Register(reg_key)
        except ValueError:
            issues.append(
                f"axes.{axis_name}.by_register: key {reg_key!r} không hợp lệ — "
                f"phải là một trong {[r.value for r in Register]}"
            )
            continue
        if not isinstance(override, dict):
            issues.append(f"axes.{axis_name}.by_register.{reg_key}: không phải mapping")
            continue
        issues += _check_key_set(f"axes.{axis_name}.by_register.{reg_key}", set(override), expected)
        issues += _check_weight_values(f"axes.{axis_name}.by_register.{reg_key}", override)
        if not issues:
            by_register[register] = _normalize(override)  # type: ignore[arg-type]

    by_category: dict[Category, Mapping[str, float]] = {}
    raw_by_category = raw_axis.get("by_category") or {}
    if not isinstance(raw_by_category, dict):
        issues.append(f"axes.{axis_name}.by_category: không phải mapping")
        raw_by_category = {}
    for cat_key, override in raw_by_category.items():
        try:
            category = Category(cat_key)
        except ValueError:
            issues.append(
                f"axes.{axis_name}.by_category: key {cat_key!r} không hợp lệ — "
                f"phải là một trong {[c.value for c in Category]}"
            )
            continue
        if not isinstance(override, dict):
            issues.append(f"axes.{axis_name}.by_category.{cat_key}: không phải mapping")
            continue
        issues += _check_key_set(f"axes.{axis_name}.by_category.{cat_key}", set(override), expected)
        issues += _check_weight_values(f"axes.{axis_name}.by_category.{cat_key}", override)
        if not issues:
            by_category[category] = _normalize(override)  # type: ignore[arg-type]

    if issues:
        return issues, None

    return [], Axis(
        name=axis_name,
        weights=_normalize(raw_weights),  # type: ignore[arg-type]
        by_register=by_register,
        by_category=by_category,
    )


def _load_lexicon_rates(raw: object) -> tuple[list[str], dict[str, Mapping[Register, float]]]:
    """#7 rate hợp lệ: mỗi slot phải phủ đủ Register, giá trị trong [0, 1]."""
    issues: list[str] = []
    result: dict[str, Mapping[Register, float]] = {}

    if not isinstance(raw, dict):
        return ["lexicon_rates: thiếu hoặc không phải mapping"], {}

    missing_slots = _LEXICON_SLOTS - set(raw)
    extra_slots = set(raw) - _LEXICON_SLOTS
    if missing_slots:
        issues.append(f"lexicon_rates: thiếu slot {sorted(missing_slots)}")
    if extra_slots:
        issues.append(f"lexicon_rates: có slot lạ {sorted(extra_slots)}")

    for slot, per_register in raw.items():
        if slot not in _LEXICON_SLOTS:
            continue
        if not isinstance(per_register, dict):
            issues.append(f"lexicon_rates.{slot}: không phải mapping")
            continue
        expected = {r.value for r in Register}
        issues += _check_key_set(f"lexicon_rates.{slot}", set(per_register), expected)
        parsed: dict[Register, float] = {}
        for reg_key, value in per_register.items():
            if reg_key not in expected:
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not (0.0 <= value <= 1.0):
                issues.append(f"lexicon_rates.{slot}.{reg_key}: phải là số trong [0, 1], nhận {value!r}")
                continue
            parsed[Register(reg_key)] = float(value)
        if not any(f"lexicon_rates.{slot}" in i for i in issues):
            result[slot] = parsed

    return issues, result


def _load_address_synthesis(raw: object) -> tuple[list[str], AddressSynthesisConfig | None]:
    """#10 address_synthesis hợp lệ: dải số nguyên dương hợp lý, hai rate
    trong [0, 1]. Không phải 1 trong 9 check gốc của docs/generation.md
    (bổ sung cùng đợt thêm address_synthesis vào config) nhưng theo đúng
    mẫu "gom hết issue, reject không warn" như các check khác.
    """
    if not isinstance(raw, dict):
        return ["address_synthesis: thiếu hoặc không phải mapping"], None

    issues: list[str] = []
    house_min = raw.get("house_number_min")
    house_max = raw.get("house_number_max")
    alley_rate = raw.get("alley_suffix_rate")
    letter_rate = raw.get("letter_suffix_rate")

    if not isinstance(house_min, int) or isinstance(house_min, bool) or house_min < 1:
        issues.append(f"address_synthesis.house_number_min: phải là số nguyên >= 1, nhận {house_min!r}")
        house_min = 1
    if not isinstance(house_max, int) or isinstance(house_max, bool) or house_max <= house_min:
        issues.append(
            f"address_synthesis.house_number_max: phải là số nguyên > house_number_min, nhận {house_max!r}"
        )
        house_max = house_min + 1
    for name, value in (("alley_suffix_rate", alley_rate), ("letter_suffix_rate", letter_rate)):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not (0.0 <= value <= 1.0):
            issues.append(f"address_synthesis.{name}: phải là số trong [0, 1], nhận {value!r}")

    if issues:
        return issues, None

    return [], AddressSynthesisConfig(
        house_number_min=house_min,
        house_number_max=house_max,
        alley_suffix_rate=float(alley_rate),
        letter_suffix_rate=float(letter_rate),
    )


def load_variation_config(path: Path) -> VariationConfig:
    """Đọc config/variation.yaml. Validator chạy ngay lúc load — reject
    (không warn) trên toàn bộ 9 check, gom hết issue trước khi raise.
    """
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    issues: list[str] = []

    # #9 schema_version
    schema_version = raw.get("schema_version")
    if schema_version != _SUPPORTED_SCHEMA_VERSION:
        issues.append(
            f"schema_version: {schema_version!r} không được hỗ trợ — "
            f"phải là {_SUPPORTED_SCHEMA_VERSION}"
        )

    raw_axes = raw.get("axes")
    if not isinstance(raw_axes, dict):
        issues.append("axes: thiếu hoặc không phải mapping")
        raw_axes = {}
    else:
        issues += _check_axes_present(raw_axes)

    axes: dict[str, Axis] = {}
    for axis_name in sorted(_ALL_AXES & set(raw_axes)):
        axis_issues, axis = _load_axis(axis_name, raw_axes[axis_name])
        issues += axis_issues
        if axis is not None:
            axes[axis_name] = axis

    lexicon_issues, lexicon_rates = _load_lexicon_rates(raw.get("lexicon_rates"))
    issues += lexicon_issues

    # #8 sàn hợp lệ — sống trong axes.name_variant (đúng vị trí trong YAML,
    # cạnh weights của trục mà nó ràng buộc).
    raw_name_variant = raw_axes.get("name_variant") if isinstance(raw_axes, dict) else None
    min_legacy_share = (
        raw_name_variant.get("min_legacy_share_per_province")
        if isinstance(raw_name_variant, dict)
        else None
    )
    if not isinstance(min_legacy_share, (int, float)) or isinstance(min_legacy_share, bool):
        issues.append(
            "axes.name_variant.min_legacy_share_per_province: thiếu hoặc không phải số"
        )
        min_legacy_share = 0.0
    elif not (0.0 <= min_legacy_share <= 1.0):
        issues.append(
            "axes.name_variant.min_legacy_share_per_province: phải trong [0, 1], "
            f"nhận {min_legacy_share}"
        )
    else:
        name_variant_axis = axes.get("name_variant")
        if name_variant_axis is not None:
            admin_unit_weights = name_variant_axis.by_category.get(
                Category.ADMIN_UNIT, name_variant_axis.weights
            )
            achievable = admin_unit_weights.get(NameVariant.LEGACY.value, 0.0)
            if min_legacy_share > achievable:
                issues.append(
                    "axes.name_variant.min_legacy_share_per_province: "
                    f"{min_legacy_share} lớn hơn share legacy khả thi "
                    f"({achievable:.4f}) của by_category.admin_unit — sàn không thể đạt được"
                )

    address_synthesis_issues, address_synthesis = _load_address_synthesis(raw.get("address_synthesis"))
    issues += address_synthesis_issues

    if issues:
        raise VariationError(issues)

    assert address_synthesis is not None  # issues rỗng => address_synthesis đã load thành công

    return VariationConfig(
        schema_version=schema_version,
        axes=axes,
        lexicon_rates=lexicon_rates,
        min_legacy_share_per_province=float(min_legacy_share),
        address_synthesis=address_synthesis,
    )


# ---------------------------------------------------------------------------
# Helper dùng chung cho phase 6
# ---------------------------------------------------------------------------


def pick_weighted(weights: Mapping[str, float], rng: random.Random) -> str:
    """Chọn một key theo trọng số. Deterministic: duyệt key đã sort (không
    phụ thuộc thứ tự dict hay PYTHONHASHSEED), đúng một lần rng.random()
    (hard rule #4: rng luôn nhận tường minh, không dùng random module-level).
    """
    keys = sorted(weights)
    if not keys:
        raise ValueError("pick_weighted: weights rỗng")

    total = sum(weights[k] for k in keys)
    if total <= 0:
        raise ValueError(f"pick_weighted: tổng weight phải > 0, nhận {total}")

    draw = rng.random() * total
    cumulative = 0.0
    for key in keys:
        cumulative += weights[key]
        if draw < cumulative:
            return key
    return keys[-1]  # fallback cho sai số float


def format_variation_key(
    name_variant: NameVariant,
    number_style: NumberStyle,
    admin_prefix_present: bool,
    register: Register,
) -> str:
    """Cột `variation` trong CSV cuối, dạng chuỗi ngắn nối bằng "+", vd
    "legacy+cardinal+prefix+casual" (docs/output.md#csv-schema).
    """
    parts = [
        name_variant.value,
        number_style.value,
        "prefix" if admin_prefix_present else "no_prefix",
        register.value,
    ]
    return "+".join(parts)


_HELP_WORD = "giúp"


def _closer_pool(
    register: Register, tail_class: TailClass, lexicon: Lexicon, self_helps: bool = False
) -> tuple[str, ...]:
    """Chọn đúng pool closer theo tail_class của template — pool hẹp
    (closers_after_question/closers_after_particle) THAY THẾ pool
    `closers` mặc định khi câu đã kết ở một từ nghi vấn/tiểu từ cuối câu,
    để không chồng tiểu từ (hard rule #9, vd "…ở đâu không được không",
    "…không nha"). Xem TailClass ở templates.py.

    `self_helps`: template đã tự chứa "giúp" ở đâu đó trong text (xem
    templates._template_mentions_help) — loại closer CŨNG chứa "giúp"
    khỏi pool để tránh lặp kiểu "tìm giúp mình đường tới ... giúp mình".

    `tail_class == TRAILING_CLAUSE`: câu đã kết bằng một mệnh đề bình luận
    tách khỏi {entity} (xem TailClass.TRAILING_CLAUSE) — không có closer nào
    gắn tự nhiên vào SAU một mệnh đề đã trọn vẹn kiểu đó, nên pool rỗng
    tuyệt đối (khác các nhánh khác, không có pool hẹp thay thế).
    """
    if tail_class == TailClass.TRAILING_CLAUSE:
        return ()
    if tail_class == TailClass.QUESTION_WORD:
        pool = lexicon.closers_after_question.get(register, ())
    elif tail_class == TailClass.FINAL_PARTICLE:
        pool = lexicon.closers_after_particle.get(register, ())
    else:
        pool = lexicon.closers.get(register, ())
    if self_helps:
        return tuple(c for c in pool if _HELP_WORD not in c)
    return pool


_ASK_WORD = "hỏi"


def _opener_pool(
    register: Register, lexicon: Lexicon, head_class: HeadClass, self_asks: bool
) -> tuple[str, ...]:
    """Pool opener cho register này — hai lớp lọc độc lập:

    1. head_class: opener khung hỏi (Opener.heads không chứa head_class hiện
       tại — xem templates.Opener) bị loại trước tiên. Chặn "xin phép hỏi" +
       "xin chỉ đường đến..." (2 khung câu chồng nhau, bug user báo cáo) —
       formal register hiện KHÔNG có opener nào dùng được cho IMPERATIVE
       (mọi opener formal đều khung hỏi), lọc ra rỗng ở bước này là hợp lệ.
    2. self_asks: loại tiếp opener CŨNG chứa "hỏi" khi template.self_asks
       (template đã tự mở đầu bằng "hỏi" ngay sau {opener}, xem
       templates._template_starts_with_ask) — tránh lặp cụm hỏi kiểu
       "xin phép hỏi xin hỏi...".

    KHÔNG fallback về pool gốc khi một trong hai bước lọc ra rỗng (làm vậy
    sẽ xoá sạch tác dụng của việc lọc) — pool rỗng khiến _draw_slot_value()
    trả "", chấp nhận template đôi khi không có opener còn hơn để lọt câu
    chồng khung/lặp cụm.
    """
    pool = tuple(o.text for o in lexicon.openers.get(register, ()) if head_class in o.heads)
    if not self_asks:
        return pool
    return tuple(o for o in pool if _ASK_WORD not in o)


def _draw_slot_value(
    slot_name: str,
    register: Register,
    config: VariationConfig,
    lexicon: Lexicon,
    rng: random.Random,
    *,
    tail_class: TailClass = TailClass.PLAIN,
    head_class: HeadClass = HeadClass.IMPERATIVE,
    self_asks: bool = False,
    self_helps: bool = False,
) -> str:
    """Rút present/absent theo lexicon_rates[slot_name][register], nếu
    present chọn uniform trong pool khác rỗng của lexicon (opener/closer).
    Không chọn trực tiếp trên list còn "" — xác suất rỗng đã đến từ
    lexicon_rates, chọn trên list còn "" sẽ tính xác suất rỗng hai lần.

    `tail_class`/`self_helps` chỉ có ý nghĩa với slot_name="closer" — quyết
    định pool nào được dùng (xem _closer_pool). `head_class`/`self_asks` chỉ
    có ý nghĩa với slot_name="opener" (xem _opener_pool). Pool rỗng (vd
    casual sau FINAL_PARTICLE chỉ có "", hoặc formal + IMPERATIVE không có
    opener nào dùng được) trả về "" giống hệt trường hợp rút "absent".
    """
    rate = config.lexicon_rates.get(slot_name, {}).get(register, 0.0)
    if rng.random() >= rate:
        return ""
    pool = (
        _opener_pool(register, lexicon, head_class, self_asks)
        if slot_name == "opener"
        else _closer_pool(register, tail_class, lexicon, self_helps)
    )
    if not pool:
        return ""
    return pool[rng.randrange(len(pool))]


def _merge_filler(base: str, filler: str) -> str:
    if not base:
        return filler
    # Guard chống lặp: "à" có mặt ở CẢ fillers lẫn openers.casual
    # (lexicon.yaml) — nếu opener đã rút trúng đúng filler đó, không nối
    # thêm lần hai (fix "à à đi Ngọc Hà kiểu gì cho lẹ vậy ta").
    if base == filler or base.startswith(f"{filler} "):
        return base
    return f"{base} {filler}"


def _filler_pool(head_class: HeadClass, lexicon: Lexicon) -> tuple[str, ...]:
    """Lọc lexicon.fillers theo HeadClass của template — đối xứng với
    _closer_pool(): filler mang nghĩa (heads=[statement]) chỉ hợp lệ khi
    câu là một mệnh đề trần thuật, xem templates.HeadClass.
    """
    return tuple(f.text for f in lexicon.fillers if head_class in f.heads)


def apply_variations(
    slots: dict[str, str],
    cell: Cell,
    config: VariationConfig,
    rng: random.Random,
    *,
    register: Register,
    lexicon: Lexicon,
    tail_class: TailClass = TailClass.PLAIN,
    head_class: HeadClass = HeadClass.IMPERATIVE,
    self_asks: bool = False,
    self_helps: bool = False,
    counters: Counter[str] | None = None,
) -> dict[str, str]:
    """Điền các slot lexicon (opener/closer/filler/category_noun) của
    template — KHÔNG xử lý {entity} (đã được compose_entity() dựng sẵn text
    trước khi gọi hàm này, generator.py truyền vào slots["entity"] đã có
    sẵn và hàm này để nguyên). Dùng rng của cell — không random global (hard
    rule #4).

    `register` không suy được từ `cell` (Cell không mang register trực
    tiếp — xem sampling.resolve_profiles) nên phải truyền tường minh.
    `tail_class` (xem templates.TailClass) thu hẹp pool closer khi template
    đã kết ở từ nghi vấn/tiểu từ cuối câu — xem _closer_pool().
    `head_class` (xem templates.HeadClass) thu hẹp pool filler khi câu là
    mệnh lệnh/nghi vấn thay vì trần thuật — xem _filler_pool().

    Filler (à/thế/kiểu/...) không có slot riêng trong ALLOWED_SLOTS
    (templates.py chỉ cho opener/entity/closer/category_noun) — khi rút
    trúng "present", CHỈ nối vào giá trị opener (đầu câu); nếu template
    không có slot opener thì bỏ qua, không nối vào closer nữa — filler ở
    cuối câu từng tạo "…Sea Food kiểu", "…không tự dưng" (không tự nhiên:
    filler là từ đệm mào đầu, không phải tiểu từ chốt câu). Filler mang
    nghĩa (tự dưng/kiểu/ấy) chỉ được rút khi head_class=STATEMENT — fix "tự
    dưng kiếm giúp tôi chỗ thành phố Đà Nẵng nhé" (câu mệnh lệnh, "tự dưng"
    sai nghĩa).

    {category_noun} (nếu template khai báo) fill độc lập với {entity} —
    danh từ chung cho "loại địa điểm đang tìm quanh {entity}", chọn uniform
    trên toàn bộ pool phẳng lexicon.category_nouns (xem lexicon.yaml).
    """
    result = dict(slots)

    if "opener" in result:
        result["opener"] = _draw_slot_value(
            "opener", register, config, lexicon, rng, head_class=head_class, self_asks=self_asks
        )
    if "closer" in result:
        result["closer"] = _draw_slot_value(
            "closer", register, config, lexicon, rng, tail_class=tail_class, self_helps=self_helps
        )
    if "category_noun" in result:
        pool = tuple(noun for nouns in lexicon.category_nouns.values() for noun in nouns)
        if pool:
            result["category_noun"] = pool[rng.randrange(len(pool))]
        elif counters is not None:
            counters["category_noun_pool_empty"] += 1

    filler_rate = config.lexicon_rates.get("filler", {}).get(register, 0.0)
    if rng.random() < filler_rate:
        pool = _filler_pool(head_class, lexicon)
        if not pool:
            if counters is not None:
                counters["filler_skipped_head_class"] += 1
        elif "opener" in result:
            filler = pool[rng.randrange(len(pool))]
            result["opener"] = _merge_filler(result["opener"], filler)
        elif counters is not None:
            counters["filler_skipped_no_opener"] += 1

    return result


# ---------------------------------------------------------------------------
# draw_axes / compose_entity — "generator giữ quy tắc, config giữ số"
# (docs/generation.md#config-variation-yaml, đoạn "Applicability là logic
# của code, không phải config").
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AxisDraw:
    """Kết quả rút 6 trục variation cho MỘT entity, đã áp dụng luật ép
    applicability (draw_axes). `admin_prefix` là Ý ĐỊNH rút được từ config —
    compose_entity() có thể phải ép hiện thêm tiền tố mang nghĩa (ngõ/hẻm/
    ngách/kiệt) bất kể giá trị này; EntityPhrase.admin_prefix_used mới là
    giá trị THỰC TẾ xuất hiện trong text, dùng để ghi cột `variation`.

    `legacy_marker` cùng bản chất: là Ý ĐỊNH rút được từ config (đã gate
    theo name_variant=legacy Ở LÚC RÚT), nhưng resolve_province() có thể
    fallback name_variant thực tế về CURRENT (tỉnh chỉ có legacy alias
    trùng tên hiện hành, hoặc không có spoken alias) — compose_entity() chỉ
    thực sự nối "cũ" khi EntityPhrase.variant_used == LEGACY, xem
    aliases.ResolvedProvince.variant_used.

    `hierarchy_admin_prefix` là trục ĐỘC LẬP với `admin_prefix`, dù rút từ
    CÙNG một config axis (axes.admin_prefix): `admin_prefix` quyết định
    tiền tố cho TÊN CỦA CHÍNH ENTITY (ép absent với POI — "tên đã tự mang
    loại", xem draw_axes rule #4); `hierarchy_admin_prefix` quyết định tiền
    tố cho tên TỈNH/HUYỆN được NHẮC THÊM trong đuôi hierarchy (POI_DISTRICT/
    FULL_ADDRESS) — một quyết định khác hẳn, không phụ thuộc category của
    entity gốc (vd "Tượng Mẹ Nhu" không tự mang tiền tố, nhưng đuôi
    hierarchy của nó vẫn tự nhiên ở cả hai dạng "ở Đà Nẵng" và "ở thành phố
    Đà Nẵng"). Dùng thẳng `admin_prefix` cho việc này sẽ ép mọi POI về
    100% absent — chỉ đổi hướng bias (từng 99% present khi hierarchy tail
    nối vô điều kiện), không phải fix diversity thật (bug user báo cáo:
    "Tượng Mẹ Nhu ở thành phố Đà Nẵng" — cần CẢ hai dạng cùng xuất hiện).
    """

    name_variant: NameVariant
    number_style: NumberStyle
    number_prefix: bool
    admin_prefix: bool
    hierarchy_depth: HierarchyDepth
    legacy_marker: bool
    hierarchy_admin_prefix: bool


@dataclass(frozen=True, slots=True)
class EntityPhrase:
    """Kết quả compose_entity() — text đã sẵn sàng đổ vào slot {entity},
    cộng field audit cho Sample (docs/output.md#csv-schema).
    """

    text: str
    admin_prefix_used: bool
    province_legacy: str | None = None
    district: str | None = None
    ward: str | None = None
    street: str | None = None
    address: str | None = None
    variant_used: NameVariant = NameVariant.CURRENT
    """NameVariant THỰC SỰ đã dùng trong text — có thể khác AxisDraw.name_variant
    khi resolve_province() phải fallback (xem aliases.ResolvedProvince.variant_used).
    generator.py ghi Sample.name_variant theo field này, không theo
    draw.name_variant, để cột audit không nói dối."""


_ANY_DIGIT = re.compile(r"\d")

# Tiền tố loại đường nhúng sẵn trong tên (khảo sát data/master/streets.csv:
# "Hẻm" 10.141, "Đường" 5.183, "Ngõ" 4.165, "Ngách" 1.418, "Phố" 1.161,
# "Kiệt" 624). Đường/Phố là trang trí, có thể lược bỏ theo admin_prefix.
# Ngõ/Hẻm/Ngách/Kiệt mang nghĩa — bỏ đi là đổi nghĩa địa chỉ, luôn giữ.
_STREET_PREFIX_TOKENS: frozenset[str] = frozenset({"Đường", "Phố", "Ngõ", "Hẻm", "Ngách", "Kiệt"})
_STREET_PREFIX_TOKENS_BY_CASEFOLD: dict[str, str] = {t.casefold(): t for t in _STREET_PREFIX_TOKENS}
_DECORATIVE_STREET_PREFIXES: frozenset[str] = frozenset({"Đường", "Phố"})


def _split_street_prefix(name: str) -> tuple[str | None, str]:
    """Tách token tiền tố loại đường nhúng sẵn ở đầu tên (nếu có) khỏi phần
    tên lõi. Trả (None, name) nếu không có tiền tố nhận diện được.

    So khớp không phân biệt hoa/thường — tên đường thô từ OSM đôi khi viết
    thường ("đường 21" thay vì "Đường 21"); nếu so khớp case-sensitive, nhánh
    admin_prefix random pool sẽ chạy đè lên và tạo cặp trùng ("phố đường
    hai mươi mốt"). Trả về token dạng chuẩn hoá (viết hoa) bất kể casing gốc,
    để so khớp _DECORATIVE_STREET_PREFIXES phía sau luôn đúng.
    """
    head, _, rest = name.partition(" ")
    canonical = _STREET_PREFIX_TOKENS_BY_CASEFOLD.get(head.casefold())
    if canonical is not None:
        return canonical, rest
    return None, name


def _name_embeds_type(name: str, type_: str) -> bool:
    """True nếu tên đã tự mang sẵn từ loại ở đầu (vd "Quận 5" mang sẵn
    "Quận" — số ở đây LÀ tên, không phải phần bổ sung). Số quận/phường kiểu
    này khá phổ biến ở VN; nối thêm district.type/ward.type vào trước sẽ
    tạo cặp trùng "quận Quận năm"."""
    head = name.split(" ", 1)[0]
    type_head = type_.split(" ", 1)[0]
    return head.casefold() == type_head.casefold()


def _lowercase_embedded_admin_words(text: str) -> str:
    """Hạ chữ hoa mọi token TRÙNG KHỚP tiền tố loại đường (Đường/Phố/Ngõ/
    Hẻm/Ngách/Kiệt) XUẤT HIỆN Ở BẤT KỲ VỊ TRÍ NÀO trong text — không chỉ
    token đầu. Cần thiết cho tên lồng nhiều lớp kiểu "Ngõ 163 Đường Nguyễn
    Khang": _split_street_prefix chỉ tách được lớp NGOÀI CÙNG ("Ngõ"), lớp
    "Đường" bên trong (không phải token đầu) vẫn còn nguyên hoa nếu không
    xử lý riêng ở đây.

    CHỈ áp cho core_text của category STREET/ADDRESS (xem
    core_text_needs_prefix_lowering ở compose_entity) — 6 token này CÓ THỂ
    là một phần tên riêng thật ở category khác (landmark "Phố cổ Hội An",
    "Động Thiên Đường"), nên không được áp toàn cục cho mọi text composed.
    """
    return " ".join(w.lower() if w in _STREET_PREFIX_TOKENS else w for w in text.split(" "))


def _number_leads(entity: LocationRef, category: Category) -> bool:
    """True nếu số nằm ở ĐẦU tên (số nhà synthesize, hoặc kiểu "Hẻm 553")
    — number_prefix ("số ...") chỉ có ý nghĩa trong trường hợp này, không
    áp cho số ở đuôi kiểu "Trung Yên 11" (docs/generation.md#variation-axes,
    quyết định phase 6).
    """
    if category == Category.ADDRESS:
        return True
    _, core = _split_street_prefix(entity.name)
    return bool(core) and core[0].isdigit()


def draw_axes(
    entity: LocationRef, cell: Cell, register: Register, config: VariationConfig, rng: random.Random
) -> AxisDraw:
    """Rút 6 trục variation cho một entity, áp dụng luật ép applicability —
    "generator giữ quy tắc, config chỉ giữ số" (docs/generation.md:123).
    Thứ tự rút CÓ phụ thuộc: hierarchy_depth trước vì name_variant đọc kết
    quả của nó (một tỉnh cũ chỉ hợp lý khi câu thực sự nhắc tên tỉnh);
    legacy_marker đọc kết quả của name_variant vì chỉ có nghĩa khi legacy
    được rút.
    """
    category = cell.category

    # 5. hierarchy_depth
    depth_weights = config.axes["hierarchy_depth"].resolve(register, category)
    hierarchy_depth = HierarchyDepth(pick_weighted(depth_weights, rng))
    if hierarchy_depth == HierarchyDepth.POI_DISTRICT and not entity.district_id:
        # district_id rỗng ở 100% street/POI trong master hiện tại (xem
        # docs/data-model.md#nguồn-dữ-liệu--build) -> degrade về
        # full_address (đuôi cấp tỉnh) — đường duy nhất để câu street/POI
        # nhắc được tên tỉnh cũ, điều kiện cần cho coverage 63/63.
        hierarchy_depth = HierarchyDepth.FULL_ADDRESS

    # 1. name_variant — chỉ có ý nghĩa khi câu thực sự nhắc tên tỉnh: bản
    # thân entity là province, hoặc đuôi hierarchy đi tới full_address.
    mentions_province = entity.subcategory == Subcategory.PROVINCE or hierarchy_depth == HierarchyDepth.FULL_ADDRESS
    if not mentions_province:
        name_variant = NameVariant.CURRENT
    else:
        nv_weights = config.axes["name_variant"].resolve(register, category)
        name_variant = NameVariant(pick_weighted(nv_weights, rng))

    # 1b. legacy_marker ("cũ") — chỉ có nghĩa khi name_variant=legacy VỪA
    # được rút ở trên. Đây là Ý ĐỊNH, không phải quyết định cuối: alias
    # thật sự chọn được có thể fallback về CURRENT bên trong
    # resolve_province() (tỉnh không sáp nhập, mọi legacy alias trùng tên
    # hiện hành) — compose_entity() kiểm EntityPhrase.variant_used trước
    # khi thực sự nối "cũ" vào text.
    if name_variant == NameVariant.LEGACY:
        lm_weights = config.axes["legacy_marker"].resolve(register, category)
        legacy_marker = pick_weighted(lm_weights, rng) == "present"
    else:
        legacy_marker = False

    # 2. number_style — chỉ có ý nghĩa khi entity thực sự mang số (hoặc
    # category=address, số nhà luôn synthesize).
    entity_has_number = category == Category.ADDRESS or bool(_ANY_DIGIT.search(entity.name))
    if not entity_has_number:
        number_style = NumberStyle.NONE
    else:
        ns_weights = config.axes["number_style"].resolve(register, category)
        number_style = NumberStyle(pick_weighted(ns_weights, rng))
        if number_style == NumberStyle.NONE:
            # NONE không bao giờ được hiểu là "để nguyên chữ số" (hard rule
            # #1) — remap sang cardinal khi entity thực sự có số.
            number_style = NumberStyle.CARDINAL

    # 3. number_prefix ("số ...") — chỉ có nghĩa khi number_style != none
    # và số nằm ở đầu tên.
    if number_style != NumberStyle.NONE and _number_leads(entity, category):
        np_weights = config.axes["number_prefix"].resolve(register, category)
        number_prefix = pick_weighted(np_weights, rng) == "present"
    else:
        number_prefix = False

    # 4. admin_prefix — chỉ áp cho subcategory có tiền tố hành chính thật
    # (province/district/ward/street/address); POI ép absent vì tên đã tự
    # mang loại (vd "Bệnh viện...", "Chùa...").
    if category in (Category.ADMIN_UNIT, Category.STREET, Category.ADDRESS):
        ap_weights = config.axes["admin_prefix"].resolve(register, category)
        admin_prefix = pick_weighted(ap_weights, rng) == "present"
    else:
        admin_prefix = False

    # 4b. hierarchy_admin_prefix — chỉ có ý nghĩa khi câu thực sự có đuôi
    # hierarchy (POI_DISTRICT/FULL_ADDRESS); rút từ CÙNG axes.admin_prefix
    # nhưng category=None (không phải category của entity — đuôi hierarchy
    # luôn nhắc một ĐƠN VỊ HÀNH CHÍNH, category gốc của entity không liên
    # quan) nên resolve() rơi về by_register, KHÔNG bị POI-forces-absent
    # (rule #4 ở trên) chi phối — xem AxisDraw.hierarchy_admin_prefix.
    if hierarchy_depth != HierarchyDepth.POI_ONLY:
        hap_weights = config.axes["admin_prefix"].resolve(register, None)
        hierarchy_admin_prefix = pick_weighted(hap_weights, rng) == "present"
    else:
        hierarchy_admin_prefix = False

    return AxisDraw(
        name_variant=name_variant,
        number_style=number_style,
        number_prefix=number_prefix,
        admin_prefix=admin_prefix,
        hierarchy_depth=hierarchy_depth,
        legacy_marker=legacy_marker,
        hierarchy_admin_prefix=hierarchy_admin_prefix,
    )


def _synthesize_house_number(config: AddressSynthesisConfig, rng: random.Random) -> str:
    """Sinh một số nhà dạng chữ-số/ký hiệu (vd "123", "45/7", "88B") — dùng
    rng của cell, không có entity address riêng trong master data
    (docs/sampling.md#entity_pool).
    """
    n = rng.randint(config.house_number_min, config.house_number_max)
    number_str = str(n)
    if rng.random() < config.alley_suffix_rate:
        number_str = f"{number_str}/{rng.randint(1, 99)}"
    if rng.random() < config.letter_suffix_rate:
        letter = chr(ord("A") + rng.randrange(26))
        number_str = f"{number_str}{letter}"
    return number_str


def _apply_legacy_marker(text: str, apply: bool, variant_used: NameVariant) -> str:
    """Nối "cũ" sau tên tỉnh — cách nói phổ biến nhất hiện nay để phân biệt
    tên tỉnh cũ với tỉnh mới sau sáp nhập 1/7/2025 (vd "Bình Dương cũ",
    "Hà Tây cũ"). Chỉ áp khi `apply` (legacy_marker được rút) VÀ
    `variant_used` THỰC SỰ là LEGACY — resolve_province() có thể đã fallback
    về CURRENT (tỉnh không sáp nhập, hoặc không có spoken alias), và khi đó
    nối "cũ" vào tên hiện hành sẽ sai (vd "Nghệ An cũ" — Nghệ An chưa từng
    đổi tên)."""
    if apply and variant_used == NameVariant.LEGACY:
        return f"{text} cũ"
    return text


def _hierarchy_tail(
    entity: LocationRef, draw: AxisDraw, master: MasterData, rng: random.Random
) -> tuple[str, str | None, str | None, NameVariant, bool]:
    """Đuôi hierarchy nối sau phần lõi entity — trả (tail_text, district
    audit, province_legacy audit, variant_used, prefix_used). tail_text rỗng
    nếu poi_only, hoặc nếu bản thân entity đã là province/district (không
    cần đuôi trỏ lên chính nó). variant_used là NameVariant THỰC SỰ đã dùng
    để dựng tail_text (xem aliases.ResolvedProvince.variant_used) —
    NameVariant.CURRENT khi tail không nhắc tên tỉnh (poi_only/poi_district)
    hoặc bản thân entity là province/district.

    `prefix_used`: đuôi hierarchy có thực sự mang tiền tố hành chính
    ("quận"/"huyện"/"tỉnh"/"thành phố") hay không — chỉ present khi
    draw.hierarchy_admin_prefix VÀ bản thân tên có nhận tiền tố được (tên
    tự mang loại như "Quận 5", hoặc alias tự cấm tiền tố như "thủ đô", luôn
    absent bất kể draw). Cả hai dạng ("ở Đà Nẵng" lẫn "ở thành phố Đà Nẵng")
    đều tự nhiên trong tiếng Việt nói (hard rule #9) — xem
    AxisDraw.hierarchy_admin_prefix.
    """
    if draw.hierarchy_depth == HierarchyDepth.POI_ONLY:
        return "", None, None, NameVariant.CURRENT, False

    if draw.hierarchy_depth == HierarchyDepth.POI_DISTRICT:
        if entity.subcategory in (Subcategory.PROVINCE, Subcategory.DISTRICT):
            return "", None, None, NameVariant.CURRENT, False
        district = master.districts.get(entity.district_id) if entity.district_id else None
        if district is None:
            # Không resolve được (district_id rỗng) — draw_axes lẽ ra đã
            # degrade trường hợp này về full_address; đây là lưới an toàn.
            return "", None, None, NameVariant.CURRENT, False
        district_name_verbalized = (
            verbalize_entity_name(
                district.name,
                draw.number_style if draw.number_style != NumberStyle.NONE else NumberStyle.CARDINAL,
            )
            if _ANY_DIGIT.search(district.name)
            else district.name
        )
        if _name_embeds_type(district.name, district.type) or not draw.hierarchy_admin_prefix:
            return f" ở {district_name_verbalized}", district.name, None, NameVariant.CURRENT, False
        return (
            f" ở {district.type} {district_name_verbalized}",
            district.name,
            None,
            NameVariant.CURRENT,
            True,
        )

    # FULL_ADDRESS
    if entity.subcategory == Subcategory.PROVINCE:
        return "", None, None, NameVariant.CURRENT, False
    province = master.provinces.get(entity.province_id)
    if province is None:
        return "", None, None, NameVariant.CURRENT, False
    from navtext.aliases import has_unambiguous_legacy_alias, resolve_province  # import trễ — tránh vòng lặp

    allowed_aliases = (
        frozenset({entity.legacy_alias_hint}) if entity.legacy_alias_hint else None
    )
    # A4: entity KHÔNG phải chính province này (đây luôn là nhánh non-
    # province — nhánh subcategory==PROVINCE đã return sớm ở trên) — chỉ
    # cho phép LEGACY khi biết chắc alias nào đúng: có hint riêng, hoặc
    # tỉnh chỉ có 1 legacy alias nên không có gì để nhầm. Không có cả hai
    # -> fallback CURRENT, tránh gán sai địa lý (fix bug user báo "Tượng Mẹ
    # Nhu ở tỉnh Quảng Nam cũ" — tượng thực tế ở Đà Nẵng cũ). Coverage
    # 63/63 không phụ thuộc nhánh này — đạt qua cell admin_unit, nơi entity
    # CHÍNH LÀ province (không đi qua _hierarchy_tail).
    name_variant = draw.name_variant
    if (
        name_variant == NameVariant.LEGACY
        and allowed_aliases is None
        and not has_unambiguous_legacy_alias(entity.province_id, master)
    ):
        name_variant = NameVariant.CURRENT
    resolved = resolve_province(
        province.name_current, name_variant, master, rng, allowed_aliases=allowed_aliases
    )
    legacy_audit = resolved.text if resolved.variant_used == NameVariant.LEGACY else None
    resolved_text = _apply_legacy_marker(resolved.text, draw.legacy_marker, resolved.variant_used)
    if resolved.admin_prefix and draw.hierarchy_admin_prefix:
        return (
            f" ở {resolved.admin_prefix} {resolved_text}",
            None,
            legacy_audit,
            resolved.variant_used,
            True,
        )
    return f" ở {resolved_text}", None, legacy_audit, resolved.variant_used, False


def compose_entity(
    entity: LocationRef,
    cell: Cell,
    draw: AxisDraw,
    master: MasterData,
    lexicon: Lexicon,
    config: VariationConfig,
    rng: random.Random,
) -> EntityPhrase:
    """Dựng chuỗi {entity} hoàn chỉnh: tiền tố hành chính + tên (đã resolve
    theo name_variant + verbalize số) + đuôi hierarchy.

    Chữ ký nhận thêm `cell` và `config` so với bản phác thảo ban đầu ở
    docs/generation.md — `cell.category` là nguồn DUY NHẤT phân biệt được
    cell address với cell street (entity_pool(..., ADDRESS) trả về đúng
    LocationRef.category=STREET, xem sampling.entity_pool), và
    `config.address_synthesis` là tham số bắt buộc để sinh số nhà (hard
    rule #8 — không hardcode dải số trong code).
    """
    district_name: str | None = None
    ward_name: str | None = None
    street_name: str | None = None
    address_raw: str | None = None
    province_legacy: str | None = None
    admin_prefix_used = False
    # variant_used mặc định CURRENT — chỉ nhánh PROVINCE (bên dưới) hoặc
    # tail FULL_ADDRESS (_hierarchy_tail) mới có thể set khác đi, vì đó là
    # 2 nơi DUY NHẤT gọi resolve_province() (xem aliases.py).
    province_variant_used: NameVariant = NameVariant.CURRENT
    # _lowercase_embedded_admin_words chỉ có ý nghĩa cho tên ĐƯỜNG lồng
    # nhiều lớp (vd "Ngõ 163 Đường Nguyễn Khang") — bật đúng ở 2 nhánh dùng
    # _split_street_prefix (ADDRESS/STREET) bên dưới, tắt cho các nhánh còn
    # lại vì có thể đụng vào tên riêng thật chứa 1 trong 6 token đó (vd
    # landmark "Phố cổ Hội An", "Động Thiên Đường" — xem docs/generation.md).
    core_text_needs_prefix_lowering = False

    if cell.category == Category.ADDRESS:
        house_number = _synthesize_house_number(config.address_synthesis, rng)
        house_words = read_house_number(house_number, draw.number_style)
        if draw.number_prefix:
            house_words = f"số {house_words}"

        street_prefix, street_core = _split_street_prefix(entity.name)
        street_core_verbalized = (
            verbalize_entity_name(street_core, draw.number_style)
            if _ANY_DIGIT.search(street_core)
            else street_core
        )
        if street_prefix is not None:
            if street_prefix in _DECORATIVE_STREET_PREFIXES:
                if draw.admin_prefix:
                    street_text = f"{street_prefix.lower()} {street_core_verbalized}"
                    admin_prefix_used = True
                else:
                    street_text = street_core_verbalized
            else:
                street_text = f"{street_prefix.lower()} {street_core_verbalized}"
                admin_prefix_used = True
        elif draw.admin_prefix:
            pool = lexicon.admin_prefixes.get("street") or ("đường",)
            street_text = f"{pool[rng.randrange(len(pool))]} {street_core_verbalized}"
            admin_prefix_used = True
        else:
            street_text = street_core_verbalized

        core_text = f"{house_words} {street_text}"
        street_name = entity.name
        address_raw = f"{house_number} {entity.name}"
        core_text_needs_prefix_lowering = True

    elif entity.subcategory == Subcategory.PROVINCE:
        from navtext.aliases import resolve_province  # import trễ — tránh vòng lặp

        resolved = resolve_province(entity.name, draw.name_variant, master, rng)
        province_variant_used = resolved.variant_used
        if resolved.variant_used == NameVariant.LEGACY:
            province_legacy = resolved.text
        name_text = _apply_legacy_marker(resolved.text, draw.legacy_marker, resolved.variant_used)
        if draw.admin_prefix and resolved.admin_prefix:
            core_text = f"{resolved.admin_prefix} {name_text}"
            admin_prefix_used = True
        else:
            # draw.admin_prefix absent, HOẶC alias này tự cấm tiền tố
            # (resolved.admin_prefix == "", vd "thủ đô", "thành phố Hồ Chí
            # Minh" đã tự mang nghĩa/tiền tố) — admin_prefix_used phải
            # phản ánh đúng những gì thực sự xuất hiện trong text, không
            # phải ý định rút được từ trục variation.
            core_text = name_text

    elif entity.subcategory == Subcategory.DISTRICT:
        district = master.districts.get(entity.entity_id)
        name_text = district.name if district is not None else entity.name
        name_text_verbalized = (
            verbalize_entity_name(
                name_text,
                draw.number_style if draw.number_style != NumberStyle.NONE else NumberStyle.CARDINAL,
            )
            if _ANY_DIGIT.search(name_text)
            else name_text
        )
        if draw.admin_prefix and district is not None and not _name_embeds_type(name_text, district.type):
            core_text = f"{district.type} {name_text_verbalized}"
            admin_prefix_used = True
        else:
            # Tên đã tự mang từ loại (vd "Quận 5" — số là tên, không phải
            # phần thêm vào), hoặc admin_prefix absent — không nối trùng
            # (fix "quận Quận năm").
            core_text = name_text_verbalized
        district_name = name_text

    elif entity.subcategory == Subcategory.WARD:
        ward = master.wards.get(entity.entity_id)
        name_text = ward.name if ward is not None else entity.name
        name_text_verbalized = (
            verbalize_entity_name(
                name_text,
                draw.number_style if draw.number_style != NumberStyle.NONE else NumberStyle.CARDINAL,
            )
            if _ANY_DIGIT.search(name_text)
            else name_text
        )
        if draw.admin_prefix and ward is not None and not _name_embeds_type(name_text, ward.type):
            core_text = f"{ward.type} {name_text_verbalized}"
            admin_prefix_used = True
        else:
            core_text = name_text_verbalized
        ward_name = name_text

    elif entity.category == Category.STREET:
        street_prefix, street_core = _split_street_prefix(entity.name)
        street_core_verbalized = (
            verbalize_entity_name(street_core, draw.number_style)
            if _ANY_DIGIT.search(street_core)
            else street_core
        )
        if street_prefix is not None:
            if street_prefix in _DECORATIVE_STREET_PREFIXES:
                if draw.admin_prefix:
                    core_text = f"{street_prefix.lower()} {street_core_verbalized}"
                    admin_prefix_used = True
                else:
                    core_text = street_core_verbalized
            else:
                core_text = f"{street_prefix.lower()} {street_core_verbalized}"
                admin_prefix_used = True
        elif draw.admin_prefix:
            pool = lexicon.admin_prefixes.get("street") or ("đường",)
            core_text = f"{pool[rng.randrange(len(pool))]} {street_core_verbalized}"
            admin_prefix_used = True
        else:
            core_text = street_core_verbalized
        street_name = entity.name
        core_text_needs_prefix_lowering = True

    else:
        # POI (landmark/transport/public_facility/commercial/poi_other) —
        # tên đã tự mang loại (vd "Bệnh viện...", "Chùa..."), admin_prefix
        # luôn absent theo draw_axes rule #4.
        core_text = (
            verbalize_entity_name(entity.name, draw.number_style)
            if draw.number_style != NumberStyle.NONE and _ANY_DIGIT.search(entity.name)
            else entity.name
        )

    tail_text, tail_district, tail_legacy, tail_variant_used, tail_prefix_used = _hierarchy_tail(
        entity, draw, master, rng
    )
    if core_text_needs_prefix_lowering:
        core_text = _lowercase_embedded_admin_words(core_text)
    text = f"{core_text}{tail_text}"

    # entity tự là province và tail (FULL_ADDRESS) là 2 nơi DUY NHẤT có thể
    # cho variant_used khác CURRENT, và không bao giờ cùng lúc (tail luôn
    # rỗng khi entity.subcategory == PROVINCE — xem _hierarchy_tail) — ghép
    # bằng "or" an toàn vì đúng 1 trong 2 vế khác CURRENT tại một thời điểm.
    variant_used = (
        province_variant_used
        if province_variant_used != NameVariant.CURRENT
        else tail_variant_used
    )
    # admin_prefix_used phải phản ánh MỌI tiền tố hành chính THỰC SỰ xuất
    # hiện trong text, kể cả tiền tố nằm trong đuôi hierarchy (trước bản sửa
    # này, đuôi hierarchy luôn có tiền tố nên không cần audit riêng — giờ nó
    # là biến, phải OR vào cùng field với core_text để cột `variation` (CSV)
    # không nói dối khi core_text absent nhưng tail present hoặc ngược lại).
    admin_prefix_used = admin_prefix_used or tail_prefix_used

    return EntityPhrase(
        text=text,
        admin_prefix_used=admin_prefix_used,
        province_legacy=province_legacy or tail_legacy,
        district=district_name or tail_district,
        ward=ward_name,
        street=street_name,
        address=address_raw,
        variant_used=variant_used,
    )
