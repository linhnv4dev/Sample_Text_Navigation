"""route(path, query_string, ctx) -> Response — hàm thuần, test được không
cần mở socket. server.py là adapter mỏng duy nhất gọi vào đây.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs

from navtext.loaders import MasterData
from navtext.templates import Lexicon, Template
from navtext.web import query as query_mod
from navtext.web import render


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    body: bytes
    content_type: str = "text/html; charset=utf-8"


_ASSET_DIR = Path(__file__).parent / "static"

# Allowlist tường minh path -> (file, content-type). KHÔNG Path.join với
# input người dùng ở đây — allowlist là cách duy nhất chặn path traversal
# mà không cần logic sanitize dễ sai (vd "/static/../../etc/passwd" đơn
# giản không khớp key nào trong dict này, không cần parse "..").
_STATIC: dict[str, tuple[Path, str]] = {
    "/static/app.css": (_ASSET_DIR / "app.css", "text/css; charset=utf-8"),
}


@dataclass(frozen=True, slots=True)
class AppContext:
    """State đã build sẵn lúc start server — load 1 lần, dùng lại mọi
    request. 41k+ row đọc lại mỗi request là vô nghĩa vì data chỉ đổi khi
    chạy lại `navtext build-master`.

    Mọi field ngoài `master`/`report` đều có default: `navtext serve` cố ý
    không fail cứng khi thiếu một nguồn (thiếu template bank không được
    ngăn xem master data), nên `None` ở đây là trạng thái hợp lệ và route
    tương ứng render callout hướng dẫn thay vì 500.
    """

    master: MasterData
    report: Mapping[str, Any]
    templates: tuple[Template, ...] = ()
    template_report: Mapping[str, Any] | None = None
    lexicon: Lexicon | None = None
    distribution_config: Mapping[str, Any] | None = None
    raw_report: Mapping[str, Any] | None = None
    readiness: Mapping[str, Any] | None = None
    variation_report: Mapping[str, Any] | None = None
    variation_issues: tuple[str, ...] = ()
    # CSV sample đã generate (--samples outputs/<run>/prototype.csv, v.v.) —
    # xem đúng 1 file người dùng chỉ định, không tự đoán "run mới nhất".
    samples: tuple[Mapping[str, str], ...] = ()
    samples_source: str | None = None
    samples_summary: Mapping[str, Any] | None = None
    # True khi samples_source được cli._discover_samples() tự động chọn
    # (không truyền --samples). Dùng để banner /samples phân biệt "đây là
    # đoán của server" với "người dùng chỉ định chính xác file này".
    samples_auto: bool = False
    # Thời điểm `navtext serve` khởi động, hiện ở footer mọi trang. Default
    # rỗng = tắt footer, để test dựng AppContext tối giản không đổi output.
    started_at: str = ""


def _html(status: int, body: str, ctx: AppContext | None = None) -> Response:
    """Mọi response HTML đi qua đây — nên footer chỉ cần chèn ở đúng một
    chỗ này là có mặt trên cả 8 trang.
    """
    if ctx is not None:
        body = render.with_footer(body, ctx.started_at, len(ctx.templates))
    return Response(status=status, body=body.encode("utf-8"))


def _json(status: int, payload: Any) -> Response:
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return Response(status=status, body=body.encode("utf-8"), content_type="application/json; charset=utf-8")


def _first(values: dict[str, list[str]], name: str) -> str | None:
    found = values.get(name)
    return found[0] if found else None


def _safe_page(raw: str | None) -> int:
    if raw is None:
        return 1
    try:
        parsed = int(raw)
    except ValueError:
        return 1
    return parsed if parsed >= 1 else 1


_PROVINCE_SORT_KEYS: dict[str, Any] = {
    "entities": lambda p: (-p["entities"], p["province_id"]),
    "name": lambda p: (p["name_current"], p["province_id"]),
    "tier": lambda p: (p["popularity_tier"], p["province_id"]),
    "province_id": lambda p: p["province_id"],
}


def _sorted_provinces(rows: list[dict[str, Any]], sort_key: str) -> list[dict[str, Any]]:
    key_fn = _PROVINCE_SORT_KEYS.get(sort_key, _PROVINCE_SORT_KEYS["province_id"])
    return sorted(rows, key=key_fn)


def route(path: str, query_string: str, ctx: AppContext) -> Response:
    """Chỉ GET — không có nhánh nào ghi dữ liệu (read-only là bất biến cấu
    trúc: route() không nhận request body, không gọi bất kỳ hàm ghi file
    nào trong navtext.build_master / navtext.loaders)."""
    params = parse_qs(query_string)

    if path in _STATIC:
        file_path, content_type = _STATIC[path]
        try:
            body = file_path.read_bytes()
        except FileNotFoundError:
            return _html(404, render.render_not_found(f"Không có route {path!r}"), ctx)
        return Response(status=200, body=body, content_type=content_type)

    if path == "/":
        return _html(200, render.render_dashboard(ctx.report), ctx)

    if path == "/provinces":
        sort_key = _first(params, "sort") or "province_id"
        rows = _sorted_provinces(list(ctx.report["by_province"]), sort_key)
        return _html(200, render.render_provinces(rows, sort_key), ctx)

    if path.startswith("/provinces/"):
        province_id = path.removeprefix("/provinces/")
        if not province_id or province_id not in ctx.master.provinces:
            return _html(404, render.render_not_found(f"Không tìm thấy tỉnh {province_id!r}"), ctx)
        return _html(200, render.render_province_detail(ctx.master, ctx.report, province_id), ctx)

    if path == "/entities":
        entity_query = query_mod.EntityQuery(
            q=_first(params, "q") or None,
            province_id=_first(params, "province") or None,
            category=_first(params, "category") or None,
            subcategory=_first(params, "subcategory") or None,
            page=_safe_page(_first(params, "page")),
        )
        entities = query_mod.all_entities(ctx.master)
        filtered = query_mod.filter_entities(entities, entity_query)
        page_items, total = query_mod.paginate(filtered, entity_query.page, entity_query.page_size)
        return _html(200, render.render_entities(entity_query, page_items, total, ctx.report), ctx)

    if path == "/templates":
        if ctx.template_report is None:
            return _html(
                200,
                render.render_missing_source(
                    "Templates",
                    "template bank (data/templates/)",
                    "Chạy: navtext serve --template-dir data/templates",
                    current="/templates",
                ),
                ctx,
            )
        template_query = query_mod.TemplateQuery(
            q=_first(params, "q") or None,
            sentence_type=_first(params, "sentence_type") or None,
            register=_first(params, "register") or None,
            length_class=_first(params, "length_class") or None,
            page=_safe_page(_first(params, "page")),
        )
        filtered = query_mod.filter_templates(ctx.templates, template_query)
        page_items, total = query_mod.paginate(
            filtered, template_query.page, template_query.page_size
        )
        return _html(
            200,
            render.render_templates(
                template_query, page_items, total, ctx.template_report, ctx.lexicon
            ),
            ctx,
        )

    if path == "/readiness":
        if ctx.readiness is None:
            return _html(
                200,
                render.render_missing_source(
                    "Readiness",
                    "dữ liệu readiness",
                    "Cần ít nhất config/distribution.yaml — chạy: navtext serve",
                    current="/readiness",
                ),
                ctx,
            )
        return _html(200, render.render_readiness(ctx.readiness), ctx)

    if path == "/config":
        if ctx.distribution_config is None:
            return _html(
                200,
                render.render_missing_source(
                    "Config",
                    "config/distribution.yaml",
                    "Chạy: navtext serve --distribution-config config/distribution.yaml",
                    current="/config",
                ),
                ctx,
            )
        return _html(200, render.render_config(ctx.distribution_config), ctx)

    if path == "/variation":
        if ctx.variation_report is None:
            return _html(
                200,
                render.render_missing_source(
                    "Variation",
                    "config/variation.yaml",
                    "Chạy: navtext serve --variation-config config/variation.yaml",
                    current="/variation",
                ),
                ctx,
            )
        return _html(200, render.render_variation(ctx.variation_report), ctx)

    if path == "/samples":
        if not ctx.samples:
            return _html(
                200,
                render.render_missing_source(
                    "Samples",
                    "sample đã generate",
                    "Chạy `navtext generate` trước — navtext serve tự tìm CSV mới nhất trong "
                    "--output-dir (mặc định outputs/), trừ khi chạy với --no-auto-samples. "
                    "Muốn xem đúng một file thì dùng --samples outputs/<run>/prototype.csv.",
                    current="/samples",
                ),
                ctx,
            )
        sample_query = query_mod.SampleQuery(
            q=_first(params, "q") or None,
            province=_first(params, "province") or None,
            category=_first(params, "category") or None,
            sentence_type=_first(params, "sentence_type") or None,
            register=_first(params, "register") or None,
            name_variant=_first(params, "name_variant") or None,
            page=_safe_page(_first(params, "page")),
        )
        filtered = query_mod.filter_samples(ctx.samples, sample_query)
        page_items, total = query_mod.paginate(filtered, sample_query.page, sample_query.page_size)
        return _html(
            200,
            render.render_samples(
                sample_query,
                page_items,
                total,
                ctx.samples_summary or {},
                ctx.samples_source,
                samples_auto=ctx.samples_auto,
            ),
            ctx,
        )

    if path == "/raw":
        if ctx.raw_report is None:
            return _html(
                200,
                render.render_missing_source(
                    "Raw data",
                    "data/raw/",
                    "Chạy: navtext serve --raw-dir data/raw",
                    current="/raw",
                ),
                ctx,
            )
        return _html(200, render.render_raw(ctx.raw_report, ctx.master), ctx)

    if path == "/api/report.json":
        return _json(200, dict(ctx.report))

    if path == "/api/readiness.json":
        if ctx.readiness is None:
            return _json(404, {"error": "readiness chưa được nạp"})
        return _json(200, dict(ctx.readiness))

    if path == "/api/variation.json":
        if ctx.variation_report is None:
            return _json(404, {"error": "config/variation.yaml chưa được nạp"})
        return _json(200, dict(ctx.variation_report))

    if path == "/api/samples.json":
        if not ctx.samples:
            return _json(404, {"error": "chưa nạp sample nào — dùng navtext serve --samples <csv>"})
        return _json(
            200,
            {
                "source": ctx.samples_source,
                "total": len(ctx.samples),
                "summary": dict(ctx.samples_summary or {}),
            },
        )

    return _html(404, render.render_not_found(f"Không có route {path!r}"), ctx)
