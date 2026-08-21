# Output

CSV schema cuối cùng, statistics report, và run manifest cho reproducibility.

## CSV schema

`outputs/<run_id>/final_100k.csv`, một dòng = một sample:

| Cột | Ý nghĩa |
|---|---|
| `id` | unique id, ổn định qua các lần build (không đổi khi regenerate cell khác) |
| `text` | câu output, spoken-form-only (hard rule #1) |
| `category` | xem [data-model.md](data-model.md#taxonomy) |
| `subcategory` | xem taxonomy |
| `entity` | tên entity đã resolve (theo `name_variant` đã chọn) |
| `entity_id` | FK về `pois.csv`/`streets.csv`/... trong master data |
| `province` | tên tỉnh hiện hành |
| `province_legacy` | tên tỉnh cũ nếu `name_variant=legacy` được dùng trong câu, rỗng nếu không |
| `district` | |
| `ward` | |
| `street` | |
| `address` | địa chỉ đầy đủ dạng cấu trúc (trước khi verbalize), phục vụ audit |
| `sentence_type` | 1 trong 6 loại, xem [generation.md](generation.md#sentence-types) |
| `template_id` | template gốc dùng để sinh câu |
| `variation` | tổ hợp variation dạng chuỗi ngắn, vd `legacy+cardinal+prefix+casual` |
| `name_variant` | current / legacy / spoken_alias |
| `number_style` | cardinal / digit_by_digit / mixed / none |
| `register` | formal / neutral / casual |
| `source` | osm / manual — nguồn gốc entity trong master data |
| `seed_cell` | cell_key dùng để sinh sample này, phục vụ debug/reproduce một sample cụ thể |

Mọi cột variation ở đây map trực tiếp tới một trục trong [generation.md#variation-axes](generation.md#variation-axes) — không thêm cột tự do ngoài danh sách này mà không cập nhật cả hai file.

## Statistics report

`navtext stats` sinh `reports/dataset_stats.md` + `reports/dataset_stats.json` (cùng nội dung, hai định dạng). Tối thiểu phải có:

- Total samples (phải đúng 100.000 ở bản final)
- Geographic distribution (theo province, top/bottom N)
- Category / subcategory distribution
- Sentence type distribution
- Variation distribution (từng trục)
- **34/34 current province coverage** — bảng PASS/FAIL, FAIL nếu bất kỳ province nào có 0 sample
- **63/63 legacy alias coverage** — tương tự, FAIL nếu bất kỳ legacy alias nào chưa từng được dùng
- Entity coverage (bao nhiêu % pois/streets trong master data thực sự được dùng ít nhất 1 lần)
- Duplicate rate (đo trước/sau dedup, để theo dõi hiệu quả tầng 2 QC)
- Template distribution (phát hiện template nào đang bị dùng quá tay dù dưới trần)

Hai dòng coverage 34/63 là **bắt buộc PASS** trước khi coi một run là final — không xuất `final_100k.csv` chính thức nếu report này FAIL.

## `plan.json`

`outputs/<run_id>/plan.json`, sinh bởi `navtext plan` (phase 3) — plan table trước khi generate bất kỳ sample nào, xem [docs/sampling.md](sampling.md):

```json
{
  "schema_version": 1,
  "run_id": "r1",
  "total": 100000,
  "global_seed": 20260815,
  "config_hash": "sha256 của config/distribution.yaml",
  "master_data_hash": "sha256 của data/master/*.csv",
  "generated_at": "2026-08-15T12:00:00+00:00",
  "cells": [
    {
      "province_id": "01",
      "category": "street",
      "sentence_type": "nav_command",
      "variation_profile": "casual_short",
      "target_count": 87
    }
  ]
}
```

`config_hash`/`master_data_hash` dùng chung helper `cli._sha256_of_paths()` với `run_manifest.json` ở dưới — cùng input hash → cùng plan tái tạo được.

## Run manifest

`outputs/<run_id>/run_manifest.json`, ghi lại mọi thứ cần để tái tạo run byte-identical:

```json
{
  "run_id": "...",
  "global_seed": 20260815,
  "config_hash": "sha256 của toàn bộ config/*.yaml",
  "master_data_hash": "sha256 của data/master/*.csv",
  "template_bank_hash": "sha256 của data/templates/*.yaml",
  "code_version": "git commit hash hoặc package version",
  "cell_counts": { "cell_key": "target_count thực tế sinh ra", "...": "..." },
  "timestamp": "..."
}
```

Nguyên tắc: **cùng manifest (cùng 5 hash + seed) → chạy lại cho ra output byte-identical.** Nếu bất kỳ input nào đổi (sửa 1 dòng master data, thêm 1 template), hash đổi theo, và manifest cũ trở thành bằng chứng "run này được sinh từ trạng thái nào" chứ không còn tái tạo được — đó là kỳ vọng đúng, không phải lỗi.

Xem [docs/testing.md](testing.md#reproduce-một-run) cho quy trình reproduce cụ thể.
