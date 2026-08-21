# Generation

Template architecture, sentence types, variation axes, quy tắc đọc số tiếng Việt, và danh sách module bắt buộc.

## Template bank

Mọi câu trong bank là câu **người dùng nói vào micro cho trợ lý điều
hướng** — không phải câu hỏi đường một người khác, và không phải văn viết.
`register` **không phải mức độ lịch sự liên nhân** (không ai dùng kính ngữ
với máy) — nó là trục "đầy đủ/rõ ràng ↔ cụt/khẩu ngữ":

- `formal` — câu đầy đủ chủ-vị, rõ ràng, ít khẩu ngữ (`"chỉ đường giúp tôi
  đến {entity}"`).
- `neutral` — cách nói mặc định, bỏ chủ ngữ, câu lệnh trần (`"chỉ đường đến
  {entity}"`) — đây là style trung tâm của bank.
- `casual` — khẩu ngữ hơn, cụt hơn, dùng "mình" + tiểu từ (`"đưa mình tới
  {entity}"`), nhưng **không phải slang vùng miền mạnh** (tránh `hông, vô,
  giùm, lẹ, quẹo, tấp, cua, lượn, phóng, coi, ngó, nè, á` — casual nghĩa là
  SPOKEN, phải nghe tự nhiên ở cả ba miền, không phải cố tình đặc trưng một
  vùng).

Ràng buộc áp cho toàn bank, không chỉ một register:

- Không kính ngữ liên nhân (`xin, vui lòng, thưa, kính, mong, nhờ, làm ơn,
  cảm ơn, ạ`) — không ai nói vậy với máy.
- Không cầu viện người thứ ba (`có ai biết…`, `hỏi thăm…`) và không dựng
  hội thoại giả giữa hai người (kịch bản nói với tài xế/người ngồi cạnh) —
  người nói luôn hướng thẳng về trợ lý.
- Câu điều khiển thiết bị có thật (mở/bật chỉ đường, đặt/đổi điểm đến, thêm
  điểm dừng) rải đều 3 register, không chỉ nav_command/route_question.

`scripts/eval_templates.py` (chạy tay, ngoài 12 phase) chấm bank theo đúng
các tiêu chí trên — dùng để so sánh trước/sau khi sửa bank, xem
`reports/template_register_review_before.md` và `_after.md`.

`data/templates/templates.yaml`, mỗi entry:

```yaml
template_id: nav_command_neutral_short_003
sentence_type: nav_command
register: neutral        # formal | neutral | casual
length_class: short      # short | medium | long
slots: [entity, closer]
text: "chỉ đường đến {entity}{closer}"
```

Chỉ 3 slot hợp lệ: `{opener}`, `{entity}`, `{closer}` (`templates.ALLOWED_SLOTS`). `{entity}` là một chuỗi **đã compose sẵn** bởi `variation.py` (admin_prefix + tên đã resolve + đuôi hierarchy, vd `"đường Nguyễn Văn Linh ở quận Hải Châu"`) — nhờ vậy một template dùng chung được cho mọi category (street, POI, admin_unit...).

**Quy ước `template_id`**: `<sentence_type>_<variation_profile>_<nnn>` (profile đã gộp sẵn register+length_class, xem `config/distribution.yaml`). Đánh số riêng trong từng ô — thêm template mới vào một ô không dịch id của ô khác.

**Quy ước spacing**: `{opener}`/`{closer}` không có khoảng trắng liền kề trong `text` (vd `"{opener}chỉ đường đến {entity}{closer}"`, không phải `"{opener} chỉ đường..."`). Composer ở phase 6 tự chèn space khi giá trị filled không rỗng và chuẩn hoá `" ".join(text.split())` — nhờ vậy opener/closer rỗng không để lại double space. `{entity}` luôn có mặt (không bao giờ rỗng) nên dùng space literal bình thường quanh nó, không cần quy ước đặc biệt.

**Casing**: toàn bộ `text` viết thường, không dấu câu cuối câu — đúng tinh thần "spoken-form transcript" (hard rule #9) và loại bỏ hoàn toàn rủi ro nhầm một từ viết hoa giữa câu với tên địa danh hardcode. Entity thật (viết hoa) chỉ tới từ `{entity}` lúc fill, không nằm trong template text.

**Quy tắc cứng: template không được chứa sẵn tên địa danh thật.** Mọi entity phải đi qua `{slot}`. `templates.load_templates()` chạy validator ngay lúc load — 9 check dưới đây, **reject chứ không warn**:

| # | Check | Reject nếu |
|---|---|---|
| 1 | id unique | `template_id` trùng hoặc rỗng |
| 2 | enum hợp lệ | `sentence_type`/`register`/`length_class` không parse được sang enum ở `schema.py` |
| 3 | slot khớp | tập slot khai báo ≠ tập `{...}` thực có trong `text` |
| 4 | slot hợp lệ | slot nào ngoài `ALLOWED_SLOTS` (`opener`/`entity`/`closer`) |
| 5 | có entity | `{entity}` vắng mặt — mọi câu navigation phải chứa location entity |
| 6 | không chữ số | `text` khớp `[0-9]` (hard rule #1) |
| 7 | không viết tắt | `text` chứa viết tắt trong `numbers.ABBREV_MAP` (`TP`, `Q.`, `P.`, `H.`, `BV`, `TTTM`, `QL`, `TL`) — một nguồn sự thật dùng chung với `expand_abbrev()` |
| 8 | không tên riêng | token viết hoa, không phải token đầu câu, không nằm trong `lexicon.proper_noun_allowlist` — nghi ngờ tên địa danh nhúng cứng (hard rule #6) |
| 9 | spacing | double space, leading/trailing whitespace, hoặc space liền kề `{opener}`/`{closer}` |

Lý do check #8: nếu một địa danh nằm cứng trong template, số lần nó xuất hiện phụ thuộc vào số lần template đó được chọn — vượt khỏi kiểm soát của trần entity 0.15% ở `sampling.md`, và không detector nào bắt được vì nó không đi qua pipeline chọn entity.

**Ngân sách độ dài** (khung chữ trước khi fill, không phải bảo đảm — đo thật ở prototype 1k):

| length_class | khung mục tiêu | ví dụ |
|---|---|---|
| short | ≤ 20 ký tự | `"đi {entity}{closer}"` |
| medium | 25–45 ký tự | `"{opener}chỉ đường đến {entity} giúp tôi{closer}"` |
| long | ≥ 55 ký tự | `"{opener}tôi đang muốn tới {entity}, từ chỗ này đi đường nào nhanh nhất{closer}"` |

## Sentence types

6 loại, cover cách nói thực tế của người dùng navigation:

| sentence_type | Ví dụ |
|---|---|
| `nav_command` | "Chỉ đường đến Hồ Hoàn Kiếm." |
| `search` | "Tìm giúp tôi sân bay Nội Bài." |
| `route_question` | "Từ đây đến Hồ Hoàn Kiếm đi thế nào?" |
| `conversational` | "Cho tôi hỏi đường đến Hồ Hoàn Kiếm với." |
| `short_command` | "Hồ Hoàn Kiếm." / "Đi Nội Bài." |
| `contextual` | "Quán đó gần Hồ Hoàn Kiếm không?" |

## Lexicon — chống câu văn viết cứng

`data/templates/lexicon.yaml` chứa filler/particle để template không đọc như văn bản dịch máy:

```
openers:    cho tôi hỏi, làm ơn, cho hỏi
closers:    với, nhé, giúp mình, được không, đi
fillers:    à, ơi, thế
phrasings:  đi thế nào, đi kiểu gì, đường nào gần nhất
```

`variation.py` chọn ngẫu nhiên (trong rng của cell) một tổ hợp opener/closer phù hợp `register` — casual dùng nhiều filler, formal thì không dùng.

## Vai trò của LLM — chỉ offline

LLM (Claude) được dùng để **soạn thêm template** khi template bank nghèo nàn cho một sentence_type/register nào đó. Quy trình: sinh draft → người review sửa → commit thành YAML vào `data/templates/`. **Runtime generator không bao giờ gọi LLM** (hard rule #5) — lý do là hallucination địa danh và mất reproducibility (một API call không xác định sẽ phá golden test).

## Variation axes

Các trục variation độc lập, kết hợp tự do, mỗi trục được ghi vào một cột riêng trong CSV cuối để audit được (xem [docs/output.md](output.md#csv-schema)):

| Trục | Giá trị | Ghi chú |
|---|---|---|
| `name_variant` | `current` / `legacy` / `spoken_alias` | chọn theo `province_alias.alias_type` |
| `number_style` | `cardinal` / `digit_by_digit` / `mixed` / `none` | cách đọc số nhà/số đường |
| `number_prefix` | có "số" / không | `"123 Nguyễn Văn Linh"` → `"một trăm hai mươi ba..."` vs `"số một trăm hai mươi ba..."` |
| `admin_prefix` | có "đường/phường/quận/tỉnh" / lược bỏ | `"Nguyễn Văn Linh"` vs `"đường Nguyễn Văn Linh"` |
| `register` | `formal` / `neutral` / `casual` | khớp với `register` của template |
| `length_class` | `short` / `medium` / `long` | khớp với `length_class` của template |
| `hierarchy_depth` | `poi_only` / `poi_district` / `full_address` | mức chi tiết địa chỉ nêu ra |

Vì output là spoken-form-only (hard rule #1), trục số **không phải** "có chữ số hay không" mà là **cách đọc**:

```
một trăm hai mươi ba Nguyễn Văn Linh                (cardinal, không prefix)
số một trăm hai mươi ba Nguyễn Văn Linh              (cardinal, có prefix)
một hai ba Nguyễn Văn Linh                           (digit_by_digit)
số một trăm hai ba đường Nguyễn Văn Linh             (cardinal + admin_prefix)
```

## `config/variation.yaml` — trọng số trục

Phase 5. Trong khi `config/distribution.yaml` quyết định **quota** (bao nhiêu sample rơi vào mỗi cell), `config/variation.yaml` quyết định **bên trong** một cell: `generate_cell()` rút `name_variant`/`legacy_marker`/`number_style`/`number_prefix`/`admin_prefix`/`hierarchy_depth` theo tỷ lệ nào (hard rule #8 — không hardcode trọng số trong code). `register`/`length_class` không có mặt ở đây vì chúng đã là một phần của `variation_profile` trong `distribution.yaml`, quyết định ở tầng plan.

Mỗi axis có bảng `weights` mặc định, cộng override tuỳ chọn `by_register`/`by_category` (by_category thắng by_register thắng default — `Axis.resolve()`). Weight là số thô, `load_variation_config()` tự normalize từng bảng về tổng 1.0, không bắt YAML cộng đúng 1.0 sẵn.

`load_variation_config()` chạy validator ngay lúc load (giống `templates.load_templates()`), reject chứ không warn, gom **toàn bộ** issue trước khi raise `VariationError`:

| # | Check | Reject nếu |
|---|---|---|
| 1 | axis bắt buộc | thiếu 1 trong 6 axis (`name_variant`, `legacy_marker`, `number_style`, `number_prefix`, `admin_prefix`, `hierarchy_depth`), hoặc có axis lạ |
| 2 | enum phủ đủ | key của `name_variant`/`number_style`/`hierarchy_depth` không phủ **đúng** enum tương ứng ở `schema.py` |
| 3 | axis boolean | `number_prefix`/`admin_prefix` phải đúng key `{present, absent}` |
| 4 | weight hợp lệ | weight âm, không phải số, hoặc tổng ≤ 0 |
| 5 | key override | key `by_register` không parse được sang `Register`; key `by_category` không parse được sang `Category` |
| 6 | override phủ đủ | mỗi bảng override phải phủ đúng cùng tập giá trị với `weights` mặc định — override một phần là bug âm thầm |
| 7 | rate hợp lệ | `lexicon_rates` thiếu slot/register nào, hoặc giá trị ngoài `[0, 1]` |
| 8 | sàn hợp lệ | `axes.name_variant.min_legacy_share_per_province` ngoài `[0, 1]`, hoặc lớn hơn share `legacy` khả thi của `name_variant.by_category.admin_unit` sau normalize |
| 9 | schema_version | thiếu, hoặc khác phiên bản được hỗ trợ |

**Applicability là logic của code, không phải config.** Alias tỉnh (legacy/spoken) chỉ tồn tại trong `data/master/province_alias.csv` — street/POI không có alias. Vì vậy khi entity không phải province và `hierarchy_depth=poi_only`, `generate_cell()` (phase 6) phải ép `name_variant=current` bất kể weight nói gì; tương tự `number_style` bị ép về `none` khi entity không có số nhà, và `number_prefix` chỉ có ý nghĩa khi `number_style != none`. Config chỉ giữ **số**, generator giữ **quy tắc**.

Hệ quả trực tiếp: để đạt PASS 63/63 legacy alias coverage ([docs/output.md](output.md#statistics-report)), câu dùng tên tỉnh cũ bắt buộc phải là câu có nhắc tên tỉnh — `category=admin_unit` với entity là province, hoặc `hierarchy_depth=full_address`. Đó là lý do `name_variant.by_category.admin_unit` đẩy `legacy` lên cao hơn default, và vì sao `min_legacy_share_per_province` tồn tại — sàn để `qc/balance.find_gaps` (phase 11) phát hiện tỉnh nào chưa từng được sinh câu bằng tên cũ.

`lexicon_rates` (`opener`/`closer`/`filler`, mỗi giá trị trong `[0, 1]` theo register) là xác suất slot đó **không rỗng**. Khi "present", phase 6 chọn đều trong các giá trị khác rỗng của `data/templates/lexicon.yaml`; `""` vẫn tồn tại trong lexicon như một giá trị dữ liệu hợp lệ nhưng generator không chọn ngẫu nhiên trực tiếp trên list còn `""` — nếu làm vậy xác suất rỗng bị tính hai lần.

`pick_weighted(weights, rng)` chọn một key theo trọng số, deterministic (duyệt key đã sort, một lần `rng.random()` — hard rule #4). `format_variation_key(...)` sinh cột `variation` trong CSV cuối, vd `"legacy+cardinal+prefix+casual"` ([docs/output.md](output.md#csv-schema)).

## `numbers.py` — module rủi ro cao nhất

Vì mọi số phải verbalize đúng, đây là module cần test kỹ nhất trong toàn bộ codebase (xem [docs/testing.md](testing.md)). Các quy tắc tiếng Việt bắt buộc:

| Input | Output | Ghi chú |
|---|---|---|
| `10` | `mười` | không phải "một mươi" |
| `15` | `mười lăm` | "lăm" không phải "năm" khi hàng đơn vị=5 sau "mười" |
| `21` | `hai mươi mốt` | "mốt" không phải "một" |
| `24` | `hai mươi tư` (hoặc `hai mươi bốn`) | cả hai đều đúng — chọn theo config |
| `25` | `hai mươi lăm` | |
| `105` | `một trăm lẻ năm` | "lẻ" khi hàng chục = 0 |
| `12/3` | `mười hai xuyệt ba` (alias: `mười hai trên ba`) | ký hiệu phân số/địa chỉ kiểu ngõ |
| `15B` | `mười lăm bê` | số + chữ cái |
| `QL1A` | `quốc lộ một a` | viết tắt + số + chữ |

Bảng phát âm ký tự Latin cho nhà số alphanumeric: `A→a, B→bê, C→xê, D→dê, ...` (bảng chữ cái đọc kiểu Việt, không đọc kiểu tiếng Anh).

`expand_abbrev()` xử lý các viết tắt hành chính phổ biến: `TP→thành phố`, `Q.→quận`, `P.→phường`, `H.→huyện`, `BV→bệnh viện`, `TTTM→trung tâm thương mại`, `QL→quốc lộ`, `TL→tỉnh lộ`.

## Module/function bắt buộc

```
schema.py          Sample, Cell, LocationRef            (dataclass)
loaders.py          load_master() -> MasterData

aliases.py           resolve_name(entity, name_variant, master, rng) -> str
                      legacy_to_current(legacy_name, master) -> province_id

numbers.py           read_number(n, style) -> str
                      read_house_number(s, style) -> str
                      expand_abbrev(text) -> str
                      verbalize_entity_name(name, style) -> str   # phase 6: verbalize chữ số NHÚNG SẴN
                                                                    # trong tên entity (78% tên đường thật
                                                                    # có số, vd "Hẻm 553/18 Lũy Bán Bích")

sampling.py           entity_pool(master, province_id, category) -> tuple[LocationRef, ...]
                       build_plan(config, master) -> list[Cell]
                       validate_plan(plan, config, master, total) -> None   # raise PlanError nếu tổng ≠ total hoặc vi phạm cap
                       scale_plan(plan, total) -> list[Cell]   # scale tỷ trọng plan canonical sang total khác
                       resolve_profiles(config) -> dict[str, tuple[Register, LengthClass]]  # phase 6
                       plan_to_doc(...) / plan_from_doc(doc) -> list[Cell]   # phase 6, dùng chung `plan`/`generate`

templates.py           load_templates(dir) -> list[Template]
                        load_lexicon(dir) -> Lexicon          # phase 6
                        index_templates(templates, profiles, min_per_cell) -> dict[(sentence_type, profile), tuple[Template, ...]]  # phase 6
                        pick_template(cell, templates, rng) -> Template   # templates ĐÃ LỌC sẵn theo ô

variation.py            load_variation_config(path) -> VariationConfig
                        pick_weighted(weights, rng) -> str
                        format_variation_key(...) -> str
                        draw_axes(entity, cell, register, config, rng) -> AxisDraw       # phase 6
                        compose_entity(entity, cell, draw, master, lexicon, config, rng) -> EntityPhrase   # phase 6
                        apply_variations(slots, cell, config, rng, *, register, lexicon) -> dict[str, str]  # chỉ opener/closer/filler

generator.py             build_context(master_dir, template_dir, distribution_config, variation_config, global_seed) -> GenerationContext
                          generate_cell(cell, ctx, counters=None) -> list[Sample]
                          generate_all(plan, ctx, counters=None) -> Iterator[Sample]

qc/validators.py          HARD_VALIDATORS: list[Callable[[Sample], Issue | None]]
qc/dedup.py                exact_key(text) -> str
                            near_dup_clusters(samples) -> list[list[Sample]]
qc/balance.py               measure(samples) -> dict
                             find_gaps(measured, target) -> list[Cell]
                             select_final(candidates, n) -> list[Sample]

stats.py                     build_report(samples) -> dict     # + render markdown
cli.py                        build-master | plan | generate | qc | balance | finalize | stats
```

**Ghi chú phase 6 (cập nhật sau khi implement):**

- `compose_entity()` nhận thêm `cell` và `config` so với phác thảo ban đầu ở
  trên: `cell.category` là nguồn DUY NHẤT phân biệt được cell `address` với
  cell `street` (`sampling.entity_pool(..., ADDRESS)` trả về LocationRef có
  `category=STREET`, không phải `ADDRESS`), và `config.address_synthesis`
  (`config/variation.yaml`) cung cấp dải số nhà/tỷ lệ hậu tố để synthesize
  (hard rule #8 — không hardcode dải số trong code).
- `read_number(n, NumberStyle.MIXED)` trên một số nguyên đơn: `<=2` chữ số
  đọc cardinal, `>=3` chữ số đọc rời từng chữ số.
- `hierarchy_depth=poi_district` degrade tự động về `full_address` khi
  `entity.district_id` rỗng (100% street/POI trong master hiện tại) — đây
  là đường duy nhất để câu street/POI/address nhắc được tên tỉnh cũ.
- Tiền tố loại đường nhúng sẵn trong tên (`Hẻm`/`Ngõ`/`Ngách`/`Kiệt` —
  mang nghĩa, luôn giữ; `Đường`/`Phố` — trang trí, theo trục
  `admin_prefix`) được tách bằng `variation._split_street_prefix()`, và mọi
  token trùng 6 từ này ở BẤT KỲ vị trí nào trong text (kể cả lồng nhiều lớp
  như `"Ngõ 163 Đường Nguyễn Khang"`) được hạ chữ thường bằng
  `variation._lowercase_embedded_admin_words()`.
- `sampling.entity_pool()` lọc thêm entity có tên chứa chữ cái ngoài Latin
  cơ bản + dấu tiếng Việt (`sampling._is_speakable()`) — loại được 38 POI
  tên tiếng Nhật/Hàn trong master hiện tại, giữ nguyên thương hiệu Latin
  không dấu (BIDV, WinMart).

Mỗi module ở `src/navtext/` (cây đầy đủ trong [CLAUDE.md](../CLAUDE.md#cây-thư-mục)).
