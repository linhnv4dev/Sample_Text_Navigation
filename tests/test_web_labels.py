"""Test cho navtext.web.labels — mọi enum phải có nhãn tiếng Việt.

Bắt lỗi ngay khi phase sau thêm Category/Subcategory mới mà quên thêm
nhãn hiển thị tương ứng — thiếu sẽ KeyError ở render.py, nhưng test này
báo sớm hơn, không cần chạy hết web stack mới phát hiện.
"""

from __future__ import annotations

from navtext.schema import Category, Subcategory
from navtext.web.labels import CATEGORY_LABELS, GLOSSARY, SUBCATEGORY_LABELS


def test_every_category_has_a_vietnamese_label() -> None:
    for category in Category:
        assert category in CATEGORY_LABELS
        assert CATEGORY_LABELS[category]  # không rỗng


def test_every_subcategory_has_a_vietnamese_label() -> None:
    for subcategory in Subcategory:
        assert subcategory in SUBCATEGORY_LABELS
        assert SUBCATEGORY_LABELS[subcategory]


def test_glossary_entries_are_nonempty_pairs() -> None:
    assert len(GLOSSARY) > 0
    for term, explanation in GLOSSARY:
        assert term.strip()
        assert explanation.strip()
