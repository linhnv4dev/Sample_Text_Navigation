"""Smoke test DUY NHẤT mở server thật (port=0 để OS tự cấp cổng trống,
tránh đụng cổng đang chạy) — chỉ để chứng minh server.py nối đúng vào
routes.route(). Mọi logic khác (filter/escape/404/...) đã test qua
route() trực tiếp ở tests/test_web_routes.py, không cần lặp lại ở đây.
"""

from __future__ import annotations

import http.client
import threading
from pathlib import Path

from navtext.build_master import build_master
from navtext.loaders import load_master
from navtext.stats import build_master_report
from navtext.web.routes import AppContext
from navtext.web.server import make_server

FIXTURE_RAW = Path(__file__).parent / "fixtures" / "raw"
FIXTURE_OSM_TAGS = Path(__file__).parent / "fixtures" / "config" / "osm_tags.yaml"
FIXTURE_RUN_CONFIG = Path(__file__).parent / "fixtures" / "config" / "run.yaml"
ADMIN_INVARIANTS = {"expected_province_count": 2, "expected_legacy_alias_count": 3}


def test_server_serves_dashboard_over_real_socket(tmp_path: Path) -> None:
    master_dir = tmp_path / "master"
    build_master(
        raw_dir=FIXTURE_RAW,
        master_dir=master_dir,
        osm_tag_config_path=FIXTURE_OSM_TAGS,
        run_config_path=FIXTURE_RUN_CONFIG,
    )
    master = load_master(master_dir)
    report = build_master_report(master, ADMIN_INVARIANTS)
    ctx = AppContext(master=master, report=report)

    server = make_server(ctx, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[0], server.server_address[1]
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        assert resp.status == 200
        body = resp.read().decode("utf-8")
        assert "Dashboard" in body
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_server_turns_route_exception_into_500_not_dropped_connection(tmp_path: Path, monkeypatch) -> None:
    """Regression: route() có thể ném exception không lường trước (vd bug
    schema như /templates từng gặp — xem test_web_templates.py). Trước khi
    server.py bọc try/except, exception bay lên handle_error() và client
    nhận response đứt (trắng trang, khó chẩn đoán). Giờ phải là 500 đọc
    được, kết nối vẫn đóng sạch."""
    master_dir = tmp_path / "master"
    build_master(
        raw_dir=FIXTURE_RAW,
        master_dir=master_dir,
        osm_tag_config_path=FIXTURE_OSM_TAGS,
        run_config_path=FIXTURE_RUN_CONFIG,
    )
    master = load_master(master_dir)
    report = build_master_report(master, ADMIN_INVARIANTS)
    ctx = AppContext(master=master, report=report)

    def _boom(path, query_string, ctx):
        raise RuntimeError("lỗi giả lập để test")

    import navtext.web.server as server_module

    monkeypatch.setattr(server_module, "route", _boom)

    server = make_server(ctx, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[0], server.server_address[1]
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        assert resp.status == 500
        body = resp.read().decode("utf-8")
        assert "lỗi giả lập để test" in body
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_server_binds_localhost_by_default_not_any_interface(tmp_path: Path) -> None:
    """Tool dev local đọc file trên máy không nên tự phơi ra LAN — bind
    0.0.0.0 phải là lựa chọn tường minh của người dùng qua --host, không
    phải default."""
    master_dir = tmp_path / "master"
    build_master(
        raw_dir=FIXTURE_RAW,
        master_dir=master_dir,
        osm_tag_config_path=FIXTURE_OSM_TAGS,
        run_config_path=FIXTURE_RUN_CONFIG,
    )
    master = load_master(master_dir)
    report = build_master_report(master, ADMIN_INVARIANTS)
    ctx = AppContext(master=master, report=report)

    server = make_server(ctx, port=0)  # không truyền host -> phải dùng default
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()
