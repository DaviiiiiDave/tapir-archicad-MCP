from pathlib import Path

from bench.driver import ClaudeOutcome
from bench.stream_metrics import RunMetrics


def metrics(**overrides) -> RunMetrics:
    values = {
        "input_tokens": 10,
        "output_tokens": 10,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "cost_usd": 0.1,
        "turns": 3,
        "tool_calls": [],
        "tool_result_bytes": 0,
        "peak_context_tokens": 5_000,
        "context_window": 200_000,
        "result_subtype": "success",
        "is_error": False,
        "result_text": "",
        "complete": True,
        "rate_limit_status": "allowed",
        "hook_events": [],
        "init": {},
        "permission_denials": [],
    }
    return RunMetrics(**(values | overrides))


def outcome(**overrides) -> ClaudeOutcome:
    values = {"exit_code": 0, "timed_out": False, "wall_s": 12.0, "stream_path": Path("stream.jsonl"), "pid": 1}
    return ClaudeOutcome(**(values | overrides))
