"""Render HTML server-side, không dependency ngoài stdlib, không JS.

Nguyên tắc bắt buộc: MỌI giá trị tới từ dữ liệu (tên OSM, alias, tham số
query string) đi qua `html.escape()` trước khi nhúng vào template. Tên POI
lấy từ OSM là dữ liệu người lạ soạn — có thể chứa `<`, `&`, hoặc chuỗi trông
giống thẻ script. Đây là phòng XSS thật, không phải phòng hờ.

Layout dùng token CSS ở static/app.css (route /static/app.css) và chart
primitive ở charts.py — xem đó cho quy tắc màu/mark. Module này chỉ ráp
HTML, không tự quyết định màu hay số liệu.
"""

from __future__ import annotations

from collections import Counter
from html import escape
from typing import Any, Mapping

from navtext.loaders import MasterData
from navtext.schema import Category, LengthClass, LocationRef, Register, SentenceType, Subcategory
# SAMPLES_PREVIEW_NOTE: ngoại lệ nhỏ so với các note khác (MASTER_COVERAGE_
# NOTE/VARIATION_WEIGHT_NOTE đi kèm report do chính navtext.stats tính) —
# summarize_samples() sống ở web/query.py (thao tác trên CSV row phẳng,
# không phải domain object của stats.py) nên note tĩnh này import thẳng
# thay vì đi vòng qua một report stats.py không tồn tại.
from navtext.stats import SAMPLES_PREVIEW_NOTE
from navtext.templates import Lexicon, Template
from navtext.web import charts
from navtext.web.labels import (
    ADMIN_STATUS_LABELS,
    ALIAS_TYPE_LABELS,
    CATEGORY_LABELS,
    CHECK_STATUS_LABELS,
    GLOSSARY,
    LENGTH_CLASS_LABELS,
    REGISTER_LABELS,
    SENTENCE_TYPE_LABELS,
    SUBCATEGORY_LABELS,
    TIER_LABELS,
    VARIATION_AXIS_LABELS,
    VARIATION_VALUE_LABELS,
)
from navtext.web.query import DEFAULT_PAGE_SIZE, EntityQuery, SampleQuery, TemplateQuery

_NAV = (
    ("/", "Dashboard"),
    ("/readiness", "Readiness"),
    ("/provinces", "Provinces"),
    ("/entities", "Entities"),
    ("/samples", "Samples"),
    ("/templates", "Templates"),
    ("/config", "Config"),
    ("/variation", "Variation"),
    ("/raw", "Raw"),
)


def _nav_html(current: str) -> str:
    parts = []
    for href, label in _NAV:
        active = current == href or (href != "/" and current.startswith(href))
        cls = ' class="active"' if active else ""
        parts.append(f'<a href="{href}"{cls}>{escape(label)}</a>')
    return "".join(parts)


def _layout(title: str, body: str, current: str = "/") -> str:
    return f"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} — NavText master data</title>
<link rel="stylesheet" href="/static/app.css">
</head>
<body>
<main>
<nav class="top-nav">{_nav_html(current)}</nav>
<h1>{escape(title)}</h1>
{body}
</main>
</body>
</html>"""


def footer_html(started_at: str, template_count: int) -> str:
    """Dòng chân trang: server khởi động lúc nào + nạp được bao nhiêu template.

    Lý do tồn tại: `AppContext` (và toàn bộ module Python) được nạp MỘT LẦN
    lúc `navtext serve` khởi động, nên một server chạy từ trước khi sửa code
    vẫn phục vụ code cũ và trông y hệt như UI bị hỏng. Có mốc thời gian ở
    đây thì đối chiếu một cái là biết ngay cần restart.
    """
    template_note = (
        f"{template_count} template" if template_count else "chưa nạp template bank"
    )
    return (
        '<footer class="page-footer">'
        f"server khởi động lúc {escape(started_at)} · {escape(template_note)} · "
        "sửa code hoặc data đều cần restart <code>navtext serve</code>"
        "</footer>"
    )


def with_footer(page_html: str, started_at: str, template_count: int) -> str:
    """Chèn `footer_html()` vào ngay trước `</main>`.

    Gọi ở `routes._html()` — chỗ duy nhất mọi response HTML đi qua — thay vì
    thêm tham số `started_at` vào cả 8 hàm `render_*`: giữ nguyên chữ ký lớp
    render (vẫn thuần, vẫn test độc lập được) cho một dòng chân trang.

    `started_at` rỗng thì trả nguyên page_html: đó là default của
    `AppContext`, và là cách các test dựng context tối giản không bị đổi
    output.
    """
    if not started_at:
        return page_html
    return page_html.replace(
        "</main>", f"{footer_html(started_at, template_count)}</main>", 1
    )


def render_not_found(message: str) -> str:
    return _layout("404", f'<div class="callout">{escape(message)}</div>')


def render_error(message: str) -> str:
    """Trang 500 — route() ném exception không lường trước (bug, không phải
    nguồn dữ liệu thiếu; xem render_missing_source() cho trường hợp đó).
    server.py bọc route() trong try/except và gọi hàm này thay vì để
    exception bay lên và cắt kết nối giữa chừng (response rỗng, khó chẩn
    đoán từ trình duyệt).
    """
    return _layout("Lỗi", f'<div class="callout">Đã xảy ra lỗi khi render trang này.<br>{escape(message)}</div>')


def render_missing_source(title: str, what: str, hint: str, current: str = "/") -> str:
    """Trang cho nguồn dữ liệu chưa nạp — callout hướng dẫn thay vì 500.

    `navtext serve` cố tình không fail cứng khi thiếu một nguồn (xem
    cli._cmd_serve): thiếu template bank không được ngăn người dùng xem
    master data.
    """
    return _layout(
        title,
        f'<div class="callout">Chưa nạp {escape(what)}.<br>{escape(hint)}</div>',
        current=current,
    )


def _category_label(value: str) -> str:
    try:
        return CATEGORY_LABELS[Category(value)]
    except ValueError:
        return value


def _subcategory_label(value: str) -> str:
    try:
        return SUBCATEGORY_LABELS[Subcategory(value)]
    except ValueError:
        return value


def _glossary_html() -> str:
    items = "".join(
        f"<dt>{escape(term)}</dt><dd>{escape(explanation)}</dd>"
        for term, explanation in GLOSSARY
    )
    return (
        '<details class="glossary"><summary>Thuật ngữ dùng trong trang này</summary>'
        f'<dl class="glossary-list">{items}</dl></details>'
    )


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


def render_dashboard(report: Mapping[str, Any]) -> str:
    totals = report["totals"]
    coverage = report["master_coverage"]
    by_province: list[dict[str, Any]] = report["by_province"]
    by_category: dict[str, int] = report["by_category"]

    provinces_with = coverage["provinces_with_entities"]
    provinces_total = coverage["province_count"]

    kpis = "".join(
        [
            charts.kpi_card(
                "Tổng entity",
                f"{totals['entities']:,}",
                "Street + POI trong master data",
            ),
            charts.kpi_card(
                "Tỉnh có dữ liệu",
                f"{provinces_with} / {provinces_total}",
                "Tỉnh có ít nhất 1 entity — xem lưới coverage bên dưới",
                tone="warning" if provinces_with < provinces_total else "good",
            ),
            charts.kpi_card(
                "Street",
                f"{totals['streets']:,}",
                "Tên đường lấy từ OSM",
            ),
            charts.kpi_card(
                "POI",
                f"{totals['pois']:,}",
                "Điểm quan tâm: trường học, hồ, sân bay...",
            ),
        ]
    )

    dot_cells = [
        charts.DotCell(
            label=f"{p['name_current']} ({p['entities']} entity)",
            filled=p["entities"] > 0,
        )
        for p in by_province
    ]
    coverage_block = f"""
<h2>Coverage 34 tỉnh</h2>
{charts.dot_grid(dot_cells)}
<div class="callout">{escape(coverage['note'])}<br>
<strong>Provinces:</strong> {coverage['province_count']}/{coverage['expected_province_count']} &nbsp;
<strong>Legacy alias (63):</strong> {coverage['legacy_alias_count']}/{coverage['expected_legacy_alias_count']}
</div>
"""

    street_count = by_category.get("street", 0)
    poi_rows = [
        charts.BarRow(
            label=_category_label(cat),
            value=count,
            color_var=charts.category_color_var(cat),
        )
        for cat, count in sorted(by_category.items(), key=lambda kv: -kv[1])
        if cat != "street"
    ]
    poi_total = sum(row.value for row in poi_rows)
    street_share = round(street_count / totals["entities"] * 100, 1) if totals["entities"] else 0.0

    category_block = f"""
<h2>Street</h2>
<p class="section-intro">{street_count:,} street — {street_share:g}% tổng entity. Chiếm áp đảo vì
thin slice hiện tại lấy toàn bộ tên đường OSM của 3 tỉnh mẫu; đây là hình dạng thật của dữ liệu,
không phải lỗi hiển thị.</p>
<h2>POI theo category</h2>
{charts.bar_list(poi_rows, total=poi_total)}
"""

    empty_subs = report["empty_subcategories"]
    empty_html = ""
    if empty_subs:
        badges = "".join(
            f'<span class="badge badge-empty">{escape(_subcategory_label(s))}</span> '
            for s in empty_subs
        )
        empty_html = (
            '<h2>Subcategory chưa có dữ liệu</h2>'
            f'<p class="section-intro">Đã khai trong taxonomy nhưng master data chưa có entity nào — '
            f"phase 4 viết template cho các subcategory này sẽ không có gì để fill slot.</p>{badges}"
        )

    without = coverage["provinces_without_entities"]
    next_steps_html = ""
    if without:
        pills = "".join(
            f'<a class="badge badge-empty" href="/provinces/{escape(p["province_id"])}">'
            f'{escape(p["name_current"])}</a> '
            for p in without
        )
        next_steps_html = f"""
<h2>Bước tiếp theo</h2>
<p class="section-intro">{len(without)} tỉnh chưa có street/POI nào. Chạy
<code>scripts/fetch_osm.py --province-id &lt;id&gt;</code> cho tỉnh cần bổ sung, rồi
<code>navtext build-master</code> lại:</p>
<p>{pills}</p>
"""

    body = f"""
<div class="grid grid-kpi">{kpis}</div>
{coverage_block}
{category_block}
{empty_html}
{next_steps_html}
<p class="muted"><a href="/api/report.json">Xem report đầy đủ dạng JSON</a></p>
{_glossary_html()}
"""
    return _layout("Dashboard", body, current="/")


# ---------------------------------------------------------------------------
# Provinces list
# ---------------------------------------------------------------------------

def _sort_link(sort_key: str, current_sort: str, label: str) -> str:
    marker = " ▾" if sort_key == current_sort else ""
    return f'<a href="/provinces?sort={escape(sort_key)}">{escape(label)}{marker}</a>'


def render_provinces(rows: list[dict[str, Any]], sort_key: str = "province_id") -> str:
    max_entities = max((r["entities"] for r in rows), default=0)

    table_rows = []
    for p in rows:
        alias_badges = "".join(
            f'<span class="badge badge-{escape(k)}">{k}: {v}</span> '
            for k, v in p["alias_counts"].items()
        ) or "—"
        is_empty = p["entities"] == 0
        table_rows.append(
            f'<tr data-empty="{"true" if is_empty else "false"}">'
            f'<td><a href="/provinces/{escape(p["province_id"])}">{escape(p["province_id"])}</a></td>'
            f'<td>{escape(p["name_current"])}</td>'
            f'<td><span class="badge badge-status-{escape(p["admin_status"])}">'
            f'{escape(ADMIN_STATUS_LABELS.get(p["admin_status"], p["admin_status"]))}</span></td>'
            f'<td>{escape(p["region"])}</td>'
            f'<td><span class="badge badge-tier-{p["popularity_tier"]}">'
            f'{escape(TIER_LABELS.get(p["popularity_tier"], str(p["popularity_tier"])))}</span></td>'
            f"<td>{alias_badges}</td>"
            f'<td class="num">{charts.mini_bar(p["entities"], max_entities)}</td>'
            "</tr>"
        )

    header_row = (
        f'<th>{_sort_link("province_id", sort_key, "Mã")}</th>'
        f'<th>{_sort_link("name", sort_key, "Tên")}</th>'
        "<th>Trạng thái</th><th>Vùng</th>"
        f'<th>{_sort_link("tier", sort_key, "Tier")}</th>'
        "<th>Alias</th>"
        f'<th class="num">{_sort_link("entities", sort_key, "Entity")}</th>'
    )
    table = f"""
<p class="section-intro">{len(rows)} tỉnh/thành. Nhấn tiêu đề cột để sắp xếp.</p>
<div class="table-wrap">
<table>
<tr>{header_row}</tr>
{"".join(table_rows)}
</table>
</div>
"""
    return _layout("Provinces", table, current="/provinces")


# ---------------------------------------------------------------------------
# Province detail
# ---------------------------------------------------------------------------


def render_province_detail(
    master: MasterData, report: Mapping[str, Any], province_id: str
) -> str:
    province = master.provinces[province_id]
    aliases = master.aliases_by_province.get(province_id, ())
    districts = [d for d in master.districts.values() if d.province_id == province_id]
    district_ids = {d.district_id for d in districts}
    wards = [w for w in master.wards.values() if w.district_id in district_ids]
    entities = master.entities_by_province.get(province_id, ())

    header = charts.info_grid(
        [
            ("Tên hiện hành", province.name_current),
            ("Trạng thái", ADMIN_STATUS_LABELS.get(province.admin_status, province.admin_status)),
            ("Vùng", province.region),
            ("Thành phố trực thuộc TW", "Có" if province.is_municipality else "Không"),
            ("Tier", TIER_LABELS.get(province.popularity_tier, str(province.popularity_tier))),
        ]
    )

    alias_by_type: dict[str, list[str]] = {}
    for a in aliases:
        alias_by_type.setdefault(a.alias_type, []).append(a.alias)
    alias_html = "".join(
        f'<p><span class="badge badge-{escape(atype)}">'
        f'{escape(ALIAS_TYPE_LABELS.get(atype, atype))}</span> '
        + ", ".join(escape(name) for name in sorted(names))
        + "</p>"
        for atype, names in sorted(alias_by_type.items())
    ) or '<p class="muted">Không có alias.</p>'

    cat_counts: Counter[str] = Counter(e.category.value for e in entities)
    cat_rows = [
        charts.BarRow(_category_label(cat), count, charts.category_color_var(cat))
        for cat, count in sorted(cat_counts.items(), key=lambda kv: -kv[1])
    ]
    category_block = charts.bar_list(cat_rows, total=len(entities)) if entities else (
        '<p class="muted">Chưa có entity nào ở tỉnh này.</p>'
    )

    district_rows = "".join(
        f"<tr><td>{escape(d.name)}</td><td>{escape(d.type)}</td>"
        f'<td><span class="badge badge-status-{escape(d.status)}">'
        f'{escape(ADMIN_STATUS_LABELS.get(d.status, d.status))}</span></td></tr>'
        for d in sorted(districts, key=lambda d: d.name)
    )
    ward_rows = "".join(
        f"<tr><td>{escape(w.name)}</td><td>{escape(w.type)}</td></tr>"
        for w in sorted(wards, key=lambda w: w.name)
    )
    entity_sample_rows = "".join(
        f"<tr><td>{escape(e.entity_id)}</td><td>{escape(e.name)}</td>"
        f'<td><span class="cat-dot cat-{escape(e.category.value)}"></span>{escape(_category_label(e.category.value))}</td>'
        f"<td>{escape(_subcategory_label(e.subcategory.value))}</td></tr>"
        for e in sorted(entities, key=lambda e: e.entity_id)[:50]
    )
    entity_note = (
        f'<p class="section-intro">{len(entities)} entity — xem 50 dòng đầu, hoặc '
        f'<a href="/entities?province={escape(province_id)}">browse toàn bộ</a>.</p>'
    )

    body = f"""
<div class="card">{header}</div>
<h2>Alias ({len(aliases)})</h2>
{alias_html}
<h2>Entity theo category</h2>
{category_block}
<h2>Districts ({len(districts)})</h2>
<div class="table-wrap"><table><tr><th>Tên</th><th>Loại</th><th>Trạng thái</th></tr>
{district_rows or '<tr><td colspan="3">—</td></tr>'}</table></div>
<h2>Wards ({len(wards)})</h2>
<div class="table-wrap"><table><tr><th>Tên</th><th>Loại</th></tr>
{ward_rows or '<tr><td colspan="2">—</td></tr>'}</table></div>
<h2>Entity mẫu</h2>
{entity_note}
<div class="table-wrap"><table><tr><th>ID</th><th>Tên</th><th>Category</th><th>Subcategory</th></tr>
{entity_sample_rows or '<tr><td colspan="4">—</td></tr>'}</table></div>
"""
    return _layout(f"Tỉnh {province.name_current}", body, current="/provinces")


# ---------------------------------------------------------------------------
# Entities browse
# ---------------------------------------------------------------------------


def _entity_row(e: LocationRef) -> str:
    return (
        "<tr>"
        f"<td>{escape(e.entity_id)}</td>"
        f"<td>{escape(e.name)}</td>"
        f"<td>{escape(e.province_id)}</td>"
        f'<td><span class="cat-dot cat-{escape(e.category.value)}"></span>{escape(_category_label(e.category.value))}</td>'
        f"<td>{escape(_subcategory_label(e.subcategory.value))}</td>"
        f'<td><span class="badge badge-tier-{e.popularity_tier}">'
        f'{escape(TIER_LABELS.get(e.popularity_tier, str(e.popularity_tier)))}</span></td>'
        f"<td>{escape(e.source.value)}</td>"
        "</tr>"
    )


def _query_string(query: EntityQuery, page: int) -> str:
    parts: list[str] = []
    if query.q:
        parts.append(f"q={escape(query.q)}")
    if query.province_id:
        parts.append(f"province={escape(query.province_id)}")
    if query.category:
        parts.append(f"category={escape(query.category)}")
    if query.subcategory:
        parts.append(f"subcategory={escape(query.subcategory)}")
    parts.append(f"page={page}")
    return "?" + "&".join(parts)


def _option(value: str, label: str, selected: str | None) -> str:
    is_selected = " selected" if value == (selected or "") else ""
    return f'<option value="{escape(value)}"{is_selected}>{escape(label)}</option>'


def render_entities(
    query: EntityQuery,
    page_items: list[LocationRef],
    total: int,
    report: Mapping[str, Any],
) -> str:
    page_size = query.page_size or DEFAULT_PAGE_SIZE
    total_pages = max((total + page_size - 1) // page_size, 1)

    rows = "".join(_entity_row(e) for e in page_items)
    table = f"""
<div class="table-wrap">
<table>
<tr><th>ID</th><th>Tên</th><th>Tỉnh</th><th>Category</th><th>Subcategory</th><th>Tier</th><th>Nguồn</th></tr>
{rows or '<tr><td colspan="7">Không có kết quả</td></tr>'}
</table>
</div>
"""

    pager_links = []
    if query.page > 1:
        pager_links.append(f'<a href="/entities{_query_string(query, query.page - 1)}">« Trang trước</a>')
    pager_links.append(f'<span class="page-info">Trang {query.page}/{total_pages} — {total:,} kết quả</span>')
    if query.page < total_pages:
        pager_links.append(f'<a href="/entities{_query_string(query, query.page + 1)}">Trang sau »</a>')

    category_options = "".join(
        _option(cat, f"{_category_label(cat)} ({count})", query.category)
        for cat, count in sorted(report["by_category"].items())
    )
    subcategory_options = "".join(
        _option(sub, f"{_subcategory_label(sub)} ({count})", query.subcategory)
        for sub, count in sorted(report["by_subcategory"].items())
    )
    province_options = "".join(
        _option(p["province_id"], f'{p["name_current"]} ({p["entities"]})', query.province_id)
        for p in report["by_province"]
    )

    form = f"""
<form class="filters" method="get" action="/entities">
  <label>Tìm theo tên
    <input type="text" name="q" placeholder="không dấu cũng được" value="{escape(query.q or '')}">
  </label>
  <label>Tỉnh
    <select name="province"><option value="">Tất cả</option>{province_options}</select>
  </label>
  <label>Category
    <select name="category"><option value="">Tất cả</option>{category_options}</select>
  </label>
  <label>Subcategory
    <select name="subcategory"><option value="">Tất cả</option>{subcategory_options}</select>
  </label>
  <button type="submit">Lọc</button>
</form>
"""

    body = f"""
{form}
{table}
<div class="pager">{''.join(pager_links)}</div>
"""
    return _layout("Entities", body, current="/entities")


# ---------------------------------------------------------------------------
# Samples — /samples (CSV đã generate, --samples outputs/<run>/*.csv)
# ---------------------------------------------------------------------------


def _sample_query_string(query: SampleQuery, page: int) -> str:
    parts: list[str] = []
    if query.q:
        parts.append(f"q={escape(query.q)}")
    if query.province:
        parts.append(f"province={escape(query.province)}")
    if query.category:
        parts.append(f"category={escape(query.category)}")
    if query.sentence_type:
        parts.append(f"sentence_type={escape(query.sentence_type)}")
    if query.register:
        parts.append(f"register={escape(query.register)}")
    if query.name_variant:
        parts.append(f"name_variant={escape(query.name_variant)}")
    parts.append(f"page={page}")
    return "?" + "&".join(parts)


def _sample_row_html(row: Mapping[str, str]) -> str:
    return (
        "<tr>"
        f'<td class="template-text">{escape(row.get("text", ""))}</td>'
        f"<td>{escape(_category_label(row.get('category', '')))}</td>"
        f"<td>{escape(row.get('province', ''))}</td>"
        f"<td>{escape(_sentence_type_label(row.get('sentence_type', '')))}</td>"
        f"<td>{escape(_register_label(row.get('register', '')))}</td>"
        f"<td>{escape(row.get('name_variant', ''))}</td>"
        f"<td><code>{escape(row.get('template_id', ''))}</code></td>"
        "</tr>"
    )


# Nhãn giai đoạn QC suy ra từ tên file — khớp _SAMPLE_FILE_PRIORITY ở
# cli._discover_samples(). Không có entry cho tên file lạ (vd người dùng tự
# đặt tên khác) — banner khi đó chỉ hiện đường dẫn, không đoán giai đoạn.
_SAMPLE_STAGE_LABELS: Mapping[str, str] = {
    "prototype.csv": "mẫu 1k đầu tiên (phase 7), CHƯA qua QC/dedup/balance",
    "candidates.csv": "candidate dư 120–150k (phase 10), CHƯA dedup/balance",
    "clean.csv": "đã qua QC + balance (phase 11), chưa cắt đúng 100k",
    "final_100k.csv": "bản cuối cùng, đúng 100.000 sample (phase 12)",
}


def _sample_stage_label(source: str | None) -> str | None:
    if source is None:
        return None
    filename = source.rsplit("/", 1)[-1]
    return _SAMPLE_STAGE_LABELS.get(filename)


def render_samples(
    query: SampleQuery,
    page_items: list[Mapping[str, str]],
    total: int,
    summary: Mapping[str, Any],
    source: str | None,
    samples_auto: bool = False,
) -> str:
    page_size = query.page_size or DEFAULT_PAGE_SIZE
    total_pages = max((total + page_size - 1) // page_size, 1)

    kpis = "".join(
        [
            charts.kpi_card("Tổng sample", f"{summary.get('total', 0):,}", source or "—"),
            charts.kpi_card(
                "Tỉnh có mặt",
                f"{summary.get('distinct_provinces', 0)} / 34",
                "trong file CSV đang xem",
            ),
            charts.kpi_card(
                "Nhắc tên tỉnh cũ",
                f"{summary.get('legacy_mentions', 0):,}",
                "sample có province_legacy khác rỗng",
            ),
            charts.kpi_card(
                "Template đã dùng", f"{summary.get('distinct_templates', 0):,}", "template_id khác nhau"
            ),
        ]
    )

    rows_html = "".join(_sample_row_html(r) for r in page_items)
    table = f"""
<div class="table-wrap">
<table>
<tr><th>Text</th><th>Category</th><th>Tỉnh</th><th>Sentence type</th><th>Register</th>
<th>Name variant</th><th>Template</th></tr>
{rows_html or '<tr><td colspan="7">Không có kết quả</td></tr>'}
</table>
</div>
"""

    pager_links = []
    if query.page > 1:
        pager_links.append(f'<a href="/samples{_sample_query_string(query, query.page - 1)}">« Trang trước</a>')
    pager_links.append(f'<span class="page-info">Trang {query.page}/{total_pages} — {total:,} kết quả</span>')
    if query.page < total_pages:
        pager_links.append(f'<a href="/samples{_sample_query_string(query, query.page + 1)}">Trang sau »</a>')

    category_options = "".join(
        _option(cat, f"{_category_label(cat)} ({count})", query.category)
        for cat, count in sorted(summary.get("by_category", {}).items())
        if cat
    )
    province_options = "".join(
        _option(p, f"{p} ({count})", query.province)
        for p, count in sorted(summary.get("by_province", {}).items())
        if p
    )
    sentence_type_options = "".join(
        _option(st, f"{st} ({count})", query.sentence_type)
        for st, count in sorted(summary.get("by_sentence_type", {}).items())
        if st
    )
    register_options = "".join(
        _option(reg, f"{_register_label(reg)} ({count})", query.register)
        for reg, count in sorted(summary.get("by_register", {}).items())
        if reg
    )
    name_variant_options = "".join(
        _option(nv, f"{nv} ({count})", query.name_variant)
        for nv, count in sorted(summary.get("by_name_variant", {}).items())
        if nv
    )

    form = f"""
<form class="filters" method="get" action="/samples">
  <label>Tìm trong text
    <input type="text" name="q" placeholder="không dấu cũng được" value="{escape(query.q or '')}">
  </label>
  <label>Tỉnh
    <select name="province"><option value="">Tất cả</option>{province_options}</select>
  </label>
  <label>Category
    <select name="category"><option value="">Tất cả</option>{category_options}</select>
  </label>
  <label>Sentence type
    <select name="sentence_type"><option value="">Tất cả</option>{sentence_type_options}</select>
  </label>
  <label>Register
    <select name="register"><option value="">Tất cả</option>{register_options}</select>
  </label>
  <label>Name variant
    <select name="name_variant"><option value="">Tất cả</option>{name_variant_options}</select>
  </label>
  <button type="submit">Lọc</button>
</form>
"""

    stage_label = _sample_stage_label(source)
    source_bits = []
    if samples_auto:
        source_bits.append("Tự động chọn (run mới nhất trong --output-dir)")
    if stage_label:
        source_bits.append(f"Giai đoạn: {stage_label}")
    source_banner = (
        f'<p class="section-intro"><code>{escape(source or "")}</code> — {" · ".join(source_bits)}. '
        "Xem file khác bằng <code>--samples &lt;path&gt;</code>, hoặc tắt tự động chọn bằng "
        "<code>--no-auto-samples</code>.</p>"
        if source_bits
        else ""
    )

    body = f"""
<p class="section-intro">{escape(SAMPLES_PREVIEW_NOTE)}</p>
{source_banner}
<div class="grid grid-kpi">{kpis}</div>
{form}
{table}
<div class="pager">{''.join(pager_links)}</div>
"""
    return _layout("Samples", body, current="/samples")


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def _template_query_string(query: TemplateQuery, page: int) -> str:
    parts: list[str] = []
    if query.q:
        parts.append(f"q={escape(query.q)}")
    if query.sentence_type:
        parts.append(f"sentence_type={escape(query.sentence_type)}")
    if query.register:
        parts.append(f"register={escape(query.register)}")
    if query.length_class:
        parts.append(f"length_class={escape(query.length_class)}")
    parts.append(f"page={page}")
    return "?" + "&".join(parts)


def _coverage_matrix_html(template_report: Mapping[str, Any]) -> str:
    profiles = template_report["profiles"]
    matrix = template_report["coverage_matrix"]
    min_per_cell = template_report["min_templates_per_cell"]

    col_labels = [p["name"] for p in profiles]
    row_labels = [
        SENTENCE_TYPE_LABELS[SentenceType(row["sentence_type"])] for row in matrix
    ]
    cells = [
        [
            charts.MatrixCell(
                value=c["count"],
                label=f"{c['sentence_type']} × {c['profile']}: {c['count']} template",
                ok=c["ok"],
            )
            for c in row["cells"]
        ]
        for row in matrix
    ]
    thin = template_report["thin_cells"]
    note = (
        f'<div class="callout">{len(thin)} ô có dưới {min_per_cell} template — '
        "phase 6 sẽ có cell không đủ template để chọn.</div>"
        if thin
        else f'<p class="muted">Cả {template_report["cell_count"]} ô đều có ≥ {min_per_cell} template.</p>'
    )
    return charts.heat_matrix(row_labels, col_labels, cells) + note


def render_templates(
    query: TemplateQuery,
    page_items: list[Template],
    total: int,
    template_report: Mapping[str, Any],
    lexicon: Lexicon | None = None,
) -> str:
    page_size = query.page_size or DEFAULT_PAGE_SIZE
    total_pages = max((total + page_size - 1) // page_size, 1)

    kpis = "".join(
        [
            charts.kpi_card(
                "Tổng template",
                f"{template_report['total']:,}",
                "Trong data/templates/templates.yaml",
            ),
            charts.kpi_card(
                "Ô đã phủ",
                f"{template_report['cell_count'] - len(template_report['thin_cells'])}"
                f" / {template_report['cell_count']}",
                f"Ô có ≥ {template_report['min_templates_per_cell']} template",
                tone="good" if not template_report["thin_cells"] else "warning",
            ),
            charts.kpi_card(
                "Trần mỗi template",
                f"{template_report['max_per_template']:,.0f}",
                "Sample tối đa một template được chiếm",
                tone="warning" if template_report["cap_risks"] else "accent",
            ),
        ]
    )

    rows = "".join(
        "<tr>"
        f"<td><code>{escape(t.template_id)}</code></td>"
        f"<td>{escape(SENTENCE_TYPE_LABELS[t.sentence_type])}</td>"
        f'<td><span class="badge badge-register-{escape(t.register.value)}">'
        f"{escape(REGISTER_LABELS[t.register])}</span></td>"
        f"<td>{escape(LENGTH_CLASS_LABELS[t.length_class])}</td>"
        f'<td class="template-text">{escape(t.text)}</td>'
        "</tr>"
        for t in page_items
    )
    table = f"""
<div class="table-wrap">
<table>
<tr><th>ID</th><th>Loại câu</th><th>Register</th><th>Độ dài</th><th>Text</th></tr>
{rows or '<tr><td colspan="5">Không có kết quả</td></tr>'}
</table>
</div>
"""

    pager_links = []
    if query.page > 1:
        pager_links.append(
            f'<a href="/templates{_template_query_string(query, query.page - 1)}">« Trang trước</a>'
        )
    pager_links.append(
        f'<span class="page-info">Trang {query.page}/{total_pages} — {total:,} kết quả</span>'
    )
    if query.page < total_pages:
        pager_links.append(
            f'<a href="/templates{_template_query_string(query, query.page + 1)}">Trang sau »</a>'
        )

    sentence_options = "".join(
        _option(st.value, f"{SENTENCE_TYPE_LABELS[st]} ({template_report['by_sentence_type'].get(st.value, 0)})", query.sentence_type)
        for st in SentenceType
    )
    register_options = "".join(
        _option(r.value, f"{REGISTER_LABELS[r]} ({template_report['by_register'].get(r.value, 0)})", query.register)
        for r in Register
    )
    length_options = "".join(
        _option(lc.value, f"{LENGTH_CLASS_LABELS[lc]} ({template_report['by_length_class'].get(lc.value, 0)})", query.length_class)
        for lc in LengthClass
    )

    form = f"""
<form class="filters" method="get" action="/templates">
  <label>Tìm trong text
    <input type="text" name="q" placeholder="không dấu cũng được" value="{escape(query.q or '')}">
  </label>
  <label>Loại câu
    <select name="sentence_type"><option value="">Tất cả</option>{sentence_options}</select>
  </label>
  <label>Register
    <select name="register"><option value="">Tất cả</option>{register_options}</select>
  </label>
  <label>Độ dài
    <select name="length_class"><option value="">Tất cả</option>{length_options}</select>
  </label>
  <button type="submit">Lọc</button>
</form>
"""

    length_rows = "".join(
        f"<tr><td>{escape(LENGTH_CLASS_LABELS[LengthClass(lc)])}</td>"
        f'<td class="num">{s["count"]}</td><td class="num">{s["min"]}</td>'
        f'<td class="num">{s["median"]}</td><td class="num">{s["max"]}</td></tr>'
        for lc, s in template_report["length_stats"].items()
    )
    length_block = f"""
<h2>Độ dài khung chữ</h2>
<p class="section-intro">Số ký tự của phần khung (đã bỏ mọi <code>{{slot}}</code>) — chưa tính
tên địa danh sẽ được fill vào. Dùng để đối chiếu với <code>qc_thresholds.length_bounds</code>
ở <code>config/run.yaml</code>.</p>
<div class="table-wrap"><table>
<tr><th>Độ dài</th><th class="num">Số template</th><th class="num">Min</th><th class="num">Median</th><th class="num">Max</th></tr>
{length_rows}
</table></div>
"""

    lexicon_block = ""
    if lexicon is not None:
        sections: list[tuple[str, Mapping[Register, tuple[str, ...] | tuple[Any, ...]]]] = [
            ("openers", {reg: tuple(o.text for o in openers) for reg, openers in lexicon.openers.items()}),
            ("closers", lexicon.closers),
            ("closers_after_question", lexicon.closers_after_question),
            ("closers_after_particle", lexicon.closers_after_particle),
        ]
        parts: list[str] = []
        for section_name, values in sections:
            rows_html = "".join(
                f"<tr><td>{escape(REGISTER_LABELS.get(reg, reg.value))}</td>"
                f'<td>{", ".join(escape(v) for v in vals) if vals else "—"}</td></tr>'
                for reg, vals in values.items()
            )
            parts.append(
                f"<h3>{escape(section_name)}</h3>"
                f'<div class="table-wrap"><table><tr><th>Register</th><th>Giá trị</th></tr>{rows_html}</table></div>'
            )
        lexicon_block = (
            "<h2>Lexicon</h2>"
            '<p class="section-intro">Filler/particle mà <code>variation.py</code> (phase 5/6) '
            "ghép vào slot <code>{opener}</code>/<code>{closer}</code> — chỉ liệt kê giá trị khác "
            'rỗng ("không dùng filler" cũng là một lựa chọn hợp lệ, nhưng xác suất của nó đến từ '
            "<code>lexicon_rates</code> ở <code>config/variation.yaml</code>, không từ danh sách này). "
            '<code>—</code> = register này không có giá trị nào.</p>' + "".join(parts)
        )

    body = f"""
<div class="grid grid-kpi">{kpis}</div>
<h2>Coverage theo ô (loại câu × variation profile)</h2>
{_coverage_matrix_html(template_report)}
<h2>Danh sách template</h2>
{form}
{table}
<div class="pager">{''.join(pager_links)}</div>
{length_block}
{lexicon_block}
"""
    return _layout("Templates", body, current="/templates")


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


def render_readiness(readiness: Mapping[str, Any]) -> str:
    counts = readiness["counts"]
    overall = readiness["overall"]

    kpis = "".join(
        [
            charts.kpi_card(
                "Trạng thái chung",
                CHECK_STATUS_LABELS.get(overall, overall).upper(),
                "fail = có việc chặn phase tiếp theo",
                tone={"ok": "good", "warn": "warning", "fail": "warning"}.get(overall, "accent"),
            ),
            charts.kpi_card("Đạt", str(counts["ok"]), "Check không có vấn đề", tone="good"),
            charts.kpi_card("Cảnh báo", str(counts["warn"]), "Chạy được nhưng nên xử lý", tone="warning"),
            charts.kpi_card("Chặn", str(counts["fail"]), "Phải sửa trước khi đi tiếp", tone="warning"),
        ]
    )

    check_objs = [
        charts.Check(
            status=c["status"],
            title=c["title"],
            detail=c["detail"],
            action=c["action"],
            href=c["href"],
        )
        for c in readiness["checks"]
    ]

    body = f"""
<div class="grid grid-kpi">{kpis}</div>
<p class="section-intro">Mỗi dòng là một điều kiện cần trước khi chạy phase tiếp theo của pipeline.
Đây là kiểm tra ở tầng <strong>dữ liệu đầu vào</strong> — không phải coverage PASS/FAIL của phase 12
(xem <a href="/">Dashboard</a>).</p>
{charts.check_list(check_objs)}
<p class="muted"><a href="/api/readiness.json">Xem dạng JSON</a></p>
"""
    return _layout("Readiness", body, current="/readiness")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _weight_bars(weights: Mapping[str, Any], color_by_category: bool = False) -> str:
    rows = [
        charts.BarRow(
            label=_category_label(k) if color_by_category else k,
            value=int(round(float(v) * 1000)),
            color_var=charts.category_color_var(k) if color_by_category else "var(--color-accent)",
        )
        for k, v in sorted(weights.items(), key=lambda kv: -float(kv[1]))
    ]
    return charts.bar_list(rows)


def render_config(config: Mapping[str, Any]) -> str:
    caps = config["caps"]

    top = charts.info_grid(
        [
            ("Target size", f"{int(config['target_size']):,}"),
            ("Oversample factor", config["oversample_factor"]),
            ("Min cell size", config["min_cell_size"]),
            ("Min template mỗi ô", config.get("min_templates_per_cell", "—")),
        ]
    )

    cap_rows = "".join(
        f"<tr><td>{escape(axis)}</td>"
        f'<td class="num">{values.get("min_share", "—")}</td>'
        f'<td class="num">{values.get("max_share", "—")}</td></tr>'
        for axis, values in caps.items()
    )

    profile_rows = "".join(
        f'<tr><td><code>{escape(p["name"])}</code></td>'
        f"<td>{escape(REGISTER_LABELS[Register(p['register'])])}</td>"
        f"<td>{escape(LENGTH_CLASS_LABELS[LengthClass(p['length_class'])])}</td>"
        f'<td class="num">{p["weight"]}</td></tr>'
        for p in config["variation_profiles"]
    )

    body = f"""
<p class="section-intro">Nội dung <code>config/distribution.yaml</code> — quyết định toàn bộ plan table
(tỷ trọng GIỮA các cell). Muốn xem tỷ trọng BÊN TRONG một cell (name_variant,
number_style, ...) thì xem <a href="/variation">/variation</a>
(<code>config/variation.yaml</code>). Trang này <strong>chỉ đọc</strong>: sửa file rồi restart
<code>navtext serve</code> để thấy thay đổi.</p>
<div class="card">{top}</div>

<h2>Trọng số category</h2>
{_weight_bars(config["category_weights"], color_by_category=True)}

<h2>Trọng số loại câu</h2>
{_weight_bars(config["sentence_type_weights"])}

<h2>Trần / sàn tỷ trọng</h2>
<div class="table-wrap"><table>
<tr><th>Trục</th><th class="num">Sàn</th><th class="num">Trần</th></tr>
{cap_rows}
</table></div>

<h2>Variation profiles</h2>
<div class="table-wrap"><table>
<tr><th>Tên</th><th>Register</th><th>Độ dài</th><th class="num">Trọng số</th></tr>
{profile_rows}
</table></div>

<h2>Trọng số tỉnh theo tier</h2>
{charts.info_grid([
    (f"Tier {tier}", weight) for tier, weight in sorted(config["province_weights"]["by_tier"].items())
] + [("Municipality bonus", config["province_weights"]["municipality_bonus"])])}

{(
    '<h3>Override theo tỉnh</h3>'
    '<p class="muted">Hệ số tinh chỉnh nhân THÊM vào weight tier ở trên, cho tỉnh cụ thể '
    '(không áp vào trọng số entity ở dưới). Xem comment '
    '<code>province_weights.overrides</code> trong <code>config/distribution.yaml</code>.</p>'
    + charts.info_grid([
        (f"Override {province_id}", weight)
        for province_id, weight in sorted((config["province_weights"].get("overrides") or {}).items())
    ])
) if config["province_weights"].get("overrides") else ""}

<h2>Trọng số entity theo tier</h2>
<p class="muted">Trọng số chọn ENTITY CỤ THỂ bên trong một cell (generator.py, phase 6) —
khác trọng số tỉnh ở trên (chọn tỉnh nào cho cell, ở tầng plan).</p>
{charts.info_grid([
    (f"Tier {tier}", weight)
    for tier, weight in sorted(config.get("entity_weights", {}).get("by_tier", {}).items())
]) if config.get("entity_weights", {}).get("by_tier") else '<p class="muted">Chưa có entity_weights trong config này.</p>'}
"""
    return _layout("Config", body, current="/config")


# ---------------------------------------------------------------------------
# Variation (phase 5 — config/variation.yaml)
# ---------------------------------------------------------------------------

# report["note"] (stats.VARIATION_WEIGHT_NOTE) mang đúng nội dung cảnh báo —
# cùng tinh thần MASTER_COVERAGE_NOTE: một nguồn sự thật duy nhất cho câu
# cảnh báo, escape khi render thay vì chép lại text ở đây.

_VARIATION_WARNING_TITLES: Mapping[str, str] = {
    "dead_value": "Giá trị chết (weight 0)",
    "empty_lexicon_pool": "Lexicon rỗng cho slot đang bật",
    "space_below_candidates": "Không gian nhỏ hơn candidate count",
}

_VARIATION_APPLICABILITY_HTML = """
<div class="callout">
<strong>Applicability là logic của generator, không phải config.</strong>
Alias tỉnh (legacy/spoken) chỉ tồn tại cho <code>province</code> — street/POI không có alias, nên khi
entity không phải province và <code>hierarchy_depth=poi_only</code>, generator ép
<code>name_variant=current</code> bất kể weight ở đây nói gì. Tương tự <code>number_style</code> bị ép về
<code>none</code> khi entity không có số nhà, và <code>number_prefix</code> chỉ có nghĩa khi
<code>number_style != none</code>. Để đạt coverage 63/63 legacy alias, câu dùng tên tỉnh cũ bắt buộc phải
là câu có nhắc tên tỉnh — <code>category=admin_unit</code> hoặc <code>hierarchy_depth=full_address</code>.
</div>
"""


def _variation_value_label(value: str) -> str:
    return VARIATION_VALUE_LABELS.get(value, value)


def _variation_weight_bars(weights: Mapping[str, float]) -> str:
    rows = [
        charts.BarRow(label=_variation_value_label(k), value=int(round(float(v) * 1000)))
        for k, v in sorted(weights.items(), key=lambda kv: -float(kv[1]))
    ]
    return charts.bar_list(rows)


def _variation_override_matrix(
    values: list[str], overrides: Mapping[str, Mapping[str, float]], col_label_fn: Any
) -> str:
    if not overrides:
        return '<p class="muted">Không có override — dùng trọng số mặc định.</p>'
    col_keys = sorted(overrides)
    col_labels = [col_label_fn(k) for k in col_keys]
    cells = [
        [
            charts.MatrixCell(
                value=int(round(overrides[col].get(value, 0.0) * 1000)),
                label=f"{overrides[col].get(value, 0.0):.1%}",
                ok=overrides[col].get(value, 0.0) > 0,
            )
            for col in col_keys
        ]
        for value in values
    ]
    return charts.heat_matrix([_variation_value_label(v) for v in values], col_labels, cells)


def _register_label(value: str) -> str:
    try:
        return REGISTER_LABELS[Register(value)]
    except ValueError:
        return value


def _sentence_type_label(value: str) -> str:
    try:
        return SENTENCE_TYPE_LABELS[SentenceType(value)]
    except ValueError:
        return value


def render_variation(report: Mapping[str, Any]) -> str:
    space = report["variation_space"]
    warnings = report["warnings"]
    min_legacy = report["min_legacy_share_per_province"]

    kpi_tone = "warning" if warnings else "accent"
    min_space = min(space.values()) if space else 0
    kpis = "".join(
        [
            charts.kpi_card(
                "Trục variation", str(len(report["axes"])), "config/variation.yaml", tone=kpi_tone
            ),
            charts.kpi_card(
                "Sàn legacy / tỉnh",
                f"{min_legacy['min_share']:.1%}",
                f"khả thi tối đa {min_legacy['achievable_share']:.1%} ở admin_unit",
                tone=kpi_tone,
            ),
            charts.kpi_card(
                "Không gian nhỏ nhất",
                f"{min_space:,}",
                "tổ hợp ước lượng, register thấp nhất",
                tone=kpi_tone,
            ),
        ]
    )

    warning_block = ""
    if warnings:
        checks = [
            charts.Check(
                status="warn",
                title=_VARIATION_WARNING_TITLES.get(w["id"], w["id"]),
                detail=w["detail"],
            )
            for w in warnings
        ]
        warning_block = f"<h2>Cảnh báo</h2>{charts.check_list(checks)}"

    note_block = "".join(f'<p class="muted">{escape(n)}</p>' for n in report["notes"])

    axis_blocks: list[str] = []
    for axis in report["axes"]:
        axis_label = VARIATION_AXIS_LABELS.get(axis["name"], axis["name"])
        by_register_html = _variation_override_matrix(
            axis["values"], axis["by_register"], _register_label
        )
        by_category_html = _variation_override_matrix(
            axis["values"], axis["by_category"], _category_label
        )
        axis_blocks.append(
            f"""
<div class="card">
<h3>{escape(axis_label)} <code>{escape(axis['name'])}</code></h3>
<h4>Trọng số mặc định</h4>
{_variation_weight_bars(axis["weights"])}
<h4>Override theo register</h4>
{by_register_html}
<h4>Override theo category</h4>
{by_category_html}
</div>"""
        )

    lexicon_header = "".join(f"<th>{escape(_register_label(r.value))}</th>" for r in Register)
    lexicon_rows = "".join(
        f"<tr><td>{escape(slot)}</td>"
        + "".join(
            f'<td class="num">{per_register.get(r.value, 0.0):.0%} '
            f'({report["lexicon_pool"].get(slot, {}).get(r.value, "—")} giá trị)</td>'
            for r in Register
        )
        + "</tr>"
        for slot, per_register in report["lexicon_rates"].items()
    )

    space_rows = "".join(
        f"<tr><td>{escape(_register_label(reg))}</td><td class=\"num\">{count:,}</td></tr>"
        for reg, count in sorted(space.items())
    )

    addr = report.get("address_synthesis")
    address_synthesis_block = ""
    if addr:
        address_synthesis_block = f"""
<h2>Synthesize số nhà (category=address)</h2>
<p class="section-intro">Không có entity address riêng trong master data — số nhà được sinh ngẫu nhiên
lúc generate (phase 6), tham số sống ở đây (<code>config/variation.yaml#address_synthesis</code>).</p>
{charts.info_grid([
    ("Số nhà nhỏ nhất", addr["house_number_min"]),
    ("Số nhà lớn nhất", addr["house_number_max"]),
    ("Tỷ lệ dạng ngõ/hẻm (xx/yy)", f"{addr['alley_suffix_rate']:.0%}"),
    ("Tỷ lệ hậu tố chữ cái (vd 15B)", f"{addr['letter_suffix_rate']:.0%}"),
])}
"""

    body = f"""
<p class="section-intro">{escape(report["note"])}
Xem <a href="/config">/config</a> cho tỷ trọng GIỮA các cell (<code>config/distribution.yaml</code>).</p>
<div class="grid grid-kpi">{kpis}</div>
{warning_block}
{note_block}
{_VARIATION_APPLICABILITY_HTML}
<h2>Trục</h2>
{"".join(axis_blocks)}

<h2>Lexicon rates (opener/closer/filler)</h2>
<p class="section-intro">Xác suất slot KHÔNG rỗng theo register. Số trong ngoặc là số giá trị khác rỗng
sẵn có trong <code>data/templates/lexicon.yaml</code> cho register đó.</p>
<div class="table-wrap"><table>
<tr><th>Slot</th>{lexicon_header}</tr>
{lexicon_rows}
</table></div>

<h2>Không gian variation ước lượng</h2>
<div class="table-wrap"><table>
<tr><th>Register</th><th class="num">Số tổ hợp</th></tr>
{space_rows}
</table></div>
{address_synthesis_block}
"""
    return _layout("Variation", body, current="/variation")


# ---------------------------------------------------------------------------
# Raw data
# ---------------------------------------------------------------------------


def render_raw(raw_report: Mapping[str, Any], master: MasterData) -> str:
    totals = raw_report["totals"]
    recon = raw_report["reconciliation"]

    kpis = "".join(
        [
            charts.kpi_card("Tỉnh (raw)", f"{totals['provinces']:,}", "data/raw/admin/provinces.yaml"),
            charts.kpi_card("District (raw)", f"{totals['districts']:,}", "data/raw/admin/districts.csv"),
            charts.kpi_card("Ward (raw)", f"{totals['wards']:,}", "data/raw/admin/wards.csv"),
            charts.kpi_card(
                "OSM snapshot",
                f"{totals['osm_snapshots']}",
                f"{totals['osm_elements']:,} element trong data/raw/osm/",
            ),
        ]
    )

    def _recon_row(label: str, raw_value: Any, master_value: Any, ok: bool) -> str:
        badge = "good" if ok else "warning"
        return (
            f"<tr><td>{escape(label)}</td>"
            f'<td class="num">{raw_value}</td><td class="num">{master_value}</td>'
            f'<td><span class="badge badge-check-{"ok" if ok else "warn"}" '
            f'data-tone="{badge}">{"khớp" if ok else "lệch"}</span></td></tr>'
        )

    recon_rows = (
        _recon_row("District", recon["district_count_raw"], recon["district_count_master"], recon["district_count_ok"])
        + _recon_row("Ward", recon["ward_count_raw"], recon["ward_count_master"], recon["ward_count_ok"])
        + _recon_row(
            "Tỉnh",
            totals["provinces"],
            len(master.provinces),
            not recon["in_raw_not_master"] and not recon["in_master_not_raw"],
        )
    )

    osm_rows = "".join(
        f'<tr><td><a href="/provinces/{escape(pid)}">{escape(pid)}</a></td>'
        f"<td>{escape(master.provinces[pid].name_current) if pid in master.provinces else '—'}</td>"
        f'<td class="num">{count:,}</td></tr>'
        for pid, count in raw_report["osm_element_counts"].items()
    )

    without_osm = raw_report["provinces_without_osm"]
    without_html = ""
    if without_osm:
        pills = "".join(
            f'<a class="badge badge-empty" href="/provinces/{escape(pid)}">'
            f"{escape(master.provinces[pid].name_current if pid in master.provinces else pid)}</a> "
            for pid in without_osm
        )
        without_html = f"""
<h2>Tỉnh chưa có OSM snapshot ({len(without_osm)})</h2>
<p class="section-intro">Chạy <code>scripts/fetch_osm.py --province-id &lt;id&gt;</code> rồi
<code>navtext build-master</code> để bổ sung street/POI cho các tỉnh này.</p>
<p>{pills}</p>
"""

    body = f"""
<p class="section-intro"><code>data/raw/</code> là nguồn <strong>bất khả xâm phạm</strong> —
mọi thứ trong <code>data/master/</code> đều sinh ra từ đây. Trang này đối chiếu hai bên để phát hiện
build lệch.</p>
<div class="grid grid-kpi">{kpis}</div>

<h2>Đối chiếu raw ↔ master</h2>
<div class="table-wrap"><table>
<tr><th>Bảng</th><th class="num">Raw</th><th class="num">Master</th><th>Trạng thái</th></tr>
{recon_rows}
</table></div>

<h2>OSM snapshot theo tỉnh</h2>
<div class="table-wrap"><table>
<tr><th>Mã tỉnh</th><th>Tên</th><th class="num">Element</th></tr>
{osm_rows or '<tr><td colspan="3">—</td></tr>'}
</table></div>
{without_html}
"""
    return _layout("Raw data", body, current="/raw")
