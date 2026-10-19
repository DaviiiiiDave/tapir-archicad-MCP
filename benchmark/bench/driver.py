"""Run Claude Code headless (claude -p, stream-json) for one benchmark run."""

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from bench.arms import kill_tree, start_process
from bench.config import BenchConfig

# --setting-sources project keeps user hooks and plugins out, but not the user CLAUDE.md.
CLAUDE_MD_EXCLUDES = {"claudeMdExcludes": ["**/CLAUDE.md"]}
ALLOWED_TOOLS = "mcp__archicad"


@dataclass(frozen=True)
class ClaudeOutcome:
    exit_code: int
    timed_out: bool
    wall_s: float
    stream_path: Path
    pid: int


def build_claude_command(cfg: BenchConfig, model_alias: str, mcp_config: Path | None, system_prompt: str) -> list[str]:
    cmd = [
        str(cfg.claude_exe),
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        cfg.models[model_alias],
        "--max-budget-usd",
        f"{cfg.budgets_usd[model_alias]:.2f}",
        "--system-prompt",
        system_prompt,
        "--tools",
        "",
        "--setting-sources",
        "project",
        "--settings",
        json.dumps(CLAUDE_MD_EXCLUDES),
        "--permission-mode",
        "dontAsk",
        "--no-session-persistence",
        "--strict-mcp-config",
    ]
    if mcp_config is not None:
        cmd += ["--mcp-config", str(mcp_config), "--allowedTools", ALLOWED_TOOLS]
    return cmd


def write_mcp_config(path: Path, proxy_port: int) -> None:
    config = {"mcpServers": {"archicad": {"type": "http", "url": f"http://127.0.0.1:{proxy_port}/mcp"}}}
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")


def run_claude(cfg: BenchConfig, cmd: list[str], prompt: str, run_dir: Path, timeout_s: float) -> ClaudeOutcome:
    """Prompt on stdin, stdout to stream.jsonl, stderr to claude.stderr.log. On timeout the process tree is killed."""
    stream_path = run_dir / "stream.jsonl"
    started = time.monotonic()
    process = start_process(
        cmd,
        cwd=run_dir,
        env=os.environ.copy(),
        log_path=stream_path,
        stderr_path=run_dir / "claude.stderr.log",
        stdin=subprocess.PIPE,
    )
    process.stdin.write(prompt.encode("utf-8"))
    process.stdin.close()
    try:
        exit_code = process.wait(timeout=timeout_s)
        timed_out = False
    except subprocess.TimeoutExpired:
        kill_tree(process)
        exit_code = process.returncode
        timed_out = True
    return ClaudeOutcome(exit_code, timed_out, time.monotonic() - started, stream_path, process.pid)
