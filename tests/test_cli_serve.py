"""Test cho navtext.cli._discover_samples() — auto-discovery của /samples
khi `navtext serve` chạy không kèm --samples. Hàm thuần (không đọc nội dung
CSV), test trực tiếp trên layout thư mục dựng bằng tmp_path, không cần
build_master.
"""

from __future__ import annotations

import time
from pathlib import Path


def test_discover_samples_returns_none_when_output_dir_missing(tmp_path: Path) -> None:
    from navtext.cli import _discover_samples

    assert _discover_samples(tmp_path / "does_not_exist") is None


def test_discover_samples_returns_none_when_output_dir_empty(tmp_path: Path) -> None:
    from navtext.cli import _discover_samples

    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    assert _discover_samples(output_dir) is None


def test_discover_samples_skips_run_dir_without_any_csv(tmp_path: Path) -> None:
    """outputs/<run>/ chỉ có plan.json (đã chạy `navtext plan` nhưng chưa
    generate) -> bỏ qua, không được coi là "có sample"."""
    from navtext.cli import _discover_samples

    output_dir = tmp_path / "outputs"
    run_dir = output_dir / "only_plan"
    run_dir.mkdir(parents=True)
    (run_dir / "plan.json").write_text("{}", encoding="utf-8")

    assert _discover_samples(output_dir) is None


def test_discover_samples_picks_most_recently_modified_run_dir(tmp_path: Path) -> None:
    from navtext.cli import _discover_samples

    output_dir = tmp_path / "outputs"
    older = output_dir / "older_run"
    newer = output_dir / "newer_run"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)
    (older / "prototype.csv").write_text("id,text\n1,a\n", encoding="utf-8")
    time.sleep(0.01)
    (newer / "prototype.csv").write_text("id,text\n1,b\n", encoding="utf-8")
    # Đảm bảo mtime của run dir (không phải file) phản ánh thứ tự tạo —
    # os trên một số filesystem không tự cập nhật mtime thư mục cha khi ghi
    # file con nhanh liên tiếp, nên set tường minh cho deterministic.
    older_time = time.time() - 100
    import os

    os.utime(older, (older_time, older_time))

    result = _discover_samples(output_dir)
    assert result == newer / "prototype.csv"


def test_discover_samples_prefers_cleaner_qc_stage_within_same_run(tmp_path: Path) -> None:
    """Trong CÙNG một run dir có cả prototype.csv (thô) và final_100k.csv
    (đã qua QC/balance) -> phải chọn final_100k.csv, không phải file đầu
    tiên tìm thấy."""
    from navtext.cli import _discover_samples

    output_dir = tmp_path / "outputs"
    run_dir = output_dir / "run1"
    run_dir.mkdir(parents=True)
    (run_dir / "prototype.csv").write_text("id,text\n1,a\n", encoding="utf-8")
    (run_dir / "candidates.csv").write_text("id,text\n1,a\n", encoding="utf-8")
    (run_dir / "final_100k.csv").write_text("id,text\n1,a\n", encoding="utf-8")

    result = _discover_samples(output_dir)
    assert result == run_dir / "final_100k.csv"


def test_discover_samples_falls_back_to_candidates_when_no_clean_or_final(tmp_path: Path) -> None:
    from navtext.cli import _discover_samples

    output_dir = tmp_path / "outputs"
    run_dir = output_dir / "run1"
    run_dir.mkdir(parents=True)
    (run_dir / "prototype.csv").write_text("id,text\n1,a\n", encoding="utf-8")
    (run_dir / "candidates.csv").write_text("id,text\n1,a\n", encoding="utf-8")

    result = _discover_samples(output_dir)
    assert result == run_dir / "candidates.csv"
