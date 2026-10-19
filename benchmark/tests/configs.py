from pathlib import Path

from bench.config import ArmConfig, BenchConfig


def make_config(tmp_path: Path) -> BenchConfig:
    return BenchConfig(
        data_dir=tmp_path / "data",
        claude_exe=tmp_path / "claude.exe",
        base_project=tmp_path / "Benchmark.pln",
        international_source=tmp_path / "TestProject.pla",
        repo_root=tmp_path / "repo",
        bench_port=19725,
        arms={
            "A": ArmConfig(name="A", ref="v0.6.5", change_log_flag=False),
            "B": ArmConfig(name="B", ref="d7a8a67", change_log_flag=True),
        },
        models={"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5"},
        budgets_usd={"haiku": 2.0, "sonnet": 6.0},
        run_timeout_s=1800,
        runs_per_cell=3,
        fixtures={},
        oracle={},
    )
