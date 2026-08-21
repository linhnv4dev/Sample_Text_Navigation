"""CLI cho NavText — xem docs/CLAUDE.md#pipeline cho 12 phase.

argparse skeleton là thật (parse được ngay); handler của từng subcommand sẽ
được điền dần khi phase tương ứng được implement.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from navtext.build_master import BuildError, build_master, read_raw
from navtext.generator import build_context, generate_all
from navtext.loaders import load_master
from navtext.sampling import PlanError, build_plan, plan_from_doc, plan_to_doc, scale_plan, validate_plan
from navtext.schema import CSV_COLUMNS
from navtext.stats import (
    build_master_report,
    build_raw_report,
    build_readiness_report,
    build_template_report,
    build_variation_report,
)
from navtext.templates import TemplateError, load_lexicon, load_templates
from navtext.variation import VariationError, load_variation_config
from navtext.web.query import summarize_samples
from navtext.web.routes import AppContext
from navtext.web.server import make_server


def _sha256_of_paths(paths: list[Path]) -> str:
    """Hash nội dung của một tập file, độc lập thứ tự — dùng cho
    plan.json#config_hash/master_data_hash và (phase 12) run_manifest.json.
    Không phải phần trong docs/output.md#run-manifest do hash một FILE duy
    nhất (config/distribution.yaml không tách nhiều file như data/master/).
    """
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _cmd_build_master(args: argparse.Namespace) -> int:
    """Phase 2: data/raw/ -> data/master/*.csv."""
    try:
        counter = build_master(args.raw_dir, args.master_dir)
    except BuildError as exc:
        print(f"build-master FAILED — {len(exc.issues)} violation(s):", file=sys.stderr)
        for issue in exc.issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1

    print("build-master OK:")
    for key, value in sorted(counter.items()):
        print(f"  {key}: {value}")
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    """Phase 3: config/distribution.yaml + master -> outputs/<run>/plan.json."""
    if not args.master_dir.exists():
        print(
            f"Không tìm thấy {args.master_dir} — chạy `navtext build-master` trước.",
            file=sys.stderr,
        )
        return 1

    with args.config.open(encoding="utf-8") as f:
        config: dict[str, Any] = yaml.safe_load(f)
    with args.run_config.open(encoding="utf-8") as f:
        run_config: dict[str, Any] = yaml.safe_load(f)

    master = load_master(args.master_dir)
    target = args.target if args.target is not None else int(config["target_size"])

    try:
        plan = build_plan(config, master)
        if target != int(config["target_size"]):
            plan = scale_plan(plan, target)
        validate_plan(plan, config, master, total=target)
    except PlanError as exc:
        print(f"plan FAILED — {len(exc.issues)} violation(s):", file=sys.stderr)
        for issue in exc.issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1

    master_paths = sorted(args.master_dir.glob("*.csv"))
    plan_doc = plan_to_doc(
        plan,
        run_id=args.run,
        total=target,
        global_seed=run_config.get("global_seed"),
        config_hash=_sha256_of_paths([args.config]),
        master_data_hash=_sha256_of_paths(master_paths),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )

    run_dir = args.output_dir / args.run
    run_dir.mkdir(parents=True, exist_ok=True)
    plan_path = run_dir / "plan.json"
    tmp_path = run_dir / ".plan.json.tmp"
    tmp_path.write_text(json.dumps(plan_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(plan_path)

    province_totals: dict[str, int] = {}
    for c in plan:
        province_totals[c.province_id] = province_totals.get(c.province_id, 0) + c.target_count
    top_provinces = sorted(province_totals.items(), key=lambda kv: -kv[1])[:5]

    print("plan OK:")
    print(f"  cells: {len(plan)}")
    print(f"  total: {target}")
    print(f"  top provinces: {top_provinces}")
    print(f"  -> {plan_path}")
    return 0


def _code_version() -> str:
    """git commit hash hiện tại, fallback package version — dùng cho
    run_manifest.json#code_version (docs/output.md#run-manifest).
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parent,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("navtext")
    except PackageNotFoundError:
        return "unknown"


def _cmd_generate(args: argparse.Namespace) -> int:
    """Phase 7/10: plan + master + templates -> candidates/prototype csv.

    Đọc plan.json đã lưu (không build lại plan từ config) — đúng quy trình
    reproduce ở docs/testing.md#reproduce-một-run: `navtext generate --seed
    <global_seed> --plan <plan.json đã lưu>`.
    """
    plan_path = args.plan if args.plan is not None else args.output_dir / args.run / "plan.json"
    if not plan_path.exists():
        print(f"Không tìm thấy {plan_path} — chạy `navtext plan` trước.", file=sys.stderr)
        return 1
    if not args.master_dir.exists():
        print(
            f"Không tìm thấy {args.master_dir} — chạy `navtext build-master` trước.",
            file=sys.stderr,
        )
        return 1

    plan_doc = json.loads(plan_path.read_text(encoding="utf-8"))
    try:
        plan = plan_from_doc(plan_doc)
    except PlanError as exc:
        print(f"generate FAILED — không đọc được {plan_path}:", file=sys.stderr)
        for issue in exc.issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1

    with args.config.open(encoding="utf-8") as f:
        distribution_config: dict[str, Any] = yaml.safe_load(f)
    with args.run_config.open(encoding="utf-8") as f:
        run_config: dict[str, Any] = yaml.safe_load(f)

    if args.target is not None:
        current_total = sum(c.target_count for c in plan)
        if args.target != current_total:
            try:
                plan = scale_plan(plan, args.target)
            except PlanError as exc:
                print(f"generate FAILED — không scale được plan sang target={args.target}:", file=sys.stderr)
                for issue in exc.issues:
                    print(f"  - {issue}", file=sys.stderr)
                return 1

    try:
        variation_config = load_variation_config(args.variation_config)
    except VariationError as exc:
        print(f"generate FAILED — {args.variation_config} có {len(exc.issues)} lỗi:", file=sys.stderr)
        for issue in exc.issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1

    global_seed = args.seed if args.seed is not None else plan_doc.get("global_seed", run_config.get("global_seed"))
    if global_seed is None:
        print(
            "generate FAILED — thiếu global_seed (không có trong plan.json, --seed, hay config/run.yaml)",
            file=sys.stderr,
        )
        return 1

    try:
        ctx = build_context(
            master_dir=args.master_dir,
            template_dir=args.template_dir,
            distribution_config=distribution_config,
            variation_config=variation_config,
            global_seed=int(global_seed),
        )
    except TemplateError as exc:
        print(f"generate FAILED — {args.template_dir} có {len(exc.issues)} lỗi:", file=sys.stderr)
        for issue in exc.issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1

    counters: Counter[str] = Counter()
    samples = list(generate_all(plan, ctx, counters))

    run_dir = args.output_dir / args.run
    run_dir.mkdir(parents=True, exist_ok=True)
    out_path = run_dir / args.out
    tmp_out_path = run_dir / f".{args.out}.tmp"
    with tmp_out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for sample in samples:
            writer.writerow(sample.to_row())
    tmp_out_path.replace(out_path)

    cell_counts = {
        key.split("::", 1)[1]: count for key, count in counters.items() if key.startswith("generated::")
    }
    manifest = {
        "run_id": args.run,
        "global_seed": int(global_seed),
        "config_hash": _sha256_of_paths([args.config, args.variation_config, args.run_config]),
        "master_data_hash": _sha256_of_paths(sorted(args.master_dir.glob("*.csv"))),
        "template_bank_hash": _sha256_of_paths(sorted(args.template_dir.glob("*.yaml"))),
        "code_version": _code_version(),
        "cell_counts": cell_counts,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = run_dir / "run_manifest.json"
    tmp_manifest_path = run_dir / ".run_manifest.json.tmp"
    tmp_manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_manifest_path.replace(manifest_path)

    rejected = {k: v for k, v in counters.items() if not k.startswith("generated::")}
    print("generate OK:")
    print(f"  samples: {len(samples)}")
    print(f"  -> {out_path}")
    print(f"  -> {manifest_path}")
    if rejected:
        print("  rejects:")
        for key, count in sorted(rejected.items()):
            print(f"    {key}: {count}")
    return 0


def _cmd_qc(args: argparse.Namespace) -> int:
    """Phase 11: candidates -> validated candidates (tầng 1-2 QC)."""
    raise NotImplementedError


def _cmd_balance(args: argparse.Namespace) -> int:
    """Phase 11: measure/find_gaps/select_final -> clean.csv + gap report."""
    raise NotImplementedError


def _cmd_finalize(args: argparse.Namespace) -> int:
    """Phase 12: clean.csv -> final_100k.csv (đúng target size)."""
    raise NotImplementedError


def _cmd_stats(args: argparse.Namespace) -> int:
    """Phase 12: final -> reports/dataset_stats.{md,json}."""
    raise NotImplementedError


def _cmd_review_pack(args: argparse.Namespace) -> int:
    """Phase 8: prototype -> reports/prototype_review.md."""
    raise NotImplementedError


# Thứ tự ưu tiên file CSV trong một run dir khi tự động chọn cho /samples —
# giai đoạn QC càng sạch càng ưu tiên (final_100k đã qua toàn bộ QC/balance,
# prototype chỉ mới sinh thô ở phase 7). Không hardcode ở CLAUDE.md vì đây
# là thứ tự ưu tiên hiển thị, không phải một ngưỡng tỷ trọng (hard rule #8
# nói về threshold, không cấm hằng số tên file).
_SAMPLE_FILE_PRIORITY = ("final_100k.csv", "clean.csv", "candidates.csv", "prototype.csv")


def _discover_samples(output_dir: Path) -> Path | None:
    """Tìm CSV sample mới nhất trong output_dir/<run>/ để xem ở /samples khi
    người dùng không truyền --samples. Hàm thuần (không đọc nội dung CSV) —
    chỉ chọn ĐƯỜNG DẪN dựa trên mtime của run dir + tên file ưu tiên.

    Chọn run dir mới nhất theo mtime (không phải tên, vì run_id không đảm
    bảo sort đúng theo thời gian). Trong run dir đó, ưu tiên file theo
    _SAMPLE_FILE_PRIORITY — file càng sạch (đã qua QC/balance) càng được ưu
    tiên hơn candidate thô. Run dir không có CSV nào hợp lệ (vd chỉ có
    plan.json) bị bỏ qua, không làm hàm trả None sớm — thử run dir cũ hơn
    tiếp theo.
    """
    if not output_dir.is_dir():
        return None

    run_dirs = sorted(
        (d for d in output_dir.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    for run_dir in run_dirs:
        for filename in _SAMPLE_FILE_PRIORITY:
            candidate = run_dir / filename
            if candidate.is_file():
                return candidate
    return None


def _cmd_serve(args: argparse.Namespace) -> int:
    """Tool phụ trợ (không phải 1 trong 12 phase): web UI read-only đọc
    data/master/ — xem docs/web.md. Không sinh ra artifact nào của dataset.
    """
    if not args.master_dir.exists():
        print(
            f"Không tìm thấy {args.master_dir} — chạy `navtext build-master` trước.",
            file=sys.stderr,
        )
        return 1

    master = load_master(args.master_dir)
    with args.run_config.open(encoding="utf-8") as f:
        run_config = yaml.safe_load(f)
    report = build_master_report(master, run_config["admin_invariants"])

    # Bốn nguồn phụ dưới đây đều nạp theo kiểu "hỏng cái nào tắt cái đó":
    # server vẫn phải chạy để xem master data. Riêng TemplateError được giữ
    # lại và đẩy vào readiness — trang đó chính là chỗ hiển thị template
    # bank đang hỏng ở đâu, thay vì crash lúc khởi động.
    templates: tuple[Any, ...] = ()
    template_report = None
    # lexicon_raw: dict thô, dùng cho build_variation_report() (nhận
    # Mapping[str, Any] tuỳ chọn — xem stats.py). lexicon: bản typed
    # (navtext.templates.Lexicon) cho web/render.py — render_templates()
    # trước đây tự đọc shape thô của "openers" và crash khi schema đổi
    # sang list[{text, heads}] (phase 5); load_lexicon() là nơi DUY NHẤT
    # nên biết shape này (một nguồn sự thật), nên web dùng lại nó thay vì
    # đoán shape lần hai.
    lexicon_raw = None
    lexicon = None
    template_issues: list[str] = []
    try:
        loaded = load_templates(args.template_dir)
        templates = tuple(loaded)
        with (args.template_dir / "lexicon.yaml").open(encoding="utf-8") as f:
            lexicon_raw = yaml.safe_load(f)
        lexicon = load_lexicon(args.template_dir)
    except TemplateError as exc:
        template_issues = exc.issues
        print(f"CẢNH BÁO: template bank có {len(exc.issues)} lỗi — xem /readiness", file=sys.stderr)
    except (FileNotFoundError, NotADirectoryError):
        print(f"CẢNH BÁO: không đọc được {args.template_dir} — trang /templates sẽ trống", file=sys.stderr)
    except (ValueError, KeyError) as exc:
        print(f"CẢNH BÁO: {args.template_dir / 'lexicon.yaml'} có schema hỏng ({exc}) — trang /templates sẽ trống", file=sys.stderr)

    distribution_config = None
    try:
        with args.distribution_config.open(encoding="utf-8") as f:
            distribution_config = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"CẢNH BÁO: không đọc được {args.distribution_config} — trang /config sẽ trống", file=sys.stderr)

    if templates and distribution_config is not None:
        template_report = build_template_report(templates, distribution_config)

    raw_report = None
    try:
        # read_raw() ôm toàn bộ element OSM; chỉ giữ report đã tính, thả
        # RawData ngay sau đó để không nhân đôi bộ nhớ với MasterData.
        raw_report = build_raw_report(read_raw(args.raw_dir), master)
    except (FileNotFoundError, NotADirectoryError, KeyError):
        print(f"CẢNH BÁO: không đọc được {args.raw_dir} — trang /raw sẽ trống", file=sys.stderr)

    variation_config = None
    variation_report = None
    variation_issues: list[str] = []
    try:
        variation_config = load_variation_config(args.variation_config)
    except VariationError as exc:
        variation_issues = exc.issues
        print(f"CẢNH BÁO: config/variation.yaml có {len(exc.issues)} lỗi — xem /readiness", file=sys.stderr)
    except FileNotFoundError:
        print(
            f"CẢNH BÁO: không đọc được {args.variation_config} — trang /variation sẽ trống",
            file=sys.stderr,
        )

    if variation_config is not None:
        variation_report = build_variation_report(
            variation_config, lexicon_raw, distribution_config, template_report
        )

    readiness = build_readiness_report(
        master,
        report,
        template_report,
        distribution_config,
        template_issues,
        variation_issues,
        variation_report,
    )

    samples: tuple[dict[str, str], ...] = ()
    samples_summary = None
    samples_source: str | None = None
    samples_auto = False
    samples_path = args.samples
    if samples_path is None and not args.no_auto_samples:
        samples_path = _discover_samples(args.output_dir)
        samples_auto = samples_path is not None
    if samples_path is not None:
        try:
            with samples_path.open(encoding="utf-8", newline="") as f:
                samples = tuple(csv.DictReader(f))
            samples_summary = summarize_samples(samples)
            samples_source = str(samples_path)
            if samples_auto:
                print(f"navtext serve: tự động chọn {samples_path} cho /samples — override bằng --samples", file=sys.stderr)
        except FileNotFoundError:
            print(f"CẢNH BÁO: không đọc được {samples_path} — trang /samples sẽ trống", file=sys.stderr)
            samples_auto = False

    ctx = AppContext(
        master=master,
        report=report,
        templates=templates,
        template_report=template_report,
        lexicon=lexicon,
        distribution_config=distribution_config,
        raw_report=raw_report,
        readiness=readiness,
        variation_report=variation_report,
        variation_issues=tuple(variation_issues),
        samples=samples,
        samples_source=samples_source,
        samples_summary=samples_summary,
        samples_auto=samples_auto,
        # Hiện ở footer mọi trang: module Python được nạp một lần lúc này,
        # nên một server chạy từ trước khi sửa code vẫn phục vụ code cũ và
        # trông y hệt UI hỏng. Có mốc thời gian thì đối chiếu là biết ngay.
        started_at=datetime.now().strftime("%H:%M:%S %d/%m/%Y"),
    )

    server = make_server(ctx, host=args.host, port=args.port)
    host, port = server.server_address[0], server.server_address[1]
    print(f"navtext serve: http://{host}:{port}/ (Ctrl-C để thoát)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="navtext", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build-master", help="Build data/master/ từ data/raw/")
    p.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    p.add_argument("--master-dir", type=Path, default=Path("data/master"))
    p.set_defaults(func=_cmd_build_master)

    p = sub.add_parser("plan", help="Sinh plan table quota-driven")
    p.add_argument("--config", type=Path, default=Path("config/distribution.yaml"))
    p.add_argument("--run", type=str, required=True, help="run_id, ghi vào outputs/<run>/")
    p.add_argument("--master-dir", type=Path, default=Path("data/master"))
    p.add_argument("--run-config", type=Path, default=Path("config/run.yaml"))
    p.add_argument("--output-dir", type=Path, default=Path("outputs"))
    p.add_argument(
        "--target",
        type=int,
        default=None,
        help="Override target_size trong config (vd 1000 cho prototype) — tỷ trọng scale từ plan canonical",
    )
    p.set_defaults(func=_cmd_plan)

    p = sub.add_parser("generate", help="Sinh sample từ plan")
    p.add_argument("--run", type=str, required=True, help="run_id, đọc/ghi trong outputs/<run>/")
    p.add_argument(
        "--plan",
        type=Path,
        default=None,
        help="Đường dẫn plan.json (mặc định outputs/<run>/plan.json)",
    )
    p.add_argument("--master-dir", type=Path, default=Path("data/master"))
    p.add_argument("--template-dir", type=Path, default=Path("data/templates"))
    p.add_argument("--config", type=Path, default=Path("config/distribution.yaml"))
    p.add_argument("--variation-config", type=Path, default=Path("config/variation.yaml"))
    p.add_argument("--run-config", type=Path, default=Path("config/run.yaml"))
    p.add_argument("--output-dir", type=Path, default=Path("outputs"))
    p.add_argument(
        "--out",
        type=str,
        default="candidates.csv",
        help="Tên file CSV output trong outputs/<run>/ (vd prototype.csv ở phase 7)",
    )
    p.add_argument(
        "--target",
        type=int,
        default=None,
        help="Scale plan đã đọc sang tổng khác trước khi generate (giữ tỷ trọng, xem sampling.scale_plan)",
    )
    p.add_argument("--seed", type=int, default=None, help="override global_seed")
    p.set_defaults(func=_cmd_generate)

    p = sub.add_parser("review-pack", help="Đóng gói prototype để mentor review")
    p.add_argument("--run", type=str, required=True)
    p.set_defaults(func=_cmd_review_pack)

    p = sub.add_parser("qc", help="Chạy validator + dedup (tầng 1-2)")
    p.add_argument("--run", type=str, required=True)
    p.set_defaults(func=_cmd_qc)

    p = sub.add_parser("balance", help="Đo phân bố, tìm gap, chọn tập cuối")
    p.add_argument("--run", type=str, required=True)
    p.set_defaults(func=_cmd_balance)

    p = sub.add_parser("finalize", help="Cắt xuống đúng target size")
    p.add_argument("--run", type=str, required=True)
    p.set_defaults(func=_cmd_finalize)

    p = sub.add_parser("stats", help="Sinh báo cáo thống kê + coverage PASS/FAIL")
    p.add_argument("--run", type=str, required=True)
    p.set_defaults(func=_cmd_stats)

    p = sub.add_parser(
        "serve", help="Web UI read-only đọc data/master/ (tool phụ trợ, không phải 1 trong 12 phase)"
    )
    p.add_argument("--master-dir", type=Path, default=Path("data/master"))
    p.add_argument("--run-config", type=Path, default=Path("config/run.yaml"))
    p.add_argument("--template-dir", type=Path, default=Path("data/templates"))
    p.add_argument("--distribution-config", type=Path, default=Path("config/distribution.yaml"))
    p.add_argument("--variation-config", type=Path, default=Path("config/variation.yaml"))
    p.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    p.add_argument("--output-dir", type=Path, default=Path("outputs"))
    p.add_argument(
        "--samples",
        type=Path,
        default=None,
        help="CSV sample đã generate để xem ở /samples (vd outputs/<run>/prototype.csv)."
        " Không truyền thì tự tìm run mới nhất trong --output-dir"
        f" (ưu tiên: {', '.join(_SAMPLE_FILE_PRIORITY)}).",
    )
    p.add_argument(
        "--no-auto-samples",
        action="store_true",
        help="Tắt tự động tìm sample trong --output-dir — /samples chỉ nạp khi có --samples.",
    )
    p.add_argument("--host", type=str, default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
