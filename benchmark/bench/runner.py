"""Run the benchmark: one run in a fresh project copy, and a round of runs that survives a usage limit."""

import itertools
import json
import os
import re
import shutil
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from bench.archicad import BenchArchicad
from bench.arms import free_port, kill_tree, server_command, start_process, wait_for_port
from bench.config import BenchConfig
from bench.driver import ClaudeOutcome, build_claude_command, run_claude, write_mcp_config
from bench.oracle import door_like_elements, door_owners, elements_of_type, snapshot_property
from bench.proxy import ProxyRules, create_proxy_app, serve_in_thread
from bench.stream_metrics import RunMetrics, parse_stream
from bench.tasks import TASKS, TaskDef, Verdict, parse_answer, score, system_prompt

# "allowed_warning" only says the limit is near; the run itself went through.
_OPEN_STATUSES = frozenset({None, "allowed", "allowed_warning"})
_LIMIT_TEXT = re.compile(r"usage limit", re.IGNORECASE)
_RESET_EPOCH = re.compile(r"\|(\d{10})\b")


@dataclass(frozen=True)
class RunSpec:
    round_id: str
    task_id: str
    arm: str
    model: str
    index: int

    @property
    def run_id(self) -> str:
        return f"{self.task_id}-{self.arm}-{self.model}-{self.index}"


def plan_matrix(
    task_ids: list[str], arms: list[str], models: list[str], runs: int, round_id: str = ""
) -> list[RunSpec]:
    """Index, then task, then model, then arm: the arms of one cell run next to each other."""
    return [
        RunSpec(round_id, task, arm, model, index)
        for index, task, model, arm in itertools.product(range(1, runs + 1), task_ids, models, arms)
    ]


class UsageLimitReached(Exception):
    def __init__(self, status: str | None, message: str, resets_at: str | None) -> None:
        super().__init__(f"usage limit reached ({status}): {message}")
        self.status = status
        self.message = message
        self.resets_at = resets_at


def usage_limit_reached(metrics: RunMetrics) -> UsageLimitReached | None:
    """The limit the run ran into, if any. The reset time is whatever the message carries."""
    if metrics.rate_limit_status in _OPEN_STATUSES and not _LIMIT_TEXT.search(metrics.result_text):
        return None
    epoch = _RESET_EPOCH.search(metrics.result_text)
    resets_at = datetime.fromtimestamp(int(epoch[1]), UTC).isoformat() if epoch else None
    return UsageLimitReached(metrics.rate_limit_status, metrics.result_text, resets_at)


def _states(items: dict) -> dict[str, list]:
    return {guid: list(item) for guid, item in items.items()}


def snapshot(ac, task: TaskDef, manifest: dict, rule: dict, like: dict | None = None) -> dict:
    """The oracle's view of what the task can change. Wall tasks look at the walls of `like` when it is given,
    so a deleted wall shows up as missing. The shapes are documented in bench.tasks."""
    prop = manifest["property"]["id"]
    if task.id in ("T1", "T3"):
        return {"doors": _states(snapshot_property(ac, door_like_elements(ac, rule), prop))}
    if task.id == "T5":
        doors = elements_of_type(ac, "Door")
        return {"doors": door_owners(ac, doors) if doors else {}}
    if like is not None:
        walls = list(like["walls"])
    elif task.id == "T2":
        walls = manifest["t2_rule_set"] + manifest["t2_other"]
    else:
        walls = elements_of_type(ac, "Wall")
    return {"walls": _states(snapshot_property(ac, walls, prop))}


def make_probe(ac, task: TaskDef, manifest: dict, before: dict, rule: dict) -> Callable[[str, dict], dict]:
    """The proxy's out-of-band probe: what differs from `before` right now."""
    lock = threading.Lock()

    def probe(_phase: str, _entry: dict) -> dict:
        with lock:
            current = snapshot(ac, task, manifest, rule, like=before)
        if task.probe == "t5_doors":
            return {"new_doors": {d: owner for d, owner in current["doors"].items() if d not in before["doors"]}}
        return {"changed": {g: v for g, v in current["walls"].items() if before["walls"].get(g) != v}}

    return probe


def run_record(
    spec: RunSpec, verdict: Verdict, metrics: RunMetrics, outcome: ClaudeOutcome, ledger: list[dict]
) -> dict:
    probe_ms = sum(entry.get("probe_ms") or 0.0 for entry in ledger)
    return {
        "run_id": spec.run_id,
        "task": spec.task_id,
        "arm": spec.arm,
        "model": spec.model,
        "index": spec.index,
        **{key: value for key, value in asdict(verdict).items()},
        "tokens": {
            "input": metrics.input_tokens,
            "output": metrics.output_tokens,
            "cache_read": metrics.cache_read_tokens,
            "cache_creation": metrics.cache_creation_tokens,
            "total": metrics.total_tokens,
        },
        "cost_usd": metrics.cost_usd,
        "turns": metrics.turns,
        "calls": len(metrics.tool_calls),
        "tool_errors": _tool_errors(metrics),
        "tool_result_kb": metrics.tool_result_bytes / 1024,
        "peak_context_tokens": metrics.peak_context_tokens,
        "context_window": metrics.context_window,
        "result_subtype": metrics.result_subtype,
        "timed_out": outcome.timed_out,
        "wall_s": outcome.wall_s,
        "probe_ms": probe_ms,
        "wall_net_s": outcome.wall_s - probe_ms / 1000,
        "ledger_calls": len(ledger),
    }


def _tool_errors(metrics: RunMetrics) -> dict[str, int]:
    return dict(Counter(call.error_class for call in metrics.tool_calls if call.error_class))


def reclassify_tool_errors(round_dir: Path) -> int:
    """Re-derive `tool_errors` of every verdict from its stream, after the error classes changed. Nothing else in
    the verdict is touched. Returns how many verdicts changed."""
    changed = 0
    for run_dir in sorted(Path(round_dir).iterdir()):
        if not run_dir.is_dir() or ".aborted" in run_dir.name or not (run_dir / "verdict.json").exists():
            continue
        record = json.loads((run_dir / "verdict.json").read_text(encoding="utf-8"))
        metrics = parse_stream((run_dir / "stream.jsonl").read_text(encoding="utf-8").splitlines())
        if record["tool_errors"] != _tool_errors(metrics):
            record["tool_errors"] = _tool_errors(metrics)
            _write_json(run_dir / "verdict.json", record)
            changed += 1
    return changed


def fresh_run_dir(data_dir: Path, spec: RunSpec) -> Path:
    """An empty run directory. An earlier attempt that never reached a verdict is kept next to it, set aside."""
    run_dir = data_dir / "runs" / spec.round_id / spec.run_id
    if run_dir.exists():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        run_dir.rename(run_dir.with_name(f"{spec.run_id}.aborted-{stamp}"))
    run_dir.mkdir(parents=True)
    return run_dir


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str), encoding="utf-8")


def run_one(cfg: BenchConfig, spec: RunSpec) -> Verdict:
    task = TASKS[spec.task_id]
    run_dir = fresh_run_dir(cfg.data_dir, spec)
    manifest = json.loads((cfg.data_dir / "fixtures" / f"{task.fixture}.manifest.json").read_text(encoding="utf-8"))
    rule = cfg.oracle[manifest.get("oracle", "german")]
    kinds = json.loads((cfg.data_dir / "kinds.json").read_text(encoding="utf-8"))

    ac = BenchArchicad(cfg.bench_port, cfg.bench_port, cfg.data_dir, cfg.base_project)
    project = run_dir / "project.pln"
    shutil.copy2(cfg.data_dir / "fixtures" / f"{task.fixture}.pln", project)
    ac.open_project(project)
    before = snapshot(ac, task, manifest, rule)
    _write_json(run_dir / "before.json", before)

    server_port, proxy_port = free_port(), free_port()
    ledger_path = run_dir / "ledger.jsonl"
    server = start_process(
        server_command(cfg, spec.arm, server_port, run_dir), run_dir, os.environ.copy(), run_dir / "server.log"
    )
    try:
        wait_for_port(server_port, timeout_s=60)
        app = create_proxy_app(
            f"http://127.0.0.1:{server_port}",
            ProxyRules(cfg.bench_port, kinds, task.drop_first_response_of),
            ledger_path,
            make_probe(ac, task, manifest, before, rule) if task.probe else None,
        )
        proxy = serve_in_thread(app, proxy_port)
        try:
            write_mcp_config(run_dir / "mcp.json", proxy_port)
            command = build_claude_command(cfg, spec.model, run_dir / "mcp.json", system_prompt(cfg.bench_port))
            outcome = run_claude(cfg, command, task.prompt, run_dir, cfg.run_timeout_s)
        finally:
            proxy.stop()
    finally:
        kill_tree(server)

    metrics = parse_stream(outcome.stream_path.read_text(encoding="utf-8").splitlines())
    if (limit := usage_limit_reached(metrics)) is not None:
        raise limit
    after = snapshot(ac, task, manifest, rule, like=before)
    _write_json(run_dir / "after.json", after)
    ac.save_project()

    ledger = (
        [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
        if ledger_path.exists()
        else []
    )
    answer = parse_answer(metrics.result_text, task.answer_lang)
    verdict = score(spec.task_id, answer, manifest, before, after, ledger, metrics, outcome)
    _write_json(run_dir / "metrics.json", asdict(metrics))
    _write_json(run_dir / "verdict.json", run_record(spec, verdict, metrics, outcome, ledger))
    return verdict


@dataclass
class RoundResult:
    done: list[str]
    skipped: list[str]
    stopped: dict | None


def run_round(
    cfg: BenchConfig,
    round_id: str,
    specs: list[RunSpec],
    run_one: Callable[[BenchConfig, RunSpec], Verdict] = run_one,
) -> RoundResult:
    """Run every spec that has no verdict yet. A usage limit stops the round with stopped.json and no verdict for
    the run it hit; calling again continues with that run."""
    round_dir = cfg.data_dir / "runs" / round_id
    round_dir.mkdir(parents=True, exist_ok=True)
    (round_dir / "stopped.json").unlink(missing_ok=True)
    result = RoundResult(done=[], skipped=[], stopped=None)
    for position, spec in enumerate(specs, start=1):
        if (round_dir / spec.run_id / "verdict.json").exists():
            result.skipped.append(spec.run_id)
            continue
        try:
            verdict = run_one(cfg, spec)
        except UsageLimitReached as limit:
            result.stopped = {
                "time": datetime.now(UTC).isoformat(),
                "run_id": spec.run_id,
                "status": limit.status,
                "message": limit.message,
                "resets_at": limit.resets_at,
            }
            _write_json(round_dir / "stopped.json", result.stopped)
            print(f"[{position}/{len(specs)}] {spec.run_id}: usage limit reached, resets at {limit.resets_at}")
            return result
        result.done.append(spec.run_id)
        print(f"[{position}/{len(specs)}] {spec.run_id}: {'ok' if verdict.success else 'FAILED'} {verdict.reasons[:2]}")
    return result
