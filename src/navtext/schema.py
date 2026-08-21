"""Taxonomy và schema dữ liệu cho NavText, code hoá từ docs/data-model.md,
docs/generation.md (variation axes) và docs/output.md (CSV schema).

Đây là contract dùng chung cho toàn bộ pipeline (phase 2–12) — không phase
nào được tự định nghĩa lại category, sentence_type, hay thứ tự cột CSV.
Mọi thay đổi taxonomy phải sửa ở đây trước, rồi mới cập nhật doc.

Module này không có side-effect ở import time: không đọc file, không build
dữ liệu (xem CLAUDE.md#code-conventions).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, fields
from enum import StrEnum
from typing import Mapping


# ---------------------------------------------------------------------------
# Taxonomy — docs/data-model.md#taxonomy
# ---------------------------------------------------------------------------


class Category(StrEnum):
    ADMIN_UNIT = "admin_unit"
    STREET = "street"
    ADDRESS = "address"
    LANDMARK = "landmark"
    TRANSPORT = "transport"
    PUBLIC_FACILITY = "public_facility"
    COMMERCIAL = "commercial"
    POI_OTHER = "poi_other"


class Subcategory(StrEnum):
    # admin_unit
    PROVINCE = "province"
    DISTRICT = "district"
    WARD = "ward"
    # street
    STREET = "street"
    HIGHWAY = "highway"  # quốc lộ / tỉnh lộ
    ALLEY = "alley"  # ngõ / hẻm
    # address
    HOUSE_ADDRESS = "house_address"
    # landmark
    LAKE = "lake"
    BRIDGE = "bridge"
    MONUMENT = "monument"
    HERITAGE_SITE = "heritage_site"
    MARKET = "market"
    MOUNTAIN = "mountain"
    BEACH = "beach"
    MUSEUM = "museum"
    TOURIST_ATTRACTION = "tourist_attraction"
    # transport
    AIRPORT = "airport"
    RAILWAY_STATION = "railway_station"
    BUS_TERMINAL = "bus_terminal"
    FERRY_TERMINAL = "ferry_terminal"
    METRO_STATION = "metro_station"
    # public_facility
    SCHOOL = "school"
    UNIVERSITY = "university"
    HOSPITAL = "hospital"
    GOVERNMENT_OFFICE = "government_office"
    # commercial
    SHOPPING_MALL = "shopping_mall"
    SUPERMARKET = "supermarket"
    HOTEL = "hotel"
    RESTAURANT = "restaurant"
    GAS_STATION = "gas_station"
    BANK = "bank"
    # poi_other
    PARK = "park"
    STADIUM = "stadium"
    INDUSTRIAL_ZONE = "industrial_zone"
    WORSHIP_PLACE = "worship_place"  # chùa / nhà thờ


SUBCATEGORIES: Mapping[Category, frozenset[Subcategory]] = {
    Category.ADMIN_UNIT: frozenset(
        {Subcategory.PROVINCE, Subcategory.DISTRICT, Subcategory.WARD}
    ),
    Category.STREET: frozenset(
        {Subcategory.STREET, Subcategory.HIGHWAY, Subcategory.ALLEY}
    ),
    Category.ADDRESS: frozenset({Subcategory.HOUSE_ADDRESS}),
    Category.LANDMARK: frozenset(
        {
            Subcategory.LAKE,
            Subcategory.BRIDGE,
            Subcategory.MONUMENT,
            Subcategory.HERITAGE_SITE,
            Subcategory.MARKET,
            Subcategory.MOUNTAIN,
            Subcategory.BEACH,
            Subcategory.MUSEUM,
            Subcategory.TOURIST_ATTRACTION,
        }
    ),
    Category.TRANSPORT: frozenset(
        {
            Subcategory.AIRPORT,
            Subcategory.RAILWAY_STATION,
            Subcategory.BUS_TERMINAL,
            Subcategory.FERRY_TERMINAL,
            Subcategory.METRO_STATION,
        }
    ),
    Category.PUBLIC_FACILITY: frozenset(
        {
            Subcategory.SCHOOL,
            Subcategory.UNIVERSITY,
            Subcategory.HOSPITAL,
            Subcategory.GOVERNMENT_OFFICE,
        }
    ),
    Category.COMMERCIAL: frozenset(
        {
            Subcategory.SHOPPING_MALL,
            Subcategory.SUPERMARKET,
            Subcategory.HOTEL,
            Subcategory.RESTAURANT,
            Subcategory.GAS_STATION,
            Subcategory.BANK,
        }
    ),
    Category.POI_OTHER: frozenset(
        {
            Subcategory.PARK,
            Subcategory.STADIUM,
            Subcategory.INDUSTRIAL_ZONE,
            Subcategory.WORSHIP_PLACE,
        }
    ),
}


class SentenceType(StrEnum):
    """docs/generation.md#sentence-types"""

    NAV_COMMAND = "nav_command"
    SEARCH = "search"
    ROUTE_QUESTION = "route_question"
    CONVERSATIONAL = "conversational"
    SHORT_COMMAND = "short_command"
    CONTEXTUAL = "contextual"


class Register(StrEnum):
    FORMAL = "formal"
    NEUTRAL = "neutral"
    CASUAL = "casual"


class LengthClass(StrEnum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class NameVariant(StrEnum):
    """docs/generation.md#variation-axes"""

    CURRENT = "current"
    LEGACY = "legacy"
    SPOKEN_ALIAS = "spoken_alias"


class NumberStyle(StrEnum):
    CARDINAL = "cardinal"
    DIGIT_BY_DIGIT = "digit_by_digit"
    MIXED = "mixed"
    NONE = "none"


class HierarchyDepth(StrEnum):
    POI_ONLY = "poi_only"
    POI_DISTRICT = "poi_district"
    FULL_ADDRESS = "full_address"


class AliasType(StrEnum):
    """docs/data-model.md#mô-hình-34--63--cách-gọi-phổ-biến"""

    LEGACY = "legacy"
    SPOKEN = "spoken"
    ABBREV_EXPANDED = "abbrev_expanded"


class AdminStatus(StrEnum):
    UNCHANGED = "unchanged"
    MERGED = "merged"


class Source(StrEnum):
    OSM = "osm"
    MANUAL = "manual"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LocationRef:
    """Một entity địa danh đã resolve, trả ra bởi sampling/loaders, nhận vào
    bởi generator. Xem docs/data-model.md#master-dataset-schema.
    """

    entity_id: str
    name: str
    category: Category
    subcategory: Subcategory
    province_id: str
    district_id: str | None
    ward_id: str | None
    street_id: str | None
    popularity_tier: int
    source: Source
    legacy_alias_hint: str | None = None


@dataclass(frozen=True, slots=True)
class Cell:
    """Một ô trong plan table — docs/sampling.md#cell-key--rng-theo-cell.

    cell_key và derive_seed là điểm duy nhất trong codebase sinh seed từ
    một cell; các phase khác gọi lại đây, không tự hash lần hai.
    """

    province_id: str
    category: Category
    sentence_type: SentenceType
    variation_profile: str
    target_count: int

    @property
    def cell_key(self) -> str:
        # "|" không xuất hiện trong giá trị enum/id nên an toàn làm separator.
        return "|".join(
            (
                self.province_id,
                self.category.value,
                self.sentence_type.value,
                self.variation_profile,
            )
        )

    def derive_seed(self, global_seed: int) -> int:
        """Seed ổn định qua process/máy/phiên bản Python cho cell này.

        Cố ý dùng hashlib.sha256 thay vì hash() built-in: hash() của Python
        randomize theo PYTHONHASHSEED cho str/tuple chứa str, nên seed sẽ
        đổi giữa các lần chạy — phá golden test (docs/testing.md) và yêu
        cầu byte-identical reproduce (docs/output.md#run-manifest).
        """
        digest = hashlib.sha256(f"{global_seed}|{self.cell_key}".encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big")


@dataclass(frozen=True, slots=True)
class Sample:
    """Một dòng output cuối cùng — docs/output.md#csv-schema.

    Thứ tự field ở đây LÀ thứ tự cột CSV (xem CSV_COLUMNS bên dưới) — đừng
    sắp lại field mà không cập nhật docs/output.md.
    """

    id: str
    text: str
    category: Category
    subcategory: Subcategory
    entity: str
    entity_id: str
    province: str
    province_legacy: str | None
    district: str | None
    ward: str | None
    street: str | None
    address: str | None
    sentence_type: SentenceType
    template_id: str
    variation: str
    name_variant: NameVariant
    number_style: NumberStyle
    register: Register
    source: Source
    seed_cell: str

    def to_row(self) -> dict[str, str]:
        """Ánh xạ sang dict phẳng để ghi CSV — None thành chuỗi rỗng."""
        row: dict[str, str] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if value is None:
                row[f.name] = ""
            elif isinstance(value, StrEnum):
                row[f.name] = value.value
            else:
                row[f.name] = str(value)
        return row


CSV_COLUMNS: tuple[str, ...] = tuple(f.name for f in fields(Sample))
