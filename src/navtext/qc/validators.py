"""Tầng 1 QC: validator cứng chạy trên từng Sample. Implement ở phase 6/9.
Xem docs/testing.md#property-test và docs/qc.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from navtext.schema import Sample


@dataclass(frozen=True, slots=True)
class Issue:
    sample_id: str
    reason: str


def _no_digits(sample: Sample) -> Issue | None:
    """text không được chứa chữ số Ả Rập (hard rule #1)."""
    raise NotImplementedError


def _no_unfilled_slots(sample: Sample) -> Issue | None:
    """text không còn "{"/"}" — slot chưa fill lọt qua."""
    raise NotImplementedError


def _no_double_space(sample: Sample) -> Issue | None:
    """text không có double space / leading-trailing space."""
    raise NotImplementedError


HARD_VALIDATORS: list[Callable[[Sample], Issue | None]] = [
    _no_digits,
    _no_unfilled_slots,
    _no_double_space,
]
