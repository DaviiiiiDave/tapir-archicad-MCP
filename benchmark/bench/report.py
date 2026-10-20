"""Turn the verdicts of a round into the baseline report: summary rows, a paired bootstrap, markdown."""

import json
import random
import statistics
import subprocess
from collections import Counter
from pathlib import Path

from bench.arms import arm_dir
from bench.config import BenchConfig

SMALL_TASKS = ("T3", "T4", "T5")
SMALL_TASK_LIMIT = 1.10
_MEDIAN_KEYS = ("total", "input", "cache_read", "cache_creation", "output")
_TASK_ORDER = ("T1", "T2", "T3", "T4", "T5")
_NEEDS_BOTH_ARMS = "No cell has both arms yet."


def load_round(round_dir: Path) -> list[dict]:
    """Every verdict of the round. Attempts that were set aside after a stop have no place in the numbers."""
    return [
        json.loads((run_dir / "verdict.json").read_text(encoding="utf-8"))
        for run_dir in sorted(Path(round_dir).iterdir())
        if run_dir.is_dir() and ".aborted" not in run_dir.name and (run_dir / "verdict.json").exists()
    ]


def summarize(records: list[dict]) -> list[dict]:
    """One row per task x arm x model."""
    cells: dict[tuple, list[dict]] = {}
    for rec in records:
        cells.setdefault((rec["task"], rec["arm"], rec["model"]), []).append(rec)
    rows = []
    for (task, arm, model), runs in sorted(cells.items()):
        median = {key: statistics.median(r["tokens"][key] for r in runs) for key in _MEDIAN_KEYS}
        for key in ("cost_usd", "calls", "tool_result_kb", "peak_context_tokens", "wall_net_s"):
            median[key] = statistics.median(r[key] for r in runs)
        separated = [r["extras"]["causes_separated"] for r in runs if "causes_separated" in r["extras"]]
        rows.append(
            {
                "task": task,
                "arm": arm,
                "model": model,
                "n": len(runs),
                "successes": sum(r["success"] for r in runs),
                "silent_errors": sum(r["silent_error"] for r in runs),
                "false_claims": sum(r["false_claim"] for r in runs),
                "unsafe_writes": sum(r["unsafe_write"] for r in runs),
                "median": median,
                "tool_errors": dict(sum((Counter(r["tool_errors"]) for r in runs), Counter())),
                "causes_separated": (sum(separated), len(separated)) if separated else None,
                "budget_stops": sum(r["result_subtype"] == "error_max_budget_usd" for r in runs),
                "timeouts": sum(r["timed_out"] for r in runs),
                "per_index_total": {r["index"]: r["tokens"]["total"] for r in runs},
            }
        )
    return rows


def paired_bootstrap_ci(
    pairs: list[tuple[float, float]], resamples: int = 10_000, seed: int = 0
) -> tuple[float, float]:
    """95% interval of the mean of (second - first) over matched pairs."""
    if not pairs:
        raise ValueError("a bootstrap needs pairs")
    differences = [second - first for first, second in pairs]
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(differences, k=len(differences))) for _ in range(resamples))
    return means[int(0.025 * (resamples - 1))], means[int(0.975 * (resamples - 1))]


def _tokens(value: float) -> str:
    return f"{value:,.0f}"


def _cell(rows: list[dict], task: str, arm: str, model: str) -> dict | None:
    return next((r for r in rows if (r["task"], r["arm"], r["model"]) == (task, arm, model)), None)


def _setup_section(setup: dict) -> list[str]:
    lines = ["## Setup", ""]
    for arm, info in setup["arms"].items():
        lines.append(f"- Arm {arm}: ref `{info['ref']}`, commit `{info['sha']}`")
    lines += [f"- Model {alias}: `{model}`" for alias, model in setup["models"].items()]
    lines.append(f"- Claude Code CLI {setup['cli_version']}")
    lines += [f"- Fixture {name}: SHA256 `{sha}`" for name, sha in setup["fixtures"].items()]
    lines.append(
        "- Pre-flight baseline (a run with no tools, no task): "
        + ", ".join(f"{alias} {tokens} tokens" for alias, tokens in setup["baseline_tokens"].items())
    )
    lines.append(
        "- Cost: the CLI's API-equivalent estimate. The runs use a Claude Code subscription, so it is not a bill."
    )
    lines.append(
        "- Limit: Archicad runs in German here, so built-in property display names are only ever seen in German."
    )
    return lines


def _task_section(rows: list[dict], task: str) -> list[str]:
    cells = [r for r in rows if r["task"] == task]
    if not cells:
        return []
    lines = [
        f"### {task}",
        "",
        (
            "| arm | model | success | wrong answers | false claims | unsafe writes | total tokens | uncached in | cache read | "
            "cache write | out | cost USD | calls | tool result KB | peak context | wall s (net) |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    notes = []
    for r in cells:
        m = r["median"]
        lines.append(
            f"| {r['arm']} | {r['model']} | {r['successes']}/{r['n']} | {r['silent_errors']} | {r['false_claims']} | {r['unsafe_writes']} "
            f"| {_tokens(m['total'])} | {_tokens(m['input'])} | {_tokens(m['cache_read'])} "
            f"| {_tokens(m['cache_creation'])} | {_tokens(m['output'])} | {m['cost_usd']:.3f} | {m['calls']:g} "
            f"| {m['tool_result_kb']:.1f} | {_tokens(m['peak_context_tokens'])} | {m['wall_net_s']:.0f} |"
        )
        if r["tool_errors"]:
            notes.append(
                f"- {r['arm']}/{r['model']} tool errors: "
                + ", ".join(f"{k} {v}" for k, v in sorted(r["tool_errors"].items()))
            )
        if r["causes_separated"]:
            separated, total = r["causes_separated"]
            notes.append(f"- {r['arm']}/{r['model']} causes separated {separated}/{total} (reported, not scored)")
    return [*lines, "", *notes, ""] if notes else [*lines, ""]


def _ratio_section(rows: list[dict]) -> list[str]:
    lines = [
        "## B/A ratios",
        "",
        (
            "Median total tokens and cost, B divided by A. The interval is a paired bootstrap of the mean token "
            "difference B - A over the runs with the same index."
        ),
        "",
    ]
    table = []
    for task in _TASK_ORDER:
        for model in sorted({r["model"] for r in rows}):
            a, b = _cell(rows, task, "A", model), _cell(rows, task, "B", model)
            if not a or not b:
                continue
            pairs = [
                (float(a["per_index_total"][i]), float(b["per_index_total"][i]))
                for i in sorted(a["per_index_total"].keys() & b["per_index_total"].keys())
            ]
            interval = "n/a"
            if pairs:
                low, high = paired_bootstrap_ci(pairs)
                mean = statistics.fmean(second - first for first, second in pairs)
                interval = f"{mean:+,.0f} [{low:+,.0f}, {high:+,.0f}]"
            table.append(
                f"| {task} | {model} | {b['median']['total'] / a['median']['total']:.2f} "
                f"| {b['median']['cost_usd'] / a['median']['cost_usd']:.2f} | {interval} | {len(pairs)} |"
            )
    if not table:
        return [*lines, _NEEDS_BOTH_ARMS, ""]
    header = [
        "| task | model | tokens B/A | cost B/A | mean diff B - A (95% CI) | pairs |",
        "|---|---|---|---|---|---|",
    ]
    return [*lines, *header, *table, ""]


def _small_task_section(rows: list[dict]) -> list[str]:
    lines = [f"## Small-task check (B tokens at most {SMALL_TASK_LIMIT:.2f} x A on {', '.join(SMALL_TASKS)})", ""]
    for task in SMALL_TASKS:
        for model in sorted({r["model"] for r in rows}):
            a, b = _cell(rows, task, "A", model), _cell(rows, task, "B", model)
            if not a or not b:
                continue
            ratio = b["median"]["total"] / a["median"]["total"]
            verdict = "REGRESSION" if ratio > SMALL_TASK_LIMIT else "ok"
            lines.append(f"- {task} / {model}: B/A {ratio:.2f} - {verdict}")
    return [*lines, *([] if len(lines) > 2 else [_NEEDS_BOTH_ARMS]), ""]


def render_markdown(summary: list[dict], setup: dict) -> str:
    lines = [f"# Benchmark round {setup['round_id']}", "", *_setup_section(setup), "", "## Results per task", ""]
    for task in _TASK_ORDER:
        lines += _task_section(summary, task)
    lines += _ratio_section(summary)
    lines += [
        "## Spec section 7 thresholds, C vs A",
        "",
        "pending: arm C not built",
        "",
        *_small_task_section(summary),
        "## Infrastructure notes",
        "",
        f"- Budget stops: {sum(r['budget_stops'] for r in summary)}",
        f"- Timeouts: {sum(r['timeouts'] for r in summary)}",
        f"- Attempts set aside after a usage-limit stop: {setup.get('aborted_attempts', 0)}",
    ]
    if setup.get("stopped"):
        lines.append(f"- Round stopped: {setup['stopped']}")
    return "\n".join(lines) + "\n"


def build_setup(cfg: BenchConfig, round_id: str) -> dict:
    """What the report states about this machine and these inputs, read from disk."""
    round_dir = cfg.data_dir / "runs" / round_id
    preflights = sorted((cfg.data_dir / "preflight").glob("*/report.json"))
    baseline = (
        {
            alias: info["baseline_tokens"]["total"]
            for alias, info in json.loads(preflights[-1].read_text())["models"].items()
        }
        if preflights
        else {}
    )
    stopped = round_dir / "stopped.json"
    return {
        "round_id": round_id,
        "arms": {
            name: {
                "ref": arm.ref,
                "sha": (arm_dir(cfg, name) / "ref.txt").read_text(encoding="utf-8").strip(),
            }
            for name, arm in cfg.arms.items()
        },
        "models": dict(cfg.models),
        "cli_version": subprocess.run(
            [str(cfg.claude_exe), "--version"], capture_output=True, text=True, check=True
        ).stdout.strip(),
        "fixtures": {
            path.name.removesuffix(".manifest.json"): json.loads(path.read_text(encoding="utf-8"))["sha256"]
            for path in sorted((cfg.data_dir / "fixtures").glob("*.manifest.json"))
        },
        "baseline_tokens": baseline,
        "aborted_attempts": len(list(round_dir.glob("*.aborted-*"))),
        "stopped": json.loads(stopped.read_text(encoding="utf-8")) if stopped.exists() else None,
    }
