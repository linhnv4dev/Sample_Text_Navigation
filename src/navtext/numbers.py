"""Đọc số tiếng Việt dạng nói. Module rủi ro cao nhất trong codebase — xem
docs/generation.md#numberspy--module-rủi-ro-cao-nhất và docs/testing.md.

Không có side-effect ở import time (CLAUDE.md#code-conventions) — mọi hàm
ở đây thuần túy (pure function), không đọc file, không giữ state.
"""

from __future__ import annotations

import re

from navtext.schema import NumberStyle

# Viết tắt hành chính phổ biến -> dạng đọc đầy đủ (docs/generation.md
# #numberspy--module-rủi-ro-cao-nhất). Khai ở đây làm MỘT nguồn sự thật dùng
# chung giữa templates.py (validator check #7, reject viết tắt lọt vào
# template) và expand_abbrev() — tránh hai danh sách lệch nhau theo thời gian.
ABBREV_MAP: dict[str, str] = {
    "TP": "thành phố",
    "Q.": "quận",
    "P.": "phường",
    "H.": "huyện",
    "BV": "bệnh viện",
    "TTTM": "trung tâm thương mại",
    "QL": "quốc lộ",
    "TL": "tỉnh lộ",
}

_UNITS: tuple[str, ...] = (
    "không",
    "một",
    "hai",
    "ba",
    "bốn",
    "năm",
    "sáu",
    "bảy",
    "tám",
    "chín",
)

# Chỉ số nhóm 3 chữ số (từ thấp lên cao): nhóm 0 (hàng đơn vị) không có tên.
_SCALE_WORDS: tuple[str, ...] = ("", "nghìn", "triệu", "tỷ")

# Bảng đọc chữ cái Latin kiểu Việt (docs/generation.md:147) — không đọc
# kiểu tiếng Anh.
_LETTER_MAP: dict[str, str] = {
    "A": "a",
    "B": "bê",
    "C": "xê",
    "D": "dê",
    "E": "e",
    "F": "ép",
    "G": "gờ",
    "H": "hát",
    "I": "i",
    "J": "gi",
    "K": "ca",
    "L": "lờ",
    "M": "mờ",
    "N": "nờ",
    "O": "o",
    "P": "pê",
    "Q": "quy",
    "R": "rờ",
    "S": "sờ",
    "T": "tê",
    "U": "u",
    "V": "vê",
    "W": "vê kép",
    "X": "ích",
    "Y": "i cờ rét",
    "Z": "dét",
}

# Trong text tự do (tên entity), một token whitespace-delimited chứa ít
# nhất một chữ số được coi là "cụm số" cần verbalize nguyên khối.
_HAS_DIGIT = re.compile(r"\d")
_ALNUM_RUN = re.compile(r"\d+|[A-Za-z]+")

# Khớp abbrev dài nhất trước (vd "TTTM" trước "TP") để không cắt nhầm.
_ABBREV_KEYS_BY_LEN_DESC = sorted(ABBREV_MAP, key=len, reverse=True)
_ABBREV_PATTERNS = {
    abbrev: re.compile(r"(?<!\w)" + re.escape(abbrev) + r"(?!\w)", re.UNICODE)
    for abbrev in ABBREV_MAP
}


def _read_two_digits(n: int) -> str:
    """Đọc số 0..99 theo quy tắc tiếng Việt: mười (không "một mươi"), mốt
    (không "một" sau hàng chục >=2), tư (mặc định, xem read_number), lăm.
    """
    tens, units = divmod(n, 10)
    if tens == 0:
        return _UNITS[units]
    if tens == 1:
        if units == 0:
            return "mười"
        if units == 5:
            return "mười lăm"
        return f"mười {_UNITS[units]}"
    word = f"{_UNITS[tens]} mươi"
    if units == 0:
        return word
    if units == 1:
        return f"{word} mốt"
    if units == 4:
        return f"{word} tư"
    if units == 5:
        return f"{word} lăm"
    return f"{word} {_UNITS[units]}"


def _read_group(n: int, *, is_leading: bool) -> str:
    """Đọc một nhóm 3 chữ số (0..999). is_leading=True nghĩa là nhóm cao
    nhất trong toàn bộ số — quyết định có cần "không trăm" mở đầu hay
    không, và "lẻ" khi hàng chục=0 nhưng có hàng trăm phía trước.
    """
    hundreds, rem = divmod(n, 100)
    parts: list[str] = []
    if hundreds > 0:
        parts.append(f"{_UNITS[hundreds]} trăm")
    elif not is_leading:
        parts.append("không trăm")

    if rem == 0:
        pass
    elif rem < 10:
        if hundreds > 0 or not is_leading:
            parts.append(f"lẻ {_UNITS[rem]}")
        else:
            parts.append(_UNITS[rem])
    else:
        parts.append(_read_two_digits(rem))

    if not parts:
        parts.append(_UNITS[0])
    return " ".join(parts)


def _read_digit_by_digit(n: int) -> str:
    return " ".join(_UNITS[int(d)] for d in str(n))


def _read_cardinal(n: int) -> str:
    if n == 0:
        return _UNITS[0]

    groups: list[int] = []
    temp = n
    while temp > 0:
        groups.append(temp % 1000)
        temp //= 1000
    num_groups = len(groups)

    parts: list[str] = []
    for i in reversed(range(num_groups)):
        g = groups[i]
        is_leading = i == num_groups - 1
        # Nhóm giữa bằng 0 bị bỏ hẳn (kể cả tên bậc) — cách nói tự nhiên,
        # vd 1.000.234 -> "một triệu hai trăm ba mươi tư", không "...không
        # nghìn...". Nhóm cao nhất (topmost) trong `groups` không bao giờ
        # bằng 0 do cách vòng lặp trên dừng lại (xem test property).
        if g == 0 and not is_leading:
            continue
        words = _read_group(g, is_leading=is_leading)
        if i < len(_SCALE_WORDS):
            scale = _SCALE_WORDS[i]
        else:
            # Fallback an toàn cho số vượt "tỷ" (không kỳ vọng gặp trong
            # dữ liệu địa danh thật) — vẫn không để lọt chữ số.
            scale = " ".join([_SCALE_WORDS[-1]] * (i - len(_SCALE_WORDS) + 2))
        if scale:
            words = f"{words} {scale}"
        parts.append(words)
    return " ".join(parts)


def read_number(n: int, style: NumberStyle) -> str:
    """Đọc một số nguyên không âm theo style. Xử lý đúng: mười (không "một
    mươi"), lăm/mốt/tư ở hàng đơn vị, lẻ ở hàng chục=0 trong số có hàng
    trăm. NumberStyle.NONE nghĩa là "không đọc số" — gọi hàm này với style
    đó là lỗi của caller (raise ValueError), không phải trường hợp hợp lệ.
    """
    if style == NumberStyle.NONE:
        raise ValueError(
            "read_number: NumberStyle.NONE nghĩa là không đọc số — "
            "caller không được gọi read_number với style này"
        )
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        raise ValueError(f"read_number: n phải là số nguyên không âm, nhận {n!r}")

    if style == NumberStyle.DIGIT_BY_DIGIT:
        return _read_digit_by_digit(n)
    if style == NumberStyle.MIXED:
        # Định nghĩa cho một số nguyên đơn (không có cấu trúc nhóm như số
        # nhà dạng "12/3"): số <=2 chữ số đọc cardinal (tự nhiên hơn), số
        # dài hơn đọc rời từng chữ số — xem docs/generation.md#numberspy.
        return _read_cardinal(n) if n < 100 else _read_digit_by_digit(n)
    return _read_cardinal(n)


def _read_alnum_run(part: str, style: NumberStyle) -> str:
    """Đọc một cụm alnum liền khối (không chứa '/'), tách thành các run
    chữ số/chữ cái liên tiếp theo đúng thứ tự xuất hiện.
    """
    words: list[str] = []
    for tok in _ALNUM_RUN.findall(part):
        if tok.isdigit():
            n = int(tok)
            eff_style = style
            if style == NumberStyle.MIXED:
                eff_style = NumberStyle.CARDINAL if len(tok) <= 2 else NumberStyle.DIGIT_BY_DIGIT
            words.append(read_number(n, eff_style))
        else:
            for ch in tok.upper():
                words.append(_LETTER_MAP.get(ch, ch.lower()))
    return " ".join(words)


def read_house_number(s: str, style: NumberStyle) -> str:
    """Đọc số nhà dạng alphanumeric/ký hiệu: "15B", "12/3", "QL1A". Nhiều
    cụm nối bằng "/" (ngõ/hẻm kiểu "553/18/55") đọc rời từng cụm, nối bằng
    "xuyệt". Tiền tố viết tắt hành chính dính liền số (vd "QL1A") được mở
    rộng qua ABBREV_MAP trước khi đọc phần số.
    """
    if style == NumberStyle.NONE:
        raise ValueError(
            "read_house_number: NumberStyle.NONE nghĩa là không đọc số — "
            "caller không được gọi read_house_number với style này"
        )

    text = s
    prefix_words: list[str] = []
    for abbrev in _ABBREV_KEYS_BY_LEN_DESC:
        if text.startswith(abbrev):
            prefix_words.append(ABBREV_MAP[abbrev])
            text = text[len(abbrev) :]
            break

    parts = text.split("/")
    part_words = [_read_alnum_run(part, style) for part in parts]
    joined = " xuyệt ".join(w for w in part_words if w)

    tokens = prefix_words + ([joined] if joined else [])
    return " ".join(tokens).strip()


def expand_abbrev(text: str) -> str:
    """Mở rộng viết tắt hành chính phổ biến (ABBREV_MAP) thành dạng đọc đầy
    đủ. Chỉ khớp trọn token (word boundary) — không cắt nhầm bên trong một
    từ dài hơn (vd "HTPX" không chứa "TP" theo nghĩa viết tắt độc lập), y
    hệt quy tắc templates._check_no_abbrev dùng để reject template.
    """
    result = text
    for abbrev in _ABBREV_KEYS_BY_LEN_DESC:
        result = _ABBREV_PATTERNS[abbrev].sub(ABBREV_MAP[abbrev], result)
    return result


def verbalize_entity_name(name: str, style: NumberStyle) -> str:
    """Verbalize toàn bộ chữ số nhúng sẵn trong TÊN entity (không chỉ số
    nhà synthesize riêng) — bắt buộc vì phần lớn tên đường/POI trong master
    data mang sẵn chữ số (vd "Hẻm 553/18 Lũy Bán Bích", "Trung Yên 11").

    Hậu điều kiện: output không còn chứa `[0-9]` (hard rule #1) — test
    property này chạy trên toàn bộ tên thật trong data/master/.
    """
    if style == NumberStyle.NONE:
        raise ValueError(
            "verbalize_entity_name: NumberStyle.NONE nghĩa là không đọc số — "
            "caller không được gọi verbalize_entity_name với style này"
        )

    text = expand_abbrev(name)
    tokens = text.split(" ")
    out_tokens = [
        read_house_number(tok, style) if _HAS_DIGIT.search(tok) else tok for tok in tokens
    ]
    return " ".join(out_tokens)
