"""Chart primitives cho `navtext serve` — hàm thuần, không side-effect,
không dependency ngoài stdlib.

Vì kiến trúc là server-side render stdlib-only (không CDN, không JS), đây
KHÔNG phải Chart.js như quy định slide-deck của ui-ux-pro-max:design-system
— quy định đó áp cho bài thuyết trình, không áp cho dashboard local chạy
offline. Đổi lại: zero dependency, không cần mạng, chart test được bằng
assert chuỗi thuần (xem tests/test_web_charts.py).

Bảng màu (biến CSS tham chiếu, không hex trần trong file này — xem
static/app.css) lấy từ dataviz skill references/palette.md, đã validate sẵn
(CVD ΔE, contrast) — không tự chế thêm màu ở đây.

Bất biến bắt buộc: MỌI nhãn hiển thị (label, tên entity/tỉnh) đi qua
`html.escape()` trước khi nhúng — kể cả bên trong <svg>, vì đó vẫn là ngữ
cảnh XML/HTML text, không phải ngữ cảnh an toàn riêng.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from html import escape
from typing import Sequence

from navtext.schema import Category

# Màu category cố định — tham chiếu biến CSS (định nghĩa ở static/app.css),
# gán theo đúng thứ tự khai báo của Category enum (schema.py). Cố định ở
# đây, không suy ra từ thứ tự dữ liệu: "color follows the entity, never its
# rank" (dataviz skill) — filter đổi tập category hiển thị không được đổi
# màu của category còn lại.
CATEGORY_COLOR_VAR: dict[Category, str] = {
    Category.ADMIN_UNIT: "var(--color-cat-admin_unit)",
    Category.STREET: "var(--color-cat-street)",
    Category.ADDRESS: "var(--color-cat-address)",
    Category.LANDMARK: "var(--color-cat-landmark)",
    Category.TRANSPORT: "var(--color-cat-transport)",
    Category.PUBLIC_FACILITY: "var(--color-cat-public_facility)",
    Category.COMMERCIAL: "var(--color-cat-commercial)",
    Category.POI_OTHER: "var(--color-cat-poi_other)",
}
_DEFAULT_COLOR_VAR = "var(--color-accent)"


def category_color_var(category_value: str) -> str:
    """Tra màu theo giá trị chuỗi của Category (vd "street"). Trả về màu
    accent mặc định nếu gặp giá trị lạ — không raise, vì đây là lớp trình
    bày, không phải validator dữ liệu."""
    try:
        category = Category(category_value)
    except ValueError:
        return _DEFAULT_COLOR_VAR
    return CATEGORY_COLOR_VAR.get(category, _DEFAULT_COLOR_VAR)


def kpi_card(label: str, value: str, sublabel: str, tone: str = "accent") -> str:
    """Một con số hero — Chart type #22 (KPI Card). `tone` chỉ nhận
    "accent"/"good"/"warning" (dùng làm giá trị `data-tone` cho CSS), không
    phải dữ liệu người dùng nên không cần escape riêng, nhưng vẫn escape để
    nhất quán và tránh vỡ markup nếu ai gọi sai."""
    return (
        f'<div class="kpi" data-tone="{escape(tone)}">'
        f'<div class="kpi-label">{escape(label)}</div>'
        f'<div class="kpi-value">{escape(value)}</div>'
        f'<div class="kpi-sublabel">{escape(sublabel)}</div>'
        "</div>"
    )


@dataclass(frozen=True, slots=True)
class BarRow:
    label: str
    value: int
    color_var: str = _DEFAULT_COLOR_VAR


def bar_list(rows: Sequence[BarRow], total: int | None = None) -> str:
    """Horizontal bar list — Chart #2. Chiều dài thanh tỉ lệ theo giá trị
    lớn nhất trong `rows` (so sánh các category với nhau); nhãn số hiển thị
    cả giá trị tuyệt đối lẫn tỷ lệ % trên `total` (mặc định = tổng `rows`).

    Không dùng legend riêng: mỗi thanh đã có nhãn trực tiếp bên cạnh (tên
    category), nên màu chỉ nhắc lại danh tính đã có ở text, không phải kênh
    nhận diện duy nhất — an toàn theo nguyên tắc "text không mang màu dữ
    liệu, nhãn luôn có mặt".
    """
    if not rows:
        return '<p class="muted">Không có dữ liệu.</p>'

    scale = max((r.value for r in rows), default=0) or 1
    denom = total if total else (sum(r.value for r in rows) or 1)

    parts: list[str] = []
    for row in rows:
        width_pct = round(row.value / scale * 100, 1)
        share_pct = round(row.value / denom * 100, 1)
        parts.append(
            '<div class="bar-row">'
            f'<div class="bar-row-label">{escape(row.label)}</div>'
            '<div class="bar-row-track">'
            f'<div class="bar-row-fill" style="width:{width_pct}%;background:{row.color_var}"></div>'
            "</div>"
            f'<div class="bar-row-value">{row.value:,} ({share_pct:g}%)</div>'
            "</div>"
        )
    return "".join(parts)


@dataclass(frozen=True, slots=True)
class DotCell:
    label: str
    filled: bool


def dot_grid(cells: Sequence[DotCell], columns: int = 10) -> str:
    """Icon array — Chart #25. Thay thế bức tường chữ liệt kê tên tỉnh
    bằng một lưới ô: sáng = có entity, xám = chưa có. Mỗi ô có `<title>`
    bên trong SVG để hiện tên khi hover (tooltip native của trình duyệt,
    không cần JS).

    Sáng/xám khác nhau cả về fill (không chỉ hue) nên vẫn phân biệt được
    dưới CVD hoặc grayscale print.
    """
    if not cells:
        return '<p class="muted">Không có dữ liệu.</p>'

    spacing = 18
    radius = 6
    rows = math.ceil(len(cells) / columns)
    width = columns * spacing
    height = rows * spacing

    circles: list[str] = []
    for i, cell in enumerate(cells):
        cx = (i % columns) * spacing + spacing / 2
        cy = (i // columns) * spacing + spacing / 2
        fill = "var(--color-accent)" if cell.filled else "var(--dot-empty)"
        circles.append(
            f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="{fill}">'
            f"<title>{escape(cell.label)}</title>"
            "</circle>"
        )

    filled_count = sum(1 for c in cells if c.filled)
    svg = (
        f'<svg class="dot-grid-svg" viewBox="0 0 {width} {height}" '
        f'width="100%" style="max-width:{width}px" role="img" '
        f'aria-label="Coverage lưới {len(cells)} tỉnh, {filled_count} có dữ liệu">'
        + "".join(circles)
        + "</svg>"
    )
    legend = (
        '<p class="muted">'
        f"● có dữ liệu ({filled_count}) &nbsp; ○ chưa có dữ liệu ({len(cells) - filled_count})"
        "</p>"
    )
    return svg + legend


def mini_bar(value: int, max_value: int) -> str:
    """Thanh nhỏ trong ô bảng — Chart #12 (sparkline inline), dùng hue
    sequential (không phải categorical: đây là magnitude thuần, không mang
    danh tính category)."""
    pct = 0 if max_value <= 0 else min(100, round(value / max_value * 100))
    return (
        '<div class="mini-bar-wrap">'
        f'<div class="mini-bar-track"><div class="mini-bar-fill" style="width:{pct}%"></div></div>'
        f'<span class="mini-bar-value">{value:,}</span>'
        "</div>"
    )


@dataclass(frozen=True, slots=True)
class MatrixCell:
    value: int
    label: str
    ok: bool = True


def heat_matrix(
    row_labels: Sequence[str],
    col_labels: Sequence[str],
    cells: Sequence[Sequence[MatrixCell]],
) -> str:
    """Lưới 2 chiều có giá trị trong ô — cho coverage matrix template
    (sentence_type × variation_profile).

    Khác `dot_grid()` (1 chiều, chỉ có/không): ở đây độ đậm mã hoá độ lớn,
    NHƯNG con số vẫn in thẳng trong ô — màu là kênh phụ, không phải kênh
    duy nhất mang thông tin. Ô dưới ngưỡng mang thêm `data-ok="false"` để
    CSS đánh dấu bằng viền, phân biệt được cả khi in grayscale.
    """
    if not row_labels or not col_labels:
        return '<p class="muted">Không có dữ liệu.</p>'

    max_value = max((c.value for row in cells for c in row), default=0) or 1

    header = "".join(f"<th>{escape(label)}</th>" for label in col_labels)
    body_rows: list[str] = []
    for label, row in zip(row_labels, cells):
        tds: list[str] = []
        for cell in row:
            intensity = round(cell.value / max_value, 3)
            tds.append(
                f'<td class="matrix-cell" data-ok="{"true" if cell.ok else "false"}" '
                f'style="--cell-intensity:{intensity}" title="{escape(cell.label)}">'
                f"{cell.value}</td>"
            )
        body_rows.append(f"<tr><th>{escape(label)}</th>{''.join(tds)}</tr>")

    return (
        '<div class="table-wrap"><table class="matrix">'
        f"<tr><th></th>{header}</tr>"
        f"{''.join(body_rows)}"
        "</table></div>"
    )


@dataclass(frozen=True, slots=True)
class Check:
    status: str  # "ok" | "warn" | "fail"
    title: str
    detail: str
    action: str = ""
    href: str = ""


_CHECK_ICON = {"ok": "✓", "warn": "!", "fail": "✗"}


def check_list(checks: Sequence[Check]) -> str:
    """Danh sách check readiness. Trạng thái mã hoá bằng CẢ ba kênh — ký
    hiệu, chữ, màu — nên không phụ thuộc màu để đọc được kết quả.
    """
    if not checks:
        return '<p class="muted">Không có check nào.</p>'

    items: list[str] = []
    for check in checks:
        icon = _CHECK_ICON.get(check.status, "?")
        action = (
            f'<p class="check-action">{escape(check.action)}</p>' if check.action else ""
        )
        link = (
            f'<a class="check-link" href="{escape(check.href)}">Xem chi tiết →</a>'
            if check.href
            else ""
        )
        items.append(
            f'<li class="check" data-status="{escape(check.status)}">'
            f'<span class="check-icon" aria-hidden="true">{icon}</span>'
            f'<div class="check-body">'
            f'<div class="check-title">{escape(check.title)}'
            f' <span class="badge badge-check-{escape(check.status)}">{escape(check.status.upper())}</span></div>'
            f'<p class="check-detail">{escape(check.detail)}</p>'
            f"{action}{link}"
            "</div></li>"
        )
    return f'<ul class="check-list">{"".join(items)}</ul>'


def info_grid(pairs: Sequence[tuple[str, object]]) -> str:
    """Cặp nhãn-giá trị dạng grid — thay `_dict_table` cũ (dùng <table> làm
    layout cho một khối thông tin, sai mục đích ngữ nghĩa của bảng)."""
    rows = "".join(
        f'<div class="info-row"><span class="info-label">{escape(str(k))}</span>'
        f'<span class="info-value">{escape(str(v))}</span></div>'
        for k, v in pairs
    )
    return f'<div class="info-grid">{rows}</div>'
