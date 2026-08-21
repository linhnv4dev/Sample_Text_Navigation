"""Test cho `navtext plan` (CLI, phase 3) — dùng cùng fixture 2 tỉnh với
test_sampling.py, chạy qua build_parser()/main() thật thay vì gọi thẳng
_cmd_plan để bắt lỗi wiring argparse.
"""

from __future__ import annotations

import json
from pathlib import Path

from navtext.build_master import build_master
from navtext.cli import main

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


def test_cmd_plan_writes_plan_json(tmp_path: Path) -> None:
    master_dir = _build(tmp_path)
    output_dir = tmp_path / "outputs"

    exit_code = main(
        [
            "plan",
            "--run",
            "r1",
            "--config",
            str(FIXTURE_DISTRIBUTION),
            "--master-dir",
            str(master_dir),
            "--run-config",
            str(FIXTURE_RUN_CONFIG),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    plan_path = output_dir / "r1" / "plan.json"
    assert plan_path.exists()
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    assert data["run_id"] == "r1"
    assert data["total"] == 1000
    assert sum(c["target_count"] for c in data["cells"]) == 1000
    assert data["config_hash"]
    assert data["master_data_hash"]


def test_cmd_plan_respects_target_override(tmp_path: Path) -> None:
    master_dir = _build(tmp_path)
    output_dir = tmp_path / "outputs"

    exit_code = main(
        [
            "plan",
            "--run",
            "proto",
            "--config",
            str(FIXTURE_DISTRIBUTION),
            "--master-dir",
            str(master_dir),
            "--run-config",
            str(FIXTURE_RUN_CONFIG),
            "--output-dir",
            str(output_dir),
            "--target",
            "100",
        ]
    )

    assert exit_code == 0
    data = json.loads((output_dir / "proto" / "plan.json").read_text(encoding="utf-8"))
    assert data["total"] == 100
    assert sum(c["target_count"] for c in data["cells"]) == 100


def test_cmd_plan_fails_without_master_dir(tmp_path: Path, capsys) -> None:
    exit_code = main(
        [
            "plan",
            "--run",
            "r1",
            "--config",
            str(FIXTURE_DISTRIBUTION),
            "--master-dir",
            str(tmp_path / "nonexistent"),
            "--run-config",
            str(FIXTURE_RUN_CONFIG),
            "--output-dir",
            str(tmp_path / "outputs"),
        ]
    )
    assert exit_code == 1
    assert "build-master" in capsys.readouterr().err
