"""Test cho `navtext generate` (CLI, phase 6/7) — dùng fixture 2 tỉnh (như
test_cli_plan.py) cho master/distribution, nhưng template bank + variation
config THẬT (fixture templates/ chỉ có case âm tính, không phải bank hợp
lệ đủ 30 ô) — chạy qua build_parser()/main() thật để bắt lỗi wiring.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from navtext.build_master import build_master
from navtext.cli import main
from navtext.schema import CSV_COLUMNS

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_TEMPLATES_DIR = REPO_ROOT / "data" / "templates"
REAL_VARIATION_CONFIG = REPO_ROOT / "config" / "variation.yaml"

FIXTURE_RAW = Path(__file__).parent / "fixtures" / "raw"
FIXTURE_OSM_TAGS = Path(__file__).parent / "fixtures" / "config" / "osm_tags.yaml"
FIXTURE_RUN_CONFIG = Path(__file__).parent / "fixtures" / "config" / "run.yaml"
FIXTURE_DISTRIBUTION = Path(__file__).parent / "fixtures" / "config" / "distribution.yaml"


def _build(tmp_path: Path) -> Path:
    master_dir = tmp_path / "master"
    build_master(
        raw_dir=FIXTURE_RAW,
        master_dir=master_dir,
        osm_tag_config_path=FIXTURE_OSM_TAGS,
        run_config_path=FIXTURE_RUN_CONFIG,
    )
    return master_dir


def _plan(tmp_path: Path, master_dir: Path, run: str, target: int) -> Path:
    output_dir = tmp_path / "outputs"
    exit_code = main(
        [
            "plan",
            "--run",
            run,
            "--config",
            str(FIXTURE_DISTRIBUTION),
            "--master-dir",
            str(master_dir),
            "--run-config",
            str(FIXTURE_RUN_CONFIG),
            "--output-dir",
            str(output_dir),
            "--target",
            str(target),
        ]
    )
    assert exit_code == 0
    return output_dir


def test_cmd_generate_writes_csv_and_manifest(tmp_path: Path) -> None:
    master_dir = _build(tmp_path)
    output_dir = _plan(tmp_path, master_dir, "r1", target=100)

    exit_code = main(
        [
            "generate",
            "--run",
            "r1",
            "--master-dir",
            str(master_dir),
            "--template-dir",
            str(REAL_TEMPLATES_DIR),
            "--config",
            str(FIXTURE_DISTRIBUTION),
            "--variation-config",
            str(REAL_VARIATION_CONFIG),
            "--run-config",
            str(FIXTURE_RUN_CONFIG),
            "--output-dir",
            str(output_dir),
            "--out",
            "prototype.csv",
        ]
    )
    assert exit_code == 0

    csv_path = output_dir / "r1" / "prototype.csv"
    assert csv_path.exists()
    with csv_path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 100
    assert set(rows[0]) == set(CSV_COLUMNS)

    manifest_path = output_dir / "r1" / "run_manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["run_id"] == "r1"
    assert manifest["config_hash"]
    assert manifest["master_data_hash"]
    assert manifest["template_bank_hash"]
    assert manifest["code_version"]
    assert sum(manifest["cell_counts"].values()) == 100


def test_cmd_generate_is_byte_identical_on_rerun(tmp_path: Path) -> None:
    master_dir = _build(tmp_path)
    output_dir = _plan(tmp_path, master_dir, "r1", target=100)

    args = [
        "generate",
        "--run",
        "r1",
        "--master-dir",
        str(master_dir),
        "--template-dir",
        str(REAL_TEMPLATES_DIR),
        "--config",
        str(FIXTURE_DISTRIBUTION),
        "--variation-config",
        str(REAL_VARIATION_CONFIG),
        "--run-config",
        str(FIXTURE_RUN_CONFIG),
        "--output-dir",
        str(output_dir),
        "--out",
        "prototype.csv",
    ]
    assert main(args) == 0
    csv_path = output_dir / "r1" / "prototype.csv"
    first_bytes = csv_path.read_bytes()

    assert main(args) == 0
    second_bytes = csv_path.read_bytes()
    assert first_bytes == second_bytes


def test_cmd_generate_seed_override_changes_output(tmp_path: Path) -> None:
    master_dir = _build(tmp_path)
    output_dir = _plan(tmp_path, master_dir, "r1", target=100)

    base_args = [
        "generate",
        "--run",
        "r1",
        "--master-dir",
        str(master_dir),
        "--template-dir",
        str(REAL_TEMPLATES_DIR),
        "--config",
        str(FIXTURE_DISTRIBUTION),
        "--variation-config",
        str(REAL_VARIATION_CONFIG),
        "--run-config",
        str(FIXTURE_RUN_CONFIG),
        "--output-dir",
        str(output_dir),
        "--out",
        "prototype.csv",
    ]
    assert main(base_args) == 0
    first_bytes = (output_dir / "r1" / "prototype.csv").read_bytes()

    assert main(base_args + ["--seed", "999999"]) == 0
    second_bytes = (output_dir / "r1" / "prototype.csv").read_bytes()
    assert first_bytes != second_bytes


def test_cmd_generate_fails_without_plan(tmp_path: Path, capsys) -> None:
    master_dir = _build(tmp_path)
    output_dir = tmp_path / "outputs"

    exit_code = main(
        [
            "generate",
            "--run",
            "no-plan-yet",
            "--master-dir",
            str(master_dir),
            "--template-dir",
            str(REAL_TEMPLATES_DIR),
            "--config",
            str(FIXTURE_DISTRIBUTION),
            "--variation-config",
            str(REAL_VARIATION_CONFIG),
            "--run-config",
            str(FIXTURE_RUN_CONFIG),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == 1
    assert "navtext plan" in capsys.readouterr().err
