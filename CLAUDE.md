# NavText — Vietnamese ASR Navigation Text Dataset

Pipeline sinh **đúng 100.000 sample text tiếng Việt** cho ASR domain Navigation (địa danh / địa chỉ / POI).

Đây là file entry point. Chi tiết từng phần nằm trong `docs/` — xem [Doc index](#doc-index) ở cuối.

---

## Objective

Dataset 100.000 câu tiếng Việt **dạng nói (spoken form)**, đa dạng và tự nhiên, mỗi câu chứa ít nhất một location entity Việt Nam, phủ toàn bộ 34 tỉnh/thành hiện hành **và** 63 tên tỉnh cũ.

Ưu tiên xếp theo thứ tự: **diversity cách nói > coverage địa danh > số lượng**. Thêm 1000 POI mới ít giá trị hơn thêm một cách nói tự nhiên mới.

## Scope

**In scope** — câu người dùng nói với ứng dụng bản đồ:
chỉ đường · tìm kiếm địa điểm · hỏi lộ trình · câu hội thoại tự nhiên có chứa địa danh/địa chỉ.

**Non-scope** — đừng làm, kể cả khi trông có vẻ hữu ích:

- Audio, TTS, transcript thật (đây là dataset **text**)
- Câu không chứa location entity
- Intent ngoài navigation (thời tiết, nhạc, gọi điện, đặt lịch)
- Địa danh ngoài Việt Nam
- Ngôn ngữ khác tiếng Việt
- Tên riêng người dùng, số điện thoại, dữ liệu cá nhân

---

## Hard rules

Chín quy tắc dưới đây là bất biến. Vi phạm bất kỳ quy tắc nào làm hỏng dataset ở quy mô lớn chứ không phải sai một chỗ — nên chúng được enforce bằng validator/test, không phải bằng thiện chí.

1. **Cột `text` không được chứa chữ số Ả Rập hoặc viết tắt Latin.** Output là dạng nói: `123` → `một trăm hai mươi ba`, `TP` → `thành phố`, `Q.1` → `quận một`.
2. **34 và 63 tỉnh không phải hai dataset.** Một bảng `provinces` canonical + bảng alias. Tên cũ là một *giá trị variation*, không phải bản sao dữ liệu.
3. **Sampling là quota-driven.** Plan table quyết định số lượng từng cell trước; random chỉ xảy ra *bên trong* cell.
4. **Cấm global random state.** Mọi hàm cần ngẫu nhiên phải nhận `rng: random.Random` tường minh. Không `random.seed()`, không gọi `random.choice()` module-level.
5. **Runtime không gọi LLM.** LLM chỉ dùng offline để soạn thêm template, có human review, rồi commit vào YAML.
6. **Template không được nhúng sẵn tên địa danh.** Mọi entity đi qua slot. `"Chỉ đường đến Hồ Hoàn Kiếm"` là template sai; `"Chỉ đường đến {entity}"` mới đúng.
7. **`data/master/` là build artifact — cấm sửa tay.** Muốn đổi dữ liệu thì sửa `data/raw/` rồi chạy lại `build-master`.
8. **Cấm hardcode `100000` và mọi ngưỡng tỷ trọng trong code.** Chúng sống trong `config/`.
9. **Ưu tiên spoken Vietnamese tự nhiên hơn văn viết đúng ngữ pháp.** `"cho tôi hỏi đường ra Nội Bài với"` tốt hơn `"Vui lòng chỉ dẫn tuyến đường đến Cảng hàng không quốc tế Nội Bài."`

---

## Pipeline

12 phase, mỗi phase có input/output rõ ràng và chạy được độc lập qua CLI.

| # | Phase | Command | Input | Output |
|---|---|---|---|---|
| 1 | Scope & taxonomy | — | — | `docs/data-model.md` |
| 2 | Location data | `navtext build-master` | `data/raw/` | `data/master/*.csv` |
| 3 | Sampling strategy | `navtext plan` | `config/distribution.yaml` + master | `outputs/<run>/plan.json` |
| 4 | Template design | — | — | `data/templates/*.yaml` |
| 5 | Variation rules | — | — | `config/variation.yaml` |
| 6 | Generator | — | phase 2–5 | `src/navtext/generator.py` |
| 7 | Prototype 1k | `navtext generate --target 1000` | plan + master + templates | `outputs/<run>/prototype.csv` |
| 8 | Mentor review | `navtext review-pack` | prototype | `reports/prototype_review.md` |
| 9 | Fix generator | — | feedback | template/config/data updates |
| 10 | Candidates | `navtext generate --target 130000` | plan (scaled) | `outputs/<run>/candidates.csv` |
| 11 | QC + balance | `navtext qc` → `navtext balance` | candidates | `outputs/<run>/clean.csv` + gap report |
| 12 | Final + stats | `navtext finalize` → `navtext stats` | clean | `outputs/<run>/final_100k.csv` + `reports/dataset_stats.md` |

**Tool phụ trợ, KHÔNG phải 1 trong 12 phase** (không sinh artifact nào của dataset): `navtext serve` — web UI read-only đọc `data/master/`, xem [docs/web.md](docs/web.md).

**Ba mốc bắt buộc, không được nhảy cóc:**

- **Prototype 1k trước.** Không bao giờ generate 100k khi chưa có người xem qua 1000 câu đầu.
- **Sinh dư 120–150k candidate.** Dedup và balance đều *loại bỏ* sample; không có phần dư thì không cắt được về đúng 100k mà vẫn giữ phân bố.
- **`finalize` cắt xuống đúng 100.000**, ưu tiên lấp cell thiếu trước khi trim cell dư.

---

## Anti-patterns

| ✗ Đừng | Vì sao |
|---|---|
| Generate 100k rồi mới nghĩ tới QC | Phải sinh lại từ đầu, mất giờ compute và mất cả feedback loop |
| `random.choice(all_pois)` | Bias theo kích thước pool — tỉnh nào nhiều POI trong OSM sẽ nuốt dataset |
| Rải `100000` khắp code | Không scale được 1k → 10k → 100k, và không ai biết con số thật nằm ở đâu |
| Gọi LLM lúc runtime để sinh địa danh | Hallucination → địa danh không tồn tại, và mất reproducibility |
| Template nhúng sẵn tên địa danh | Entity đó vượt trần tỷ trọng mà không hệ thống nào phát hiện được |
| Sửa tay `data/master/` | Lần build sau ghi đè mất, và không ai truy được thay đổi từ đâu ra |
| Coi 34/63 là hai dataset | Sample trùng lặp, coverage tính sai, không map được tên cũ ↔ tên mới |
| Dùng `random` global | Thêm một lệnh gọi ở giữa pipeline là toàn bộ output sau đó đổi |
| Để chữ số / viết tắt lọt vào `text` | Sai mục đích dataset — model ASR học nhầm |
| Sinh thêm sample cho tỉnh "dễ" khi thiếu số lượng | Phá balance; phải regenerate đúng cell thiếu |

---

## Code conventions

- Setup: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`. Test: `.venv/bin/pytest`.
- **Python 3.12**, stdlib-first. Chỉ thêm dependency khi thật sự cần (đọc `.pbf` OSM, near-dup hashing).
- **Type hints bắt buộc** ở mọi public function.
- `dataclass` cho schema (`Sample`, `Cell`, `LocationRef`) — không dùng dict trần để truyền dữ liệu giữa module.
- `pathlib.Path`, không string path.
- **Không side-effect ở import time**: không đọc file, không build dữ liệu khi import module.
- Logging dùng **structured counter** (`Counter` theo cell/reject-reason), không print rải rác. Mỗi lần generate/QC phải log được: số sinh ra, số reject theo từng lý do, số dedup, thời gian.
- Hàm nhận `rng` tường minh (xem hard rule #4).
- Tiếng Việt trong data/template/doc; tên biến, hàm, module bằng tiếng Anh.

---

## Cây thư mục

```
CLAUDE.md
docs/                     # tài liệu chi tiết, xem index bên dưới
config/
  distribution.yaml       # target size, trọng số, trần/sàn tỷ trọng
  variation.yaml          # trọng số các trục variation
  run.yaml                # global_seed, đường dẫn, ngưỡng QC, admin_invariants (34/63)
  osm_tags.yaml           # mapping OSM tag -> (category, subcategory) cho fetch_osm.py/build_master.py
data/
  raw/                    # BẤT KHẢ XÂM PHẠM — nguồn gốc, không sửa
    admin/                # danh sách hành chính (34 hiện hành + 63 cũ), landmarks.yaml,
                          #   wards_osm.csv (trích từ .pbf) + SOURCES.md
    osm/                  # snapshot JSON thô OSM (ingest bằng scripts/fetch_osm.py)
    osm_pbf/              # .pbf Geofabrik toàn quốc — KHÔNG commit, tải lại khi cần
  master/                 # BUILD ARTIFACT — sinh bởi build-master, cấm sửa tay
    provinces.csv  province_alias.csv  districts.csv
    wards.csv      streets.csv         pois.csv
  templates/
    templates.yaml        # template bank
    lexicon.yaml          # filler, particle, tiền tố hành chính
scripts/
  fetch_osm.py            # ingest OSM chạy tay, ngoài pipeline — build-master không gọi network
  extract_wards.py        # trích phường/xã từ .pbf (point-in-polygon theo ranh giới tỉnh)
src/navtext/
  schema.py  loaders.py  build_master.py  aliases.py  numbers.py
  sampling.py  templates.py  variation.py  generator.py
  stats.py  cli.py
  qc/
    validators.py  dedup.py  balance.py
  web/                    # tool phụ trợ read-only, xem docs/web.md — không phải 1 trong 12 phase
    query.py  render.py  routes.py  server.py
tests/
outputs/<run_id>/         # plan.json, candidates.csv, final_100k.csv, run_manifest.json
reports/                  # prototype_review.md, dataset_stats.md
```

---

## Doc index

| Cần làm gì | Đọc |
|---|---|
| Hiểu taxonomy, schema địa danh, mô hình 34↔63 | [docs/data-model.md](docs/data-model.md) |
| Phân bổ quota, trần/sàn tỷ trọng, seed theo cell | [docs/sampling.md](docs/sampling.md) |
| Viết template, variation rules, đọc số tiếng Việt, module list | [docs/generation.md](docs/generation.md) |
| Validator, dedup, balance loop, mentor review | [docs/qc.md](docs/qc.md) |
| CSV schema, statistics report, run manifest | [docs/output.md](docs/output.md) |
| Viết test, reproduce một run cũ | [docs/testing.md](docs/testing.md) |
| Chạy `navtext serve`, endpoint list, giới hạn coverage ở web UI | [docs/web.md](docs/web.md) |
