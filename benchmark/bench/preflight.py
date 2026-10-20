"""Pre-flight: prove that benchmark runs see nothing but the system prompt, the task and the arm's tools."""

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from bench.arms import arm_dir, free_port, kill_tree, server_command, start_process, wait_for_port
from bench.config import BenchConfig
from bench.driver import build_claude_command, run_claude, write_mcp_config
from bench.proxy import ProxyRules, create_proxy_app, serve_in_thread
from bench.stream_metrics import RunMetrics, parse_stream

CANARY_SYSTEM = "You are a test harness."
CANARY_PROMPT = "Reply with OK."
CONTEXT_PROBE = (
    "List every section heading or file name that appears in your context outside this message and the system "
    "prompt (for example CLAUDE.md files, memory indexes, skill lists, reminders). Quote each heading verbatim, "
    "one per line. If there is none, reply NONE."
)
MCP_CANARY = "Call discovery_list_active_archicads once and reply with the number of active instances."
# Claude Code always adds the current date as a reminder; it is the same for every run.
ALLOWED_CONTEXT_LINES = frozenset({"# currentDate"})


def check_init(metrics: RunMetrics, expect_server: bool, expected_model: str) -> list[str]:
    problems = []
    if metrics.hook_events:
        problems.append(f"hooks ran: {', '.join(metrics.hook_events)}")
    init = metrics.init
    if not init:
        return [*problems, "no init event in the stream"]
    if init.get("plugins"):
        problems.append(f"plugins loaded: {', '.join(p.get('name', '?') for p in init['plugins'])}")
    foreign = [tool for tool in init.get("tools", []) if not tool.startswith("mcp__archicad__")]
    if foreign:
        problems.append(f"tools outside mcp__archicad__: {', '.join(foreign)}")
    servers = [(s.get("name"), s.get("status")) for s in init.get("mcp_servers", [])]
    if expect_server and servers != [("archicad", "connected")]:
        problems.append(f"expected exactly one connected archicad server, got {servers}")
    if not expect_server and servers:
        problems.append(f"unexpected MCP servers: {servers}")
    if init.get("model") != expected_model:
        problems.append(f"model {init.get('model')!r} instead of {expected_model!r}")
    if metrics.permission_denials:
        problems.append(f"permission denials: {metrics.permission_denials}")
    return problems


def context_probe_ok(answer: str) -> bool:
    found = [line.strip() for line in answer.splitlines() if line.strip()]
    return found == ["NONE"] or (bool(found) and set(found) <= ALLOWED_CONTEXT_LINES)


def _run(cfg: BenchConfig, run_dir: Path, alias: str, prompt: str, mcp_config: Path | None) -> RunMetrics:
    run_dir.mkdir(parents=True, exist_ok=True)
    cmd = build_claude_command(cfg, alias, mcp_config, system_prompt=CANARY_SYSTEM)
    outcome = run_claude(cfg, cmd, prompt, run_dir, timeout_s=cfg.run_timeout_s)
    return parse_stream(outcome.stream_path.read_text(encoding="utf-8").splitlines())


def _tokens(metrics: RunMetrics) -> dict:
    return {
        "input": metrics.input_tokens,
        "cache_read": metrics.cache_read_tokens,
        "cache_creation": metrics.cache_creation_tokens,
        "output": metrics.output_tokens,
        "total": metrics.total_tokens,
    }


def run_preflight(cfg: BenchConfig) -> Path:
    """Run the canaries, write data_dir/preflight/<stamp>/report.json and return its path."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base = cfg.data_dir / "preflight" / stamp
    kinds = json.loads((cfg.data_dir / "kinds.json").read_text(encoding="utf-8"))
    cli_version = subprocess.run(
        [str(cfg.claude_exe), "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    report = {"time": stamp, "cli_version": cli_version, "models": {}, "arms": {}, "problems": []}

    for alias, model_id in cfg.models.items():
        canary = _run(cfg, base / f"canary-{alias}", alias, CANARY_PROMPT, None)
        probe = _run(cfg, base / f"context-{alias}", alias, CONTEXT_PROBE, None)
        problems = check_init(canary, expect_server=False, expected_model=model_id)
        problems += check_init(probe, expect_server=False, expected_model=model_id)
        if not context_probe_ok(probe.result_text):
            problems.append(f"context probe saw: {probe.result_text!r}")
        report["models"][alias] = {
            "id": model_id,
            "baseline_tokens": _tokens(canary),
            "context_probe": probe.result_text,
        }
        report["problems"] += [f"{alias}: {problem}" for problem in problems]

    for arm in cfg.arms:
        sha = (arm_dir(cfg, arm) / "ref.txt").read_text(encoding="utf-8").strip()
        report["arms"][arm] = {"ref": cfg.arms[arm].ref, "sha": sha, "models": {}}
        for alias, model_id in cfg.models.items():
            run_dir = base / f"mcp-{arm}-{alias}"
            run_dir.mkdir(parents=True, exist_ok=True)
            server_port, proxy_port = free_port(), free_port()
            ledger = run_dir / "ledger.jsonl"
            server = start_process(
                server_command(cfg, arm, server_port, run_dir), run_dir, os.environ.copy(), run_dir / "server.log"
            )
            try:
                wait_for_port(server_port, timeout_s=60)
                app = create_proxy_app(f"http://127.0.0.1:{server_port}", ProxyRules(cfg.bench_port, kinds), ledger)
                proxy = serve_in_thread(app, proxy_port)
                try:
                    write_mcp_config(run_dir / "mcp.json", proxy_port)
                    metrics = _run(cfg, run_dir, alias, MCP_CANARY, run_dir / "mcp.json")
                finally:
                    proxy.stop()
            finally:
                kill_tree(server)
            problems = check_init(metrics, expect_server=True, expected_model=model_id)
            tools = (
                [json.loads(line)["tool"] for line in ledger.read_text(encoding="utf-8").splitlines()]
                if ledger.exists()
                else []
            )
            if "discovery_list_active_archicads" not in tools:
                problems.append(f"the proxy saw no discovery call (saw {tools})")
            report["arms"][arm]["models"][alias] = {
                "tokens": _tokens(metrics),
                "calls": tools,
                "answer": metrics.result_text,
            }
            report["problems"] += [f"{arm}/{alias}: {problem}" for problem in problems]

    report["ok"] = not report["problems"]
    path = base / "report.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
