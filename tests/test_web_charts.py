"""Test cho navtext.web.charts — hàm thuần, không cần route()/socket."""

from __future__ import annotations

from navtext.web.charts import BarRow, DotCell, bar_list, dot_grid, kpi_card, mini_bar


def test_bar_list_width_proportional_to_value() -> None:
    rows = [BarRow("A", 100, "var(--x)"), BarRow("B", 50, "var(--x)")]
    html = bar_list(rows, total=150)
    assert "width:100.0%" in html  # hàng lớn nhất luôn 100%
    assert "width:50.0%" in html


def test_bar_list_empty_total_does_not_divide_by_zero() -> None:
    rows = [BarRow("A", 0, "var(--x)")]
    html = bar_list(rows, total=0)  # không được raise ZeroDivisionError
    assert "0 (0%)" in html


def test_bar_list_no_rows() -> None:
    assert "Không có dữ liệu" in bar_list([], total=0)


def test_bar_list_escapes_malicious_label() -> None:
    rows = [BarRow("<script>alert(1)</script>", 5, "var(--x)")]
    html = bar_list(rows, total=5)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_dot_grid_renders_exact_cell_count() -> None:
    cells = [DotCell(f"P{i}", filled=(i < 3)) for i in range(34)]
    html = dot_grid(cells)
    assert html.count("<circle") == 34
    assert 'fill="var(--color-accent)"' in html  # ô đã có dữ liệu
    assert 'fill="var(--dot-empty)"' in html  # ô chưa có dữ liệu


def test_dot_grid_escapes_malicious_label_in_svg_title() -> None:
    """<title> bên trong SVG vẫn là ngữ cảnh text/HTML — không phải vùng an
    toàn riêng, phải escape như mọi nơi khác."""
    cells = [DotCell("<script>alert(1)</script>", filled=True)]
    html = dot_grid(cells)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_dot_grid_no_cells() -> None:
    assert "Không có dữ liệu" in dot_grid([])


def test_kpi_card_escapes_all_fields() -> None:
    html = kpi_card("<b>label</b>", "<b>value</b>", "<b>sub</b>")
    assert "<b>" not in html
    assert "&lt;b&gt;" in html


def test_mini_bar_zero_max_does_not_divide_by_zero() -> None:
    html = mini_bar(5, 0)
    assert "width:0%" in html


def test_mini_bar_full_value_is_100_percent() -> None:
    html = mini_bar(10, 10)
    assert "width:100%" in html
