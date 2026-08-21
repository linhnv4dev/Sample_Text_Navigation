# Sampling Strategy

Cơ chế phân bổ 100k (thực tế: 120–150k candidate) sao cho không tỉnh/category/template/entity nào chiếm tỷ trọng bất hợp lý, và mọi bước đều reproducible.

## Nguyên tắc: quota-driven, không random thuần

```
Plan Table = tích Descartes có kiểm soát của các trục:
  geography × category × sentence_type × variation_profile
→ mỗi "cell" có target_count được tính từ config/distribution.yaml
→ generator sinh ĐÚNG target_count cho từng cell
→ random chỉ xảy ra BÊN TRONG cell (chọn entity cụ thể, chọn template cụ thể)
```

`sampling.build_plan(config, master) -> list[Cell]` sinh ra plan table này **trước khi** generate bất kỳ câu nào. `validate_plan(plan, config, master, total)` kiểm tra tổng = `total` và mọi cell đều thoả trần/sàn trước khi cho generate chạy.

### Feasibility-aware: chỉ tạo cell có entity thật

`sampling.entity_pool(master, province_id, category) -> tuple[LocationRef, ...]` trả về entity khả thi cho một (province, category). `build_plan` chỉ tạo cell cho (province, category) có pool khác rỗng — không bao giờ tạo cell rồi hy vọng generator tự xoay sở khi pool trống.

- `admin_unit`: pool luôn khác rỗng (tên tỉnh từ `provinces.csv` luôn có), cộng thêm district/ward của tỉnh nếu `data/master/districts.csv`/`wards.csv` đã phủ tỉnh đó.
- `address`: dùng chung pool với `street` — số nhà được synthesize lúc generate (phase 6), không có entity address riêng trong master.
- 5 category POI còn lại: lọc `entities_by_province` theo category.

**Lọc charset (phase 6)**: mọi nhánh trên đều lọc qua `sampling._is_speakable()` trước khi trả về — loại entity có tên chứa chữ cái ngoài Latin cơ bản + dấu tiếng Việt (vd 38 POI tên tiếng Nhật/Hàn trong master hiện tại: `丸亀製麺`, `맛찬들 소금구이`). Lọc ở tầng pool (không phải ở QC phase 11) để feasibility của `build_plan` phản ánh đúng thực tế — entity không đọc được coi như không tồn tại ngay từ bước lập plan. Thương hiệu Latin không dấu (BIDV, WinMart, GO!) được giữ nguyên vì vẫn đọc được bình thường.

Ý nghĩa thực tế: master data hiện đã phủ **đủ 34/34 tỉnh** ở cả street/POI (ingest từ `.pbf` toàn quốc) lẫn `admin_unit` (3.3k phường/xã trích từ ranh giới hành chính OSM) — xem [docs/data-model.md](data-model.md#nguồn-dữ-liệu--build). `navtext plan --target 100000` **PASS**. Trước đó, khi chỉ 3/34 tỉnh có dữ liệu, plan được kỳ vọng FAIL và `category_weights.admin_unit` bị đặt cao bất thường để hấp thụ khối lượng của 31 tỉnh chỉ-có-tên-tỉnh; lý do đó nay không còn.

**Hai lớp trần cùng ràng buộc một block** — cần phân biệt khi đọc `PlanError`:

- `caps.province.max_share` (trần tỷ trọng tỉnh): bị vi phạm khi một category chỉ khả thi ở rất ít tỉnh, dồn toàn bộ khối lượng vào đúng những tỉnh ấy. Đây là dạng lỗi của giai đoạn thin slice.
- `caps.entity.max_share` (trần tỷ trọng MỘT entity): bị vi phạm khi block đòi nhiều sample hơn `len(pool) × max_share × target`. Dạng này không phụ thuộc số tỉnh mà phụ thuộc **độ giàu của pool trong từng tỉnh** — đã gặp thật: sau khi có đủ OSM 34 tỉnh, `admin_unit` vẫn FAIL vì 31 tỉnh chỉ có ĐÚNG 1 entity (chính tên tỉnh, do `wards.csv` curate tay chỉ phủ 3 tỉnh), trần 150 sample/entity trong khi quota đòi 233–696. Fix đúng là **làm giàu pool** (trích phường/xã cho cả 34 tỉnh), không phải hạ `category_weights` — hạ weight sẽ cắt `admin_unit` xuống sát sàn `caps.category.min_share` và làm mất gần hết câu dạng "đi quận X"/"tới phường Y".

Trong cả hai trường hợp `build_plan` raise `PlanError` liệt kê chính xác block nào bị ép và vì sao, cùng gợi ý: thêm entity vào master data, hạ `province_weights` của tỉnh bị ép, hoặc nới trần tương ứng — đây là quyết định sản phẩm, `build_plan` không tự ý chọn thay.

### `variation_profile` — trục thứ 4 của cell_key

`variation_profile` cố định 2 trục **register × length_class**, định nghĩa trong `config/distribution.yaml#variation_profiles` (mỗi entry có `name`, `register`, `length_class`, `weight`). Hai trục này quyết định field nào của template được chọn ([docs/generation.md](generation.md#template-bank)) nên cần quota rõ ràng; các trục variation còn lại (`number_style`, `name_variant`, `admin_prefix`, `hierarchy_depth`) không nằm trong cell_key — chúng được chọn bằng `rng` của cell ở phase 5/6 ([config/variation.yaml](../config/variation.yaml)).

### Thuật toán: IPF + largest-remainder

1. Tính marginal `province_share`/`category_share`/`sentence_type_share` bằng `clamped_shares()` — water-filling: trọng số thô chuẩn hoá rồi kẹp trong [sàn, trần], phần dư chia lại cho phần tử chưa bị kẹp, lặp tới hội tụ.
2. Chạy **iterative proportional fitting (IPF)** trên ma trận (province × category) giới hạn ở các ô khả thi, khớp đồng thời hai marginal. Số vòng lặp cố định (không random, không phụ thuộc dữ liệu) nên kết quả deterministic.
3. Nở 2 marginal còn lại (`sentence_type`, `variation_profile`) bằng tích trực tiếp — cả hai đều khả thi ở mọi block nên không cần IPF riêng.
4. Làm tròn toàn bộ cell shares về số nguyên bằng `largest_remainder()` — một lượt duy nhất trên toàn bộ cell, tie-break theo `cell_key` để deterministic, tổng luôn đúng bằng target.
5. Cell dưới `min_cell_size` bị gộp mass về các cell còn lại trong cùng phạm vi (một lượt `largest_remainder` phụ).

`scale_plan(plan, total)` dùng lại đúng `largest_remainder()` để scale tỷ trọng của một plan đã build sang một `total` khác — prototype 1k / candidate 130k / final 100k không lệch tỷ trọng so với plan canonical.

### Chọn entity cụ thể trong cell (phase 6)

`config/distribution.yaml#entity_weights.by_tier` — khác `province_weights.by_tier` (chọn tỉnh nào cho cell, ở tầng plan) — quyết định trọng số chọn ENTITY CỤ THỂ bên trong một cell đã có target_count. `generator.generate_cell()` dùng weighted sampling without replacement kiểu Efraimidis–Spirakis (`generator._weighted_shuffle()`): mỗi entity trong pool nhận key `rng.random() ** (1/w)` rồi sort giảm dần theo key — cho diversity tối đa (mọi entity trong pool đều có cơ hội xuất hiện, không phải `random.choice()` lặp lại) mà vẫn nghiêng về tier cao. Pool nhỏ hơn `target_count` thì vòng lại danh sách đã xáo.

Không dùng cách "random sample POI rồi random template rồi xem tỷ lệ ra sao" — cách đó luôn bias theo phân bố sẵn có của master dataset (tỉnh nào OSM có nhiều POI hơn sẽ tự động nhiều sample hơn).

### `province_weights.overrides` — tinh chỉnh riêng một tỉnh

`popularity_tier` chỉ có 3 bậc, không đủ để diễn đạt "tỉnh này nổi bật hơn hẳn các tỉnh cùng tier, nhưng chưa ngang tier 1 municipality". Ví dụ Đà Nẵng: nâng tier 2 → 1 khiến weight bằng đúng Hà Nội/TP.HCM (`6.0 × 1.5`), cả ba cùng chạm `caps.province.max_share = 0.12` và nuốt 36% dataset. `province_weights.overrides` (map `province_id -> hệ số nhân`, mặc định 1.0 nếu vắng mặt) nhân THÊM vào weight sau `by_tier × municipality_bonus`, chỉ ở tầng **plan** (chọn tỉnh nào cho cell) — **không** áp vào `entity_weights` (chọn entity cụ thể trong cell, xem trên): entity `province` vẫn kế thừa nguyên `popularity_tier` của tỉnh, nên nâng tier vẫn khiến tên tỉnh dễ được chọn hơn trong pool `admin_unit`, override chỉ nắn lại tổng khối lượng câu của tỉnh đó. `build_plan` raise `PlanError` nếu `overrides` chứa `province_id` không tồn tại trong master data, thay vì âm thầm bỏ qua.

## Cell key & RNG theo cell

```python
cell_key = (province_id, category, sentence_type, variation_profile)
seed = cell.derive_seed(global_seed)   # navtext.schema.Cell.derive_seed
rng  = random.Random(seed)
```

`derive_seed` dùng `hashlib.sha256`, không phải `hash()` built-in — `hash()` của Python randomize theo `PYTHONHASHSEED` cho `str`/`tuple` chứa `str`, nên seed sẽ đổi giữa các lần chạy và phá golden test / reproduce-run (xem [docs/testing.md](testing.md#golden-test)).

Vì seed dẫn xuất riêng theo cell (không dùng một RNG toàn cục), **regenerate lại một cell không làm xê dịch output của cell khác**. Đây là điều kiện kỹ thuật bắt buộc để vòng lặp QC ở phase 11 hoạt động: khi `qc/balance.find_gaps()` phát hiện một cell thiếu, ta gọi lại đúng `generate_cell(cell, ...)` đó mà không phải generate lại toàn bộ 130k candidate.

## Distribution caps

Định nghĩa trong `config/distribution.yaml`, **không hardcode trong code** (hard rule #8). Bảng dưới là điểm khởi đầu — điều chỉnh sau khi xem kết quả balance của prototype 1k, không phải số cố định vĩnh viễn:

| Trục | Trần | Sàn |
|---|---|---|
| 1 province | ≤ 12% tổng | mỗi province ≥ 0.3% |
| 1 category | ≤ 30% tổng | mỗi category ≥ 3% |
| 1 template_id | ≤ 1.5% tổng | — |
| 1 entity cụ thể (một POI/street) | ≤ 0.15% (~150 sample trên 100k) | — |
| 1 sentence_type | ≤ 35% tổng | ≥ 8% |

Province có tier không đồng đều — Hà Nội/TP.HCM được trọng số cao hơn tỉnh nhỏ vì thực tế người dùng navigation hỏi về đó nhiều hơn — nhưng **vẫn phải nằm dưới trần cứng 12%**. Trần tồn tại chính xác để ngăn hai thành phố lớn nuốt hết dataset.

`popularity_tier` của POI (từ `data-model.md`) ảnh hưởng tới xác suất *bên trong* cell (entity nổi tiếng được chọn thường hơn), nhưng trần entity 0.15% vẫn áp dụng — Hồ Hoàn Kiếm không được xuất hiện quá 150 lần dù nó là landmark nổi tiếng nhất Hà Nội.

## Balancing loop

```
generate candidates (dư 120–150k)
        ↓
qc/balance.measure(samples) -> đếm theo mọi trục ở trên
        ↓
qc/balance.find_gaps(measured, target) -> cell nào thiếu / cell nào thừa
        ↓
   thiếu → generate_cell() lại đúng cell đó (seed riêng, không đụng cell khác)
   thừa  → giữ lại làm buffer cho select_final(), không cần regenerate
        ↓
qc/balance.select_final(candidates, n=100_000) -> cắt xuống đúng 100k,
   ưu tiên giữ đủ sàn mọi cell trước khi trim cell vượt trần
```

Xem chi tiết quy trình QC 4 tầng bao quanh loop này ở [docs/qc.md](qc.md).
