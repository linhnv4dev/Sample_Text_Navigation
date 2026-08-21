# Testing & Reproducibility

Cách test generator, và cách tái tạo lại một run đã sinh trước đó.

## Golden test

Chạy `generate_all(plan, ctx)` với một `global_seed` cố định trên một plan nhỏ (vài chục cell), hash toàn bộ output, so với hash đã lưu trong `tests/golden/`. Đổi bất kỳ điều gì trong `numbers.py`, `templates.py`, `variation.py` mà không cố ý thay output → test này báo đỏ ngay.

Cập nhật golden hash là hành động **có chủ đích**, không phải auto-fix khi test fail — nếu output đổi, phải biết chính xác vì sao trước khi chấp nhận hash mới.

## Unit test cho `numbers.py`

Module rủi ro cao nhất (xem [generation.md](generation.md#numberspy--module-rủi-ro-cao-nhất)), cần bảng test case rộng, tối thiểu:

```python
cases = [
    (10, "mười"),
    (15, "mười lăm"),
    (21, "hai mươi mốt"),
    (24, "hai mươi tư"),   # hoặc "hai mươi bốn" tuỳ config, test cả 2 branch
    (25, "hai mươi lăm"),
    (100, "một trăm"),
    (105, "một trăm lẻ năm"),
    (110, "một trăm mười"),
]
```

Cộng test riêng cho `read_house_number()`: `"15B"`, `"12/3"`, `"QL1A"` — các case alphanumeric/ký hiệu, không chỉ số nguyên thuần.

Cộng test cho `expand_abbrev()`: mọi viết tắt trong bảng ở `generation.md` phải có ít nhất 1 test case.

## Property test

Chạy trên output của `generate_cell()`/`generate_all()`, không cần biết nội dung cụ thể:

- `text` không chứa `[0-9]` (hard rule #1)
- `text` không còn `{`/`}` (slot chưa fill lọt qua)
- Với mọi sample có `ward`, `ward` thực sự thuộc `district` đã khai (hierarchy FK đúng)
- `text` không có double space / leading-trailing space

Property test này nên là bản Python thuần của tầng 1 validator trong `qc/validators.py` — nếu hai bên lệch nhau, một trong hai đang sai.

## Distribution test

Chạy trên prototype 1k (phase 7), không chờ tới 100k mới phát hiện lệch:

- Không province nào vượt trần / dưới sàn theo tỷ lệ tương ứng ở quy mô 1k
- Không template_id nào chiếm quá trần
- Mọi sentence_type đều xuất hiện

Test này chạy lại mỗi lần trước khi lên candidate 120–150k (phase 10) — xem mốc bắt buộc ở [CLAUDE.md](../CLAUDE.md#pipeline).

## Reproduce một run

1. Lấy `run_manifest.json` của run cần tái tạo.
2. Checkout đúng `code_version`.
3. Kiểm tra `config_hash`, `master_data_hash`, `template_bank_hash` khớp với trạng thái hiện tại của `config/`, `data/master/`, `data/templates/` — nếu không khớp, dừng lại, không chạy tiếp (kết quả sẽ không tái tạo được đúng).
4. Chạy `navtext generate --seed <global_seed> --plan <plan.json đã lưu>`.
5. So sánh hash output với hash trong manifest — phải khớp tuyệt đối (byte-identical), không phải "gần giống".

Nếu hash không khớp dù mọi input hash đều khớp → có global random state hoặc side-effect ở đâu đó đang vi phạm hard rule #4, cần tìm và sửa trước khi tiếp tục dùng pipeline.
