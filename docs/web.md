# Web UI — `navtext serve`

Web UI **read-only**, đọc `data/master/`, `data/templates/`, `data/raw/`, `config/distribution.yaml` và `config/variation.yaml`, phục vụ soi dữ liệu khi chuẩn bị phase 3 (sampling)/phase 4 (template)/phase 5 (variation)/phase 6 (generator). Không phải một trong 12 phase — không sinh ra artifact nào của dataset, không được import bởi bất kỳ module pipeline nào.

## Giới hạn cần biết trước khi đọc số liệu ở đây

**"Coverage" trên trang này KHÔNG PHẢI coverage 34/34 + 63/63 của [docs/output.md](output.md#statistics-report).**

- Coverage ở `docs/output.md` đo địa danh có xuất hiện trong `text` của `final_100k.csv` hay không — điều kiện PASS/FAIL bắt buộc để xuất dataset (phase 12).
- Coverage trên `navtext serve` chỉ đo ở tầng **master data**: tỉnh/alias có tồn tại trong bảng, và có entity nào thuộc về nó hay không. Đây là điều kiện cần, yếu hơn nhiều — dashboard hiện "34/34 OK" ở đây không có nghĩa dataset đã sẵn sàng: coverage 34/34 + 63/63 chính thức chỉ tính trên `final_100k.csv` sau khi qua đủ QC/balance/finalize (phase 11–12), xem [docs/output.md](output.md#statistics-report).

Mỗi chỗ hiển thị coverage trên UI đều kèm ghi chú này (`stats.MASTER_COVERAGE_NOTE`) — đừng xoá ghi chú đó khi sửa template.

Tương tự, `tier_distribution` chưa mang thông tin thật: mọi POI từ OSM hiện mang `popularity_tier=2` (default cứng của `build_master.build_pois()`), chờ tới khi có tín hiệu độ nổi tiếng thật.

**Trọng số trên trang `/variation` là tỷ lệ MONG MUỐN, không phải tần suất thực tế.** `config/variation.yaml` khai báo trọng số các trục (`name_variant`, `number_style`, ...); generator (phase 6) ép giá trị khi trục không áp dụng được cho entity — vd `name_variant=legacy` chỉ có nghĩa khi câu nhắc tên tỉnh, nên street/POI luôn bị ép `current` bất kể weight khai báo. Phân bố thật trong dataset chỉ đo được sau phase 7 bằng `navtext stats` (`stats.VARIATION_WEIGHT_NOTE`).

## Chạy

```bash
navtext build-master          # nếu data/master/ chưa có
navtext serve                 # mặc định http://127.0.0.1:8000/ — /samples tự tìm
                               # CSV mới nhất trong outputs/, nếu có
navtext serve --port 8123 --host 127.0.0.1
navtext serve --samples outputs/<run>/prototype.csv   # xem đúng một file, không tự đoán
navtext serve --no-auto-samples                       # /samples chỉ nạp khi có --samples
```

Bind mặc định `127.0.0.1`, không `0.0.0.0` — đây là tool dev đọc file trên máy, không tự phơi ra LAN. Muốn khác thì truyền `--host` tường minh.

Ctrl-C để thoát (exit 0, không traceback).

## Endpoints

| Path | Nội dung |
|---|---|
| `GET /` | Dashboard: totals, master-data coverage, phân bố category, subcategory rỗng |
| `GET /readiness` | Danh sách check "đã đủ điều kiện chạy phase tiếp theo chưa" |
| `GET /provinces` | Bảng 34 tỉnh: tên, tier, region, số alias, số entity từng loại |
| `GET /provinces/<province_id>` | Chi tiết 1 tỉnh: alias theo loại, district/ward, mẫu entity |
| `GET /entities?q=&province=&category=&subcategory=&page=` | Browse toàn bộ street/POI, có filter + phân trang (100/trang) |
| `GET /samples?q=&province=&category=&sentence_type=&register=&name_variant=&page=` | Xem MỘT file CSV đã generate — `--samples` hoặc tự động chọn run mới nhất trong `--output-dir` — filter + phân trang, banner ghi rõ nguồn + giai đoạn QC — không phải QC/coverage chính thức, xem callout trên trang |
| `GET /templates?q=&sentence_type=&register=&length_class=&page=` | Template bank: ma trận coverage 30 ô, filter + phân trang, độ dài khung, lexicon |
| `GET /config` | `config/distribution.yaml`: trọng số, trần/sàn, variation profile |
| `GET /variation` | `config/variation.yaml` (phase 5): trọng số 6 trục trong-cell, override by_register/by_category, lexicon_rates, không gian variation ước lượng |
| `GET /raw` | `data/raw/`: đếm bảng admin, snapshot OSM, đối chiếu raw ↔ master |
| `GET /api/report.json` | Toàn bộ `build_master_report()` dạng JSON, cho script/CI đọc mà không parse HTML |
| `GET /api/readiness.json` | `build_readiness_report()` dạng JSON — dùng được làm cổng CI |
| `GET /api/variation.json` | `build_variation_report()` dạng JSON |
| `GET /api/samples.json` | Tổng hợp (`source`, `total`, `summary` theo category/province/sentence_type/register/name_variant) — KHÔNG dump toàn bộ hàng, tránh response khổng lồ khi candidate lên 130k dòng |

**Read-only tuyệt đối**: chỉ có `do_GET`, không `do_POST`/`do_PUT`/`do_DELETE` (xem [src/navtext/web/server.py](../src/navtext/web/server.py)). Muốn đổi dữ liệu vẫn đi đúng đường: sửa `data/raw/` hoặc `data/templates/`, chạy lại `navtext build-master`, restart `navtext serve`.

## Trang `/readiness`

Gom mọi tín hiệu chặn pipeline thành một danh sách check có trạng thái `ok`/`warn`/`fail`:

| Check | fail/warn khi |
|---|---|
| `template_bank_valid` | `load_templates()` raise `TemplateError` |
| `template_coverage` | có ô dưới `min_templates_per_cell` trong 30 ô |
| `template_cap` | ô nào ép template vượt `caps.template.max_share` |
| `plan_feasible` | `build_plan()` raise `PlanError` |
| `province_entities` | tỉnh có 0 entity |
| `empty_subcategories` | subcategory không có entity nào |
| `variation_config_valid` | `load_variation_config()` raise `VariationError` |
| `variation_space` | có warning `space_below_candidates` (không gian variation nhỏ hơn candidate count) hoặc `dead_value` (giá trị weight 0, không bao giờ được chọn) |

`plan_feasible` hiện **PASS** trên master data thật (34/34 tỉnh có street/POI + phường/xã — xem [docs/data-model.md](data-model.md#nguồn-dữ-liệu--build)). Trước đây check này FAIL suốt giai đoạn thin slice 3/34 tỉnh, và đó chính là lý do trang này tồn tại: để thấy ngay trạng thái feasibility mà không phải chạy Python trong terminal.

Template bank hỏng **không** làm server crash: `TemplateError` được bắt lúc khởi động và đẩy vào check `template_bank_valid` để hiển thị nguyên danh sách lỗi.

## Nạp thiếu nguồn — degrade, không crash

`navtext serve` nạp sáu nguồn độc lập; thiếu/hỏng nguồn nào thì chỉ trang đó hiện callout hướng dẫn, phần còn lại vẫn chạy:

```bash
navtext serve --template-dir data/templates \
              --distribution-config config/distribution.yaml \
              --variation-config config/variation.yaml \
              --raw-dir data/raw \
              --output-dir outputs \
              --samples outputs/<run>/prototype.csv   # tuỳ chọn — không truyền thì tự tìm trong --output-dir
```

Lý do: thiếu template bank không được ngăn người dùng xem master data. Mỗi nguồn thiếu in một dòng `CẢNH BÁO` ra stderr lúc khởi động.

## Search không dấu

`/entities?q=` fold cả chuỗi tìm kiếm và tên entity qua `web.query.fold_search_key()` (NFD, bỏ combining mark, casefold, cộng riêng `đ/Đ -> d/D` vì đây là ký tự Unicode base độc lập không tách được bằng NFD) — gõ `"duong nguyen hue"` khớp `"Đường Nguyễn Huệ"`.

## Kiến trúc nội bộ

```
src/navtext/web/
  query.py     EntityQuery/TemplateQuery/SampleQuery, filter_*(), summarize_samples(), paginate(), fold_search_key()  — hàm thuần
  charts.py    kpi_card/bar_list/dot_grid/heat_matrix/check_list -> str              — hàm thuần
  labels.py    nhãn tiếng Việt cho enum                                              — hằng số
  render.py    render_dashboard/provinces/entities/samples/templates/readiness/config/variation/raw — hàm thuần
  routes.py    Response, AppContext, route(path, query_string, ctx) -> Response      — hàm thuần
  server.py    make_server(ctx, host, port) -> ThreadingHTTPServer                   — adapter mỏng, điểm DUY NHẤT chạm http.server
```

Số liệu **không** tính trong `web/`: mọi report đến từ `navtext.stats` (`build_master_report`, `build_template_report`, `build_raw_report`, `build_readiness_report`) — hàm thuần, không đọc file, caller truyền data vào tường minh. `web/` chỉ trình bày. Ngoại lệ nhỏ: `web.query.summarize_samples()` đếm trực tiếp trên CSV row đã đọc (`cli._cmd_serve` đọc file, không qua `navtext.stats`) vì đây là dữ liệu duyệt (browse), không phải một domain report của pipeline — `SAMPLES_PREVIEW_NOTE` (hằng số trong `navtext.stats`) vẫn được import thẳng vào `render.py` để giữ đúng câu cảnh báo dùng chung.

`paginate()` là generic (`list[T]`) nên dùng chung cho `/entities` lẫn `/templates`, không có hai bản sao lệch nhau về sau.

`route()` test được trực tiếp, không cần mở socket thật (xem [tests/test_web_routes.py](../tests/test_web_routes.py)); `tests/test_web_server.py` chỉ có 1 smoke test mở server thật để xác nhận adapter nối đúng.

`AppContext` (chứa `MasterData` + report đã build) tạo **một lần lúc `navtext serve` khởi động**, dùng lại cho mọi request — 41k+ row đọc lại mỗi request là vô nghĩa vì data chỉ đổi khi chạy lại `build-master`. Muốn thấy data mới thì restart server.

**Sửa code cũng phải restart, không riêng sửa data.** Module Python được nạp vào bộ nhớ lúc khởi động và không đọc lại file; một server chạy từ trước khi sửa code vẫn phục vụ code cũ — route mới trả 404 và trông y hệt như UI bị hỏng. Vì vậy mọi trang có **dòng chân trang ghi thời điểm khởi động**: thấy mốc thời gian cũ hơn lần sửa code gần nhất thì nguyên nhân là server cũ, không phải UI lỗi.

```bash
ps -o pid,lstart,cmd -C navtext     # xem server nào đang chạy, từ lúc nào
```

Mọi giá trị nhúng vào HTML (tên entity từ OSM, alias, tham số query string phản chiếu lại vào form) đi qua `html.escape()` — tên POI là dữ liệu người lạ soạn, phòng XSS thật chứ không phải phòng hờ.

## `/samples` — xem CSV đã generate

Từ phase 6, `navtext generate` đã sinh ra CSV thật (`outputs/<run>/prototype.csv`, `candidates.csv`, ...). `--samples <path>` nạp **đúng một file** người dùng chỉ định, ghi đè tuyệt đối lên auto-discovery bên dưới.

**Không truyền `--samples`**: `cli._discover_samples()` tự chọn run dir mới nhất trong `--output-dir` (mặc định `outputs/`, xếp theo mtime — KHÔNG phải theo tên `run_id`), rồi trong run dir đó ưu tiên file theo giai đoạn QC càng sạch càng trước: `final_100k.csv` > `clean.csv` > `candidates.csv` > `prototype.csv`. Run dir không có file nào trong 4 tên trên (vd chỉ có `plan.json`) bị bỏ qua, thử run dir cũ hơn tiếp theo. Banner trên trang luôn ghi rõ đường dẫn đang xem + giai đoạn QC suy ra từ tên file, và có "Tự động chọn" khi đó là kết quả auto-discovery (không phải người dùng chỉ định). Tắt hẳn cơ chế này bằng `--no-auto-samples`.

**Đây chỉ là xem nhanh, KHÔNG PHẢI coverage/QC chính thức** (`stats.SAMPLES_PREVIEW_NOTE`, hiển thị ngay trên trang, cùng tinh thần `MASTER_COVERAGE_NOTE`): coverage 34/34 + 63/63 chính thức của [docs/output.md](output.md#statistics-report) chỉ đo trên `final_100k.csv` sau khi qua đủ `qc`/`balance`/`finalize` (phase 11–12) — xem một `prototype.csv`/`candidates.csv` tuỳ ý ở đây không thay thế được `navtext stats`. Auto-discovery ưu tiên file sạch nhất có mặt, nhưng nếu run mới nhất chỉ mới chạy tới `prototype.csv` thì đó vẫn là thứ được hiện — đọc kỹ nhãn giai đoạn trên banner trước khi diễn giải số liệu.

Không có sample nào trong `--output-dir` (kể cả sau khi thử auto-discovery) → `/samples` hiện callout hướng dẫn, không lỗi (đúng pattern degrade của 4 nguồn phụ khác).

## Ngoài phạm vi

Form sửa dữ liệu, QC review annotation, auth, deploy ngoài localhost.

**Không có form sửa dữ liệu là quyết định có chủ đích**, không phải việc chưa làm: `data/master/` là build artifact (hard rule #7), sửa tay sẽ bị ghi đè ở lần `build-master` sau và không truy được nguồn gốc thay đổi. Đường sửa dữ liệu đúng vẫn là `data/raw/` → `build-master` → restart. Tương tự, `/samples` chỉ đọc — không có nút loại sample, sửa text, hay ghi annotation; đường sửa đúng vẫn là `qc/`/`data/templates/`/`config/` rồi generate lại.
