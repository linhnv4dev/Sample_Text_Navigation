"""Resolve tên địa danh theo name_variant (current/legacy/spoken_alias) và
map legacy province name -> province_id hiện hành. Implement ở phase 2/6.

Xem docs/data-model.md#mô-hình-34--63--cách-gọi-phổ-biến.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from navtext.loaders import MasterData
from navtext.schema import AliasType, NameVariant
from navtext.variation import pick_weighted


@dataclass(frozen=True, slots=True)
class ResolvedProvince:
    """Tên tỉnh đã resolve + tiền tố hành chính ĐÚNG của chính tên đó.

    admin_prefix là "tỉnh" | "thành phố" | "" (cấm gắn tiền tố — tên đã tự
    mang nghĩa hành chính, vd "thủ đô", "thành phố Hồ Chí Minh"). Đây là
    NGUỒN SỰ THẬT DUY NHẤT cho cặp (tên, tiền tố) — không được tính lại
    prefix_word từ province.is_municipality của tỉnh HIỆN HÀNH ở nơi khác,
    vì một legacy alias có thể mang tiền tố khác tỉnh hiện hành nó thuộc về
    (vd "Đà Nẵng" hiện là thành phố, nhưng legacy alias "Quảng Nam" của nó
    vốn là một tỉnh).
    """

    text: str
    admin_prefix: str
    variant_used: NameVariant = NameVariant.CURRENT
    """NameVariant THỰC SỰ đã dùng để tạo `text` — có thể khác `name_variant`
    truyền vào resolve_province() khi hàm phải fallback (vd LEGACY được rút
    nhưng tỉnh không có legacy alias nào KHÁC tên hiện hành, hoặc
    SPOKEN_ALIAS được rút nhưng tỉnh không có spoken alias nào). Caller
    (generator.py) phải ghi Sample.name_variant theo field này, không theo
    draw.name_variant, để cột audit không nói dối — xem
    docs/data-model.md#mô-hình-34--63--cách-gọi-phổ-biến."""


def resolve_province(
    entity: str,
    name_variant: NameVariant,
    master: MasterData,
    rng: random.Random,
    *,
    allowed_aliases: frozenset[str] | None = None,
) -> ResolvedProvince:
    """Trả về (tên, admin_prefix) đã resolve theo name_variant. rng dùng khi
    một variant có nhiều alias hợp lệ và cần chọn ngẫu nhiên trong số đó
    (hard rule #4: rng luôn nhận tường minh, không dùng random module-level).

    `entity` chỉ có ý nghĩa với name_variant khác CURRENT khi nó là tên
    tỉnh (hoặc một alias đã biết của tỉnh) — alias chỉ tồn tại ở cấp tỉnh
    trong province_alias.csv, street/POI không có alias (docs/generation.md
    #config-variation-yaml, đoạn "Applicability là logic của code"). Nếu
    `entity` không khớp tỉnh nào, trả nguyên vẹn với prefix suy từ chính
    tỉnh đó (không có tỉnh để suy thì coi như "tỉnh") — caller (variation.py,
    phase 6) chịu trách nhiệm không gọi hàm này với legacy/spoken_alias trên
    entity không phải province (đã bị draw_axes ép về CURRENT).

    `allowed_aliases`: sub-region hint — khi truyền vào (khác None), lọc
    candidates xuống chỉ những alias nằm trong tập này trước khi chọn ngẫu
    nhiên. Dùng khi entity biết chắc mình thuộc tỉnh cũ nào trong số các
    legacy alias của tỉnh gộp (vd LocationRef.legacy_alias_hint từ
    landmarks.yaml/districts.csv), tránh ghép sai kiểu "Núi Bà Đen ở tỉnh
    Long An". Lọc ra rỗng (hint không khớp alias thật nào — data bug phòng
    hờ) fallback về candidates đầy đủ thay vì raise, giữ generator không bao
    giờ crash vì lỗi data (fail loud ở build, không ở generate).
    """
    province_id = master.province_id_by_alias.get(entity.casefold())
    if province_id is None:
        return ResolvedProvince(text=entity, admin_prefix="tỉnh")

    province = master.provinces.get(province_id)
    if province is None:
        return ResolvedProvince(text=entity, admin_prefix="tỉnh")

    current_prefix = "thành phố" if province.is_municipality else "tỉnh"
    current_result = ResolvedProvince(
        text=province.name_current, admin_prefix=current_prefix, variant_used=NameVariant.CURRENT
    )
    if name_variant == NameVariant.CURRENT:
        return current_result

    alias_type = AliasType.LEGACY if name_variant == NameVariant.LEGACY else AliasType.SPOKEN
    candidates = [
        a
        for a in master.aliases_by_province.get(province_id, ())
        if a.alias_type == alias_type.value
    ]
    if allowed_aliases is not None:
        restricted = [a for a in candidates if a.alias in allowed_aliases]
        if restricted:
            candidates = restricted

    if name_variant == NameVariant.LEGACY and province.admin_status == "unchanged":
        # CHỈ áp lọc identity cho tỉnh KHÔNG sáp nhập (11/34, vd Hà Nội/Nghệ
        # An/Sơn La): mọi legacy alias của nhóm này trùng nguyên văn tên
        # hiện hành ("legacy" của chúng chỉ là chính chúng — không sáp nhập
        # thì không có tên cũ nào khác để nói). Rút một alias trùng tên hiện
        # hành tạo ra text y hệt bản CURRENT trong khi cột audit lại ghi
        # "legacy" — vừa vô nghĩa, vừa làm cột audit nói dối. Ưu tiên alias
        # THỰC SỰ khác tên hiện hành (vd Huế/06 vẫn có "Thừa Thiên Huế");
        # giữ lại candidates gốc chỉ khi lọc ra rỗng (tỉnh chỉ có alias
        # identity, vd Hà Nội).
        #
        # KHÔNG áp cho tỉnh MERGED (23/34): với nhóm này, một alias trùng
        # chữ với tên hiện hành KHÔNG phải filler vô nghĩa — nó là một
        # trong các tỉnh CŨ THẬT SỰ trước sáp nhập, tình cờ trùng tên với
        # tỉnh mới (vd Đà Nẵng cũ + Quảng Nam -> Đà Nẵng mới; "Đà Nẵng" là
        # một legacy alias HOÀN TOÀN hợp lệ, không phải trùng lặp). 22/23
        # tỉnh merged rơi vào trường hợp này — lọc nó ra khiến LEGACY không
        # bao giờ trả về được predecessor TRÙNG TÊN, dù đó thường là
        # predecessor CHÍNH (bug thật: resolve_province("Đà Nẵng", LEGACY)
        # trước fix này LUÔN trả "Quảng Nam", KHÔNG BAO GIỜ "Đà Nẵng" — gốc
        # rễ của bug user báo cáo "Tượng Mẹ Nhu ở tỉnh Quảng Nam cũ").
        non_identity = [c for c in candidates if c.alias != province.name_current]
        if non_identity:
            candidates = non_identity
        else:
            candidates = []

    if not candidates:
        # Không phải tỉnh nào cũng có spoken alias (chỉ 3/34 trong master
        # hiện tại), và không phải tỉnh nào cũng có legacy alias khác tên
        # hiện hành (10/34 tỉnh không sáp nhập) — fallback về tên hiện hành
        # thay vì lỗi, để caller không phải tự dò trường hợp thiếu dữ liệu
        # này. variant_used=CURRENT phản ánh đúng NameVariant thật đã dùng.
        return current_result

    weights = {c.alias: c.weight for c in candidates}
    chosen = pick_weighted(weights, rng)
    prefix_by_alias = {c.alias: c.admin_prefix for c in candidates}
    return ResolvedProvince(
        text=chosen, admin_prefix=prefix_by_alias[chosen], variant_used=name_variant
    )


def has_unambiguous_legacy_alias(province_id: str, master: MasterData) -> bool:
    """True nếu tỉnh có TỐI ĐA 1 candidate legacy alias thật sự khả thi
    (cùng logic chọn candidates với resolve_province(), xem comment ở đó) —
    nghĩa là dùng name_variant=LEGACY cho một entity KHÔNG PHẢI province
    (đuôi hierarchy gắn vào POI/street/address) không có rủi ro gán sai địa
    lý.

    Mọi tỉnh MERGED (23/34) có ÍT NHẤT 2 legacy alias thật (không tỉnh merged
    nào chỉ có 1 — mỗi tỉnh sáp nhập từ >=2 tỉnh cũ, mỗi tỉnh cũ là một
    candidate riêng dù chữ có trùng tên hiện hành hay không, xem
    resolve_province()) — luôn ambiguous nếu không có hint xác định entity
    thuộc phần nào (fix bug user báo: "Tượng Mẹ Nhu ở tỉnh Quảng Nam cũ" —
    tượng thực tế ở Đà Nẵng cũ). Chỉ tỉnh UNCHANGED (11/34) mới an toàn
    unambiguous, vì với nhóm này legacy chỉ có tối đa 1 alias thật khác tên
    hiện hành (vd Huế -> "Thừa Thiên Huế"), hoặc 0 (vd Hà Nội, fallback
    CURRENT). Caller (variation._hierarchy_tail) chỉ cho phép LEGACY khi
    hàm này trả True, hoặc khi có hint riêng (entity.legacy_alias_hint).

    province_id không khớp tỉnh nào (data bug phòng hờ) -> True, không chặn
    oan — caller khác đã xử lý trường hợp province không resolve được.
    """
    province = master.provinces.get(province_id)
    if province is None:
        return True
    candidates = [
        a
        for a in master.aliases_by_province.get(province_id, ())
        if a.alias_type == AliasType.LEGACY.value
    ]
    if province.admin_status == "unchanged":
        candidates = [a for a in candidates if a.alias != province.name_current]
    return len(candidates) <= 1


def resolve_name(
    entity: str, name_variant: NameVariant, master: MasterData, rng: random.Random
) -> str:
    """Wrapper mỏng quanh resolve_province() cho caller chỉ cần chuỗi tên,
    không cần admin_prefix (vd nơi tên tỉnh xuất hiện không kèm tiền tố).
    """
    return resolve_province(entity, name_variant, master, rng).text


def legacy_to_current(legacy_name: str, master: MasterData) -> str:
    """Map một tên tỉnh cũ (trong số 63) về province_id hiện hành (trong 34).
    Raise ValueError nếu tên không khớp tỉnh/alias nào đã biết.
    """
    province_id = master.province_id_by_alias.get(legacy_name.casefold())
    if province_id is None:
        raise ValueError(
            f"legacy_to_current: không tìm thấy tỉnh hiện hành cho tên {legacy_name!r}"
        )
    return province_id
