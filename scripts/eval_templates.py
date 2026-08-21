#!/usr/bin/env python3
"""Đánh giá template bank theo rubric "câu user nói với trợ lý ảo" — SCRIPT
CHẠY TAY, ngoài 12 phase, không sinh artifact nào của dataset (giống
fetch_osm.py/extract_wards.py). Không side-effect trên data/templates/ —
chỉ đọc và in báo cáo ra stdout.

Dùng để so sánh "trước/sau" khi chỉnh templates.yaml/lexicon.yaml theo
yêu cầu: loại kính ngữ liên nhân, loại lời cầu viện người thứ ba, loại
slang vùng miền mạnh, và kiểm tra có đủ câu điều khiển thiết bị thật.

Cách dùng:
    python3 scripts/eval_templates.py
    python3 scripts/eval_templates.py --templates-dir data/templates
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

# Các danh sách rubric — cố tình KHÔNG tái dùng lexicon.yaml (mục đích khác
# nhau: đây là rubric CHẤM bank, lexicon.yaml là NGUỒN TỪ VỰNG bank dùng).
HONORIFIC_WORDS = {
    "xin", "vui", "lòng", "thưa", "kính", "mong", "nhờ", "ơn", "cảm", "ạ",
}
HONORIFIC_PHRASES = ["vui lòng", "làm ơn", "xin phép", "cảm ơn", "mong được"]
THIRD_PARTY_PHRASES = [
    "có ai biết", "ai biết", "hỏi thăm", "có biết",
]
REGIONAL_SLANG_WORDS = {
    "hông", "vô", "giùm", "lẹ", "quẹo", "tấp", "cua", "lượn", "ngó", "coi",
    "nè", "á", "phóng", "vọt", "mò", "lục", "táp",
}
DEVICE_CONTROL_PHRASES = [
    "mở chỉ đường", "bật chỉ đường", "đặt điểm đến", "đổi điểm đến",
    "thêm điểm dừng", "hủy dẫn đường", "huỷ dẫn đường", "định vị",
    "mở bản đồ",
]

_SLOT_PATTERN = re.compile(r"\{[a-zA-Z_]+\}")


def _bare_tokens(text: str) -> list[str]:
    """Token nội dung của text, đã bỏ {slot} — dùng để so khớp từ nguyên."""
    stripped = _SLOT_PATTERN.sub(" ", text)
    stripped = stripped.replace(",", " ")
    return stripped.split()


def _load_bank(templates_dir: Path) -> list[dict]:
    path = templates_dir / "templates.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or []


def evaluate(templates_dir: Path) -> dict:
    entries = _load_bank(templates_dir)
    n = len(entries)

    honorific_hits: list[tuple[str, str]] = []
    third_party_hits: list[tuple[str, str]] = []
    slang_hits: list[tuple[str, str]] = []
    device_hits: set[str] = set()  # register có ít nhất 1 câu device control
    head_counter: Counter[str] = Counter()
    honorific_by_register: Counter[str] = Counter()
    total_by_register: Counter[str] = Counter()

    for e in entries:
        text = e["text"]
        register = e["register"]
        total_by_register[register] += 1
        tokens = set(_bare_tokens(text))

        is_honorific = bool(tokens & HONORIFIC_WORDS) or any(
            p in text for p in HONORIFIC_PHRASES
        )
        if is_honorific:
            honorific_hits.append((e["template_id"], text))
            honorific_by_register[register] += 1

        if any(p in text for p in THIRD_PARTY_PHRASES):
            third_party_hits.append((e["template_id"], text))

        slang = tokens & REGIONAL_SLANG_WORDS
        if slang:
            slang_hits.append((e["template_id"], text))

        if any(p in text for p in DEVICE_CONTROL_PHRASES):
            device_hits.add(register)

        head = re.sub(r"^\{opener\}", "", text).split()[0]
        head_counter[head] += 1

    distinct_heads = len(head_counter)
    top_head, top_head_count = head_counter.most_common(1)[0]
    top_head_share = top_head_count / n if n else 0.0

    return {
        "n": n,
        "honorific_hits": honorific_hits,
        "third_party_hits": third_party_hits,
        "slang_hits": slang_hits,
        "device_hits": device_hits,
        "registers": sorted(total_by_register),
        "honorific_by_register": honorific_by_register,
        "total_by_register": total_by_register,
        "distinct_heads": distinct_heads,
        "top_head": (top_head, top_head_count, top_head_share),
    }


def format_report(result: dict, templates_dir: Path) -> str:
    lines = [f"# Template register review — {templates_dir}", ""]
    n = result["n"]

    def check(name: str, ok: bool, detail: str) -> str:
        mark = "PASS" if ok else "FAIL"
        return f"[{mark}] {name}: {detail}"

    lines.append(
        check(
            "A1 không kính ngữ liên nhân",
            not result["honorific_hits"],
            f"{len(result['honorific_hits'])}/{n} vi phạm",
        )
    )
    lines.append(
        check(
            "A2 không cầu viện người thứ ba",
            not result["third_party_hits"],
            f"{len(result['third_party_hits'])}/{n} vi phạm",
        )
    )
    lines.append(
        check(
            "A4 không slang vùng miền mạnh",
            not result["slang_hits"],
            f"{len(result['slang_hits'])}/{n} vi phạm",
        )
    )
    missing_device = [
        r for r in result["registers"] if r not in result["device_hits"]
    ]
    lines.append(
        check(
            "A6 có câu điều khiển thiết bị mỗi register",
            not missing_device,
            f"thiếu ở register: {missing_device}" if missing_device else "đủ 3 register",
        )
    )
    formal_total = result["total_by_register"].get("formal", 0)
    formal_honorific = result["honorific_by_register"].get("formal", 0)
    lines.append(
        check(
            "A7 formal không phải kính ngữ",
            formal_honorific == 0,
            f"{formal_honorific}/{formal_total} câu formal là kính ngữ",
        )
    )
    top_head, top_count, top_share = result["top_head"]
    lines.append(
        check(
            "C2 không khung mở đầu nào > 8% bank",
            top_share <= 0.08,
            f"{top_head!r} chiếm {top_count}/{n} = {top_share:.1%}",
        )
    )
    lines.append(f"[INFO] C1 số khung mở đầu khác nhau: {result['distinct_heads']}")
    lines.append("")

    for label, hits in (
        ("Chi tiết A1 (kính ngữ)", result["honorific_hits"]),
        ("Chi tiết A2 (người thứ ba)", result["third_party_hits"]),
        ("Chi tiết A4 (slang)", result["slang_hits"]),
    ):
        if not hits:
            continue
        lines.append(f"## {label} ({len(hits)})")
        for tid, text in hits:
            lines.append(f"- {tid}: {text}")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--templates-dir",
        type=Path,
        default=REPO_ROOT / "data" / "templates",
    )
    args = parser.parse_args()
    result = evaluate(args.templates_dir)
    print(format_report(result, args.templates_dir))


if __name__ == "__main__":
    main()
