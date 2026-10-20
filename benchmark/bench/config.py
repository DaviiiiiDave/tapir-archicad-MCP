"""Benchmark settings: bench.toml (committed) plus local.toml (machine paths, git-ignored)."""

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

LOCAL_PATH_KEYS = ("data_dir", "claude_exe", "base_project", "international_source", "repo_root")


@dataclass(frozen=True)
class ArmConfig:
    name: str
    ref: str
    change_log_flag: bool


@dataclass(frozen=True)
class BenchConfig:
    data_dir: Path
    claude_exe: Path
    base_project: Path
    international_source: Path
    repo_root: Path
    bench_port: int
    arms: dict[str, ArmConfig]
    models: dict[str, str]
    budgets_usd: dict[str, float]
    run_timeout_s: int
    runs_per_cell: int
    fixtures: dict
    oracle: dict


def _expand(value: str) -> Path:
    """Expand %VAR% (on every OS) and $VAR / ${VAR}."""
    value = re.sub(r"%([^%]+)%", lambda match: os.environ.get(match.group(1), match.group(0)), value)
    return Path(os.path.expandvars(value))


def load_config(bench_toml: Path, local_toml: Path) -> BenchConfig:
    if not local_toml.exists():
        raise FileNotFoundError(
            f"{local_toml} does not exist. Copy benchmark/local.example.toml to {local_toml} and fill in the paths."
        )
    bench = tomllib.loads(bench_toml.read_text(encoding="utf-8"))
    local = tomllib.loads(local_toml.read_text(encoding="utf-8"))

    missing = [key for key in LOCAL_PATH_KEYS if key not in local]
    if missing:
        raise ValueError(f"{local_toml} is missing: {', '.join(missing)}")

    models = dict(bench["models"])
    budgets = {alias: float(cap) for alias, cap in bench["budgets_usd"].items()}
    if set(budgets) != set(models):
        unknown = sorted(set(budgets) ^ set(models))
        raise ValueError(f"[models] and [budgets_usd] must name the same aliases; mismatched: {', '.join(unknown)}")

    arms = {
        name: ArmConfig(name=name, ref=str(arm["ref"]), change_log_flag=bool(arm["change_log_flag"]))
        for name, arm in bench["arms"].items()
    }
    return BenchConfig(
        **{key: _expand(local[key]) for key in LOCAL_PATH_KEYS},
        bench_port=int(bench["bench_port"]),
        arms=arms,
        models=models,
        budgets_usd=budgets,
        run_timeout_s=int(bench["run_timeout_s"]),
        runs_per_cell=int(bench["runs_per_cell"]),
        fixtures=dict(bench.get("fixtures", {})),
        oracle=dict(bench.get("oracle", {})),
    )
