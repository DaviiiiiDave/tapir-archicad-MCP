"""Benchmark command line. Run from the repository root: python benchmark/cli.py <command>."""

import argparse
import json
import sys
from pathlib import Path

from bench.archicad import BenchArchicad
from bench.arms import dump_kinds, prepare_arm
from bench.config import load_config
from bench.preflight import run_preflight
from bench.seed import seed_german, seed_scale, verify

SEEDERS = {"german": seed_german, "scale": seed_scale}

HERE = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python benchmark/cli.py")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup-arms", help="export every arm's ref and build its environment")
    commands.add_parser("preflight", help="prove that runs are isolated and every arm answers")
    seed = commands.add_parser("seed", help="seed a fixture on the Benchmark instance, or verify all seeded ones")
    seed.add_argument("fixture", nargs="?", choices=sorted(SEEDERS))
    seed.add_argument("--verify", action="store_true")
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

    path = run_preflight(cfg)
    report = json.loads(path.read_text(encoding="utf-8"))
    print(f"report: {path}")
    print(f"ok: {report['ok']}")
    for problem in report["problems"]:
        print(f"  - {problem}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
