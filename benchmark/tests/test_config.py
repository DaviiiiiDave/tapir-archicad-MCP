from pathlib import Path

import pytest
from bench.config import load_config

BENCH_TOML = """
bench_port = 19725
runs_per_cell = 3
run_timeout_s = 1800

[models]
haiku = "claude-haiku-5-5"

[budgets_usd]
haiku = 2.0

[arms.A]
ref = "v0.6.5"
change_log_flag = false
"""

LOCAL_TOML = """
data_dir = "%BENCH_TEST_ROOT%/data"
claude_exe = "$BENCH_TEST_ROOT/claude.exe"
base_project = "%BENCH_TEST_ROOT%/Benchmark.pln"
international_source = "%BENCH_TEST_ROOT%/TestProject.pla"
repo_root = "%BENCH_TEST_ROOT%/repo"
"""


def write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_paths_expand_environment_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCH_TEST_ROOT", "C:/bench-root")

    cfg = load_config(write(tmp_path / "bench.toml", BENCH_TOML), write(tmp_path / "local.toml", LOCAL_TOML))

    assert cfg.data_dir == Path("C:/bench-root/data")
    assert cfg.claude_exe == Path("C:/bench-root/claude.exe")


def test_budget_for_unknown_model_alias_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCH_TEST_ROOT", "C:/bench-root")
    bench = BENCH_TOML.replace("haiku = 2.0", "haiku = 2.0\nopus = 9.0")

    with pytest.raises(ValueError, match="opus"):
        load_config(write(tmp_path / "bench.toml", bench), write(tmp_path / "local.toml", LOCAL_TOML))


def test_missing_local_toml_names_the_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="local.example.toml"):
        load_config(write(tmp_path / "bench.toml", BENCH_TOML), tmp_path / "local.toml")


def test_committed_bench_toml_loads(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCH_TEST_ROOT", "C:/bench-root")

    cfg = load_config(Path(__file__).parents[1] / "bench.toml", write(tmp_path / "local.toml", LOCAL_TOML))

    assert 19723 <= cfg.bench_port < 19744
    assert set(cfg.models) == set(cfg.budgets_usd) == {"haiku", "sonnet"}
    assert {name: (arm.ref, arm.change_log_flag) for name, arm in cfg.arms.items()} == {
        "A": ("v0.6.5", False),
        "B": ("d7a8a67", True),
    }
