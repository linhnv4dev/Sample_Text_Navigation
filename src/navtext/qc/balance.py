"""Tầng 3 QC: đo phân bố, tìm gap, chọn tập cuối. Implement ở phase 11.
Xem docs/sampling.md#balancing-loop.
"""

from __future__ import annotations

from typing import Any

from navtext.schema import Cell, Sample


def measure(samples: list[Sample]) -> dict[str, Any]:
    """Đếm samples theo mọi trục ở docs/sampling.md#distribution-caps."""
    raise NotImplementedError


def find_gaps(measured: dict[str, Any], target: dict[str, Any]) -> list[Cell]:
    """Trả về danh sách cell đang thiếu so với target (cần generate lại)."""
    raise NotImplementedError


def select_final(candidates: list[Sample], n: int) -> list[Sample]:
    """Cắt candidates xuống đúng n, ưu tiên giữ đủ sàn mọi cell trước khi
    trim cell vượt trần.
    """
    raise NotImplementedError
