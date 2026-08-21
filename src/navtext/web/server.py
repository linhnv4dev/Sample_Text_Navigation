"""Adapter mỏng quanh http.server — điểm DUY NHẤT trong navtext.web chạm
vào socket. Mọi logic route/render/filter nằm ở routes.py/render.py/query.py
và test được mà không cần server thật (xem tests/test_web_routes.py).

Chỉ implement do_GET — không do_POST/do_PUT/do_DELETE. Đây là cách rẻ nhất
để "read-only" là bất biến cấu trúc của class, không phải một lời hứa có
thể phá vỡ khi ai đó thêm route sau này.
"""

from __future__ import annotations

import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from navtext.web import render
from navtext.web.routes import AppContext, Response, route


def make_server(
    ctx: AppContext, host: str = "127.0.0.1", port: int = 8000
) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — tên bắt buộc bởi BaseHTTPRequestHandler
            parts = urlsplit(self.path)
            try:
                response = route(parts.path, parts.query, ctx)
            except Exception as exc:  # noqa: BLE001 — bug ở bất kỳ route nào cũng
                # phải thành trang 500 đọc được, không phải kết nối đứt giữa
                # chừng (log_message() bị làm câm bên dưới nên traceback đi
                # thẳng ra stderr, không qua BaseHTTPRequestHandler logging).
                traceback.print_exc(file=sys.stderr)
                response = Response(
                    status=500,
                    body=render.render_error(str(exc)).encode("utf-8"),
                )
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)

        def log_message(self, format: str, *args: object) -> None:
            # Im lặng theo mặc định — logging thật (nếu cần) nên đi qua
            # structured counter như CLAUDE.md#code-conventions, không phải
            # print rải rác của BaseHTTPRequestHandler.
            pass

    return ThreadingHTTPServer((host, port), Handler)
