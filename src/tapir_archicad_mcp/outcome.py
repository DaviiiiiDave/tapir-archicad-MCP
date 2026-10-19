"""Summaries of per-element execution results, so a partial write is never reported as plain success."""

from typing import Any

MAX_EXAMPLES = 3


def summarize_execution_results(result: Any) -> dict | None:
    """Count successes and failures of a write response and group the failures by cause.

    Returns None when the response has no top-level list ``executionResults``.
    """
    if not isinstance(result, dict) or not isinstance(result.get("executionResults"), list):
        return None

    results = result["executionResults"]
    causes: dict[tuple, dict] = {}
    failed = 0
    for index, item in enumerate(results):
        if not isinstance(item, dict) or item.get("success") is not False:
            continue
        failed += 1
        error = item.get("error") if isinstance(item.get("error"), dict) else {}
        key = (error.get("code"), error.get("message"))
        cause = causes.setdefault(key, {"code": key[0], "message": key[1], "count": 0, "examples": []})
        cause["count"] += 1
        if len(cause["examples"]) < MAX_EXAMPLES:
            cause["examples"].append(index)

    return {
        "requested": len(results),
        "succeeded": len(results) - failed,
        "failed": failed,
        "failuresByCause": sorted(causes.values(), key=lambda cause: cause["count"], reverse=True),
    }
