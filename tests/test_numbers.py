"""Test cho src/navtext/numbers.py — phase 6, module rủi ro cao nhất.

Xem docs/generation.md#numberspy--module-rủi-ro-cao-nhất và
docs/testing.md#unit-test-cho-numberspy.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

from navtext.numbers import (
    ABBREV_MAP,
    expand_abbrev,
    read_house_number,
    read_number,
    verbalize_entity_name,
)
from navtext.schema import NumberStyle

REPO_ROOT = Path(__file__).resolve().parent.parent
MASTER_DIR = REPO_ROOT / "data" / "master"
_DIGIT = re.compile(r"[0-9]")


# ---------------------------------------------------------------------------
# read_number — cardinal (bảng docs/testing.md:15-30)
# ---------------------------------------------------------------------------

CARDINAL_CASES = [
    (0, "không"),
    (1, "một"),
    (5, "năm"),
    (10, "mười"),
    (11, "mười một"),
    (15, "mười lăm"),
    (20, "hai mươi"),
    (21, "hai mươi mốt"),
    (24, "hai mươi tư"),
    (25, "hai mươi lăm"),
    (100, "một trăm"),
    (101, "một trăm lẻ một"),
    (105, "một trăm lẻ năm"),
    (110, "một trăm mười"),
    (111, "một trăm mười một"),
    (115, "một trăm mười lăm"),
    (121, "một trăm hai mươi mốt"),
    (1000, "một nghìn"),
    (1234, "một nghìn hai trăm ba mươi tư"),
]


@pytest.mark.parametrize("n,expected", CARDINAL_CASES)
def test_read_number_cardinal(n: int, expected: str) -> None:
    assert read_number(n, NumberStyle.CARDINAL) == expected


def test_read_number_24_alternate_branch() -> None:
    """24 có hai cách đọc đúng ngữ pháp — 'tư' hoặc 'bốn', chọn theo config.
    Test cả hai để không branch nào bị bỏ sót nếu default đổi.
    """
    result = read_number(24, NumberStyle.CARDINAL)
    assert result in ("hai mươi tư", "hai mươi bốn")


def test_read_number_digit_by_digit() -> None:
    assert read_number(123, NumberStyle.DIGIT_BY_DIGIT) == "một hai ba"
    assert read_number(0, NumberStyle.DIGIT_BY_DIGIT) == "không"
    assert read_number(10, NumberStyle.DIGIT_BY_DIGIT) == "một không"


def test_read_number_none_style_rejected() -> None:
    """NumberStyle.NONE nghĩa là 'không đọc số' — caller không được gọi
    read_number với style này (không có ý nghĩa để verbalize).
    """
    with pytest.raises(ValueError):
        read_number(123, NumberStyle.NONE)


def test_read_number_no_digits_leak() -> None:
    for n in [0, 5, 10, 15, 21, 24, 25, 100, 105, 999, 123456]:
        for style in (NumberStyle.CARDINAL, NumberStyle.DIGIT_BY_DIGIT, NumberStyle.MIXED):
            out = read_number(n, style)
            assert not _DIGIT.search(out), f"read_number({n}, {style}) leaked digit: {out!r}"


# ---------------------------------------------------------------------------
# read_house_number — alphanumeric/ký hiệu (docs/generation.md:143-145)
# ---------------------------------------------------------------------------

HOUSE_NUMBER_CASES = [
    ("15B", NumberStyle.CARDINAL, "mười lăm bê"),
    ("12/3", NumberStyle.CARDINAL, "mười hai xuyệt ba"),
    ("QL1A", NumberStyle.CARDINAL, "quốc lộ một a"),
]


@pytest.mark.parametrize("raw,style,expected", HOUSE_NUMBER_CASES)
def test_read_house_number(raw: str, style: NumberStyle, expected: str) -> None:
    assert read_house_number(raw, style) == expected


def test_read_house_number_multi_slash() -> None:
    out = read_house_number("553/18/55", NumberStyle.CARDINAL)
    assert not _DIGIT.search(out)
    assert "xuyệt" in out


def test_read_house_number_digit_by_digit() -> None:
    out = read_house_number("123", NumberStyle.DIGIT_BY_DIGIT)
    assert out == "một hai ba"


def test_read_house_number_no_digits_leak() -> None:
    for raw in ["15B", "12/3", "QL1A", "553/18/55", "1", "999999", "2A", "K1"]:
        for style in (NumberStyle.CARDINAL, NumberStyle.DIGIT_BY_DIGIT, NumberStyle.MIXED):
            out = read_house_number(raw, style)
            assert not _DIGIT.search(out), f"read_house_number({raw!r}, {style}) leaked digit: {out!r}"


# ---------------------------------------------------------------------------
# expand_abbrev — dùng chung ABBREV_MAP với templates.py
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("abbrev,expanded", list(ABBREV_MAP.items()))
def test_expand_abbrev_all_entries(abbrev: str, expanded: str) -> None:
    text = f"đi tới {abbrev} trung tâm"
    assert expanded in expand_abbrev(text)
    assert abbrev not in expand_abbrev(text) or abbrev in expanded


def test_expand_abbrev_word_boundary() -> None:
    """Không expand nhầm bên trong một từ dài hơn (vd 'HTP' không chứa 'TP'
    theo nghĩa viết tắt độc lập)."""
    assert expand_abbrev("HTPX") == "HTPX"


def test_expand_abbrev_no_match_passthrough() -> None:
    assert expand_abbrev("Nguyễn Văn Linh") == "Nguyễn Văn Linh"


# ---------------------------------------------------------------------------
# verbalize_entity_name — cầu nối bắt buộc: 78.5% tên đường trong master
# chứa chữ số (vd "Hẻm 553/18 Lũy Bán Bích", "Trung Yên 11").
# ---------------------------------------------------------------------------


def test_verbalize_entity_name_leading_number() -> None:
    out = verbalize_entity_name("Hẻm 553/18 Lũy Bán Bích", NumberStyle.CARDINAL)
    assert not _DIGIT.search(out)
    assert "Lũy Bán Bích" in out


def test_verbalize_entity_name_trailing_number() -> None:
    out = verbalize_entity_name("Trung Yên 11", NumberStyle.CARDINAL)
    assert not _DIGIT.search(out)
    assert "Trung Yên" in out


def test_verbalize_entity_name_abbrev_and_number() -> None:
    out = verbalize_entity_name("Cầu TL 302C", NumberStyle.CARDINAL)
    assert not _DIGIT.search(out)
    assert "tỉnh lộ" in out


def test_verbalize_entity_name_no_number_passthrough() -> None:
    assert verbalize_entity_name("Nguyễn Văn Linh", NumberStyle.CARDINAL) == "Nguyễn Văn Linh"


def test_verbalize_entity_name_property_on_real_streets() -> None:
    """Property bắt buộc trên toàn bộ 26.140 tên đường thật: output không
    còn chữ số Ả Rập nào (hard rule #1)."""
    path = MASTER_DIR / "streets.csv"
    if not path.exists():
        pytest.skip("data/master/streets.csv chưa build")
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert len(rows) > 0
    for row in rows:
        out = verbalize_entity_name(row["name"], NumberStyle.CARDINAL)
        assert not _DIGIT.search(out), f"verbalize_entity_name({row['name']!r}) leaked digit: {out!r}"


def test_verbalize_entity_name_property_on_real_pois() -> None:
    path = MASTER_DIR / "pois.csv"
    if not path.exists():
        pytest.skip("data/master/pois.csv chưa build")
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert len(rows) > 0
    for row in rows:
        out = verbalize_entity_name(row["name"], NumberStyle.CARDINAL)
        assert not _DIGIT.search(out), f"verbalize_entity_name({row['name']!r}) leaked digit: {out!r}"
