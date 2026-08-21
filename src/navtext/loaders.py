"""Đọc data/master/*.csv thành MasterData in-memory.

Xem docs/data-model.md#master-dataset-schema.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from navtext.schema import Category, LocationRef, Source, Subcategory


@dataclass(frozen=True, slots=True)
class Province:
    province_id: str
    name_current: str
    admin_status: str  # "unchanged" | "merged"
    region: str
    is_municipality: bool
    popularity_tier: int


@dataclass(frozen=True, slots=True)
class ProvinceAlias:
    province_id: str
    alias: str
    alias_type: str  # "legacy" | "spoken" | "abbrev_expanded"
    weight: float
    admin_prefix: str  # "tỉnh" | "thành phố" | "" — tiền tố đúng của CHÍNH alias này


@dataclass(frozen=True, slots=True)
class District:
    district_id: str
    province_id: str
    name: str
    type: str
    status: str  # "current" | "abolished"
    legacy_alias_hint: str | None  # sub-region hint — xem docs/data-model.md#sub-region-hint


@dataclass(frozen=True, slots=True)
class Ward:
    ward_id: str
    district_id: str | None  # nguồn curate tay — quận/huyện cũ trước 1/7/2025
    province_id: str | None  # nguồn OSM — mô hình 2 cấp hiện hành (tỉnh -> xã)
    name: str
    type: str
    legacy_alias_hint: str | None = None  # sub-region hint — chỉ từ nguồn OSM (province_id path)


@dataclass(frozen=True, slots=True)
class Street:
    street_id: str
    province_id: str
    district_id: str | None
    name: str
    street_type: str


@dataclass(frozen=True, slots=True)
class Poi:
    poi_id: str
    name: str
    category: Category
    subcategory: Subcategory
    ward_id: str | None
    district_id: str | None
    province_id: str | None
    popularity_tier: int
    source: Source
    osm_id: str
    legacy_alias_hint: str | None  # sub-region hint — xem docs/data-model.md#sub-region-hint


@dataclass(frozen=True, slots=True)
class MasterData:
    """In-memory view của toàn bộ data/master/*.csv, cộng index build sẵn
    lúc load để phase 3 (sampling) và phase 6 (generator) không phải scan
    lại toàn bảng mỗi cell.
    """

    provinces: Mapping[str, Province]
    aliases_by_province: Mapping[str, tuple[ProvinceAlias, ...]]
    province_id_by_alias: Mapping[str, str]  # alias đã casefold -> province_id
    districts: Mapping[str, District]
    wards: Mapping[str, Ward]
    streets: Mapping[str, Street]
    pois: Mapping[str, Poi]
    entities_by_province: Mapping[str, tuple[LocationRef, ...]]


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _opt(value: str) -> str | None:
    return value if value else None


def _street_to_entity(street: Street) -> LocationRef:
    return LocationRef(
        entity_id=street.street_id,
        name=street.name,
        category=Category.STREET,
        subcategory=Subcategory(street.street_type),
        province_id=street.province_id,
        district_id=street.district_id,
        ward_id=None,
        street_id=street.street_id,
        popularity_tier=2,
        source=Source.OSM,
    )


def _poi_to_entity(poi: Poi) -> LocationRef:
    return LocationRef(
        entity_id=poi.poi_id,
        name=poi.name,
        category=poi.category,
        subcategory=poi.subcategory,
        province_id=poi.province_id or "",
        district_id=poi.district_id,
        ward_id=poi.ward_id,
        street_id=None,
        popularity_tier=poi.popularity_tier,
        source=poi.source,
        legacy_alias_hint=poi.legacy_alias_hint,
    )


def load_master(master_dir: Path) -> MasterData:
    """Đọc data/master/*.csv (provinces, province_alias, districts, wards,
    streets, pois) thành MasterData. Không đọc file gì ở import time — chỉ
    khi hàm này được gọi tường minh.
    """
    provinces: dict[str, Province] = {}
    for row in _read_rows(master_dir / "provinces.csv"):
        provinces[row["province_id"]] = Province(
            province_id=row["province_id"],
            name_current=row["name_current"],
            admin_status=row["admin_status"],
            region=row["region"],
            is_municipality=row["is_municipality"] == "True",
            popularity_tier=int(row["popularity_tier"]),
        )

    aliases_by_province: dict[str, list[ProvinceAlias]] = defaultdict(list)
    province_id_by_alias: dict[str, str] = {}
    for row in _read_rows(master_dir / "province_alias.csv"):
        alias = ProvinceAlias(
            province_id=row["province_id"],
            alias=row["alias"],
            alias_type=row["alias_type"],
            weight=float(row["weight"]),
            admin_prefix=row["admin_prefix"],
        )
        aliases_by_province[alias.province_id].append(alias)
        province_id_by_alias[alias.alias.casefold()] = alias.province_id
    for p in provinces.values():
        province_id_by_alias.setdefault(p.name_current.casefold(), p.province_id)

    districts: dict[str, District] = {}
    for row in _read_rows(master_dir / "districts.csv"):
        districts[row["district_id"]] = District(
            district_id=row["district_id"],
            province_id=row["province_id"],
            name=row["name"],
            type=row["type"],
            status=row["status"],
            legacy_alias_hint=_opt(row.get("legacy_alias", "")),
        )

    wards: dict[str, Ward] = {}
    for row in _read_rows(master_dir / "wards.csv"):
        wards[row["ward_id"]] = Ward(
            ward_id=row["ward_id"],
            district_id=_opt(row["district_id"]),
            province_id=_opt(row.get("province_id", "")),
            name=row["name"],
            type=row["type"],
            legacy_alias_hint=_opt(row.get("legacy_alias", "")),
        )

    streets: dict[str, Street] = {}
    for row in _read_rows(master_dir / "streets.csv"):
        streets[row["street_id"]] = Street(
            street_id=row["street_id"],
            province_id=row["province_id"],
            district_id=_opt(row["district_id"]),
            name=row["name"],
            street_type=row["street_type"],
        )

    pois: dict[str, Poi] = {}
    for row in _read_rows(master_dir / "pois.csv"):
        pois[row["poi_id"]] = Poi(
            poi_id=row["poi_id"],
            name=row["name"],
            category=Category(row["category"]),
            subcategory=Subcategory(row["subcategory"]),
            ward_id=_opt(row["ward_id"]),
            district_id=_opt(row["district_id"]),
            province_id=_opt(row["province_id"]),
            popularity_tier=int(row["popularity_tier"]),
            source=Source(row["source"]),
            osm_id=row["osm_id"],
            legacy_alias_hint=_opt(row.get("legacy_alias_hint", "")),
        )

    entities_by_province: dict[str, list[LocationRef]] = defaultdict(list)
    for street in streets.values():
        entities_by_province[street.province_id].append(_street_to_entity(street))
    for poi in pois.values():
        if poi.province_id:
            entities_by_province[poi.province_id].append(_poi_to_entity(poi))

    return MasterData(
        provinces=provinces,
        aliases_by_province={k: tuple(v) for k, v in aliases_by_province.items()},
        province_id_by_alias=province_id_by_alias,
        districts=districts,
        wards=wards,
        streets=streets,
        pois=pois,
        entities_by_province={k: tuple(v) for k, v in entities_by_province.items()},
    )
