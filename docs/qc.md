# QC & Mentor Review

QC nhiều tầng để candidate 120–150k rơi xuống đúng 100k mà vẫn sạch và cân bằng, cộng quy trình review với mentor sau prototype 1k.

## Tầng 1 — Hard validators (reject ngay)

`qc/validators.py`, mỗi validator là `Callable[[Sample], Issue | None]`, chạy hết trên mọi sample, gộp lại thành `HARD_VALIDATORS`. Sample fail bất kỳ validator nào bị loại, lý do được log vào counter (xem [output.md](output.md#run-manifest)).

| Check | Reject nếu |
|---|---|
| No digits | còn ký tự `[0-9]` trong `text` |
| No Latin abbrev | còn viết tắt chưa expand (`TP`, `Q.`, `BV`, `TTTM`...) |
| Slot filled | còn dấu `{`/`}` sót lại (template chưa fill hết) |
| Hierarchy valid | `ward_id` không thuộc `district_id` đã khai, hoặc FK khác sai |
| Length bound | ngoài ngưỡng `[min_len, max_len]` ký tự theo `length_class` |
| Charset | ký tự lạ ngoài bảng chữ cái tiếng Việt + dấu câu cơ bản |
| Whitespace | double space, leading/trailing space |

Đây là tầng duy nhất **auto-reject**. Ngưỡng cụ thể (min/max length...) nằm trong `config/run.yaml`, không hardcode trong `validators.py`.

## Tầng 2 — Deduplication

`qc/dedup.py`, hai bước:

1. **Exact dedup**: `exact_key(text)` = lowercase + bỏ dấu câu + chuẩn hoá khoảng trắng. Trùng key → chỉ giữ 1.
2. **Near-dup**: `near_dup_clusters(samples)` bằng token-shingle hoặc simhash — bắt các câu chỉ khác nhau filler/particle (`"cho tôi hỏi đường đến X với"` vs `"cho hỏi đường đến X với"`). Mỗi cluster giữ lại tối đa N sample (config), phần dư loại.
3. **Rule bổ sung**: cấm cặp `(template_id, entity_id)` xuất hiện quá 1 lần trong dataset cuối — kể cả khi text khác nhau do variation khác, cặp này lặp quá nhiều nghĩa là generator đang quay vòng hẹp.

## Tầng 3 — Naturalness heuristic (đánh dấu, không auto-reject)

Không có ground truth máy học được cho "câu này có tự nhiên không" ở giai đoạn này, nên tầng này chỉ **flag để người review**, không tự loại:

- Câu dài bất thường so với `length_class` khai báo
- Lặp từ liên tiếp (thường do ghép filler + template lỗi)
- Chồng particle (`"...với nhé với"`)

Output là cột `flags` trong candidate CSV, mentor xem ở bước review.

## Tầng 4 — Balance

`qc/balance.py`, chạy sau tầng 1–2 (chỉ đo trên sample đã sạch):

```
measure(samples)              -> đếm theo mọi trục: province, category, sentence_type,
                                  template_id, entity_id, variation axes
find_gaps(measured, target)   -> cell nào dưới sàn / trên trần so với config/distribution.yaml
                               -> regenerate đúng cell thiếu (xem sampling.md#balancing-loop)
select_final(candidates, n)   -> cắt xuống đúng 100.000:
                                  1. giữ đủ sàn mọi cell trước
                                  2. trim cell vượt trần
                                  3. lấp phần còn lại theo trọng số tier
```

## Mentor Review Workflow

Sau phase 7 (prototype 1k), trước khi generate 120–150k:

1. `navtext review-pack` xuất `reports/prototype_review.md` + một CSV mẫu stratified (lấy đều theo category/sentence_type để mentor không chỉ thấy toàn 1 loại).
2. Mentor đánh giá theo 7 trục: **content, location, natural speech, distribution, variation, format, diversity**.
3. Feedback ghi trực tiếp vào file review (checklist + comment), không qua kênh khác — để có audit trail.

**Feedback → artifact cần sửa:**

| Feedback | Sửa ở đâu |
|---|---|
| Câu không tự nhiên, nghe như dịch máy | `data/templates/templates.yaml`, `lexicon.yaml` |
| Địa danh sai/không tồn tại | `data/raw/`, rebuild master |
| Lệch tỷ trọng (một tỉnh/category quá nhiều) | `config/distribution.yaml` |
| Variation nghèo, ít cách nói | `config/variation.yaml`, thêm giá trị trục |
| Lỗi format (số sót, viết tắt sót) | `qc/validators.py` hoặc `numbers.py` |

Sau khi áp dụng fix (phase 9), **generate lại prototype 1k** trước khi lên 120–150k — không skip vòng lặp này dù đã sửa "nhỏ".
