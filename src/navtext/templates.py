"""Load và chọn template. Implement ở phase 4/6. Xem docs/generation.md#template-bank.

Không có side-effect ở import time (CLAUDE.md#code-conventions) — mọi đọc
file chỉ xảy ra khi load_templates() được gọi tường minh.
"""

from __future__ import annotations

import enum
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml

from navtext.numbers import ABBREV_MAP
from navtext.schema import Cell, LengthClass, Register, SentenceType, Subcategory

# Slot hợp lệ duy nhất trong template bank — contract code, không phải
# ngưỡng tỷ trọng, nên là module constant chứ không sống trong config/
# (khác hard rule #8, cái đó chỉ áp cho số lượng/trọng số).
ALLOWED_SLOTS: frozenset[str] = frozenset(
    {"opener", "entity", "closer", "category_noun"}
)

_SLOT_PATTERN = re.compile(r"\{([a-zA-Z_]+)\}")
_DIGIT_PATTERN = re.compile(r"[0-9]")
_TAIL_TOKEN_PATTERN = re.compile(r"(\S+)\{closer\}$")
_HEAD_TOKEN_PATTERN = re.compile(r"^(?:\{opener\})?(\S+)")
# #10: lượng từ/so sánh nhất bám vào {entity} — {entity} luôn là một tên
# riêng xác định duy nhất, không phải một CLASS để định lượng/so sánh (xem
# docs/generation.md#template-bank, check #10).
_INDEFINITE_QUANTIFIER_AFTER_ENTITY = re.compile(r"\{entity\}\s+nào\b")
_QUANTIFIER_BEFORE_ENTITY = re.compile(r"\b(một|các|những|mấy|vài|cái)\s+\{entity\}")
_SUPERLATIVE_AFTER_ENTITY = re.compile(
    r"\{entity\}[^{}]{0,20}?\bgần(?:\s+đây)?\s+nhất\b"
)
# #11: danh từ loại đường (đường/lối/hướng/phố) đứng NGAY TRƯỚC {entity}
# không giới từ — đọc thành "con đường tên {entity}", đúng khi entity là
# street nhưng sai khi entity là POI/tỉnh/địa chỉ (fix "hỏi đường Tượng Mẹ
# Nhu" — Tượng Mẹ Nhu là tượng, không phải tên đường). Thêm giới từ
# (đến/tới/ra/về/vô...) giữa classifier và {entity} khử mơ hồ với MỌI loại
# entity — xem docs/generation.md#template-bank check #11.
_BARE_CLASSIFIER_BEFORE_ENTITY = re.compile(r"\b(đường|lối|hướng|phố)\s+\{entity\}")


class TailClass(enum.Enum):
    """Phân loại token đứng NGAY TRƯỚC {closer} trong template.text — quyết
    định apply_variations() được rút closer từ pool nào (docs/generation.md,
    xem CLAUDE.md hard rule #9: câu đã kết ở một tiểu từ/từ nghi vấn thì
    không được rút thêm closer bất kỳ, kẻo chồng tiểu từ — "…ở đâu không
    được không", "…không nha").

    Suy diễn tự động lúc load_templates() từ text, KHÔNG khai báo tay trong
    templates.yaml — 300 template hiện có không đổi, chỉ code hiểu thêm.
    """

    PLAIN = "plain"  # token trước {closer} là {entity} hoặc từ thường
    QUESTION_WORD = "question_word"  # "…ở đâu{closer}", "…đường nào{closer}"
    FINAL_PARTICLE = "final_particle"  # "…không{closer}", "…nha{closer}"
    TRAILING_CLAUSE = "trailing_clause"
    """Câu kết bằng một mệnh đề BÌNH LUẬN sau dấu phẩy, tách khỏi mệnh đề
    yêu cầu chứa {entity} — vd "...tới {entity} với, mình không rành đường
    lắm{closer}". Token ngay trước {closer} không phải từ nghi vấn/tiểu từ
    (nên PLAIN theo luật cũ) nhưng closer rút thêm sẽ dính vào SAU mệnh đề
    bình luận đã trọn vẹn, sai chỗ (fix "...mình không rành đường lắm đi",
    "...đói meo rồi được không" — closer không thuộc mệnh đề phụ này).
    apply_variations() không rút closer nào cho lớp này (giống pool rỗng),
    cùng tinh thần FINAL_PARTICLE/QUESTION_WORD."""


def _classify_tail(
    text: str, question_words: frozenset[str], sentence_final_particles: frozenset[str]
) -> TailClass:
    match = _TAIL_TOKEN_PATTERN.search(text)
    if match is None:
        return TailClass.PLAIN
    token = match.group(1)
    if token == "{entity}":
        return TailClass.PLAIN
    bare = token.casefold()
    if bare in question_words:
        return TailClass.QUESTION_WORD
    if bare in sentence_final_particles:
        return TailClass.FINAL_PARTICLE
    # TRAILING_CLAUSE: có dấu phẩy VÀ đoạn sau dấu phẩy CUỐI CÙNG không chứa
    # {entity} -> mệnh đề cuối là bình luận đứng tách khỏi entity, không
    # phải mệnh đề đang nói về entity (trường hợp đó vẫn PLAIN như cũ, closer
    # gắn tự nhiên — vd "mình đang ở đây, đi {entity} kiểu gì nhanh nhất").
    if "," in text:
        tail_segment = text.rsplit(",", 1)[1]
        if "{entity}" not in tail_segment:
            return TailClass.TRAILING_CLAUSE
    return TailClass.PLAIN


class HeadClass(enum.Enum):
    """Phân loại THỨC của câu suy diễn từ token nội dung ĐẦU TIÊN của
    template.text (bỏ qua {opener} nếu có) — đối xứng với TailClass, nhưng
    ở đầu câu thay vì cuối câu. Quyết định apply_variations() được rút
    filler mang nghĩa (vd "tự dưng", "kiểu") từ pool nào: filler mang nghĩa
    chỉ hợp với câu TRẦN THUẬT ("tự dưng muốn qua Hoàn Kiếm"), sai nghĩa nếu
    gắn vào câu MỆNH LỆNH ("tự dưng kiếm giúp tôi...") — xem lexicon.yaml.

    Suy diễn tự động lúc load_templates() từ text, KHÔNG khai báo tay trong
    templates.yaml — cùng cơ chế TailClass.
    """

    IMPERATIVE = "imperative"  # "chỉ đường đến…", "kiếm giúp tôi…"
    STATEMENT = "statement"  # "mình muốn qua…", "mình không rành đường…"
    QUESTION = "question"  # "từ đây đến … đi lối nào"


def _classify_head(
    text: str, imperative_verbs: frozenset[str], statement_markers: frozenset[str]
) -> HeadClass:
    match = _HEAD_TOKEN_PATTERN.match(text)
    if match is None:
        return HeadClass.IMPERATIVE
    bare = match.group(1).casefold()
    if bare in statement_markers:
        return HeadClass.STATEMENT
    if bare in imperative_verbs:
        return HeadClass.IMPERATIVE
    # Không khớp từ vựng nào — mặc định bảo thủ: coi là mệnh lệnh để CHẶN
    # filler mang nghĩa (statement-only), thà bỏ sót diversity còn hơn để
    # lọt câu sai nghĩa.
    return HeadClass.IMPERATIVE


_ASK_WORD = "hỏi"
_HEAD_ASK_WINDOW_WORDS = 5


def _template_starts_with_ask(text: str) -> bool:
    """True nếu vài từ đầu câu (sau khi bỏ `{opener}`) đã tự chứa "hỏi" —
    dùng để chặn KHÔNG rút thêm một opener CŨNG chứa "hỏi" (phần lớn opener
    formal/neutral trong lexicon.yaml là cụm xin phép: "xin cho hỏi", "cho
    tôi hỏi", "xin phép hỏi"...). Không chặn thì ghép ra lặp cụm hỏi kiểu
    "xin phép hỏi xin hỏi chỗ này..." — bug thật quan sát được ~2,5% sample
    (template tự mở đầu bằng "xin hỏi"/"cho hỏi xíu" ngay sau {opener})."""
    stripped = text.replace("{opener}", "").strip()
    words = stripped.split()[:_HEAD_ASK_WINDOW_WORDS]
    return any(w.strip(",.") == _ASK_WORD for w in words)


_HELP_WORD = "giúp"


def _template_mentions_help(text: str) -> bool:
    """True nếu template TỰ chứa "giúp" ở đâu đó trong text (không giới
    hạn vị trí đầu câu như _template_starts_with_ask — "giúp" thường nằm
    GIỮA câu, vd "tìm giúp mình đường tới {entity}") — dùng để chặn KHÔNG
    rút thêm closer CŨNG chứa "giúp" (lexicon.yaml#closers có "giúp tôi"/
    "giúp mình"/"giúp tôi với ạ"), tránh lặp kiểu "tìm giúp mình đường tới
    ... giúp mình"."""
    return _HELP_WORD in text


class TemplateError(Exception):
    """Raise với TOÀN BỘ danh sách violation cùng lúc — theo mẫu
    build_master.BuildError / sampling.PlanError, không fail-fast ở
    violation đầu tiên.
    """

    def __init__(self, issues: list[str]) -> None:
        self.issues = issues
        super().__init__("\n".join(f"- {i}" for i in issues))


@dataclass(frozen=True, slots=True)
class Template:
    template_id: str
    sentence_type: SentenceType
    register: Register
    length_class: LengthClass
    slots: tuple[str, ...]
    text: str
    tail_class: TailClass
    head_class: HeadClass
    self_asks: bool
    """True nếu vài từ đầu câu (sau {opener}) đã tự chứa "hỏi" — xem
    _template_starts_with_ask(). apply_variations() dùng field này để không
    rút thêm một opener CŨNG chứa "hỏi", tránh lặp cụm hỏi."""
    self_helps: bool
    """True nếu text tự chứa "giúp" ở đâu đó — xem _template_mentions_help().
    apply_variations() dùng field này để không rút thêm một closer CŨNG
    chứa "giúp", tránh lặp kiểu "...giúp mình...giúp mình"."""


@dataclass(frozen=True, slots=True)
class Filler:
    """Một giá trị filler kèm HeadClass nào được phép dùng nó — xem
    HeadClass + _classify_head. "à"/"ờ"/"thế" (heads đủ 3 giá trị) là thán
    từ trung tính; "tự dưng"/"kiểu"/"ấy" (heads chỉ STATEMENT) mang nghĩa,
    chỉ tự nhiên trước một mệnh đề trần thuật (lexicon.yaml).
    """

    text: str
    heads: frozenset[HeadClass]


@dataclass(frozen=True, slots=True)
class Opener:
    """Một giá trị opener kèm HeadClass nào được phép dùng nó — cùng cơ chế
    với Filler, đối xứng nhưng cho slot {opener} thay vì filler. Opener
    khung hỏi ("xin phép hỏi", "cho tôi hỏi"...) chỉ hợp lý trước mệnh đề
    trần thuật/nghi vấn (heads=[statement, question]) — đứng trước một câu
    CẦU KHIẾN tạo ra 2 khung câu chồng nhau ("xin phép hỏi xin chỉ đường
    đến..." — fix bug user báo "xin phép hỏi xin hướng dẫn đường đến...").
    Opener hô gọi trung tính ("này", "ê", "à", "ơ") dùng được cho mọi
    HeadClass (lexicon.yaml).
    """

    text: str
    heads: frozenset[HeadClass]


@dataclass(frozen=True, slots=True)
class Lexicon:
    """Từ vựng đã load từ data/templates/lexicon.yaml, sẵn sàng cho
    variation.apply_variations() — xem docs/generation.md#lexicon.

    openers/closers CHỈ giữ giá trị khác rỗng: "" là dữ liệu hợp lệ trong
    YAML (một lựa chọn có thật) nhưng xác suất rỗng đến từ
    VariationConfig.lexicon_rates, không phải từ việc chọn ngẫu nhiên trên
    list còn "" — giữ "" ở đây sẽ tính xác suất rỗng hai lần
    (lexicon.yaml:9-16). `openers` mỗi entry còn kèm `heads` (xem Opener) —
    formal register hiện KHÔNG có opener nào dùng được cho HeadClass.
    IMPERATIVE (mọi opener formal đều khung hỏi) — _opener_pool() trả pool
    rỗng cho tổ hợp đó, chấp nhận được: formal không có cách hô gọi trung
    tính nào trong lexicon hiện tại, còn hơn ép opener khung hỏi lên câu cầu
    khiến (xem Opener docstring).

    closers_after_question/closers_after_particle: pool closer hẹp hơn dùng
    khi template.tail_class khác PLAIN (xem TailClass) — cùng quy ước "chỉ
    giữ giá trị khác rỗng" như closers.
    """

    openers: Mapping[Register, tuple[Opener, ...]]
    closers: Mapping[Register, tuple[str, ...]]
    closers_after_question: Mapping[Register, tuple[str, ...]]
    closers_after_particle: Mapping[Register, tuple[str, ...]]
    fillers: tuple[Filler, ...]
    head_statement_markers: frozenset[str]
    head_imperative_verbs: frozenset[str]
    admin_prefixes: Mapping[
        str, tuple[str, ...]
    ]  # "province"/"district"/"ward"/"street", khác rỗng
    category_nouns: Mapping[Subcategory, tuple[str, ...]]
    question_words: frozenset[str]
    sentence_final_particles: frozenset[str]
    proper_noun_allowlist: frozenset[str]


def load_lexicon(templates_dir: Path) -> Lexicon:
    """Đọc data/templates/lexicon.yaml. Không side-effect ở import time —
    chỉ đọc khi gọi tường minh. load_templates() gọi lại hàm này cho
    proper_noun_allowlist thay vì tự đọc YAML lần hai (một nguồn sự thật).
    """
    lexicon_path = templates_dir / "lexicon.yaml"
    with lexicon_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    def _load_register_pool(key: str) -> dict[Register, tuple[str, ...]]:
        pool: dict[Register, tuple[str, ...]] = {}
        for reg_key, values in (raw.get(key) or {}).items():
            register = Register(reg_key)
            pool[register] = tuple(v for v in values if v)
        return pool

    def _load_opener_pool() -> dict[Register, tuple[Opener, ...]]:
        """openers có shape khác các pool lexicon còn lại — mỗi entry là
        {text, heads} (xem Opener), không phải chuỗi trần — nên cần loader
        riêng thay vì tái dùng _load_register_pool()."""
        pool: dict[Register, tuple[Opener, ...]] = {}
        for reg_key, entries in (raw.get("openers") or {}).items():
            register = Register(reg_key)
            openers_out: list[Opener] = []
            for entry in entries or []:
                text = entry["text"]
                if not text:
                    continue
                heads = frozenset(HeadClass(h) for h in entry["heads"])
                if not heads:
                    raise ValueError(
                        f"load_lexicon: opener {text!r} phải khai báo ít nhất 1 heads"
                    )
                openers_out.append(Opener(text=text, heads=heads))
            pool[register] = tuple(openers_out)
        return pool

    openers = _load_opener_pool()
    closers = _load_register_pool("closers")
    closers_after_question = _load_register_pool("closers_after_question")
    closers_after_particle = _load_register_pool("closers_after_particle")

    fillers_out: list[Filler] = []
    for entry in raw.get("fillers", []) or []:
        text = entry["text"]
        heads = frozenset(HeadClass(h) for h in entry["heads"])
        if not heads:
            raise ValueError(
                f"load_lexicon: filler {text!r} phải khai báo ít nhất 1 heads"
            )
        fillers_out.append(Filler(text=text, heads=heads))
    fillers = tuple(fillers_out)

    head_statement_markers = frozenset(raw.get("head_statement_markers", []) or [])
    head_imperative_verbs = frozenset(raw.get("head_imperative_verbs", []) or [])

    admin_prefixes: dict[str, tuple[str, ...]] = {}
    for key, values in (raw.get("admin_prefixes") or {}).items():
        admin_prefixes[key] = tuple(v for v in values if v)

    category_nouns: dict[Subcategory, tuple[str, ...]] = {}
    for key, values in (raw.get("category_nouns") or {}).items():
        category_nouns[Subcategory(key)] = tuple(v for v in values if v)

    question_words = frozenset(raw.get("question_words", []) or [])
    sentence_final_particles = frozenset(raw.get("sentence_final_particles", []) or [])
    allowlist = frozenset(raw.get("proper_noun_allowlist", []) or [])

    return Lexicon(
        openers=openers,
        closers=closers,
        closers_after_question=closers_after_question,
        closers_after_particle=closers_after_particle,
        fillers=fillers,
        head_statement_markers=head_statement_markers,
        head_imperative_verbs=head_imperative_verbs,
        admin_prefixes=admin_prefixes,
        category_nouns=category_nouns,
        question_words=question_words,
        sentence_final_particles=sentence_final_particles,
        proper_noun_allowlist=allowlist,
    )


# ---------------------------------------------------------------------------
# Per-entry validators — mỗi hàm trả về list[str] issue (rỗng nếu sạch).
# Đánh số theo bảng 9 check ở docs/generation.md#template-bank.
# ---------------------------------------------------------------------------


def _check_slots_match(
    entry_id: str, declared_slots: list[str], text: str
) -> list[str]:
    """#3 slot khớp: tập slot khai báo phải bằng tập {...} thực có trong text."""
    declared = set(declared_slots)
    found = set(_SLOT_PATTERN.findall(text))
    if declared != found:
        return [
            f"template {entry_id!r}: slot khai báo {sorted(declared)} không khớp "
            f"slot thực có trong text {sorted(found)}"
        ]
    return []


def _check_slots_allowed(entry_id: str, declared_slots: list[str]) -> list[str]:
    """#4 slot hợp lệ: mọi slot khai báo phải nằm trong ALLOWED_SLOTS."""
    invalid = [s for s in declared_slots if s not in ALLOWED_SLOTS]
    if invalid:
        return [
            f"template {entry_id!r}: slot không hợp lệ {invalid} — chỉ cho phép {sorted(ALLOWED_SLOTS)}"
        ]
    return []


def _check_has_entity(entry_id: str, text: str) -> list[str]:
    """#5 có entity: {entity} phải có mặt — mọi câu phải chứa location entity."""
    if "{entity}" not in text:
        return [
            f"template {entry_id!r}: thiếu slot {{entity}} — câu navigation phải chứa location entity"
        ]
    return []


def _check_no_digits(entry_id: str, text: str) -> list[str]:
    """#6 không chữ số (hard rule #1)."""
    if _DIGIT_PATTERN.search(text):
        return [
            f"template {entry_id!r}: text chứa chữ số Ả Rập — output phải là spoken form"
        ]
    return []


def _check_no_abbrev(entry_id: str, text: str) -> list[str]:
    """#7 không viết tắt — dùng chung ABBREV_MAP với numbers.expand_abbrev()
    (một nguồn sự thật, xem numbers.py) để template validator và QC
    validator không lệch danh sách theo thời gian.
    """
    issues = []
    for abbrev in ABBREV_MAP:
        pattern = re.compile(r"(?<!\w)" + re.escape(abbrev) + r"(?!\w)", re.UNICODE)
        if pattern.search(text):
            issues.append(
                f"template {entry_id!r}: text chứa viết tắt {abbrev!r} chưa expand"
            )
    return issues


def _check_no_hardcoded_proper_noun(
    entry_id: str, text: str, allowlist: frozenset[str]
) -> list[str]:
    """#8 không tên riêng hardcode (hard rule #6): token viết hoa, không
    phải token đầu câu, không nằm trong lexicon.proper_noun_allowlist ->
    bị coi là địa danh nhúng cứng.
    """
    tokens = text.split()
    issues = []
    for i, token in enumerate(tokens):
        literal = _SLOT_PATTERN.sub("", token)
        literal = literal.strip(",.!?;:\"'()")
        if not literal:
            continue
        if i == 0:
            continue
        if not literal[0].isupper():
            continue
        if literal in allowlist:
            continue
        issues.append(
            f"template {entry_id!r}: token {literal!r} viết hoa giữa câu, không nằm trong "
            "proper_noun_allowlist — nghi ngờ tên địa danh nhúng cứng (hard rule #6)"
        )
    return issues


def _check_spacing(entry_id: str, text: str) -> list[str]:
    """#9 spacing: double space, leading/trailing space, hoặc space liền kề
    {opener}/{closer}.
    """
    issues = []
    if text != text.strip():
        issues.append(f"template {entry_id!r}: text có leading/trailing whitespace")
    if "  " in text:
        issues.append(f"template {entry_id!r}: text có double space")
    if re.search(r"\s\{(opener|closer)\}", text) or re.search(
        r"\{(opener|closer)\}\s", text
    ):
        issues.append(
            f"template {entry_id!r}: có khoảng trắng liền kề {{opener}}/{{closer}} — vi phạm quy ước spacing"
        )
    return issues


def _check_no_common_noun_context(entry_id: str, text: str) -> list[str]:
    """#10 không đặt {entity} vào ngữ cảnh danh từ chung: {entity} luôn là
    một tên RIÊNG xác định duy nhất, không phải một CLASS để định lượng
    ("có {entity} nào", "một/các/những/cái {entity}") hay so sánh nhất
    ("{entity} ... gần nhất") — fix bug "gần đây có Chợ Miếu Bông nào
    không" (đọc thành "có bao nhiêu cái chợ tên Miếu Bông", sai nghĩa).

    Không reject "cái chỗ {entity} đó" (tham chiếu hồi chỉ, "cái" đứng
    trước "chỗ" chứ không ngay trước {entity}) — chỉ bắt "cái" NGAY TRƯỚC
    {entity}.
    """
    issues = []
    if _INDEFINITE_QUANTIFIER_AFTER_ENTITY.search(text):
        issues.append(
            f"template {entry_id!r}: '{{entity}} nào' — lượng từ bất định trên tên riêng, dùng {{category_noun}} nào thay vào"
        )
    if _QUANTIFIER_BEFORE_ENTITY.search(text):
        issues.append(
            f"template {entry_id!r}: lượng từ (một/các/những/mấy/vài/cái) đứng ngay trước {{entity}} — tên riêng không nhận lượng từ"
        )
    if _SUPERLATIVE_AFTER_ENTITY.search(text):
        issues.append(
            f"template {entry_id!r}: '{{entity}} ... gần nhất' — so sánh nhất giả định một tập, tên riêng chỉ định duy nhất một cái"
        )
    return issues


def _check_no_bare_classifier_before_entity(entry_id: str, text: str) -> list[str]:
    """#11 danh từ loại đường không đứng trần trước {entity} — xem
    _BARE_CLASSIFIER_BEFORE_ENTITY.
    """
    match = _BARE_CLASSIFIER_BEFORE_ENTITY.search(text)
    if match is None:
        return []
    return [
        f"template {entry_id!r}: {match.group(1)!r} đứng ngay trước {{entity}} không giới từ — "
        f"thêm giới từ (đến/tới/ra/về/vô...) giữa {match.group(1)!r} và {{entity}}, tránh đọc thành "
        f"'con {match.group(1)} tên {{entity}}'"
    ]


def _parse_enum(
    entry_id: str, field_name: str, value: str, enum_cls: type
) -> tuple[list[str], object | None]:
    """#2 enum hợp lệ."""
    try:
        return [], enum_cls(value)
    except ValueError:
        valid = [e.value for e in enum_cls]
        return [
            f"template {entry_id!r}: {field_name}={value!r} không hợp lệ — phải là một trong {valid}"
        ], None


def load_templates(templates_dir: Path) -> list[Template]:
    """Đọc data/templates/templates.yaml. Validator chạy ngay lúc load:
    reject template chứa tên riêng viết hoa ngoài slot (không chỉ warn) —
    xem quy tắc cứng ở docs/generation.md#template-bank.
    """
    templates_path = templates_dir / "templates.yaml"

    lexicon = load_lexicon(templates_dir)
    allowlist = lexicon.proper_noun_allowlist

    with templates_path.open(encoding="utf-8") as f:
        raw_entries = yaml.safe_load(f) or []

    issues: list[str] = []
    seen_ids: set[str] = set()
    templates: list[Template] = []

    for raw in raw_entries:
        entry_id = raw.get("template_id") or "<missing id>"

        # #1 id unique
        tid = raw.get("template_id")
        id_ok = True
        if not tid:
            issues.append("template có template_id rỗng")
            id_ok = False
        elif tid in seen_ids:
            issues.append(f"template_id {tid!r} bị trùng")
            id_ok = False
        else:
            seen_ids.add(tid)

        text = raw.get("text", "")
        declared_slots = list(raw.get("slots", []))

        entry_issues: list[str] = []
        entry_issues += _check_slots_allowed(entry_id, declared_slots)
        entry_issues += _check_slots_match(entry_id, declared_slots, text)
        entry_issues += _check_has_entity(entry_id, text)
        entry_issues += _check_no_digits(entry_id, text)
        entry_issues += _check_no_abbrev(entry_id, text)
        entry_issues += _check_no_hardcoded_proper_noun(entry_id, text, allowlist)
        entry_issues += _check_spacing(entry_id, text)
        entry_issues += _check_no_common_noun_context(entry_id, text)
        entry_issues += _check_no_bare_classifier_before_entity(entry_id, text)

        st_issues, sentence_type = _parse_enum(
            entry_id, "sentence_type", raw.get("sentence_type"), SentenceType
        )
        reg_issues, register = _parse_enum(
            entry_id, "register", raw.get("register"), Register
        )
        len_issues, length_class = _parse_enum(
            entry_id, "length_class", raw.get("length_class"), LengthClass
        )
        entry_issues += st_issues
        entry_issues += reg_issues
        entry_issues += len_issues

        issues.extend(entry_issues)

        if not entry_issues and id_ok:
            tail_class = _classify_tail(
                text, lexicon.question_words, lexicon.sentence_final_particles
            )
            head_class = _classify_head(
                text, lexicon.head_imperative_verbs, lexicon.head_statement_markers
            )
            self_asks = _template_starts_with_ask(text)
            self_helps = _template_mentions_help(text)
            templates.append(
                Template(
                    template_id=tid,
                    sentence_type=sentence_type,  # type: ignore[arg-type]
                    register=register,  # type: ignore[arg-type]
                    length_class=length_class,  # type: ignore[arg-type]
                    slots=tuple(declared_slots),
                    text=text,
                    tail_class=tail_class,
                    head_class=head_class,
                    self_asks=self_asks,
                    self_helps=self_helps,
                )
            )

    if issues:
        raise TemplateError(issues)

    return templates


def index_templates(
    templates: list[Template],
    profiles: Mapping[str, tuple[Register, LengthClass]],
    min_templates_per_cell: int,
) -> dict[tuple[SentenceType, str], tuple[Template, ...]]:
    """Index template bank theo (sentence_type, variation_profile) — build
    một lần lúc dựng GenerationContext, tránh generate_cell() phải scan lại
    toàn bộ 300 template mỗi cell.

    Raise TemplateError (gom toàn bộ issue, không fail-fast — theo mẫu
    load_templates()) nếu bất kỳ ô (sentence_type x variation_profile) nào
    có ít hơn min_templates_per_cell template — dưới ngưỡng này diversity
    quá nghèo và template dễ bị ép vượt caps.template.max_share
    (config/distribution.yaml#min_templates_per_cell).
    """
    index: dict[tuple[SentenceType, str], list[Template]] = {}
    for sentence_type in SentenceType:
        for profile_name, (register, length_class) in profiles.items():
            index[(sentence_type, profile_name)] = []

    for t in templates:
        for profile_name, (register, length_class) in profiles.items():
            if t.register == register and t.length_class == length_class:
                index[(t.sentence_type, profile_name)].append(t)

    issues: list[str] = []
    for key, matched in index.items():
        if len(matched) < min_templates_per_cell:
            sentence_type, profile_name = key
            issues.append(
                f"index_templates: ô ({sentence_type.value!r}, {profile_name!r}) chỉ có "
                f"{len(matched)} template, dưới min_templates_per_cell={min_templates_per_cell}"
            )
    if issues:
        raise TemplateError(issues)

    return {key: tuple(matched) for key, matched in index.items()}


def pick_template(
    cell: Cell, templates: list[Template], rng: random.Random
) -> Template:
    """Chọn một template phù hợp cell (sentence_type/register) bằng rng
    của cell — không dùng random module-level (hard rule #4).

    `templates` là danh sách ĐÃ LỌC sẵn cho đúng ô (sentence_type,
    variation_profile) của cell này (vd một giá trị của index_templates()).
    Chọn uniform trong list đó — diversity giữa các template được đảm bảo
    bằng ràng buộc cặp (template_id, entity_id) duy nhất trong cell ở
    generator.py, không phải bằng logic ở đây.
    """
    if not templates:
        raise ValueError(
            f"pick_template: không có template nào cho cell {cell.cell_key!r}"
        )
    candidates = [t for t in templates if t.sentence_type == cell.sentence_type]
    if not candidates:
        raise ValueError(
            f"pick_template: templates truyền vào không khớp sentence_type của cell {cell.cell_key!r}"
        )
    return candidates[rng.randrange(len(candidates))]
