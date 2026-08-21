"""Filter/search/paginate cho /entities, /templates, /samples — hàm thuần,
không http.server.

Xem docs/web.md#entities cho hợp đồng query string.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Mapping, Sequence, TypeVar

from navtext.loaders import MasterData
from navtext.schema import LocationRef
from navtext.templates import Template

DEFAULT_PAGE_SIZE = 100

T = TypeVar("T")


def fold_search_key(text: str) -> str:
    """NFD + bỏ combining mark + casefold — để "nguyen hue" khớp "Nguyễn
    Huệ". Người dùng gõ tiếng Việt không dấu khi tìm kiếm là chuyện thường.

    "đ"/"Đ" cố tình xử lý riêng: đây là ký tự Unicode base độc lập
    (U+0111/U+0110), KHÔNG phải base+combining mark như "ư"/"ơ" nên NFD
    không tách được — không thay "đ" -> "d" thì "đường" sẽ không khớp
    "duong", hỏng đúng use-case chính của hàm này.
    """
    without_d_stroke = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", without_d_stroke)
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return without_marks.casefold()


@dataclass(frozen=True, slots=True)
class EntityQuery:
    q: str | None = None
    province_id: str | None = None
    category: str | None = None
    subcategory: str | None = None
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE


def all_entities(master: MasterData) -> list[LocationRef]:
    """Flatten toàn bộ entity, sort ổn định theo entity_id — phân trang phải
    cho cùng kết quả ở cùng trang qua nhiều lần gọi.
    """
    entities: list[LocationRef] = []
    for province_entities in master.entities_by_province.values():
        entities.extend(province_entities)
    entities.sort(key=lambda e: e.entity_id)
    return entities


def filter_entities(
    entities: list[LocationRef], query: EntityQuery
) -> list[LocationRef]:
    filtered = entities
    if query.province_id:
        filtered = [e for e in filtered if e.province_id == query.province_id]
    if query.category:
        filtered = [e for e in filtered if e.category.value == query.category]
    if query.subcategory:
        filtered = [e for e in filtered if e.subcategory.value == query.subcategory]
    if query.q:
        key = fold_search_key(query.q)
        filtered = [e for e in filtered if key in fold_search_key(e.name)]
    return filtered


def paginate(items: list[T], page: int, page_size: int) -> tuple[list[T], int]:
    """Trả (items của trang, tổng số item khớp filter). page bắt đầu từ 1.

    Generic để dùng chung cho cả LocationRef (/entities) lẫn Template
    (/templates) — logic phân trang không phụ thuộc kiểu phần tử, copy
    thành hai hàm chỉ tạo chỗ cho hai hành vi lệch nhau về sau.
    """
    safe_page = max(page, 1)
    start = (safe_page - 1) * page_size
    end = start + page_size
    return items[start:end], len(items)


# ---------------------------------------------------------------------------
# Template bank — /templates
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TemplateQuery:
    q: str | None = None
    sentence_type: str | None = None
    register: str | None = None
    length_class: str | None = None
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE


def summarize_samples(rows: Sequence[Mapping[str, str]]) -> dict[str, object]:
    """Đếm nhanh một CSV sample đã generate (đọc bởi cli._cmd_serve qua
    --samples) — dùng để đổ dropdown filter kèm count và KPI card ở
    /samples. Hoạt động trực tiếp trên dict phẳng từ csv.DictReader, không
    parse lại thành Sample/enum (CSV đã là nguồn sự thật, xem
    docs/output.md#csv-schema).
    """
    by_category: Counter[str] = Counter()
    by_province: Counter[str] = Counter()
    by_sentence_type: Counter[str] = Counter()
    by_register: Counter[str] = Counter()
    by_name_variant: Counter[str] = Counter()
    templates: set[str] = set()
    provinces: set[str] = set()
    legacy_mentions = 0

    for row in rows:
        by_category[row.get("category", "")] += 1
        by_province[row.get("province", "")] += 1
        by_sentence_type[row.get("sentence_type", "")] += 1
        by_register[row.get("register", "")] += 1
        by_name_variant[row.get("name_variant", "")] += 1
        templates.add(row.get("template_id", ""))
        provinces.add(row.get("province", ""))
        if row.get("province_legacy"):
            legacy_mentions += 1

    return {
        "total": len(rows),
        "by_category": dict(by_category),
        "by_province": dict(by_province),
        "by_sentence_type": dict(by_sentence_type),
        "by_register": dict(by_register),
        "by_name_variant": dict(by_name_variant),
        "distinct_templates": len(templates),
        "distinct_provinces": len(provinces),
        "legacy_mentions": legacy_mentions,
    }


# ---------------------------------------------------------------------------
# Sample đã generate — /samples (outputs/<run>/*.csv, xem cli._cmd_serve)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SampleQuery:
    q: str | None = None
    province: str | None = None
    category: str | None = None
    sentence_type: str | None = None
    register: str | None = None
    name_variant: str | None = None
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE


def filter_samples(
    rows: Sequence[Mapping[str, str]], query: SampleQuery
) -> list[Mapping[str, str]]:
    """Lọc sample đã generate theo các trục CSV có sẵn — so khớp chuỗi trực
    tiếp (mọi cột đã là giá trị hiển thị được, không cần tra cứu master).
    Search `q` qua fold_search_key() trên cột `text`, giống /entities.
    """
    filtered = list(rows)
    if query.province:
        filtered = [r for r in filtered if r.get("province") == query.province]
    if query.category:
        filtered = [r for r in filtered if r.get("category") == query.category]
    if query.sentence_type:
        filtered = [r for r in filtered if r.get("sentence_type") == query.sentence_type]
    if query.register:
        filtered = [r for r in filtered if r.get("register") == query.register]
    if query.name_variant:
        filtered = [r for r in filtered if r.get("name_variant") == query.name_variant]
    if query.q:
        key = fold_search_key(query.q)
        filtered = [r for r in filtered if key in fold_search_key(r.get("text", ""))]
    return filtered


def filter_templates(
    templates: Sequence[Template], query: TemplateQuery
) -> list[Template]:
    """Lọc template bank theo trục phân loại + tìm trong text.

    Tìm kiếm đi qua `fold_search_key()` giống /entities: template text viết
    thường có dấu, người dùng gõ không dấu vẫn phải khớp.
    """
    filtered = list(templates)
    if query.sentence_type:
        filtered = [t for t in filtered if t.sentence_type.value == query.sentence_type]
    if query.register:
        filtered = [t for t in filtered if t.register.value == query.register]
    if query.length_class:
        filtered = [t for t in filtered if t.length_class.value == query.length_class]
    if query.q:
        key = fold_search_key(query.q)
        filtered = [
            t
            for t in filtered
            if key in fold_search_key(t.text) or key in fold_search_key(t.template_id)
        ]
    return filtered
