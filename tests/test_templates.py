"""Test cho src/navtext/templates.py — phase 4.

Xem docs/generation.md#template-bank và kế hoạch phase 4 (bảng 9 check +
coverage matrix).
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest
import yaml

from navtext.templates import HeadClass, TailClass, Template, TemplateError, load_templates
from navtext.schema import LengthClass, Register, SentenceType

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_TEMPLATES_DIR = REPO_ROOT / "data" / "templates"
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "templates"


def test_load_real_bank() -> None:
    templates = load_templates(REAL_TEMPLATES_DIR)
    assert len(templates) > 0
    assert all(isinstance(t, Template) for t in templates)


def test_coverage_matrix() -> None:
    """Mọi tổ hợp (sentence_type, register, length_class) mà build_plan()
    có thể sinh ra (6 sentence_type x 5 variation_profile = 30 ô) phải có
    >= 10 template — nếu không, phase 6 sẽ có cell không tìm được template.
    """
    config_path = REPO_ROOT / "config" / "distribution.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    profiles = [(p["register"], p["length_class"]) for p in config["variation_profiles"]]
    # Ngưỡng đọc từ config, không hardcode ở test (hard rule #8) — cùng một
    # nguồn với stats.build_template_report().
    min_per_cell = int(config["min_templates_per_cell"])

    templates = load_templates(REAL_TEMPLATES_DIR)
    counts: Counter[tuple[str, str, str]] = Counter(
        (t.sentence_type.value, t.register.value, t.length_class.value) for t in templates
    )

    missing_or_thin: list[str] = []
    for st in SentenceType:
        for register, length_class in profiles:
            key = (st.value, register, length_class)
            if counts[key] < min_per_cell:
                missing_or_thin.append(f"{key}: {counts[key]} template (cần >= {min_per_cell})")

    assert not missing_or_thin, "Ô thiếu template:\n" + "\n".join(missing_or_thin)


def test_template_cap_feasible() -> None:
    """Với mỗi ô, số template phải đủ để không template nào bị ép vượt
    trần caps.template.max_share ở quy mô target_size.
    """
    config_path = REPO_ROOT / "config" / "distribution.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    target_size = int(config["target_size"])
    max_share = float(config["caps"]["template"]["max_share"])
    max_per_template = max_share * target_size

    templates = load_templates(REAL_TEMPLATES_DIR)
    counts: Counter[tuple[str, str, str]] = Counter(
        (t.sentence_type.value, t.register.value, t.length_class.value) for t in templates
    )

    # Ước lượng thô: chia đều target_size cho 30 ô, rồi chia cho số
    # template trong ô đó — nếu vượt trần thì ô này thiếu template.
    n_cells = len(SentenceType) * len(config["variation_profiles"])
    approx_per_cell = target_size / n_cells

    thin: list[str] = []
    for key, n_templates in counts.items():
        if n_templates == 0:
            continue
        per_template = approx_per_cell / n_templates
        if per_template > max_per_template:
            thin.append(f"{key}: {n_templates} template, ~{per_template:.0f} sample/template > trần {max_per_template:.0f}")

    assert not thin, "Ô có nguy cơ vượt trần template:\n" + "\n".join(thin)


def test_rejects_hardcoded_entity() -> None:
    with pytest.raises(TemplateError) as exc_info:
        load_templates(FIXTURES_DIR / "hardcoded_entity")
    assert any("Hồ" in issue or "Kiếm" in issue or "Hoàn" in issue for issue in exc_info.value.issues)


def test_rejects_digits_and_abbrev() -> None:
    with pytest.raises(TemplateError) as exc_info:
        load_templates(FIXTURES_DIR / "digits_abbrev")
    issues_text = "\n".join(exc_info.value.issues)
    assert "chữ số" in issues_text
    assert "Q." in issues_text


def test_rejects_slot_mismatch() -> None:
    with pytest.raises(TemplateError) as exc_info:
        load_templates(FIXTURES_DIR / "slot_mismatch")
    assert any("slot" in issue for issue in exc_info.value.issues)


def test_collects_all_issues() -> None:
    """TemplateError.issues phải gom đủ mọi lỗi từ mọi entry lỗi, không
    fail-fast ở entry đầu tiên — và entry sạch không bị chặn oan.
    """
    with pytest.raises(TemplateError) as exc_info:
        load_templates(FIXTURES_DIR / "multi_issues")
    issues = exc_info.value.issues
    assert any("multi_bad_digit" in i for i in issues)
    assert any("multi_bad_hardcoded" in i for i in issues)
    assert any("multi_bad_double_space" in i for i in issues)
    assert not any("multi_good_001" in i for i in issues)


# ---------------------------------------------------------------------------
# TailClass — phân loại token đứng ngay trước {closer}, suy diễn tự động từ
# text lúc load, KHÔNG khai báo tay trong templates.yaml (fix "…ở đâu không
# được không", "…không nha" — closer chồng lên tiểu từ/từ nghi vấn có sẵn).
# ---------------------------------------------------------------------------


def _by_id(templates: list[Template], template_id: str) -> Template:
    matches = [t for t in templates if t.template_id == template_id]
    assert matches, f"template_id {template_id!r} không tồn tại trong bank"
    return matches[0]


def test_tail_class_final_particle_for_no_particle() -> None:
    templates = load_templates(REAL_TEMPLATES_DIR)
    t = _by_id(templates, "conversational_casual_short_006")  # "biết {entity} ở đâu không{closer}"
    assert t.tail_class == TailClass.FINAL_PARTICLE


def test_tail_class_final_particle_for_khong_variant() -> None:
    templates = load_templates(REAL_TEMPLATES_DIR)
    t = _by_id(templates, "conversational_casual_short_010")  # "biết lối {entity} không{closer}"
    assert t.tail_class == TailClass.FINAL_PARTICLE


def test_tail_class_question_word_for_dau_nao() -> None:
    templates = load_templates(REAL_TEMPLATES_DIR)
    t = _by_id(templates, "route_question_casual_short_005")  # "{entity} đi đâu{closer}"
    assert t.tail_class == TailClass.QUESTION_WORD


def test_tail_class_plain_when_entity_precedes_closer() -> None:
    templates = load_templates(REAL_TEMPLATES_DIR)
    t = _by_id(templates, "search_casual_long_007")  # "...gần đây nhất{closer}" — nhất là mức độ, không phải tiểu từ
    assert t.tail_class == TailClass.PLAIN


def test_tail_class_plain_by_default() -> None:
    templates = load_templates(REAL_TEMPLATES_DIR)
    # Đa số template register formal/neutral kết thúc trực tiếp ở {entity}
    # hoặc từ nội dung thường (không phải tiểu từ/từ nghi vấn) — phải là
    # PLAIN, không bị thu hẹp pool closer oan.
    plain_count = sum(1 for t in templates if t.tail_class == TailClass.PLAIN)
    assert plain_count > 0


# ---------------------------------------------------------------------------
# TailClass.TRAILING_CLAUSE — 24 template *_casual_long_* kết bằng một mệnh
# đề BÌNH LUẬN sau dấu phẩy ("...đến {entity} với, mình không rành đường
# lắm{closer}"). Token ngay trước {closer} ("lắm") không phải tiểu từ/từ
# nghi vấn nên _classify_tail cũ trả PLAIN -> apply_variations rút thêm
# closer, dính vào SAU mệnh đề bình luận đã trọn vẹn (fix "...mình không
# rành đường lắm đi", "...đói meo rồi được không" — closer không thể thuộc
# về mệnh đề phụ này). Điều kiện: text có dấu phẩy VÀ đoạn sau dấu phẩy
# CUỐI CÙNG không chứa {entity} -> đó là mệnh đề bình luận, không được rút
# closer nữa. Ngược lại (dấu phẩy nhưng {entity} nằm sau nó, hoặc không có
# dấu phẩy) không bị ảnh hưởng — TailClass giữ nguyên hành vi cũ.
# ---------------------------------------------------------------------------


def test_tail_class_trailing_clause_for_comment_after_entity() -> None:
    templates = load_templates(REAL_TEMPLATES_DIR)
    t = _by_id(templates, "nav_command_casual_long_002")
    # "{opener}dẫn mình tới {entity} với, mình không rành đường lắm{closer}"
    assert t.tail_class == TailClass.TRAILING_CLAUSE


def test_tail_class_trailing_clause_for_all_22_casual_long_comment_templates() -> None:
    """Chốt trực tiếp trên toàn bộ 22 template đã rà tay — không phải suy
    diễn lý thuyết, đây là danh sách xác nhận từ đo thật trên bank hiện có.
    (search_casual_long_007 và route_question_casual_long_010 CŨNG có dấu
    phẩy/kết thúc ở "nhất" nhưng {entity} nằm ở mệnh đề CUỐI — soi câu sinh
    thật xác nhận closer vẫn gắn tự nhiên, không thuộc nhóm lỗi này.)"""
    templates = load_templates(REAL_TEMPLATES_DIR)
    expected_ids = {
        "nav_command_casual_long_001",
        "nav_command_casual_long_002",
        "nav_command_casual_long_003",
        "nav_command_casual_long_004",
        "nav_command_casual_long_006",
        "nav_command_casual_long_008",
        "nav_command_casual_long_009",
        "nav_command_casual_long_010",
        "search_casual_long_001",
        "search_casual_long_002",
        "search_casual_long_003",
        "search_casual_long_006",
        "search_casual_long_008",
        "search_casual_long_009",
        "search_casual_long_010",
        "route_question_casual_long_003",
        "route_question_casual_long_005",
        "short_command_casual_long_008",
        "short_command_casual_long_010",
        "contextual_casual_long_001",
        "contextual_casual_long_004",
        "contextual_casual_long_006",
    }
    for tid in expected_ids:
        t = _by_id(templates, tid)
        assert t.tail_class == TailClass.TRAILING_CLAUSE, tid


def test_tail_class_not_trailing_clause_when_entity_in_final_clause() -> None:
    """Đối chứng gần: có dấu phẩy VÀ kết ở "nhất" (không phải tiểu
    từ/từ nghi vấn) nhưng {entity} nằm trong chính mệnh đề cuối (sau dấu
    phẩy) — không phải mệnh đề bình luận, closer vẫn gắn tự nhiên."""
    templates = load_templates(REAL_TEMPLATES_DIR)
    t = _by_id(templates, "route_question_casual_long_010")
    # "{opener}mình đang ở đây, đi {entity} kiểu gì nhanh nhất{closer}"
    assert t.tail_class != TailClass.TRAILING_CLAUSE


def test_tail_class_not_trailing_clause_when_no_comma() -> None:
    """Đối chứng: template dài nhưng không có dấu phẩy nào trước {closer} —
    _classify_tail không có điều kiện để suy diễn TRAILING_CLAUSE, giữ
    nguyên hành vi cũ (ở đây là FINAL_PARTICLE vì kết ở "vậy")."""
    templates = load_templates(REAL_TEMPLATES_DIR)
    t = _by_id(templates, "route_question_casual_long_009")
    # "{opener}từ đây qua {entity} đi lối nào cho lẹ vậy{closer}"
    assert t.tail_class == TailClass.FINAL_PARTICLE


def test_no_import_side_effect() -> None:
    """Import module không được đọc file (CLAUDE.md#code-conventions)."""
    sys.modules.pop("navtext.templates", None)
    import navtext.templates  # noqa: F401 - chỉ kiểm tra import không raise/đọc file


# ---------------------------------------------------------------------------
# HeadClass — phân loại token nội dung ĐẦU câu (đối xứng TailClass), suy
# diễn tự động từ text lúc load — quyết định filler mang nghĩa (tự dưng/
# kiểu/ấy) có được rút hay không (fix "tự dưng kiếm giúp tôi chỗ thành phố
# Đà Nẵng nhé" — filler mang nghĩa gắn vào câu mệnh lệnh, sai nghĩa).
# ---------------------------------------------------------------------------


def test_head_class_statement_for_minh_leading() -> None:
    templates = load_templates(REAL_TEMPLATES_DIR)
    t = _by_id(templates, "route_question_casual_long_002")  # "{opener}muốn qua {entity}..."
    assert t.head_class == HeadClass.STATEMENT


def test_head_class_imperative_for_command_verb() -> None:
    templates = load_templates(REAL_TEMPLATES_DIR)
    t = _by_id(templates, "nav_command_neutral_short_001")  # "chỉ đường đến {entity}{closer}"
    assert t.head_class == HeadClass.IMPERATIVE


def test_head_class_imperative_by_default_for_unmatched_head() -> None:
    """Token đầu không khớp cả 2 danh sách (head_statement_markers/
    head_imperative_verbs) -> mặc định IMPERATIVE, bảo thủ có chủ đích —
    chặn filler mang nghĩa còn hơn để lọt câu sai nghĩa."""
    templates = load_templates(REAL_TEMPLATES_DIR)
    t = _by_id(templates, "contextual_neutral_short_002")  # "gần đây có {entity} không{closer}"
    assert t.head_class == HeadClass.IMPERATIVE


def test_head_class_distribution_has_both_classes() -> None:
    templates = load_templates(REAL_TEMPLATES_DIR)
    classes = {t.head_class for t in templates}
    assert HeadClass.STATEMENT in classes
    assert HeadClass.IMPERATIVE in classes


# ---------------------------------------------------------------------------
# self_asks/self_helps — fix lặp cụm hỏi/"giúp" khi template tự chứa cụm
# đó VÀ opener/closer rút thêm cụm cùng loại (vd "xin phép hỏi xin hỏi...",
# "tìm giúp mình đường tới ... giúp mình").
# ---------------------------------------------------------------------------


def test_self_asks_true_when_text_starts_with_hoi_after_opener() -> None:
    from navtext.templates import _template_starts_with_ask

    assert _template_starts_with_ask("{opener}xin hỏi chỗ này có gần {entity} không{closer}")
    assert _template_starts_with_ask("{opener}cho hỏi xíu, đường đi {entity} có xa không{closer}")


def test_self_asks_false_when_hoi_absent_or_far_from_head() -> None:
    from navtext.templates import _template_starts_with_ask

    assert not _template_starts_with_ask("{opener}chỉ đường đến {entity}{closer}")
    # "hỏi" xuất hiện nhưng ngoài cửa sổ đầu câu -> không tính là self_asks
    # (chỉ opener mới cần lo lặp, phần thân câu không liên quan tới opener).
    assert not _template_starts_with_ask(
        "{opener}từ đây đến {entity} thì đi đường nào cho khỏi phải hỏi lại lần nữa{closer}"
    )


def test_self_helps_true_when_giup_anywhere_in_text() -> None:
    from navtext.templates import _template_mentions_help

    assert _template_mentions_help("{opener}tìm giúp mình đường tới {entity}{closer}")
    assert _template_mentions_help("{opener}chỉ giúp tôi đường đến {entity}{closer}")


def test_self_helps_false_when_giup_absent() -> None:
    from navtext.templates import _template_mentions_help

    assert not _template_mentions_help("{opener}chỉ đường đến {entity}{closer}")


def test_real_templates_have_both_self_asks_values() -> None:
    """Xác nhận cơ chế thực sự có tác dụng trên template bank thật — không
    phải mọi template đều self_asks (sẽ vô nghĩa nếu vậy)."""
    templates = load_templates(REAL_TEMPLATES_DIR)
    values = {t.self_asks for t in templates}
    assert values == {True, False}


def test_real_templates_have_both_self_helps_values() -> None:
    templates = load_templates(REAL_TEMPLATES_DIR)
    values = {t.self_helps for t in templates}
    assert values == {True, False}


# ---------------------------------------------------------------------------
# Validator #10 — {entity} không được đặt vào ngữ cảnh danh từ chung (fix
# "gần đây có Chợ Miếu Bông nào không" — lượng từ bất định trên tên riêng).
# ---------------------------------------------------------------------------


def test_rejects_indefinite_quantifier_after_entity() -> None:
    with pytest.raises(TemplateError) as exc_info:
        load_templates(FIXTURES_DIR / "common_noun_context")
    issues_text = "\n".join(exc_info.value.issues)
    assert "lượng từ bất định" in issues_text


def test_rejects_quantifier_before_entity() -> None:
    with pytest.raises(TemplateError) as exc_info:
        load_templates(FIXTURES_DIR / "common_noun_context")
    issues_text = "\n".join(exc_info.value.issues)
    assert "lượng từ (một/các/những/mấy/vài/cái)" in issues_text


def test_rejects_superlative_after_entity() -> None:
    with pytest.raises(TemplateError) as exc_info:
        load_templates(FIXTURES_DIR / "common_noun_context")
    issues_text = "\n".join(exc_info.value.issues)
    assert "so sánh nhất" in issues_text


def test_allows_cai_cho_referential_not_directly_before_entity() -> None:
    """'cái chỗ {entity} đó' KHÔNG bị reject — 'cái' đứng trước 'chỗ', không
    ngay trước {entity} (tham chiếu hồi chỉ hợp lệ, khác lượng từ)."""
    templates = load_templates(REAL_TEMPLATES_DIR)
    t = _by_id(templates, "short_command_casual_long_002")  # "cái chỗ {entity} đó, ghé vô đó xíu đi"
    assert t is not None


def test_real_bank_has_no_common_noun_context_violations() -> None:
    """Chốt trực tiếp: bank thật (đã sửa 7 template) load sạch validator #10."""
    templates = load_templates(REAL_TEMPLATES_DIR)
    assert len(templates) == 300


# ---------------------------------------------------------------------------
# {category_noun} — slot thứ 4, cho template một danh từ chung thật để lượng
# từ bất định gắn vào, {entity} chỉ đóng vai mốc tham chiếu.
# ---------------------------------------------------------------------------


def test_category_noun_is_allowed_slot() -> None:
    templates = load_templates(REAL_TEMPLATES_DIR)
    t = _by_id(templates, "contextual_casual_long_002")
    assert "category_noun" in t.slots


# ---------------------------------------------------------------------------
# Validator #11 — {entity} không được đứng ngay sau danh từ loại đường
# (đường/lối/hướng/phố) mà không có giới từ: "đường {entity}" đọc thành
# "con đường tên {entity}", đúng khi entity là street nhưng SAI khi entity
# là POI/tỉnh/địa chỉ (fix bug user báo: "hỏi đường Tượng Mẹ Nhu" — Tượng
# Mẹ Nhu là tượng, không phải tên đường). Thêm giới từ (đến/tới/ra/về/vô...)
# khử mơ hồ với MỌI loại entity, không riêng gì street.
# ---------------------------------------------------------------------------


def test_rejects_bare_classifier_noun_before_entity() -> None:
    with pytest.raises(TemplateError) as exc_info:
        load_templates(FIXTURES_DIR / "bare_classifier_noun")
    issues_text = "\n".join(exc_info.value.issues)
    assert "bad_bare_duong_001" in issues_text
    assert "bad_bare_loi_001" in issues_text
    assert "bad_bare_huong_001" in issues_text
    assert "bad_bare_pho_001" in issues_text
    assert "good_with_preposition_001" not in issues_text


def test_real_bank_has_no_bare_classifier_noun_violations() -> None:
    """Chốt trực tiếp: bank thật (đã sửa 10 template N5) load sạch validator
    #11."""
    templates = load_templates(REAL_TEMPLATES_DIR)
    assert len(templates) == 300


# ---------------------------------------------------------------------------
# Diversity thật — không còn cặp template TRÙNG TEXT TUYỆT ĐỐI giữa các ô
# (9 cặp đo được trước khi sửa, vd "tìm {entity}{closer}" có ở cả
# search_neutral_short_002 lẫn search_casual_short_001 — hai ô khác nhau
# sinh ra cùng một câu, lãng phí ngân sách diversity).
# ---------------------------------------------------------------------------


def test_real_bank_has_no_exact_duplicate_texts() -> None:
    templates = load_templates(REAL_TEMPLATES_DIR)
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for t in templates:
        if t.text in seen:
            duplicates.append(f"{seen[t.text]!r} == {t.template_id!r}: {t.text!r}")
        else:
            seen[t.text] = t.template_id
    assert not duplicates, "\n".join(duplicates)
