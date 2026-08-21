# Data Model

Taxonomy, schema địa danh, và mô hình 34 (hiện hành) ↔ 63 (cũ) ↔ cách gọi phổ biến.

## Taxonomy

`category → subcategory`, phủ toàn bộ danh sách navigation entity cần cover:

| category | subcategory |
|---|---|
| `admin_unit` | province, district, ward |
| `street` | street, quốc lộ/tỉnh lộ, ngõ/hẻm |
| `address` | house_number + street (+ ward/district tuỳ độ sâu) |
| `landmark` | hồ, cầu, tượng đài, di tích, chợ |
| `transport` | airport, railway_station, bus_terminal, bến phà, metro |
| `public_facility` | school, university, hospital, cơ quan hành chính |
| `commercial` | TTTM, siêu thị, khách sạn, nhà hàng, cây xăng, ngân hàng |
| `poi_other` | công viên, sân vận động, khu công nghiệp, chùa/nhà thờ |

`category`/`subcategory` là 2 cột riêng trong CSV cuối — không gộp thành một chuỗi tự do, để `qc/balance.py` group được chính xác.

Toàn bộ taxonomy này được code hoá thành enum + `SUBCATEGORIES` trong [src/navtext/schema.py](../src/navtext/schema.py) — đó là nguồn sự thật khi viết code, doc này là nguồn sự thật khi đọc hiểu.

## Geographic hierarchy

```
Province / City
      ↓
District
      ↓
Ward
      ↓
Street
      ↓
Address / POI
```

Mỗi cấp dưới có FK trỏ lên cấp trên. `pois.csv` có thể trỏ trực tiếp tới `ward_id`/`district_id`/`province_id` (không phải POI nào cũng nằm trên một street cụ thể — ví dụ một hồ, một khu công nghiệp).

## Master dataset schema

```
provinces.csv       province_id, name_current, admin_status(unchanged|merged),
                     region, is_municipality, popularity_tier

province_alias.csv  province_id, alias, alias_type(legacy|spoken|abbrev_expanded), weight

districts.csv       district_id, province_id, name, type(quận|huyện|thị xã|tp thuộc tỉnh),
                     status(current|abolished), legacy_alias

wards.csv            ward_id, district_id, name, type(phường|xã|thị trấn)

streets.csv          street_id, province_id, district_id, name, street_type

pois.csv             poi_id, name, category, subcategory,
                      ward_id, district_id, province_id,
                      popularity_tier, source(osm|manual), osm_id,
                      legacy_alias_hint
```

**`districts.status`**: từ 1/7/2025, Việt Nam bỏ cấp huyện, chuyển mô hình
hành chính 2 cấp (tỉnh → xã/phường). `districts.csv` vẫn giữ lại toàn bộ
quận/huyện cũ với `status=abolished`, vì "quận Hoàn Kiếm", "quận một" vẫn
là cách người dùng nói thật khi chỉ đường (spoken form hợp lệ), và
`hierarchy_depth=poi_district` ở [docs/generation.md](generation.md#variation-axes)
cần lớp dữ liệu này để verbalize. `status=current` dành cho trường hợp
tương lai nếu cấp huyện được khôi phục ở một số nơi.

**`streets.district_id` có thể rỗng**: dữ liệu street lấy từ OSM chỉ có
toạ độ, không có ranh giới hành chính quận/phường để point-in-polygon —
xem [scripts/fetch_osm.py](../scripts/fetch_osm.py) và
`build_streets()` trong [build_master.py](../src/navtext/build_master.py).
Vì vậy `streets.csv` luôn có `province_id` (biết chắc vì fetch theo tỉnh)
nhưng `district_id` để trống cho tới khi có bước point-in-polygon với ranh
giới quận/phường thật — cùng kiểu linh hoạt FK mà `pois.csv` đã áp dụng.

`popularity_tier` (1–3) dùng ở `sampling.py` để cho địa danh nổi tiếng (Hồ Hoàn Kiếm, Nội Bài) trọng số cao hơn nhưng vẫn dưới trần — xem [docs/sampling.md](sampling.md).

## Sub-region hint

**Vấn đề**: 23/34 tỉnh hiện hành là `admin_status: merged`, gộp 2-3 tỉnh cũ vào một `province_id`. `resolve_province()` (name_variant=legacy) chọn ngẫu nhiên trên TOÀN BỘ legacy alias của tỉnh gộp — nếu không có thêm tín hiệu, một entity chỉ tồn tại thật ở MỘT tỉnh cũ cụ thể (vd Núi Bà Đen chỉ ở Tây Ninh cũ, chưa từng thuộc Long An) vẫn có thể bị ghép ngẫu nhiên với tên tỉnh cũ khác trong cùng nhóm gộp ("Núi Bà Đen ở tỉnh Long An" — sai địa lý).

**Cơ chế**: `LocationRef.legacy_alias_hint` (optional) mang tên legacy alias ĐÚNG mà entity đó thuộc về. Khi có giá trị, `variation.py#_hierarchy_tail()` (nhánh FULL_ADDRESS) truyền nó vào `resolve_province(..., allowed_aliases={hint})` — hàm này lọc candidate xuống chỉ alias khớp hint trước khi chọn ngẫu nhiên (`aliases.py`). Hint không khớp alias thật nào (data bug phòng hờ, đã được `build_master.py` validate chặn từ trước) thì fallback về pool đầy đủ, không crash.

**Nguồn hint** — chỉ áp dụng cho dữ liệu curate tay, KHÔNG áp dụng cho OSM (`addr:province` tag quá bẩn/thưa để tin cậy làm sub-region signal, xem ghi chú trong [SOURCES.md](../data/raw/admin/SOURCES.md)):

- `data/raw/admin/landmarks.yaml`: field optional `legacy_alias` trên từng landmark ở tỉnh gộp — validate khớp đúng 1 trong `provinces.yaml#aliases.legacy.name` của đúng tỉnh đó (`build_master.py#_build_manual_landmarks`), ghi ra `pois.csv#legacy_alias_hint`.
- `data/raw/admin/districts.csv`: cột optional `legacy_alias` — cùng cơ chế validate (`build_master.py#build_districts`). `wards.csv` KHÔNG có cột riêng — ward kế thừa hint từ `district_id` của nó lúc `sampling.py#entity_pool()` build `LocationRef` (nhánh `ADMIN_UNIT`).

**Rủi ro tồn dư đã biết**: street/POI từ OSM không có sub-region hint — một street/POI OSM ở tỉnh gộp vẫn có thể bị ghép sai legacy alias khi `name_variant=legacy` được rút, dù xác suất thấp vì bbox fetch hiện tại theo tỉnh hiện hành. Giải quyết triệt để cần vòng fetch riêng theo ranh giới tỉnh cũ — chưa làm.

## Mô hình 34 ↔ 63 ↔ cách gọi phổ biến

**Nguyên tắc cốt lõi: một bảng `provinces` canonical (34 đơn vị hiện hành), mọi tên khác là alias trỏ vào đó.**

```
province_alias.alias_type = legacy          → tên trong 63 tỉnh/thành cũ, ví dụ "Hà Tây" → province_id của Hà Nội
province_alias.alias_type = spoken           → cách gọi dân gian, ví dụ "Sài Gòn" → TP.HCM, "thủ đô" → Hà Nội
province_alias.alias_type = abbrev_expanded  → dạng viết tắt đã mở rộng, ví dụ "TP.HCM" → "thành phố Hồ Chí Minh"
```

Mỗi legacy name trỏ về **đúng một** `province_id` hiện hành (kể cả trường hợp sáp nhập — nhiều legacy name có thể trỏ về cùng một province hiện tại). Không có bảng `provinces_legacy` riêng — điều đó sẽ biến 34/63 thành hai dataset, vi phạm hard rule #2.

Legacy/spoken name **là một giá trị hợp lệ trong lời nói thật** ("cho tôi đường về Hà Tây" vẫn là câu người dùng có thể nói), nên nó được chọn bởi trục variation `name_variant` ở generation time — xem [docs/generation.md](generation.md#variation-axes) — chứ không phải dữ liệu chết chỉ để tra cứu.

**Coverage bắt buộc**: dataset cuối phải chứng minh được toàn bộ 34/34 province hiện hành và 63/63 legacy alias đều xuất hiện ít nhất một lần trong `text` — đây là hai dòng PASS/FAIL bắt buộc trong `reports/dataset_stats.md`, xem [docs/output.md](output.md#statistics-report).

## Nguồn dữ liệu & build

- `data/raw/admin/` — danh sách hành chính chính thức (34 tỉnh hiện hành, 63 tỉnh cũ, quận/huyện, phường/xã), nhập tay hoặc từ nguồn nhà nước công bố. Đây là nguồn duy nhất của sự thật cho `admin_unit`. Xem [data/raw/admin/SOURCES.md](../data/raw/admin/SOURCES.md) cho nguồn cụ thể + cảnh báo độ tin cậy từng file.
- `data/raw/osm/` — snapshot JSON thô từ Overpass API, fetch bằng [scripts/fetch_osm.py](../scripts/fetch_osm.py) (script chạy tay, ngoài pipeline `navtext`). `build-master` **không bao giờ gọi network** — chỉ đọc snapshot đã commit. Mapping OSM tag → category/subcategory nằm ở `config/osm_tags.yaml`, không hardcode trong code (hard rule #8).
- `build_master.py` (chạy qua `navtext build-master`): đọc `data/raw/`, normalize tên (bỏ khoảng trắng thừa, chuẩn hoá Unicode NFC), resolve alias, dedupe street segment cùng tên trong cùng tỉnh thành 1 entity, validate FK hierarchy + bất biến 34/63 (đọc từ `config/run.yaml#admin_invariants`, không hardcode), ghi ra `data/master/*.csv` deterministic (sort theo id, atomic write — fail thì không ghi gì).
- `data/master/` là build artifact thuần tuý — xem hard rule #7 trong [CLAUDE.md](../CLAUDE.md#hard-rules). Sửa dữ liệu luôn đi qua `data/raw/` rồi build lại.
- **Phạm vi hiện tại (toàn quốc)**: `streets`/`pois` phủ đủ **34/34 tỉnh** (ingest offline từ `.pbf` Geofabrik qua `scripts/fetch_osm.py --from-pbf`), `wards` phủ đủ 34/34 với ~3.3k phường/xã hiện hành (`scripts/extract_wards.py`, xem dưới). `provinces.csv`/`province_alias.csv` đã phủ đủ 34/63 ngay từ đầu (coverage bắt buộc, xem trên). Riêng `districts.csv` vẫn là thin slice 14 quận/huyện của 3 tỉnh mẫu — đây là lớp **đã bị bãi bỏ** về mặt hành chính từ 1/7/2025, chỉ giữ làm lớp spoken/legacy, nên không mở rộng thêm.
- **`wards` từ ranh giới hành chính OSM**: `scripts/extract_wards.py` duyệt `.pbf` lấy relation `boundary=administrative` + `admin_level=6` (quy ước OSM Việt Nam cho cấp xã/phường hiện hành sau sáp nhập 2025), rồi gán mỗi phường vào tỉnh bằng **point-in-polygon trên ranh giới tỉnh thật** (`admin_level=4`) — không dùng bbox chữ nhật. Lý do: bbox tỉnh Khánh Hòa bao cả quần đảo Trường Sa nên tâm bbox rơi ra giữa biển, khiến gần như toàn bộ phường đất liền Nha Trang bị gán nhầm sang tỉnh lân cận (1/~130 phường ở lại đúng tỉnh). Đa giác thật không có lớp lỗi đó: 3318/3320 phường gán đúng, 1 ca mơ hồ.
- **`wards.province_id` vs `wards.district_id`**: ward nối lên tỉnh theo một trong hai đường — qua `district_id` (nguồn curate tay, quận/huyện **cũ**) hoặc thẳng qua `province_id` (nguồn OSM, mô hình 2 cấp hiện hành). `build_master.validate()` bắt buộc có ít nhất một trong hai. Chỉ ward đi qua district mới mang được [sub-region hint](#sub-region-hint); ward từ OSM không biết mình thuộc tỉnh **cũ** nào — cùng giới hạn tồn dư đã ghi nhận với street/POI từ OSM.
