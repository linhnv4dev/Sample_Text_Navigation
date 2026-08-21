# Nguồn dữ liệu — `data/raw/admin/`

Mỗi file dưới đây phải có mục tương ứng ghi URL nguồn, ngày fetch/curate, và
sha256 tại thời điểm commit. `data/raw/` là bất khả xâm phạm (hard rule #7
áp dụng cho `data/master/`, còn `data/raw/` là nơi duy nhất truy được "dữ
liệu này từ đâu ra" — sửa dữ liệu luôn đi qua đây).

## `provinces.yaml`

- **Nguồn**: curate tay từ tin tức công khai về đợt sáp nhập tỉnh/thành có
  hiệu lực 1/7/2025 (Nghị quyết 202/2025/QH15, giảm 63 → 34 đơn vị cấp tỉnh).
- **⚠️ CẢNH BÁO ĐỘ TIN CẬY**: danh sách tỉnh nào sáp nhập với tỉnh nào trong
  file này được tái dựng từ trí nhớ/báo chí, **chưa đối chiếu trực tiếp với
  văn bản pháp lý gốc**. Trước khi dùng cho môi trường sản xuất hoặc công bố,
  cần một người có quyền truy cập Nghị quyết 202/2025/QH15 (hoặc Cổng thông
  tin Chính phủ) rà soát lại từng dòng `aliases.legacy` so với danh sách
  chính thức.
- **Quy ước "63 tỉnh cũ"**: invariant 63/63 trong CLAUDE.md/docs tham chiếu
  tới 63 tỉnh/thành giai đoạn 2008–2025 (sau đợt sáp nhập Hà Tây vào Hà Nội
  năm 2008, trước đợt sáp nhập 2025). Vì vậy "Hà Tây" được xếp vào
  `aliases.spoken` của Hà Nội (vẫn là cách nói hợp lệ) chứ **không** tính vào
  tập 63 — tính cả Hà Tây sẽ ra 64, sai invariant.
- Ngày curate: 2026-08-15.
- sha256: xem `git log -p` / chạy `sha256sum data/raw/admin/provinces.yaml`
  tại thời điểm commit — không ghi cứng ở đây vì file còn có thể sửa trước
  khi review xong.

## `districts.csv`, `wards.csv`

- **Nguồn**: thin slice thủ công cho 3 tỉnh mẫu (Hà Nội `01`, Hồ Chí Minh
  `02`, Đà Nẵng `04`) — tên quận/phường phổ biến, curate tay để chạy thử
  end-to-end pipeline phase 2. **Chưa phải danh sách đầy đủ toàn quốc.**
- `status=abolished` trên mọi dòng: cấp quận/huyện bị bãi bỏ về mặt hành
  chính từ 1/7/2025 (mô hình 2 cấp tỉnh → xã/phường), nhưng vẫn giữ lại làm
  lớp spoken/legacy vì người dùng vẫn nói "quận Hoàn Kiếm", "quận một" khi
  chỉ đường — xem docs/data-model.md.
- **`districts.csv#legacy_alias`** (thêm 2026-08-17, optional): sub-region
  hint — xem docs/data-model.md#sub-region-hint. Điền cho 5 district của
  `02` (toàn bộ là Hồ Chí Minh cũ) và 4 district của `04` (Hải Châu/Sơn
  Trà/Thanh Khê = Đà Nẵng cũ, Tam Kỳ = Quảng Nam cũ). Rỗng cho tỉnh
  `unchanged` (không mơ hồ, không cần hint).
- Ngày curate: 2026-08-15 (bảng gốc), 2026-08-17 (cột `legacy_alias`).

## `landmarks.yaml`

- **Nguồn**: curate tay — kiến thức phổ thông về địa danh cấp quốc gia/tỉnh
  của 34 tỉnh/thành hiện hành (Hồ Hoàn Kiếm, Dinh Độc Lập, Vịnh Hạ Long...),
  soạn offline (hard rule #5: LLM chỉ dùng ngoài runtime để soạn draft, có
  human review trước khi commit — cùng quy trình đã dùng cho template bank).
- **Mục đích**: bù cho lỗ hổng "0 POI landmark nổi tiếng ở 31/34 tỉnh" — OSM
  snapshot hiện chỉ phủ 3 tỉnh (`01`/`02`/`04`) và cố tình loại tag
  `tourism=attraction` (xem `config/osm_tags.yaml`), nên các địa danh như
  Bà Nà Hills, Ngũ Hành Sơn, Vịnh Hạ Long, Chùa Một Cột... không có mặt.
  `build_master.build_pois()` merge nguồn này vào `pois.csv` với
  `source=manual`, `popularity_tier` lấy từ `fame_tier` — khác POI từ OSM
  (hardcode `popularity_tier=2`, không phân biệt Hồ Hoàn Kiếm với một cái ao
  vô danh).
- **⚠️ CẢNH BÁO ĐỘ TIN CẬY**: đây là kiến thức phổ thông, **CHƯA đối chiếu
  toạ độ GPS hay văn bản xếp hạng di tích chính thức** (Bộ VHTTDL, UNESCO...).
  277 địa danh trên 34 tỉnh (~8-10/tỉnh, sau đợt mở rộng 2026-08-17 — vòng
  đầu chỉ ~5/tỉnh, ưu tiên độ chính xác hơn số lượng). Trước khi dùng cho
  môi trường sản xuất, cần một người rà soát lại từng tên + gán đúng
  `subcategory`/`fame_tier`.
- Ranh giới sáp nhập: mỗi `province_id` gộp landmark của MỌI tỉnh cũ đã sáp
  nhập vào nó (vd `04` Đà Nẵng gồm cả địa danh Quảng Nam cũ như Hội An, Mỹ
  Sơn — xem bảng sáp nhập ở `provinces.yaml`), không tạo province_id riêng
  cho tỉnh đã biến mất.
- **`legacy_alias`** (thêm 2026-08-17, optional trên từng landmark): sub-region
  hint cho tỉnh gộp — ghi tên legacy alias ĐÚNG mà landmark đó thuộc về (vd
  "Núi Bà Đen" ở `30` Tây Ninh mang `legacy_alias: "Tây Ninh"`, không phải
  "Long An" dù cùng `province_id`). Xem docs/data-model.md#sub-region-hint
  cho cơ chế đầy đủ. Đã gán cho phần lớn landmark ở 23/34 tỉnh gộp dựa trên
  kiến thức phổ thông về vị trí địa lý thật — CHƯA đối chiếu toạ độ GPS,
  cùng mức cảnh báo độ tin cậy như bản thân file này.
- Ngày curate: 2026-08-17 (170 landmark đầu), 2026-08-17 (mở rộng lên 277 +
  gán `legacy_alias`).

## `wards_osm.csv`

- **Nguồn**: trích tự động từ chính file `.pbf` Geofabrik (xem mục "Snapshot
  OSM" dưới) bằng `scripts/extract_wards.py` — relation
  `boundary=administrative` + `admin_level=6`, tức cấp xã/phường **hiện
  hành** theo quy ước OSM Việt Nam sau sáp nhập 1/7/2025.
- **Mục đích**: `wards.csv` curate tay chỉ có 18 phường của 3 tỉnh mẫu, nên
  31 tỉnh còn lại chỉ có ĐÚNG 1 entity `admin_unit` (chính tên tỉnh) —
  không đủ pool để `navtext plan` phân bổ quota mà không vượt
  `caps.entity.max_share`. File này lấp đúng lỗ đó: **3.318 phường/xã, đủ
  34/34 tỉnh** (Việt Nam hiện có ~3.321 đơn vị cấp xã).
- **Gán tỉnh bằng point-in-polygon**, không phải bbox: script dựng đa giác
  ranh giới tỉnh từ relation `admin_level=4` rồi ray-casting. Bbox chữ nhật
  từng gán sai nghiêm trọng — bbox Khánh Hòa bao cả Trường Sa nên tâm bbox
  rơi giữa biển, gần như toàn bộ phường Nha Trang bị đẩy sang tỉnh lân cận
  (chỉ 1/~130 phường ở lại đúng). Với đa giác thật: 3318/3320 gán được, 2 ca
  nằm ngoài mọi đa giác, 1 ca mơ hồ.
- **Tên tách tiền tố**: OSM nhúng tiền tố trong `name` ("Phường Sơn Trà"),
  schema tách `name`/`type` ("Sơn Trà" + "phường") — xem
  `_NAME_PREFIX_TO_TYPE`. Tên không mang tiền tố hành chính VN nào (76 ca:
  vùng biên Trung Quốc/Campuchia trong extract Geofabrik) bị loại.
- **`district_id` để rỗng**: mô hình 2 cấp hiện hành, xã thuộc thẳng tỉnh —
  ward nối lên tỉnh qua `province_id`.
- **`legacy_alias`** (thêm 2026-08-19, optional, cột mới trong `wards_osm.csv`
  — cùng cơ chế validate với `districts.csv`/`landmarks.yaml`, xem
  docs/data-model.md#sub-region-hint): script dựng THÊM một lớp đa giác cho
  **tỉnh CŨ** (admin_level=4, tên khớp một legacy alias — CHỈ chấp nhận
  `boundary=administrative` HOẶC `historic`, vì cộng đồng OSM thường đổi tag
  ranh giới tỉnh cũ sang "historic" sau khi tỉnh đó sáp nhập, trong khi ranh
  giới tỉnh hiện hành vẫn "administrative"), rồi gán ward theo point-in-
  polygon + suy luận loại trừ (tỉnh gộp thiếu ĐÚNG 1 alias có đa giác thì
  suy ra được alias còn lại).
  **⚠️ Yield thấp hơn nhiều so với ước tính ban đầu**: chỉ **3/52** legacy
  alias không-identity còn tồn tại dưới dạng relation `admin_level=4` trong
  bản `.pbf` hiện tại (Yên Bái, Hòa Bình, Quảng Bình) — phần lớn ranh giới
  tỉnh cũ đã bị XOÁ hẳn khỏi OSM khi sáp nhập (không chỉ đổi tag), không
  phải "phần lớn còn nhưng gắn historic" như giả định ban đầu. Kết quả:
  **222/3318 ward (~6,7%)** có `legacy_alias` — chỉ ở 5 tỉnh gộp (16 Tuyên
  Quang, 19 Phú Thọ, 23 Quảng Trị và 2 tỉnh liên đới). 18/23 tỉnh gộp còn
  lại KHÔNG có sub-region hint cho ward OSM — cùng giới hạn tồn dư với
  street/POI OSM, ghi nhận là rủi ro tồn dư đã biết, không phải lỗi.
  Điều tra thêm cho thấy con số "18/23 giải quyết được" trong plan ban đầu
  bị thổi phồng bởi lỗi tính: quan hệ `admin_level=4` mang tên TRÙNG alias
  identity (== tên tỉnh hiện hành, vd "Đà Nẵng") chính là ranh giới ĐÃ GỘP
  toàn bộ lãnh thổ mới, không phải một ranh giới con nhỏ hơn — dùng nó làm
  "đa giác legacy" khiến MỌI điểm trong tỉnh khớp nhầm, đã sửa bằng cách
  loại alias identity khỏi bước tìm relation (xem
  `extract_wards.py#load_legacy_names_by_province`).
- **⚠️ ĐỘ TIN CẬY**: ranh giới OSM do cộng đồng cập nhật, mức độ hoàn thiện
  sau đợt sáp nhập 2025 không đồng đều giữa các tỉnh. Đây KHÔNG phải danh
  sách hành chính chính thức — cần đối chiếu với văn bản nhà nước trước khi
  dùng cho môi trường sản xuất.
- Ngày trích: 2026-08-18 (bảng gốc), 2026-08-19 (cột `legacy_alias`). Chạy
  lại: `python3 scripts/extract_wards.py data/raw/osm_pbf/vietnam-latest.osm.pbf`.

## Snapshot OSM — `data/raw/osm/*.json`

- **Nguồn**: Overpass API (`https://overpass-api.de/api/interpreter`), fetch
  bằng `scripts/fetch_osm.py` (script chạy tay, ngoài pipeline `navtext` —
  `build-master` không bao giờ gọi network).
- **Bbox**: 3 tỉnh mẫu ban đầu (`01`/`02`/`04`) dùng bbox lõi đô thị curate
  tay (`SAMPLE_PROVINCE_BBOX` trong script). 31 tỉnh còn lại (mở rộng
  2026-08-17) dùng bbox lấy TỰ ĐỘNG qua Nominatim
  (`https://nominatim.openstreetmap.org/search`, `featuretype=state`) — query
  `"Thành phố {tên hiện hành}, Vietnam"` hoặc `"Tỉnh {tên hiện hành}, Vietnam"`
  theo `is_municipality`, fallback sang legacy alias đầu tiên nếu 0 kết quả.
- **⚠️ Bbox hình chữ nhật chỉ là XẤP XỈ ranh giới hành chính thật** (đa giác
  bất kỳ) — tỉnh liền kề chắc chắn có bbox overlap ở vùng biên, khiến cùng
  một OSM element xuất hiện trong nhiều snapshot tỉnh. `build_master.py`
  (`dedupe_cross_province_osm_elements`) xử lý bằng cách giữ mỗi element ở
  đúng MỘT tỉnh — tỉnh có bbox center gần toạ độ element nhất — trước khi
  build streets/pois. Đây là proxy hợp lý, không phải point-in-polygon
  chính xác; sai số còn lại (nhỏ, ở vùng giáp ranh) là rủi ro tồn dư đã biết.
- Ngày fetch batch: 2026-08-17.

## Mở rộng toàn quốc (đã làm 2026-08-17)

`districts.csv`/`wards.csv` cho 31 tỉnh còn lại VẪN CHƯA có (chỉ 3 tỉnh mẫu
— xem mục trên) — đây là phần còn thiếu duy nhất của "thin slice" ban đầu.
`streets`/`pois` qua OSM đã mở rộng đủ 34/34 tỉnh (xem mục "Snapshot OSM"
trên). `landmarks.yaml` giải quyết nhu cầu "địa danh nổi tiếng" mà không
cần network, độc lập với vòng mở rộng OSM.
