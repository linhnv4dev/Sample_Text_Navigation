"""Tầng 2 QC: exact + near-dup dedup. Implement ở phase 11. Xem docs/qc.md."""

from __future__ import annotations

from navtext.schema import Sample


def exact_key(text: str) -> str:
    """Key chuẩn hoá dùng để phát hiện trùng lặp chính xác (normalize
    whitespace/case theo quy tắc sẽ chốt ở phase 11)."""
    raise NotImplementedError


def near_dup_clusters(samples: list[Sample]) -> list[list[Sample]]:
    """Gom cụm các sample gần giống nhau (near-duplicate), mỗi cụm là một
    list sample cùng cluster."""
    raise NotImplementedError
