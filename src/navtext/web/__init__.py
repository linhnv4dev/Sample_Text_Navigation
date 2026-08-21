"""Web UI read-only cho master data — xem docs/web.md.

Layer tách theo CLAUDE.md#code-conventions: `query.py`/`routes.py`/`render.py`
là hàm thuần, test được không cần mở socket; `server.py` là adapter mỏng duy
nhất chạm vào `http.server`.
"""
