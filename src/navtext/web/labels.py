"""Nhãn tiếng Việt + glossary cho lớp trình bày của `navtext serve`.

Lớp thuần trình bày — KHÔNG đụng navtext.schema. Giá trị enum tiếng Anh giữ
nguyên trong data, URL, `/api/report.json`; module này chỉ map sang chuỗi
hiển thị cho HTML. Mọi mapping ở đây bọc `Mapping[..., str]` nên thiếu một
key gây `KeyError` ngay khi render thay vì âm thầm hiện chuỗi rỗng.
"""

from __future__ import annotations

from typing import Mapping

from navtext.schema import (
    Category,
    HierarchyDepth,
    LengthClass,
    NameVariant,
    NumberStyle,
    Register,
    SentenceType,
    Subcategory,
)

CATEGORY_LABELS: Mapping[Category, str] = {
    Category.ADMIN_UNIT: "Đơn vị hành chính",
    Category.STREET: "Đường / phố",
    Category.ADDRESS: "Địa chỉ nhà",
    Category.LANDMARK: "Địa danh",
    Category.TRANSPORT: "Giao thông",
    Category.PUBLIC_FACILITY: "Cơ sở công cộng",
    Category.COMMERCIAL: "Thương mại",
    Category.POI_OTHER: "POI khác",
}

SUBCATEGORY_LABELS: Mapping[Subcategory, str] = {
    Subcategory.PROVINCE: "Tỉnh / thành phố",
    Subcategory.DISTRICT: "Quận / huyện",
    Subcategory.WARD: "Phường / xã",
    Subcategory.STREET: "Đường",
    Subcategory.HIGHWAY: "Quốc lộ / tỉnh lộ",
    Subcategory.ALLEY: "Ngõ / hẻm",
    Subcategory.HOUSE_ADDRESS: "Địa chỉ nhà",
    Subcategory.LAKE: "Hồ",
    Subcategory.BRIDGE: "Cầu",
    Subcategory.MONUMENT: "Tượng đài",
    Subcategory.HERITAGE_SITE: "Di tích",
    Subcategory.MARKET: "Chợ",
    Subcategory.MOUNTAIN: "Núi",
    Subcategory.BEACH: "Bãi biển",
    Subcategory.MUSEUM: "Bảo tàng",
    Subcategory.TOURIST_ATTRACTION: "Điểm du lịch",
    Subcategory.AIRPORT: "Sân bay",
    Subcategory.RAILWAY_STATION: "Ga tàu",
    Subcategory.BUS_TERMINAL: "Bến xe",
    Subcategory.FERRY_TERMINAL: "Bến phà",
    Subcategory.METRO_STATION: "Ga metro",
    Subcategory.SCHOOL: "Trường học",
    Subcategory.UNIVERSITY: "Đại học",
    Subcategory.HOSPITAL: "Bệnh viện",
    Subcategory.GOVERNMENT_OFFICE: "Cơ quan hành chính",
    Subcategory.SHOPPING_MALL: "Trung tâm thương mại",
    Subcategory.SUPERMARKET: "Siêu thị",
    Subcategory.HOTEL: "Khách sạn",
    Subcategory.RESTAURANT: "Nhà hàng",
    Subcategory.GAS_STATION: "Cây xăng",
    Subcategory.BANK: "Ngân hàng",
    Subcategory.PARK: "Công viên",
    Subcategory.STADIUM: "Sân vận động",
    Subcategory.INDUSTRIAL_ZONE: "Khu công nghiệp",
    Subcategory.WORSHIP_PLACE: "Chùa / nhà thờ",
}

ALIAS_TYPE_LABELS: Mapping[str, str] = {
    "legacy": "Tên tỉnh cũ",
    "spoken": "Cách gọi dân gian",
    "abbrev_expanded": "Viết tắt đã mở rộng",
}

ADMIN_STATUS_LABELS: Mapping[str, str] = {
    "unchanged": "Không đổi",
    "merged": "Đã sáp nhập",
    "current": "Còn hiệu lực",
    "abolished": "Đã bỏ (cải cách 1/7/2025)",
}

SOURCE_LABELS: Mapping[str, str] = {
    "osm": "OpenStreetMap",
    "manual": "Nhập tay",
}

TIER_LABELS: Mapping[int, str] = {
    1: "Nổi bật",
    2: "Bình thường",
    3: "Ít phổ biến",
}

SENTENCE_TYPE_LABELS: Mapping[SentenceType, str] = {
    SentenceType.NAV_COMMAND: "Lệnh chỉ đường",
    SentenceType.SEARCH: "Tìm kiếm",
    SentenceType.ROUTE_QUESTION: "Hỏi lộ trình",
    SentenceType.CONVERSATIONAL: "Hội thoại",
    SentenceType.SHORT_COMMAND: "Lệnh ngắn",
    SentenceType.CONTEXTUAL: "Ngữ cảnh",
}

REGISTER_LABELS: Mapping[Register, str] = {
    Register.FORMAL: "Trang trọng",
    Register.NEUTRAL: "Trung tính",
    Register.CASUAL: "Suồng sã",
}

LENGTH_CLASS_LABELS: Mapping[LengthClass, str] = {
    LengthClass.SHORT: "Ngắn",
    LengthClass.MEDIUM: "Vừa",
    LengthClass.LONG: "Dài",
}

CHECK_STATUS_LABELS: Mapping[str, str] = {
    "ok": "Đạt",
    "warn": "Cảnh báo",
    "fail": "Chặn",
}

# Nhãn trục variation (phase 5, config/variation.yaml) — key là tên axis
# dạng chuỗi (khớp VariationConfig.axes), không phải enum, vì hai trục
# number_prefix/admin_prefix là boolean {present, absent}, không có enum
# riêng trong schema.py.
VARIATION_AXIS_LABELS: Mapping[str, str] = {
    "name_variant": "Biến thể tên",
    "legacy_marker": 'Hậu tố "cũ"',
    "number_style": "Cách đọc số",
    "number_prefix": 'Tiền tố "số"',
    "admin_prefix": "Tiền tố hành chính",
    "hierarchy_depth": "Độ sâu địa chỉ",
}

VARIATION_VALUE_LABELS: Mapping[str, str] = {
    NameVariant.CURRENT.value: "Tên hiện hành",
    NameVariant.LEGACY.value: "Tên cũ",
    NameVariant.SPOKEN_ALIAS.value: "Cách gọi dân gian",
    NumberStyle.CARDINAL.value: "Đọc số nguyên khối",
    NumberStyle.DIGIT_BY_DIGIT.value: "Đọc từng chữ số",
    NumberStyle.MIXED.value: "Đọc hỗn hợp",
    NumberStyle.NONE.value: "Không có số",
    HierarchyDepth.POI_ONLY.value: "Chỉ nêu entity",
    HierarchyDepth.POI_DISTRICT.value: "Kèm quận/huyện",
    HierarchyDepth.FULL_ADDRESS.value: "Địa chỉ đầy đủ",
    "present": "Có",
    "absent": "Không",
}

# (thuật ngữ, giải thích một câu) — render thành panel gập ở cuối dashboard.
GLOSSARY: tuple[tuple[str, str], ...] = (
    (
        "Entity",
        "Một địa danh cụ thể trong master data — street hoặc POI — có thể "
        "được nhắc tới trong một câu navigation.",
    ),
    (
        "Street vs POI",
        "Street là tên đường (không có category/subcategory riêng ngoài "
        "'street'/'highway'/'alley'). POI (Point of Interest) là địa điểm cụ "
        "thể — trường học, bệnh viện, hồ, sân bay... — có category/"
        "subcategory theo taxonomy.",
    ),
    (
        "Alias — legacy",
        "Tên tỉnh trong số 63 tỉnh/thành cũ trước sáp nhập, ví dụ 'Hà Tây' "
        "trỏ về Hà Nội hiện hành. Vẫn là câu nói hợp lệ trong đời thật.",
    ),
    (
        "Alias — spoken",
        "Cách gọi dân gian không phải tên hành chính, ví dụ 'Sài Gòn' cho "
        "TP.HCM, 'thủ đô' cho Hà Nội.",
    ),
    (
        "Alias — abbrev_expanded",
        "Dạng viết tắt đã mở rộng thành spoken form, ví dụ 'TP.HCM' -> "
        "'thành phố Hồ Chí Minh'.",
    ),
    (
        "popularity_tier",
        "Độ nổi tiếng ước lượng (1 = nổi bật nhất). Hiện mọi POI từ OSM đều "
        "mang tier mặc định 2 — con số này CHƯA mang thông tin thật cho tới "
        "khi có tín hiệu độ nổi tiếng riêng.",
    ),
    (
        "admin_status",
        "Trạng thái hành chính của tỉnh: 'unchanged' (giữ nguyên) hay "
        "'merged' (từng sáp nhập từ tỉnh khác).",
    ),
    (
        "Cấp huyện (district) sau 1/7/2025",
        "Về mặt hành chính chính thức, Việt Nam đã bỏ cấp huyện — chỉ còn "
        "tỉnh -> xã/phường. district.csv vẫn được giữ vì 'quận Hoàn Kiếm' là "
        "cách nói tự nhiên thật trong đời sống, đánh dấu status='abolished'.",
    ),
    (
        "Thin slice",
        "Trạng thái hiện tại của master data: đủ 34 tỉnh + 63 alias hành "
        "chính, nhưng street/POI thật mới có ở 3 tỉnh mẫu (Hà Nội, TP.HCM, "
        "Đà Nẵng) để chạy thử toàn bộ pipeline trước khi mở rộng toàn quốc.",
    ),
    (
        "Coverage ở đây khác coverage phase 12",
        "Trang này chỉ đo 'tỉnh/alias có tồn tại trong bảng và có entity "
        "thuộc về nó' — điều kiện CẦN. Coverage 34/34 + 63/63 chính thức "
        "(bắt buộc PASS trước khi xuất final_100k.csv) đo địa danh có thật "
        "sự xuất hiện trong câu văn đã sinh hay không.",
    ),
    (
        "Trục variation",
        "Một chiều biến đổi độc lập của câu (config/variation.yaml, phase "
        "5) — ví dụ dùng tên tỉnh hiện hành hay tên cũ, đọc số nguyên khối "
        "hay từng chữ số. Kết hợp tự do với nhau và với template.",
    ),
    (
        "Không gian variation",
        "Số tổ hợp khả thi ước lượng được từ trọng số các trục × số "
        "template × hệ số opener/closer. Nhỏ hơn số candidate cần sinh "
        "(target_size × oversample_factor) nghĩa là trùng lặp chắc chắn "
        "xảy ra trước khi dedup (phase 11) kịp chạy.",
    ),
    (
        "Sàn legacy theo tỉnh",
        "min_legacy_share_per_province trong config/variation.yaml — tỷ lệ "
        "tối thiểu câu dùng tên tỉnh cũ cho mỗi tỉnh, để đạt coverage 63/63 "
        "legacy alias bắt buộc ở phase 12.",
    ),
)
