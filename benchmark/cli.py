"""Benchmark command line. Run from the repository root: python benchmark/cli.py <command>."""

import argparse
import json
import sys
from pathlib import Path

from bench.archicad import BenchArchicad
from bench.arms import dump_kinds, prepare_arm
from bench.config import load_config
from bench.preflight import run_preflight
from bench.report import build_setup, load_round, render_markdown, summarize
from bench.runner import plan_matrix, rescore_round, run_round
from bench.seed import seed_german, seed_international, seed_scale, verify
from bench.tasks import TASKS

SEEDERS = {"german": seed_german, "scale": seed_scale, "international": seed_international}

HERE = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python benchmark/cli.py")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup-arms", help="export every arm's ref and build its environment")
    commands.add_parser("preflight", help="prove that runs are isolated and every arm answers")
    seed = commands.add_parser("seed", help="seed a fixture on the Benchmark instance, or verify all seeded ones")
    seed.add_argument("fixture", nargs="?", choices=sorted(SEEDERS))
    seed.add_argument("--verify", action="store_true")
    run = commands.add_parser("run", help="run a round; runs that already have a verdict are skipped")
    run.add_argument("--round", required=True, dest="round_id")
    run.add_argument("--arms", help="comma separated, default: every arm")
    run.add_argument("--models", help="comma separated, default: every model")
    run.add_argument("--tasks", help="comma separated, default: T1..T5")
    run.add_argument("--runs", type=int, help="runs per cell, default: runs_per_cell of bench.toml")
    rescore = commands.add_parser("rescore", help="score a finished round again from its stored runs")
    rescore.add_argument("--round", required=True, dest="round_id")
    report = commands.add_parser("report", help="write benchmark/results/<round>.md and .json from the verdicts")
    report.add_argument("--round", required=True, dest="round_id")
    args = parser.parse_args(argv)
    cfg = load_config(HERE / "bench.toml", HERE / "local.toml")

    if args.command == "setup-arms":
        for name in cfg.arms:
            print(f"arm {name}: {prepare_arm(cfg, name)}")
        print(f"kinds: {dump_kinds(cfg)}")
        return 0

    if args.command == "seed":
        ac = BenchArchicad(cfg.bench_port, cfg.bench_port, cfg.data_dir, cfg.base_project)
        if not args.verify:
            print(f"manifest: {SEEDERS[args.fixture](ac, cfg)}")
            return 0
        failed = False
        for name in SEEDERS:
            path = cfg.data_dir / "fixtures" / f"{name}.manifest.json"
            if path.exists():
                problems = verify(ac, cfg, path)
                failed |= bool(problems)
                print(f"{name}: {'verify ok' if not problems else f'{len(problems)} problems'}")
                for problem in problems[:20]:
                    print(f"  - {problem}")
        return 1 if failed else 0

    if args.command == "run":
        specs = plan_matrix(
            args.tasks.split(",") if args.tasks else list(TASKS),
            args.arms.split(",") if args.arms else list(cfg.arms),
            args.models.split(",") if args.models else list(cfg.models),
            args.runs or cfg.runs_per_cell,
            args.round_id,
        )
        result = run_round(cfg, args.round_id, specs)
        print(f"done {len(result.done)}, skipped {len(result.skipped)}, stopped: {result.stopped}")
        return 1 if result.stopped else 0

    if args.command == "rescore":
        print(f"{rescore_round(cfg, args.round_id)} verdicts changed")
        return 0

    if args.command == "report":
        records = load_round(cfg.data_dir / "runs" / args.round_id)
        summary, setup = summarize(records), build_setup(cfg, args.round_id)
        results = HERE / "results"
        (results / f"{args.round_id}.md").write_text(render_markdown(summary, setup), encoding="utf-8")
        (results / f"{args.round_id}.json").write_text(
            json.dumps({"setup": setup, "rows": summary}, indent=1, ensure_ascii=False), encoding="utf-8"
        )
        print(f"{len(records)} verdicts -> {results / (args.round_id + '.md')}")
        return 0

    path = run_preflight(cfg)
    report = json.loads(path.read_text(encoding="utf-8"))
    print(f"report: {path}")
    print(f"ok: {report['ok']}")
    for problem in report["problems"]:
        print(f"  - {problem}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
